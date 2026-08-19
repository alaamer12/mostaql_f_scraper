"""
inference.py
-------------
Context-aware, structure-independent field extraction for Mostaql project
pages. Does NOT rely on exact label strings or current class/id names.

Pipeline:
  1. Flatten the DOM into an ordered text-token stream, each token carrying
     a pointer back to its source element (defeats split-word / split-char
     / per-word-span attacks).
  2. Extract VALUE CANDIDATES from that stream (numbers, currency, ranges,
     percentages, dates, duration units, placeholders) - independent of
     which field they might belong to.
  3. Score every (candidate, field) pair using hand-weighted additive
     signals (Arabic stem match, unit match, type match, position prior),
     with distance decay in both token-space and DOM-space.
  4. Softmax-normalize competing field scores per candidate to produce
     probabilities, so ambiguous candidates are ranked, not binary-assigned.
  5. Resolve one winner per field across all its competing candidates,
     keeping runner-ups as "competing_candidates" for transparency.

No hardcoded synonym dictionary. No ML / TF-IDF - see design rationale in
conversation. All weights are named constants in WEIGHTS below, meant to be
hand-tuned against adversarial fixtures.
"""

import re
import math
from bs4 import BeautifulSoup, NavigableString, Comment

# ---------------------------------------------------------------------------
# Tunable weights (hand-set, not learned)
# ---------------------------------------------------------------------------

WEIGHTS = {
    "STEM_WEIGHT": 3.0,
    "UNIT_WEIGHT": 2.0,
    "TYPE_WEIGHT": 1.0,
    "POSITION_WEIGHT": 0.5,
    "MISSING_UNIT_PENALTY": -1.5,   # field requires a unit/format, candidate has none nearby
    "BOILERPLATE_DAMPING_THRESHOLD": 6,  # if a stem/unit token appears >= this many times
                                          # page-wide, damp its weight (cheap page-local stat,
                                          # not corpus TF-IDF)
    "BOILERPLATE_DAMPING_FACTOR": 0.35,
}

LOCAL_WINDOW_TOKENS = 12          # how far (in flattened tokens) to look for local signals
LOCAL_CONFIDENCE_MARGIN = 0.20    # min softmax gap between 1st and 2nd place to accept locally

ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ARABIC_TO_ASCII = str.maketrans(ARABIC_DIGITS, "0123456789")
ARABIC_DIGIT_RE = re.compile(f"[{ARABIC_DIGITS}]")

# Arabic prefixes/suffixes stripped for crude root/stem matching.
# This is morphology, not a synonym table: it lets "المدة" match "مدة",
# "بدأت" match "بدأ", "تنفيذه" match "تنفيذ", etc.
_PREFIXES = ["ال", "و", "ف", "ب", "ل", "لل"]
_SUFFIXES = ["ها", "هم", "ه", "ة", "ات", "ين", "ون", "ي", "ا"]


def _strip_affixes(word):
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
            w = w[: -len(s)]
            break
    return w


def normalize_ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def stem(word):
    """Very crude Arabic stem: strip common affixes, lowercase (no-op for
    Arabic but harmless), drop diacritics/tatweel."""
    w = normalize_ws(word)
    w = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", w)  # diacritics + tatweel
    w = w.strip("،.,:؛;()[]{}»«\"'")
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
CURRENCY_SYMS = ["$", "usd", "دولار", "ريال", "sar", "egp", "جنيه"]
NOT_CALCULATED_MARKERS = ["لم يحسب بعد", "غير محدد", "n/a", "لا يوجد"]

DURATION_UNITS = ["يوم", "يوما", "أيام", "ايام", "ساعة", "ساعات", "أسبوع", "اسبوع", "أسابيع", "شهر", "أشهر"]
RELATIVE_DATE_WORDS = ["منذ", "قبل", "خلال"]
ABSOLUTE_DATE_RE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b")
MONTH_NAMES = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
               "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def classify_value_types(token_text):
    """Return a set of type tags for a raw numeric/date-ish token string."""
    t = token_text.strip()
    types = set()
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
    return types


# ---------------------------------------------------------------------------
# Field profiles - domain knowledge, not a synonym dictionary
# ---------------------------------------------------------------------------
# core_stems: crude stems expected to appear near a label for this field
# expected_types: candidate VALUE types this field can plausibly hold
# unit_hints: literal unit/format tokens that strongly support this field
# requires_unit: if True and no unit_hint is found nearby, apply penalty

FIELD_PROFILES = {
    "project_status": {
        "core_stems": [stem("حالة"), stem("المشروع")],
        "expected_types": {"TEXT"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "published_date": {
        "core_stems": [stem("نشر"), stem("تاريخ")],
        "expected_types": {"DATE"},
        "expected_types_weak": {"NUMBER"},  # accepted but scored lower than DATE
        "unit_hints": MONTH_NAMES,
        "requires_unit": False,
    },
    "budget": {
        "core_stems": [stem("ميزانية"), stem("تكلفة"), stem("سعر")],
        "expected_types": {"NUMBER", "FLOAT", "RANGE", "PLACEHOLDER"},
        "unit_hints": CURRENCY_SYMS,
        "requires_unit": True,
    },
    "duration": {
        "core_stems": [stem("مدة"), stem("تنفيذ"), stem("وقت"), stem("لازم")],
        "expected_types": {"NUMBER", "FLOAT"},
        "unit_hints": DURATION_UNITS,
        "requires_unit": True,
    },
    "registration_date": {
        "core_stems": [stem("تسجيل"), stem("تاريخ")],
        "expected_types": {"DATE"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": MONTH_NAMES,
        "requires_unit": False,
    },
    "hire_rate": {
        "core_stems": [stem("معدل"), stem("توظيف")],
        "expected_types": {"PERCENT", "NUMBER"},
        "unit_hints": ["%"],
        "requires_unit": True,
    },
    "open_projects_count": {
        "core_stems": [stem("مشاريع"), stem("مفتوحة")],
        "expected_types": {"NUMBER"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "in_progress_count": {
        "core_stems": [stem("مشاريع"), stem("تنفيذ")],
        "expected_types": {"NUMBER"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "ongoing_conversations": {
        "core_stems": [stem("تواصلات"), stem("جارية")],
        "expected_types": {"NUMBER"},
        "unit_hints": [],
        "requires_unit": False,
    },
    "started_since": {
        "core_stems": [stem("بدأ"), stem("تنفيذه"), stem("منذ")],
        "expected_types": {"DATE", "NUMBER"},
        "unit_hints": RELATIVE_DATE_WORDS + DURATION_UNITS,
        "requires_unit": False,
    },
    "deal_date": {
        "core_stems": [stem("تاريخ"), stem("الصفقة")],
        "expected_types": {"DATE"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": MONTH_NAMES,
        "requires_unit": False,
    },
    "delivery_date": {
        "core_stems": [stem("موعد"), stem("التسليم")],
        "expected_types": {"DATE"},
        "expected_types_weak": {"NUMBER"},
        "unit_hints": MONTH_NAMES,
        "requires_unit": False,
    },
}


# ---------------------------------------------------------------------------
# Step 1: flatten DOM into ordered token stream
# ---------------------------------------------------------------------------

class Token:
    __slots__ = ("text", "index", "element", "dom_path")

    def __init__(self, text, index, element, dom_path):
        self.text = text
        self.index = index
        self.element = element
        self.dom_path = dom_path  # tuple of ancestor ids (python id()) for cheap distance calc

    def __repr__(self):
        return f"Token({self.text!r}@{self.index})"


def _dom_path(el):
    path = []
    cur = el
    while cur is not None and getattr(cur, "name", None) is not None:
        path.append(id(cur))
        cur = cur.parent
    return tuple(path)


def flatten(soup):
    """Walk the DOM in document order, split leaf text into whitespace
    tokens, and return a flat list of Token objects. Splitting into
    word-tokens (not per-element chunks) is what defeats 'every word in
    its own span' and 'split words/characters across nested elements':
    once flattened, the stream reads the same either way."""
    tokens = []
    idx = 0
    for el in soup.find_all(True):
        # only take text that belongs directly to this element to avoid
        # duplicating text that will also be seen via a child element
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


def dom_distance(path_a, path_b):
    """Cheap DOM distance: number of hops to the shared ancestor, summed
    across both sides. Lower = closer in the tree."""
    set_b = set(path_b)
    hops_a = 0
    for node in path_a:
        if node in set_b:
            hops_b = path_b.index(node)
            return hops_a + hops_b
        hops_a += 1
    return hops_a + len(path_b)  # no common ancestor found (shouldn't happen)


# ---------------------------------------------------------------------------
# Step 2: candidate extraction from the flattened stream
# ---------------------------------------------------------------------------

class Candidate:
    def __init__(self, raw_text, types, token_index, element, dom_path, unit_nearby=None):
        self.raw_text = raw_text
        self.types = types
        self.token_index = token_index
        self.element = element
        self.dom_path = dom_path
        self.unit_nearby = unit_nearby  # literal unit text found adjacent, if any
        self.scores = {}       # field -> raw additive score
        self.probabilities = {}  # field -> softmax probability

    def __repr__(self):
        return f"Candidate({self.raw_text!r}, types={self.types})"


_MERGE_CONNECTOR_RE = re.compile(r"\A(?:[-.]|\$)\Z")
_MERGE_DIGIT_RE = re.compile(r"\A\d+%?\Z")
_VALUE_SEED_RE = re.compile(r"\d")  # a token can only START a candidate/merge if it contains a digit


def extract_candidates(tokens):
    """Find value-shaped tokens, merging forward only from a token that is
    ITSELF already digit-bearing (never starts a merge from an arbitrary
    word) - this catches a number split from its unit/continuation like
    '11' + '.' or a range split across ' - ' tokens, without accidentally
    swallowing a preceding word like 'تنفيذ' into the merge."""
    candidates = []
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]

        if not _VALUE_SEED_RE.search(tok.text):
            # not digit-bearing at all: only useful as adjacent-unit context,
            # never as a candidate seed or merge start
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

        # evaluate both the bare single token and the merged window
        for candidate_text, end_idx in [(tok.text, i), (merged, j)]:
            types = classify_value_types(candidate_text)
            if not types:
                continue
            unit_nearby = _find_adjacent_unit(tokens, end_idx)
            cand = Candidate(candidate_text, types, tok.index, tok.element, tok.dom_path, unit_nearby)
            candidates.append(cand)

        i = j + 1 if merged != tok.text else i + 1

    # de-duplicate identical (text, token_index) pairs from the merge/bare overlap
    seen = set()
    deduped = []
    for c in candidates:
        key = (c.raw_text, c.token_index)
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    # Suppress a bare sub-candidate when a longer merged candidate starting
    # at the SAME token already subsumes it (e.g. don't let "500" compete
    # against "500-$800" from the same seed - they're the same underlying
    # value at two granularities, not two independent pieces of evidence).
    by_seed = {}
    for c in deduped:
        by_seed.setdefault(c.token_index, []).append(c)
    final = []
    for token_index, group in by_seed.items():
        if len(group) == 1:
            final.extend(group)
            continue
        longest = max(group, key=lambda c: len(c.raw_text))
        final.append(longest)
    return final


def _find_adjacent_unit(tokens, idx, window=3):
    lo, hi = max(0, idx - window), min(len(tokens), idx + window + 1)
    for k in range(lo, hi):
        if k == idx:
            continue
        t = tokens[k].text.strip("،.,:؛;()[]{}»«\"'%$")
        if t.lower() in [u.lower() for u in DURATION_UNITS] or t in ["%"] or \
           any(cs in tokens[k].text.lower() for cs in CURRENCY_SYMS):
            return tokens[k].text
    return None


# ---------------------------------------------------------------------------
# Step 3+4: scoring
# ---------------------------------------------------------------------------

def _page_wide_stem_counts(tokens):
    counts = {}
    for tok in tokens:
        s = stem(tok.text)
        if s:
            counts[s] = counts.get(s, 0) + 1
    return counts


def _local_window(tokens, center_idx, window=LOCAL_WINDOW_TOKENS):
    lo = max(0, center_idx - window)
    hi = min(len(tokens), center_idx + window + 1)
    return tokens[lo:hi]


def score_candidate(candidate, tokens, stem_counts):
    """Compute additive score for `candidate` against every field profile."""
    nearby = _local_window(tokens, candidate.token_index)
    nearby_stems = [stem(t.text) for t in nearby]
    nearby_text_join = " ".join(t.text for t in nearby)

    for field, profile in FIELD_PROFILES.items():
        score = 0.0
        # --- stem signal, with token-distance decay and DOM-distance decay ---
        best_stem_hit = None
        for t in nearby:
            s = stem(t.text)
            if s and s in profile["core_stems"]:
                token_dist = abs(t.index - candidate.token_index)
                dom_dist = dom_distance(t.dom_path, candidate.dom_path)
                dist = min(token_dist, dom_dist)
                decayed = 1.0 / (1.0 + dist)
                weight = WEIGHTS["STEM_WEIGHT"]
                # boilerplate damping: if this stem is extremely common
                # page-wide, it's probably chrome/footer text, not a real
                # nearby label - damp its contribution (page-local stat,
                # not corpus TF-IDF)
                if stem_counts.get(s, 0) >= WEIGHTS["BOILERPLATE_DAMPING_THRESHOLD"]:
                    weight *= WEIGHTS["BOILERPLATE_DAMPING_FACTOR"]
                # reading-order prior: on this (RTL Arabic) site a label is
                # always written before its value in document order. If the
                # matched label token comes AFTER the candidate, it is more
                # likely to be the tail of a completely unrelated, adjacent
                # block (e.g. the last word of the previous field's label)
                # rather than this candidate's own label - damp it instead
                # of letting it compete at full strength.
                if t.index > candidate.token_index:
                    weight *= 0.5
                contribution = weight * decayed
                if best_stem_hit is None or contribution > best_stem_hit:
                    best_stem_hit = contribution
        if best_stem_hit:
            score += best_stem_hit

        # --- unit signal ---
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
        elif profile["requires_unit"]:
            score += WEIGHTS["MISSING_UNIT_PENALTY"]

        # --- type compatibility signal (strong types full weight, weak
        # types - e.g. a bare NUMBER for a field that prefers DATE - get a
        # fraction of the weight so they don't out-compete a properly typed
        # candidate purely on proximity) ---
        if candidate.types & profile["expected_types"]:
            score += WEIGHTS["TYPE_WEIGHT"]
        elif candidate.types & profile.get("expected_types_weak", set()):
            score += WEIGHTS["TYPE_WEIGHT"] * 0.25

        # --- position prior: candidate sits in a block with >=1 other
        # numeric/date candidate (i.e. looks like a metadata cluster) ---
        # computed by caller and passed in via candidate.scores.setdefault below
        candidate.scores[field] = score

    return candidate.scores


def apply_position_prior(candidates):
    """Boost candidates that sit in a DOM neighborhood dense with other
    candidates (a 'metadata cluster'), a cheap structural prior that needs
    no field-specific knowledge."""
    for c in candidates:
        cluster_size = sum(
            1 for other in candidates
            if other is not c and dom_distance(other.dom_path, c.dom_path) <= 3
        )
        if cluster_size >= 2:
            for field in c.scores:
                c.scores[field] += WEIGHTS["POSITION_WEIGHT"]


def softmax(score_map):
    if not score_map:
        return {}
    values = list(score_map.values())
    m = max(values)
    exps = {k: math.exp(v - m) for k, v in score_map.items()}
    total = sum(exps.values()) or 1.0
    return {k: v / total for k, v in exps.items()}


def score_all(candidates, tokens, stem_counts):
    for c in candidates:
        score_candidate(c, tokens, stem_counts)
    apply_position_prior(candidates)
    for c in candidates:
        c.probabilities = softmax(c.scores)
    return candidates


# ---------------------------------------------------------------------------
# Step 5: resolve one winner per field
# ---------------------------------------------------------------------------

def resolve_fields(candidates):
    """For each field, pick the best-scoring candidate across the page,
    keep runner-ups for transparency."""
    per_field = {field: [] for field in FIELD_PROFILES}
    for c in candidates:
        for field, prob in c.probabilities.items():
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
        results[field] = {
            "value": top_cand.raw_text + (f" {top_cand.unit_nearby}" if top_cand.unit_nearby and top_cand.unit_nearby not in top_cand.raw_text else ""),
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
# Public entry point
# ---------------------------------------------------------------------------

def infer_fields(html_or_soup):
    if isinstance(html_or_soup, BeautifulSoup):
        soup = html_or_soup
    else:
        soup = BeautifulSoup(html_or_soup, "html.parser")

    tokens = flatten(soup)
    stem_counts = _page_wide_stem_counts(tokens)
    candidates = extract_candidates(tokens)
    candidates = score_all(candidates, tokens, stem_counts)
    return resolve_fields(candidates)
