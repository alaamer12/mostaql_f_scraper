"""
followup.py
===========
Re-fetch failed profiles from an existing JSON export and merge results back.

Only processes records where `name` is null or `parse_confidence` is
`no_html` / `blocked`.  Uses gentle rate limiting (defaults from
config.FOLLOWUP_DEFAULTS).

Usage
-----
    # Repair in-place (overwrite input JSON + CSV)
    python followup.py

    # Read one file, write to a new file (keep original untouched)
    python followup.py --input mostaql_freelancers_analytics.json \\
                       --output mostaql_freelancers_repaired.json

    # Test on first 10 failures
    python followup.py --limit 10

    # Multi-pass until all fixed or max passes reached
    python followup.py --passes 3

    # Tune pacing
    python followup.py --concurrency 2 --burst 2 --period 3
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from config import CONFIG, FOLLOWUP_DEFAULTS
from fetch import fetch_profiles_batch
from logging_utils import setup_logging
from reporting import configure_stdout, print_followup_report, write_banner, write_line
from storage import (
    build_dataframe,
    filter_failed_records,
    load_records,
    merge_records,
    save_outputs,
)

log = setup_logging()


async def run_followup(
    input_path: Path,
    output_path: Path,
    *,
    in_place: bool,
    limit: int | None,
    passes: int,
    concurrency: int,
    burst: float,
    period: float,
    max_retries: int,
    fetch_portfolio: bool,
) -> None:
    records = load_records(input_path)
    failed_before = filter_failed_records(records)
    urls = [r["profile_url"] for r in failed_before if r.get("profile_url")]

    write_banner("MOSTAQL FOLLOW-UP REPAIR")
    write_line(f"  Input            : {input_path}")
    write_line(f"  Output           : {output_path}  ({'in-place' if in_place else 'new file'})")
    write_line(f"  Total records    : {len(records)}")
    write_line(f"  Failed (before)  : {len(failed_before)}")
    write_line(f"  Max passes       : {passes}")
    write_line(f"  Concurrency      : {concurrency}")
    write_line(f"  Rate limit       : {burst} req / {period}s")
    write_line(f"  Fetch portfolio  : {fetch_portfolio}")
    write_line("")

    if not urls:
        write_line("  Nothing to repair — all records have a name and ok confidence.")
        return

    if limit:
        urls = urls[:limit]
        write_line(f"  Limited to first {limit} failed profile(s)\n")

    total_fixed = 0
    total_attempted = 0
    limiter_summary = ""
    current_records = records

    for pass_num in range(1, passes + 1):
        to_fix = filter_failed_records(current_records)
        pass_urls = [r["profile_url"] for r in to_fix if r.get("profile_url")]
        if limit and pass_num == 1:
            pass_urls = pass_urls[:limit]
        if not pass_urls:
            write_line(f"  Pass {pass_num}: no failures remaining.")
            break

        write_line(f"[ Pass {pass_num}/{passes} ]  Re-fetching {len(pass_urls)} profile(s)...")

        repaired, limiter = await fetch_profiles_batch(
            pass_urls,
            concurrency=concurrency,
            burst=burst,
            period=period,
            max_retries=max_retries,
            retry_wait_min=FOLLOWUP_DEFAULTS["RETRY_WAIT_MIN"],
            retry_wait_max=FOLLOWUP_DEFAULTS["RETRY_WAIT_MAX"],
            fetch_portfolio=fetch_portfolio,
            progress_desc=f"  Pass {pass_num} profiles",
        )
        limiter_summary = limiter.summary()

        current_records, stats = merge_records(current_records, repaired)
        total_fixed += stats["fixed"]
        total_attempted += stats["attempted"]

        write_line(
            f"  Pass {pass_num} result: {stats['fixed']} fixed, "
            f"{stats['still_failed']} still failed"
        )

        if stats["still_failed"] == 0:
            break

    # Save output
    df = build_dataframe(current_records)
    csv_path = output_path.with_suffix(".csv")
    save_outputs(df, json_path=output_path, csv_path=csv_path)

    still_failed_recs = filter_failed_records(current_records)
    still_urls = [r["profile_url"] for r in still_failed_recs]

    print_followup_report(
        input_path=input_path,
        output_path=output_path,
        total_in_file=len(current_records),
        failed_before=len(failed_before),
        attempted=total_attempted,
        fixed=total_fixed,
        still_failed=len(still_failed_recs),
        unchanged_ok=len(current_records) - len(still_failed_recs),
        http_stats=limiter_summary,
        failed_urls=still_urls,
    )


def main() -> None:
    configure_stdout()
    parser = argparse.ArgumentParser(
        description="Re-fetch failed Mostaql profiles from an existing JSON export",
    )
    parser.add_argument(
        "--input", "-i",
        default=CONFIG["OUTPUT_JSON"],
        help="Input JSON file (default: %(default)s)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON file (default: same as --input, i.e. in-place)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        default=None,
        help="Overwrite the input file (default when --output is omitted)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only repair the first N failed profiles",
    )
    parser.add_argument(
        "--passes", type=int, default=1,
        help="Number of repair passes (default: 1)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=FOLLOWUP_DEFAULTS["CONCURRENCY"],
    )
    parser.add_argument(
        "--burst", type=float, default=FOLLOWUP_DEFAULTS["RATE_LIMIT_BURST"],
        help="Token-bucket burst size",
    )
    parser.add_argument(
        "--period", type=float, default=FOLLOWUP_DEFAULTS["RATE_LIMIT_PERIOD"],
        help="Token-bucket window (seconds)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=FOLLOWUP_DEFAULTS["MAX_RETRIES"],
    )
    parser.add_argument(
        "--no-portfolio", action="store_true",
        help="Skip /portfolio tab (faster; portfolio_count may stay null)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    in_place = args.in_place if args.in_place is not None else (args.output is None)
    output_path = Path(args.output) if args.output else input_path

    asyncio.run(run_followup(
        input_path=input_path,
        output_path=output_path,
        in_place=in_place,
        limit=args.limit,
        passes=args.passes,
        concurrency=args.concurrency,
        burst=args.burst,
        period=args.period,
        max_retries=args.max_retries,
        fetch_portfolio=not args.no_portfolio,
    ))


if __name__ == "__main__":
    main()
