"""
Phase 1 — Discovery.

Maps every combo's *true* last page via a per-combo binary search
(adapted from scraper.py's binary_search_last_page), in parallel across
WORKER_COUNT workers, each owning its own CountdownRateLimiter.

The resulting mapping `combo_key -> {last_page, eliminated}` is cached to
`pagination_cache.json`. Eliminated combos (page 1 empty -> zero users) are
flagged so Phase 2 can skip them entirely.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from combos import combo_url, combo_label
from common import (
    CountdownRateLimiter,
    combo_key,
    log,
    make_headers,
    parse_directory_with_names,
)
from config import CONFIG

CACHE_FILE = "pagination_cache.json"

# Limiter tunables (mirrors bruteforce_scraper defaults)
PER_WORKER_BURST = 2
PER_WORKER_PERIOD = 2.5
MAX_RETRIES = 6
RETRY_WAIT_MIN = 3
RETRY_WAIT_MAX = 90
TIMEOUT = CONFIG["TIMEOUT"]


class DiscoveryProgress:
    """Tiny shared counter object for live Phase-1 progress."""

    def __init__(self, total: int):
        self.total = total
        self.probed = 0
        self.eliminated = 0


async def _page_exists(
    session: aiohttp.ClientSession,
    limiter: CountdownRateLimiter,
    sem: asyncio.Semaphore,
    combo: dict,
    page: int,
    ua_index: int,
) -> bool:
    url = combo_url(combo, page)
    status, html = await limiter.get(
        session, url, make_headers(ua_index), sem, timeout=TIMEOUT
    )
    if status == 404 or html is None:
        return False
    rows = parse_directory_with_names(html)
    return len(rows) > 0


async def _binary_search_last_page(
    session: aiohttp.ClientSession,
    limiter: CountdownRateLimiter,
    sem: asyncio.Semaphore,
    combo: dict,
    ua_index: int,
) -> tuple[int, bool]:
    """Return (last_page, eliminated)."""
    # 1. Zero-check
    if not await _page_exists(session, limiter, sem, combo, 1, ua_index):
        return 0, True

    initial = CONFIG.get("BINARY_SEARCH_INITIAL", 100)
    lo, hi = 1, initial

    # 2. Expansion phase — bracket the last page between lo (exists) and hi (missing)
    while await _page_exists(session, limiter, sem, combo, hi, ua_index):
        lo = hi
        hi = hi * 2

    # 3. Binary search phase
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if await _page_exists(session, limiter, sem, combo, mid, ua_index):
            lo = mid
        else:
            hi = mid

    # 4. Result
    return lo, False


async def _discovery_worker(
    worker_id: int,
    combos_slice: list[dict],
    results: dict[str, dict],
    progress: DiscoveryProgress,
    on_update,
) -> CountdownRateLimiter:
    limiter = CountdownRateLimiter(
        worker_id,
        max_rate=PER_WORKER_BURST,
        time_period=PER_WORKER_PERIOD,
        max_retries=MAX_RETRIES,
        retry_wait_min=RETRY_WAIT_MIN,
        retry_wait_max=RETRY_WAIT_MAX,
    )
    sem = asyncio.Semaphore(2)
    connector = aiohttp.TCPConnector(limit=4)

    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(
                "https://mostaql.com", timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                await resp.read()
        except Exception as exc:
            log.warning("[W%d] cookie priming failed: %s", worker_id, exc)

        for combo in combos_slice:
            last_page, eliminated = await _binary_search_last_page(
                session, limiter, sem, combo, worker_id
            )
            key = combo_key(combo)
            results[key] = {"last_page": last_page, "eliminated": eliminated}
            progress.probed += 1
            if eliminated:
                progress.eliminated += 1
            log.info(
                "[W%d] %s -> last_page=%d eliminated=%s",
                worker_id, combo_label(combo), last_page, eliminated,
            )
            if on_update:
                on_update(worker_id, combo, last_page, eliminated)

    return limiter


async def run_discovery(
    combos: list[dict],
    worker_count: int,
    on_update=None,
) -> tuple[dict[str, dict], list[CountdownRateLimiter], float]:
    """
    Run Phase 1 over all combos.

    Returns (mapping, limiters, elapsed_seconds) where mapping is
    combo_key -> {last_page, eliminated}.
    """
    progress = DiscoveryProgress(len(combos))
    buckets: list[list[dict]] = [[] for _ in range(worker_count)]
    for i, c in enumerate(combos):
        buckets[i % worker_count].append(c)

    results: dict[str, dict] = {}
    start = time.monotonic()
    tasks = [
        asyncio.create_task(
            _discovery_worker(w, buckets[w], results, progress, on_update)
        )
        for w in range(worker_count)
    ]
    limiters = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - start
    return results, limiters, elapsed


# ---------------------------------------------------------------------------
# Cache read / write
# ---------------------------------------------------------------------------
def write_cache(mapping: dict[str, dict], path: str = CACHE_FILE) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "combos": mapping,
    }
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_cache(path: str = CACHE_FILE) -> dict[str, dict] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("combos", {})
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read cache %s: %s", path, exc)
        return None
