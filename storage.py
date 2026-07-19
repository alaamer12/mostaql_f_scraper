"""Load, merge, transform, and save freelancer records."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from config import CONFIG


def load_records(path: str | Path | None = None) -> list[dict]:
    """Load freelancer records from a JSON file."""
    path = Path(path or CONFIG["OUTPUT_JSON"])
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def is_failed_record(rec: dict) -> bool:
    """True when a record needs re-fetching (missing name or bad confidence)."""
    if not rec.get("name"):
        return True
    return rec.get("parse_confidence") in ("no_html", "blocked")


def filter_failed_records(records: list[dict]) -> list[dict]:
    return [r for r in records if is_failed_record(r)]


def dedupe_records(records: list[dict]) -> list[dict]:
    """Deduplicate by profile_url (last-write-wins, order preserved)."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in records:
        url = r.get("profile_url", "")
        if url and url not in seen:
            seen.add(url)
            out.append(r)
    return out


def merge_records(
    existing: list[dict],
    updates: list[dict],
) -> tuple[list[dict], dict]:
    """
    Replace matching rows in *existing* with rows from *updates* (by profile_url).

    Returns (merged_list, stats_dict).
    """
    by_url = {r["profile_url"]: r for r in updates if r.get("profile_url")}
    merged: list[dict] = []
    stats = {
        "attempted": len(updates),
        "updated": 0,
        "fixed": 0,
        "still_failed": 0,
        "unchanged_ok": 0,
    }

    for rec in existing:
        url = rec.get("profile_url", "")
        if url in by_url:
            new_rec = by_url[url]
            was_failed = is_failed_record(rec)
            now_ok = not is_failed_record(new_rec)
            merged.append(new_rec)
            stats["updated"] += 1
            if was_failed and now_ok:
                stats["fixed"] += 1
            elif is_failed_record(new_rec):
                stats["still_failed"] += 1
        else:
            merged.append(rec)
            if not is_failed_record(rec):
                stats["unchanged_ok"] += 1

    return merged, stats


def compute_success_score(df: pd.DataFrame) -> pd.Series:
    w_completion, w_ontime = 0.35, 0.25
    w_volume, w_rehire, w_communication = 0.20, 0.12, 0.08

    completion = pd.to_numeric(df["completion_rate"], errors="coerce").fillna(0)
    ontime = pd.to_numeric(df["ontime_delivery_rate"], errors="coerce").fillna(0)
    rehire = pd.to_numeric(df["rehire_rate"], errors="coerce").fillna(0)
    communication = pd.to_numeric(df["communication_success_rate"], errors="coerce").fillna(0)
    projects = pd.to_numeric(df["total_completed_projects"], errors="coerce").fillna(0)

    max_projects = projects.replace(0, 1).max()
    volume_norm = np.log1p(projects) / np.log1p(max_projects) * 100

    return (
        completion * w_completion
        + ontime * w_ontime
        + volume_norm * w_volume
        + rehire * w_rehire
        + communication * w_communication
    ).round(2)


def build_dataframe(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return df
    # Drop columns that are re-computed on each save (from prior exports)
    for col in ("rank", "success_score", "skills_str", "registration_date_str"):
        if col in df.columns:
            df = df.drop(columns=[col])
    df["success_score"] = compute_success_score(df)
    df["skills_str"] = df["skills"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else ""
    )
    df["registration_date_str"] = df["registration_date"].apply(
        lambda x: x.isoformat() if isinstance(x, datetime) else None
    )
    df = df.sort_values("success_score", ascending=False).reset_index(drop=True)
    df.index += 1
    df.index.name = "rank"
    return df


def _serialize_record(record: dict) -> dict:
    out = dict(record)
    for k, v in list(out.items()):
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            try:
                if pd.isna(v):
                    out[k] = None
            except (TypeError, ValueError):
                pass
    return out


def save_outputs(
    df: pd.DataFrame,
    json_path: str | Path | None = None,
    csv_path: str | Path | None = None,
) -> None:
    """Persist DataFrame to JSON and CSV."""
    if df.empty:
        return

    json_path = Path(json_path or CONFIG["OUTPUT_JSON"])
    csv_path = Path(csv_path or CONFIG["OUTPUT_CSV"])

    records = [_serialize_record(r) for r in df.reset_index().to_dict(orient="records")]
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    csv_df = df.reset_index().copy()
    csv_df["skills"] = csv_df["skills"].apply(
        lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, list) else x
    )
    csv_df["registration_date"] = csv_df["registration_date"].apply(
        lambda x: x.isoformat() if isinstance(x, datetime) else x
    )
    csv_df.to_csv(csv_path, index=False, encoding="utf-8-sig")


def save_records_json(records: list[dict], path: str | Path) -> None:
    """Save raw record list to JSON (with success_score via DataFrame)."""
    df = build_dataframe(records)
    csv_path = Path(path).with_suffix(".csv")
    save_outputs(df, json_path=path, csv_path=csv_path)
