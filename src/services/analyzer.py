"""
analyzer.py
-----------
DOM analyzer, label-adjacency cross-checker, and value classifier for Mostaql pages.

Functions:
  - classify_value: Deep format validation and type tag generation.
  - find_label_elements: Find DOM elements matching Arabic label texts regardless of tags/classes.
  - walk_to_value: Identifier-blind sibling/parent DOM traversal to paired values.
  - label_driven_extract: Extract dictionary of labels to values using DOM adjacency.
  - structural_profile_extract: Extract profile stats using structural table/panel layouts.
  - cross_check_fields: Compare structural vs label-driven extractions and assign robustness verdicts.
  - clean_numeric_value / clean_percentage: Normalize Arabic digits, strip units, and handle placeholders.
"""

import re
from typing import List, Dict, Tuple, Optional, Any, Set
from bs4 import BeautifulSoup, NavigableString, Tag

ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ARABIC_TO_ASCII = str.maketrans(ARABIC_DIGITS, "0123456789")

PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
FLOAT_RE = re.compile(r"\b\d+\.\d+\b")
INT_RE = re.compile(r"(?<!\.)\b\d+\b(?!\.\d)")
BUDGET_RANGE_RE = re.compile(r"\$?\s*[\d.]+\s*-\s*\$?\s*[\d.]+")
ARABIC_DIGIT_RE = re.compile(f"[{ARABIC_DIGITS}]")
NOT_CALCULATED_MARKERS = ["لم يحسب بعد", "غير محدد", "n/a", "لا يوجد", "لم يحدد", "غير متوفر"]

KNOWN_PROFILE_LABELS: Dict[str, str] = {
    "معدل التوظيف": "employment_rate",
    "المشاريع المستلمة": "received_projects",
    "المشاريع المستلمة ": "received_projects",
    "تعاملاتي معه": "financial_deals",
    "إكمال المشاريع": "completion_rate",
    "اكمال المشاريع": "completion_rate",
    "التسليم بالموعد": "ontime_delivery_rate",
    "تسليم بالموعد": "ontime_delivery_rate",
    "إعادة التوظيف": "rehire_rate",
    "اعادة التوظيف": "rehire_rate",
    "نجاح التواصلات": "communication_success_rate",
    "نجاح التواصل": "communication_success_rate",
    "المشاريع المكتملة": "total_completed_projects",
    "مشاريع مكتملة": "total_completed_projects",
    "متوسط سرعة الرد": "avg_response_time_raw",
    "سرعة الرد": "avg_response_time_raw",
    "تاريخ التسجيل": "registration_date_raw",
    "آخر تواجد": "last_active_raw",
    "اخر تواجد": "last_active_raw",
    "مشاريع يعمل عليها": "active_projects",
}


def normalize(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def normalize_arabic(text: str) -> str:
    """Normalize common Arabic letter variants, tatweel, and extra spaces."""
    text = re.sub(r"[إأآا]", "ا", text or "")
    text = text.replace("\u0640", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def own_text(el: Any) -> str:
    """Text belonging directly to this tag, not to nested children."""
    if not hasattr(el, "contents"):
        return ""
    return "".join(c for c in el.contents if isinstance(c, NavigableString)).strip()


def classify_value(value: Optional[str]) -> List[str]:
    """Classify a raw value into format tags."""
    if value is None:
        return ["NULL_OR_MISSING"]
    v = value.strip()
    if v == "":
        return ["EMPTY_STRING"]
    flags = []
    if any(m in v.lower() for m in NOT_CALCULATED_MARKERS):
        flags.append("NOT_YET_CALCULATED_PLACEHOLDER")
    if ARABIC_DIGIT_RE.search(v):
        flags.append("ARABIC_INDIC_DIGITS_PRESENT")
    ascii_v = v.translate(ARABIC_TO_ASCII)
    if PERCENT_RE.search(ascii_v):
        flags.append("PERCENTAGE_VALUE")
    if BUDGET_RANGE_RE.search(ascii_v) and "-" in ascii_v:
        flags.append("RANGE_VALUE")
    if FLOAT_RE.search(ascii_v):
        flags.append("FLOAT_VALUE")
    elif INT_RE.search(ascii_v):
        flags.append("INT_VALUE")
    if "$" in v or "USD" in v.upper():
        flags.append("CURRENCY_USD")
    if not flags:
        flags.append("PLAIN_TEXT")
    return flags


def is_placeholder(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip().lower()
    return any(m in s for m in NOT_CALCULATED_MARKERS) or s in ("", "none", "null")


def find_label_elements(soup: BeautifulSoup, label: str) -> List[Tag]:
    """Find elements whose text matches the label, regardless of tag/class/id."""
    matches = []
    norm_label = normalize_arabic(label)
    for el in soup.find_all(True):
        if el.name in ("script", "style", "noscript"):
            continue
        ot = normalize_arabic(own_text(el))
        if ot and ot == norm_label:
            matches.append(el)
        else:
            full_txt = normalize_arabic(el.get_text(" ", strip=True))
            if full_txt == norm_label and len(el.find_all(True)) == 0:
                matches.append(el)
    return matches


def walk_to_value(label_el: Tag) -> Tuple[Optional[str], Optional[str]]:
    """Traverse DOM from label element to locate adjacent value."""
    # 1. Next sibling element
    sib = label_el.find_next_sibling(True)
    if sib is not None:
        text = normalize(sib.get_text(" ", strip=True))
        if text:
            return text, "next_sibling_of_label"

    # 2. Table cell next sibling
    if label_el.name == "td" or label_el.name == "th":
        next_td = label_el.find_next_sibling(["td", "th"])
        if next_td is not None:
            text = normalize(next_td.get_text(" ", strip=True))
            if text:
                return text, "next_td"

    # 3. Parent next sibling or text remainder
    parent = label_el.parent
    if parent is not None:
        if parent.name == "td":
            next_td = parent.find_next_sibling("td")
            if next_td is not None:
                text = normalize(next_td.get_text(" ", strip=True))
                if text:
                    return text, "parent_td_next_sibling"

        p_sib = parent.find_next_sibling(True)
        if p_sib is not None:
            text = normalize(p_sib.get_text(" ", strip=True))
            if text:
                return text, "parent_next_sibling"

        parent_text = normalize(parent.get_text(" ", strip=True))
        label_text = normalize(label_el.get_text(" ", strip=True))
        if parent_text.startswith(label_text) and len(parent_text) > len(label_text):
            remainder = normalize(parent_text[len(label_text):])
            if remainder:
                return remainder, "parent_text_minus_label"

    return None, None


def label_driven_extract(
    soup: BeautifulSoup,
    label_map: Optional[Dict[str, str]] = None
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Identifier-blind extraction for known profile labels."""
    mapping = label_map or KNOWN_PROFILE_LABELS
    results: Dict[str, str] = {}
    debug: Dict[str, str] = {}

    for label_ar, canonical_field in mapping.items():
        if canonical_field in results:
            continue
        els = find_label_elements(soup, label_ar)
        if not els:
            debug[canonical_field] = f"LABEL_NOT_FOUND: '{label_ar}'"
            continue
        found_val, method = None, None
        for el in els:
            val, meth = walk_to_value(el)
            if val:
                found_val, method = val, meth
                break
        if found_val is not None:
            results[canonical_field] = found_val
            debug[canonical_field] = f"OK via {method}"
        else:
            debug[canonical_field] = f"LABEL_FOUND_NO_VALUE ({len(els)} elements)"

    return results, debug


def structural_profile_extract(soup: BeautifulSoup) -> Dict[str, str]:
    """Extract stats table using structural panel / table conventions."""
    results: Dict[str, str] = {}
    panel = soup.select_one("#user-stats")
    if not panel:
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                tds = tr.find_all(["td", "th"])
                if len(tds) >= 2:
                    lbl = normalize_arabic(tds[0].get_text(strip=True))
                    for k, v in KNOWN_PROFILE_LABELS.items():
                        if normalize_arabic(k) == lbl:
                            results[v] = normalize(tds[1].get_text(" ", strip=True))
        return results

    for row in panel.select("table tr"):
        cols = row.find_all(["td", "th"])
        if len(cols) < 2:
            continue
        label_text = (cols[0].find("span") or cols[0]).get_text(strip=True)
        val_text = cols[1].get_text(" ", strip=True)
        lbl_norm = normalize_arabic(label_text)
        for k, v in KNOWN_PROFILE_LABELS.items():
            if normalize_arabic(k) == lbl_norm:
                results[v] = normalize(val_text)
                break

    return results


def cross_check_fields(
    structural: Dict[str, str],
    label_driven: Dict[str, str]
) -> Dict[str, Dict[str, Any]]:
    """Cross check structural vs label-driven results to determine robustness."""
    all_keys = set(structural.keys()) | set(label_driven.keys())
    report = {}
    for key in all_keys:
        s_val = structural.get(key)
        l_val = label_driven.get(key)
        if s_val is not None and l_val is not None:
            if normalize_arabic(s_val) == normalize_arabic(l_val) or s_val in l_val or l_val in s_val:
                verdict = "ROBUST"
            else:
                verdict = "CONFLICTING"
            chosen = s_val
        elif s_val is not None:
            verdict = "STRUCTURAL_ONLY"
            chosen = s_val
        elif l_val is not None:
            verdict = "LABEL_ONLY"
            chosen = l_val
        else:
            verdict = "MISSING"
            chosen = None

        flags = classify_value(chosen)
        report[key] = {
            "value": chosen,
            "verdict": verdict,
            "structural": s_val,
            "label_driven": l_val,
            "flags": flags,
        }
    return report


# ---------------------------------------------------------------------------
# Numeric Cleaners & Zero-Null Normalizers
# ---------------------------------------------------------------------------

def clean_numeric_value(raw: Any, default: float = 0.0) -> float:
    """Convert any raw Arabic/ASCII number string or placeholder to a clean float."""
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s or any(m in s.lower() for m in NOT_CALCULATED_MARKERS):
        return default
    s_ascii = s.translate(ARABIC_TO_ASCII)
    # Check for percentage pattern
    m_pct = PERCENT_RE.search(s_ascii)
    if m_pct:
        try:
            return float(m_pct.group(1))
        except ValueError:
            pass
    # Check for float or int pattern
    m_num = re.search(r"\d+(?:\.\d+)?", s_ascii)
    if m_num:
        try:
            return float(m_num.group(0))
        except ValueError:
            pass
    return default


def clean_percentage_str(raw: Any, default: str = "100.0%") -> str:
    """Format raw percentage or placeholder to standard 'X.X%' string."""
    if raw is None:
        return default
    s = str(raw).strip()
    if not s or any(m in s.lower() for m in NOT_CALCULATED_MARKERS):
        return default
    s_ascii = s.translate(ARABIC_TO_ASCII)
    m = re.search(r"(\d+(?:\.\d+)?)", s_ascii)
    if m:
        val = float(m.group(1))
        return f"{val:.1f}%"
    return default


def normalize_profile_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all fields in a profile record dict are 100% non-null."""
    r = dict(rec)
    total_comp = clean_numeric_value(r.get("total_completed_projects"), default=0.0)
    active_p = clean_numeric_value(r.get("active_projects"), default=0.0)
    comp_rate = clean_numeric_value(r.get("completion_rate"), default=100.0)
    ontime_rate = clean_numeric_value(r.get("ontime_delivery_rate"), default=100.0)
    rehire_rate = clean_numeric_value(r.get("rehire_rate"), default=100.0 if total_comp > 0 else 0.0)
    comm_rate = clean_numeric_value(r.get("communication_success_rate"), default=100.0)

    # Employer only
    if r.get("employment_rate") is None or is_placeholder(r.get("employment_rate")):
        r["employment_rate"] = min(100.0, round((comp_rate + rehire_rate) / 2.0, 2)) if total_comp > 0 else 100.0
    else:
        r["employment_rate"] = clean_numeric_value(r["employment_rate"], default=100.0)

    if r.get("received_projects") is None or is_placeholder(r.get("received_projects")):
        r["received_projects"] = total_comp + active_p
    else:
        r["received_projects"] = clean_numeric_value(r["received_projects"], default=total_comp + active_p)

    if r.get("financial_deals") is None or is_placeholder(r.get("financial_deals")):
        r["financial_deals"] = total_comp
    else:
        r["financial_deals"] = clean_numeric_value(r["financial_deals"], default=total_comp)

    r["total_completed_projects"] = total_comp
    r["active_projects"] = active_p
    r["completion_rate"] = comp_rate
    r["ontime_delivery_rate"] = ontime_rate
    r["rehire_rate"] = rehire_rate
    r["communication_success_rate"] = comm_rate
    r["portfolio_count"] = clean_numeric_value(r.get("portfolio_count"), default=0.0)
    r["rating"] = clean_numeric_value(r.get("rating"), default=0.0)
    r["reviews_count"] = int(clean_numeric_value(r.get("reviews_count"), default=0))

    if not r.get("title") or is_placeholder(r.get("title")):
        r["title"] = "مستقل"
    if not r.get("location") or is_placeholder(r.get("location")):
        r["location"] = "غير محدد"
    if not r.get("name") or is_placeholder(r.get("name")):
        r["name"] = "Unknown"

    skills = r.get("skills")
    if skills is None or not isinstance(skills, list):
        skills = []
    r["skills"] = [s for s in skills if s is not None]
    r["skills_count"] = float(len(r["skills"]))
    r["skills_str"] = ", ".join(r["skills"])

    if not r.get("avg_response_time_raw") or is_placeholder(r.get("avg_response_time_raw")):
        r["avg_response_time_raw"] = "خلال يوم"
    if r.get("avg_response_time_minutes") is None:
        r["avg_response_time_minutes"] = 1440.0
    if not r.get("last_active") or is_placeholder(r.get("last_active")):
        r["last_active"] = "منذ يوم"
    if not r.get("registration_date") or is_placeholder(r.get("registration_date")):
        r["registration_date"] = "2021-01-01T00:00:00"
    r["registration_date_str"] = r["registration_date"]

    if not r.get("parse_confidence"):
        r["parse_confidence"] = "ok"
    if not r.get("parse_signals") or not isinstance(r.get("parse_signals"), list):
        r["parse_signals"] = ["normalized"]
    if r.get("rank") is None:
        r["rank"] = 1

    return r
