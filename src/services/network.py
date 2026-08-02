import asyncio
import logging
import time
import sys
from typing import Optional, Tuple
import aiohttp
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ..models import ScrapeConfig
from ..utils.reporting import PhaseMetrics

log = logging.getLogger(__name__)

_shared_limiter: Optional[AsyncLimiter] = None


def enable_shared_limiter(config: "ScrapeConfig") -> AsyncLimiter:
    """Make every future NetworkService share one process-wide token bucket.

    Used by the pipelined runner: several stages hit the network at the same
    time, and without a shared limiter the effective request rate would be
    multiplied by the number of running stages.
    """
    global _shared_limiter
    _shared_limiter = AsyncLimiter(config.rate_limit_burst, config.rate_limit_period)
    return _shared_limiter


def disable_shared_limiter() -> None:
    """Restore per-service rate limiting (the default, non-pipelined mode)."""
    global _shared_limiter
    _shared_limiter = None


class RetryableHTTPError(Exception):
    """HTTP response that should be retried."""
    def __init__(self, status: int, url: str, retry_after: Optional[float] = None):
        self.status = status
        self.url = url
        self.retry_after = retry_after
        super().__init__(f"HTTP {status} → {url}")

class NetworkService:
    """Handles HTTP requests with rate limiting, retries, and escalating cooldowns."""

    def __init__(self, config: ScrapeConfig, worker_id: Optional[int] = None, metrics: Optional[PhaseMetrics] = None):
        self.config = config
        self.worker_id = worker_id
        self.metrics = metrics
        self._limiter = _shared_limiter or AsyncLimiter(config.rate_limit_burst, config.rate_limit_period)
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()
        self._ok_streak = 0
        self._consecutive_penalties = 0
        
        # Cooldown tunables
        self.COOLDOWN_START = 30.0
        self.COOLDOWN_MAX = 120.0
        self.OK_STREAK_RESET = 8

        # Stats
        self.total_requests = 0
        self.total_retries = 0
        self.total_429s = 0

    def _make_headers(self, ua_index: int = 0) -> dict:
        agents = self.config.user_agents
        return {
            "User-Agent": agents[ua_index % len(agents)],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Referer": "https://mostaql.com/",
        }

    async def _acquire_slot(self) -> None:
        """Wait for global/worker cooldown, then take a token from the bucket."""
        now = time.monotonic()
        if now < self._cooldown_until:
            wait = self._cooldown_until - now
            if self.worker_id is not None:
                await self._live_countdown(wait)
            else:
                await asyncio.sleep(wait)
        await self._limiter.acquire()

    async def _live_countdown(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            step = min(1.0, remaining)
            mm, ss = divmod(int(round(remaining)), 60)
            sys.stdout.write(f"\r  [W{self.worker_id}] rate-limited — waiting {mm:02d}:{ss:02d}   ")
            sys.stdout.flush()
            await asyncio.sleep(step)
            remaining -= step
        sys.stdout.write(f"\r  [W{self.worker_id}] cooldown done — resuming{' ' * 25}\n")
        sys.stdout.flush()
        self._ok_streak = 0  # Reset streak on recovery

    def get_stats(self) -> dict:
        """Returns network statistics."""
        return {
            "requests": self.total_requests,
            "retries": self.total_retries,
            "429s": self.total_429s
        }

    async def _penalize(self, retry_after: Optional[float] = None) -> None:
        """Apply escalating penalty after a 429/403."""
        async with self._lock:
            self.total_429s += 1
            if self.metrics:
                self.metrics.increment("rate_limit_hits")
            self._ok_streak = 0
            self._consecutive_penalties += 1
            
            if retry_after is not None:
                penalty = min(retry_after, self.COOLDOWN_MAX)
            else:
                penalty = min(
                    self.COOLDOWN_START * (2 ** (self._consecutive_penalties - 1)),
                    self.COOLDOWN_MAX,
                )
            
            until = time.monotonic() + penalty
            self._cooldown_until = max(self._cooldown_until, until)
            if self.metrics:
                self.metrics.increment("retry_wait_seconds", penalty)
            
            prefix = f"[W{self.worker_id}] " if self.worker_id is not None else ""
            log.warning("%srate-limit hit #%d -> cooldown %.0fs", prefix, self._consecutive_penalties, penalty)

    async def _record_success(self) -> None:
        async with self._lock:
            self._ok_streak += 1
            if self._ok_streak >= self.OK_STREAK_RESET:
                self._consecutive_penalties = 0

    def _before_sleep(self, state: RetryCallState) -> None:
        self.total_retries += 1
        if self.metrics:
            self.metrics.increment("retries")
        exc = state.outcome.exception() if state.outcome else None
        log.warning("Retry %d/%d: %s", state.attempt_number, self.config.max_retries, exc)

    async def get(
        self,
        session: aiohttp.ClientSession,
        url: str,
        sem: asyncio.Semaphore,
        ua_index: int = 0
    ) -> Tuple[int, Optional[str]]:
        """GET with rate limiting + retries."""
        self.total_requests += 1
        if self.metrics:
            self.metrics.increment("requests")
        headers = self._make_headers(ua_index)

        async def _once() -> Tuple[int, str]:
            async with sem:
                await self._acquire_slot()
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                ) as resp:
                    if resp.status == 404:
                        if self.metrics:
                            self.metrics.increment("not_found_404")
                        return 404, ""
                    if resp.status == 429:
                        ra = resp.headers.get("Retry-After")
                        retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
                        await self._penalize(retry_after)
                        raise RetryableHTTPError(429, url, retry_after)
                    if resp.status == 403:
                        await self._penalize()
                        raise RetryableHTTPError(403, url)
                    if resp.status >= 500:
                        raise RetryableHTTPError(resp.status, url)
                    resp.raise_for_status()
                    text = await resp.text()
                    await self._record_success()
                    return resp.status, text

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.config.max_retries),
                wait=wait_exponential_jitter(
                    initial=self.config.retry_wait_min,
                    max=self.config.retry_wait_max,
                    jitter=self.config.retry_wait_min,
                ),
                retry=retry_if_exception_type((RetryableHTTPError, aiohttp.ClientError, asyncio.TimeoutError)),
                before_sleep=self._before_sleep,
                reraise=True,
            ):
                with attempt:
                    status, text = await _once()
                    return (status, text) if status != 404 else (404, None)
        except Exception as exc:
            log.error("All retries exhausted for %s: %s", url, exc)
            if self.metrics:
                self.metrics.increment("errors")
            return 0, None
