import pytest
from bs4 import BeautifulSoup
from src.services.inference import (
    stem,
    normalize_ws,
    classify_value_types,
    flatten,
    extract_candidates,
    score_all,
    resolve_fields,
    infer_fields,
)

def test_stemming():
    assert stem("المشاريع") == "مشاريع"
    assert stem("إكمال") == "كمال" or stem("إكمال") == "اكمال" or "كمال" in stem("إكمال")
    assert stem("بالتأكيد") != ""
    assert stem("التنفيذ") == "تنفيذ"

def test_classify_value_types():
    assert "PERCENT" in classify_value_types("100%")
    assert "PERCENT" in classify_value_types("85.5 %")
    assert "NUMBER" in classify_value_types("42")
    assert "FLOAT" in classify_value_types("4.8")
    assert "PLACEHOLDER" in classify_value_types("لم يحسب بعد")
    assert "PLACEHOLDER" in classify_value_types("غير محدد")
    assert "DATE" in classify_value_types("2024-05-12")

def test_flatten_dom():
    html = """
    <div class="user-stats">
        <span>معدل التوظيف</span>
        <div>100%</div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    tokens = flatten(soup)
    assert len(tokens) >= 3
    texts = [t.text for t in tokens]
    assert "100%" in texts
    assert "معدل" in texts

def test_infer_fields_stats():
    html = """
    <div id="user-stats">
        <table>
            <tr>
                <td>معدل التوظيف</td>
                <td>95%</td>
            </tr>
            <tr>
                <td>إكمال المشاريع</td>
                <td>100%</td>
            </tr>
            <tr>
                <td>المشاريع المكتملة</td>
                <td>12</td>
            </tr>
            <tr>
                <td>متوسط سرعة الرد</td>
                <td>ساعة واحدة</td>
            </tr>
        </table>
    </div>
    """
    res = infer_fields(html)
    assert "employment_rate" in res
    assert "95%" in res["employment_rate"]["value"]
    assert "completion_rate" in res
    assert "100%" in res["completion_rate"]["value"]
    assert "total_completed_projects" in res
    assert "12" in res["total_completed_projects"]["value"]
