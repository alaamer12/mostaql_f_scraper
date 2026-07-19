"""
Combination universe for the `specialities=development` brute-force sweep.

Every combo = FIXED_PARAMS (verified=false, specialities=development)
             + exactly ONE extra filter value (never two extras stacked).

Dimensions: base (none), rating, titles, skills, country.
"""

from __future__ import annotations

import urllib.parse as urlparse

BASE_URL = "https://mostaql.com/freelancers"
FIXED_PARAMS = {"verified": "false", "specialities": "development"}

# ---------------------------------------------------------------------------
# Titles (from the 3 title-batch URLs you provided).
# NOTE: one Arabic slug in the 2nd batch ("%D9%85%D8%A8%D8%B1" -> "مبر") was
# truncated in your message and decodes to an incomplete word, so it's
# excluded here rather than guessed.
# ---------------------------------------------------------------------------
TITLES = [
    "researcher", "pharmacist", "writer", "translator", "data-analyst",
    "graphic-designer", "web-designer", "software-engineer",
    "computer-engineer", "electrical-engineer",
    "logo-designer", "web-developer", "digital-marketer", "virtual-assistant",
    "flutter-developer", "executive-director",
    "مصمم-ومدخل-بيانات", "مبرمج-و-مسوق", "تصميم-وكتابة",
    "محاسب-بنكي",
]

# ---------------------------------------------------------------------------
# Skills: your 14 example-URL values + the 44-row dropdown table, deduped
# (4 values appeared in both: اسكتش-اب, phone-support, psychology, sticker-design)
# ---------------------------------------------------------------------------
_SKILLS_BATCH_A = [
    "troubleshooting", "sql", "graphic-design", "phone-support", "psychology",
    "ajax", "data-warehousing", "اسكتش-اب", "data-entry", "sticker-design",
    "microsoft", "circuit-design", "asp-net", "life-coaching", "unix",
]

_SKILLS_DROPDOWN = [
    "mysql", "angular-js", "research", "website-design", "creative-design",
    "templates", "dot-net", "IOS", "english-grammar", "إنشاء-موقع-إلكتروني",
    "search-engine-marketing", "jquery", "google-adwords", "market-research",
    "csharp-programming", "proposal-bid-writing", "broadcast-direction",
    "powerpoint", "advertisement-design", "landing-pages", "animation",
    "concept-design", "accounting", "time-management", "اسكتش-اب",
    "investment-research", "slogans", "phone-support", "content-writing",
    "english-spelling", "database-programming", "تعديل-الصوت", "telemarketing",
    "word-processing", "psychology", "electrical-engineering",
    "test-automation", "sticker-design", "photoshop", "business-writing",
    "video-production", "إنشاء-موقع-ووردبريس", "interior-design", "startups",
    "software-development",
]

SKILLS = sorted(set(_SKILLS_BATCH_A) | set(_SKILLS_DROPDOWN), key=str)

RATINGS = ["1", "2", "3", "4", "5"]

# ---------------------------------------------------------------------------
# Countries (231 codes from your table)
# ---------------------------------------------------------------------------
COUNTRIES = [
    "jo", "bh", "dz", "sa", "sd", "so", "iq", "kw", "ma", "ye", "tn", "km",
    "dj", "sy", "om", "ps", "qa", "lb", "ly", "eg", "mr", "aw", "az", "am",
    "au", "ee", "af", "al", "de", "aq", "ag", "ad", "id", "ao", "ai", "uy",
    "uz", "ug", "qo", "ua", "ie", "is", "et", "es", "ir", "it", "ar", "io",
    "ec", "eu", "bs", "br", "pt", "ba", "ga", "me", "dk", "cv", "sv", "sn",
    "se", "cn", "va", "ph", "cm", "cg", "cd", "tf", "mx", "gb", "at", "ne",
    "in", "us", "jp", "gr", "pg", "py", "pk", "pw", "bw", "bb", "bm", "bn",
    "be", "bg", "bz", "bd", "pa", "bj", "bt", "pr", "bf", "bi", "pl", "bo",
    "pf", "pe", "tz", "th", "tw", "tm", "tr", "ta", "tt", "td", "tg", "tv",
    "tk", "to", "tl", "jm", "gi", "ax", "an", "tc", "ky", "ic", "mh", "mv",
    "um", "pn", "sb", "fo", "vi", "vg", "fk", "ck", "cc", "mp", "wf", "ac",
    "cx", "bv", "cp", "im", "nf", "hm", "cf", "cz", "do", "za", "gp", "ge",
    "gs", "je", "dm", "dg", "rw", "ru", "by", "ro", "re", "zm", "zw", "ci",
    "ws", "as", "bl", "sm", "pm", "vc", "kn", "lc", "mf", "sh", "st", "lk",
    "sj", "sk", "si", "sg", "sz", "sr", "ch", "sl", "sc", "sx", "ea", "cl",
    "rs", "tj", "gm", "gh", "gd", "gl", "gt", "gu", "gf", "gy", "gg", "gn",
    "gq", "gw", "vu", "fr", "il", "ve", "fi", "vn", "fj", "cy", "kg", "kz",
    "nc", "hr", "kh", "ca", "cu", "cw", "kr", "kp", "cr", "xk", "co", "ki",
    "ke", "lv", "la", "lu", "lr", "lt", "li", "ls", "mq", "mt", "ml", "my",
    "yt", "mg", "mk", "mo", "mw", "mn", "mu", "mz", "md", "mc", "ms", "mm",
    "fm", "na", "nr", "np", "ng", "ni", "nz", "nu", "ht", "hn", "hu", "nl",
    "bq", "hk",
]


def build_combinations() -> list[dict]:
    """Return the full list of combo descriptors (dim, value, extra params)."""
    combos: list[dict] = [{"dim": "base", "value": None, "params": {}}]
    for r in RATINGS:
        combos.append({"dim": "rating", "value": r, "params": {"rating": r}})
    for t in TITLES:
        combos.append({"dim": "titles", "value": t, "params": {"titles": t}})
    for s in SKILLS:
        combos.append({"dim": "skills", "value": s, "params": {"skills": s}})
    for c in COUNTRIES:
        combos.append({"dim": "country", "value": c, "params": {"country": c}})
    return combos


def combo_url(combo: dict, page: int = 1) -> str:
    params = dict(FIXED_PARAMS)
    params.update(combo["params"])
    if page > 1:
        params["page"] = str(page)
    return BASE_URL + "?" + urlparse.urlencode(params)


def combo_label(combo: dict) -> str:
    if combo["dim"] == "base":
        return "development (base)"
    return f"development+{combo['dim']}={combo['value']}"


if __name__ == "__main__":
    combos = build_combinations()
    print(f"titles={len(TITLES)}  skills={len(SKILLS)}  ratings={len(RATINGS)}  countries={len(COUNTRIES)}")
    print(f"TOTAL COMBINATIONS = {len(combos)}")
    print("sample URL:", combo_url(combos[1]))
    print("sample URL (arabic title):", combo_url({"dim": "titles", "value": "محاسب-بنكي", "params": {"titles": "محاسب-بنكي"}}))
