"""
test_inference.py
------------------
Validates inference.py against:
  A) targeted unit tests - small hostile snippets isolating one attack
     each (split words, synonyms, reordered DOM, ambiguous numbers, etc.)
  B) the full adversarial_mostaql.html fixture, checked field-by-field.

Run: python3 test_inference.py
Exits non-zero if any assertion fails; prints a pass/fail table either way.
"""

import os
import sys
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inference as inf

HERE = os.path.dirname(os.path.abspath(__file__))

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL  {name}  -- {detail}")


def run_snippet(html):
    soup = BeautifulSoup(html, "html.parser")
    return inf.infer_fields(soup)


# ---------------------------------------------------------------------------
# A) Targeted snippets - one attack at a time
# ---------------------------------------------------------------------------

def test_split_words_duration():
    html = """
    <div>
      <span>و</span><span>قت</span> <span>ال</span><span>تنفيذ</span>
      <span>1</span><span>1</span> <span>يوما</span>
    </div>
    """
    results = run_snippet(html)
    d = results["duration"]
    check(
        "split_words_duration: extracts '11'",
        d["value"] is not None and "11" in d["value"],
        detail=str(d),
    )
    check(
        "split_words_duration: confidence > 0.3",
        d["confidence"] > 0.3,
        detail=str(d),
    )


def test_synonym_label_for_budget():
    html = """
    <div>التكلفة التقديرية</div>
    <div>$ 500 - $ 800</div>
    """
    results = run_snippet(html)
    b = results["budget"]
    check(
        "synonym_budget: extracts a range containing 500",
        b["value"] is not None and "500" in b["value"],
        detail=str(b),
    )
    check(
        "synonym_budget: type RANGE detected",
        "RANGE" in b["evidence"]["types"],
        detail=str(b),
    )


def test_reordered_dom_status_vs_budget():
    html = """
    <section><div>$300</div></section>
    <section><div>حالة المشروع</div><div>مفتوح</div></section>
    """
    results = run_snippet(html)
    b = results["budget"]
    check(
        "reordered_dom: budget still found despite appearing before status block",
        b["value"] is not None and "300" in b["value"],
        detail=str(b),
    )


def test_ambiguous_number_no_context():
    html = "<div><span>30</span></div>"
    results = run_snippet(html)
    # With zero context, no field should claim high confidence.
    best_conf = max(v["confidence"] for v in results.values())
    check(
        "ambiguous_no_context: no field claims high confidence for a bare unlabeled number",
        best_conf < 0.5,
        detail=f"best_conf={best_conf}",
    )


def test_competing_fields_for_same_number():
    # "11" near both duration stems AND a currency-less generic 'بدأ منذ'
    # phrase - duration should win because of the explicit unit 'يوما'.
    html = """
    <div>
      وقت التنفيذ
      <span>11</span>
      يوما
    </div>
    <div>
      بدأ تنفيذه منذ
    </div>
    """
    results = run_snippet(html)
    d = results["duration"]
    s = results["started_since"]
    check(
        "competing_fields: duration wins with unit present",
        d["confidence"] >= s["confidence"],
        detail=f"duration={d['confidence']} started_since={s['confidence']}",
    )


def test_hidden_decorative_number_not_picked_for_real_field():
    html = """
    <div>معدل التوظيف <span>30</span>%</div>
    <button data-count="999">like</button>
    <span style="display:none">42</span>
    """
    results = run_snippet(html)
    hr = results["hire_rate"]
    check(
        "decorative_numbers: hire_rate correctly resolves to 30, not 999/42",
        hr["value"] is not None and "30" in hr["value"] and "999" not in hr["value"] and "42" not in hr["value"],
        detail=str(hr),
    )


def test_date_not_misread_as_plain_number_field():
    html = "<div>تاريخ التسجيل <span>2024/03/10</span></div>"
    results = run_snippet(html)
    rd = results["registration_date"]
    check(
        "date_field: registration_date captures the date token",
        rd["value"] is not None and "2024" in rd["value"],
        detail=str(rd),
    )


def test_missing_label_field_stays_low_confidence_or_none():
    html = "<div><div>7</div></div>"  # open_projects_count with no label at all
    results = run_snippet(html)
    op = results["open_projects_count"]
    check(
        "missing_label: open_projects_count is either not claimed or low-confidence",
        op["value"] is None or op["confidence"] < 0.5,
        detail=str(op),
    )


def test_currency_word_variant_not_only_symbol():
    html = "<div>الميزانية <span>1200</span> دولار</div>"
    results = run_snippet(html)
    b = results["budget"]
    check(
        "currency_word_variant: budget recognizes 'دولار' not just '$'",
        b["value"] is not None and "1200" in b["value"],
        detail=str(b),
    )


TARGETED_TESTS = [
    test_split_words_duration,
    test_synonym_label_for_budget,
    test_reordered_dom_status_vs_budget,
    test_ambiguous_number_no_context,
    test_competing_fields_for_same_number,
    test_hidden_decorative_number_not_picked_for_real_field,
    test_date_not_misread_as_plain_number_field,
    test_missing_label_field_stays_low_confidence_or_none,
    test_currency_word_variant_not_only_symbol,
]


# ---------------------------------------------------------------------------
# B) Full adversarial fixture
# ---------------------------------------------------------------------------

def test_full_fixture():
    path = os.path.join(HERE, "adversarial_mostaql.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    results = run_snippet(html)

    expectations = {
        "budget": lambda r: r["value"] and "500" in r["value"] and "800" in r["value"],
        "duration": lambda r: r["value"] and "11" in r["value"],
        "hire_rate": lambda r: r["value"] and "30" in r["value"],
        "in_progress_count": lambda r: r["value"] and "3" in r["value"],
        "ongoing_conversations": lambda r: r["value"] and "5" in r["value"],
        "registration_date": lambda r: r["value"] and "2024" in r["value"],
        "started_since": lambda r: r["value"] and "15" in r["value"],
        "published_date": lambda r: r["value"] and "2024" in r["value"],
    }

    print("\n  -- full fixture field results --")
    for field, result in results.items():
        print(f"    {field}: value={result['value']!r} conf={result['confidence']} "
              f"strategy={result['strategy']}")

    for field, expect_fn in expectations.items():
        r = results[field]
        check(f"fixture[{field}]: matches expected value", expect_fn(r), detail=str(r))

    # Sanity: probabilities/confidences must be in [0,1]
    for field, r in results.items():
        check(
            f"fixture[{field}]: confidence within [0,1]",
            0.0 <= r["confidence"] <= 1.0,
            detail=str(r["confidence"]),
        )

    # Sanity: decorative junk numbers (999, 42) must not win any field
    for field, r in results.items():
        val = r["value"] or ""
        check(
            f"fixture[{field}]: does not accidentally resolve to decorative junk (999/42)",
            "999" not in val and "42" not in val,
            detail=val,
        )

    # Requirement: ambiguous / low-signal candidates should surface
    # competing_candidates rather than silently vanishing.
    check(
        "fixture[hire_rate]: exposes competing_candidates list (even if empty is valid, key must exist)",
        "competing_candidates" in results["hire_rate"],
        detail=str(results["hire_rate"].keys()),
    )

    return results


def main():
    print("Running targeted adversarial snippet tests...")
    for t in TARGETED_TESTS:
        print(f"\n[{t.__name__}]")
        t()

    print("\nRunning full adversarial fixture test...")
    fixture_results = test_full_fixture()

    print(f"\n{'=' * 60}")
    print(f"TOTAL: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\nFailed checks:")
        for name, detail in FAIL:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    else:
        print("All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
