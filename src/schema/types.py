"""Value-type library for Mostaql profile field parsing, normalization and validation.

Every type defines:
- parse(raw) -> ParseOutcome (total function, never raises)
- validate(value) -> list[str]
- format(value) -> str
- pandas_dtype: standard pandas/pyarrow dtype
- default: standard default value
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, List, Optional, Sequence, Set, Union

import pandas as pd
from pandas.api.types import CategoricalDtype

from src.schema.arabic import (
    normalize_arabic_text,
    normalize_digits,
    strip_bidi_controls,
    parse_arabic_duration,
    parse_arabic_inflected_count,
    ARABIC_MONTHS,
)


@dataclass(frozen=True)
class ParseOutcome:
    """Immutable result of parsing a raw field string."""
    value: Any
    raw: str = ""
    issues: List[str] = field(default_factory=list)
    confidence: float = 1.0
    matched_type: str = ""


PLACEHOLDER_SUBSTRINGS = ["لم يحسب", "غير محدد", "غير متوفر", "لا يوجد"]
PLACEHOLDER_EXACT = {"-", "—", "–", "n/a", "none", "null", "nan"}


def is_placeholder_text(text: str) -> bool:
    """Check if text is a placeholder string."""
    if not text:
        return True
    s = text.strip().lower()
    if s in PLACEHOLDER_EXACT:
        return True
    return any(p in s for p in PLACEHOLDER_SUBSTRINGS)


class ValueType(ABC):
    """Abstract Base Class for all schema value types."""
    default: Any = None
    pandas_dtype: Union[str, CategoricalDtype] = "object"

    def parse(self, raw: Any) -> ParseOutcome:
        """Parse raw input safely. Never raises an unhandled exception."""
        try:
            return self._parse_impl(raw)
        except Exception as exc:
            return ParseOutcome(
                value=self.default,
                raw=str(raw) if raw is not None else "",
                issues=["internal_error", f"exception:{type(exc).__name__}"],
                confidence=0.0,
                matched_type=self.__class__.__name__,
            )

    @abstractmethod
    def _parse_impl(self, raw: Any) -> ParseOutcome:
        """Subclasses implement specific parsing logic."""
        pass

    @abstractmethod
    def validate(self, value: Any) -> List[str]:
        """Check if value violates bounds or constraints."""
        pass

    def format(self, value: Any) -> str:
        """Format value for presentation / export."""
        return str(value) if value is not None else ""


class Text(ValueType):
    """Plain or normalized text."""
    def __init__(
        self,
        min_len: int = 0,
        max_len: Optional[int] = None,
        strip: bool = True,
        normalize_arabic: bool = False,
        default: str = "",
        dtype: str = "string",
    ):
        self.min_len = min_len
        self.max_len = max_len
        self.strip = strip
        self.normalize_arabic = normalize_arabic
        self.default = default
        self.pandas_dtype = dtype

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["empty"], confidence=0.0, matched_type="Text")
        
        s = str(raw)
        if self.strip:
            s = s.strip()
        s = strip_bidi_controls(s)
        if self.normalize_arabic:
            s = normalize_arabic_text(s, normalize_letters=False)

        issues = self.validate(s)
        conf = 0.5 if issues else 1.0
        return ParseOutcome(value=s, raw=str(raw), issues=issues, confidence=conf, matched_type="Text")

    def validate(self, value: Any) -> List[str]:
        issues = []
        if not isinstance(value, str):
            return ["not_a_string"]
        if len(value) < self.min_len:
            issues.append("below_min_length")
        if self.max_len is not None and len(value) > self.max_len:
            issues.append("above_max_length")
        return issues


class Enum(ValueType):
    """Restricted set of allowed strings."""
    def __init__(
        self,
        allowed: Sequence[str],
        default: str = "",
        dtype: Optional[CategoricalDtype] = None,
    ):
        self.allowed = list(allowed)
        self.default = default or (self.allowed[0] if self.allowed else "")
        self.pandas_dtype = dtype or CategoricalDtype(categories=self.allowed)

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["empty"], confidence=0.0, matched_type="Enum")
        s = strip_bidi_controls(str(raw)).strip()
        if s in self.allowed:
            return ParseOutcome(value=s, raw=str(raw), issues=[], confidence=1.0, matched_type="Enum")
        
        # Try normalized matching
        s_norm = normalize_arabic_text(s, normalize_letters=True)
        for cand in self.allowed:
            if normalize_arabic_text(cand, normalize_letters=True) == s_norm:
                return ParseOutcome(value=cand, raw=str(raw), issues=[], confidence=0.9, matched_type="Enum")

        return ParseOutcome(
            value=self.default,
            raw=str(raw),
            issues=["unrecognized_enum_value"],
            confidence=0.0,
            matched_type="Enum",
        )

    def validate(self, value: Any) -> List[str]:
        if value not in self.allowed:
            return ["unrecognized_enum_value"]
        return []


class Count(ValueType):
    """Integer non-negative count with soft and hard caps."""
    def __init__(
        self,
        min: int = 0,
        soft_max: int = 500,
        hard_max: int = 5000,
        default: int = 0,
        dtype: str = "Int64",
    ):
        self.min = min
        self.soft_max = soft_max
        self.hard_max = hard_max
        self.default = default
        self.pandas_dtype = dtype

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["placeholder"], confidence=0.0, matched_type="Count")
        
        raw_str = str(raw).strip()
        if not raw_str or is_placeholder_text(raw_str):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["placeholder"], confidence=0.0, matched_type="Count")

        # Clean digits and punctuation
        cleaned = normalize_digits(strip_bidi_controls(raw_str))
        # Handle (0) or +500 or 1,234
        cleaned = cleaned.replace(",", "").replace("+", "").replace("(", "").replace(")", "").strip()
        
        # Try direct int conversion via pd.to_numeric
        num_series = pd.to_numeric([cleaned], errors="coerce")
        if not pd.isna(num_series[0]):
            val = int(round(num_series[0]))
        else:
            # Try grammatical inflection (e.g. 'مشروعان' -> 2)
            inflected = parse_arabic_inflected_count(raw_str)
            if inflected is not None:
                val = inflected
            else:
                return ParseOutcome(value=self.default, raw=raw_str, issues=["unparsable"], confidence=0.0, matched_type="Count")

        issues = self.validate(val)
        conf = 0.5 if "above_soft_max" in issues else (0.1 if "above_hard_max" in issues or "below_min" in issues else 1.0)
        return ParseOutcome(value=val, raw=raw_str, issues=issues, confidence=conf, matched_type="Count")

    def validate(self, value: Any) -> List[str]:
        issues = []
        try:
            v = int(value)
        except (ValueError, TypeError):
            return ["invalid_type"]
        if v < self.min:
            issues.append("below_min")
        if v > self.hard_max:
            issues.append("above_hard_max")
        elif v > self.soft_max:
            issues.append("above_soft_max")
        return issues

    def format(self, value: Any) -> str:
        return str(int(value)) if value is not None else "0"


class Percentage(ValueType):
    """Percentage value between 0.0 and 100.0."""
    def __init__(
        self,
        min: float = 0.0,
        max: float = 100.0,
        decimals: int = 2,
        unit: str = "%",
        default: float = 0.0,
        dtype: str = "Float64",
    ):
        self.min = min
        self.max = max
        self.decimals = decimals
        self.unit = unit
        self.default = default
        self.pandas_dtype = dtype

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["placeholder"], confidence=0.0, matched_type="Percentage")
        
        raw_str = str(raw).strip()
        if not raw_str or is_placeholder_text(raw_str):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["placeholder"], confidence=0.0, matched_type="Percentage")

        cleaned = normalize_digits(strip_bidi_controls(raw_str))
        cleaned = cleaned.replace("%", "").replace("٪", "").replace(",", ".").strip()
        
        num_series = pd.to_numeric([cleaned], errors="coerce")
        if pd.isna(num_series[0]):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["unparsable"], confidence=0.0, matched_type="Percentage")

        val = round(float(num_series[0]), self.decimals)
        issues = self.validate(val)
        conf = 0.2 if issues else 1.0
        return ParseOutcome(value=val, raw=raw_str, issues=issues, confidence=conf, matched_type="Percentage")

    def validate(self, value: Any) -> List[str]:
        issues = []
        try:
            v = float(value)
        except (ValueError, TypeError):
            return ["invalid_type"]
        if v < self.min:
            issues.append("below_min")
        if v > self.max:
            issues.append("above_max")
        return issues

    def format(self, value: Any) -> str:
        if value is None:
            return f"0.0{self.unit}"
        return f"{float(value):.{self.decimals}f}{self.unit}"


class Rating(ValueType):
    """Rating value between 0.0 and 5.0."""
    def __init__(
        self,
        min: float = 0.0,
        max: float = 5.0,
        decimals: int = 2,
        default: float = 0.0,
        dtype: str = "Float64",
    ):
        self.min = min
        self.max = max
        self.decimals = decimals
        self.default = default
        self.pandas_dtype = dtype

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["placeholder"], confidence=0.0, matched_type="Rating")

        raw_str = str(raw).strip()
        if not raw_str or is_placeholder_text(raw_str):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["placeholder"], confidence=0.0, matched_type="Rating")

        cleaned = normalize_digits(strip_bidi_controls(raw_str))
        cleaned = cleaned.replace("/5", "").replace(",", ".").strip()
        
        num_series = pd.to_numeric([cleaned], errors="coerce")
        if pd.isna(num_series[0]):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["unparsable"], confidence=0.0, matched_type="Rating")

        val = round(float(num_series[0]), self.decimals)
        issues = self.validate(val)
        conf = 0.2 if issues else 1.0
        return ParseOutcome(value=val, raw=raw_str, issues=issues, confidence=conf, matched_type="Rating")

    def validate(self, value: Any) -> List[str]:
        issues = []
        try:
            v = float(value)
        except (ValueError, TypeError):
            return ["invalid_type"]
        if v < self.min:
            issues.append("below_min")
        if v > self.max:
            issues.append("above_max")
        return issues

    def format(self, value: Any) -> str:
        return f"{float(value):.{self.decimals}f}" if value is not None else "0.0"


class Money(ValueType):
    """Monetary amount."""
    def __init__(
        self,
        currency: str = "USD",
        min: float = 0.0,
        soft_max: float = 1_000_000.0,
        default: float = 0.0,
        dtype: str = "Float64",
    ):
        self.currency = currency
        self.min = min
        self.soft_max = soft_max
        self.default = default
        self.pandas_dtype = dtype

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["placeholder"], confidence=0.0, matched_type="Money")
        raw_str = str(raw).strip()
        cleaned = normalize_digits(strip_bidi_controls(raw_str))
        cleaned = cleaned.replace("$", "").replace(",", "").strip()

        num_series = pd.to_numeric([cleaned], errors="coerce")
        if pd.isna(num_series[0]):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["unparsable"], confidence=0.0, matched_type="Money")

        val = float(num_series[0])
        issues = self.validate(val)
        return ParseOutcome(value=val, raw=raw_str, issues=issues, confidence=1.0 if not issues else 0.5, matched_type="Money")

    def validate(self, value: Any) -> List[str]:
        issues = []
        try:
            v = float(value)
        except (ValueError, TypeError):
            return ["invalid_type"]
        if v < self.min:
            issues.append("below_min")
        if v > self.soft_max:
            issues.append("above_soft_max")
        return issues


class Duration(ValueType):
    """Duration represented in minutes."""
    def __init__(
        self,
        unit: str = "minutes",
        min: float = 0.0,
        max: float = 43200.0,  # 30 days
        default: float = 1440.0, # 1 day default
        dtype: str = "Float64",
    ):
        self.unit = unit
        self.min = min
        self.max = max
        self.default = default
        self.pandas_dtype = dtype

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["placeholder"], confidence=0.0, matched_type="Duration")

        raw_str = str(raw).strip()
        if not raw_str or is_placeholder_text(raw_str):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["placeholder"], confidence=0.0, matched_type="Duration")

        # If already numeric
        num_series = pd.to_numeric([raw_str], errors="coerce")
        if not pd.isna(num_series[0]):
            val = float(num_series[0])
        else:
            val = parse_arabic_duration(raw_str)
            if val is None:
                return ParseOutcome(value=self.default, raw=raw_str, issues=["unparsable"], confidence=0.0, matched_type="Duration")

        issues = self.validate(val)
        conf = 0.5 if issues else 1.0
        return ParseOutcome(value=val, raw=raw_str, issues=issues, confidence=conf, matched_type="Duration")

    def validate(self, value: Any) -> List[str]:
        issues = []
        try:
            v = float(value)
        except (ValueError, TypeError):
            return ["invalid_type"]
        if v < self.min:
            issues.append("below_min")
        if v > self.max:
            issues.append("above_max")
        return issues


class ArabicDate(ValueType):
    """Date parsed from Arabic format (e.g. '27 ديسمبر 2023') to ISO string."""
    def __init__(
        self,
        min: str = "2013-01-01",
        max: str = "now",
        default: str = "2021-01-01T00:00:00",
        dtype: str = "string",
    ):
        self.min = min
        self.max = max
        self.default = default
        self.pandas_dtype = dtype

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["placeholder"], confidence=0.0, matched_type="ArabicDate")

        raw_str = str(raw).strip()
        if not raw_str or is_placeholder_text(raw_str):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["placeholder"], confidence=0.0, matched_type="ArabicDate")

        cleaned = normalize_digits(strip_bidi_controls(raw_str))

        # Check ISO format first
        if "T" in cleaned or (len(cleaned) == 10 and cleaned.count("-") == 2):
            try:
                dt = datetime.fromisoformat(cleaned)
                iso_str = dt.isoformat()
                issues = self.validate(iso_str)
                return ParseOutcome(value=iso_str, raw=raw_str, issues=issues, confidence=1.0, matched_type="ArabicDate")
            except ValueError:
                pass

        # Try Arabic month format: "27 ديسمبر 2023" or "01 يونيو 2023"
        m = re.search(r"(\d{1,2})\s+([^\d\s]+(?:\s+[^\d\s]+)?)\s+(\d{4})", cleaned)
        if m:
            day = int(m.group(1))
            month_str = normalize_arabic_text(m.group(2), normalize_letters=True)
            year = int(m.group(3))

            month = None
            for name, num in ARABIC_MONTHS.items():
                if normalize_arabic_text(name, normalize_letters=True) == month_str:
                    month = num
                    break

            if month is not None:
                try:
                    dt = datetime(year, month, day)
                    iso_str = dt.isoformat()
                    issues = self.validate(iso_str)
                    return ParseOutcome(value=iso_str, raw=raw_str, issues=issues, confidence=1.0, matched_type="ArabicDate")
                except ValueError:
                    pass

        return ParseOutcome(value=self.default, raw=raw_str, issues=["unparsable"], confidence=0.0, matched_type="ArabicDate")

    def validate(self, value: Any) -> List[str]:
        issues = []
        if not isinstance(value, str) or not value:
            return ["invalid_date"]
        try:
            dt = datetime.fromisoformat(value)
            min_dt = datetime.fromisoformat(self.min)
            if dt < min_dt:
                issues.append("before_min_date")
            max_dt = datetime.now() if self.max == "now" else datetime.fromisoformat(self.max)
            if dt > max_dt:
                issues.append("future_date")
        except ValueError:
            issues.append("invalid_date_format")
        return issues


class RelativeTime(ValueType):
    """Relative Arabic time expression (e.g. 'منذ دقيقة', 'منذ سنتين')."""
    def __init__(self, default: str = "غير محدد", dtype: str = "string"):
        self.default = default
        self.pandas_dtype = dtype

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=self.default, raw="", issues=["placeholder"], confidence=0.0, matched_type="RelativeTime")
        raw_str = strip_bidi_controls(str(raw)).strip()
        if not raw_str or is_placeholder_text(raw_str):
            return ParseOutcome(value=self.default, raw=raw_str, issues=["placeholder"], confidence=0.0, matched_type="RelativeTime")
        return ParseOutcome(value=raw_str, raw=raw_str, issues=[], confidence=1.0, matched_type="RelativeTime")

    def validate(self, value: Any) -> List[str]:
        return []


class ListOf(ValueType):
    """Homogeneous list of item_type values."""
    def __init__(
        self,
        item_type: ValueType,
        min_items: int = 0,
        max_items: int = 100,
        default: Optional[List[Any]] = None,
    ):
        self.item_type = item_type
        self.min_items = min_items
        self.max_items = max_items
        self.default = default if default is not None else []
        self.pandas_dtype = "object"

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        if raw is None:
            return ParseOutcome(value=list(self.default), raw="", issues=[], confidence=1.0, matched_type="ListOf")

        items_raw = raw if isinstance(raw, (list, tuple, set)) else [raw]
        parsed_items = []
        issues = []
        conf_sum = 0.0

        for it in items_raw:
            outcome = self.item_type.parse(it)
            parsed_items.append(outcome.value)
            issues.extend(outcome.issues)
            conf_sum += outcome.confidence

        if len(parsed_items) < self.min_items:
            issues.append("below_min_items")
        if len(parsed_items) > self.max_items:
            issues.append("above_max_items")

        avg_conf = (conf_sum / max(1, len(parsed_items))) if parsed_items else 1.0
        return ParseOutcome(
            value=parsed_items,
            raw=str(raw),
            issues=list(set(issues)),
            confidence=avg_conf,
            matched_type="ListOf",
        )

    def validate(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return ["not_a_list"]
        issues = []
        if len(value) < self.min_items:
            issues.append("below_min_items")
        if len(value) > self.max_items:
            issues.append("above_max_items")
        for it in value:
            issues.extend(self.item_type.validate(it))
        return list(set(issues))


class OneOf(ValueType):
    """Union type trying member types in ordered priority."""
    def __init__(self, types: Sequence[ValueType], default: Any = None):
        self.types = list(types)
        self.default = default if default is not None else (self.types[0].default if self.types else None)
        self.pandas_dtype = "object"

    def _parse_impl(self, raw: Any) -> ParseOutcome:
        for t in self.types:
            outcome = t.parse(raw)
            if "unparsable" not in outcome.issues and "placeholder" not in outcome.issues:
                return ParseOutcome(
                    value=outcome.value,
                    raw=outcome.raw,
                    issues=outcome.issues,
                    confidence=outcome.confidence,
                    matched_type=outcome.matched_type or t.__class__.__name__,
                )

        # If none cleanly parsed, return fallback
        first_type = self.types[0] if self.types else None
        return ParseOutcome(
            value=self.default,
            raw=str(raw) if raw is not None else "",
            issues=["unrecognized_union_value"],
            confidence=0.0,
            matched_type=(first_type.__class__.__name__ if first_type else "None"),
        )

    def validate(self, value: Any) -> List[str]:
        for t in self.types:
            issues = t.validate(value)
            if not issues:
                return []
        return ["no_union_branch_valid"]
