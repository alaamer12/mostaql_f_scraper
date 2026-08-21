"""Schema and linguistic normalization package for Mostaql profile scraper."""

from src.schema.arabic import (
    normalize_arabic_text,
    normalize_digits,
    normalize_alif,
    normalize_taa_marboota,
    normalize_yaa,
    normalize_hamza,
    strip_tashkeel,
    strip_tatweel,
    strip_bidi_controls,
    strip_prepositions,
    parse_arabic_duration,
    parse_arabic_inflected_count,
)

from src.schema.types import (
    ParseOutcome,
    ValueType,
    Text,
    Enum,
    Count,
    Percentage,
    Rating,
    Money,
    Duration,
    ArabicDate,
    RelativeTime,
    ListOf,
    OneOf,
)

from src.schema.spec import (
    FieldSpec,
    FIELD_SPECS,
    check_record_coherence,
)

from src.schema.frame import (
    pandas_dtypes,
    apply_dtypes,
    validate_frame,
)

__all__ = [
    "normalize_arabic_text",
    "normalize_digits",
    "normalize_alif",
    "normalize_taa_marboota",
    "normalize_yaa",
    "normalize_hamza",
    "strip_tashkeel",
    "strip_tatweel",
    "strip_bidi_controls",
    "strip_prepositions",
    "parse_arabic_duration",
    "parse_arabic_inflected_count",
    "ParseOutcome",
    "ValueType",
    "Text",
    "Enum",
    "Count",
    "Percentage",
    "Rating",
    "Money",
    "Duration",
    "ArabicDate",
    "RelativeTime",
    "ListOf",
    "OneOf",
    "FieldSpec",
    "FIELD_SPECS",
    "check_record_coherence",
    "pandas_dtypes",
    "apply_dtypes",
    "validate_frame",
]
