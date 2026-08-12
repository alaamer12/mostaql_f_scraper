import urllib.parse as urlparse
from typing import List, Dict, Any, Optional

class ComboManager:
    """Manages the generation and labeling of search filter combinations."""
    
    FIXED_PARAMS = {"verified": "false", "specialities": "development"}
    
    TITLES = [
        "researcher", "pharmacist", "writer", "translator", "data-analyst",
        "graphic-designer", "web-designer", "software-engineer",
        "computer-engineer", "electrical-engineer",
        "logo-designer", "web-developer", "digital-marketer", "virtual-assistant",
        "flutter-developer", "executive-director",
        "مصمم-ومدخل-بيانات", "مبرمج-و-مسوق", "تصميم-وكتابة",
        "محاسب-بنكي",
    ]

    SKILLS = sorted({
        "troubleshooting", "sql", "graphic-design", "phone-support", "psychology",
        "ajax", "data-warehousing", "اسكتش-اب", "data-entry", "sticker-design",
        "microsoft", "circuit-design", "asp-net", "life-coaching", "unix",
        "mysql", "angular-js", "research", "website-design", "creative-design",
        "templates", "dot-net", "IOS", "english-grammar", "إنشاء-موقع-إلكتروني",
        "search-engine-marketing", "jquery", "google-adwords", "market-research",
        "csharp-programming", "proposal-bid-writing", "broadcast-direction",
        "powerpoint", "advertisement-design", "landing-pages", "animation",
        "concept-design", "accounting", "time-management", "investment-research",
        "slogans", "content-writing", "english-spelling", "database-programming",
        "تعديل-الصوت", "telemarketing", "word-processing", "electrical-engineering",
        "test-automation", "photoshop", "business-writing", "video-production",
        "إنشاء-موقع-ووردبريس", "interior-design", "startups", "software-development",
    })

    RATINGS = ["1", "2", "3", "4", "5"]

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

    def __init__(self, base_url: str = "https://mostaql.com/freelancers"):
        self.base_url = base_url

    def get_combinations(self) -> List[Dict[str, Any]]:
        """Return the full list of combo descriptors."""
        combos = [{"dim": "base", "value": None, "params": {}}]
        for r in self.RATINGS:
            combos.append({"dim": "rating", "value": r, "params": {"rating": r}})
        for t in self.TITLES:
            combos.append({"dim": "titles", "value": t, "params": {"titles": t}})
        for s in self.SKILLS:
            combos.append({"dim": "skills", "value": s, "params": {"skills": s}})
        for c in self.COUNTRIES:
            combos.append({"dim": "country", "value": c, "params": {"country": c}})
        return combos

    def get_url(self, combo: Dict[str, Any], page: int = 1) -> str:
        """Construct the URL for a specific combination and page."""
        params = dict(self.FIXED_PARAMS)
        params.update(combo.get("params", {}))
        if "keyword" in combo:
            params["keyword"] = combo["keyword"]
        if page > 1:
            params["page"] = str(page)
        return self.base_url + "?" + urlparse.urlencode(params)

    def get_label(self, combo: Dict[str, Any]) -> str:
        """Get a human-readable label for the combination."""
        if combo["dim"] == "base":
            return "development (base)"
        return f"development+{combo['dim']}={combo['value']}"
