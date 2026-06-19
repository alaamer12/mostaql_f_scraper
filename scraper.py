"""
=============================================================================
  MOSTAQL FREELANCERS SCRAPER — Async / High-Performance Edition
  Target  : https://mostaql.com/freelancers
  Output  : mostaql_freelancers_analytics.json  +  .csv

  Speed-up strategy
  -----------------
  • aiohttp  — single async event loop; no GIL contention, no thread overhead
  • asyncio.Semaphore — caps simultaneous outbound connections (politeness)
  • asyncio.Queue — producer/consumer pipeline: paginator feeds URLs,
                    N worker coroutines drain them concurrently
  • asyncio.to_thread — offloads BeautifulSoup/regex parsing (CPU-bound)
                        so it never blocks the event loop
  • tqdm.asyncio — thread-safe progress bars in async context
  • All shared state is either immutable or guarded by asyncio primitives
    (no threading.Lock needed; asyncio is single-threaded cooperative)
=============================================================================
"""

import re
import json
import asyncio
import logging
import numpy as np
import aiohttp
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm as atqdm
from tqdm import tqdm

# ---------------------------------------------------------------------------
# GLOBAL CONFIGURATION
# ---------------------------------------------------------------------------
CONFIG = {
    "BASE_URL"          : "https://mostaql.com/freelancers",

    # Set to -1  → scrape ALL pages until the site returns 404 / no results
    # Set to 500 → scrape up to 500 pages but stop early on first empty page
    "MAX_PAGES"         : -1,

    # How many profile pages to fetch in parallel.
    # Tune to taste: higher = faster but more likely to trigger rate-limits.
    "PROFILE_CONCURRENCY": 15,

    # How many directory pages to fetch in parallel.
    # Keep low — directory pages are fetched sequentially by default so we
    # detect the last page correctly; set > 1 only if you need extra speed
    # and are willing to over-fetch past the real last page.
    "DIR_CONCURRENCY"   : 3,

    "REQUEST_DELAY"     : 0.3,      # seconds between requests per worker
    "RETRY_DELAYS"      : [2, 5, 10],  # back-off delays between retries
    "TIMEOUT"           : 20,
    "OUTPUT_JSON"       : "mostaql_freelancers_analytics.json",
    "OUTPUT_CSV"        : "mostaql_freelancers_analytics.csv",
    # Binary search initial upper-bound probe.
    # If the site has more pages than this, the search doubles automatically.
    "BINARY_SEARCH_INITIAL": 100,
    "USER_AGENTS"       : [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ],
}

# Arabic label → Python field name mapping
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
    # ── fields missing from original STAT_MAP ──────────────────────────────
    "تاريخ التسجيل"     : "registration_date_raw",   # Bug 2 fix
    "آخر تواجد"         : "last_active_raw",          # Bug 3 fix
    "مشاريع يعمل عليها" : "active_projects",          # Bug 6 fix
}

# ── Arabic month names → month number ──────────────────────────────────────
# Used by parse_registration_date to handle "09 أغسطس 2021" style strings.
ARABIC_MONTHS = {
    "يناير": 1,  "جانفي": 1,
    "فبراير": 2, "فيفري": 2,
    "مارس": 3,
    "أبريل": 4,  "ابريل": 4,
    "مايو": 5,   "ماي": 5,
    "يونيو": 6,  "يونيه": 6,
    "يوليو": 7,  "يوليه": 7,
    "أغسطس": 8,  "اغسطس": 8, "أغسطص": 8,
    "سبتمبر": 9, "سبتمبر": 9,
    "أكتوبر": 10,"اكتوبر": 10,
    "نوفمبر": 11,"نوفمبر": 11,
    "ديسمبر": 12,"ديسمبر": 12,
}

# ---------------------------------------------------------------------------
# LOGGING  (file-only so tqdm bars are never broken by interleaved log lines)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("scraper.log", encoding="utf-8")],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NUMERIC PARSERS  (pure functions — safe to call from any coroutine/thread)
# ---------------------------------------------------------------------------

def parse_percentage(raw: str) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", raw))
    except (ValueError, TypeError):
        return 0.0


def parse_integer(raw: str) -> int:
    try:
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else 0
    except (ValueError, TypeError):
        return 0


def parse_dollar(raw: str) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", raw))
    except (ValueError, TypeError):
        return 0.0


ARABIC_WORD_NUMS = {
    "دقيقة": 1, "دقيقتين": 2, "دقائق": 1,
    "ساعة": 60, "ساعتين": 120, "ساعات": 60,
    "يوم": 1440, "يومين": 2880, "أيام": 1440,
    "أسبوع": 10080, "أسبوعين": 20160,
}
ARABIC_DIGIT_WORDS = {
    "صفر": 0, "واحد": 1, "اثنين": 2, "ثلاثة": 3, "أربعة": 4,
    "خمسة": 5, "ستة": 6, "سبعة": 7, "ثمانية": 8, "تسعة": 9,
    "عشرة": 10, "عشرين": 20, "ثلاثين": 30, "أربعين": 40,
    "خمسين": 50,
}


def parse_response_time(raw: str) -> int:
    if not raw:
        return 0
    raw = raw.strip()
    if "أقل من دقيقة" in raw or "أقل من" in raw:
        return 1
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
            i += 1
            continue
        if token in ARABIC_DIGIT_WORDS:
            quantity = ARABIC_DIGIT_WORDS[token]
            if i + 1 < len(tokens) and tokens[i + 1] in ARABIC_WORD_NUMS:
                total += quantity * ARABIC_WORD_NUMS[tokens[i + 1]]
                i += 2
            else:
                i += 1
            continue
        digits = re.sub(r"[^\d٠-٩]", "", token)
        if digits:
            arabic_indic = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
            quantity = int(digits.translate(arabic_indic))
            if i + 1 < len(tokens) and tokens[i + 1] in ARABIC_WORD_NUMS:
                total += quantity * ARABIC_WORD_NUMS[tokens[i + 1]]
                i += 2
            else:
                i += 1
            continue
        i += 1
    return total if total > 0 else 0


def _normalize_arabic(text: str) -> str:
    """Normalise common Arabic letter variants so lookups are accent-insensitive."""
    # alef variants → bare alef
    text = re.sub(r"[إأآا]", "ا", text)
    # remove tatweel
    text = text.replace("\u0640", "")
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_registration_date(raw: str) -> datetime | None:
    """
    Parse a registration-date string into a datetime.

    Handles:
      • ISO / slash / dash formats  ("2021-08-09", "09/08/2021", …)
      • English month names         ("August 9, 2021")
      • Arabic month names          ("09 أغسطس 2021")
      • Bare 4-digit year fallback  ("since 2021")
    """
    if not raw:
        return None

    raw = raw.strip()

    # ── 1. Standard numeric / English formats via dateutil ─────────────────
    from dateutil import parser as du_parser
    try:
        return du_parser.parse(raw, dayfirst=True)
    except Exception:
        pass

    # ── 2. Arabic month-name pattern: "DD MonthName YYYY" ──────────────────
    norm = _normalize_arabic(raw)
    # strip any leading non-digit noise ("تاريخ التسجيل: …")
    norm = re.sub(r"^[^\d]+", "", norm).strip()

    m = re.search(r"(\d{1,2})\s+([^\d\s]+)\s+(\d{4})", norm)
    if m:
        day_s, month_s, year_s = m.group(1), m.group(2), m.group(3)
        month_s_norm = _normalize_arabic(month_s)
        # look up in ARABIC_MONTHS (also normalised keys)
        month_num = None
        for ar_name, num in ARABIC_MONTHS.items():
            if _normalize_arabic(ar_name) == month_s_norm:
                month_num = num
                break
        if month_num:
            try:
                return datetime(int(year_s), month_num, int(day_s))
            except ValueError:
                pass

    # ── 3. Bare year fallback ───────────────────────────────────────────────
    m2 = re.search(r"\b(20\d{2})\b", raw)
    if m2:
        try:
            return datetime(int(m2.group(1)), 1, 1)
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# ASYNC HTTP HELPERS
# ---------------------------------------------------------------------------

def _make_headers(ua_index: int) -> dict:
    return {
        "User-Agent"               : CONFIG["USER_AGENTS"][ua_index % len(CONFIG["USER_AGENTS"])],
        "Accept"                   : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language"          : "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding"          : "gzip, deflate, br",
        "Connection"               : "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer"                  : "https://mostaql.com/",
    }


async def async_get(
    session: aiohttp.ClientSession,
    url: str,
    sem: asyncio.Semaphore,
    ua_index: int = 0,
) -> tuple[int, str | None]:
    """
    Async GET with semaphore, retry back-off, and UA rotation.
    Returns (status_code, html_text).
    Returns (404, None) immediately — never retried; 404 is a definitive
    server answer used intentionally by the binary search.
    Returns (0, None) on total network failure after all retries.
    """
    headers = _make_headers(ua_index)
    for attempt, delay in enumerate(CONFIG["RETRY_DELAYS"], start=1):
        async with sem:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=CONFIG["TIMEOUT"]),
                ) as resp:
                    if resp.status == 404:
                        # Definitive "does not exist" — return immediately.
                        # Binary search treats this as its upper-bound signal.
                        log.info(f"404 → {url}")
                        return 404, None
                    if resp.status == 403:
                        log.warning(f"403 attempt {attempt} → {url}")
                        await asyncio.sleep(delay * 2)
                        continue
                    resp.raise_for_status()
                    text = await resp.text()
                    await asyncio.sleep(CONFIG["REQUEST_DELAY"])
                    return resp.status, text
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning(f"Attempt {attempt} error for {url}: {e}")
                if attempt < len(CONFIG["RETRY_DELAYS"]):
                    await asyncio.sleep(delay)
    log.error(f"All retries exhausted: {url}")
    return 0, None


async def _page_exists(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    page: int,
) -> bool:
    """
    Probe whether directory page `page` exists and has freelancer rows.

    True  → page is valid
    False → beyond the end (404 OR 200 with no rows)

    Used only by binary_search_last_page().
    """
    url = CONFIG["BASE_URL"] if page == 1 else f"{CONFIG['BASE_URL']}?page={page}"
    status, html = await async_get(session, url, sem, ua_index=page)
    if status == 404 or html is None:
        return False
    urls = await asyncio.to_thread(_parse_directory_page, html)
    return len(urls) > 0


async def binary_search_last_page(
    session: aiohttp.ClientSession,
    dir_sem: asyncio.Semaphore,
) -> int:
    """
    Find the last valid directory page index using binary search.

    O(log N) probes instead of O(N) sequential requests.
    For 350 pages that's ~9 probes instead of 350.

    Algorithm
    ---------
    1. Verify page 1 exists (sanity check).
    2. Start with hi = BINARY_SEARCH_INITIAL (default 100).
       Double hi until _page_exists(hi) is False — finds the bracket.
    3. Classic binary search in [lo, hi) until lo+1 == hi.
    4. lo is the last valid page.

    404 is the intentional "too high" signal here — no retries.
    Real transient errors (timeouts, 503s) still retry inside async_get.
    """
    initial = CONFIG.get("BINARY_SEARCH_INITIAL", 100)
    tqdm.write(f"\n[ PHASE 0 ]  Binary-searching for last directory page "
               f"(initial probe: page {initial})")
    log.info(f"Binary search starting — initial hi={initial}")

    # Sanity check: page 1 must exist
    if not await _page_exists(session, dir_sem, 1):
        tqdm.write("  ✗  Page 1 returned no results — site unreachable or empty.")
        log.error("Page 1 unreachable — aborting.")
        return 0

    lo = 1
    hi = initial

    # Step 1: exponential expansion until hi is past the last page
    while await _page_exists(session, dir_sem, hi):
        tqdm.write(f"  … page {hi} exists → expanding to {hi * 2}")
        log.info(f"Binary search: page {hi} valid, doubling hi → {hi * 2}")
        lo = hi
        hi *= 2

    tqdm.write(f"  … bracket confirmed: last page is somewhere in [{lo}, {hi})")
    log.info(f"Binary search: bracket [{lo}, {hi})")

    # Step 2: binary search within the bracket
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        exists = await _page_exists(session, dir_sem, mid)
        tqdm.write(f"  … probe page {mid} → {'✓ exists' if exists else '✗ gone'}")
        log.info(f"Binary search: probe {mid} → {'valid' if exists else 'invalid'}")
        if exists:
            lo = mid
        else:
            hi = mid

    tqdm.write(f"  ✓  Last valid page = {lo}")
    log.info(f"Binary search complete — last page = {lo}")
    return lo


# ---------------------------------------------------------------------------
# PARSING  (CPU-bound — runs in thread pool via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _parse_directory_page(html: str) -> list[str]:
    """Extract profile URLs from a directory listing page."""
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for row in soup.select("tr.freelancer-row"):
        a = row.select_one("td.info-td a[href]")
        if a and a.get("href"):
            href = a["href"].strip()
            if not href.startswith("http"):
                href = "https://mostaql.com" + href
            urls.append(href)
    return urls


def _parse_profile_page(html: str, url: str, portfolio_html: str | None = None) -> dict:
    """
    Full profile-page parser.  Pure function — no I/O, no shared state.
    Safe to call from asyncio.to_thread.

    Parameters
    ----------
    html           : HTML of the main profile page (e.g. /u/DavidLabib)
    url            : canonical profile URL (stored as profile_url in the record)
    portfolio_html : HTML of the /portfolio tab page (e.g. /u/DavidLabib/portfolio).
                     Must be fetched separately by the caller because #portfolio-grid
                     on the main profile page is always empty (JS-rendered).

    Fixed bugs vs. original
    ───────────────────────
    1. title          — reads li.profile-title > a (real HTML structure);
                        old ul.user__meta selector does not exist on the page
    2. registration_date — pulled from stats table via STAT_MAP + Arabic month parser
    3. last_active    — pulled from stats table via STAT_MAP; broken [data-original-title]
                        selector removed entirely
    4. portfolio_count — fetched from portfolio_html (/portfolio tab); the main profile
                        page always has an empty #portfolio-grid (content is JS-injected)
    5. skills         — searches the full #user_skills-panel (catches both the collapsible
                        body and the panel itself), plus a page-wide fallback so truncated
                        server HTML still yields the skills that are present
    6. active_projects — new field captured via STAT_MAP "مشاريع يعمل عليها"
    7. login-gated fields (employment_rate, received_projects, financial_deals)
                       — kept in STAT_MAP; captured when present, stay 0 when absent
    """
    record: dict = {
        "profile_url"               : url,
        "name"                      : None,
        "title"                     : None,
        "registration_date"         : None,
        "last_active"               : None,
        "active_projects"           : 0,
        "portfolio_count"           : 0,
        "employment_rate"           : 0.0,
        "received_projects"         : 0,
        "financial_deals"           : 0.0,
        "completion_rate"           : 0.0,
        "ontime_delivery_rate"      : 0.0,
        "rehire_rate"               : 0.0,
        "communication_success_rate": 0.0,
        "total_completed_projects"  : 0,
        "avg_response_time_raw"     : None,
        "avg_response_time_minutes" : 0,
        "skills"                    : [],
        "skills_count"              : 0,
    }

    soup = BeautifulSoup(html, "lxml")

    # ── 1. Name ───────────────────────────────────────────────────────────
    name_tag = (
        soup.select_one("h1.usercard__username bdi")
        or soup.select_one("h1 bdi")
        or soup.select_one(".usercard__username bdi")
    )
    if name_tag:
        record["name"] = name_tag.get_text(strip=True)

    # ── 2. Title (job title next to the briefcase icon) ───────────────────
    # CONFIRMED FIX (from test_parser2.py diagnostics):
    # Real HTML structure is:
    #   <ul class="list-meta">
    #     <li class="profile-title">
    #       <i class="fa fa-briefcase"></i>
    #       <a href="...">مهندس حاسوب</a>
    #     </li>
    #   </ul>
    # The old selectors (ul.user__meta, ul.user__meta li .fa-briefcase) do not
    # exist on the page. The correct selectors are shown below.
    title_li = (
        soup.select_one("li.profile-title")                  # primary: exact class
        or soup.select_one("ul.list-meta li .fa-briefcase")  # fallback via icon
    )
    if title_li:
        # grab the <a> text inside the li (strips the icon text)
        a_tag = title_li.select_one("a")
        if a_tag:
            record["title"] = a_tag.get_text(strip=True)
        else:
            # no <a>: get li text but strip the icon character
            record["title"] = title_li.get_text(strip=True)
    if not record["title"]:
        # broadest fallback: any .fa-briefcase on the page
        briefcase = soup.select_one(".fa-briefcase")
        if briefcase:
            parent_li = briefcase.find_parent("li")
            if parent_li:
                a_tag = parent_li.select_one("a")
                record["title"] = (a_tag or parent_li).get_text(strip=True)

    # ── 3 & 4. Stats sidebar — ALL tables inside #user-stats ──────────────
    # BUG FIX (registration_date): label "تاريخ التسجيل" is in a SECOND table
    #   inside #user-stats (after the divider line). The original code searched
    #   ul.user__meta which never contains this data.
    # BUG FIX (last_active): label "آخر تواجد" is in the same second table.
    #   The broken [data-original-title] selector was matching tooltip HTML
    #   from the stats percentages, returning the site description string.
    # Both are now captured via STAT_MAP like every other stat.
    stats_panel = soup.select_one("#user-stats")
    if stats_panel:
        for row in stats_panel.select("table.table-meta tbody tr"):
            cols = row.find_all("td")
            if len(cols) < 2:
                continue

            # Use the first <span> text if present (tooltipped labels), else td text
            label_cell = cols[0]
            label_span = label_cell.find("span")
            label = (label_span or label_cell).get_text(strip=True)

            # For value cells that contain a <time> tag, prefer its text
            value_cell = cols[1]
            time_tag = value_cell.find("time")
            if time_tag:
                # "آخر تواجد" cell: "منذ <time>6 دقائق</time>"
                # get full cell text so we preserve "منذ 6 دقائق"
                value = value_cell.get_text(separator=" ", strip=True)
            else:
                value = value_cell.get_text(strip=True)

            field = STAT_MAP.get(label)
            if not field:
                # Try label normalisation in case of minor diacritic differences
                label_norm = _normalize_arabic(label)
                for k, v in STAT_MAP.items():
                    if _normalize_arabic(k) == label_norm:
                        field = v
                        break
            if not field:
                continue

            if field == "employment_rate":
                record["employment_rate"] = parse_percentage(value)
            elif field == "received_projects":
                record["received_projects"] = parse_integer(value)
            elif field == "financial_deals":
                record["financial_deals"] = parse_dollar(value)
            elif field == "completion_rate":
                record["completion_rate"] = parse_percentage(value)
            elif field == "ontime_delivery_rate":
                record["ontime_delivery_rate"] = parse_percentage(value)
            elif field == "rehire_rate":
                record["rehire_rate"] = parse_percentage(value)
            elif field == "communication_success_rate":
                record["communication_success_rate"] = parse_percentage(value)
            elif field == "total_completed_projects":
                record["total_completed_projects"] = parse_integer(value)
            elif field == "avg_response_time_raw":
                record["avg_response_time_raw"] = value
                record["avg_response_time_minutes"] = parse_response_time(value)
            elif field == "registration_date_raw":
                # value is like "09 أغسطس 2021" — parsed by our Arabic-aware function
                record["registration_date"] = parse_registration_date(value)
            elif field == "last_active_raw":
                # value is like "منذ 6 دقائق"
                record["last_active"] = value
            elif field == "active_projects":
                record["active_projects"] = parse_integer(value)

    # ── 5. Portfolio count ────────────────────────────────────────────────
    # CONFIRMED FIX (from test_parser2.py diagnostics):
    # The profile page DOES contain #portfolio-grid but it is ALWAYS EMPTY —
    # the items are injected by JavaScript after the page loads.
    # The only reliable source is the /portfolio tab URL, whose HTML is passed
    # in as portfolio_html by the worker (fetched as a second request).
    # We parse portfolio_html if provided, otherwise fall back to 0.
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

    # ── 6. Skills ─────────────────────────────────────────────────────────
    # BUG FIX: the original selector was correct in theory but some server
    # responses truncate the collapsible body.  We search the whole panel
    # (#user_skills-panel) not just the body (#user_skills), and add a
    # page-wide fallback so partial HTML still yields whatever skills are present.
    #
    # Selector path confirmed from real HTML:
    #   #user_skills-panel > .carda__body#user_skills > .carda__content
    #     > ul.skills.text-zeta.list-tags > li.skills__item > a.tag > bdi
    _SKILL_SELECTORS = [
        "#user_skills-panel ul.skills li.skills__item a bdi",   # full panel scope
        "#user_skills ul.skills li.skills__item a bdi",         # body scope (original)
        "ul.skills li.skills__item a bdi",                      # page-wide fallback
        "#user_skills-panel .skills__item bdi",                 # simplified fallback
        ".skills__item a bdi",                                  # broadest fallback
    ]
    skills: list[str] = []
    for sel in _SKILL_SELECTORS:
        found = [
            s.get_text(strip=True)
            for s in soup.select(sel)
            if s.get_text(strip=True)
        ]
        if len(found) > len(skills):
            skills = found          # keep the selector that gives the most skills
        if len(skills) >= 30:       # stop early if we already have a full set
            break

    record["skills"] = skills
    record["skills_count"] = len(skills)

    return record


# ---------------------------------------------------------------------------
# FEATURE ENGINEERING
# ---------------------------------------------------------------------------

def compute_success_score(df: pd.DataFrame) -> pd.Series:
    """Weighted composite success score (0–100)."""
    w_completion    = 0.35
    w_ontime        = 0.25
    w_volume        = 0.20
    w_rehire        = 0.12
    w_communication = 0.08

    max_projects = df["total_completed_projects"].replace(0, 1).max()
    volume_norm = (
        np.log1p(df["total_completed_projects"])
        / np.log1p(max_projects)
        * 100
    )
    return (
        df["completion_rate"]              * w_completion
        + df["ontime_delivery_rate"]       * w_ontime
        + volume_norm                      * w_volume
        + df["rehire_rate"]                * w_rehire
        + df["communication_success_rate"] * w_communication
    ).round(2)


def build_dataframe(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        log.warning("No records to build DataFrame.")
        return df
    df["success_score"] = compute_success_score(df)
    df["skills_str"] = df["skills"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else ""
    )
    df["registration_date_str"] = df["registration_date"].apply(
        lambda x: x.isoformat() if isinstance(x, datetime) else None
    )
    df = df.sort_values("success_score", ascending=False).reset_index(drop=True)
    df.index += 1
    df.index.name = "rank"
    log.info(f"DataFrame built: {len(df)} records, {len(df.columns)} columns")
    return df


# ---------------------------------------------------------------------------
# PERSISTENCE
# ---------------------------------------------------------------------------

def save_outputs(df: pd.DataFrame) -> None:
    if df.empty:
        log.warning("Empty DataFrame — nothing saved.")
        return

    records = df.reset_index().to_dict(orient="records")
    for r in records:
        for k, v in r.items():
            if isinstance(v, datetime):
                r[k] = v.isoformat()
            elif not isinstance(v, (list, dict)) and pd.isna(v):
                r[k] = None

    with open(CONFIG["OUTPUT_JSON"], "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    log.info(f"Saved JSON → {CONFIG['OUTPUT_JSON']}")

    csv_df = df.reset_index().copy()
    csv_df["skills"] = csv_df["skills"].apply(
        lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, list) else x
    )
    csv_df["registration_date"] = csv_df["registration_date"].apply(
        lambda x: x.isoformat() if isinstance(x, datetime) else x
    )
    csv_df.to_csv(CONFIG["OUTPUT_CSV"], index=False, encoding="utf-8-sig")
    log.info(f"Saved CSV  → {CONFIG['OUTPUT_CSV']}")


# ---------------------------------------------------------------------------
# ASYNC PIPELINE
# ---------------------------------------------------------------------------

async def fetch_directory_page(
    session: aiohttp.ClientSession,
    dir_sem: asyncio.Semaphore,
    page: int,
    url_queue: asyncio.Queue,
    profile_bar: tqdm,
    page_pbar: tqdm,
    total_counter: list,
) -> None:
    """
    Fetch one directory page and push all discovered profile URLs onto the queue.
    Runs concurrently for all pages in [1, last_page] — safe because the exact
    range is known in advance from binary_search_last_page().
    total_counter is a [int] list so it can be mutated across concurrent calls
    without a lock (asyncio is single-threaded cooperative).
    """
    url = CONFIG["BASE_URL"] if page == 1 else f"{CONFIG['BASE_URL']}?page={page}"
    log.info(f"Fetching directory page {page}: {url}")

    status, html = await async_get(session, url, dir_sem, ua_index=page)
    if not html:
        log.warning(f"Page {page}: no HTML (status={status}), skipping.")
        page_pbar.update(1)
        return

    urls = await asyncio.to_thread(_parse_directory_page, html)
    for u in urls:
        await url_queue.put(u)

    total_counter[0] += len(urls)
    page_pbar.set_postfix_str(f"{len(urls)} URLs on page {page}", refresh=True)
    page_pbar.update(1)

    profile_bar.total = (profile_bar.total or 0) + len(urls)
    profile_bar.refresh()
    log.info(f"Page {page}: {len(urls)} URLs | running total: {total_counter[0]}")


async def paginate_directory(
    session: aiohttp.ClientSession,
    dir_sem: asyncio.Semaphore,
    url_queue: asyncio.Queue,
    profile_bar: tqdm,
    last_page: int,
    pagination_done: asyncio.Event,
) -> int:
    """
    Producer coroutine.
    Fetches all pages 1..last_page concurrently (bounded by DIR_CONCURRENCY
    via the semaphore inside async_get) and pushes URLs into url_queue.

    Because last_page is known exactly from binary search, production is
    complete the moment all page tasks finish — pagination_done is then set
    and workers drain the remaining queue before exiting cleanly.
    No race condition: the event is only set after ALL pages are fetched.
    """
    tqdm.write(f"\n[ PHASE 1 ]  Fetching {last_page} directory pages in parallel")
    total_counter = [0]

    with tqdm(
        total=last_page,
        desc="  Pages fetched",
        unit="page",
        colour="green",
        ncols=90,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} pages  [{elapsed}<{remaining}] {postfix}",
    ) as page_pbar:
        tasks = [
            asyncio.create_task(
                fetch_directory_page(
                    session, dir_sem, page,
                    url_queue, profile_bar, page_pbar, total_counter,
                )
            )
            for page in range(1, last_page + 1)
        ]
        await asyncio.gather(*tasks)

    pagination_done.set()
    log.info(f"pagination_done set — {total_counter[0]} total URLs pushed")
    return total_counter[0]


async def profile_worker(
    worker_id: int,
    session: aiohttp.ClientSession,
    profile_sem: asyncio.Semaphore,
    url_queue: asyncio.Queue,
    results: list,
    profile_bar: tqdm,
    pagination_done: asyncio.Event,
) -> None:
    """
    Consumer coroutine.
    Drains url_queue, fetches & parses each profile, appends to results.

    Shutdown protocol
    -----------------
    OLD (buggy): workers exited on a sentinel None value in the queue.
      Race: workers are ~10× faster than the paginator, so they drained the
      queue AND consumed the sentinel while the paginator was still on page 8,
      causing early exit and ~10% URL loss.

    NEW (fixed): workers loop with a non-blocking get().  When the queue is
      transiently empty they yield and retry — UNLESS pagination_done is set,
      in which case they do a final drain and exit cleanly.  No sentinel, no
      race.
    """
    while True:
        try:
            # Non-blocking: raises QueueEmpty immediately if nothing is queued
            url = url_queue.get_nowait()
        except asyncio.QueueEmpty:
            if pagination_done.is_set():
                # Paginator is done. Do one final blocking drain attempt to
                # catch any URLs that arrived between our empty-check and the
                # event-check (the "TOCTOU" window).
                try:
                    while True:
                        url = url_queue.get_nowait()
                        log.info(f"[Worker-{worker_id}] Final-drain: {url}")
                        status, html = await async_get(
                            session, url, profile_sem, ua_index=worker_id
                        )
                        # Portfolio tab — same as main path
                        p_status, portfolio_html = await async_get(
                            session, url.rstrip("/") + "/portfolio",
                            profile_sem, ua_index=worker_id
                        )
                        if p_status != 200:
                            portfolio_html = None
                        if html:
                            record = await asyncio.to_thread(
                                _parse_profile_page, html, url, portfolio_html
                            )
                        else:
                            log.warning(f"[Worker-{worker_id}] Skipping (no HTML): {url}")
                            record = {"profile_url": url, "name": None}
                        results.append(record)
                        name = record.get("name") or url.split("/")[-1]
                        pct  = record.get("completion_rate", 0)
                        profile_bar.set_postfix_str(
                            f"W{worker_id}: {name} | {pct:.0f}%", refresh=True
                        )
                        profile_bar.update(1)
                        url_queue.task_done()
                except asyncio.QueueEmpty:
                    pass
                # Queue is empty and paginator is done — safe to exit
                break
            else:
                # Paginator is still running; queue is momentarily empty.
                # Yield control so other coroutines (including the paginator)
                # can make progress, then retry.
                await asyncio.sleep(0.05)
                continue

        log.info(f"[Worker-{worker_id}] Fetching profile: {url}")

        status, html = await async_get(session, url, profile_sem, ua_index=worker_id)

        # Portfolio items are JS-injected on the main page — fetch the tab separately
        portfolio_html = None
        portfolio_url  = url.rstrip("/") + "/portfolio"
        p_status, portfolio_html = await async_get(
            session, portfolio_url, profile_sem, ua_index=worker_id
        )
        if p_status != 200:
            log.warning(f"[Worker-{worker_id}] Portfolio tab non-200 ({p_status}): {portfolio_url}")
            portfolio_html = None

        if html:
            record = await asyncio.to_thread(_parse_profile_page, html, url, portfolio_html)
        else:
            log.warning(f"[Worker-{worker_id}] Skipping (no HTML): {url}")
            record = {"profile_url": url, "name": None}

        results.append(record)

        name = record.get("name") or url.split("/")[-1]
        pct  = record.get("completion_rate", 0)
        profile_bar.set_postfix_str(f"W{worker_id}: {name} | {pct:.0f}%", refresh=True)
        profile_bar.update(1)

        url_queue.task_done()


async def run_async(max_pages: int | None = None) -> pd.DataFrame:
    """
    Full async pipeline:
      Phase 1 — paginate directory (producer)
      Phase 2 — scrape profiles concurrently (consumers)
      Phase 3 — build DataFrame + persist
    """
    if max_pages is None:
        max_pages = CONFIG["MAX_PAGES"]

    tqdm.write("=" * 62)
    tqdm.write("  MOSTAQL FREELANCERS SCRAPER — Async Edition")
    tqdm.write(f"  max_pages={max_pages if max_pages != -1 else '∞ (auto)'}  "
               f"concurrency={CONFIG['PROFILE_CONCURRENCY']}")
    tqdm.write("=" * 62)
    log.info(f"ASYNC SCRAPER STARTED  max_pages={max_pages}")

    # Semaphores — separate pools for directory vs profile fetching
    dir_sem     = asyncio.Semaphore(CONFIG["DIR_CONCURRENCY"])
    profile_sem = asyncio.Semaphore(CONFIG["PROFILE_CONCURRENCY"])

    # Shared queue: paginator pushes URLs, workers pop them
    url_queue: asyncio.Queue = asyncio.Queue(maxsize=0)  # unbounded

    # Set by paginate_directory once ALL pages have been fetched.
    # Workers exit only after this is set AND the queue is empty.
    pagination_done: asyncio.Event = asyncio.Event()

    results: list[dict] = []

    connector = aiohttp.TCPConnector(limit=CONFIG["PROFILE_CONCURRENCY"] + CONFIG["DIR_CONCURRENCY"])
    async with aiohttp.ClientSession(connector=connector) as session:

        # Prime cookies
        try:
            async with session.get("https://mostaql.com",
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                await r.read()
        except Exception as e:
            log.warning(f"Cookie priming failed: {e}")

        tqdm.write("✓ Async session initialised\n")

        # ── Phase 0: binary-search for the last directory page ────────────
        last_page = await binary_search_last_page(session, dir_sem)
        if last_page == 0:
            tqdm.write("  ✗  Could not determine page count. Aborting.")
            return pd.DataFrame()

        # Honour MAX_PAGES cap if the user set a finite limit
        if max_pages != -1:
            last_page = min(last_page, max_pages)
            tqdm.write(f"  (capped at max_pages={max_pages} → scraping {last_page} pages)")

        tqdm.write(f"  → Will scrape pages 1 – {last_page}\n")

        # ── Shared profile progress bar (total known upfront after search) ─
        profile_bar = tqdm(
            total=None,   # updated live as directory pages complete
            desc="  Profiles scraped",
            unit="profile",
            colour="yellow",
            ncols=90,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}  [{elapsed}<{remaining}, {rate_fmt}]  {postfix}",
        )

        # ── Launch producer + consumers concurrently ──────────────────────
        producer = asyncio.create_task(
            paginate_directory(session, dir_sem, url_queue, profile_bar, last_page, pagination_done)
        )

        workers = [
            asyncio.create_task(
                profile_worker(wid, session, profile_sem, url_queue, results, profile_bar, pagination_done)
            )
            for wid in range(1, CONFIG["PROFILE_CONCURRENCY"] + 1)
        ]

        # Wait for everything to finish
        await asyncio.gather(producer, *workers)
        profile_bar.close()

    # Deduplicate results by profile_url (last-write-wins; order preserved)
    seen: set[str] = set()
    deduped = []
    for r in results:
        u = r.get("profile_url", "")
        if u not in seen:
            seen.add(u)
            deduped.append(r)
    results = deduped

    tqdm.write(f"\n  ✓ {len(results)} profiles scraped")

    # ── Phase 3 ───────────────────────────────────────────────────────────
    tqdm.write("\n[ PHASE 3 ]  Building DataFrame & saving outputs")
    df = build_dataframe(results)
    save_outputs(df)

    tqdm.write("")
    tqdm.write("=" * 62)
    tqdm.write(f"  ✓ COMPLETE — {len(df)} freelancers indexed & saved")
    tqdm.write(f"    JSON → {CONFIG['OUTPUT_JSON']}")
    tqdm.write(f"    CSV  → {CONFIG['OUTPUT_CSV']}")
    tqdm.write("=" * 62)
    log.info(f"SCRAPER COMPLETE — {len(df)} records")

    return df


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def run_scraper(max_pages: int | None = None) -> pd.DataFrame:
    """Synchronous entry point; wraps the async pipeline."""
    return asyncio.run(run_async(max_pages))


if __name__ == "__main__":
    import sys

    # Allow overriding max_pages from CLI: python scraper.py 50   or   python scraper.py -1
    cli_pages = int(sys.argv[1]) if len(sys.argv) > 1 else None
    df = run_scraper(max_pages=cli_pages)

    if not df.empty:
        print("\n── Top 10 by Success Score ──")
        cols = ["name", "title", "completion_rate", "ontime_delivery_rate",
                "total_completed_projects", "success_score"]
        available = [c for c in cols if c in df.columns]
        print(df[available].head(10).to_string())