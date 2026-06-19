"""
rescue_nulls.py
===============
Re-fetch profiles that failed during the main scrape (parse_confidence != 'ok')
and merge the freshly parsed data back into mostaql_freelancers_analytics.json.

Uses the same AdaptiveRateLimiter (aiolimiter + tenacity) as scraper.py.

Usage
-----
    python rescue_nulls.py --limit 50     # test on 50 failed profiles
    python rescue_nulls.py                # rescue all failed profiles
    python rescue_nulls.py --burst 4 --period 2.5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from tqdm import tqdm

from http_client import AdaptiveRateLimiter
from scraper import (
    CONFIG,
    _parse_profile_page,
    async_get,
    build_dataframe,
    save_outputs,
)

# Gentler defaults for rescue runs (lower burst than main scraper)
RESCUE_BURST = 4
RESCUE_PERIOD = 2.5
RESCUE_CONCURRENCY = 5


def load_records() -> list[dict]:
    path = Path(CONFIG["OUTPUT_JSON"])
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def needs_rescue(rec: dict) -> bool:
    conf = rec.get("parse_confidence", "ok")
    if conf in ("no_html", "blocked"):
        return True
    if not rec.get("name"):
        return True
    return False


async def rescue_profiles(
    urls: list[str],
    concurrency: int,
    burst: float,
    period: float,
) -> tuple[list[dict], AdaptiveRateLimiter]:
    import aiohttp

    rate_limiter = AdaptiveRateLimiter(
        max_rate=burst,
        time_period=period,
        max_retries=CONFIG["MAX_RETRIES"],
        retry_wait_min=CONFIG["RETRY_WAIT_MIN"],
        retry_wait_max=CONFIG["RETRY_WAIT_MAX"],
    )
    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []

    connector = aiohttp.TCPConnector(limit=concurrency + 2)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(
                "https://mostaql.com",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                await r.read()
        except Exception:
            pass

        async def fetch_one(url: str) -> dict:
            status, html = await async_get(session, url, sem, rate_limiter, ua_index=0)
            portfolio_html = None
            if html:
                p_url = url.rstrip("/") + "/portfolio"
                p_status, portfolio_html = await async_get(
                    session, p_url, sem, rate_limiter, ua_index=0
                )
                if p_status != 200:
                    portfolio_html = None

            if html:
                record = await asyncio.to_thread(
                    _parse_profile_page, html, url, portfolio_html
                )
            else:
                record = {
                    "profile_url": url,
                    "name": None,
                    "parse_confidence": "no_html",
                }
            return record

        tasks = [fetch_one(url) for url in urls]
        bar = tqdm(total=len(tasks), desc="  Rescuing profiles", unit="profile", colour="cyan")
        for coro in asyncio.as_completed(tasks):
            record = await coro
            results.append(record)
            name = record.get("name") or "?"
            conf = record.get("parse_confidence", "ok")
            bar.set_postfix_str(f"{name} [{conf}]", refresh=True)
            bar.update(1)
        bar.close()

    return results, rate_limiter


def merge_records(existing: list[dict], rescued: list[dict]) -> tuple[list[dict], dict]:
    rescued_by_url = {r["profile_url"]: r for r in rescued}
    merged = []
    stats = {"updated": 0, "still_failed": 0, "fixed": 0}

    for rec in existing:
        url = rec.get("profile_url", "")
        if url in rescued_by_url:
            new_rec = rescued_by_url[url]
            old_conf = rec.get("parse_confidence", "ok")
            new_conf = new_rec.get("parse_confidence", "ok")
            merged.append(new_rec)
            stats["updated"] += 1
            if old_conf != "ok" and new_conf == "ok":
                stats["fixed"] += 1
            elif new_conf != "ok":
                stats["still_failed"] += 1
        else:
            merged.append(rec)

    return merged, stats


async def run_rescue(
    limit: int | None,
    concurrency: int,
    burst: float,
    period: float,
) -> None:
    records = load_records()
    failed = [r for r in records if needs_rescue(r)]
    urls = [r["profile_url"] for r in failed]
    if limit:
        urls = urls[:limit]

    print("=" * 62)
    print("  MOSTAQL NULL-USER RESCUE")
    print(f"  Failed in JSON : {len(failed)}")
    print(f"  Rescuing now   : {len(urls)}")
    print(f"  Concurrency    : {concurrency}")
    print(f"  Rate limit     : {burst} req / {period}s  (aiolimiter + tenacity)")
    print("=" * 62)

    if not urls:
        print("  Nothing to rescue — all profiles look OK.")
        return

    rescued, limiter = await rescue_profiles(urls, concurrency, burst, period)

    ok_now = sum(1 for r in rescued if r.get("parse_confidence") == "ok" and r.get("name"))
    still_bad = len(rescued) - ok_now
    print(f"\n  Rescue results: {ok_now} fixed, {still_bad} still failing")
    print(f"  HTTP stats: {limiter.summary()}")

    if limit:
        report_path = Path("rescue_sample_report.json")
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(rescued, f, ensure_ascii=False, indent=2, default=str)
        print(f"  Sample report saved -> {report_path}")
        print("\n  Per-profile results:")
        for r in rescued:
            name = r.get("name") or "-"
            conf = r.get("parse_confidence", "?")
            comp = r.get("completion_rate", "-")
            slug = r["profile_url"].split("/")[-1]
            print(f"    {slug:30s}  {name:25s}  conf={conf}  completion={comp}")
    else:
        merged, stats = merge_records(records, rescued)
        df = build_dataframe(merged)
        save_outputs(df)
        print(f"\n  Merged: {stats['updated']} updated, {stats['fixed']} newly fixed, "
              f"{stats['still_failed']} still failed")
        print(f"  Saved -> {CONFIG['OUTPUT_JSON']}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Re-fetch failed Mostaql profiles")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only rescue first N failed profiles (test mode)")
    parser.add_argument("--concurrency", type=int, default=RESCUE_CONCURRENCY)
    parser.add_argument("--burst", type=float, default=RESCUE_BURST,
                        help="Token-bucket burst size (aiolimiter max_rate)")
    parser.add_argument("--period", type=float, default=RESCUE_PERIOD,
                        help="Token-bucket window in seconds")
    args = parser.parse_args()
    asyncio.run(run_rescue(args.limit, args.concurrency, args.burst, args.period))


if __name__ == "__main__":
    main()
