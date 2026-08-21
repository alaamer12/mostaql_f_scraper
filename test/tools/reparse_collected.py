"""Reparse utility tool to validate and re-process cached HTML profiles into the new schema."""

import glob
import json
import os
import sys
import pandas as pd
from bs4 import BeautifulSoup

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.models import ScrapeConfig
from src.services.parser import ParsingService
from src.utils.validators import StrictZeroNullValidator, SchemaValidator, dataset_report, write_quarantine_profiles


def reparse_fixtures_and_cache():
    config = ScrapeConfig()
    parser = ParsingService(config=config)

    fixtures_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../fixtures/profiles"))
    output_report = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../outsourcing/field_survey/reparse_report.md"))
    os.makedirs(os.path.dirname(output_report), exist_ok=True)

    html_files = glob.glob(os.path.join(fixtures_dir, "*.html"))
    print(f"Reparsing {len(html_files)} profile HTML snapshots...")

    parsed_profiles = []
    flat_records = []

    for html_path in sorted(html_files):
        fname = os.path.basename(html_path)
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()

        profile = parser.parse_profile(html, f"https://mostaql.com/u/{fname.replace('.html', '')}")
        assert profile is not None

        # Validate with zero null barrier and schema validator
        StrictZeroNullValidator.validate_profile(profile, html=html)
        issues = SchemaValidator.validate_profile(profile)

        parsed_profiles.append(profile)
        flat_records.append(profile.to_flat_dict())
        print(f"Parsed `{fname}` -> quality: {profile.metadata.quality}, issues: {len(issues)}")

    # Write quarantine dump
    quarantine_count = write_quarantine_profiles(parsed_profiles)
    print(f"Quarantined {quarantine_count} profiles.")

    # Generate Dataset Report
    df = pd.DataFrame(flat_records)
    report_md = dataset_report(df)
    with open(output_report, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\nDataset Report successfully written to:\n  {output_report}\n")
    print(report_md)


if __name__ == "__main__":
    reparse_fixtures_and_cache()
