"""
diagnose_nulls.py
=================
Analyze mostaql_freelancers_analytics.json and classify every null field.

Root-cause categories
---------------------
  fetch_failed   — parse_confidence is 'no_html' (HTTP 429 / timeout during scrape)
  blocked        — parse_confidence is 'blocked' (login wall / bot page)
  not_on_page    — page fetched OK but label absent (employer-only or optional stat)
  parse_miss     — label exists in live HTML but parser left field null (real bug)

Usage
-----
    python diagnose_nulls.py                         # summary only
    python diagnose_nulls.py --sample 10             # live-fetch 10 null users
    python diagnose_nulls.py --sample 50 --verbose   # detailed per-user report
    python diagnose_nulls.py --url https://mostaql.com/u/SomeUser
"""

from __future__ import annotations

import argparse
import orjson
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import CONFIG
from parsing import (
    STAT_MAP,
    _normalize_arabic,
    _resolve_stat_field,
    parse_profile_page as _parse_profile_page,
)
from storage import load_records

JSON_PATH = Path(CONFIG["OUTPUT_JSON"])
HEADERS = {
    "User-Agent": CONFIG["USER_AGENTS"][0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://mostaql.com/",
}

# Fields that only appear when logged in as an employer who worked with the freelancer
EMPLOYER_ONLY = {"employment_rate", "received_projects", "financial_deals"}

# Core fields expected on every public profile page
CORE_FIELDS = [
    "name", "title", "completion_rate", "ontime_delivery_rate",
    "rehire_rate", "communication_success_rate", "total_completed_projects",
    "avg_response_time_raw", "registration_date", "last_active", "skills",
]

OPTIONAL_FIELDS = ["active_projects", "portfolio_count"] + list(EMPLOYER_ONLY)


def load_records() -> list[dict]:
    if not JSON_PATH.exists():
        print(f"ERROR: {JSON_PATH} not found", file=sys.stderr)
        sys.exit(1)
    return orjson.loads(JSON_PATH.read_bytes())


def classify_record(rec: dict) -> str:
    conf = rec.get("parse_confidence", "ok")
    if conf == "no_html":
        return "fetch_failed"
    if conf == "blocked":
        return "blocked"
    if not rec.get("name"):
        return "parse_miss"
    return "ok"


def summarize(records: list[dict]) -> None:
    print("=" * 72)
    print("  MOSTAQL NULL-FIELD DIAGNOSTIC SUMMARY")
    print("=" * 72)
    print(f"  Total records : {len(records)}")

    by_status = Counter(classify_record(r) for r in records)
    print("\n  Record status breakdown:")
    for status, count in by_status.most_common():
        pct = 100 * count / len(records)
        print(f"    {status:20s}  {count:5d}  ({pct:.1f}%)")

    ok_records = [r for r in records if classify_record(r) == "ok"]
    print(f"\n  Successfully parsed profiles: {len(ok_records)}")

    all_fields = CORE_FIELDS + OPTIONAL_FIELDS
    print("\n  Null counts (all records):")
    for field in all_fields:
        if field == "skills":
            nulls = sum(1 for r in records if not r.get(field))
        else:
            nulls = sum(1 for r in records if r.get(field) is None)
        note = " [employer-only]" if field in EMPLOYER_ONLY else ""
        print(f"    {field:30s}  {nulls:5d}  ({100*nulls/len(records):.1f}%){note}")

    if ok_records:
        print("\n  Null counts (only successfully parsed profiles):")
        for field in all_fields:
            if field in EMPLOYER_ONLY:
                nulls = sum(1 for r in ok_records if r.get(field) is None)
                print(f"    {field:30s}  {nulls:5d}  ({100*nulls/len(ok_records):.1f}%)  [expected without login]")
            elif field == "skills":
                nulls = sum(1 for r in ok_records if not r.get(field))
                print(f"    {field:30s}  {nulls:5d}  ({100*nulls/len(ok_records):.1f}%)")
            else:
                nulls = sum(1 for r in ok_records if r.get(field) is None)
                flag = "  <-- investigate" if nulls > 0 else ""
                print(f"    {field:30s}  {nulls:5d}  ({100*nulls/len(ok_records):.1f}%){flag}")

    failed = [r for r in records if classify_record(r) != "ok"]
    if failed:
        print(f"\n  PRIMARY ROOT CAUSE: {len(failed)} profiles never fetched (HTTP 429 rate-limit).")
        print("  Fix: run  python rescue_nulls.py --limit 50  to re-fetch with gentle pacing.")
        print("       then  python rescue_nulls.py           to rescue all remaining.")
        print("  Prevention: scraper now uses aiolimiter + tenacity (see http_client.py).")


def fetch_html(url: str) -> tuple[int, str | None]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        return r.status_code, r.text if r.status_code == 200 else None
    except requests.RequestException as e:
        print(f"    fetch error: {e}")
        return 0, None


def extract_labels_from_html(html: str) -> dict[str, str]:
    """Return {normalised_label: raw_value} from all stats tables on the page."""
    soup = BeautifulSoup(html, "lxml")
    labels: dict[str, str] = {}

    for panel_sel in ("#user-stats", "#profile-stats"):
        panel = soup.select_one(panel_sel)
        if not panel:
            continue
        for row in panel.select("table tr"):
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            span = cols[0].find("span")
            raw_label = (span or cols[0]).get_text(strip=True)
            value = cols[1].get_text(separator=" ", strip=True)
            labels[_normalize_arabic(raw_label)] = value

    # Also scan any table-meta on the page
    for table in soup.select("table.table-meta"):
        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            raw_label = cols[0].get_text(strip=True)
            value = cols[1].get_text(separator=" ", strip=True)
            labels[_normalize_arabic(raw_label)] = value

    return labels


def diagnose_one(url: str, stored: dict | None, verbose: bool) -> dict:
    """Live-fetch one profile and compare stored vs parsed vs HTML labels."""
    result = {"url": url, "issues": []}

    status, html = fetch_html(url)
    p_status, p_html = fetch_html(url.rstrip("/") + "/portfolio")

    result["http_status"] = status
    result["html_size"] = len(html) if html else 0

    if not html:
        result["issues"].append(f"fetch_failed (HTTP {status})")
        if verbose:
            print(f"\n  {url}")
            print(f"    ✗ fetch failed HTTP {status}")
        return result

    labels = extract_labels_from_html(html)
    parsed = _parse_profile_page(html, url, p_html if p_status == 200 else None)

    if verbose:
        print(f"\n  {url}")
        print(f"    HTTP {status}  |  {len(html):,} chars  |  confidence={parsed.get('parse_confidence')}")
        if stored:
            print(f"    stored confidence: {stored.get('parse_confidence')}  name={stored.get('name')!r}")
        print(f"    parsed name: {parsed.get('name')!r}  completion: {parsed.get('completion_rate')}")

    for field in CORE_FIELDS + OPTIONAL_FIELDS:
        if field in EMPLOYER_ONLY:
            continue  # skip employer-only in live comparison

        parsed_val = parsed.get(field)
        is_null = parsed_val is None or (field == "skills" and not parsed_val)

        if not is_null:
            continue

        # Find if any STAT_MAP label maps to this field and exists in HTML
        matching_labels = [
            k for k, v in STAT_MAP.items()
            if v == field or _resolve_stat_field(k) == field
        ]
        found_in_html = any(_normalize_arabic(lbl) in labels for lbl in matching_labels)

        if found_in_html:
            issue = f"parse_miss: {field} (label on page but parser returned null)"
            result["issues"].append(issue)
            if verbose:
                print(f"    ✗ BUG  {issue}")
        else:
            issue = f"not_on_page: {field}"
            result["issues"].append(issue)
            if verbose:
                print(f"    · optional/absent  {field}")

    # Show unmapped labels (potential new fields)
    if verbose:
        known_norm = {_normalize_arabic(k) for k in STAT_MAP}
        unknown = [k for k in labels if k not in known_norm and _resolve_stat_field(k) is None]
        if unknown:
            print(f"    unmapped labels in HTML: {unknown[:6]}")

    return result


def run_sample(records: list[dict], sample_size: int, verbose: bool) -> None:
    failed = [r for r in records if classify_record(r) != "ok"]
    targets = failed[:sample_size]

    print(f"\n{'='*72}")
    print(f"  LIVE SAMPLE TEST — {len(targets)} profiles with fetch/parse failures")
    print("=" * 72)

    issue_counts: Counter = Counter()
    fixed = 0

    for rec in targets:
        url = rec["profile_url"]
        diag = diagnose_one(url, rec, verbose=verbose)
        if diag["http_status"] == 200 and diag["html_size"] > CONFIG["MIN_HTML_BYTES"]:
            reparsed = _parse_profile_page(
                fetch_html(url)[1] or "",
                url,
                fetch_html(url.rstrip("/") + "/portfolio")[1],
            )
            if reparsed.get("name") and classify_record(rec) != "ok":
                fixed += 1
        for issue in diag["issues"]:
            issue_counts[issue.split(":")[0]] += 1

    print(f"\n  Sample results:")
    print(f"    Would fix (fetch now succeeds): {fixed}/{len(targets)}")
    print(f"    Issue breakdown:")
    for kind, count in issue_counts.most_common():
        print(f"      {kind:20s}  {count}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Diagnose null fields in scraper output")
    parser.add_argument("--sample", type=int, default=0, metavar="N",
                        help="Live-fetch N failed profiles and compare")
    parser.add_argument("--verbose", action="store_true", help="Per-profile detail")
    parser.add_argument("--url", type=str, default=None, help="Diagnose a single profile URL")
    args = parser.parse_args()

    records = load_records()
    summarize(records)

    if args.url:
        stored = next((r for r in records if r.get("profile_url") == args.url), None)
        diagnose_one(args.url, stored, verbose=True)
    elif args.sample > 0:
        run_sample(records, args.sample, verbose=args.verbose)


if __name__ == "__main__":
    main()
