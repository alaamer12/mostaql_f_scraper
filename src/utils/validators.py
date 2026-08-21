"""
validators.py
-------------
Strict Zero-Null Validator, Schema Validator, and Dataset Health Reporting.
"""

import os
import sys
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from dataclasses import dataclass, is_dataclass, asdict
import pandas as pd

from src.schema.spec import FIELD_SPECS, check_record_coherence

log = logging.getLogger(__name__)

CRASH_REPORTS_DIR = Path("outsourcing") / "crash_reports"
QUARANTINE_DIR = Path("outsourcing")


@dataclass(frozen=True)
class Issue:
    """Represents a validation issue on a specific profile field."""
    field: str
    issue_code: str
    message: str
    value: Any


class NullFieldException(Exception):
    """Raised immediately when a field in a profile or exported record resolves to None / null."""

    def __init__(self, message: str, field_name: str, crash_report_path: Optional[str] = None):
        super().__init__(message)
        self.field_name = field_name
        self.crash_report_path = crash_report_path


class StrictZeroNullValidator:
    """Zero-Null enforcement barrier for profile parsing and data exporting."""

    @classmethod
    def validate_profile(cls, profile: Any, html: Optional[str] = None) -> None:
        """Validate all attributes and nested stats of a ProfileDetails object."""
        if profile is None:
            cls._crash_and_dump("profile_object", None, html)
            return

        if hasattr(profile, "to_dict"):
            data = profile.to_dict()
        elif hasattr(profile, "model_dump"):
            data = profile.model_dump()
        elif is_dataclass(profile):
            data = asdict(profile)
        else:
            data = dict(profile)

        for key, value in data.items():
            if value is None:
                cls._crash_and_dump(key, data, html)
            elif isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    if sub_val is None:
                        cls._crash_and_dump(f"{key}.{sub_key}", data, html)
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    if item is None:
                        cls._crash_and_dump(f"{key}[{idx}]", data, html)

    @classmethod
    def validate_record_dict(cls, record: Dict[str, Any], html: Optional[str] = None) -> None:
        """Recursively validate a record dictionary to guarantee 0 nulls."""
        if not isinstance(record, dict):
            if record is None:
                cls._crash_and_dump("root_record", None, html)
            return

        for key, value in record.items():
            if value is None:
                cls._crash_and_dump(key, record, html)
            elif isinstance(value, dict):
                cls.validate_record_dict(value, html)
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    if item is None:
                        cls._crash_and_dump(f"{key}[{idx}]", record, html)
                    elif isinstance(item, dict):
                        cls.validate_record_dict(item, html)

    @classmethod
    def _crash_and_dump(cls, field_name: str, item_data: Any, html: Optional[str] = None) -> None:
        """Capture diagnostic snapshot, dump crash report to disk, and raise NullFieldException."""
        CRASH_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        report_json_path = CRASH_REPORTS_DIR / f"null_field_{ts}.json"
        report_log_path = CRASH_REPORTS_DIR / f"null_field_{ts}.log"

        stack = traceback.format_stack()
        exc_info = traceback.format_exc()

        report_payload = {
            "timestamp": datetime.now().isoformat(),
            "offending_field": field_name,
            "error": f"StrictZeroNullValidator: field '{field_name}' is None/null",
            "item_data": item_data,
            "stack_trace": stack,
            "exception_trace": exc_info,
            "html_snapshot": html[:10000] if html else None,
        }

        # Write JSON diagnostic dump
        try:
            with open(report_json_path, "w", encoding="utf-8") as f:
                json.dump(report_payload, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            log.error(f"Failed to write crash JSON report: {e}")

        # Write readable log dump
        try:
            with open(report_log_path, "w", encoding="utf-8") as f:
                f.write("=" * 75 + "\n")
                f.write("  ZERO-NULL FATAL CRASH REPORT\n")
                f.write("=" * 75 + "\n")
                f.write(f"Timestamp: {report_payload['timestamp']}\n")
                f.write(f"Offending Field: {field_name}\n\n")
                f.write("Stack Trace:\n")
                f.writelines(stack)
                f.write("\nItem Data:\n")
                f.write(json.dumps(item_data, indent=2, default=str, ensure_ascii=False))
                f.write("\n" + "=" * 75 + "\n")
        except Exception as e:
            log.error(f"Failed to write crash text log: {e}")

        err_msg = (
            f"FATAL: Zero-Null assertion violated! Field '{field_name}' resolved to None. "
            f"Diagnostics saved to {report_json_path}"
        )
        log.critical(err_msg)
        raise NullFieldException(err_msg, field_name=field_name, crash_report_path=str(report_json_path))


class SchemaValidator:
    """Validates ProfileDetails against the declared FIELD_SPECS schema."""

    @classmethod
    def validate_profile(cls, profile: Any) -> List[Issue]:
        """Validate profile fields against bounds and relational coherence rules."""
        issues: List[Issue] = []
        if profile is None:
            return [Issue(field="root", issue_code="null_profile", message="Profile is None", value=None)]

        # Top-level & Stats dictionary extraction
        if hasattr(profile, "to_flat_dict"):
            flat_dict = profile.to_flat_dict()
        elif hasattr(profile, "model_dump"):
            flat_dict = profile.model_dump()
        elif is_dataclass(profile):
            flat_dict = asdict(profile)
        else:
            flat_dict = dict(profile)

        # 1. Check every spec field
        for field_name, spec in FIELD_SPECS.items():
            if field_name not in flat_dict:
                if spec.required:
                    issues.append(Issue(
                        field=field_name,
                        issue_code="missing_required_field",
                        message=f"Required field '{field_name}' is missing",
                        value=None,
                    ))
                continue

            val = flat_dict[field_name]
            type_issues = spec.type.validate(val)
            for code in type_issues:
                issues.append(Issue(
                    field=field_name,
                    issue_code=code,
                    message=f"Field '{field_name}' with value '{val}' violated constraint: {code}",
                    value=val,
                ))

        # 2. Check relational coherence
        coherence_issues = check_record_coherence(flat_dict)
        for code in coherence_issues:
            issues.append(Issue(
                field="stats",
                issue_code=code,
                message=f"Relational coherence breach: {code}",
                value=flat_dict.get("total_completed_projects"),
            ))

        return issues


def write_quarantine_profiles(
    profiles: Sequence[Any],
    path: str = "outsourcing/quarantine_profiles.json"
) -> int:
    """Write records with quality != 'ok' to quarantine path."""
    quarantined = []
    for p in profiles:
        if hasattr(p, "metadata") and p.metadata.quality != "ok":
            quarantined.append(p.to_dict() if hasattr(p, "to_dict") else p)

    if quarantined:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(quarantined, f, ensure_ascii=False, indent=2, default=str)
        log.warning(f"Quarantined {len(quarantined)} suspect/bad profiles -> {path}")

    return len(quarantined)


def dataset_report(df: pd.DataFrame) -> str:
    """Generate a comprehensive Markdown dataset health report using pandas."""
    if df.empty:
        return "# Dataset Health Report\n\n*Dataset is empty.*\n"

    lines = []
    lines.append("# Dataset Health & Schema Quality Report")
    lines.append(f"\n- **Total Records Analyzed**: {len(df):,}")
    lines.append(f"- **Total Columns**: {len(df.columns)}")
    lines.append(f"- **Generated At**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    lines.append("## Column Statistics & Outlier Metrics\n")
    lines.append("| Column | Dtype | Null Count (%) | Default Share (%) | Min | Max | p50 (Median) | p95 | Outliers |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for col in sorted(df.columns):
        s = df[col]
        dtype_str = str(s.dtype)
        null_count = int(s.isna().sum())
        null_pct = f"{round((null_count / len(df)) * 100, 1)}%"

        spec = FIELD_SPECS.get(col)
        default_val = spec.type.default if spec else None
        
        # Default share
        if default_val is not None:
            try:
                if isinstance(default_val, (list, dict, set)):
                    default_count = int(s.apply(lambda x: x == default_val).sum())
                else:
                    default_count = int((s == default_val).sum())
                default_pct = f"{round((default_count / len(df)) * 100, 1)}%"
            except Exception:
                default_pct = "-"
        else:
            default_pct = "-"

        # Numeric stats
        num_s = pd.to_numeric(s, errors="coerce").dropna()
        if not num_s.empty:
            min_val = round(float(num_s.min()), 2)
            max_val = round(float(num_s.max()), 2)
            p50 = round(float(num_s.quantile(0.50)), 2)
            p95 = round(float(num_s.quantile(0.95)), 2)

            # Outlier check
            outlier_count = 0
            if spec and hasattr(spec.type, "soft_max") and spec.type.soft_max is not None:
                outlier_count += int((num_s > spec.type.soft_max).sum())
            elif spec and hasattr(spec.type, "max") and spec.type.max is not None:
                outlier_count += int((num_s > spec.type.max).sum())
            outlier_str = str(outlier_count)
        else:
            min_val = "-"
            max_val = "-"
            p50 = "-"
            p95 = "-"
            outlier_str = "0"

        lines.append(f"| `{col}` | `{dtype_str}` | {null_count} ({null_pct}) | {default_pct} | {min_val} | {max_val} | {p50} | {p95} | {outlier_str} |")

    # Coherence summary
    if "total_completed_projects" in df.columns and "received_projects" in df.columns:
        comp = pd.to_numeric(df["total_completed_projects"], errors="coerce").fillna(0)
        recv = pd.to_numeric(df["received_projects"], errors="coerce").fillna(0)
        recv_less = int((recv < comp).sum())
        lines.append("\n## Relational Coherence Integrity\n")
        lines.append(f"- `received_projects < total_completed_projects` breaches: **{recv_less}**")

    return "\n".join(lines) + "\n"
