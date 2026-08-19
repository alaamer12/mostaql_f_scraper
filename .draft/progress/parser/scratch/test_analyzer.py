"""
test_analyzer.py
----------------
Focused tests for analyzer.py's own concern: structural (class/id) field
extraction + the identifier-blind label-driven cross-check, run against a
real captured project page (project_1.html).

Run: python test_analyzer.py
Exits non-zero if any assertion fails.
"""

import os
import sys
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyzer

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


def _load_soup(filename):
    with open(os.path.join(HERE, filename), "r", encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "html.parser")


def test_structural_meta_extract_core_fields():
    soup = _load_soup("project_1.html")
    structural = analyzer.structural_meta_extract(soup)
    for label in ["حالة المشروع", "تاريخ النشر", "الميزانية", "مدة التنفيذ"]:
        check(
            f"structural_meta_extract: '{label}' present and non-empty",
            bool(structural.get(label)),
            detail=str(structural.get(label)),
        )


def test_cross_check_robustness_core_fields_are_robust():
    soup = _load_soup("project_1.html")
    fields, weak_points = analyzer.cross_check_robustness(soup)
    by_label = {f["label"]: f for f in fields}
    for label in ["حالة المشروع", "الميزانية", "مدة التنفيذ", "تاريخ التسجيل", "معدل التوظيف"]:
        entry = by_label.get(label)
        check(
            f"cross_check_robustness: '{label}' resolves ROBUST or STRUCTURAL_SELECTOR_OUTDATED",
            entry is not None and entry["robustness"] in ("ROBUST", "STRUCTURAL_SELECTOR_OUTDATED"),
            detail=str(entry),
        )
    check("cross_check_robustness: returns a list (possibly empty) of weak points", isinstance(weak_points, list))


def test_classify_value_placeholder_and_percent():
    check(
        "classify_value: 'لم يحسب بعد' flagged as placeholder",
        "NOT_YET_CALCULATED_PLACEHOLDER" in analyzer.classify_value("لم يحسب بعد"),
    )
    check(
        "classify_value: '83.33%' flagged as percentage",
        "PERCENTAGE_VALUE" in analyzer.classify_value("83.33%"),
    )
    check(
        "classify_value: '$100.00 - $250.00' flagged as range",
        "RANGE_VALUE" in analyzer.classify_value("$100.00 - $250.00"),
    )
    check(
        "classify_value: None value flagged as NULL_OR_MISSING",
        "NULL_OR_MISSING" in analyzer.classify_value(None),
    )


def test_analyze_file_reports_skills_bug_workaround():
    filepath = os.path.join(HERE, "project_1.html")
    analysis = analyzer.analyze_file(filepath)
    skills_entries = [f for f in analysis["fields_found"] if f.get("field") == "skills"]
    check(
        "analyze_file: reads skills directly from ul.skills (not via meta-row)",
        len(skills_entries) == 1 and isinstance(skills_entries[0].get("values"), list),
        detail=str(skills_entries),
    )


TESTS = [
    test_structural_meta_extract_core_fields,
    test_cross_check_robustness_core_fields_are_robust,
    test_classify_value_placeholder_and_percent,
    test_analyze_file_reports_skills_bug_workaround,
]


def main():
    print("Running analyzer.py focused tests...")
    for t in TESTS:
        print(f"\n[{t.__name__}]")
        t()

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
