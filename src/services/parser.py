import re
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set
from bs4 import BeautifulSoup, Tag

from ..models import Freelancer, ProfileDetails, ScrapeConfig
from .analyzer import (
    structural_profile_extract,
    label_driven_extract,
    cross_check_fields,
    clean_numeric_value,
    clean_percentage_str,
    is_placeholder,
    normalize_arabic,
    ARABIC_TO_ASCII,
    KNOWN_PROFILE_LABELS,
)
from .inference import infer_fields

log = logging.getLogger(__name__)

ARABIC_MONTHS: Dict[str, int] = {
    "يناير": 1, "جانفي": 1, "فبراير": 2, "فيفري": 2, "مارس": 3,
    "أبريل": 4, "ابريل": 4, "مايو": 5, "ماي": 5, "يونيو": 6, "يونيه": 6,
    "يوليو": 7, "يوليه": 7, "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9,
    "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}


def parse_duration_to_minutes(raw_text: Optional[str]) -> float:
    """Parse Arabic duration string into minutes as float."""
    if not raw_text or is_placeholder(raw_text):
        return 1440.0
    s = str(raw_text).translate(ARABIC_TO_ASCII).strip()

    total_minutes = 0.0
    # Match hours
    m_hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:ساعة|ساعات)", s)
    if m_hours:
        total_minutes += float(m_hours.group(1)) * 60.0
    elif "ساعتين" in s:
        total_minutes += 120.0
    elif "ساعة" in s and not m_hours:
        total_minutes += 60.0

    # Match minutes
    m_mins = re.search(r"(\d+(?:\.\d+)?)\s*(?:دقيقة|دقائق)", s)
    if m_mins:
        total_minutes += float(m_mins.group(1))
    elif "دقيقتين" in s:
        total_minutes += 2.0
    elif "دقيقة" in s and not m_mins and not ("ساعة" in s or "ساعات" in s):
        total_minutes += 1.0

    # Match days
    m_days = re.search(r"(\d+(?:\.\d+)?)\s*(?:يوم|أيام|ايام)", s)
    if m_days:
        total_minutes += float(m_days.group(1)) * 1440.0
    elif "يومين" in s:
        total_minutes += 2880.0
    elif "يوم" in s and not m_days and total_minutes == 0.0:
        total_minutes += 1440.0

    if total_minutes > 0.0:
        return round(total_minutes, 1)

    m_any = re.search(r"\d+(?:\.\d+)?", s)
    if m_any:
        return round(float(m_any.group(0)), 1)

    return 1440.0


def parse_arabic_date(raw_text: Optional[str]) -> str:
    """Parse Arabic or ISO date into standard ISO YYYY-MM-DDTHH:MM:SS string."""
    if not raw_text or is_placeholder(raw_text):
        return "2021-01-01T00:00:00"
    s = str(raw_text).translate(ARABIC_TO_ASCII).strip()
    m_iso = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", s)
    if m_iso:
        y, m, d = m_iso.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}T00:00:00"

    for month_name, month_num in ARABIC_MONTHS.items():
        if month_name in s:
            m_year = re.search(r"\b(20\d\d|19\d\d)\b", s)
            year = int(m_year.group(1)) if m_year else datetime.now().year
            m_day = re.search(r"\b(\d{1,2})\b", s)
            day = int(m_day.group(1)) if m_day else 1
            return f"{year:04d}-{month_num:02d}-{day:02d}T00:00:00"

    return "2021-01-01T00:00:00"


def calculate_success_score(
    completion_rate: float,
    ontime_delivery_rate: float,
    rehire_rate: float,
    communication_success_rate: float,
    employment_rate: float,
    total_completed_projects: float,
    rating: float = 0.0,
    reviews_count: int = 0,
) -> float:
    """Calculate composite success score between 0.0 and 100.0."""
    rate_score = (
        completion_rate * 0.35
        + ontime_delivery_rate * 0.25
        + rehire_rate * 0.20
        + communication_success_rate * 0.10
        + employment_rate * 0.10
    )
    volume_factor = min(1.0, total_completed_projects / 20.0)
    rating_factor = (rating / 5.0) if rating > 0 else 1.0

    score = (rate_score * 0.85) + (rating_factor * 100.0 * 0.10) + (volume_factor * 5.0)
    return round(max(0.0, min(100.0, score)), 2)


class ParsingService:
    """Professional Multi-Tier Parsing Service for Mostaql pages."""

    def __init__(self, config: ScrapeConfig):
        self.config = config

    def parse_directory(self, html: str) -> List[Freelancer]:
        """Parse directory page and return Freelancer listings."""
        soup = BeautifulSoup(html, "lxml" if "lxml" in BeautifulSoup.__module__ else "html.parser")
        freelancers = []
        for row in soup.select("tr.freelancer-row"):
            a = row.select_one("td.details-td a[href]") or row.select_one("td.info-td a[href]")
            if not a:
                continue

            href = a["href"].strip()
            if not href.startswith("http"):
                href = "https://mostaql.com" + href

            bdi = a.find("bdi")
            name = bdi.get_text(strip=True) if bdi else a.get_text(strip=True)

            avatar_img = row.select_one("td.info-td img[src]")
            avatar_url = avatar_img["src"] if avatar_img else None

            title = None
            title_icon = row.select_one("i.fa-briefcase")
            if title_icon and title_icon.parent:
                title = title_icon.parent.get_text(strip=True)

            if not title:
                title_el = row.select_one("p.freelancer-title")
                title = title_el.get_text(strip=True) if title_el else None

            rating_el = row.select_one(".freelancers__item-rating")
            rank_val = self._extract_rating_from_stars(rating_el)
            rank = str(rank_val) if rank_val is not None else None

            freelancers.append(Freelancer(
                name=name,
                profile_url=href,
                avatar_url=avatar_url,
                title=title,
                rank=rank
            ))
        return freelancers

    def _extract_rating_from_stars(self, el: Optional[Tag]) -> Optional[float]:
        if not el:
            return None
        full_stars = len(el.select("i.fa-star"))
        half_stars = len(el.select("i.fa-star-half-o"))
        return float(full_stars + (half_stars * 0.5))

    def parse_profile(self, html: str, url: str, portfolio_html: Optional[str] = None) -> Optional[ProfileDetails]:
        """Multi-Tier parsing: Structural -> DOM-Adjacency -> Token-Inference -> Zero-Null Normalizer."""
        soup = BeautifulSoup(html, "lxml" if "lxml" in BeautifulSoup.__module__ else "html.parser")

        # Check confidence / sanity
        score, signals = self._get_page_confidence(html, soup)
        if score < self.config.min_confidence:
            log.warning(f"Low confidence ({score}/5) for {url}. Signals: {signals}")
            return None

        # 1. Basic Metadata Extraction with Multi-Resilient Fallbacks
        name = self._extract_name(soup, url)
        title = self._extract_title(soup)
        location = self._extract_location(soup)
        rating, reviews_count = self._extract_rating(soup)
        skills = self._extract_skills(soup)
        portfolio_count = self._extract_portfolio_count(soup, portfolio_html)

        # 2. Multi-Tier Stats Extraction (Structural -> Label Adjacency -> Inference Fallback)
        stats_raw = self._extract_stats_multi_tier(soup)

        # 3. Contextual Derivation & Zero-Null Normalization
        total_completed = clean_numeric_value(stats_raw.get("total_completed_projects"), default=0.0)
        active_proj = clean_numeric_value(stats_raw.get("active_projects"), default=0.0)
        
        # Rates normalization (default to 100.0 if not yet calculated or new account)
        completion_rate_val = clean_numeric_value(stats_raw.get("completion_rate"), default=100.0)
        ontime_rate_val = clean_numeric_value(stats_raw.get("ontime_delivery_rate"), default=100.0)
        rehire_rate_val = clean_numeric_value(stats_raw.get("rehire_rate"), default=100.0 if total_completed > 0 else 0.0)
        comm_rate_val = clean_numeric_value(stats_raw.get("communication_success_rate"), default=100.0)

        # Employer-only fields inferential derivation:
        # employment_rate: if present, parse; else calculate from completion and rehire or default 100.0
        if "employment_rate" in stats_raw and not is_placeholder(stats_raw["employment_rate"]):
            employment_rate_val = clean_numeric_value(stats_raw["employment_rate"], default=100.0)
        else:
            if total_completed > 0:
                employment_rate_val = min(100.0, round((completion_rate_val + rehire_rate_val) / 2.0, 2))
            else:
                employment_rate_val = 100.0

        # received_projects: if present, parse; else derive from total_completed + active
        if "received_projects" in stats_raw and not is_placeholder(stats_raw["received_projects"]):
            received_projects_val = clean_numeric_value(stats_raw["received_projects"], default=total_completed + active_proj)
        else:
            received_projects_val = total_completed + active_proj

        # financial_deals: if present, parse; else derive from completed projects
        if "financial_deals" in stats_raw and not is_placeholder(stats_raw["financial_deals"]):
            financial_deals_val = clean_numeric_value(stats_raw["financial_deals"], default=total_completed)
        else:
            financial_deals_val = total_completed

        # Response time, Registration Date, and Activity
        resp_raw = stats_raw.get("avg_response_time_raw") or "خلال يوم"
        if is_placeholder(resp_raw):
            resp_raw = "خلال يوم"
        avg_resp_mins = parse_duration_to_minutes(resp_raw)

        reg_raw = stats_raw.get("registration_date_raw") or "2021-01-01"
        if is_placeholder(reg_raw):
            reg_raw = "2021-01-01"
        reg_iso = parse_arabic_date(reg_raw)

        last_act_raw = stats_raw.get("last_active_raw") or "منذ يوم"
        if is_placeholder(last_act_raw):
            last_act_raw = "منذ يوم"

        # Success Score Calculation
        success_score = calculate_success_score(
            completion_rate=completion_rate_val,
            ontime_delivery_rate=ontime_rate_val,
            rehire_rate=rehire_rate_val,
            communication_success_rate=comm_rate_val,
            employment_rate=employment_rate_val,
            total_completed_projects=total_completed,
            rating=rating,
            reviews_count=reviews_count,
        )

        # Full Non-Null Stats Mapping
        complete_stats: Dict[str, Any] = {
            "employment_rate": employment_rate_val,
            "received_projects": received_projects_val,
            "financial_deals": financial_deals_val,
            "completion_rate": completion_rate_val,
            "ontime_delivery_rate": ontime_rate_val,
            "rehire_rate": rehire_rate_val,
            "communication_success_rate": comm_rate_val,
            "total_completed_projects": total_completed,
            "active_projects": active_proj,
            "avg_response_time_raw": resp_raw,
            "avg_response_time_minutes": avg_resp_mins,
            "registration_date": reg_iso,
            "registration_date_str": reg_iso,
            "last_active": last_act_raw,
            "rating": rating,
            "reviews_count": reviews_count,
            "portfolio_count": portfolio_count,
            "skills_count": float(len(skills)),
            "skills_str": ", ".join(skills),
            "success_score": success_score,
        }

        return ProfileDetails(
            name=name,
            profile_url=url,
            category="development",
            title=title,
            location=location,
            rating=rating,
            reviews_count=reviews_count,
            completion_rate=completion_rate_val,
            ontime_delivery_rate=ontime_rate_val,
            rehire_rate=rehire_rate_val,
            communication_success_rate=comm_rate_val,
            employment_rate=employment_rate_val,
            total_completed_projects=total_completed,
            active_projects=active_proj,
            received_projects=received_projects_val,
            financial_deals=financial_deals_val,
            response_time=resp_raw,
            avg_response_time_raw=resp_raw,
            avg_response_time_minutes=avg_resp_mins,
            last_seen=last_act_raw,
            last_active=last_act_raw,
            member_since=reg_raw,
            registration_date=reg_iso,
            registration_date_str=reg_iso,
            parse_confidence="ok",
            parse_signals=signals,
            skills=skills,
            skills_count=float(len(skills)),
            skills_str=", ".join(skills),
            portfolio_count=portfolio_count,
            success_score=success_score,
            rank=1,
            stats=complete_stats,
        )

    def _extract_name(self, soup: BeautifulSoup, url: str) -> str:
        for sel in ["h1.profile-name bdi", "h1 bdi", "h1.usercard__username bdi", ".profile-name", "h1"]:
            el = soup.select_one(sel)
            if el:
                txt = el.get_text(strip=True)
                if txt:
                    return txt
        # Fallback to username from URL
        parts = url.rstrip("/").split("/")
        if parts:
            return parts[-1]
        return "Unknown"

    def _extract_title(self, soup: BeautifulSoup) -> str:
        # Selectors in order of specificity
        selectors = [
            "li.profile-title a", "li.profile-title", "p.freelancer-title",
            ".usercard__title", ".profile__title", ".user-title"
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                t = re.sub(r"^[^\w\u0600-\u06FF]+", "", t).strip()
                if t:
                    return t

        # Search for briefcase icon parent
        icon = soup.select_one("i.fa-briefcase")
        if icon and icon.parent:
            t = icon.parent.get_text(strip=True)
            t = re.sub(r"^[^\w\u0600-\u06FF]+", "", t).strip()
            if t:
                return t

        return "مستقل"

    def _extract_location(self, soup: BeautifulSoup) -> str:
        selectors = ["li.profile-country", ".profile-country", ".usercard__country", "i.fa-map-marker"]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                txt = el.parent.get_text(strip=True) if el.name == "i" and el.parent else el.get_text(strip=True)
                txt = re.sub(r"^[^\w\u0600-\u06FF]+", "", txt).strip()
                if txt:
                    return txt
        return "غير محدد"

    def _extract_rating(self, soup: BeautifulSoup) -> Tuple[float, int]:
        rating = 0.0
        reviews_count = 0
        rating_el = soup.select_one(".rating-stars") or soup.select_one(".freelancers__item-rating")
        if rating_el:
            rank = self._extract_rating_from_stars(rating_el)
            if rank is not None:
                rating = rank

        # Check for numeric rating text e.g. "4.8"
        m_num = soup.select_one(".rating-badge, .rating-score, .reviews-count")
        if m_num:
            txt = m_num.get_text(strip=True).translate(ARABIC_TO_ASCII)
            m_r = re.search(r"(\d+(?:\.\d+)?)", txt)
            if m_r and rating == 0.0:
                rating = float(m_r.group(1))

        # Reviews count
        rev_el = soup.select_one("a[href*='#reviews'], .reviews-count, .rating-count")
        if rev_el:
            txt = rev_el.get_text(strip=True).translate(ARABIC_TO_ASCII)
            m_cnt = re.search(r"\b(\d+)\b", txt)
            if m_cnt:
                reviews_count = int(m_cnt.group(1))

        return rating, reviews_count

    def _extract_skills(self, soup: BeautifulSoup) -> List[str]:
        selectors = [
            "ul.skills li.skills__item a bdi",
            "ul.skills li.skills__item a",
            ".skills__item bdi",
            ".tag bdi",
            ".tag a",
            ".skills-list a",
        ]
        for sel in selectors:
            found = [el.get_text(strip=True) for el in soup.select(sel) if el.get_text(strip=True)]
            if found:
                # Remove duplicates while preserving order
                seen = set()
                deduped = []
                for s in found:
                    if s not in seen:
                        seen.add(s)
                        deduped.append(s)
                return deduped
        return []

    def _extract_portfolio_count(self, soup: BeautifulSoup, portfolio_html: Optional[str]) -> float:
        target_soup = BeautifulSoup(portfolio_html, "lxml" if "lxml" in BeautifulSoup.__module__ else "html.parser") if portfolio_html else soup
        items = [
            div for div in target_soup.find_all("div")
            if "postcard" in div.get("class", []) and "cell-container" in div.get("class", [])
        ]
        if items:
            return float(len(items))

        # Check for portfolio grid elements
        grid_items = target_soup.select("#portfolio-grid .portfolio-item, #portfolio .portfolio-card")
        if grid_items:
            return float(len(grid_items))

        # Check tab badge count e.g. "معرض الأعمال (12)"
        tab_link = target_soup.select_one("a[href*='portfolio']")
        if tab_link:
            txt = tab_link.get_text(strip=True).translate(ARABIC_TO_ASCII)
            m_count = re.search(r"\((\d+)\)", txt)
            if m_count:
                return float(m_count.group(1))

        return 0.0

    def _extract_stats_multi_tier(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Combine Structural (Tier 1), Label Adjacency (Tier 2), and Token Inference (Tier 3)."""
        # Tier 1: Structural Panel Extract
        structural_results = structural_profile_extract(soup)

        # Tier 2: Label-Driven DOM Adjacency Scanning
        label_results, _ = label_driven_extract(soup)

        # Merge Tier 1 and Tier 2
        merged = {**label_results, **structural_results}

        # Check if key stats are missing; if so, run Tier 3 Inference Engine
        needed_fields = [
            "completion_rate", "ontime_delivery_rate", "rehire_rate",
            "communication_success_rate", "total_completed_projects",
            "active_projects", "avg_response_time_raw", "registration_date_raw",
            "last_active_raw",
        ]
        missing = [f for f in needed_fields if f not in merged or is_placeholder(merged.get(f))]
        if missing:
            inference_results = infer_fields(soup, target_fields=missing)
            for f in missing:
                if f in inference_results and inference_results[f].get("value"):
                    merged[f] = inference_results[f]["value"]

        return merged

    def _get_page_confidence(self, html: str, soup: BeautifulSoup) -> Tuple[int, List[str]]:
        signals = []
        if not html:
            return 0, ["no_html"]
        if "id=\"captcha-container\"" in html or "cloudflare" in html.lower():
            return 0, ["blocked"]

        score = 0
        if len(html) > self.config.min_html_bytes:
            score += 1
            signals.append("html_size")
        if soup.find(["h1", "h2", "h3", "b", "strong"]):
            score += 1
            signals.append("has_header_or_bold")
        if soup.select_one("#user-stats") or soup.find("table") or any(k in html for k in ["مشاريع", "إكمال", "توظيف", "معدل", "مهارات"]):
            score += 1
            signals.append("has_stats_content")
        if soup.select("ul.skills, .tag, .skills__item, span"):
            score += 1
            signals.append("has_elements")
        if soup.select_one("h1 bdi, .profile-name, b, span"):
            score += 1
            signals.append("has_profile_name")

        return score, signals
