import pytest
from bs4 import BeautifulSoup
from src.services.analyzer import (
    classify_value,
    find_label_elements,
    walk_to_value,
    label_driven_extract,
    structural_profile_extract,
    cross_check_fields,
    clean_numeric_value,
    clean_percentage_str,
    is_placeholder,
)

def test_classify_value():
    assert "PERCENTAGE_VALUE" in classify_value("100%")
    assert "ARABIC_INDIC_DIGITS_PRESENT" in classify_value("١٢")
    assert "NOT_YET_CALCULATED_PLACEHOLDER" in classify_value("لم يحسب بعد")
    assert "FLOAT_VALUE" in classify_value("4.9")
    assert "INT_VALUE" in classify_value("15")

def test_label_driven_extract():
    html = """
    <div class="custom-card">
        <div class="field-item">
            <span class="label">إكمال المشاريع</span>
            <span class="val">100%</span>
        </div>
        <div class="field-item">
            <span class="label">المشاريع المكتملة</span>
            <span class="val">8</span>
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    extracted, debug = label_driven_extract(soup)
    assert extracted.get("completion_rate") == "100%"
    assert extracted.get("total_completed_projects") == "8"

def test_clean_numeric_value():
    assert clean_numeric_value("100%", default=0.0) == 100.0
    assert clean_numeric_value("١٥", default=0.0) == 15.0
    assert clean_numeric_value("لم يحسب بعد", default=0.0) == 0.0
    assert clean_numeric_value(None, default=5.0) == 5.0
    assert clean_percentage_str("لم يحسب بعد", default="100.0%") == "100.0%"
    assert clean_percentage_str("95 %") == "95.0%"

def test_cross_check_fields():
    structural = {"completion_rate": "100%", "total_completed_projects": "10"}
    label_driven = {"completion_rate": "100%", "total_completed_projects": "10", "rehire_rate": "50%"}
    report = cross_check_fields(structural, label_driven)
    assert report["completion_rate"]["verdict"] == "ROBUST"
    assert report["total_completed_projects"]["verdict"] == "ROBUST"
    assert report["rehire_rate"]["verdict"] == "LABEL_ONLY"
