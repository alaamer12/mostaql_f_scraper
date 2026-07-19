"""
Phase 3 — Deep Profile Scrape.

Takes the unique profile URLs discovered in Phase 2 and performs a detailed
scrape of each profile (stats, portfolio count, skills, etc.) using the
robust worker-based architecture from bruteforce_scraper.py.

CLI:
    python profile_scraper.py            # start or resume deep scrape
    python profile_scraper.py --new      # force restart (clear checkpoints)
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

from common import (
    CountdownRateLimiter,
    make_headers,
)
from config import CONFIG
from parsing import parse_profile_page, make_failed_record

# ---------------------------------------------------------------------------
# Tunables (reusing Phase 2 defaults)
# ---------------------------------------------------------------------------
WORKER_COUNT = 6
PER_WORKER_BURST = 1           # Profiles are heavier; slower rate
PER_WORKER_PERIOD = 3.5
MAX_RETRIES = 6
RETRY_WAIT_MIN = 3
RETRY_WAIT_MAX = 90
TIMEOUT = CONFIG["TIMEOUT"]

FLUSH_EVERY = 20               # Flush more frequently for detailed records

INPUT_JSON = CONFIG["OUTPUT_JSON"]
OUTPUT_JSON = CONFIG["PROFILES_JSON"]
OUTPUT_CSV = CONFIG["PROFILES_CSV"]
CHECKPOINT_FILE = "checkpoint_profiles.json"
TEMP_DIR = "temp_profiles"

log = logging.getLogger("mostaql.bruteforce")
console = Console()

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
class ProfileScrapeState:
    def __init__(self) -> None:
        self.profiles_scraped = 0
        self.start_time = 0.0
        self.lock = asyncio.Lock()
        self.checkpoint: dict[str, dict] = {}
        self.bar: tqdm | None = None

STATE = ProfileScrapeState()

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
# Workers
# ---------------------------------------------------------------------------
async def _flush(worker_id: int, buffer: list[dict], part: int) -> int:
    if not buffer:
        return part
    temp_dir = Path(TEMP_DIR)
    temp_dir.mkdir(exist_ok=True)
    path = temp_dir / f"worker_{worker_id}_part_{part:04d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for rec in buffer:
            # datetime objects are not JSON serializable in parsing.py, 
            # but they should have been converted to strings or kept as raw if we want easy JSON.
            # Actually, parsing.py uses datetime for registration_date.
            
            # Helper to handle datetime
            def json_serial(obj):
                if isinstance(obj, (time.struct_time,)):
                     return time.strftime('%Y-%m-%dT%H:%M:%S', obj)
                from datetime import datetime
                if isinstance(obj, datetime):
                    return obj.isoformat()
                raise TypeError ("Type %s not serializable" % type(obj))

            f.write(json.dumps(rec, ensure_ascii=False, default=json_serial) + "\n")
    buffer.clear()
    return part + 1

async def _scrape_profile(
    session: aiohttp.ClientSession,
    limiter: CountdownRateLimiter,
    sem: asyncio.Semaphore,
    url: str,
    ua_index: int,
) -> dict:
    # 1. Main profile
    status, html = await limiter.get(
        session, url, make_headers(ua_index), sem, timeout=TIMEOUT
    )
    if not html or status != 200:
        return make_failed_record(url, f"http_{status}" if status else "no_html")
    
    # 2. Portfolio tab (optional fetch)
    portfolio_html = None
    p_url = url.rstrip("/") + "/portfolio"
    p_status, p_html = await limiter.get(
        session, p_url, make_headers(ua_index), sem, timeout=TIMEOUT
    )
    if p_status == 200:
        portfolio_html = p_html

    # 3. Parse
    try:
        # Offload parsing to a thread to keep the event loop snappy
        record = await asyncio.to_thread(parse_profile_page, html, url, portfolio_html)
        return record
    except Exception as exc:
        log.error("Failed to parse profile %s: %s", url, exc)
        return make_failed_record(url, "parse_error")

async def profile_worker(
    worker_id: int,
    urls: list[str],
    resume: bool,
) -> CountdownRateLimiter:
    limiter = CountdownRateLimiter(
        worker_id,
        max_rate=PER_WORKER_BURST,
        time_period=PER_WORKER_PERIOD,
        max_retries=MAX_RETRIES,
        retry_wait_min=RETRY_WAIT_MIN,
        retry_wait_max=RETRY_WAIT_MAX,
    )
    sem = asyncio.Semaphore(1) # One at a time per worker to avoid too much concurrency per IP
    connector = aiohttp.TCPConnector(limit=2)
    wkey = f"worker_{worker_id}"

    resume_after_url = None
    if resume:
        cp = STATE.checkpoint.get(wkey)
        if cp:
            resume_after_url = cp.get("last_completed_url")

    part = 0
    existing = sorted(Path(TEMP_DIR).glob(f"worker_{worker_id}_part_*.jsonl")) \
        if Path(TEMP_DIR).exists() else []
    if existing:
        part = max(int(p.stem.split("_")[-1]) for p in existing) + 1

    buffer: list[dict] = []
    reached_resume_point = resume_after_url is None

    async with aiohttp.ClientSession(connector=connector) as session:
        # Cookie priming
        try:
            async with session.get("https://mostaql.com", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                await resp.read()
        except Exception:
            pass

        for url in urls:
            if not reached_resume_point:
                if url == resume_after_url:
                    reached_resume_point = True
                
                async with STATE.lock:
                    if STATE.bar is not None:
                        STATE.bar.update(1)
                continue

            record = await _scrape_profile(session, limiter, sem, url, worker_id)
            buffer.append(record)

            async with STATE.lock:
                STATE.profiles_scraped += 1
                STATE.checkpoint[wkey] = {
                    "last_completed_url": url,
                }
                write_checkpoint(STATE.checkpoint)
                if STATE.bar is not None:
                    STATE.bar.update(1)
                    name = record.get("name") or "Unknown"
                    STATE.bar.set_postfix_str(f"W{worker_id} {name[:20]}")

            if len(buffer) >= FLUSH_EVERY:
                part = await _flush(worker_id, buffer, part)

        await _flush(worker_id, buffer, part)

    return limiter

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def merge_temp_files() -> list[dict]:
    seen: dict[str, dict] = {}
    temp_dir = Path(TEMP_DIR)
    if temp_dir.exists():
        for path in sorted(temp_dir.glob("worker_*_part_*.jsonl")):
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        rec = json.loads(line)
                        seen[rec["profile_url"]] = rec
                    except json.JSONDecodeError:
                        continue
    return sorted(seen.values(), key=lambda r: (r.get("name") or "", r["profile_url"]))

def save_outputs(records: list[dict]):
    out_json = Path(OUTPUT_JSON)
    out_csv = Path(OUTPUT_CSV)
    
    # JSON
    def json_serial(obj):
        from datetime import datetime
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)

    with out_json.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=json_serial)
    
    # CSV (flattening skills)
    if not records:
        return
    
    # Get all keys from all records to ensure consistent CSV columns
    keys = set()
    for r in records:
        keys.update(r.keys())
    
    # Remove some fields we don't want in CSV or need to transform
    fieldnames = sorted(list(keys))
    
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            row = dict(r)
            if isinstance(row.get("skills"), list):
                row["skills"] = "|".join(row["skills"])
            if isinstance(row.get("parse_signals"), list):
                row["parse_signals"] = "|".join(row["parse_signals"])
            # Handle datetimes
            for k, v in row.items():
                from datetime import datetime
                if isinstance(v, datetime):
                    row[k] = v.isoformat()
            writer.writerow(row)

async def run_phase3(resume: bool = True, limit: int | None = None):
    input_path = Path(INPUT_JSON)
    if not input_path.exists():
        console.print(f"[red]Error:[/] {INPUT_JSON} not found. Run bruteforce_scraper.py first.")
        return

    with input_path.open("r", encoding="utf-8") as f:
        all_users = json.load(f)
    
    urls = [u["profile_url"] for u in all_users]
    if limit:
        urls = urls[:limit]
    total = len(urls)

    console.print(Panel.fit(
        f"Performing Deep Scrape on [bold]{total}[/] profiles across {WORKER_COUNT} workers",
        title="Phase 3 — Deep Profile Scrape",
    ))

    STATE.checkpoint = load_checkpoint() if resume else {}
    STATE.start_time = time.monotonic()
    STATE.bar = tqdm(total=total, desc="Profiles", unit="profile")

    # Round-robin distribution
    buckets = [[] for _ in range(WORKER_COUNT)]
    for i, url in enumerate(urls):
        buckets[i % WORKER_COUNT].append(url)

    tasks = [
        asyncio.create_task(profile_worker(w, buckets[w], resume))
        for w in range(WORKER_COUNT)
    ]
    limiters = await asyncio.gather(*tasks)
    STATE.bar.close()
    
    elapsed = time.monotonic() - STATE.start_time
    records = merge_temp_files()
    save_outputs(records)
    
    console.print(f"[green]Done![/] Scraped {len(records)} profiles in {elapsed:.1f}s")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--new", action="store_true", help="Discard checkpoint and start fresh")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of profiles to scrape")
    args = parser.parse_args()
    
    if args.new:
        Path(CHECKPOINT_FILE).unlink(missing_ok=True)
        temp_dir = Path(TEMP_DIR)
        if temp_dir.exists():
            for p in temp_dir.glob("*.jsonl"): p.unlink()
    
    asyncio.run(run_phase3(resume=not args.new, limit=args.limit))
