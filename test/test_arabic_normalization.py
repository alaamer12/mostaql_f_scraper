"""Tests for Arabic linguistic normalization and inflection engine."""

import pytest
from src.schema.arabic import (
    strip_tashkeel,
    strip_tatweel,
    strip_bidi_controls,
    normalize_digits,
    normalize_alif,
    normalize_taa_marboota,
    normalize_yaa,
    normalize_hamza,
    normalize_arabic_text,
    strip_prepositions,
    parse_arabic_duration,
    parse_arabic_inflected_count,
)


def test_tashkeel_and_tatweel_stripping():
    text_with_tashkeel = "مُسْتَقِلٌّ ذُو خِبْرَةٍ"
    assert strip_tashkeel(text_with_tashkeel) == "مستقل ذو خبرة"

    text_with_tatweel = "مــــســــتــــقــــل"
    assert strip_tatweel(text_with_tatweel) == "مستقل"


def test_bidi_controls_removal():
    text_with_bidi = "\u200e100%\u200f \u202aمكتمل\u202c"
    cleaned = strip_bidi_controls(text_with_bidi)
    assert "\u200e" not in cleaned
    assert "\u200f" not in cleaned
    assert "100% مكتمل" in cleaned


def test_digits_normalization():
    arabic_indic = "٨٢٦٦ مشروع و ٥٠٪"
    assert normalize_digits(arabic_indic) == "8266 مشروع و 50%"


def test_orthographic_letter_normalization():
    # Alif
    assert normalize_alif("أحمد إبراهيم آلاء ٱسم") == "احمد ابراهيم الاء اسم"
    # Taa Marboota
    assert normalize_taa_marboota("خبرة هندسة برمجة") == "خبره هندسه برمجه"
    # Yaa / Alef Maksura
    assert normalize_yaa("علي على مصطفى") == "علي علي مصطفي"
    # Hamza
    assert normalize_hamza("مسؤولية بريئة") == "مسءولية بريءة"


def test_preposition_stripping():
    assert strip_prepositions("منذ سنتين") == "سنتين"
    assert strip_prepositions("خلال يومين") == "يومين"
    assert strip_prepositions("في ساعتين") == "ساعتين"
    assert strip_prepositions("قبل 3 أيام") == "3 ايام"


def test_duration_parsing_inflections_and_phrases():
    # Singular, dual, plural, composite
    assert parse_arabic_duration("دقيقة") == 1.0
    assert parse_arabic_duration("دقيقتان") == 2.0
    assert parse_arabic_duration("دقيقتين") == 2.0
    assert parse_arabic_duration("ساعة") == 60.0
    assert parse_arabic_duration("ساعتان") == 120.0
    assert parse_arabic_duration("ساعتين") == 120.0
    assert parse_arabic_duration("يوم") == 1440.0
    assert parse_arabic_duration("يومان") == 2880.0
    assert parse_arabic_duration("يومين") == 2880.0
    assert parse_arabic_duration("خلال يوم") == 1440.0
    assert parse_arabic_duration("خلال يومين") == 2880.0
    assert parse_arabic_duration("منذ سنتين") == 2.0 * 525600.0
    assert parse_arabic_duration("3 ساعات و 48 دقيقة") == (3 * 60) + 48
    assert parse_arabic_duration("ساعة و 22 دقيقة") == 60 + 22
    assert parse_arabic_duration("44 دقيقة") == 44.0

    # Placeholders return None
    assert parse_arabic_duration("لم يحسب بعد") is None
    assert parse_arabic_duration("غير محدد") is None


def test_inflected_count_resolution():
    assert parse_arabic_inflected_count("مشروع") == 1
    assert parse_arabic_inflected_count("مشروعان") == 2
    assert parse_arabic_inflected_count("مشروعين") == 2
    assert parse_arabic_inflected_count("15 مشروع") == 15
    assert parse_arabic_inflected_count("صفقتان") == 2
    assert parse_arabic_inflected_count("تقييمان") == 2
