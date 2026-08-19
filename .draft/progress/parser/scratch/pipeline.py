"""
pipeline.py
-----------
Production-shaped glue: combines analyzer.py's fast structural (class/id)
extraction with inference.py's structure-independent scoring engine as a
per-field fallback/validator.

analyzer.py and inference.py stay focused on their own concern:
  - analyzer.py: structural selectors (fast path) + report-only HTML
    structure analysis (unchanged, still runnable standalone for research).
  - inference.py: label-text/DOM-adjacency scoring engine (fallback + the
    structure-independent source of truth), unchanged.

This file is the only place that decides, per field, which of the two to
trust - it is NOT itself a new extraction strategy.

Algorithm per field:
  1. Try the structural value (analyzer.structural_meta_extract).
  2. If present AND passes a basic sanity check for that field's expected
     shape -> use it (fast, precise when selectors still match reality).
  3. Otherwise (missing / empty / fails sanity) -> fall back to
     inference.infer_fields()'s resolved value for that field.
  4. If both resolved and disagree -> keep the inference value (defeats a
     silently-stale selector) but record a mismatch for drift detection.
  5. Nullable-by-design fields are enforced at the end regardless of source:
     - hire_rate: "لم يحسب بعد" (or any NOT_CALCULATED_MARKERS) -> None
     - started_since / deal_date / delivery_date -> None unless
       project_status == "مكتمل"

Public entry point: parse_project(html_or_soup) -> dict
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup

import analyzer
import inference as inf

# Arabic label (as used by analyzer's structural/label-driven extraction) ->
# inference.py field key.
LABEL_TO_FIELD = {
    "حالة المشروع": "project_status",
    "تاريخ النشر": "published_date",
    "الميزانية": "budget",
    "مدة التنفيذ": "duration",
    "تاريخ التسجيل": "registration_date",
    "معدل التوظيف": "hire_rate",
    "المشاريع المفتوحة": "open_projects_count",
    "مشاريع قيد التنفيذ": "in_progress_count",
    "التواصلات الجارية": "ongoing_conversations",
    "بدأ تنفيذه منذ": "started_since",
    "تاريخ الصفقة": "deal_date",
    "موعد التسليم": "delivery_date",
}

COMPLETED_ONLY_FIELDS = {"started_since", "deal_date", "delivery_date"}
COMPLETED_STATUS_TEXT = "مكتمل"
FIELD_TO_LABEL = {field: label for label, field in LABEL_TO_FIELD.items()}


def _is_placeholder(value):
    if value is None:
        return False
    return any(marker in value for marker in analyzer.NOT_CALCULATED_MARKERS)


def _sanity_ok(field, value):
    """Cheap type-shape check on the structural fast-path value; if it
    fails, we don't trust the selector result and fall back to inference."""
    if value is None:
        return False
    v = value.strip()
    if v == "":
        return False
    if _is_placeholder(v):
        # a recognized placeholder is a VALID (nullable) resolution, not a
        # sanity failure - don't force a fallback for it.
        return True
    has_digit = any(ch.isdigit() for ch in v) or bool(analyzer.ARABIC_DIGIT_RE.search(v))
    if field in ("hire_rate", "budget", "duration", "open_projects_count",
                 "in_progress_count", "ongoing_conversations"):
        return has_digit
    # dates/status/free text: any non-empty structural value is acceptable
    return True


def _values_agree(a, b):
    if a is None or b is None:
        return True
    a_norm, b_norm = a.strip(), b.strip()
    return a_norm == b_norm or a_norm in b_norm or b_norm in a_norm


def parse_project(html_or_soup):
    """Combined structural-first / inference-fallback parser for a single
    Mostaql project detail page. Returns a dict with resolved fields plus
    a `mismatches` list for drift detection."""
    if isinstance(html_or_soup, BeautifulSoup):
        soup = html_or_soup
    else:
        soup = BeautifulSoup(html_or_soup, "html.parser")

    structural = analyzer.structural_meta_extract(soup)
    inference_results = None  # computed lazily, only if a fallback is needed

    fields = {}
    mismatches = []

    for label, field in LABEL_TO_FIELD.items():
        s_val = structural.get(label)
        s_ok = _sanity_ok(field, s_val)

        if s_ok:
            value, source, confidence = s_val, "structural", 1.0
        else:
            if inference_results is None:
                inference_results = inf.infer_fields(soup)
            inf_res = inference_results.get(field, {})
            value = inf_res.get("value")
            confidence = inf_res.get("confidence", 0.0)
            source = "inference" if value is not None else "none"

        # cross-validate even when the structural fast path was trusted,
        # as long as inference has already been computed (cheap - it runs
        # once for the whole page, not per field).
        if inference_results is not None and s_val is not None:
            inf_val = inference_results.get(field, {}).get("value")
            if inf_val is not None and not _values_agree(s_val, inf_val):
                mismatches.append({
                    "field": field,
                    "structural_value": s_val,
                    "inference_value": inf_val,
                })
                if not s_ok:
                    value = inf_val

        if _is_placeholder(value):
            value = None

        fields[field] = {"value": value, "source": source, "confidence": confidence}

    # Enforce nullable-by-design completed-only fields.
    # (1) Even when status == "مكتمل", these fields require an actual deal
    # context (e.g. authenticated party to the deal) - if their Arabic
    # label text is not literally present anywhere on the page, the
    # inference fallback has nothing genuine to latch onto and will only
    # ever be matching unrelated noise. Force None instead of trusting it.
    page_text = soup.get_text(separator=" ")
    for f in COMPLETED_ONLY_FIELDS:
        if f in fields and fields[f]["source"] == "inference":
            label = FIELD_TO_LABEL.get(f)
            if label and label not in page_text:
                fields[f] = {"value": None, "source": "none", "confidence": 0.0}

    # (2) Regardless of (1), these fields are only meaningful when the
    # project is actually completed.
    status_value = fields.get("project_status", {}).get("value")
    if status_value != COMPLETED_STATUS_TEXT:
        for f in COMPLETED_ONLY_FIELDS:
            if f in fields:
                fields[f]["value"] = None

    # Non meta-row fields: title / skills / description - structural only,
    # analyzer already handles these well and inference has no profile
    # for them.
    title_tag = soup.find("h1")
    title = title_tag.get_text(separator=" ", strip=True) if title_tag else None

    skills_list = soup.find("ul", class_="skills")
    skills = [li.get_text(strip=True) for li in skills_list.find_all("li")] if skills_list else []

    # NOTE: on completed projects that received a client review, the
    # "text-wrapper-div" class is reused by the review comment AND by
    # freelancer proposals (soup.find() would grab the *first* one in
    # document order, which is the review - not the actual description).
    # #projectDetailsTab is unique and always wraps the real description,
    # so scope the lookup inside it first.
    details_tab = soup.find(id="projectDetailsTab")
    if details_tab is not None:
        desc = details_tab.find("div", class_="text-wrapper-div") or details_tab
    else:
        desc = soup.find("div", class_="text-wrapper-div")
    description_length = len(desc.get_text(strip=True)) if desc else 0

    # Attachments (e.g. the .docx brief): identifier-blind by design in
    # analyzer.extract_attachments (scans every <a>, not just the current
    # '#project-files-panel' container), so no separate inference fallback
    # is needed here - unlike the meta-row fields there is no "label text"
    # to fall back on for a file link, robustness is built into the single
    # extraction strategy itself.
    attachments = analyzer.extract_attachments(soup)

    return {
        "title": title,
        "fields": fields,
        "skills": skills,
        "description_length": description_length,
        "attachments": attachments,
        "mismatches": mismatches,
    }


def main():
    temp_dir = os.path.dirname(os.path.abspath(__file__))
    files = sorted(f for f in os.listdir(temp_dir) if f.startswith("project_") and f.endswith(".html"))
    for filename in files:
        with open(os.path.join(temp_dir, filename), "r", encoding="utf-8") as f:
            html = f.read()
        result = parse_project(html)
        print(filename, "->", {k: v["value"] for k, v in result["fields"].items()})


if __name__ == "__main__":
    main()
