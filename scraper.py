"""
=============================================================================
  MOSTAQL FREELANCERS SCRAPER — Async / High-Performance Edition
  Target  : https://mostaql.com/freelancers
  Output  : mostaql_freelancers_analytics.json  +  .csv

  Speed-up strategy
  -----------------
  • aiohttp  — single async event loop; no GIL contention, no thread overhead
  • asyncio.Semaphore — caps simultaneous outbound connections (politeness)
  • aiolimiter — token-bucket rate limiter shared across all workers; allows
    high concurrency while pacing requests to avoid 429 rate-limit errors
  • tenacity — exponential backoff + jitter retries on 429/403/5xx/network errors
  • asyncio.Queue — producer/consumer pipeline: paginator feeds URLs,
                    N worker coroutines drain them concurrently
  • asyncio.to_thread — offloads BeautifulSoup/regex parsing (CPU-bound)
                        so it never blocks the event loop
  • tqdm.asyncio — thread-safe progress bars in async context
  • All shared state is either immutable or guarded by asyncio primitives
    (no threading.Lock needed; asyncio is single-threaded cooperative)

  Extraction engine (Tiered Field Extractor)
  ------------------------------------------
  Every field is extracted through three escalating tiers, tried in order:

    Tier 0 — Structural selectors
      An ordered list of CSS selectors tried in sequence.  First match wins.
      Fast, zero ambiguity.  Breaks only when the site changes CSS class names.

    Tier 1 — Semantic heuristics
      Field-specific rules derived from what the data looks like and how
      Mostaql consistently marks it up (e.g. "name is always in the first h1
      that contains a <bdi>", "title is always adjacent to a .fa-briefcase
      icon", "skills are always a <ul> where every <li> has a <bdi>").
      Survives CSS class renames as long as semantic structure is kept.

    Tier 2 — Label search (structure-agnostic)
      Walk the entire DOM looking for a text node that matches a known Arabic
      label from STAT_MAP, then grab its sibling/parent value.  Slowest but
      survives complete layout redesigns.  Only runs for stats fields (those
      with known Arabic labels).

  Page confidence guard (pre-extraction)
  ----------------------------------------
  Before any extraction runs, the HTML is scored against a set of signals
  (size, presence of a <h1>, stats table, skills list …).  Pages scoring
  below MIN_CONFIDENCE are almost certainly login walls, bot-block pages, or
  redirects.  They are logged as WARNING and returned with all fields null
  plus parse_confidence="blocked" so they can be filtered or retried.
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
from dataclasses import dataclass, field
from typing import Callable
from bs4 import BeautifulSoup, Tag
from tqdm.asyncio import tqdm as atqdm
from tqdm import tqdm

from http_client import AdaptiveRateLimiter

# ---------------------------------------------------------------------------
# GLOBAL CONFIGURATION
# ---------------------------------------------------------------------------
CONFIG = {
    "BASE_URL"              : "https://mostaql.com/freelancers",

    # Set to -1  → scrape ALL pages
    # Set to N   → scrape up to N pages
    "MAX_PAGES"             : -1,

    # How many profile pages to fetch in parallel (actual throughput is capped
    # by RATE_LIMIT_BURST / RATE_LIMIT_PERIOD below).
    "PROFILE_CONCURRENCY"   : 10,

    # How many directory pages to fetch in parallel
    "DIR_CONCURRENCY"       : 3,

    # Token-bucket rate limiter (aiolimiter): max RATE_LIMIT_BURST requests
    # per RATE_LIMIT_PERIOD seconds, shared across ALL workers.
    # Example: 6 requests / 2 s ≈ 3 req/s sustained, with burst up to 6.
    "RATE_LIMIT_BURST"      : 6,
    "RATE_LIMIT_PERIOD"     : 2.0,

    # Tenacity retry policy (exponential backoff + jitter)
    "MAX_RETRIES"           : 6,
    "RETRY_WAIT_MIN"        : 2,
    "RETRY_WAIT_MAX"        : 90,

    "TIMEOUT"               : 20,
    "OUTPUT_JSON"           : "mostaql_freelancers_analytics.json",
    "OUTPUT_CSV"            : "mostaql_freelancers_analytics.csv",

    # Binary search initial upper-bound probe
    "BINARY_SEARCH_INITIAL" : 100,

    # Page confidence — minimum number of signals that must be True for the
    # page to be treated as a real profile page (not a block/redirect).
    # Range: 0–5.  Recommended: 2.
    "MIN_CONFIDENCE"        : 2,

    # HTML size threshold below which we immediately suspect a block page.
    # Real profile pages are typically 60–120 KB.
    "MIN_HTML_BYTES"        : 20_000,

    "USER_AGENTS"           : [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ],
}

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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("scraper.log", encoding="utf-8")],
)
log = logging.getLogger(__name__)


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


# ===========================================================================
# SECTION 4 — ASYNC HTTP HELPERS
# ===========================================================================

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


def _make_rate_limiter() -> AdaptiveRateLimiter:
    """Build the shared rate limiter from CONFIG."""
    return AdaptiveRateLimiter(
        max_rate=CONFIG["RATE_LIMIT_BURST"],
        time_period=CONFIG["RATE_LIMIT_PERIOD"],
        max_retries=CONFIG["MAX_RETRIES"],
        retry_wait_min=CONFIG["RETRY_WAIT_MIN"],
        retry_wait_max=CONFIG["RETRY_WAIT_MAX"],
    )


async def async_get(
    session: aiohttp.ClientSession,
    url: str,
    sem: asyncio.Semaphore,
    rate_limiter: AdaptiveRateLimiter,
    ua_index: int = 0,
) -> tuple[int, str | None]:
    """
    Async GET with token-bucket rate limiting (aiolimiter) and exponential
    backoff retries with jitter (tenacity).

    Returns (status_code, html_text).
    Returns (404, None) immediately — never retried.
    Returns (0, None) on total failure after all retries.
    """
    return await rate_limiter.get(
        session,
        url,
        _make_headers(ua_index),
        sem,
        timeout=CONFIG["TIMEOUT"],
    )


async def _page_exists(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    rate_limiter: AdaptiveRateLimiter,
    page: int,
) -> bool:
    url = CONFIG["BASE_URL"] if page == 1 else f"{CONFIG['BASE_URL']}?page={page}"
    status, html = await async_get(session, url, sem, rate_limiter, ua_index=page)
    if status == 404 or html is None:
        return False
    urls = await asyncio.to_thread(_parse_directory_page, html)
    return len(urls) > 0


async def binary_search_last_page(
    session: aiohttp.ClientSession,
    dir_sem: asyncio.Semaphore,
    rate_limiter: AdaptiveRateLimiter,
) -> int:
    """
    Find the last valid directory page index using binary search.
    O(log N) probes instead of O(N) sequential requests.
    """
    initial = CONFIG.get("BINARY_SEARCH_INITIAL", 100)
    tqdm.write(f"\n[ PHASE 0 ]  Binary-searching for last directory page "
               f"(initial probe: page {initial})")
    log.info(f"Binary search starting — initial hi={initial}")

    if not await _page_exists(session, dir_sem, rate_limiter, 1):
        tqdm.write("  ✗  Page 1 returned no results — site unreachable or empty.")
        log.error("Page 1 unreachable — aborting.")
        return 0

    lo, hi = 1, initial

    while await _page_exists(session, dir_sem, rate_limiter, hi):
        tqdm.write(f"  … page {hi} exists → expanding to {hi * 2}")
        log.info(f"Binary search: page {hi} valid, doubling hi → {hi * 2}")
        lo = hi
        hi *= 2

    tqdm.write(f"  … bracket confirmed: last page in [{lo}, {hi})")
    log.info(f"Binary search: bracket [{lo}, {hi})")

    while lo + 1 < hi:
        mid = (lo + hi) // 2
        exists = await _page_exists(session, dir_sem, rate_limiter, mid)
        tqdm.write(f"  … probe page {mid} → {'✓ exists' if exists else '✗ gone'}")
        if exists:
            lo = mid
        else:
            hi = mid

    tqdm.write(f"  ✓  Last valid page = {lo}")
    log.info(f"Binary search complete — last page = {lo}")
    return lo


# ===========================================================================
# SECTION 5 — PAGE PARSERS (CPU-bound, safe for asyncio.to_thread)
# ===========================================================================

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


# ===========================================================================
# SECTION 6 — FEATURE ENGINEERING
# ===========================================================================

def compute_success_score(df: pd.DataFrame) -> pd.Series:
    """
    Weighted composite success score (0–100).

    BUG FIX: numeric columns may now be None (not 0.0) for absent/uncalculated
    fields.  fillna(0) is applied before arithmetic so the score degrades
    gracefully rather than propagating NaN or raising TypeError.
    """
    w_completion    = 0.35
    w_ontime        = 0.25
    w_volume        = 0.20
    w_rehire        = 0.12
    w_communication = 0.08

    completion    = df["completion_rate"].fillna(0)
    ontime        = df["ontime_delivery_rate"].fillna(0)
    rehire        = df["rehire_rate"].fillna(0)
    communication = df["communication_success_rate"].fillna(0)
    projects      = df["total_completed_projects"].fillna(0)

    max_projects = projects.replace(0, 1).max()
    volume_norm  = np.log1p(projects) / np.log1p(max_projects) * 100

    return (
        completion    * w_completion
        + ontime      * w_ontime
        + volume_norm * w_volume
        + rehire      * w_rehire
        + communication * w_communication
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


# ===========================================================================
# SECTION 7 — PERSISTENCE
# ===========================================================================

def save_outputs(df: pd.DataFrame) -> None:
    if df.empty:
        log.warning("Empty DataFrame — nothing saved.")
        return

    records = df.reset_index().to_dict(orient="records")
    for r in records:
        for k, v in list(r.items()):
            if isinstance(v, datetime):
                r[k] = v.isoformat()
            elif isinstance(v, (list, dict)):
                pass  # keep as-is; json.dump handles them
            else:
                try:
                    if pd.isna(v):
                        r[k] = None
                except (TypeError, ValueError):
                    pass

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


# ===========================================================================
# SECTION 8 — ASYNC PIPELINE
# ===========================================================================

async def fetch_directory_page(
    session: aiohttp.ClientSession,
    dir_sem: asyncio.Semaphore,
    rate_limiter: AdaptiveRateLimiter,
    page: int,
    url_queue: asyncio.Queue,
    profile_bar: tqdm,
    page_pbar: tqdm,
    total_counter: list,
) -> None:
    """
    Fetch one directory page and push all discovered profile URLs onto the queue.
    total_counter is a [int] list so it can be mutated across concurrent calls
    without a lock (asyncio is single-threaded cooperative).
    """
    url = CONFIG["BASE_URL"] if page == 1 else f"{CONFIG['BASE_URL']}?page={page}"
    log.info(f"Fetching directory page {page}: {url}")

    status, html = await async_get(session, url, dir_sem, rate_limiter, ua_index=page)
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
    rate_limiter: AdaptiveRateLimiter,
    url_queue: asyncio.Queue,
    profile_bar: tqdm,
    last_page: int,
    pagination_done: asyncio.Event,
) -> int:
    """
    Producer coroutine.
    Fetches all pages 1..last_page concurrently (bounded by DIR_CONCURRENCY)
    and pushes profile URLs into url_queue.
    Sets pagination_done once ALL pages have been fetched.
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
                    session, dir_sem, rate_limiter, page,
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
    rate_limiter: AdaptiveRateLimiter,
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
    Workers loop with non-blocking get().  When the queue is transiently empty
    they yield and retry — UNLESS pagination_done is set, in which case they
    do a final drain and exit cleanly.  No sentinel, no race condition.
    """
    while True:
        try:
            url = url_queue.get_nowait()
        except asyncio.QueueEmpty:
            if pagination_done.is_set():
                # Final drain — catch any URLs that arrived in the TOCTOU window
                try:
                    while True:
                        url = url_queue.get_nowait()
                        log.info(f"[Worker-{worker_id}] Final-drain: {url}")
                        status, html = await async_get(
                            session, url, profile_sem, rate_limiter, ua_index=worker_id
                        )
                        p_status, portfolio_html = await async_get(
                            session, url.rstrip("/") + "/portfolio",
                            profile_sem, rate_limiter, ua_index=worker_id
                        )
                        if p_status != 200:
                            portfolio_html = None
                        if html:
                            record = await asyncio.to_thread(
                                _parse_profile_page, html, url, portfolio_html
                            )
                        else:
                            log.warning(f"[Worker-{worker_id}] Skipping (no HTML): {url}")
                            record = {"profile_url": url, "name": None,
                                      "parse_confidence": "no_html"}
                        results.append(record)
                        _update_bar(profile_bar, worker_id, record)
                        url_queue.task_done()
                except asyncio.QueueEmpty:
                    pass
                break
            else:
                await asyncio.sleep(0.05)
                continue

        log.info(f"[Worker-{worker_id}] Fetching profile: {url}")

        status, html = await async_get(session, url, profile_sem, rate_limiter, ua_index=worker_id)

        portfolio_html = None
        portfolio_url  = url.rstrip("/") + "/portfolio"
        p_status, portfolio_html = await async_get(
            session, portfolio_url, profile_sem, rate_limiter, ua_index=worker_id
        )
        if p_status != 200:
            log.warning(f"[Worker-{worker_id}] Portfolio tab non-200 ({p_status}): {portfolio_url}")
            portfolio_html = None

        if html:
            record = await asyncio.to_thread(_parse_profile_page, html, url, portfolio_html)
        else:
            log.warning(f"[Worker-{worker_id}] Skipping (no HTML): {url}")
            record = {"profile_url": url, "name": None, "parse_confidence": "no_html"}

        results.append(record)
        _update_bar(profile_bar, worker_id, record)
        url_queue.task_done()


def _update_bar(bar: tqdm, worker_id: int, record: dict) -> None:
    """Update the progress bar postfix with the last parsed profile summary."""
    name = record.get("name") or record.get("profile_url", "?").split("/")[-1]
    pct  = record.get("completion_rate") or 0
    conf = record.get("parse_confidence", "ok")
    flag = "" if conf == "ok" else f" [{conf}]"
    bar.set_postfix_str(f"W{worker_id}: {name} | {pct:.0f}%{flag}", refresh=True)
    bar.update(1)


# ===========================================================================
# SECTION 9 — ENTRY POINTS
# ===========================================================================

async def run_async(max_pages: int | None = None) -> pd.DataFrame:
    """
    Full async pipeline:
      Phase 0 — binary-search for last directory page
      Phase 1 — paginate directory (producer)
      Phase 2 — scrape profiles concurrently (consumers)
      Phase 3 — build DataFrame + persist
    """
    if max_pages is None:
        max_pages = CONFIG["MAX_PAGES"]

    tqdm.write("=" * 62)
    tqdm.write("  MOSTAQL FREELANCERS SCRAPER — Async Edition")
    tqdm.write(f"  max_pages={max_pages if max_pages != -1 else '∞ (auto)'}  "
               f"concurrency={CONFIG['PROFILE_CONCURRENCY']}  "
               f"rate={CONFIG['RATE_LIMIT_BURST']}/{CONFIG['RATE_LIMIT_PERIOD']}s")
    tqdm.write("=" * 62)
    log.info(f"ASYNC SCRAPER STARTED  max_pages={max_pages}")

    dir_sem       = asyncio.Semaphore(CONFIG["DIR_CONCURRENCY"])
    profile_sem   = asyncio.Semaphore(CONFIG["PROFILE_CONCURRENCY"])
    rate_limiter  = _make_rate_limiter()
    url_queue: asyncio.Queue = asyncio.Queue(maxsize=0)
    pagination_done: asyncio.Event = asyncio.Event()
    results: list[dict] = []

    connector = aiohttp.TCPConnector(
        limit=CONFIG["PROFILE_CONCURRENCY"] + CONFIG["DIR_CONCURRENCY"]
    )
    async with aiohttp.ClientSession(connector=connector) as session:

        # Prime cookies
        try:
            async with session.get(
                "https://mostaql.com",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                await r.read()
                log.info(f"Cookie priming: HTTP {r.status}, "
                         f"cookies={list(session.cookie_jar)[:3]}")
        except Exception as e:
            log.warning(f"Cookie priming failed: {e}")

        tqdm.write("✓ Async session initialised\n")

        # Phase 0 — binary search
        last_page = await binary_search_last_page(session, dir_sem, rate_limiter)
        if last_page == 0:
            tqdm.write("  ✗  Could not determine page count. Aborting.")
            return pd.DataFrame()

        if max_pages != -1:
            last_page = min(last_page, max_pages)
            tqdm.write(f"  (capped at max_pages={max_pages} → scraping {last_page} pages)")

        tqdm.write(f"  → Will scrape pages 1 – {last_page}\n")

        profile_bar = tqdm(
            total=None,
            desc="  Profiles scraped",
            unit="profile",
            colour="yellow",
            ncols=90,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}  [{elapsed}<{remaining}, {rate_fmt}]  {postfix}",
        )

        producer = asyncio.create_task(
            paginate_directory(
                session, dir_sem, rate_limiter, url_queue, profile_bar, last_page, pagination_done
            )
        )
        workers = [
            asyncio.create_task(
                profile_worker(
                    wid, session, profile_sem, rate_limiter, url_queue,
                    results, profile_bar, pagination_done
                )
            )
            for wid in range(1, CONFIG["PROFILE_CONCURRENCY"] + 1)
        ]

        await asyncio.gather(producer, *workers)
        profile_bar.close()
        tqdm.write(f"\n  HTTP stats: {rate_limiter.summary()}")

    # Deduplicate (last-write-wins, order preserved)
    seen: set[str] = set()
    deduped = []
    for r in results:
        u = r.get("profile_url", "")
        if u not in seen:
            seen.add(u)
            deduped.append(r)
    results = deduped

    # Report blocked pages
    blocked = [r for r in results if r.get("parse_confidence") != "ok"]
    if blocked:
        tqdm.write(f"\n  ⚠  {len(blocked)} profiles had low/no confidence "
                   f"(blocked/redirect) — check scraper.log for details.")

    tqdm.write(f"\n  ✓ {len(results)} profiles scraped  "
               f"({len(results) - len(blocked)} ok, {len(blocked)} blocked)")

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


def run_scraper(max_pages: int | None = None) -> pd.DataFrame:
    """Synchronous entry point; wraps the async pipeline."""
    return asyncio.run(run_async(max_pages))


if __name__ == "__main__":
    import sys

    cli_pages = int(sys.argv[1]) if len(sys.argv) > 1 else None
    df = run_scraper(max_pages=cli_pages)

    if not df.empty:
        print("\n── Top 10 by Success Score ──")
        cols = ["name", "title", "completion_rate", "ontime_delivery_rate",
                "total_completed_projects", "success_score"]
        available = [c for c in cols if c in df.columns]
        print(df[available].head(10).to_string())