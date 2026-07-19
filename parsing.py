"""HTML parsing for Mostaql freelancer profile and directory pages."""

import re
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable

from bs4 import BeautifulSoup, Tag

from config import CONFIG

log = logging.getLogger("mostaql.parsing")

# ---------------------------------------------------------------------------
# ARABIC DATA TABLES
# ---------------------------------------------------------------------------

# Arabic label → Python field name mapping
# Used by Tier 0 (stats table parsing) and Tier 2 (label search).
STAT_MAP: dict[str, str] = {
    "معدل التوظيف"      : "employment_rate",
    "المشاريع المستلمة" : "received_projects",
    "تعاملاتي معه"      : "financial_deals",
    "إكمال المشاريع"    : "completion_rate",
    "التسليم بالموعد"   : "ontime_delivery_rate",
    "إعادة التوظيف"     : "rehire_rate",
    "نجاح التواصلات"    : "communication_success_rate",
    "نجاح التواصل"      : "communication_success_rate",
    "المشاريع المكتملة" : "total_completed_projects",
    "متوسط سرعة الرد"   : "avg_response_time_raw",
    "تاريخ التسجيل"     : "registration_date_raw",
    "آخر تواجد"         : "last_active_raw",
    "مشاريع يعمل عليها" : "active_projects",
}

# Pre-normalised version of STAT_MAP keys (built once at import time).
# Used for diacritic-insensitive matching.
_STAT_MAP_NORM: dict[str, str] = {}   # populated after _normalize_arabic is defined

ARABIC_MONTHS: dict[str, int] = {
    "يناير": 1,  "جانفي": 1,
    "فبراير": 2, "فيفري": 2,
    "مارس"  : 3,
    "أبريل" : 4, "ابريل": 4,
    "مايو"  : 5, "ماي"  : 5,
    "يونيو" : 6, "يونيه": 6,
    "يوليو" : 7, "يوليه": 7,
    "أغسطس" : 8, "اغسطس": 8, "أغسطص": 8,
    "سبتمبر": 9,
    "أكتوبر": 10, "اكتوبر": 10,
    "نوفمبر": 11,
    "ديسمبر": 12,
}

ARABIC_WORD_NUMS: dict[str, int] = {
    "دقيقة"  : 1,     "دقيقتين": 2,    "دقائق"  : 1,
    "ساعة"   : 60,    "ساعتين" : 120,  "ساعات"  : 60,
    "يوم"    : 1440,  "يومين"  : 2880, "أيام"   : 1440,
    "أسبوع"  : 10080, "أسبوعين": 20160,
}

ARABIC_DIGIT_WORDS: dict[str, int] = {
    "صفر": 0, "واحد": 1, "اثنين": 2, "ثلاثة": 3, "أربعة": 4,
    "خمسة": 5, "ستة": 6, "سبعة": 7, "ثمانية": 8, "تسعة": 9,
    "عشرة": 10, "عشرين": 20, "ثلاثين": 30, "أربعين": 40,
    "خمسين": 50,
}

# ---------------------------------------------------------------------------
# LOGGING  (file-only so tqdm bars are never broken by interleaved log lines)
# ---------------------------------------------------------------------------
log = logging.getLogger("mostaql.bruteforce")


# ===========================================================================
# SECTION 1 — PURE PARSERS
#   All functions here are pure (no I/O, no shared state).
#   Safe to call from asyncio.to_thread.
# ===========================================================================

def _normalize_arabic(text: str) -> str:
    """Normalise common Arabic letter variants so lookups are accent-insensitive."""
    text = re.sub(r"[إأآا]", "ا", text)       # alef variants → bare alef
    text = text.replace("\u0640", "")           # remove tatweel
    text = re.sub(r"\s+", " ", text).strip()    # collapse whitespace
    return text


def _resolve_stat_field(label: str) -> str | None:
    """
    Map an Arabic stat label to its Python field name.
    Tries exact match first, then diacritic-normalised match.
    Returns None if no match found.
    """
    # Exact match (fast path)
    if label in STAT_MAP:
        return STAT_MAP[label]
    # Normalised match (handles minor diacritic / whitespace differences)
    label_norm = _normalize_arabic(label)
    if label_norm in _STAT_MAP_NORM:
        return _STAT_MAP_NORM[label_norm]
    return _STAT_LABEL_ALIASES.get(label_norm)


def parse_percentage(raw: str) -> float | None:
    """
    Parse a percentage string.
    Returns float if digits are present, None if the value is non-numeric
    (e.g. "لم يحسب بعد" — not yet calculated).

    BUG FIX: the old version returned 0.0 for non-numeric strings, making
    "not yet calculated" indistinguishable from "0%".
    """
    if not raw:
        return None
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits:
        return None          # ← was: return 0.0  (silent wrong value)
    try:
        return float(digits)
    except ValueError:
        return None


def parse_integer(raw: str) -> int | None:
    """Parse an integer string.  Returns None if no digits found."""
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def parse_dollar(raw: str) -> float | None:
    """Parse a dollar amount string.  Returns None if no digits found."""
    if not raw:
        return None
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def parse_response_time(raw: str) -> int:
    """
    Convert an Arabic response-time string into total minutes.
    Examples:
      "ساعتين و 11 دقيقة"  → 131
      "أقل من دقيقة"        → 1
      "3 أيام"              → 4320
    Returns 0 if the string cannot be parsed.
    """
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
            # Dual forms already encode ×2 in the table
            total += multiplier
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
    norm = re.sub(r"^[^\d]+", "", norm).strip()  # strip leading non-digit noise

    m = re.search(r"(\d{1,2})\s+([^\d\s]+)\s+(\d{4})", norm)
    if m:
        day_s, month_s, year_s = m.group(1), m.group(2), m.group(3)
        month_s_norm = _normalize_arabic(month_s)
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


# Build the normalised STAT_MAP after _normalize_arabic is defined
_STAT_MAP_NORM = {_normalize_arabic(k): v for k, v in STAT_MAP.items()}

# Extra label spellings not in STAT_MAP (normalised key → field name)
_STAT_LABEL_ALIASES: dict[str, str] = {
    "مشاريع يعمل عليها حاليا": "active_projects",
    "المشاريع الجارية":        "active_projects",
}


# ===========================================================================
# SECTION 2 — TIERED FIELD EXTRACTOR
# ===========================================================================

@dataclass
class FieldExtractor:
    """
    Encapsulates the three extraction tiers for one profile field.

    Attributes
    ----------
    field           : str
        Name of the key in the output record dict.
    selectors       : list[str]
        Tier 0 — CSS selectors tried in order; first non-empty match wins.
    semantic_fn     : Callable[[BeautifulSoup], str | None] | None
        Tier 1 — function that receives the full soup and returns a raw string
        value, or None if the heuristic found nothing.
    label_keywords  : list[str]
        Tier 2 — Arabic label strings to search for anywhere on the page.
        If the label is found, its sibling/parent text is returned as value.
        Only meaningful for stats fields (where label+value pairs exist in
        tables).  Leave empty for name/title/skills.
    post_process    : Callable[[str], any] | None
        Applied to the raw string returned by whichever tier succeeded.
        Converts to the final typed value (float, int, datetime, list …).
        If None, the raw string is stored as-is.
    default         : any
        Value stored in the record if all tiers fail.
    """
    field          : str
    selectors      : list[str]                      = field(default_factory=list)
    semantic_fn    : Callable | None                = None
    label_keywords : list[str]                      = field(default_factory=list)
    post_process   : Callable | None                = None
    default        : object                         = None

    def extract(self, soup: BeautifulSoup) -> object:
        """
        Run Tier 0 → Tier 1 → Tier 2 in order.
        Returns the post-processed value of the first tier that yields a
        non-empty string, or self.default if all tiers fail.
        """
        raw = self._tier0(soup) or self._tier1(soup) or self._tier2(soup)
        if raw is None:
            return self.default
        return self.post_process(raw) if self.post_process else raw

    # ── internal tier runners ────────────────────────────────────────────

    def _tier0(self, soup: BeautifulSoup) -> str | None:
        """Try CSS selectors in order; return text of first match."""
        for sel in self.selectors:
            try:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(strip=True)
                    if text:
                        return text
            except Exception:
                continue
        return None

    def _tier1(self, soup: BeautifulSoup) -> str | None:
        """Run semantic heuristic function."""
        if self.semantic_fn is None:
            return None
        try:
            return self.semantic_fn(soup) or None
        except Exception:
            return None

    def _tier2(self, soup: BeautifulSoup) -> str | None:
        """
        Label search: walk the DOM for any element whose text exactly (or
        after normalisation) matches one of label_keywords; return the text
        of its nearest sibling or parent td/dd.
        """
        if not self.label_keywords:
            return None
        for keyword in self.label_keywords:
            kw_norm = _normalize_arabic(keyword)
            for el in soup.find_all(string=True):
                el_text = _normalize_arabic(el.strip())
                if el_text != kw_norm:
                    continue
                # Found the label — look for the value next to it
                parent = el.parent
                if parent is None:
                    continue
                # Case A: label and value are sibling <td>s in a <tr>
                row = parent.find_parent("tr")
                if row:
                    tds = row.find_all("td")
                    if len(tds) >= 2:
                        # label is in tds[0], value in tds[1]
                        val = tds[1].get_text(separator=" ", strip=True)
                        if val:
                            return val
                # Case B: label and value are sibling <dt>/<dd>
                dd = parent.find_next_sibling("dd")
                if dd:
                    val = dd.get_text(strip=True)
                    if val:
                        return val
                # Case C: the value is the next text node or next sibling element
                next_sib = parent.find_next_sibling()
                if next_sib:
                    val = next_sib.get_text(strip=True)
                    if val:
                        return val
        return None


# ---------------------------------------------------------------------------
# SEMANTIC HEURISTIC FUNCTIONS (Tier 1)
# ---------------------------------------------------------------------------

def _sem_name(soup: BeautifulSoup) -> str | None:
    """
    Name heuristic:
      Rule 1 — First <h1> or <h2> that contains a <bdi> child.
               Names on Mostaql are always bidi-isolated.
      Rule 2 — First <h1> whose text is short (< 60 chars) and does not look
               like a section heading (no colon, no Arabic ownership suffix).
    """
    # Rule 1: any heading with a <bdi>
    for tag in ("h1", "h2"):
        for el in soup.find_all(tag):
            bdi = el.find("bdi")
            if bdi:
                text = bdi.get_text(strip=True)
                if text:
                    return text

    # Rule 2: first short <h1>
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(strip=True)
        if text and len(text) < 60 and ":" not in text:
            return text
    return None


def _sem_title(soup: BeautifulSoup) -> str | None:
    """
    Title heuristic:
      Rule 1 — Any element with class fa-briefcase; take text of its parent <li>.
               Mostaql consistently marks the job title with a briefcase icon.
      Rule 2 — Any <li> or <span> that is a child of an element with class
               'profile-type' or 'list-meta'; take its anchor text.
    """
    # Rule 1: briefcase icon anchor
    briefcase = soup.find(class_=re.compile(r"\bfa-briefcase\b"))
    if briefcase:
        parent = briefcase.find_parent("li")
        if parent:
            a = parent.find("a")
            text = (a or parent).get_text(strip=True)
            if text:
                return text

    # Rule 2: list-meta > li with an anchor (skip the "type" li)
    for ul in soup.find_all(class_=re.compile(r"\blist-meta\b")):
        for li in ul.find_all("li"):
            if "profile-type" in li.get("class", []):
                continue
            a = li.find("a")
            if a:
                text = a.get_text(strip=True)
                if text:
                    return text
    return None


def _sem_skills(soup: BeautifulSoup) -> str | None:
    """
    Skills heuristic — returns a pipe-joined string of skill names.
    Rule: find any <ul> where every <li> child (that isn't empty) contains
    both an <a> and a <bdi>.  That is the structural fingerprint of a tag list.
    Returns None so the extractor falls through to the default empty list when
    no such ul exists — actual list assembly is done in _extract_skills().
    """
    return None   # skills use a custom extraction path (see _extract_skills)


def _sem_stats_table(soup: BeautifulSoup) -> Tag | None:
    """
    Find the stats panel by content fingerprint rather than ID.
    Looks for ANY <table> where ≥ 3 rows have a recognisable STAT_MAP label.
    Returns the table Tag, or None.
    Used by _extract_stats() as the Tier 1 panel fallback.
    """
    for table in soup.find_all("table"):
        hits = 0
        for row in table.find_all("tr"):
            tds = row.find_all("td")
            if not tds:
                continue
            label = _normalize_arabic(tds[0].get_text(strip=True))
            if label in _STAT_MAP_NORM:
                hits += 1
        if hits >= 3:
            return table
    return None


def _sem_portfolio_count(soup: BeautifulSoup) -> str | None:
    """
    Portfolio count heuristic:
    Count any <div> that has BOTH 'postcard' and 'cell-container' anywhere
    in its class list (not requiring them to be the only classes).
    Returns count as string, or None if 0.
    """
    count = 0
    for div in soup.find_all("div"):
        classes = div.get("class", [])
        if "postcard" in classes and "cell-container" in classes:
            count += 1
    return str(count) if count > 0 else None


# ---------------------------------------------------------------------------
# SKILLS — custom extraction (returns list, not string)
# ---------------------------------------------------------------------------

_SKILL_SELECTORS = [
    "#user_skills-panel ul.skills li.skills__item a bdi",
    "#user_skills ul.skills li.skills__item a bdi",
    "ul.skills li.skills__item a bdi",
    "#user_skills-panel .skills__item bdi",
    ".skills__item a bdi",
    ".tag bdi",
]


def _extract_skills(soup: BeautifulSoup) -> list[str]:
    """
    Tier 0 + Tier 1 skill extraction.

    Tier 0: try each selector in _SKILL_SELECTORS; keep the one that returns
            the most results (max coverage across partial HTML responses).
    Tier 1: if Tier 0 yields nothing, find any <ul> where every non-empty <li>
            contains a <bdi> — that is the structural fingerprint of a tag list.
    """
    # Tier 0 — selector sweep
    best: list[str] = []
    for sel in _SKILL_SELECTORS:
        found = [el.get_text(strip=True) for el in soup.select(sel) if el.get_text(strip=True)]
        if len(found) > len(best):
            best = found
        if len(best) >= 30:
            break

    if best:
        return best

    # Tier 1 — structural fingerprint
    for ul in soup.find_all("ul"):
        lis = [li for li in ul.find_all("li") if li.get_text(strip=True)]
        if not lis:
            continue
        # Every non-empty li must contain a <bdi>
        if all(li.find("bdi") for li in lis):
            skills = [li.get_text(strip=True) for li in lis]
            if len(skills) >= 2:
                return skills

    return []


# ---------------------------------------------------------------------------
# STATS — custom extraction (multi-row, multi-field from tables)
# ---------------------------------------------------------------------------

def _extract_stats(soup: BeautifulSoup, record: dict) -> None:
    """
    Extract all stats fields into `record` in-place.

    Tier 0: look for #user-stats panel (known ID from confirmed HTML).
    Tier 1: if #user-stats not found, use _sem_stats_table() to find the
            stats table by content fingerprint.
    Tier 2: per-field label search is delegated to individual FieldExtractors
            in EXTRACTORS (those with label_keywords set).

    For each row:
      - label: first <span> text if present (tooltipped label), else td text
      - value: full td text with separator=" " to preserve space around <time>
    """
    # Tier 0 — known ID
    panel = soup.select_one("#user-stats")

    # Tier 1 — content fingerprint fallback
    if panel is None:
        panel = _sem_stats_table(soup)

    if panel is None:
        log.debug("_extract_stats: no stats panel found by ID or fingerprint")
        return

    for row in panel.select("table tr"):
        cols = row.find_all("td")
        if len(cols) < 2:
            continue

        label_cell = cols[0]
        label_span = label_cell.find("span")
        label = (label_span or label_cell).get_text(strip=True)

        value_cell = cols[1]
        # Preserve "منذ X دقائق" by using separator=" " (space around <time>)
        value = value_cell.get_text(separator=" ", strip=True)

        field_name = _resolve_stat_field(label)
        if field_name is None:
            continue

        _apply_stat(record, field_name, value)


def _apply_stat(record: dict, field_name: str, value: str) -> None:
    """
    Type-convert a raw stat value and store it in record.
    Centralised so the same logic runs whether the value came from Tier 0,
    Tier 1, or Tier 2 label search.
    """
    if field_name == "employment_rate":
        record["employment_rate"] = parse_percentage(value)

    elif field_name == "received_projects":
        record["received_projects"] = parse_integer(value)

    elif field_name == "financial_deals":
        record["financial_deals"] = parse_dollar(value)

    elif field_name == "completion_rate":
        record["completion_rate"] = parse_percentage(value)

    elif field_name == "ontime_delivery_rate":
        record["ontime_delivery_rate"] = parse_percentage(value)

    elif field_name == "rehire_rate":
        record["rehire_rate"] = parse_percentage(value)

    elif field_name == "communication_success_rate":
        record["communication_success_rate"] = parse_percentage(value)

    elif field_name == "total_completed_projects":
        record["total_completed_projects"] = parse_integer(value)

    elif field_name == "avg_response_time_raw":
        record["avg_response_time_raw"] = value
        record["avg_response_time_minutes"] = parse_response_time(value)

    elif field_name == "registration_date_raw":
        record["registration_date"] = parse_registration_date(value)

    elif field_name == "last_active_raw":
        record["last_active"] = value

    elif field_name == "active_projects":
        record["active_projects"] = parse_integer(value)


# ---------------------------------------------------------------------------
# PAGE CONFIDENCE GUARD
# ---------------------------------------------------------------------------

def _page_confidence(html: str, soup: BeautifulSoup) -> tuple[int, list[str]]:
    """
    Score the page against a fixed set of signals.

    Returns
    -------
    (score, signals_found)
      score         : int — number of signals that fired (0–5)
      signals_found : list[str] — names of signals that fired (for logging)

    Signals
    -------
    html_size       : len(html) >= MIN_HTML_BYTES
    has_h1          : at least one <h1> exists
    has_stats_table : a <table class*='table-meta'> exists
    has_skills      : a .skills__item element exists
    has_profile_name: an element with class containing 'profile-name' exists,
                      OR a <h1> contains a <bdi>
    """
    signals: list[str] = []

    if len(html) >= CONFIG["MIN_HTML_BYTES"]:
        signals.append("html_size")

    if soup.find("h1"):
        signals.append("has_h1")

    if soup.find("table", class_=re.compile(r"\btable-meta\b")):
        signals.append("has_stats_table")

    if soup.find(class_=re.compile(r"\bskills__item\b")):
        signals.append("has_skills")

    # profile-name class OR h1 > bdi (two layout variants, same semantic)
    has_pname = bool(
        soup.find(class_=re.compile(r"\bprofile-name\b"))
        or (soup.find("h1") and soup.find("h1").find("bdi"))
    )
    if has_pname:
        signals.append("has_profile_name")

    return len(signals), signals


# ===========================================================================
# SECTION 3 — MAIN PROFILE PARSER
# ===========================================================================

def _parse_profile_page(html: str, url: str, portfolio_html: str | None = None) -> dict:
    """
    Full profile-page parser.  Pure function — no I/O, no shared state.
    Safe to call from asyncio.to_thread.

    Parameters
    ----------
    html           : HTML of the main profile page
    url            : canonical profile URL
    portfolio_html : HTML of the /portfolio tab (fetched separately because
                     #portfolio-grid on the main page is always JS-empty)

    Returns
    -------
    dict with all extracted fields plus:
      parse_confidence : "ok" | "blocked"
      parse_signals    : list[str] of confidence signals that fired
    """
    record: dict = {
        "profile_url"               : url,
        "name"                      : None,
        "title"                     : None,
        "registration_date"         : None,
        "last_active"               : None,
        "active_projects"           : None,
        "portfolio_count"           : None,
        "employment_rate"           : None,
        "received_projects"         : None,
        "financial_deals"           : None,
        "completion_rate"           : None,
        "ontime_delivery_rate"      : None,
        "rehire_rate"               : None,
        "communication_success_rate": None,
        "total_completed_projects"  : None,
        "avg_response_time_raw"     : None,
        "avg_response_time_minutes" : None,
        "skills"                    : [],
        "skills_count"              : 0,
        "parse_confidence"          : "ok",
        "parse_signals"             : [],
    }

    soup = BeautifulSoup(html, "lxml")

    # ── Confidence guard ──────────────────────────────────────────────────
    score, signals = _page_confidence(html, soup)
    record["parse_signals"] = signals

    if score < CONFIG["MIN_CONFIDENCE"]:
        record["parse_confidence"] = "blocked"
        log.warning(
            f"[parser] Low confidence ({score}/5 signals: {signals}) for {url} "
            f"— HTML size={len(html):,} chars.  Likely block/redirect page."
        )
        return record

    # ── 1. Name ───────────────────────────────────────────────────────────
    # Tier 0: structural selectors (ordered by specificity + confirmed layouts)
    # Tier 1: _sem_name — first heading that contains a <bdi>
    name_selectors = [
        "h1.usercard__username bdi",   # layout A (older profiles)
        "h1.profile-name bdi",         # layout B (confirmed in diagnostics)
        "h1 bdi",                      # any h1 with bidi isolation
        ".usercard__username bdi",     # no-h1 fallback
        "h2.profile-name bdi",         # rare h2 variant
        "h2 bdi",                      # broader h2 fallback
    ]
    name_raw = None
    for sel in name_selectors:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t:
                name_raw = t
                break
    if not name_raw:
        name_raw = _sem_name(soup)
    record["name"] = name_raw

    # ── 2. Title ──────────────────────────────────────────────────────────
    # Tier 0: structural selectors
    # Tier 1: _sem_title — briefcase icon anchor
    title_selectors = [
        "li.profile-title a",          # primary confirmed structure
        "li.profile-title",            # li without anchor
        "ul.list-meta li.profile-title a",
        "ul.list-meta li .fa-briefcase",
    ]
    title_raw = None
    for sel in title_selectors:
        el = soup.select_one(sel)
        if el:
            # If we matched the icon element itself, step up to parent li
            if "fa-briefcase" in " ".join(el.get("class", [])):
                el = el.find_parent("li") or el
            t = el.get_text(strip=True)
            # strip the icon glyph prefix if any (non-alphanum leading chars)
            t = re.sub(r"^[^\w\u0600-\u06FF]+", "", t).strip()
            if t:
                title_raw = t
                break
    if not title_raw:
        title_raw = _sem_title(soup)
    record["title"] = title_raw

    # ── 3. Stats ──────────────────────────────────────────────────────────
    # Handled by _extract_stats which applies Tier 0 (#user-stats ID),
    # Tier 1 (content fingerprint), and stores each field via _apply_stat.
    _extract_stats(soup, record)

    # ── 4. Portfolio count ────────────────────────────────────────────────
    # Primary source: portfolio_html passed in from the /portfolio tab fetch.
    # Tier 0: #portfolio-grid > div.postcard.cell-container
    # Tier 1: _sem_portfolio_count — class substring matching
    portfolio_count = 0

    def _count_portfolio(s: BeautifulSoup) -> int:
        grid = s.select_one("#portfolio-grid")
        scope = grid if grid else s
        # Multi-class selector: elements with BOTH classes
        items = [
            div for div in scope.find_all("div")
            if "postcard" in div.get("class", []) and "cell-container" in div.get("class", [])
        ]
        return len(items)

    if portfolio_html:
        psoup = BeautifulSoup(portfolio_html, "lxml")
        portfolio_count = _count_portfolio(psoup)
        # Tier 1 fallback if Tier 0 found nothing
        if portfolio_count == 0:
            raw_sem = _sem_portfolio_count(psoup)
            portfolio_count = int(raw_sem) if raw_sem else 0
    else:
        # No portfolio tab HTML — try the main page as last resort
        portfolio_count = _count_portfolio(soup)
        if portfolio_count == 0:
            raw_sem = _sem_portfolio_count(soup)
            portfolio_count = int(raw_sem) if raw_sem else 0

    record["portfolio_count"] = portfolio_count

    # ── 5. Skills ─────────────────────────────────────────────────────────
    skills = _extract_skills(soup)
    record["skills"]       = skills
    record["skills_count"] = len(skills)

    return record


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


def make_failed_record(url: str, reason: str = "no_html") -> dict:
    """Minimal record for a profile whose HTTP fetch failed."""
    return {
        "profile_url": url,
        "name": None,
        "parse_confidence": reason,
    }


parse_profile_page = _parse_profile_page
parse_directory_page = _parse_directory_page
