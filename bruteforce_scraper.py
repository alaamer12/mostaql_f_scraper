"""
Mostaql "development" freelancer brute-force sweep (two-phase).

Mostaql caps every filtered directory query independently, so fixing
specialities=development and layering on 335 additional single-filter
variations (rating / titles / skills / country) surfaces freelancers that
never show up in any single query. Output is {name, profile_url} only.

Two phases:
    Phase 1 (discovery) — binary-search each combo's true last page, cache it.
    Phase 2 (scrape)    — scrape pages 1..last_page of every surviving combo,
                          flushing every 50 records and checkpointing per page
                          so a crash/kill can resume without data loss.

CLI:
    python bruteforce_scraper.py          # use cache if present, resume if checkpoint present
    python bruteforce_scraper.py --new    # force fresh Phase 1 discovery, overwrite cache

Runtime artifacts (not source-controlled):
    pagination_cache.json, checkpoint.json, temp/
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import time
from pathlib import Path

import aiohttp
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from tqdm import tqdm

from combos import build_combinations, combo_url, combo_label
from common import (
    CountdownRateLimiter,
    FULL_PAGE_SIZE,
    combo_key,
    make_headers,
    parse_directory_with_names,
)
from config import CONFIG
from pagination_discovery import (
    CACHE_FILE,
    load_cache,
    run_discovery,
    write_cache,
)
from profile_scraper import run_phase3

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
WORKER_COUNT = 6
PER_WORKER_BURST = 2
PER_WORKER_PERIOD = 2.5
MAX_RETRIES = 6
RETRY_WAIT_MIN = 3
RETRY_WAIT_MAX = 90
TIMEOUT = CONFIG["TIMEOUT"]

FLUSH_EVERY = 50               # flush a worker's buffer once it holds this many records

OUTPUT_JSON = CONFIG["OUTPUT_JSON"]
OUTPUT_CSV = CONFIG["OUTPUT_CSV"]
REPORT_TXT = "bruteforce_report.txt"
CHECKPOINT_FILE = "checkpoint.json"
TEMP_DIR = "temp"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bruteforce.log", encoding="utf-8")],
)
log = logging.getLogger("mostaql.bruteforce")
console = Console()


# ---------------------------------------------------------------------------
# Phase-2 shared state
# ---------------------------------------------------------------------------
class ScrapeState:
    def __init__(self) -> None:
        self.pages_parsed = 0
        self.raw_rows = 0
        self.start_time = 0.0
        self.lock = asyncio.Lock()
        self.checkpoint: dict[str, dict] = {}
        self.bar: tqdm | None = None


STATE = ScrapeState()


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------
def load_checkpoint() -> dict[str, dict]:
    p = Path(CHECKPOINT_FILE)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def write_checkpoint(checkpoint: dict[str, dict]) -> None:
    tmp = Path(CHECKPOINT_FILE + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)
    tmp.replace(CHECKPOINT_FILE)


# ---------------------------------------------------------------------------
# Phase-2 worker
# ---------------------------------------------------------------------------
async def _flush(worker_id: int, buffer: list[dict], part: int) -> int:
    """Append the buffer to a new JSONL part file. Returns the next part index."""
    if not buffer:
        return part
    temp_dir = Path(TEMP_DIR)
    temp_dir.mkdir(exist_ok=True)
    path = temp_dir / f"worker_{worker_id}_part_{part:04d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for rec in buffer:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    buffer.clear()
    return part + 1


async def _scrape_page(
    session: aiohttp.ClientSession,
    limiter: CountdownRateLimiter,
    sem: asyncio.Semaphore,
    combo: dict,
    page: int,
    ua_index: int,
) -> list[dict]:
    url = combo_url(combo, page)
    status, html = await limiter.get(
        session, url, make_headers(ua_index), sem, timeout=TIMEOUT
    )
    if not html or status != 200:
        return []
    return parse_directory_with_names(html)


async def scrape_worker(
    worker_id: int,
    combo_units: list[tuple[dict, int]],
    resume: bool,
) -> CountdownRateLimiter:
    """
    combo_units: ordered list of (combo, last_page) assigned to this worker.
    The worker scrapes pages 1..last_page of each combo in order.
    """
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
    wkey = f"worker_{worker_id}"

    # Determine where to resume from.
    resume_combo = None
    resume_after_page = 0
    if resume:
        cp = STATE.checkpoint.get(wkey)
        if cp:
            resume_combo = cp.get("current_combo")
            resume_after_page = cp.get("last_completed_page", 0)

    # Figure out the next part index so we don't clobber already-flushed files.
    part = 0
    existing = sorted(Path(TEMP_DIR).glob(f"worker_{worker_id}_part_*.jsonl")) \
        if Path(TEMP_DIR).exists() else []
    if existing:
        part = max(int(p.stem.split("_")[-1]) for p in existing) + 1

    buffer: list[dict] = []
    reached_resume_point = resume_combo is None

    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(
                "https://mostaql.com", timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                await resp.read()
        except Exception as exc:
            log.warning("[W%d] cookie priming failed: %s", worker_id, exc)

        for combo, last_page in combo_units:
            key = combo_key(combo)
            start_page = 1

            if not reached_resume_point:
                if key == resume_combo:
                    # Resume in the middle of this combo.
                    reached_resume_point = True
                    start_page = resume_after_page + 1
                else:
                    # This whole combo was completed in a prior run — skip it,
                    # but still advance the progress bar for its pages.
                    async with STATE.lock:
                        if STATE.bar is not None:
                            STATE.bar.update(last_page)
                    continue

            for page in range(start_page, last_page + 1):
                rows = await _scrape_page(
                    session, limiter, sem, combo, page, worker_id
                )
                buffer.extend(rows)

                async with STATE.lock:
                    STATE.pages_parsed += 1
                    STATE.raw_rows += len(rows)
                    STATE.checkpoint[wkey] = {
                        "current_combo": key,
                        "last_completed_page": page,
                    }
                    write_checkpoint(STATE.checkpoint)
                    if STATE.bar is not None:
                        STATE.bar.update(1)
                        STATE.bar.set_postfix_str(
                            f"W{worker_id} {combo_label(combo)[:24]} p{page}/{last_page}"
                        )

                if len(buffer) >= FLUSH_EVERY:
                    part = await _flush(worker_id, buffer, part)

                # Stop early if the page returned fewer than a full page of rows.
                if rows and len(rows) < FULL_PAGE_SIZE:
                    break

        # Final flush of whatever is left.
        await _flush(worker_id, buffer, part)

    return limiter


# ---------------------------------------------------------------------------
# Merge + dedupe
# ---------------------------------------------------------------------------
def merge_temp_files() -> tuple[list[dict], int]:
    """Read every temp JSONL, dedupe by profile_url (last-write-wins)."""
    seen: dict[str, dict] = {}
    raw = 0
    temp_dir = Path(TEMP_DIR)
    if temp_dir.exists():
        for path in sorted(temp_dir.glob("worker_*_part_*.jsonl")):
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    raw += 1
                    seen[rec["profile_url"]] = rec
    unique = sorted(seen.values(), key=lambda r: r.get("name") or "")
    return unique, raw


def cleanup_artifacts() -> None:
    """Remove checkpoint + temp files after a fully clean run."""
    Path(CHECKPOINT_FILE).unlink(missing_ok=True)
    temp_dir = Path(TEMP_DIR)
    if temp_dir.exists():
        for path in temp_dir.glob("worker_*_part_*.jsonl"):
            path.unlink(missing_ok=True)
        try:
            temp_dir.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Phase 1 orchestration
# ---------------------------------------------------------------------------
async def do_phase1(combos: list[dict], force_new: bool) -> tuple[dict[str, dict], dict]:
    """Return (mapping, phase1_stats)."""
    stats = {
        "from_cache": False,
        "elapsed": 0.0,
        "requests": 0,
        "retries": 0,
        "hits": 0,
        "wait_events": 0,
        "wait_time": 0.0,
    }

    cached = None if force_new else load_cache(CACHE_FILE)
    if cached is not None:
        stats["from_cache"] = True
        console.print(Panel.fit(
            f"[green]Loaded pagination cache[/] ({len(cached)} combos) — skipping Phase 1",
            title="Phase 1 — Discovery (cached)",
        ))
        return cached, stats

    console.print(Panel.fit(
        f"Binary-searching last page for [bold]{len(combos)}[/] combos "
        f"across {WORKER_COUNT} workers…",
        title="Phase 1 — Discovery",
    ))

    total = len(combos)
    bar = tqdm(total=total, desc="Phase 1 combos", unit="combo")

    def on_update(worker_id, combo, last_page, eliminated):
        bar.update(1)
        bar.set_postfix_str(
            f"{combo_label(combo)[:24]} -> {'ELIM' if eliminated else last_page}"
        )

    mapping, limiters, elapsed = await run_discovery(combos, WORKER_COUNT, on_update)
    bar.close()

    write_cache(mapping, CACHE_FILE)

    stats["elapsed"] = elapsed
    stats["requests"] = sum(l.total_requests for l in limiters)
    stats["retries"] = sum(l.total_retries for l in limiters)
    stats["hits"] = sum(l.total_429s for l in limiters)
    stats["wait_events"] = sum(l.wait_events for l in limiters)
    stats["wait_time"] = sum(l.accumulated_wait for l in limiters)

    eliminated = sum(1 for v in mapping.values() if v["eliminated"])
    surviving = len(mapping) - eliminated
    total_pages = sum(v["last_page"] for v in mapping.values() if not v["eliminated"])

    tbl = Table(title="Phase 1 complete")
    tbl.add_column("Metric")
    tbl.add_column("Value", justify="right")
    tbl.add_row("Total combos", str(len(mapping)))
    tbl.add_row("Eliminated (0 users)", str(eliminated))
    tbl.add_row("Surviving", str(surviving))
    tbl.add_row("Total pages to scrape", str(total_pages))
    tbl.add_row("Discovery time", f"{elapsed:.1f}s")
    console.print(tbl)

    return mapping, stats


# ---------------------------------------------------------------------------
# Phase 2 orchestration
# ---------------------------------------------------------------------------
async def do_phase2(
    combos: list[dict],
    mapping: dict[str, dict],
    resume: bool,
) -> tuple[list[dict], int, list[CountdownRateLimiter], float, int]:
    """
    Returns (unique_users, raw_rows, limiters, elapsed, total_pages).
    """
    # Build the surviving combo list (preserve deterministic combos.py order).
    surviving: list[tuple[dict, int]] = []
    for combo in combos:
        key = combo_key(combo)
        info = mapping.get(key)
        if info is None or info["eliminated"] or info["last_page"] < 1:
            continue
        surviving.append((combo, info["last_page"]))

    total_pages = sum(lp for _, lp in surviving)

    # Stable round-robin assignment of whole combos to workers.
    buckets: list[list[tuple[dict, int]]] = [[] for _ in range(WORKER_COUNT)]
    for i, unit in enumerate(surviving):
        buckets[i % WORKER_COUNT].append(unit)

    console.print(Panel.fit(
        f"Scraping [bold]{len(surviving)}[/] surviving combos "
        f"({total_pages} pages) across {WORKER_COUNT} workers"
        + ("  [yellow](resuming from checkpoint)[/]" if resume else ""),
        title="Phase 2 — Scrape",
    ))

    STATE.checkpoint = load_checkpoint() if resume else {}
    STATE.start_time = time.monotonic()
    STATE.bar = tqdm(total=total_pages, desc="Phase 2 pages", unit="page")

    tasks = [
        asyncio.create_task(scrape_worker(w, buckets[w], resume))
        for w in range(WORKER_COUNT)
    ]
    limiters = await asyncio.gather(*tasks)
    STATE.bar.close()
    elapsed = time.monotonic() - STATE.start_time

    unique, raw = merge_temp_files()
    return unique, raw, limiters, elapsed, total_pages


# ---------------------------------------------------------------------------
# Reporting / output
# ---------------------------------------------------------------------------
def save_outputs(unique_users: list[dict]) -> tuple[Path, Path]:
    out_json = Path(OUTPUT_JSON)
    out_csv = Path(OUTPUT_CSV)
    with out_json.open("w", encoding="utf-8") as f:
        json.dump(unique_users, f, ensure_ascii=False, indent=2)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "profile_url"])
        writer.writeheader()
        writer.writerows(unique_users)
    return out_json, out_csv


def build_report(
    total_combos: int,
    mapping: dict[str, dict],
    p1: dict,
    unique_users: list[dict],
    raw_rows: int,
    limiters: list[CountdownRateLimiter],
    elapsed: float,
    total_pages: int,
    out_json: Path,
    out_csv: Path,
) -> str:
    eliminated = sum(1 for v in mapping.values() if v["eliminated"])
    surviving = len(mapping) - eliminated
    req = sum(l.total_requests for l in limiters) + p1["requests"]
    retries = sum(l.total_retries for l in limiters) + p1["retries"]
    hits = sum(l.total_429s for l in limiters) + p1["hits"]
    wait_events = sum(l.wait_events for l in limiters) + p1["wait_events"]
    wait_time = sum(l.accumulated_wait for l in limiters) + p1["wait_time"]
    pps = STATE.pages_parsed / max(elapsed, 0.001)

    return f"""
{'=' * 70}
  BRUTE-FORCE REPORT
{'=' * 70}
  Combinations planned          : {total_combos}
  Combinations eliminated (0)   : {eliminated}
  Combinations surviving        : {surviving}
  Phase 1 from cache            : {p1['from_cache']}

  Total pages known (denominator): {total_pages}
  Pages scraped this run         : {STATE.pages_parsed}

  Total raw rows collected      : {raw_rows}
  Total UNIQUE users            : {len(unique_users)}

  Elapsed (this run, Phase 2)   : {elapsed:.1f}s ({elapsed/60:.1f} min)
  Pages parsed / second         : {pps:.2f}
  Total HTTP requests           : {req}
  Total retries (tenacity)      : {retries}
  Total 429/403 hits            : {hits}
  Rate-limit wait events        : {wait_events}
  Accumulated wait time         : {wait_time:.1f}s ({wait_time/60:.1f} min)

  Output JSON                   : {out_json}
  Output CSV                    : {out_csv}
  Pagination cache              : {CACHE_FILE}
  Checkpoint                    : {CHECKPOINT_FILE}
  Temp dir                      : {TEMP_DIR}/
{'=' * 70}
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run(force_new: bool, deep: bool = False, limit: int | None = None) -> None:
    combos = build_combinations()
    total_combos = len(combos)

    console.print(Panel.fit(
        f"[bold cyan]MOSTAQL 'development' BRUTE-FORCE SWEEP[/]\n"
        f"combinations = {total_combos}  |  workers = {WORKER_COUNT}  |  "
        f"aggregate ~{WORKER_COUNT * PER_WORKER_BURST / PER_WORKER_PERIOD:.1f} req/s",
        title="Startup",
    ))

    # Phase 1 (or load cache).
    mapping, p1 = await do_phase1(combos, force_new)

    # Resume Phase 2 only when NOT a fresh discovery run and a checkpoint exists.
    resume = (not force_new) and bool(load_checkpoint())

    # Phase 2.
    unique_users, raw_rows, limiters, elapsed, total_pages = await do_phase2(
        combos, mapping, resume
    )

    out_json, out_csv = save_outputs(unique_users)

    report = build_report(
        total_combos, mapping, p1, unique_users, raw_rows,
        limiters, elapsed, total_pages, out_json, out_csv,
    )
    console.print(report)
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write(report)

    # Clean run finished — clear resume artifacts so the next normal run is fresh.
    cleanup_artifacts()
    console.print("[green]Run complete — Phase 2 checkpoint and temp files cleared.[/]")

    # Phase 3 (Deep Scrape)
    if deep:
        await run_phase3(resume=(not force_new), limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mostaql 'development' two-phase brute-force scraper.",
    )
    parser.add_argument(
        "--continue",
        dest="resume",
        action="store_true",
        default=True,
        help="Continue from last checkpoint (default: True).",
    )
    parser.add_argument(
        "--no-continue",
        dest="resume",
        action="store_false",
        help="Do not continue from last checkpoint.",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="Force a fresh Phase 1 discovery, overwriting pagination_cache.json "
             "(also ignores any existing checkpoint).",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Run Phase 3 (Deep Scrape) after discovery/URL extraction. "
             "Fetches full stats and portfolio counts for all unique users.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of profiles to scrape in Phase 3.",
    )
    args = parser.parse_args()
    # force_new is True if --new is passed OR if --no-continue was explicitly passed.
    force_new = args.new or (not args.resume)
    asyncio.run(run(force_new=force_new, deep=args.deep, limit=args.limit))


if __name__ == "__main__":
    main()
