"""
analyzer.py
-----------
Single unified analyzer for Mostaql HTML pages (projects_list.html + project_N.html).

For each file it produces:
  - basic structure info (tag counts, inline/outline styles)
  - "fields_found": current class/id based extraction (STRUCTURAL strategy)
  - "robust_fields": identifier-blind cross-check for every known project field,
    comparing the STRUCTURAL extraction above against a LABEL-DRIVEN extraction
    that ignores tags/classes/ids entirely. It scans the whole DOM for any
    element whose own text exactly equals a known Arabic label (e.g.
    "الميزانية", "مدة التنفيذ"), then walks to the adjacent element via pure
    DOM position (next sibling / parent's next sibling / next <td>) to read
    the value - i.e. what would still work even if Mostaql renamed every
    class/id tomorrow.
  - each robust field gets a "robustness" verdict: ROBUST / CONFLICTING /
    FRAGILE (structural-only) / STRUCTURAL_SELECTOR_OUTDATED (label-only) /
    MISSING (not found by either) - plus numeric/format flags (percentage,
    float, range, Arabic-Indic digits, "لم يحسب بعد" placeholder, etc.)
  - "weak_points": plain-language list of anything that could break a naive
    parser (empty values, mismatches, optional/hidden fields).

Output per file: temp/<file>.analysis.txt
Aggregate output: temp/summary_report.json (structure/fields for all files)
                   temp/robust_summary_report.json (field robustness tally +
                   parser recommendation, project_* files only)
"""

import os
import re
import json
from bs4 import BeautifulSoup, NavigableString

import mimetypes
from pathlib import Path

TEMP_DIR = os.path.dirname(os.path.abspath(__file__))

# Known Arabic labels expected somewhere on a project-detail page, independent
# of whichever element currently wraps them.
KNOWN_LABELS = [
    "حالة المشروع",
    "تاريخ النشر",
    "الميزانية",
    "مدة التنفيذ",
    "المهارات",
    "تاريخ التسجيل",
    "معدل التوظيف",
    "المشاريع المفتوحة",
    "مشاريع قيد التنفيذ",
    "التواصلات الجارية",
    "بدأ تنفيذه منذ",
    "تاريخ الصفقة",
    "موعد التسليم",
    "صاحب المشروع",
]

ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ARABIC_TO_ASCII = str.maketrans(ARABIC_DIGITS, "0123456789")

PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
FLOAT_RE = re.compile(r"\d+\.\d+")
INT_RE = re.compile(r"(?<!\.)\b\d+\b(?!\.\d)")
BUDGET_RANGE_RE = re.compile(r"\$?\s*[\d.]+\s*-\s*\$?\s*[\d.]+")
ARABIC_DIGIT_RE = re.compile(f"[{ARABIC_DIGITS}]")
NOT_CALCULATED_MARKERS = ["لم يحسب بعد", "غير محدد", "N/A", "لا يوجد"]


def normalize(s):
    return re.sub(r"\s+", " ", s or "").strip()


def own_text(el):
    """Text belonging directly to this tag, not to nested children."""
    return "".join(c for c in el.contents if isinstance(c, NavigableString)).strip()


def classify_value(value):
    """Deeper numeric/format validation for a raw text value."""
    if value is None:
        return ["NULL_OR_MISSING"]
    v = value.strip()
    if v == "":
        return ["EMPTY_STRING"]
    flags = []
    if any(m in v for m in NOT_CALCULATED_MARKERS):
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


# ---------------------------------------------------------------------------
# Label-driven (identifier-blind) extraction
# ---------------------------------------------------------------------------

def find_label_elements(soup, label):
    """Elements whose own/leaf text matches `label` exactly, regardless of
    tag/class/id."""
    matches = []
    for el in soup.find_all(True):
        if normalize(own_text(el)) == label:
            matches.append(el)
        else:
            full_txt = normalize(el.get_text(" ", strip=True))
            if full_txt == label and len(el.find_all(True)) == 0:
                matches.append(el)
    return matches


def walk_to_value(label_el):
    """Identifier-blind heuristic to find the value paired with a label,
    purely via DOM adjacency (not by class name)."""
    sib = label_el.find_next_sibling(True)
    if sib is not None:
        text = normalize(sib.get_text(" ", strip=True))
        if text:
            return text, "next_sibling_of_label"

    if label_el.name == "td":
        next_td = label_el.find_next_sibling("td")
        if next_td is not None:
            text = normalize(next_td.get_text(" ", strip=True))
            if text:
                return text, "next_td"

    parent = label_el.parent
    if parent is not None:
        p_sib = parent.find_next_sibling(True)
        if p_sib is not None:
            text = normalize(p_sib.get_text(" ", strip=True))
            if text:
                return text, "parent_next_sibling"

        parent_text = normalize(parent.get_text(" ", strip=True))
        label_text = normalize(label_el.get_text(" ", strip=True))
        if parent_text.startswith(label_text) and parent_text != label_text:
            remainder = normalize(parent_text[len(label_text):])
            if remainder:
                return remainder, "parent_text_minus_label"

    return None, None


def label_driven_extract(soup):
    results, debug = {}, {}
    for label in KNOWN_LABELS:
        els = find_label_elements(soup, label)
        if not els:
            debug[label] = "LABEL_TEXT_NOT_FOUND_ANYWHERE"
            continue
        found_value, method_used = None, None
        for el in els:
            value, method = walk_to_value(el)
            if value:
                found_value, method_used = value, method
                break
        if found_value is not None:
            results[label] = found_value
            debug[label] = f"OK via {method_used} ({len(els)} candidate el(s))"
        else:
            debug[label] = f"LABEL_FOUND_BUT_NO_VALUE_ADJACENT ({len(els)} candidate el(s))"
    return results, debug


def structural_meta_extract(soup):
    """Same label->value map as the class/id based 'fields_found' extraction,
    reshaped as a dict for cross-checking against label_driven_extract()."""
    results = {}
    card = soup.find('div', id='project-meta-panel') or soup.find('div', class_='meta-container')
    if card:
        for row in card.find_all('div', class_='meta-row'):
            label = row.find('div', class_='meta-label')
            value = row.find('div', class_='meta-value')
            if label and value:
                results[normalize(label.get_text(strip=True))] = normalize(value.get_text(" ", strip=True))

    owner_card = soup.find('div', class_='profile_card')
    if owner_card:
        owner_stats = owner_card.find('table', class_='table')
        if owner_stats:
            for tr in owner_stats.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) == 2:
                    results[normalize(tds[0].get_text(strip=True))] = normalize(tds[1].get_text(" ", strip=True))
    return results


POSSIBLE_FILE_EXTENSIONS = [
    "docx", "doc", "pdf", "zip", "rar", "7z", "xlsx", "xls", "pptx", "ppt",
    "psd", "ai", "png", "jpg", "jpeg", "gif", "svg", "txt", "csv", "json",
    "sql", "sketch", "fig", "mp4", "mp3", "rtf",
]


# Get all extensions known to Python's MIME database
KNOWN_FILE_EXTENSIONS = {
    ext.lower().lstrip(".")
    for ext in mimetypes.types_map
}

KNOWN_FILE_EXTENSIONS = list(set(KNOWN_FILE_EXTENSIONS) | set(POSSIBLE_FILE_EXTENSIONS))

FILENAME_EXT_RE = re.compile(r"\.([A-Za-z0-9]{2,5})$")


def _attachment_from_link(link):
    """Build one attachment dict from an <a> tag, resolving the extension
    from whichever of (data-file-type attribute / a sibling "ext" badge /
    the filename's own suffix) is actually present - so a single missing
    signal (e.g. Mostaql drops data-file-type tomorrow) doesn't blank the
    field, since the other two are checked as well."""
    url = link.get('href')
    filename = normalize(link.get('title') or link.get_text(strip=True))
    if not filename:
        return None

    file_type = link.get('data-file-type')

    # The <a> is nested inside an inner '<li>' (its own list-meta item);
    # the badge/size siblings live in OTHER list-meta items one level up,
    # under the outer attachment '<li>'. Prefer an ancestor <li> whose
    # class mentions "attachment" (identifier-blind: substring match, not
    # exact), falling back to the immediate <li> parent if none is found.
    ext_badge = None
    container = link.find_parent('li', class_=lambda c: c and 'attachment' in c)
    if container is None:
        container = link.find_parent('li') or link.parent
    if container is not None:
        badge = container.find(lambda t: t.name == 'bdi' and t.has_attr('class')
                                and any('ext-file' in c for c in t['class']))
        if badge is not None:
            ext_badge = normalize(badge.get_text(strip=True)).lower()

    ext_from_name = None
    m = FILENAME_EXT_RE.search(filename)
    if m:
        ext_from_name = m.group(1).lower()

    extension = file_type or ext_badge or ext_from_name
    if extension is None or extension.lower() not in KNOWN_FILE_EXTENSIONS:
        # not actually a downloadable-file link (e.g. a plain text link) -
        # unless a data-file-type/ext badge explicitly said so, skip it.
        if file_type is None and ext_badge is None:
            return None

    size_text = None
    if container is not None:
        size_tag = container.find('small')
        if size_tag:
            size_text = normalize(size_tag.get_text(strip=True))

    requires_auth = bool(url) and ('/register' in url or '/login' in url)

    return {
        "filename": filename,
        "extension": extension,
        "url": url if not requires_auth else None,
        "raw_url": url,
        "requires_auth": requires_auth,
        "size_text": size_text,
    }


def extract_attachments(soup):
    """Project asset files (e.g. the brief .docx). Identifier-blind by
    design: instead of relying solely on '#project-files-panel'/'.attachment'
    (which would silently return nothing if Mostaql renames that container),
    it scans EVERY <a> tag on the page and recognizes an attachment link via
    any of three independent signals - a 'data-file-type' attribute, a
    sibling badge whose class merely *contains* "ext-file" (not an exact
    class-name match), or the file extension in the filename/title itself.
    A structural container match (when present) is still used as-is; it's
    the fallback signals that make this survive a full class/id rename.

    Observed structure (real completed-project page):
        <ul class="list-group attachments ..." id="project-files-panel">
          <li class="list-group-item attachment ...">
            <bdi class="label label-ext-file">DOCX</bdi>
            <a href="..." title="EmotifyAIDevBriefv3.docx"
               data-file-type="docx" data-grouping="project-1242939">
               EmotifyAIDevBriefv3.docx
            </a>
            <small class="text-muted">(15.99KB)</small>
          </li>
        </ul>

    WEAK POINT: for anonymous/unauthenticated requests, the <a href> does
    NOT point at the real file - it points at "/register?...". A parser
    that blindly downloads `href` for a not-logged-in session would fetch
    the registration page instead of the .docx. We surface this via
    `requires_auth` (and null out `url`, keeping `raw_url` for debugging)
    so callers can decide (e.g. warn / skip / login first) rather than
    silently downloading the wrong content."""
    attachments = []
    seen_urls = set()

    for link in soup.find_all('a'):
        att = _attachment_from_link(link)
        if att is None:
            continue
        key = att["raw_url"] or att["filename"]
        if key in seen_urls:
            continue
        seen_urls.add(key)
        attachments.append(att)

    return attachments


def cross_check_robustness(soup):
    """Compare STRUCTURAL vs LABEL-DRIVEN extraction for every known label and
    return a list of per-field verdicts plus a weak_points list."""
    structural = structural_meta_extract(soup)
    label_driven, label_debug = label_driven_extract(soup)

    all_labels = sorted(set(structural.keys()) | set(label_driven.keys()) | set(KNOWN_LABELS))
    fields, weak_points = [], []

    for label in all_labels:
        s_val, l_val = structural.get(label), label_driven.get(label)

        if s_val is not None and l_val is not None:
            agreement = "MATCH" if normalize(s_val) == normalize(l_val) else "MISMATCH"
        elif s_val is not None:
            agreement = "STRUCTURAL_ONLY"
        elif l_val is not None:
            agreement = "LABEL_ONLY"
        else:
            agreement = "NOT_FOUND_BY_EITHER"

        robustness = {
            "MATCH": "ROBUST",
            "MISMATCH": "CONFLICTING",
            "STRUCTURAL_ONLY": "FRAGILE (breaks if class/id renamed)",
            "LABEL_ONLY": "STRUCTURAL_SELECTOR_OUTDATED",
            "NOT_FOUND_BY_EITHER": "MISSING",
        }[agreement]

        value = s_val if s_val is not None else l_val
        fields.append({
            "label": label,
            "structural_value": s_val,
            "label_driven_value": l_val,
            "agreement": agreement,
            "robustness": robustness,
            "value_flags": classify_value(value),
            "debug": label_debug.get(label, "n/a"),
        })

        if agreement == "NOT_FOUND_BY_EITHER":
            weak_points.append(f"'{label}' not found by any strategy - field may be hidden/removed for this project")
        elif agreement == "MISMATCH":
            weak_points.append(f"'{label}' structural vs label-driven VALUES DIFFER: '{s_val}' vs '{l_val}'")
        elif agreement == "STRUCTURAL_ONLY":
            weak_points.append(f"'{label}' only found via current class/id selectors - fragile to redesign")
        if value is not None and value.strip() == "":
            weak_points.append(f"'{label}' resolved to an EMPTY value - treat as null, not error")

    return fields, weak_points


# ---------------------------------------------------------------------------
# Main per-file analysis (structure + structural fields + robustness)
# ---------------------------------------------------------------------------

def analyze_file(filepath):
    print(f"Analyzing {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, 'html.parser')
    analysis = {
        "filename": os.path.basename(filepath),
        "structure": {},
        "fields_found": [],
        "robust_fields": [],
        "styles": {"inline": False, "outline": []},
        "weak_points": [],
    }

    for link in soup.find_all('link', rel='stylesheet'):
        analysis["styles"]["outline"].append(link.get('href'))
    if soup.find_all(style=True):
        analysis["styles"]["inline"] = True

    tags_count = {}
    for tag in soup.find_all(True):
        tags_count[tag.name] = tags_count.get(tag.name, 0) + 1
    analysis["structure"]["tags_count"] = tags_count

    if "project_" in filepath:
        title_tag = soup.find('h1')
        if title_tag:
            analysis["fields_found"].append({"field": "title", "tag": "h1", "text": title_tag.get_text(separator=" ", strip=True)})
        else:
            analysis["weak_points"].append("Title (h1) not found")

        card = soup.find('div', id='project-meta-panel') or soup.find('div', class_='meta-container')
        if card:
            for row in card.find_all('div', class_='meta-row'):
                label = row.find('div', class_='meta-label')
                value = row.find('div', class_='meta-value')
                if label and value:
                    analysis["fields_found"].append({
                        "field": label.get_text(strip=True),
                        "value": value.get_text(separator=" ", strip=True),
                    })

        owner_card = soup.find('div', class_='profile_card')
        if owner_card:
            owner_name = owner_card.find('h5', class_='profile__name')
            if owner_name:
                analysis["fields_found"].append({"field": "owner_name", "text": owner_name.get_text(separator=" ", strip=True)})

            owner_stats = owner_card.find('table', class_='table')
            if owner_stats:
                for tr in owner_stats.find_all('tr'):
                    tds = tr.find_all('td')
                    if len(tds) == 2:
                        l_text = tds[0].get_text(strip=True)
                        v_text = tds[1].get_text(separator=" ", strip=True)
                        analysis["fields_found"].append({"field": "owner_stat_" + l_text, "value": v_text})

        desc = soup.find('div', class_='text-wrapper-div') or soup.find('div', id='projectDetailsTab')
        if desc:
            analysis["fields_found"].append({"field": "description", "found": True, "length": len(desc.text.strip())})
        else:
            analysis["weak_points"].append("Description container (text-wrapper-div) not found")

        skills_list = soup.find('ul', class_='skills')
        if skills_list:
            skills = [li.text.strip() for li in skills_list.find_all('li')]
            analysis["fields_found"].append({"field": "skills", "values": skills})

        # Identifier-blind cross-check (STRUCTURAL vs LABEL-DRIVEN)
        robust_fields, robust_weak_points = cross_check_robustness(soup)
        analysis["robust_fields"] = robust_fields
        analysis["weak_points"].extend(robust_weak_points)

    if "projects_list" in filepath:
        project_rows = soup.find_all('tr', class_='project-row')
        analysis["fields_found"].append({"field": "project_items_count", "value": len(project_rows)})

        if project_rows:
            first_row = project_rows[0]
            title_link = first_row.find('h2').find('a') if first_row.find('h2') else None
            meta = first_row.find('ul', class_='project__meta')
            if title_link:
                analysis["fields_found"].append({"field": "sample_list_title", "value": title_link.get_text(strip=True)})
            if meta:
                analysis["fields_found"].append({"field": "sample_list_meta", "value": meta.get_text(separator=" | ", strip=True)})
        else:
            project_items = soup.find_all('div', class_='project-item')
            if project_items:
                analysis["fields_found"].append({"field": "project_items_div_count", "value": len(project_items)})
            else:
                analysis["weak_points"].append("No project items (tr.project-row) found on list page")

    return analysis


def build_robust_summary(all_analysis):
    """Aggregate robust_fields across all project_* files into a tally +
    a plain-language parser recommendation per field."""
    tally = {}
    for res in all_analysis:
        if not res.get("robust_fields"):
            continue
        for entry in res["robust_fields"]:
            t = tally.setdefault(entry["label"], {})
            t[entry["robustness"]] = t.get(entry["robustness"], 0) + 1

    recommendation = {}
    for label, t in tally.items():
        total = sum(t.values())
        if t.get("ROBUST", 0) == total:
            rec = "SAFE to parse via Arabic label text matching, ignore current class/id names"
        elif t.get("MISSING", 0) > 0:
            rec = "OPTIONAL FIELD - can be legitimately absent/hidden per project, parser must treat as nullable"
        elif t.get("CONFLICTING", 0) > 0:
            rec = "INVESTIGATE - structural and label-driven extraction disagree on value"
        else:
            rec = "Use BOTH structural selectors AND label-text fallback (dual-strategy parser)"
        recommendation[label] = rec

    return {
        "files_analyzed": len(all_analysis),
        "field_robustness_tally": tally,
        "overall_recommendation": recommendation,
    }


def main():
    files = [f for f in os.listdir(TEMP_DIR) if f.endswith('.html')]
    all_analysis = []

    for filename in sorted(files):
        if filename == 'projects_list.html' or filename.startswith('project_'):
            filepath = os.path.join(TEMP_DIR, filename)
            res = analyze_file(filepath)
            all_analysis.append(res)

            report_path = filepath + ".analysis.txt"
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(json.dumps(res, indent=2, ensure_ascii=False))

    with open(os.path.join(TEMP_DIR, 'summary_report.json'), 'w', encoding='utf-8') as f:
        json.dump(all_analysis, f, indent=2, ensure_ascii=False)

    robust_summary = build_robust_summary(all_analysis)
    with open(os.path.join(TEMP_DIR, 'robust_summary_report.json'), 'w', encoding='utf-8') as f:
        json.dump(robust_summary, f, indent=2, ensure_ascii=False)

    print(f"Analyzed {len(all_analysis)} files. See summary_report.json and robust_summary_report.json for details.")


if __name__ == '__main__':
    main()
