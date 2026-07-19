"""
Shared helpers for the Mostaql brute-force sweep.

Kept in a standalone module so both the Phase-1 discovery tool
(`pagination_discovery.py`) and the Phase-2 scraper (`bruteforce_scraper.py`)
can import them without creating an import cycle.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import aiohttp
from bs4 import BeautifulSoup

from combos import combo_url  # noqa: F401  (re-exported for convenience)
from config import CONFIG
from http_client import AdaptiveRateLimiter

log = logging.getLogger("mostaql.bruteforce")

# ---------------------------------------------------------------------------
# Escalating-cooldown tunables (shared by both phases)
# ---------------------------------------------------------------------------
COOLDOWN_START = 30.0          # first rate-limit wait
COOLDOWN_MAX = 120.0           # cap ("2 minutes")
OK_STREAK_RESET = 8            # clean requests needed to reset the penalty ladder

FULL_PAGE_SIZE = 25            # rows on a full (non-last) directory page


def combo_key(combo: dict) -> str:
    """Stable, deterministic cache key for a combo."""
    if combo["dim"] == "base":
        return "base"
    return f"{combo['dim']}={combo['value']}"


def make_headers(ua_index: int = 0) -> dict:
    agents = CONFIG["USER_AGENTS"]
    return {
        "User-Agent": agents[ua_index % len(agents)],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://mostaql.com/",
    }


def parse_directory_with_names(html: str) -> list[dict]:
    """Extract {name, profile_url} pairs straight from a directory page."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for row in soup.select("tr.freelancer-row"):
        # The link in info-td is just the avatar. The one in details-td has the name.
        a = row.select_one("td.details-td a[href]")
        if not a:
            a = row.select_one("td.info-td a[href]")
        if not a:
            continue
        href = a["href"].strip()
        if not href.startswith("http"):
            href = "https://mostaql.com" + href
        bdi = a.find("bdi")
        name = bdi.get_text(strip=True) if bdi else a.get_text(strip=True)
        if name:
            out.append({"name": name, "profile_url": href})
    return out


class CountdownRateLimiter(AdaptiveRateLimiter):
    """
    Per-worker limiter: own token bucket + own escalating cooldown state.

    Escalating cooldown on 429/403: 30s -> 60s -> 90s -> 120s (cap), doubling
    each consecutive hit, resetting to the start after OK_STREAK_RESET clean
    requests. A live per-second countdown is shown while a worker waits.
    """

    def __init__(self, worker_id: int, *a, **kw):
        super().__init__(*a, **kw)
        self.worker_id = worker_id
        self._consecutive_penalties = 0
        self.wait_events = 0
        self.accumulated_wait = 0.0

    async def _penalize(self, retry_after: float | None = None) -> None:
        async with self._lock:
            self.total_429s += 1
            self._ok_streak = 0
            self._consecutive_penalties += 1
            if retry_after is not None:
                penalty = min(retry_after, COOLDOWN_MAX)
            else:
                penalty = min(
                    COOLDOWN_START * (2 ** (self._consecutive_penalties - 1)),
                    COOLDOWN_MAX,
                )
            until = time.monotonic() + penalty
            self._cooldown_until = max(self._cooldown_until, until)
            log.warning(
                "[W%d] rate-limit hit #%d -> cooldown %.0fs",
                self.worker_id, self._consecutive_penalties, penalty,
            )

    async def _record_success(self) -> None:
        async with self._lock:
            self._ok_streak += 1
            if self._ok_streak >= OK_STREAK_RESET:
                self._consecutive_penalties = 0

    async def _acquire_slot(self) -> None:
        now = time.monotonic()
        if now < self._cooldown_until:
            wait = self._cooldown_until - now
            self.wait_events += 1
            self.accumulated_wait += wait
            await self._live_countdown(wait)
        await self._limiter.acquire()

    async def _live_countdown(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            step = min(1.0, remaining)
            mm, ss = divmod(int(round(remaining)), 60)
            sys.stdout.write(
                f"\r  [W{self.worker_id}] rate-limited — waiting {mm:02d}:{ss:02d}   "
            )
            sys.stdout.flush()
            await asyncio.sleep(step)
            remaining -= step
        sys.stdout.write(
            f"\r  [W{self.worker_id}] cooldown done — resuming{' ' * 25}\n"
        )
        sys.stdout.flush()
