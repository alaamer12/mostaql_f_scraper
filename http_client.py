"""
Adaptive HTTP client for Mostaql scraping.

Libraries
---------
* aiolimiter  — token-bucket rate limiter (burst-friendly, shared across workers)
* tenacity    — exponential backoff + jitter on 429 / 5xx / network errors

Design
------
Concurrency (Semaphore) caps parallel in-flight connections.
Rate limiter caps actual request throughput — lets you run many workers
safely because excess coroutines wait on the bucket instead of hammering
the server and getting 429s.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)


class RetryableHTTPError(Exception):
    """HTTP response that should be retried."""

    def __init__(self, status: int, url: str, retry_after: float | None = None):
        self.status = status
        self.url = url
        self.retry_after = retry_after
        super().__init__(f"HTTP {status} → {url}")


class AdaptiveRateLimiter:
    """
    Shared token-bucket limiter with global cooldown on rate-limit responses.

    Parameters
    ----------
    max_rate    : max requests allowed per time_period (burst capacity)
    time_period : bucket window in seconds (larger window = smoother pacing)
    max_retries : tenacity retry attempts per URL
    """

    def __init__(
        self,
        max_rate: float = 6,
        time_period: float = 2.0,
        max_retries: int = 6,
        retry_wait_min: float = 2.0,
        retry_wait_max: float = 90.0,
    ):
        self.max_rate = max_rate
        self.time_period = time_period
        self.max_retries = max_retries
        self.retry_wait_min = retry_wait_min
        self.retry_wait_max = retry_wait_max

        self._limiter = AsyncLimiter(max_rate, time_period)
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()
        self._ok_streak = 0

        # Stats (for logging / diagnostics)
        self.total_requests = 0
        self.total_retries = 0
        self.total_429s = 0

    # ── bucket / cooldown ────────────────────────────────────────────────

    async def _acquire_slot(self) -> None:
        """Wait for global cooldown, then take a token from the bucket."""
        now = time.monotonic()
        if now < self._cooldown_until:
            wait = self._cooldown_until - now
            log.debug("Cooldown active — sleeping %.1fs", wait)
            await asyncio.sleep(wait)
        await self._limiter.acquire()

    async def _penalize(self, retry_after: float | None = None) -> None:
        """Pause all workers after a 429/403."""
        async with self._lock:
            self.total_429s += 1
            self._ok_streak = 0
            penalty = retry_after if retry_after is not None else 8.0
            penalty = min(penalty, 120.0)
            until = time.monotonic() + penalty
            self._cooldown_until = max(self._cooldown_until, until)
            log.warning(
                "Rate-limit response → global cooldown %.0fs (total 429s: %d)",
                penalty, self.total_429s,
            )

    async def _record_success(self) -> None:
        async with self._lock:
            self._ok_streak += 1

    def _before_sleep(self, state: RetryCallState) -> None:
        self.total_retries += 1
        exc = state.outcome.exception() if state.outcome else None
        log.warning("Retry %d/%d: %s", state.attempt_number, self.max_retries, exc)

    # ── public API ───────────────────────────────────────────────────────

    async def get(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict,
        sem: asyncio.Semaphore,
        timeout: float = 20,
    ) -> tuple[int, str | None]:
        """
        GET with rate limiting + retries.

        Returns (status_code, html_text).
        404 → (404, None) immediately.
        Total failure → (0, None).
        """
        self.total_requests += 1

        async def _once() -> tuple[int, str]:
            await self._acquire_slot()
            async with sem:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 404:
                        return 404, ""
                    if resp.status == 429:
                        ra = resp.headers.get("Retry-After")
                        retry_after = (
                            float(ra)
                            if ra and ra.replace(".", "", 1).isdigit()
                            else None
                        )
                        await self._penalize(retry_after)
                        raise RetryableHTTPError(429, url, retry_after)
                    if resp.status == 403:
                        await self._penalize(12.0)
                        raise RetryableHTTPError(403, url)
                    if resp.status >= 500:
                        raise RetryableHTTPError(resp.status, url)
                    resp.raise_for_status()
                    text = await resp.text()
                    await self._record_success()
                    return resp.status, text

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_retries),
                wait=wait_exponential_jitter(
                    initial=self.retry_wait_min,
                    max=self.retry_wait_max,
                    jitter=self.retry_wait_min,
                ),
                retry=retry_if_exception_type(
                    (RetryableHTTPError, aiohttp.ClientError, asyncio.TimeoutError)
                ),
                before_sleep=self._before_sleep,
                reraise=True,
            ):
                with attempt:
                    status, text = await _once()
                    if status == 404:
                        log.info("404 → %s", url)
                        return 404, None
                    return status, text
        except RetryableHTTPError as exc:
            log.error("All retries exhausted (HTTP %d): %s", exc.status, url)
        except Exception as exc:
            log.error("All retries exhausted: %s — %s", url, exc)
        return 0, None

    def summary(self) -> str:
        avg_rps = self.max_rate / self.time_period
        return (
            f"limiter {self.max_rate}/{self.time_period}s (~{avg_rps:.1f} rps) | "
            f"requests={self.total_requests} retries={self.total_retries} "
            f"429s={self.total_429s}"
        )
