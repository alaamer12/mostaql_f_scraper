"""Tests for the ValueType library (Count, Percentage, Rating, Duration, ArabicDate, OneOf, etc.)."""

import pytest
from src.schema.types import (
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


def test_count_type():
    c = Count(min=0, soft_max=500, hard_max=5000)
    
    # Normal numbers
    res = c.parse("150")
    assert res.value == 150
    assert not res.issues
    assert res.confidence == 1.0

    # Arabic-Indic & separators
    res = c.parse("٢٣٤")
    assert res.value == 234
    assert not res.issues

    res_comma = c.parse("١,٢٣٤")
    assert res_comma.value == 1234
    assert "above_soft_max" in res_comma.issues

    # Inflections
    res = c.parse("مشروعان")
    assert res.value == 2
    assert not res.issues

    # Placeholder
    res = c.parse("لم يحسب بعد")
    assert res.value == 0
    assert "placeholder" in res.issues
    assert res.confidence == 0.0

    # Outliers (soft max / hard max / negative)
    res_soft = c.parse("800")
    assert res_soft.value == 800
    assert "above_soft_max" in res_soft.issues
    assert res_soft.confidence == 0.5

    res_hard = c.parse("8266")
    assert res_hard.value == 8266
    assert "above_hard_max" in res_hard.issues
    assert res_hard.confidence == 0.1

    res_neg = c.parse("-5")
    assert res_neg.value == -5
    assert "below_min" in res_neg.issues


def test_percentage_type():
    p = Percentage(min=0.0, max=100.0, decimals=2)

    res = p.parse("97.92%")
    assert res.value == 97.92
    assert not res.issues
    assert res.confidence == 1.0

    # Arabic percent & bidi
    res = p.parse("\u200e١٠٠٪\u200f")
    assert res.value == 100.0
    assert not res.issues

    # Placeholder
    res = p.parse("لم يحسب بعد")
    assert res.value == 0.0
    assert "placeholder" in res.issues

    # Outlier
    res_outlier = p.parse("150.0%")
    assert res_outlier.value == 150.0
    assert "above_max" in res_outlier.issues


def test_rating_type():
    r = Rating(min=0.0, max=5.0)

    res = r.parse("4.85/5")
    assert res.value == 4.85
    assert not res.issues

    res = r.parse("(0)")
    assert res.value == 0.0

    res_bad = r.parse("7.5")
    assert res_bad.value == 7.5
    assert "above_max" in res_bad.issues


def test_duration_type():
    d = Duration(min=0.0, max=43200.0, default=1440.0)

    res = d.parse("3 ساعات و 48 دقيقة")
    assert res.value == 228.0
    assert not res.issues

    res = d.parse("غير محدد")
    assert res.value == 1440.0
    assert "placeholder" in res.issues


def test_arabic_date_type():
    ad = ArabicDate(min="2013-01-01", max="now")

    res = ad.parse("27 ديسمبر 2023")
    assert res.value.startswith("2023-12-27")
    assert not res.issues

    res = ad.parse("01 يونيو 2023")
    assert res.value.startswith("2023-06-01")
    assert not res.issues

    res = ad.parse("لم يحسب بعد")
    assert "placeholder" in res.issues


def test_one_of_union_type():
    union = OneOf([Count(), Text()])

    res_num = union.parse("42")
    assert res_num.value == 42
    assert res_num.matched_type == "Count"

    res_txt = union.parse("مهندس برمجيات")
    assert res_txt.value == "مهندس برمجيات"
    assert res_txt.matched_type == "Text"


def test_total_functions_never_raise():
    # Pass malformed, exotic and extreme inputs across all types
    types = [
        Text(),
        Enum(allowed=["a", "b"]),
        Count(),
        Percentage(),
        Rating(),
        Money(),
        Duration(),
        ArabicDate(),
        RelativeTime(),
        ListOf(Text()),
        OneOf([Count(), Text()]),
    ]
    crazy_inputs = [None, "", {}, [], 12345, object(), "A" * 10000, "\x00\xff", "\u200e\u200f\ufeff"]
    for t in types:
        for inp in crazy_inputs:
            outcome = t.parse(inp)
            assert isinstance(outcome.issues, list)
            assert outcome.value is not None or t.default is None
