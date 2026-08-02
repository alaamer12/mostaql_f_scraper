import re
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from bs4 import BeautifulSoup, Tag

from ..models import Freelancer, ProfileDetails, ScrapeConfig

log = logging.getLogger(__name__)

class ParsingService:
    """Stateless service for parsing Mostaql HTML content."""

    STAT_MAP: Dict[str, str] = {
        "معدل التوظيف": "employment_rate",
        "المشاريع المستلمة": "received_projects",
        "تعاملاتي معه": "financial_deals",
        "إكمال المشاريع": "completion_rate",
        "التسليم بالموعد": "ontime_delivery_rate",
        "إعادة التوظيف": "rehire_rate",
        "نجاح التواصلات": "communication_success_rate",
        "نجاح التواصل": "communication_success_rate",
        "المشاريع المكتملة": "total_completed_projects",
        "متوسط سرعة الرد": "avg_response_time_raw",
        "تاريخ التسجيل": "registration_date_raw",
        "آخر تواجد": "last_active_raw",
        "مشاريع يعمل عليها": "active_projects",
    }

    ARABIC_MONTHS: Dict[str, int] = {
        "يناير": 1, "جانفي": 1, "فبراير": 2, "فيفري": 2, "مارس": 3,
        "أبريل": 4, "ابريل": 4, "مايو": 5, "ماي": 5, "يونيو": 6, "يونيه": 6,
        "يوليو": 7, "يوليه": 7, "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9,
        "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    }

    ARABIC_WORD_NUMS: Dict[str, int] = {
        "دقيقة": 1, "دقيقتين": 2, "دقائق": 1, "ساعة": 60, "ساعتين": 120,
        "ساعات": 60, "يوم": 1440, "يومين": 2880, "أيام": 1440,
        "أسبوع": 10080, "أسبوعين": 20160,
    }

    def __init__(self, config: ScrapeConfig):
        self.config = config
        self._stat_map_norm = {self._normalize_arabic(k): v for k, v in self.STAT_MAP.items()}

    def _normalize_arabic(self, text: str) -> str:
        """Normalise common Arabic letter variants."""
        text = re.sub(r"[إأآا]", "ا", text)
        text = text.replace("\u0640", "")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def parse_directory(self, html: str) -> List[Freelancer]:
        """Parse a directory page and return a list of Freelancer objects."""
        soup = BeautifulSoup(html, "lxml")
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
            
            # Additional info
            avatar_img = row.select_one("td.info-td img[src]")
            avatar_url = avatar_img["src"] if avatar_img else None
            
            title_el = row.select_one("p.freelancer-title")
            title = title_el.get_text(strip=True) if title_el else None
            
            freelancers.append(Freelancer(
                name=name,
                profile_url=href,
                avatar_url=avatar_url,
                title=title
            ))
        return freelancers

    def parse_profile(self, html: str, url: str, portfolio_html: Optional[str] = None) -> Optional[ProfileDetails]:
        """Parse a detailed profile page."""
        soup = BeautifulSoup(html, "lxml")
        
        # Confidence Check
        score, signals = self._get_page_confidence(html, soup)
        if score < self.config.min_confidence:
            log.warning(f"Low confidence ({score}/5) for {url}. Signals: {signals}")
            return None

        # Basic Info
        name = self._extract_name(soup)
        title = self._extract_title(soup)
        location = self._extract_location(soup)
        
        # Stats Table Extraction
        stats_data = self._extract_stats_table(soup)
        
        # Skills
        skills = self._extract_skills(soup)
        
        # Portfolio Count
        portfolio_count = self._extract_portfolio_count(soup, portfolio_html)

        return ProfileDetails(
            name=name or "Unknown",
            profile_url=url,
            category="development",
            title=title,
            location=location,
            rating=stats_data.get("rating", 0.0),
            reviews_count=stats_data.get("reviews_count", 0),
            completion_rate=stats_data.get("completion_rate"),
            rehire_rate=stats_data.get("rehire_rate"),
            response_time=stats_data.get("avg_response_time_raw"),
            last_seen=stats_data.get("last_active_raw"),
            member_since=stats_data.get("registration_date_raw"),
            parse_confidence=signals[0] if signals else "no_html",
            skills=skills,
            portfolio_count=portfolio_count,
            stats=stats_data
        )

    def _extract_name(self, soup: BeautifulSoup) -> Optional[str]:
        for sel in ["h1.profile-name bdi", "h1 bdi", "h1.usercard__username bdi"]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return None

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        for sel in ["li.profile-title a", "li.profile-title"]:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                return re.sub(r"^[^\w\u0600-\u06FF]+", "", t).strip()
        return None

    def _extract_location(self, soup: BeautifulSoup) -> Optional[str]:
        el = soup.select_one("li.profile-country")
        return el.get_text(strip=True) if el else None

    def _extract_skills(self, soup: BeautifulSoup) -> List[str]:
        selectors = ["ul.skills li.skills__item a bdi", ".skills__item bdi", ".tag bdi"]
        for sel in selectors:
            found = [el.get_text(strip=True) for el in soup.select(sel) if el.get_text(strip=True)]
            if found: return found
        return []

    def _extract_stats_table(self, soup: BeautifulSoup) -> Dict[str, Any]:
        results = {}
        panel = soup.select_one("#user-stats") or self._find_stats_table_by_fingerprint(soup)
        if not panel:
            return results

        for row in panel.select("table tr"):
            cols = row.find_all("td")
            if len(cols) < 2: continue
            
            label = (cols[0].find("span") or cols[0]).get_text(strip=True)
            value = cols[1].get_text(separator=" ", strip=True)
            
            field_name = self._stat_map_norm.get(self._normalize_arabic(label))
            if field_name:
                results[field_name] = value
        
        # Rating parsing
        rating_el = soup.select_one(".rating-stars")
        if rating_el:
            # Heuristic for rating
            pass
            
        return results

    def _find_stats_table_by_fingerprint(self, soup: BeautifulSoup) -> Optional[Tag]:
        for table in soup.find_all("table"):
            hits = 0
            for row in table.find_all("tr"):
                tds = row.find_all("td")
                if not tds: continue
                if self._normalize_arabic(tds[0].get_text(strip=True)) in self._stat_map_norm:
                    hits += 1
            if hits >= 3: return table
        return None

    def _extract_portfolio_count(self, soup: BeautifulSoup, portfolio_html: Optional[str]) -> int:
        target_soup = BeautifulSoup(portfolio_html, "lxml") if portfolio_html else soup
        items = [div for div in target_soup.find_all("div") 
                 if "postcard" in div.get("class", []) and "cell-container" in div.get("class", [])]
        return len(items)

    def _get_page_confidence(self, html: str, soup: BeautifulSoup) -> Tuple[int, List[str]]:
        signals = []
        if not html:
            return 0, ["no_html"]
        if "id=""captcha-container""" in html or "cloudflare" in html.lower():
            return 0, ["blocked"]
        
        if len(html) >= self.config.min_html_bytes: signals.append("html_size")
        if soup.find("h1"): signals.append("has_h1")
        if soup.find("table", class_=re.compile(r"\btable-meta\b")): signals.append("has_stats_table")
        if soup.find(class_=re.compile(r"\bskills__item\b")): signals.append("has_skills")
        if soup.find(class_=re.compile(r"\bprofile-name\b")) or (soup.find("h1") and soup.find("h1").find("bdi")):
            signals.append("has_profile_name")
        return len(signals), signals
