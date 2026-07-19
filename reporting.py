"""Console output helpers for scraper and follow-up scripts."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from tqdm import tqdm


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def write_line(msg: str = "") -> None:
    tqdm.write(msg)


def write_banner(title: str, width: int = 62) -> None:
    write_line("=" * width)
    write_line(f"  {title}")
    write_line("=" * width)


def print_scraper_header(max_pages: int | str, concurrency: int, rate: str) -> None:
    write_banner("MOSTAQL FREELANCERS SCRAPER")
    write_line(f"  max_pages={max_pages}  concurrency={concurrency}  rate={rate}")


def print_scrape_summary(total: int, ok: int, failed: int, http_stats: str) -> None:
    if failed:
        write_line(f"\n  !  {failed} profiles had fetch/parse issues — see scraper.log")
    write_line(f"\n  OK {total} profiles scraped  ({ok} ok, {failed} failed)")
    write_line(f"\n  HTTP stats: {http_stats}")


def print_completion_paths(json_path: str | Path, csv_path: str | Path, count: int) -> None:
    write_line("")
    write_banner("COMPLETE")
    write_line(f"  {count} freelancers indexed and saved")
    write_line(f"    JSON -> {json_path}")
    write_line(f"    CSV  -> {csv_path}")


def print_top_freelancers(df: pd.DataFrame, n: int = 10) -> None:
    if df.empty:
        return
    cols = [
        "name", "title", "completion_rate", "ontime_delivery_rate",
        "total_completed_projects", "success_score",
    ]
    available = [c for c in cols if c in df.columns]
    print(f"\n-- Top {n} by Success Score --")
    print(df[available].head(n).to_string())


def print_followup_report(
    *,
    input_path: Path,
    output_path: Path,
    total_in_file: int,
    failed_before: int,
    attempted: int,
    fixed: int,
    still_failed: int,
    unchanged_ok: int,
    http_stats: str,
    failed_urls: list[str] | None = None,
) -> None:
    write_banner("FOLLOW-UP REPORT")
    write_line(f"  Input file       : {input_path}")
    write_line(f"  Output file      : {output_path}")
    write_line(f"  Total records    : {total_in_file}")
    write_line(f"  Failed before    : {failed_before}")
    write_line("")
    write_line(f"  Attempted repair : {attempted}")
    write_line(f"  Fixed            : {fixed}")
    write_line(f"  Still failed     : {still_failed}")
    write_line(f"  Unchanged (ok)   : {unchanged_ok}")
    write_line(f"  Failed after     : {failed_before - fixed}")
    write_line("")
    write_line(f"  HTTP stats       : {http_stats}")

    if failed_urls:
        write_line("")
        write_line("  Still-failing profiles:")
        for url in failed_urls[:20]:
            slug = url.rstrip("/").split("/")[-1]
            write_line(f"    - {slug}  ({url})")
        if len(failed_urls) > 20:
            write_line(f"    ... and {len(failed_urls) - 20} more")

    write_line("")
    success_rate = (fixed / attempted * 100) if attempted else 100.0
    write_line(f"  Repair success rate: {success_rate:.1f}%")
