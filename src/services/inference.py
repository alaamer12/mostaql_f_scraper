"""
inference.py
------------
Context-aware, structure-independent field extraction for Mostaql profile and
project pages. Does NOT rely on exact CSS selectors, fixed IDs, or class names.

Pipeline:
  1. Flatten the DOM into an ordered text-token stream, where each token carries
     a pointer back to its source element and DOM ancestor path.
  2. Extract VALUE CANDIDATES from the stream (numbers, currency, ranges,
     percentages, dates, duration units, placeholders).
  3. Score every (candidate, field) pair using hand-weighted additive signals
     (Arabic stem match, unit match, type match, reading-order prior, position prior),
     with distance decay in both token-space and DOM-space.
  4. Softmax-normalize competing field scores per candidate.
  5. Resolve the top winner per field across all candidates, keeping runner-ups
     as competing candidates for full diagnostic transparency.
"""

import re
import math
from typing import List, Dict, Set, Optional, Tuple, Any
from bs4 import BeautifulSoup, NavigableString, Comment, Tag

# ---------------------------------------------------------------------------
# Tunable weights
# ---------------------------------------------------------------------------

WEIGHTS = {
    "STEM_WEIGHT": 3.0,
    "UNIT_WEIGHT": 2.0,
    "TYPE_WEIGHT": 1.0,
    "POSITION_WEIGHT": 0.5,
    "MISSING_UNIT_PENALTY": -1.5,
    "BOILERPLATE_DAMPING_THRESHOLD": 6,
    "BOILERPLATE_DAMPING_FACTOR": 0.35,
}

LOCAL_WINDOW_TOKENS = 12
LOCAL_CONFIDENCE_MARGIN = 0.20

ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ARABIC_TO_ASCII = str.maketrans(ARABIC_DIGITS, "0123456789")
ARABIC_DIGIT_RE = re.compile(f"[{ARABIC_DIGITS}]")

# Arabic prefixes/suffixes stripped for crude root/stem matching
_PREFIXES = ["ال", "و", "ف", "ب", "ل", "لل", "ك", "بال", "كال", "ولل"]
_SUFFIXES = ["ها", "هم", "هن", "ه", "ة", "ات", "ين", "ون", "ي", "ا", "كم", "نا"]


def _strip_affixes(word: str) -> str:
    w = word
    changed = True
    while changed:
        changed = False
        for p in sorted(_PREFIXES, key=len, reverse=True):
            if w.startswith(p) and len(w) - len(p) >= 2:
                w = w[len(p):]
                changed = True
                break
    for s in sorted(_SUFFIXES, key=len, reverse=True):
        if w.endswith(s) and len(w) - len(s) >= 2:
            w = w[:-len(s)]
            break
    return w


def normalize_ws(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def stem(word: str) -> str:
    """Crude Arabic stem: strip common affixes, diacritics/tatweel, and punctuation."""
    w = normalize_ws(word)
    w = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", w)
    w = w.strip("،.,:؛;()[]{}»«\"'|/-")
    if not w:
        return ""
    return _strip_affixes(w)


# ---------------------------------------------------------------------------
# Value type patterns
# ---------------------------------------------------------------------------

PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
RANGE_RE = re.compile(r"\$?\s*([\d.]+)\s*-\s*\$?\s*([\d.]+)")
FLOAT_RE = re.compile(r"\b\d+\.\d+\b")
INT_RE = re.compile(r"(?<!\.)\b\d+\b(?!\.\d)")
CURRENCY_SYMS = ["$", "usd", "دولار", "ريال", "sar", "egp", "جنيه", "درهم", "aed"]
NOT_CALCULATED_MARKERS = ["لم يحسب بعد", "غير محدد", "n/a", "لا يوجد", "لم يحدد"]

DURATION_UNITS = [
    "دقيقة", "دقائق", "ساعة", "ساعات", "يوم", "يوما", "أيام", "ايام",
    "أسبوع", "اسبوع", "أسابيع", "شهر", "أشهر", "شهور", "سنة", "سنوات"
]
RELATIVE_DATE_WORDS = ["منذ", "قبل", "خلال", "حوالي", "الان", "الآن"]
ABSOLUTE_DATE_RE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b")
MONTH_NAMES = [
    "يناير", "جانفي", "فبراير", "فيفري", "مارس", "أبريل", "ابريل", "مايو", "ماي",
    "يونيو", "يونيه", "يوليو", "يوليه", "أغسطس", "اغسطس", "سبتمبر", "أكتوبر",
    "اكتوبر", "نوفمبر", "ديسمبر"
]


def classify_value_types(token_text: str) -> Set[str]:
    """Return a set of type tags for a raw token string."""
    t = token_text.strip()
    types: Set[str] = set()
    if not t:
        return types
    ascii_t = t.translate(ARABIC_TO_ASCII)
    if any(m in t.lower() for m in NOT_CALCULATED_MARKERS):
        types.add("PLACEHOLDER")
        return types
    if PERCENT_RE.search(ascii_t):
        types.add("PERCENT")
    if RANGE_RE.search(ascii_t) and "-" in ascii_t:
        types.add("RANGE")
    if ABSOLUTE_DATE_RE.search(ascii_t):
        types.add("DATE")
    if FLOAT_RE.search(ascii_t):
        types.add("FLOAT")
        types.add("NUMBER")
    elif INT_RE.search(ascii_t):
        types.add("NUMBER")
    if any(u in t for u in DURATION_UNITS):
        types.add("DURATION")
    return types


# ---------------------------------------------------------------------------
# Field profiles for freelancer & project statistics
# ---------------------------------------------------------------------------

FIELD_PROFILES: Dict[str, Dict[str, Any]] = {
    # Freelancer profile fields
    "completion_rate": {
        "core_stems": [stem("إكمال"), stem("اكمال")],
        "expected_types": {"PERCENT", "PLACEHOLDER"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": ["%"],
        "requires_unit": False,
    },
    "ontime_delivery_rate": {
        "core_stems": [stem("تسليم"), stem("موعد")],
        "expected_types": {"PERCENT", "PLACEHOLDER"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": ["%"],
        "requires_unit": False,
    },
    "rehire_rate": {
        "core_stems": [stem("إعادة"), stem("اعادة"), stem("توظيف")],
        "expected_types": {"PERCENT", "PLACEHOLDER"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": ["%"],
        "requires_unit": False,
    },
    "communication_success_rate": {
        "core_stems": [stem("نجاح"), stem("تواصل"), stem("تواصلات")],
        "expected_types": {"PERCENT", "PLACEHOLDER"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": ["%"],
        "requires_unit": False,
    },
    "employment_rate": {
        "core_stems": [stem("معدل"), stem("توظيف")],
        "expected_types": {"PERCENT", "PLACEHOLDER"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": ["%"],
        "requires_unit": False,
    },
    "total_completed_projects": {
        "core_stems": [stem("مكتملة"), stem("مكتمل"), stem("منجزة"), stem("منجز")],
        "expected_types": {"NUMBER", "PLACEHOLDER"},
        "forbidden_types": {"PERCENT", "FLOAT"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "active_projects": {
        "core_stems": [stem("يعمل"), stem("عليها")],
        "expected_types": {"NUMBER", "PLACEHOLDER"},
        "forbidden_types": {"PERCENT", "FLOAT"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "received_projects": {
        "core_stems": [stem("مستلمة"), stem("استلام")],
        "expected_types": {"NUMBER", "PLACEHOLDER"},
        "forbidden_types": {"PERCENT", "FLOAT"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "financial_deals": {
        "core_stems": [stem("تعاملاتي"), stem("صفقات")],
        "expected_types": {"NUMBER", "PLACEHOLDER"},
        "forbidden_types": {"PERCENT", "FLOAT"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "avg_response_time_raw": {
        "core_stems": [stem("متوسط"), stem("سرعة"), stem("رد"), stem("تجاوب")],
        "expected_types": {"NUMBER", "FLOAT", "DATE", "DURATION", "PLACEHOLDER"},
        "unit_hints": DURATION_UNITS,
        "requires_unit": False,
    },
    "registration_date_raw": {
        "core_stems": [stem("تاريخ"), stem("تسجيل"), stem("عضو"), stem("انضمام")],
        "expected_types": {"DATE", "NUMBER", "DURATION", "PLACEHOLDER"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": MONTH_NAMES + RELATIVE_DATE_WORDS,
        "requires_unit": False,
    },
    "last_active_raw": {
        "core_stems": [stem("آخر"), stem("اخر"), stem("تواجد"), stem("نشاط"), stem("ظهور")],
        "expected_types": {"DATE", "NUMBER", "DURATION", "PLACEHOLDER"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": RELATIVE_DATE_WORDS + DURATION_UNITS,
        "requires_unit": False,
    },
    "portfolio_count": {
        "core_stems": [stem("أعمال"), stem("اعمال"), stem("معرض"), stem("نماذج")],
        "expected_types": {"NUMBER"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "rating": {
        "core_stems": [stem("تقييم"), stem("نجوم")],
        "expected_types": {"FLOAT", "NUMBER"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "reviews_count": {
        "core_stems": [stem("تقييمات"), stem("مراجعات"), stem("أراء"), stem("اراء")],
        "expected_types": {"NUMBER"},
        "unit_hints": [],
        "requires_unit": False,
    },
    # Project detail fields
    "project_status": {
        "core_stems": [stem("حالة"), stem("مشروع")],
        "expected_types": {"PLACEHOLDER"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "budget": {
        "core_stems": [stem("ميزانية"), stem("تكلفة"), stem("سعر")],
        "expected_types": {"NUMBER", "FLOAT", "RANGE", "PLACEHOLDER"},
        "unit_hints": CURRENCY_SYMS,
        "requires_unit": True,
    },
    "duration": {
        "core_stems": [stem("مدة"), stem("تنفيذ"), stem("وقت")],
        "expected_types": {"NUMBER", "FLOAT"},
        "unit_hints": DURATION_UNITS,
        "requires_unit": True,
    },
}


# ---------------------------------------------------------------------------
# Token stream
# ---------------------------------------------------------------------------

class Token:
    __slots__ = ("text", "index", "element", "dom_path")

    def __init__(self, text: str, index: int, element: Any, dom_path: Tuple[int, ...]):
        self.text = text
        self.index = index
        self.element = element
        self.dom_path = dom_path

    def __repr__(self) -> str:
        return f"Token({self.text!r}@{self.index})"


def _dom_path(el: Any) -> Tuple[int, ...]:
    path = []
    cur = el
    while cur is not None and getattr(cur, "name", None) is not None:
        path.append(id(cur))
        cur = cur.parent
    return tuple(path)


def flatten(soup: BeautifulSoup) -> List[Token]:
    """Walk DOM in document order and return flattened Token objects."""
    tokens = []
    idx = 0
    for el in soup.find_all(True):
        # Ignore script and style tags
        if el.name in ("script", "style", "noscript"):
            continue
        own = "".join(
            c for c in el.contents
            if isinstance(c, NavigableString) and not isinstance(c, Comment)
        )
        own = normalize_ws(own)
        if not own:
            continue
        for word in own.split(" "):
            if word:
                tokens.append(Token(word, idx, el, _dom_path(el)))
                idx += 1
    return tokens


def dom_distance(path_a: Tuple[int, ...], path_b: Tuple[int, ...]) -> int:
    """Distance in DOM tree: number of hops to common ancestor."""
    set_b = set(path_b)
    hops_a = 0
    for node in path_a:
        if node in set_b:
            hops_b = path_b.index(node)
            return hops_a + hops_b
        hops_a += 1
    return hops_a + len(path_b)


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

class Candidate:
    def __init__(
        self,
        raw_text: str,
        types: Set[str],
        token_index: int,
        element: Any,
        dom_path: Tuple[int, ...],
        unit_nearby: Optional[str] = None,
    ):
        self.raw_text = raw_text
        self.types = types
        self.token_index = token_index
        self.element = element
        self.dom_path = dom_path
        self.unit_nearby = unit_nearby
        self.scores: Dict[str, float] = {}
        self.probabilities: Dict[str, float] = {}

    def __repr__(self) -> str:
        return f"Candidate({self.raw_text!r}, types={self.types})"


_MERGE_CONNECTOR_RE = re.compile(r"\A(?:[-./:]|\$)\Z")
_MERGE_DIGIT_RE = re.compile(r"\A\d+%?\Z")
_VALUE_SEED_RE = re.compile(r"[\d" + ARABIC_DIGITS + "]")


def extract_candidates(tokens: List[Token]) -> List[Candidate]:
    """Find value candidates in token stream, merging tokens when appropriate."""
    candidates: List[Candidate] = []
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]

        # Also check for placeholder phrases (e.g. "لم يحسب بعد")
        tok_lower = tok.text.lower()
        if any(m in tok_lower for m in NOT_CALCULATED_MARKERS):
            cand = Candidate(tok.text, {"PLACEHOLDER"}, tok.index, tok.element, tok.dom_path)
            candidates.append(cand)
            i += 1
            continue

        # Lookahead multi-token placeholder check
        if i + 2 < n:
            three_tok = f"{tokens[i].text} {tokens[i+1].text} {tokens[i+2].text}"
            if any(m in three_tok for m in NOT_CALCULATED_MARKERS):
                cand = Candidate(three_tok, {"PLACEHOLDER"}, tok.index, tok.element, tok.dom_path)
                candidates.append(cand)
                i += 3
                continue

        if not _VALUE_SEED_RE.search(tok.text):
            i += 1
            continue

        j = i
        window_texts = [tok.text]
        while j + 1 < n and (j - i) < 4:
            nxt = tokens[j + 1]
            if _MERGE_CONNECTOR_RE.fullmatch(nxt.text) or _MERGE_DIGIT_RE.fullmatch(nxt.text):
                window_texts.append(nxt.text)
                j += 1
            else:
                break
        merged = "".join(window_texts) if len(window_texts) > 1 else tok.text

        for candidate_text, end_idx in [(tok.text, i), (merged, j)]:
            types = classify_value_types(candidate_text)
            if not types:
                continue
            unit_nearby = _find_adjacent_unit(tokens, end_idx)
            cand = Candidate(candidate_text, types, tok.index, tok.element, tok.dom_path, unit_nearby)
            candidates.append(cand)

        i = j + 1 if merged != tok.text else i + 1

    # De-duplicate identical (raw_text, token_index)
    seen = set()
    deduped: List[Candidate] = []
    for c in candidates:
        key = (c.raw_text, c.token_index)
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    # Prefer longer merged candidate from same seed
    by_seed: Dict[int, List[Candidate]] = {}
    for c in deduped:
        by_seed.setdefault(c.token_index, []).append(c)
    final: List[Candidate] = []
    for token_index, group in by_seed.items():
        if len(group) == 1:
            final.extend(group)
        else:
            longest = max(group, key=lambda c: len(c.raw_text))
            final.append(longest)
    return final


def _find_adjacent_unit(tokens: List[Token], idx: int, window: int = 3) -> Optional[str]:
    lo, hi = max(0, idx - window), min(len(tokens), idx + window + 1)
    for k in range(lo, hi):
        if k == idx:
            continue
        t = tokens[k].text.strip("،.,:؛;()[]{}»«\"'%$")
        if (
            t.lower() in [u.lower() for u in DURATION_UNITS]
            or t in ["%"]
            or any(cs in tokens[k].text.lower() for cs in CURRENCY_SYMS)
        ):
            return tokens[k].text
    return None


# ---------------------------------------------------------------------------
# Candidate scoring and ranking
# ---------------------------------------------------------------------------

def _page_wide_stem_counts(tokens: List[Token]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for tok in tokens:
        s = stem(tok.text)
        if s:
            counts[s] = counts.get(s, 0) + 1
    return counts


def _local_window(tokens: List[Token], center_idx: int, window: int = LOCAL_WINDOW_TOKENS) -> List[Token]:
    lo = max(0, center_idx - window)
    hi = min(len(tokens), center_idx + window + 1)
    return tokens[lo:hi]


def score_candidate(candidate: Candidate, tokens: List[Token], stem_counts: Dict[str, int]) -> Dict[str, float]:
    nearby = _local_window(tokens, candidate.token_index)
    nearby_text_join = " ".join(t.text for t in nearby)

    for field, profile in FIELD_PROFILES.items():
        score = 0.0

        # Stem matching with distance decay
        best_stem_hit = None
        for t in nearby:
            s = stem(t.text)
            if s and s in profile["core_stems"]:
                token_dist = abs(t.index - candidate.token_index)
                dom_dist = dom_distance(t.dom_path, candidate.dom_path)
                dist = min(token_dist, dom_dist)
                decayed = 1.0 / (1.0 + dist)
                weight = WEIGHTS["STEM_WEIGHT"]

                if stem_counts.get(s, 0) >= WEIGHTS["BOILERPLATE_DAMPING_THRESHOLD"]:
                    weight *= WEIGHTS["BOILERPLATE_DAMPING_FACTOR"]

                # RTL reading-order prior
                if t.index > candidate.token_index:
                    weight *= 0.5

                contribution = weight * decayed
                if best_stem_hit is None or contribution > best_stem_hit:
                    best_stem_hit = contribution

        if best_stem_hit:
            score += best_stem_hit

        # Unit signal
        unit_hit = False
        if candidate.unit_nearby:
            unit_lower = candidate.unit_nearby.lower()
            if any(u.lower() in unit_lower or unit_lower in u.lower() for u in profile["unit_hints"]):
                unit_hit = True
        else:
            if any(u.lower() in nearby_text_join.lower() for u in profile["unit_hints"]):
                unit_hit = True

        if unit_hit:
            score += WEIGHTS["UNIT_WEIGHT"]
        elif profile.get("requires_unit"):
            score += WEIGHTS["MISSING_UNIT_PENALTY"]

        # Type compatibility
        forbidden = profile.get("forbidden_types", set())
        if candidate.types & forbidden:
            score -= 5.0
        elif candidate.types & profile["expected_types"]:
            score += WEIGHTS["TYPE_WEIGHT"]
        elif candidate.types & profile.get("expected_types_weak", set()):
            score += WEIGHTS["TYPE_WEIGHT"] * 0.25

        candidate.scores[field] = score

    return candidate.scores


def apply_position_prior(candidates: List[Candidate]) -> None:
    for c in candidates:
        cluster_size = sum(
            1 for other in candidates
            if other is not c and dom_distance(other.dom_path, c.dom_path) <= 3
        )
        if cluster_size >= 2:
            for field in c.scores:
                c.scores[field] += WEIGHTS["POSITION_WEIGHT"]


def softmax(score_map: Dict[str, float]) -> Dict[str, float]:
    if not score_map:
        return {}
    values = list(score_map.values())
    m = max(values)
    exps = {k: math.exp(v - m) for k, v in score_map.items()}
    total = sum(exps.values()) or 1.0
    return {k: v / total for k, v in exps.items()}


def score_all(candidates: List[Candidate], tokens: List[Token], stem_counts: Dict[str, int]) -> List[Candidate]:
    for c in candidates:
        score_candidate(c, tokens, stem_counts)
    apply_position_prior(candidates)
    for c in candidates:
        c.probabilities = softmax(c.scores)
    return candidates


def resolve_fields(candidates: List[Candidate], target_fields: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    fields = target_fields or list(FIELD_PROFILES.keys())
    per_field: Dict[str, List[Tuple[float, Candidate]]] = {field: [] for field in fields}

    for c in candidates:
        for field in fields:
            prob = c.probabilities.get(field, 0.0)
            if prob > 0.0:
                per_field[field].append((prob, c))

    results = {}
    for field, scored in per_field.items():
        if not scored:
            results[field] = {
                "value": None,
                "confidence": 0.0,
                "strategy": "no_candidates_found",
                "competing_candidates": [],
            }
            continue
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top_prob, top_cand = scored[0]
        runner_ups = scored[1:4]
        margin = top_prob - (runner_ups[0][0] if runner_ups else 0.0)
        strategy = "local_inference" if margin >= LOCAL_CONFIDENCE_MARGIN else "global_inference_ambiguous"
        
        full_value = top_cand.raw_text
        if top_cand.unit_nearby and top_cand.unit_nearby not in top_cand.raw_text:
            full_value = f"{top_cand.raw_text} {top_cand.unit_nearby}"
            
        results[field] = {
            "value": full_value,
            "confidence": round(top_prob, 3),
            "strategy": strategy,
            "evidence": {
                "types": sorted(top_cand.types),
                "unit_nearby": top_cand.unit_nearby,
                "raw_score": round(top_cand.scores.get(field, 0.0), 3),
            },
            "competing_candidates": [
                {"value": c.raw_text, "confidence": round(p, 3)} for p, c in runner_ups
            ],
        }
    return results


# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------

def infer_fields(html_or_soup: Any, target_fields: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Context-aware inference extraction from HTML string or BeautifulSoup instance."""
    if isinstance(html_or_soup, BeautifulSoup):
        soup = html_or_soup
    else:
        soup = BeautifulSoup(html_or_soup, "lxml" if "lxml" in BeautifulSoup.__module__ else "html.parser")

    tokens = flatten(soup)
    if not tokens:
        return {}
    stem_counts = _page_wide_stem_counts(tokens)
    candidates = extract_candidates(tokens)
    candidates = score_all(candidates, tokens, stem_counts)
    return resolve_fields(candidates, target_fields=target_fields)
