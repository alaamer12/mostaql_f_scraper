"""
validators.py
-------------
Strict Zero-Null Validator & Fail-Fast Crash Reporting System.
Enforces zero tolerance for None / null values in parsed profiles and exported records.
"""

import os
import sys
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import is_dataclass, asdict

log = logging.getLogger(__name__)

CRASH_REPORTS_DIR = Path("outsourcing") / "crash_reports"


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

        data = asdict(profile) if is_dataclass(profile) else dict(profile)
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
