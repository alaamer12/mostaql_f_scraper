"""
test_diagnose_profile.py
========================
Diagnostic script for profiles that return all-null fields.
Fetches a real Mostaql profile, then walks every selector used by
scraper.py and prints exactly what was (or wasn't) found, with HTML
context for failed lookups.

Usage:
    python test_diagnose_profile.py
    python test_diagnose_profile.py https://mostaql.com/u/some_other_user

The script prints a clearly structured log so you can spot which
selectors are silently failing for profiles with a different HTML layout.
"""

import re
import sys
import textwrap
import requests
from bs4 import BeautifulSoup, Tag

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_URL = "https://mostaql.com/u/developer2helpu"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://mostaql.com/",
}

STAT_MAP = {
    "معدل التوظيف":       "employment_rate",
    "المشاريع المستلمة":  "received_projects",
    "تعاملاتي معه":       "financial_deals",
    "إكمال المشاريع":     "completion_rate",
    "التسليم بالموعد":    "ontime_delivery_rate",
    "إعادة التوظيف":      "rehire_rate",
    "نجاح التواصلات":     "communication_success_rate",
    "المشاريع المكتملة":  "total_completed_projects",
    "متوسط سرعة الرد":    "avg_response_time_raw",
    "تاريخ التسجيل":      "registration_date_raw",
    "آخر تواجد":          "last_active_raw",
    "مشاريع يعمل عليها":  "active_projects",
    # ── extra labels sometimes seen in the wild ────────────────────────────
    "التقييمات":          "ratings_count",
    "التقييم":            "ratings_count",
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

SEP_MAJOR = "═" * 72
SEP_MINOR = "─" * 72
SEP_THIN  = "· " * 36

def h1(title: str) -> None:
    print(f"\n{SEP_MAJOR}")
    print(f"  {title}")
    print(SEP_MAJOR)

def h2(title: str) -> None:
    print(f"\n{SEP_MINOR}")
    print(f"  {title}")
    print(SEP_MINOR)

def ok(msg: str) -> None:
    print(f"  ✓  {msg}")

def fail(msg: str) -> None:
    print(f"  ✗  {msg}")

def info(msg: str) -> None:
    print(f"     {msg}")

def warn(msg: str) -> None:
    print(f"  ⚠  {msg}")

def show_html(tag: Tag | None, label: str = "HTML snippet", max_lines: int = 12) -> None:
    """Pretty-print a BeautifulSoup tag for debugging."""
    if tag is None:
        info(f"{label}: <None>")
        return
    raw = tag.prettify()
    lines = raw.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"  … ({len(lines) - max_lines} more lines)"]
    info(f"{label}:")
    for line in lines:
        info("    " + line)

def _normalize_arabic(text: str) -> str:
    text = re.sub(r"[إأآا]", "ا", text)
    text = text.replace("\u0640", "")
    return re.sub(r"\s+", " ", text).strip()

# ─────────────────────────────────────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch(url: str) -> str | None:
    print(f"\n  Fetching: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        print(f"  HTTP {r.status_code}  |  Content-Length: {len(r.text):,} chars  |  Encoding: {r.encoding}")
        if r.status_code != 200:
            fail(f"Non-200 status: {r.status_code}")
            return None
        return r.text
    except Exception as e:
        fail(f"Request failed: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSTIC SECTIONS
# ─────────────────────────────────────────────────────────────────────────────

def diag_page_structure(soup: BeautifulSoup) -> None:
    h2("PAGE STRUCTURE — top-level landmarks")

    landmarks = [
        ("body",                    "body"),
        (".usercard",               "usercard wrapper"),
        (".usercard__username",     "username heading"),
        ("h1",                      "h1 tag"),
        ("h1 bdi",                  "h1 > bdi (name target)"),
        (".usercard__username bdi", "usercard username bdi"),
        ("ul.list-meta",            "list-meta ul"),
        ("li.profile-title",        "profile-title li"),
        ("#user-stats",             "user-stats panel"),
        ("#user_skills-panel",      "skills panel"),
        ("#user_skills",            "skills body"),
        (".profile-sidebar",        "profile sidebar"),
        ("#portfolio-grid",         "portfolio grid (main page)"),
    ]

    for sel, label in landmarks:
        el = soup.select_one(sel)
        if el:
            preview = el.get_text(" ", strip=True)[:80].replace("\n", " ")
            ok(f"{label:35s}  →  {repr(preview)}")
        else:
            fail(f"{label:35s}  →  NOT FOUND  (selector: {repr(sel)})")


def diag_name(soup: BeautifulSoup) -> None:
    h2("FIELD: name")

    selectors = [
        "h1.usercard__username bdi",
        "h1 bdi",
        ".usercard__username bdi",
        "h1",
        ".usercard__username",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            ok(f"[{sel}]  →  {repr(el.get_text(strip=True))}")
        else:
            fail(f"[{sel}]  →  not found")

    # Dump the h1 structure regardless
    h1_tag = soup.find("h1")
    if h1_tag:
        show_html(h1_tag, "h1 full HTML")
    else:
        fail("No <h1> at all on this page")

    # Also check for any bdi tags on the page
    all_bdi = soup.find_all("bdi")
    info(f"Total <bdi> tags on page: {len(all_bdi)}")
    for i, bdi in enumerate(all_bdi[:5]):
        info(f"  bdi[{i}]: {repr(bdi.get_text(strip=True))[:80]}")


def diag_title(soup: BeautifulSoup) -> None:
    h2("FIELD: title (job/profession)")

    selectors = [
        "li.profile-title",
        "ul.list-meta li .fa-briefcase",
        ".fa-briefcase",
        "ul.list-meta",
        ".list-meta",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            ok(f"[{sel}]  →  {repr(el.get_text(' ', strip=True)[:80])}")
            show_html(el, f"  HTML for [{sel}]", max_lines=8)
        else:
            fail(f"[{sel}]  →  not found")

    # Check for .fa-briefcase specifically
    briefcase = soup.select_one(".fa-briefcase")
    if briefcase:
        parent = briefcase.find_parent("li")
        if parent:
            ok(f"briefcase parent <li>: {repr(parent.get_text(' ', strip=True)[:80])}")
            show_html(parent, "briefcase parent li HTML", max_lines=10)
        else:
            warn("Found .fa-briefcase but it has no parent <li>")
            show_html(briefcase.parent, "briefcase's parent element", max_lines=8)


def diag_stats_panel(soup: BeautifulSoup) -> None:
    h2("FIELD: stats panel — #user-stats")

    stats_panel = soup.select_one("#user-stats")
    if not stats_panel:
        fail("#user-stats panel NOT FOUND on this page")

        # Try alternative IDs/classes that might hold the same data
        alternatives = [
            "#stats",
            ".user-stats",
            ".stats-panel",
            "[id*='stats']",
            "[class*='stats']",
        ]
        info("Trying alternative selectors for stats panel:")
        for sel in alternatives:
            el = soup.select_one(sel)
            if el:
                ok(f"  [{sel}]  →  {repr(el.get_text(' ', strip=True)[:60])}")
                show_html(el, f"HTML for [{sel}]", max_lines=15)
            else:
                fail(f"  [{sel}]  →  not found")
        return

    ok(f"#user-stats found — {len(str(stats_panel)):,} chars")

    # Walk ALL tables within #user-stats
    tables = stats_panel.find_all("table")
    info(f"Tables inside #user-stats: {len(tables)}")

    for t_idx, table in enumerate(tables):
        print(f"\n  TABLE {t_idx + 1}:")
        rows = table.select("tbody tr")
        info(f"  tbody rows found: {len(rows)}")

        if not rows:
            warn("  No <tbody><tr> rows — checking if rows are direct children of table")
            rows = table.find_all("tr")
            info(f"  All <tr> rows: {len(rows)}")

        for r_idx, row in enumerate(rows):
            cols = row.find_all("td")
            if not cols:
                warn(f"    Row {r_idx}: no <td> cells")
                show_html(row, f"    Row {r_idx} HTML", max_lines=5)
                continue

            label_cell = cols[0]
            label_span = label_cell.find("span")
            raw_label  = (label_span or label_cell).get_text(strip=True)
            label_norm = _normalize_arabic(raw_label)

            value_cell = cols[1] if len(cols) > 1 else None
            if value_cell:
                time_tag = value_cell.find("time")
                raw_value = (
                    value_cell.get_text(separator=" ", strip=True)
                    if time_tag
                    else value_cell.get_text(strip=True)
                )
            else:
                raw_value = "<no value cell>"

            # Map label → field
            field = STAT_MAP.get(raw_label)
            if not field:
                for k, v in STAT_MAP.items():
                    if _normalize_arabic(k) == label_norm:
                        field = v
                        break

            if field:
                ok(f"    Row {r_idx}: label={repr(raw_label)!s:35s}  "
                   f"value={repr(raw_value)!s:25s}  → field={field}")
            else:
                warn(f"    Row {r_idx}: label={repr(raw_label)!s:35s}  "
                     f"value={repr(raw_value)!s:25s}  → NOT IN STAT_MAP")


def diag_stats_outside_panel(soup: BeautifulSoup) -> None:
    """
    For profiles where #user-stats does NOT exist, hunt for stats rows
    anywhere on the page by looking for all table.table-meta elements.
    """
    h2("STATS: searching ALL table.table-meta on page (outside #user-stats too)")

    all_tables = soup.select("table.table-meta")
    info(f"table.table-meta elements found anywhere on page: {len(all_tables)}")

    for t_idx, table in enumerate(all_tables):
        print(f"\n  table.table-meta [{t_idx}]:")
        rows = table.find_all("tr")
        info(f"  Total <tr>: {len(rows)}")
        for r_idx, row in enumerate(rows[:20]):
            cols = row.find_all("td")
            if not cols:
                continue
            label_text = cols[0].get_text(strip=True)
            value_text = cols[1].get_text(strip=True) if len(cols) > 1 else "<none>"
            info(f"    [{r_idx}] label={repr(label_text)!s:35s}  value={repr(value_text)}")


def diag_stats_dl_format(soup: BeautifulSoup) -> None:
    """
    Some Mostaql profiles render stats as <dl>/<dt>/<dd> rather than tables.
    Check for that alternative layout.
    """
    h2("STATS: checking for <dl> / <dt> / <dd> layout (alternative structure)")

    dls = soup.find_all("dl")
    info(f"<dl> elements on page: {len(dls)}")
    for dl_idx, dl in enumerate(dls[:3]):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        info(f"  dl[{dl_idx}] — {len(dts)} <dt>, {len(dds)} <dd>")
        for dt, dd in zip(dts[:10], dds[:10]):
            info(f"    dt={repr(dt.get_text(strip=True))!s:35s}  "
                 f"dd={repr(dd.get_text(strip=True))}")


def diag_stats_list_format(soup: BeautifulSoup) -> None:
    """
    Check if stats are in an <ul>/<li> list format.
    """
    h2("STATS: checking for <ul>/<li> stats list format")

    # Find any <li> that contains Arabic stat-label keywords
    keywords = ["إكمال", "التسليم", "إعادة", "نجاح", "مكتملة", "سرعة"]
    matched = []
    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        if any(kw in text for kw in keywords):
            matched.append(li)

    info(f"<li> elements containing stat keywords: {len(matched)}")
    for i, li in enumerate(matched[:10]):
        info(f"  li[{i}]: {repr(li.get_text(' ', strip=True)[:100])}")
        show_html(li, f"  li[{i}] HTML", max_lines=8)


def diag_sidebar_structure(soup: BeautifulSoup) -> None:
    """
    Dump everything inside the sidebar / aside to find where stats live
    when the profile has a different layout.
    """
    h2("SIDEBAR: dumping aside / sidebar element structure")

    sidebar_selectors = [
        "aside",
        ".sidebar",
        ".profile-sidebar",
        "[class*='sidebar']",
        "[class*='aside']",
    ]
    found_sidebar = None
    for sel in sidebar_selectors:
        el = soup.select_one(sel)
        if el:
            ok(f"[{sel}] found — {len(str(el)):,} chars")
            found_sidebar = el
            break
        else:
            fail(f"[{sel}] not found")

    if not found_sidebar:
        warn("No sidebar/aside found — printing main content area instead")
        main = soup.select_one("main") or soup.select_one(".main") or soup.body
        found_sidebar = main

    if found_sidebar:
        # Print child element tag names + classes to get an overview
        info("Direct children of sidebar (tag + classes):")
        for child in found_sidebar.children:
            if isinstance(child, Tag):
                cls = " ".join(child.get("class", []))
                id_ = child.get("id", "")
                text_preview = child.get_text(" ", strip=True)[:60].replace("\n", " ")
                info(f"  <{child.name} class='{cls}' id='{id_}'>  →  {repr(text_preview)}")


def diag_all_ids_and_classes(soup: BeautifulSoup) -> None:
    """
    Print all unique IDs and notable class names on the page —
    helps spot new wrapper elements for different profile layouts.
    """
    h2("PAGE IDs: all element id= attributes on this page")

    ids = set()
    for tag in soup.find_all(id=True):
        ids.add(tag["id"])

    sorted_ids = sorted(ids)
    info(f"Total unique IDs: {len(sorted_ids)}")
    for id_ in sorted_ids:
        el = soup.find(id=id_)
        tag_name = el.name if el else "?"
        info(f"  #{id_:40s}  <{tag_name}>")

    h2("PAGE CLASSES: classes containing 'stats', 'user', 'profile', 'card'")
    interesting_classes = set()
    for tag in soup.find_all(class_=True):
        for cls in tag.get("class", []):
            if any(kw in cls.lower() for kw in ["stats", "user", "profile", "card", "meta", "sidebar"]):
                interesting_classes.add((cls, tag.name))

    for cls, tag_name in sorted(interesting_classes):
        info(f"  .{cls:40s}  on <{tag_name}>")


def diag_skills(soup: BeautifulSoup) -> None:
    h2("FIELD: skills")

    selectors = [
        "#user_skills-panel ul.skills li.skills__item a bdi",
        "#user_skills ul.skills li.skills__item a bdi",
        "ul.skills li.skills__item a bdi",
        "#user_skills-panel .skills__item bdi",
        ".skills__item a bdi",
        ".skills__item",
        "ul.skills",
        ".tag bdi",
        "[class*='skill']",
    ]
    for sel in selectors:
        els = soup.select(sel)
        if els:
            texts = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
            ok(f"[{sel}]  →  {len(els)} elements  →  {texts[:5]}")
        else:
            fail(f"[{sel}]  →  not found")

    # Check if the skills panel itself exists but is empty
    panel = soup.select_one("#user_skills-panel")
    if panel:
        info(f"#user_skills-panel exists — {len(str(panel)):,} chars")
        show_html(panel, "#user_skills-panel HTML", max_lines=20)
    else:
        fail("#user_skills-panel does not exist")


def diag_portfolio(soup: BeautifulSoup) -> None:
    h2("FIELD: portfolio (main profile page)")

    selectors = [
        "#portfolio-grid",
        "#portfolio-grid div.postcard.cell-container",
        "div.postcard.cell-container",
        "[id*='portfolio']",
        "[class*='portfolio']",
    ]
    for sel in selectors:
        els = soup.select(sel)
        if els:
            ok(f"[{sel}]  →  {len(els)} elements")
        else:
            fail(f"[{sel}]  →  not found")


def diag_ratings(soup: BeautifulSoup) -> None:
    h2("FIELD: ratings (التقييمات)")

    selectors = [
        ".ratings__score",
        "[class*='rating']",
        ".stars",
        "[class*='star']",
        "#user-stats",  # ratings row inside stats
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            ok(f"[{sel}]  →  {repr(el.get_text(' ', strip=True)[:80])}")
        else:
            fail(f"[{sel}]  →  not found")

    # Look for the Arabic word التقييمات anywhere
    hits = []
    for tag in soup.find_all(string=re.compile("التقييمات")):
        hits.append(tag.strip()[:60])
    info(f"Text nodes containing 'التقييمات': {len(hits)}")
    for h in hits[:5]:
        info(f"  {repr(h)}")


def diag_raw_html_snippet(soup: BeautifulSoup, url: str) -> None:
    """
    Save the raw HTML to a file so you can inspect it in a browser.
    """
    h2("RAW HTML DUMP")
    html_text = str(soup)
    filename = url.rstrip("/").split("/")[-1] + "_raw.html"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_text)
        ok(f"Raw HTML saved to: {filename}  ({len(html_text):,} chars)")
        info("Open this file in a browser or text editor for full inspection.")
    except Exception as e:
        fail(f"Could not save raw HTML: {e}")


def diag_stats_panel_detailed_dump(soup: BeautifulSoup) -> None:
    """
    Dump the full text and HTML of every element that contains Arabic stat
    keywords — useful when the wrapping structure differs from what scraper.py
    expects.
    """
    h2("KEYWORD SEARCH: finding elements containing stat labels in ANY structure")

    # All labels from STAT_MAP plus some extras
    labels_to_find = list(STAT_MAP.keys()) + [
        "التقييمات", "مشروع", "مشاريع", "التسليم", "الموعد"
    ]

    for label in labels_to_find:
        # Find any text node containing this label
        matches = soup.find_all(string=re.compile(re.escape(label)))
        if matches:
            for match_text in matches[:2]:  # show up to 2 matches per label
                parent = match_text.parent
                grandparent = parent.parent if parent else None
                gp_html = grandparent.get_text(" ", strip=True)[:80] if grandparent else ""
                ok(f"'{label}'  found in <{parent.name if parent else '?'}>  "
                   f"parent text: {repr(gp_html)}")
        else:
            fail(f"'{label}'  NOT found anywhere on page")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_diagnostics(url: str) -> None:
    h1(f"MOSTAQL PROFILE PARSER DIAGNOSTICS")
    print(f"  URL: {url}")
    print(f"  Python requests + BeautifulSoup (lxml parser)")

    # ── Fetch main profile page ───────────────────────────────────────────
    h1("PHASE 1: FETCH MAIN PROFILE PAGE")
    html = fetch(url)
    if not html:
        print("\n  FATAL: Could not fetch page. Aborting diagnostics.")
        return

    soup = BeautifulSoup(html, "lxml")

    # ── Run all diagnostic checks ─────────────────────────────────────────
    h1("PHASE 2: PAGE STRUCTURE & FIELD DIAGNOSTICS")

    diag_page_structure(soup)
    diag_name(soup)
    diag_title(soup)
    diag_stats_panel(soup)
    diag_stats_outside_panel(soup)
    diag_stats_dl_format(soup)
    diag_stats_list_format(soup)
    diag_sidebar_structure(soup)
    diag_skills(soup)
    diag_portfolio(soup)
    diag_ratings(soup)

    h1("PHASE 3: DEEP LABEL SEARCH (structure-agnostic)")
    diag_stats_panel_detailed_dump(soup)

    h1("PHASE 4: ALL IDs AND CLASSES ON PAGE")
    diag_all_ids_and_classes(soup)

    # ── Fetch portfolio tab ───────────────────────────────────────────────
    h1("PHASE 5: FETCH PORTFOLIO TAB")
    portfolio_url = url.rstrip("/") + "/portfolio"
    p_html = fetch(portfolio_url)
    if p_html:
        psoup = BeautifulSoup(p_html, "lxml")
        diag_portfolio(psoup)
    else:
        warn("Portfolio tab fetch failed — skipping portfolio diagnostics")

    # ── Save raw HTML ─────────────────────────────────────────────────────
    h1("PHASE 6: SAVE RAW HTML FOR MANUAL INSPECTION")
    diag_raw_html_snippet(soup, url)

    h1("DIAGNOSTICS COMPLETE")
    print("  Review the output above to identify which selectors are failing.")
    print("  Check the saved *_raw.html file for the full page source.")
    print()


if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    run_diagnostics(target_url)
