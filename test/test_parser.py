"""
test_parser.py
Run: python test_parser.py

Fetches DavidLabib's real profile, runs every parsing function,
prints PASS/FAIL vs known expected values from screenshots.
"""

import re
import sys
import requests
from datetime import datetime
from bs4 import BeautifulSoup

# ── constants from scraper.py ──────────────────────────────────────────────

STAT_MAP = {
    "معدل التوظيف"      : "employment_rate",
    "المشاريع المستلمة" : "received_projects",
    "تعاملاتي معه"      : "financial_deals",
    "إكمال المشاريع"    : "completion_rate",
    "التسليم بالموعد"   : "ontime_delivery_rate",
    "إعادة التوظيف"     : "rehire_rate",
    "نجاح التواصلات"    : "communication_success_rate",
    "المشاريع المكتملة" : "total_completed_projects",
    "متوسط سرعة الرد"   : "avg_response_time_raw",
    "تاريخ التسجيل"     : "registration_date_raw",
    "آخر تواجد"         : "last_active_raw",
    "مشاريع يعمل عليها" : "active_projects",
}

ARABIC_MONTHS = {
    "يناير": 1,  "جانفي": 1,  "فبراير": 2, "فيفري": 2,
    "مارس": 3,   "أبريل": 4,  "ابريل": 4,  "مايو": 5,
    "ماي": 5,    "يونيو": 6,  "يونيه": 6,  "يوليو": 7,
    "يوليه": 7,  "أغسطس": 8,  "اغسطس": 8,  "أغسطص": 8,
    "سبتمبر": 9, "أكتوبر": 10,"اكتوبر": 10,"نوفمبر": 11,
    "ديسمبر": 12,
}

ARABIC_WORD_NUMS = {
    "دقيقة": 1, "دقيقتين": 2, "دقائق": 1,
    "ساعة": 60, "ساعتين": 120, "ساعات": 60,
    "يوم": 1440, "يومين": 2880, "أيام": 1440,
    "أسبوع": 10080, "أسبوعين": 20160,
}

ARABIC_DIGIT_WORDS = {
    "صفر": 0, "واحد": 1, "اثنين": 2, "ثلاثة": 3, "أربعة": 4,
    "خمسة": 5, "ستة": 6, "سبعة": 7, "ثمانية": 8, "تسعة": 9,
    "عشرة": 10, "عشرين": 20, "ثلاثين": 30, "أربعين": 40, "خمسين": 50,
}

# ── parsers (verbatim from scraper.py) ────────────────────────────────────

def parse_percentage(raw):
    try:    return float(re.sub(r"[^\d.]", "", raw))
    except: return 0.0

def parse_integer(raw):
    try:
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else 0
    except: return 0

def parse_dollar(raw):
    try:    return float(re.sub(r"[^\d.]", "", raw))
    except: return 0.0

def _normalize_arabic(text):
    text = re.sub(r"[إأآا]", "ا", text)
    text = text.replace("\u0640", "")
    return re.sub(r"\s+", " ", text).strip()

def parse_response_time(raw):
    if not raw: return 0
    raw = raw.strip()
    if "أقل من دقيقة" in raw or "أقل من" in raw: return 1
    total = 0
    normalized = re.sub(r"\s+و\s+", " ", raw)
    tokens = normalized.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ARABIC_WORD_NUMS:
            multiplier = ARABIC_WORD_NUMS[token]
            if token in ("ساعتين", "يومين", "أسبوعين", "دقيقتين"):
                total += multiplier
            else:
                total += 1 * multiplier
            i += 1; continue
        if token in ARABIC_DIGIT_WORDS:
            quantity = ARABIC_DIGIT_WORDS[token]
            if i+1 < len(tokens) and tokens[i+1] in ARABIC_WORD_NUMS:
                total += quantity * ARABIC_WORD_NUMS[tokens[i+1]]; i += 2
            else: i += 1
            continue
        digits = re.sub(r"[^\d٠-٩]", "", token)
        if digits:
            arabic_indic = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
            quantity = int(digits.translate(arabic_indic))
            if i+1 < len(tokens) and tokens[i+1] in ARABIC_WORD_NUMS:
                total += quantity * ARABIC_WORD_NUMS[tokens[i+1]]; i += 2
            else: i += 1
            continue
        i += 1
    return total if total > 0 else 0

def parse_registration_date(raw):
    if not raw: return None
    raw = raw.strip()
    from dateutil import parser as du_parser
    try:    return du_parser.parse(raw, dayfirst=True)
    except: pass
    norm = _normalize_arabic(raw)
    norm = re.sub(r"^[^\d]+", "", norm).strip()
    m = re.search(r"(\d{1,2})\s+([^\d\s]+)\s+(\d{4})", norm)
    if m:
        day_s, month_s, year_s = m.group(1), m.group(2), m.group(3)
        month_num = None
        for ar_name, num in ARABIC_MONTHS.items():
            if _normalize_arabic(ar_name) == _normalize_arabic(month_s):
                month_num = num; break
        if month_num:
            try:    return datetime(int(year_s), month_num, int(day_s))
            except: pass
    m2 = re.search(r"\b(20\d{2})\b", raw)
    if m2:
        try:    return datetime(int(m2.group(1)), 1, 1)
        except: pass
    return None

# ── _parse_profile_page verbatim from scraper.py ──────────────────────────

def _parse_profile_page(html, url):
    record = {
        "profile_url": url, "name": None, "title": None,
        "registration_date": None, "last_active": None,
        "active_projects": 0, "portfolio_count": 0,
        "employment_rate": 0.0, "received_projects": 0, "financial_deals": 0.0,
        "completion_rate": 0.0, "ontime_delivery_rate": 0.0, "rehire_rate": 0.0,
        "communication_success_rate": 0.0, "total_completed_projects": 0,
        "avg_response_time_raw": None, "avg_response_time_minutes": 0,
        "skills": [], "skills_count": 0,
    }
    soup = BeautifulSoup(html, "lxml")

    name_tag = (soup.select_one("h1.usercard__username bdi")
                or soup.select_one("h1 bdi")
                or soup.select_one(".usercard__username bdi"))
    if name_tag:
        record["name"] = name_tag.get_text(strip=True)

    briefcase = soup.select_one("ul.user__meta li .fa-briefcase")
    if briefcase:
        parent_li = briefcase.find_parent("li")
        if parent_li:
            record["title"] = parent_li.get_text(strip=True)
    if not record["title"]:
        for li in soup.select("ul.user__meta li"):
            text = li.get_text(strip=True)
            if text and "mostaql" not in text.lower() and len(text) > 2:
                record["title"] = text; break

    stats_panel = soup.select_one("#user-stats")
    if stats_panel:
        for row in stats_panel.select("table.table-meta tbody tr"):
            cols = row.find_all("td")
            if len(cols) < 2: continue
            label_cell = cols[0]
            label_span = label_cell.find("span")
            label = (label_span or label_cell).get_text(strip=True)
            value_cell = cols[1]
            time_tag = value_cell.find("time")
            value = (value_cell.get_text(separator=" ", strip=True)
                     if time_tag else value_cell.get_text(strip=True))
            field = STAT_MAP.get(label)
            if not field:
                label_norm = _normalize_arabic(label)
                for k, v in STAT_MAP.items():
                    if _normalize_arabic(k) == label_norm:
                        field = v; break
            if not field: continue
            if field == "employment_rate":            record["employment_rate"]            = parse_percentage(value)
            elif field == "received_projects":        record["received_projects"]           = parse_integer(value)
            elif field == "financial_deals":          record["financial_deals"]             = parse_dollar(value)
            elif field == "completion_rate":          record["completion_rate"]             = parse_percentage(value)
            elif field == "ontime_delivery_rate":     record["ontime_delivery_rate"]        = parse_percentage(value)
            elif field == "rehire_rate":              record["rehire_rate"]                 = parse_percentage(value)
            elif field == "communication_success_rate": record["communication_success_rate"] = parse_percentage(value)
            elif field == "total_completed_projects": record["total_completed_projects"]    = parse_integer(value)
            elif field == "avg_response_time_raw":
                record["avg_response_time_raw"]     = value
                record["avg_response_time_minutes"] = parse_response_time(value)
            elif field == "registration_date_raw":    record["registration_date"]           = parse_registration_date(value)
            elif field == "last_active_raw":          record["last_active"]                 = value
            elif field == "active_projects":          record["active_projects"]             = parse_integer(value)

    portfolio_grid = soup.select_one("#portfolio-grid")
    if portfolio_grid:
        record["portfolio_count"] = len(portfolio_grid.select("div.postcard.cell-container"))
    else:
        record["portfolio_count"] = len(
            soup.select("#portfolio div.postcard.cell-container")
            or soup.select("div.postcard.cell-container")
        )

    _SKILL_SELECTORS = [
        "#user_skills-panel ul.skills li.skills__item a bdi",
        "#user_skills ul.skills li.skills__item a bdi",
        "ul.skills li.skills__item a bdi",
        "#user_skills-panel .skills__item bdi",
        ".skills__item a bdi",
    ]
    skills = []
    for sel in _SKILL_SELECTORS:
        found = [s.get_text(strip=True) for s in soup.select(sel) if s.get_text(strip=True)]
        if len(found) > len(skills): skills = found
        if len(skills) >= 30: break
    record["skills"] = skills
    record["skills_count"] = len(skills)
    return record

# ── expected values from screenshots ──────────────────────────────────────

EXPECTED = {
    "name":                       "ديفيد ل.",
    "title":                      "مهندس حاسوب",
    "completion_rate":            100.0,
    "ontime_delivery_rate":       100.0,
    "rehire_rate":                41.33,
    "communication_success_rate": 30.05,
    "avg_response_time_raw":      "44 دقيقة",
    "avg_response_time_minutes":  44,
    "total_completed_projects":   113,
    "registration_date_year":     2023,
    "registration_date_month":    6,
    "registration_date_day":      1,
    "portfolio_count_min":        4,
    "skills_count_min":           1,
}

# ── fetch ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://mostaql.com/",
}

def fetch(url):
    s = requests.Session()
    s.get("https://mostaql.com", headers=HEADERS, timeout=15)
    r = s.get(url, headers=HEADERS, timeout=20)
    print(f"GET {url}  →  {r.status_code}  ({len(r.text):,} chars)")
    if r.status_code != 200:
        print(f"Non-200! body: {r.text[:200]}"); sys.exit(1)
    return r.text

# ── helpers ────────────────────────────────────────────────────────────────

P, F = [0], [0]
def check(label, got, expected=None, ok_fn=None):
    ok = ok_fn(got) if ok_fn else (got == expected)
    if ok: P[0] += 1; print(f"  [✓]  {label}: {got!r}")
    else:
        F[0] += 1
        exp = f"expected {expected!r}" if ok_fn is None else "condition failed"
        print(f"  [✗]  {label}: got {got!r}  ({exp})")
    return ok

SEP = "─" * 65

# ── main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("MOSTAQL PARSER DIAGNOSTIC")
    print("=" * 65)

    html_profile   = fetch("https://mostaql.com/u/DavidLabib")
    html_portfolio = fetch("https://mostaql.com/u/DavidLabib/portfolio")

    with open("profile_raw.html",   "w", encoding="utf-8") as f: f.write(html_profile)
    with open("portfolio_raw.html", "w", encoding="utf-8") as f: f.write(html_portfolio)
    print("saved → profile_raw.html / portfolio_raw.html\n")

    soup_p  = BeautifulSoup(html_profile,   "lxml")
    soup_pf = BeautifulSoup(html_portfolio, "lxml")

    # ── 1. run the actual scraper function ────────────────────────────────
    print(SEP)
    print("1. _parse_profile_page OUTPUT (scraper function, profile page)")
    rec = _parse_profile_page(html_profile, "https://mostaql.com/u/DavidLabib")
    for k, v in rec.items():
        print(f"  {k:30s}: {(str(v[:3])+'…') if isinstance(v,list) else v!r}")

    # ── 2. pass/fail ──────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("2. PASS / FAIL")
    check("name",                       rec["name"],                       EXPECTED["name"])
    check("title",                      rec["title"],                      EXPECTED["title"])
    check("completion_rate",            rec["completion_rate"],            EXPECTED["completion_rate"])
    check("ontime_delivery_rate",       rec["ontime_delivery_rate"],       EXPECTED["ontime_delivery_rate"])
    check("rehire_rate",                rec["rehire_rate"],                EXPECTED["rehire_rate"])
    check("communication_success_rate", rec["communication_success_rate"], EXPECTED["communication_success_rate"])
    check("avg_response_time_raw",      rec["avg_response_time_raw"],      EXPECTED["avg_response_time_raw"])
    check("avg_response_time_minutes",  rec["avg_response_time_minutes"],  EXPECTED["avg_response_time_minutes"])
    check("total_completed_projects",   rec["total_completed_projects"],   EXPECTED["total_completed_projects"])
    dt = rec["registration_date"]
    check("registration_date year",  dt.year  if dt else None, EXPECTED["registration_date_year"])
    check("registration_date month", dt.month if dt else None, EXPECTED["registration_date_month"])
    check("registration_date day",   dt.day   if dt else None, EXPECTED["registration_date_day"])
    check("portfolio_count >= 4", rec["portfolio_count"], ok_fn=lambda v: v >= 4)
    check("skills_count >= 1",    rec["skills_count"],    ok_fn=lambda v: v >= 1)

    # ── 3. selector diagnostics ───────────────────────────────────────────
    print(f"\n{SEP}")
    print("3. SELECTOR DIAGNOSTICS\n")

    # name
    print("[name selectors]")
    for sel in ["h1.usercard__username bdi", "h1 bdi", ".usercard__username bdi"]:
        tags = soup_p.select(sel)
        print(
    f"  {sel!r:40s} → "
    f"{repr(tags[0].get_text(strip=True)) if tags else 'NOT FOUND'}"
)

    # title
    print("\n[title / briefcase]")
    bf = soup_p.select_one("ul.user__meta li .fa-briefcase")
    print(f"  .fa-briefcase: {'found' if bf else 'NOT FOUND'}")
    print(f"  ul.user__meta li texts: {[li.get_text(strip=True) for li in soup_p.select('ul.user__meta li')][:5]}")

    # stats
    print("\n[#user-stats rows — raw labels & values]")
    panel = soup_p.select_one("#user-stats")
    if not panel:
        print("  !! #user-stats NOT FOUND")
    else:
        for row in panel.select("table.table-meta tbody tr"):
            cols = row.find_all("td")
            if len(cols) < 2: continue
            ls = cols[0].find("span")
            label = (ls or cols[0]).get_text(strip=True)
            tt = cols[1].find("time")
            value = (cols[1].get_text(separator=" ", strip=True) if tt else cols[1].get_text(strip=True))
            field = STAT_MAP.get(label, "?")
            print(f"  {label!r:28s}  {value!r:28s}  → {field!r}")

    # portfolio
    print("\n[portfolio selectors — profile page]")
    for sel in [
        "#portfolio-grid div.postcard.cell-container",
        "#portfolio div.postcard.cell-container",
        "div.postcard.cell-container",
        "div.col-md-3.cell-container",
        "a.portfolio__thumb-href",
        "div.postcard__thumb",
    ]:
        n = len(soup_p.select(sel))
        print(f"  {'✓' if n else '✗'}  {sel!r:50s}: {n}")

    print("\n[portfolio selectors — portfolio tab page]")
    for sel in [
        "#portfolio-grid div.postcard.cell-container",
        "#portfolio div.postcard.cell-container",
        "div.postcard.cell-container",
        "div.col-md-3.cell-container",
        "a.portfolio__thumb-href",
        "div.postcard__thumb",
    ]:
        n = len(soup_pf.select(sel))
        print(f"  {'✓' if n else '✗'}  {sel!r:50s}: {n}")

    # show actual classes on first portfolio card
    print("\n[first portfolio card classes — portfolio tab page]")
    first = soup_pf.select_one("a.portfolio__thumb-href")
    if first:
        for ancestor in [first.parent, first.parent.parent if first.parent else None,
                         first.parent.parent.parent if first.parent and first.parent.parent else None]:
            if ancestor and ancestor.name == "div":
                print(f"  div classes: {ancestor.get('class')}")

    # skills
    print("\n[skills selectors — profile page]")
    for sel in [
        "#user_skills-panel ul.skills li.skills__item a bdi",
        "#user_skills ul.skills li.skills__item a bdi",
        "ul.skills li.skills__item a bdi",
        ".skills__item a bdi",
        ".tag bdi",
        "li.skills__item bdi",
    ]:
        found = [s.get_text(strip=True) for s in soup_p.select(sel) if s.get_text(strip=True)]
        print(f"  {'✓' if found else '✗'}  {sel!r:55s}: {len(found)}  {found[:2]}")

    # ── summary ───────────────────────────────────────────────────────────
    print(f"\n{'=' * 65}")
    print(f"SUMMARY: {P[0]} passed  /  {F[0]} failed  /  {P[0]+F[0]} total")
    print("=" * 65)

if __name__ == "__main__":
    main()
