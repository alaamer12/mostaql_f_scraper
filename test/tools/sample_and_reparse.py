"""Sample extraction, live re-scraping, and verification tool.

Finds profiles with completed_projects > threshold from a source JSON file,
extracts a sample of URLs, fetches their live HTML, parses them with the new
schema-validated ParsingService, saves to fixed_samples.json, and prints a comparative report.
"""

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from typing import List, Dict, Any, Optional

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import httpx
from src.models import ScrapeConfig, ProfileDetails
from src.services.parser import ParsingService
from src.schema.frame import apply_dtypes, validate_frame
from src.utils.validators import dataset_report

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sample_and_reparse")


def extract_completed_projects(p: Dict[str, Any]) -> float:
    """Extract completed projects from either flat or nested stats format."""
    stats = p.get("stats")
    if isinstance(stats, dict) and "total_completed_projects" in stats:
        try:
            return float(stats["total_completed_projects"])
        except (ValueError, TypeError):
            pass
    val = p.get("total_completed_projects")
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return 0.0


def generate_sample(
    input_file: str,
    output_file: str,
    threshold: float = 500.0,
    samples_number: int = 100,
    seed: int = 42
) -> List[Dict[str, Any]]:
    """Extract sample of profiles having completed_projects > threshold."""
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")

    log.info(f"Loading profiles from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected list of profiles in {input_file}, got {type(data)}")

    matching = [p for p in data if extract_completed_projects(p) > threshold and p.get("profile_url")]
    log.info(f"Found {len(matching)} / {len(data)} profiles with completed_projects > {threshold}")

    if not matching:
        log.warning(f"No profiles matched threshold > {threshold}. Using top 10 highest if available.")
        sorted_profiles = sorted(data, key=extract_completed_projects, reverse=True)
        matching = sorted_profiles[:samples_number]

    random.seed(seed)
    if len(matching) > samples_number:
        sample = random.sample(matching, samples_number)
    else:
        sample = matching

    os.makedirs(os.path.dirname(os.path.abspath(output_file)) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)

    log.info(f"Wrote {len(sample)} sample profiles to {output_file}")
    return sample


async def fetch_url(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    retries: int = 3
) -> Optional[str]:
    """Fetch profile HTML with retry logic."""
    async with semaphore:
        for attempt in range(1, retries + 1):
            try:
                resp = await client.get(url, timeout=15.0)
                if resp.status_code == 200:
                    return resp.text
                elif resp.status_code == 429:
                    log.warning(f"Rate limited (429) on {url}, backing off...")
                    await asyncio.sleep(attempt * 3.0)
                elif resp.status_code == 404:
                    log.warning(f"Profile {url} returned 404 Not Found")
                    return None
            except Exception as e:
                if attempt == retries:
                    log.error(f"Failed to fetch {url} after {retries} attempts: {e}")
                await asyncio.sleep(attempt * 1.5)
        return None


async def reparse_sample_urls(
    sample_urls: List[str],
    output_file: str,
    concurrency: int = 5,
    delay_between_requests: float = 0.05
) -> List[ProfileDetails]:
    """Read URLs, fetch live HTMLs, run new ParsingService, and write fixed_samples.json."""
    parser = ParsingService(config=ScrapeConfig())
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    
    semaphore = asyncio.Semaphore(concurrency)
    results: List[ProfileDetails] = []
    failed_urls: List[str] = []
    inferred_occurrences: List[Dict[str, Any]] = []

    log.info(f"Fetching and re-parsing {len(sample_urls)} profile URLs (concurrency={concurrency})...")

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        async def worker(idx: int, url: str):
            html = await fetch_url(client, url, semaphore)
            if html:
                try:
                    profile = parser.parse_profile(html, url)
                    results.append(profile)
                    
                    # Check for any inferred fields
                    inferred_fields = []
                    for f_name, f_meta in profile.metadata.fields.items():
                        if f_meta.source == "inferred":
                            inferred_fields.append((f_name, f_meta.model_dump()))
                    
                    if inferred_fields:
                        inferred_occurrences.append({
                            "url": url,
                            "fields": inferred_fields
                        })
                        log.warning(f"[{idx}/{len(sample_urls)}] INFERRED detected in {url}: {inferred_fields}")
                    else:
                        log.info(
                            f"[{idx}/{len(sample_urls)}] Parsed {url} -> "
                            f"Projects: {profile.stats.total_completed_projects}, "
                            f"Rating: {profile.stats.rating}, "
                            f"CompRate: {profile.stats.completion_rate}%, "
                            f"Quality: {profile.metadata.quality}"
                        )
                except Exception as e:
                    log.error(f"[{idx}/{len(sample_urls)}] Parsing exception on {url}: {e}")
                    failed_urls.append(url)
            else:
                log.warning(f"[{idx}/{len(sample_urls)}] Skipped {url} due to fetch error")
                failed_urls.append(url)
            
            if delay_between_requests > 0:
                await asyncio.sleep(delay_between_requests)

        tasks = [worker(i, url) for i, url in enumerate(sample_urls, 1)]
        await asyncio.gather(*tasks)

    # Save to fixed_samples.json
    os.makedirs(os.path.dirname(os.path.abspath(output_file)) or ".", exist_ok=True)
    serializable = [p.to_dict() for p in results]
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

    log.info(f"Saved {len(results)} fixed profiles to {output_file}")
    
    # Inferred & Failure summary
    print("\n" + "=" * 80)
    print(" " * 25 + "INSPECTION SUMMARY (200 SAMPLES)")
    print("=" * 80)
    print(f"Total Sample URLs Processed   : {len(sample_urls)}")
    print(f"Successfully Parsed Profiles  : {len(results)}")
    print(f"Failed / Unreachable Profiles : {len(failed_urls)} -> {failed_urls[:10]}")
    print(f"Profiles with 'inferred' fields: {len(inferred_occurrences)}")
    if inferred_occurrences:
        for item in inferred_occurrences:
            print(f"  - URL: {item['url']}")
            for fname, meta in item["fields"]:
                print(f"    Field: {fname} | Meta: {meta}")
    else:
        print("  -> ZERO inferred fields detected! All fields were extracted structurally, by label, derived, or spec defaults.")
    print("=" * 80 + "\n")

    return results


def print_comparison_witness(
    old_sample: List[Dict[str, Any]],
    fixed_profiles: List[ProfileDetails]
):
    """Witness and compare old corrupted values with new schema-fixed values."""
    old_map = {p.get("profile_url"): p for p in old_sample if p.get("profile_url")}
    
    print("\n" + "=" * 80)
    print(" " * 25 + "SAMPLE RE-PARSE WITNESS REPORT")
    print("=" * 80)
    
    over_500_old = 0
    over_500_new = 0
    fixed_count = 0
    total_compared = 0

    print(f"\n{'Profile URL':<35} | {'Old Projects':<12} | {'New Projects':<12} | {'Old Rate':<10} | {'New Rate':<10} | {'Quality'}")
    print("-" * 105)

    for p in fixed_profiles:
        url = p.profile_url
        old = old_map.get(url)
        if not old:
            continue
        
        total_compared += 1
        old_tcp = extract_completed_projects(old)
        new_tcp = p.stats.total_completed_projects
        old_rate = old.get("completion_rate") or (old.get("stats", {}).get("completion_rate", 0.0))
        new_rate = p.stats.completion_rate

        if old_tcp > 500:
            over_500_old += 1
        if new_tcp > 500:
            over_500_new += 1
        if old_tcp != new_tcp or old_rate != new_rate:
            fixed_count += 1

        short_url = url.replace("https://mostaql.com/u/", "u/")
        print(f"{short_url:<35} | {old_tcp:<12.1f} | {new_tcp:<12.1f} | {float(old_rate):<10.1f} | {new_rate:<10.1f} | {p.metadata.quality}")

    print("-" * 105)
    print(f"Total Profiles Compared       : {total_compared}")
    print(f"Profiles with >500 in Old Data: {over_500_old} ({over_500_old/max(1, total_compared)*100:.1f}%)")
    print(f"Profiles with >500 in New Data: {over_500_new} ({over_500_new/max(1, total_compared)*100:.1f}%)")
    print(f"Values Fixed / Corrected      : {fixed_count} / {total_compared}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Generate sample from profiles.json and re-parse with new schema.")
    parser.add_argument("--input", default="collected/profiles.json", help="Path to input profiles.json")
    parser.add_argument("--threshold", type=float, default=500.0, help="Completed projects threshold (default: 500)")
    parser.add_argument("--samples", type=int, default=10, help="Number of samples to extract (default: 10)")
    parser.add_argument("--sample-output", default="collected/sample.json", help="Path to save sample.json")
    parser.add_argument("--fixed-output", default="collected/fixed_samples.json", help="Path to save fixed_samples.json")
    parser.add_argument("--concurrency", type=int, default=3, help="Max async concurrency (default: 3)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for sampling (default: 42)")

    args = parser.parse_args()

    # Step 1: Generate sample
    sample_data = generate_sample(
        input_file=args.input,
        output_file=args.sample_output,
        threshold=args.threshold,
        samples_number=args.samples,
        seed=args.seed
    )

    # Step 2: Read sample as URLs only
    sample_urls = [p["profile_url"] for p in sample_data if "profile_url" in p]
    log.info(f"Extracted {len(sample_urls)} URLs from {args.sample_output}")

    # Step 3 & 4: Fetch live HTML, parse with new schema, save to fixed_output
    fixed_profiles = asyncio.run(
        reparse_sample_urls(
            sample_urls=sample_urls,
            output_file=args.fixed_output,
            concurrency=args.concurrency
        )
    )

    # Step 5: Witness report
    print_comparison_witness(sample_data, fixed_profiles)


if __name__ == "__main__":
    main()
