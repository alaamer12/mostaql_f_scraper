"""Arabic linguistic normalization and inflection engine.

Provides:
- Orthographic normalization: Alif variants, Taa-marboota/Haa, Yaa/Alef Maksura,
  Hamza normalization, Tashkeel/Harakat stripping, Tatweel stripping, and invisible Unicode mark removal.
- Arabic-Indic numeral folding (٠-٩, ۰-۹ -> 0-9).
- Grammatical number inflection resolution for singular, dual, and plural (مفرد، مثنى، جمع).
- Temporal / prepositional phrase parsing (منذ, خلال, في, قبل, بعد).
"""

import re
from typing import Optional, Tuple, Dict, Any

# Tashkeel / Harakat characters
TASHKEEL_REGEX = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")

# Tatweel (Kashida)
TATWEEL_REGEX = re.compile(r"\u0640")

# Control / Zero-width / BiDi characters (LRM, RLM, ZWNJ, ZWJ, etc.)
BIDI_REGEX = re.compile(r"[\u200B-\u200F\u202A-\u202E\uFEFF]")

# Arabic-Indic digits & Arabic symbols mapping
ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٪٬٫", "01234567890123456789%,.")

# Arabic Month names to number mapping
ARABIC_MONTHS = {
    "يناير": 1, "كانون الثاني": 1,
    "فبراير": 2, "شباط": 2,
    "مارس": 3, "آذار": 3, "اذار": 3,
    "أبريل": 4, "ابريل": 4, "نيسان": 4,
    "مايو": 5, "أيار": 5, "ايار": 5,
    "يونيو": 6, "حزيران": 6,
    "يوليو": 7, "تموز": 7,
    "أغسطس": 8, "اغسطس": 8, "آب": 8, "اب": 8,
    "سبتمبر": 9, "أيلول": 9, "ايلول": 9,
    "أكتوبر": 10, "اكتوبر": 10, "تشرين الأول": 10, "تشرين الاول": 10,
    "نوفمبر": 11, "تشرين الثاني": 11,
    "ديسمبر": 12, "كانون الأول": 12, "كانون الاول": 12,
}

# Unit multipliers in minutes
UNIT_MINUTES = {
    "minute": 1.0,
    "hour": 60.0,
    "day": 1440.0,
    "month": 43200.0,
    "year": 525600.0,
}

# Grammatical inflections dictionary: (singular, dual, plural forms) -> (unit, count)
INFLECTION_RULES = {
    # Minute
    "دقيقة": ("minute", 1.0),
    "دقيقه": ("minute", 1.0),
    "دقيقتان": ("minute", 2.0),
    "دقيقتين": ("minute", 2.0),
    "دقائق": ("minute", 5.0),  # generic plural / few minutes
    "دقايق": ("minute", 5.0),
    "بضع دقائق": ("minute", 5.0),
    
    # Hour
    "ساعة": ("hour", 1.0),
    "ساعه": ("hour", 1.0),
    "ساعتان": ("hour", 2.0),
    "ساعتين": ("hour", 2.0),
    "ساعات": ("hour", 3.0),
    "بضع ساعات": ("hour", 3.0),
    
    # Day
    "يوم": ("day", 1.0),
    "يوما": ("day", 1.0),
    "يومان": ("day", 2.0),
    "يومين": ("day", 2.0),
    "أيام": ("day", 3.0),
    "ايام": ("day", 3.0),
    
    # Month
    "شهر": ("month", 1.0),
    "شهرا": ("month", 1.0),
    "شهران": ("month", 2.0),
    "شهرين": ("month", 2.0),
    "أشهر": ("month", 3.0),
    "اشهر": ("month", 3.0),
    "شهور": ("month", 3.0),
    
    # Year
    "سنة": ("year", 1.0),
    "سنه": ("year", 1.0),
    "عام": ("year", 1.0),
    "عاما": ("year", 1.0),
    "سنتان": ("year", 2.0),
    "سنتين": ("year", 2.0),
    "عامان": ("year", 2.0),
    "عامين": ("year", 2.0),
    "سنوات": ("year", 3.0),
    "أعوام": ("year", 3.0),
    "اعوام": ("year", 3.0),
    
    # Project / Deal / Review inflections for count resolution
    "مشروع": ("project", 1.0),
    "مشروعان": ("project", 2.0),
    "مشروعين": ("project", 2.0),
    "مشاريع": ("project", 3.0),
    
    "صفقة": ("deal", 1.0),
    "صفقه": ("deal", 1.0),
    "صفقتان": ("deal", 2.0),
    "صفقتين": ("deal", 2.0),
    "صفقات": ("deal", 3.0),
    
    "تقييم": ("review", 1.0),
    "تقييمان": ("review", 2.0),
    "تقييمين": ("review", 2.0),
    "تقييمات": ("review", 3.0),
}


def strip_tashkeel(text: str) -> str:
    """Remove Arabic diacritical marks (Harakat/Tashkeel)."""
    if not text:
        return ""
    return TASHKEEL_REGEX.sub("", text)


def strip_tatweel(text: str) -> str:
    """Remove Arabic Tatweel (Kashida)."""
    if not text:
        return ""
    return TATWEEL_REGEX.sub("", text)


def strip_bidi_controls(text: str) -> str:
    """Remove invisible Unicode bidirectional & formatting control characters."""
    if not text:
        return ""
    return BIDI_REGEX.sub("", text)


def normalize_digits(text: str) -> str:
    """Fold Arabic-Indic digits (٠-٩, ۰-۹) into standard ASCII digits (0-9)."""
    if not text:
        return ""
    return text.translate(ARABIC_INDIC_DIGITS)


def normalize_alif(text: str) -> str:
    """Normalize Alif forms (أ, إ, آ, ٱ -> ا)."""
    if not text:
        return ""
    return re.sub(r"[أإآٱ]", "ا", text)


def normalize_taa_marboota(text: str) -> str:
    """Normalize Taa Marboota (ة -> ه)."""
    if not text:
        return ""
    return re.sub(r"ة", "ه", text)


def normalize_yaa(text: str) -> str:
    """Normalize Alef Maksura (ى -> ي)."""
    if not text:
        return ""
    return re.sub(r"ى", "ي", text)


def normalize_hamza(text: str) -> str:
    """Normalize Hamza variations (ؤ, ئ -> ء)."""
    if not text:
        return ""
    return re.sub(r"[ؤئ]", "ء", text)


def normalize_arabic_text(
    text: str,
    normalize_letters: bool = False,
    normalize_digits_flag: bool = True
) -> str:
    """Comprehensive Arabic text normalization.
    
    Always strips Tashkeel, Tatweel, and BiDi controls.
    If normalize_letters is True, normalizes Alif, Taa-Marboota, Yaa, and Hamzas.
    """
    if not text:
        return ""
    
    # 1. Clean invisible characters & diacritics
    cleaned = strip_bidi_controls(text)
    cleaned = strip_tashkeel(cleaned)
    cleaned = strip_tatweel(cleaned)
    
    # 2. Digits
    if normalize_digits_flag:
        cleaned = normalize_digits(cleaned)
        
    # 3. Orthographic normalization
    if normalize_letters:
        cleaned = normalize_alif(cleaned)
        cleaned = normalize_taa_marboota(cleaned)
        cleaned = normalize_yaa(cleaned)
        cleaned = normalize_hamza(cleaned)
        
    return cleaned.strip()


def strip_prepositions(text: str) -> str:
    """Strip common temporal and relational prepositions from Arabic phrases."""
    norm = normalize_arabic_text(text, normalize_letters=True)
    # Remove leading prepositions: منذ, خلال, في, قبل, بعد, حوالي, قرابة
    norm = re.sub(r"^(منذ|خلال|في|قبل|بعد|حوالي|قرابة|بـ|لـ)\s+", "", norm)
    # Remove trailing pronouns: له, لها, بهم, هم
    norm = re.sub(r"\s+(له|لها|بهم|هم)$", "", norm)
    return norm.strip()


def parse_arabic_duration(text: str) -> Optional[float]:
    """Parse Arabic duration phrases into total minutes.
    
    Examples:
    - '3 ساعات و 48 دقيقة' -> 228.0
    - 'ساعة و 22 دقيقة' -> 82.0
    - '44 دقيقة' -> 44.0
    - 'ساعتان' / 'ساعتين' -> 120.0
    - 'يومان' / 'يومين' -> 2880.0
    - 'خلال يوم' -> 1440.0
    - 'منذ 3 دقائق' -> 3.0
    - 'منذ سنتين' -> 1051200.0
    """
    if not text:
        return None

    raw_clean = normalize_arabic_text(text, normalize_letters=False)
    raw_norm = normalize_arabic_text(text, normalize_letters=True)
    
    # Check placeholders
    if any(p in raw_norm for p in ["لم يحسب", "غير محدد", "غير متوفر", "لا يوجد"]):
        return None

    # Remove prepositions for unit matching
    stripped = strip_prepositions(raw_clean)
    stripped_norm = strip_prepositions(raw_norm)

    # 1. Check composite phrases connected by 'و' (e.g. '3 ساعات و 48 دقيقة' or 'ساعة و 22 دقيقة')
    parts = re.split(r"\s+و\s+", stripped)
    if len(parts) > 1:
        total = 0.0
        matched_any = False
        for part in parts:
            part_mins = parse_arabic_duration(part)
            if part_mins is not None:
                total += part_mins
                matched_any = True
        if matched_any:
            return total

    # 2. Check explicit numeral + unit (e.g. '3 ساعات', '48 دقيقة', '2 يوم')
    num_match = re.search(r"(\d+(?:\.\d+)?)\s*([^\d\s]+)", stripped_norm)
    if num_match:
        val = float(num_match.group(1))
        unit_str = num_match.group(2)
        
        # Match unit
        for key, (unit_type, _) in INFLECTION_RULES.items():
            norm_key = normalize_arabic_text(key, normalize_letters=True)
            if norm_key in unit_str or unit_str in norm_key:
                return val * UNIT_MINUTES[unit_type]

    # 3. Check standalone inflection terms (e.g. 'ساعة', 'ساعتان', 'ساعتين', 'يومان', 'سنتين', 'بضع دقائق')
    for key, (unit_type, mult) in INFLECTION_RULES.items():
        norm_key = normalize_arabic_text(key, normalize_letters=True)
        if stripped_norm == norm_key or norm_key in stripped_norm.split():
            return mult * UNIT_MINUTES[unit_type]

    return None


def parse_arabic_inflected_count(text: str) -> Optional[int]:
    """Resolve singular/dual/plural grammatical inflections to exact counts.
    
    Examples:
    - 'مشروع' -> 1
    - 'مشروعان' / 'مشروعين' -> 2
    - 'سنتان' / 'سنتين' -> 2
    - '5 مشاريع' -> 5
    """
    if not text:
        return None
        
    norm = normalize_arabic_text(text, normalize_letters=True)
    
    # Check if explicit digits exist
    num_match = re.search(r"\b(\d+)\b", norm)
    if num_match:
        return int(num_match.group(1))
        
    stripped = strip_prepositions(norm)
    for key, (_, mult) in INFLECTION_RULES.items():
        norm_key = normalize_arabic_text(key, normalize_letters=True)
        if stripped == norm_key or norm_key in stripped.split():
            return int(mult)
            
    return None
