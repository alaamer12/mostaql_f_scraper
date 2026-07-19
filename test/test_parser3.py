"""
test_parser3.py — verify the two fixes from scraper.py
Reads already-saved profile_raw.html + portfolio_raw.html (no network).
Run: python test_parser3.py
"""

import re
import sys
from datetime import datetime
from bs4 import BeautifulSoup

# ── copy of every helper from scraper.py (non-async, no imports of aiohttp etc) ──

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
    "يناير": 1, "جانفي": 1, "فبراير": 2, "فيفري": 2,
    "مارس": 3,  "أبريل": 4, "ابريل": 4,  "مايو": 5,
    "ماي": 5,   "يونيو": 6, "يونيه": 6,  "يوليو": 7,
    "يوليه": 7, "أغسطس": 8, "اغسطس": 8,  "أغسطص": 8,
    "سبتمبر": 9,"أكتوبر": 10,"اكتوبر": 10,"نوفمبر": 11,
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
            total += multiplier if token in ("ساعتين","يومين","أسبوعين","دقيقتين") else 1 * multiplier
            i += 1; continue
        if token in ARABIC_DIGIT_WORDS:
            quantity = ARABIC_DIGIT_WORDS[token]
            if i+1 < len(tokens) and tokens[i+1] in ARABIC_WORD_NUMS:
                total += quantity * ARABIC_WORD_NUMS[tokens[i+1]]; i += 2
            else: i += 1
            continue
        digits = re.sub(r"[^\d٠-٩]", "", token)
        if digits:
            quantity = int(digits.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩","0123456789")))
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
    norm = re.sub(r"^[^\d]+", "", _normalize_arabic(raw)).strip()
    m = re.search(r"(\d{1,2})\s+([^\d\s]+)\s+(\d{4})", norm)
    if m:
        day_s, month_s, year_s = m.group(1), m.group(2), m.group(3)
        month_num = next((n for ar,n in ARABIC_MONTHS.items()
                          if _normalize_arabic(ar)==_normalize_arabic(month_s)), None)
        if month_num:
            try:    return datetime(int(year_s), month_num, int(day_s))
            except: pass
    m2 = re.search(r"\b(20\d{2})\b", raw)
    if m2:
        try:    return datetime(int(m2.group(1)), 1, 1)
        except: pass
    return None


# ── FIXED _parse_profile_page (with both bug fixes applied) ───────────────

def _parse_profile_page(html: str, url: str, portfolio_html: str | None = None) -> dict:
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

    # name
    name_tag = (soup.select_one("h1.usercard__username bdi")
                or soup.select_one("h1 bdi")
                or soup.select_one(".usercard__username bdi"))
    if name_tag:
        record["name"] = name_tag.get_text(strip=True)

    # ── FIX 1: title ─────────────────────────────────────────────────────
    title_li = (
        soup.select_one("li.profile-title")
        or soup.select_one("ul.list-meta li .fa-briefcase")
    )
    if title_li:
        a_tag = title_li.select_one("a")
        if a_tag:
            record["title"] = a_tag.get_text(strip=True)
        else:
            record["title"] = title_li.get_text(strip=True)
    if not record["title"]:
        briefcase = soup.select_one(".fa-briefcase")
        if briefcase:
            parent_li = briefcase.find_parent("li")
            if parent_li:
                a_tag = parent_li.select_one("a")
                record["title"] = (a_tag or parent_li).get_text(strip=True)

    # stats
    stats_panel = soup.select_one("#user-stats")
    if stats_panel:
        for row in stats_panel.select("table.table-meta tbody tr"):
            cols = row.find_all("td")
            if len(cols) < 2: continue
            label_span = cols[0].find("span")
            label = (label_span or cols[0]).get_text(strip=True)
            time_tag = cols[1].find("time")
            value = (cols[1].get_text(separator=" ", strip=True)
                     if time_tag else cols[1].get_text(strip=True))
            field = STAT_MAP.get(label)
            if not field:
                label_norm = _normalize_arabic(label)
                field = next((v for k,v in STAT_MAP.items()
                              if _normalize_arabic(k)==label_norm), None)
            if not field: continue
            if field == "employment_rate":              record["employment_rate"]            = parse_percentage(value)
            elif field == "received_projects":          record["received_projects"]           = parse_integer(value)
            elif field == "financial_deals":            record["financial_deals"]             = parse_dollar(value)
            elif field == "completion_rate":            record["completion_rate"]             = parse_percentage(value)
            elif field == "ontime_delivery_rate":       record["ontime_delivery_rate"]        = parse_percentage(value)
            elif field == "rehire_rate":                record["rehire_rate"]                 = parse_percentage(value)
            elif field == "communication_success_rate": record["communication_success_rate"]  = parse_percentage(value)
            elif field == "total_completed_projects":   record["total_completed_projects"]    = parse_integer(value)
            elif field == "avg_response_time_raw":
                record["avg_response_time_raw"]     = value
                record["avg_response_time_minutes"] = parse_response_time(value)
            elif field == "registration_date_raw":      record["registration_date"]           = parse_registration_date(value)
            elif field == "last_active_raw":            record["last_active"]                 = value
            elif field == "active_projects":            record["active_projects"]             = parse_integer(value)

    # ── FIX 2: portfolio_count — read from portfolio_html, not main page ──
    if portfolio_html:
        psoup = BeautifulSoup(portfolio_html, "lxml")
        portfolio_grid = psoup.select_one("#portfolio-grid")
        if portfolio_grid:
            record["portfolio_count"] = len(
                portfolio_grid.select("div.postcard.cell-container")
            )
        else:
            record["portfolio_count"] = len(
                psoup.select("div.postcard.cell-container")
            )

    # skills
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

# ── helpers ────────────────────────────────────────────────────────────────

P, F = [0], [0]
SEP = "─" * 65

def check(label, got, expected=None, ok_fn=None):
    ok = ok_fn(got) if ok_fn else (got == expected)
    if ok:
        P[0] += 1; print(f"  [✓]  {label}: {got!r}")
    else:
        F[0] += 1
        exp = f"expected {expected!r}" if ok_fn is None else "condition failed"
        print(f"  [✗]  {label}: got {got!r}  ({exp})")
    return ok

# ── main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("test_parser3.py — verifying fixes (reads saved HTML files)")
    print("=" * 65)

    try:
        with open("profile_raw.html",   encoding="utf-8") as f: html_profile   = f.read()
        with open("portfolio_raw.html", encoding="utf-8") as f: html_portfolio = f.read()
    except FileNotFoundError as e:
        print(f"\nERROR: {e}")
        print("Run test_parser.py first to save the HTML files, then re-run this.")
        sys.exit(1)

    print(f"\nLoaded profile_raw.html   ({len(html_profile):,} chars)")
    print(f"Loaded portfolio_raw.html ({len(html_portfolio):,} chars)")

    # ── run fixed parser ──────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("Running fixed _parse_profile_page ...")
    rec = _parse_profile_page(html_profile, "https://mostaql.com/u/DavidLabib", html_portfolio)

    print("\nFull output:")
    for k, v in rec.items():
        if k == "skills":
            print(f"  {'skills':30s}: {len(v)} items  {v[:5]}")
        else:
            print(f"  {k:30s}: {v!r}")

    # ── pass/fail ─────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("PASS / FAIL vs expected values")
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
    check("portfolio_count >= 4",    rec["portfolio_count"], ok_fn=lambda v: v >= 4)
    check("skills_count >= 1",       rec["skills_count"],    ok_fn=lambda v: v >= 1)

    # ── targeted fix confirmations ────────────────────────────────────────
    print(f"\n{SEP}")
    print("FIX CONFIRMATIONS\n")

    print("[Fix 1] title selector — li.profile-title")
    soup_p = BeautifulSoup(html_profile, "lxml")
    li = soup_p.select_one("li.profile-title")
    print(f"  li.profile-title found : {li is not None}")
    if li:
        a = li.select_one("a")
        print(f"  <a> inside li found    : {a is not None}")
        a_text = a.get_text(strip=True) if a else "N/A"
        print(f"  <a> text               : {a_text!r}")

    print("\n[Fix 2] portfolio_count — from portfolio_raw.html")
    soup_pf = BeautifulSoup(html_portfolio, "lxml")
    grid = soup_pf.select_one("#portfolio-grid")
    items = grid.select("div.postcard.cell-container") if grid else []
    print(f"  #portfolio-grid found  : {grid is not None}")
    print(f"  items in grid          : {len(items)}")
    print(f"  (main profile page grid items: "
          f"{len((BeautifulSoup(html_profile,'lxml').select_one('#portfolio-grid') or BeautifulSoup('','lxml')).select('div.postcard.cell-container'))})"
          f"  ← was always 0, still 0, expected")

    # ── summary ───────────────────────────────────────────────────────────
    total = P[0] + F[0]
    print(f"\n{'=' * 65}")
    print(f"SUMMARY: {P[0]}/{total} passed,  {F[0]} failed")
    if F[0] == 0:
        print("All checks passed ✓")
    print("=" * 65)

if __name__ == "__main__":
    main()
