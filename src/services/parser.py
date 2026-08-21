import re
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set
from bs4 import BeautifulSoup, Tag

from ..models import Freelancer, ProfileDetails, ProfileStats, ProfileMetadata, FieldMeta, ScrapeConfig, Source
from ..schema.spec import FIELD_SPECS, check_record_coherence
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
        """Multi-Tier parsing: Structural -> DOM-Adjacency -> Token-Inference -> Schema Normalizer."""
        soup = BeautifulSoup(html, "lxml" if "lxml" in BeautifulSoup.__module__ else "html.parser")

        # Check confidence / sanity
        score, signals = self._get_page_confidence(html, soup)
        if score < self.config.min_confidence:
            log.warning(f"Low confidence ({score}/5) for {url}. Signals: {signals}")
            return None

        field_metas: Dict[str, FieldMeta] = {}
        outlier_fields: List[str] = []

        # 1. Identity & Content Extraction
        name_raw = self._extract_name(soup, url)
        avatar_raw = self._extract_avatar(soup)
        title_raw = self._extract_title(soup)
        location_raw = self._extract_location(soup)
        bio_raw = self._extract_bio(soup)
        verifications_raw = self._extract_verifications(soup)
        badges_raw = self._extract_badges(soup)
        rating_raw, reviews_count_raw = self._extract_rating(soup)
        skills_raw = self._extract_skills(soup)
        portfolio_raw = self._extract_portfolio_count(soup, portfolio_html)

        # Parse Identity & Content
        name_out = FIELD_SPECS["name"].type.parse(name_raw)
        field_metas["name"] = FieldMeta(
            source="dom_structural" if soup.select_one("h1.profile-name, h1 bdi") else "default",
            confidence=round(name_out.confidence, 2),
            raw=str(name_raw or ""),
            outlier=bool(name_out.issues),
            issues=name_out.issues,
            type="Text",
            formatted=FIELD_SPECS["name"].type.format(name_out.value),
        )

        avatar_out = FIELD_SPECS["avatar_url"].type.parse(avatar_raw)
        field_metas["avatar_url"] = FieldMeta(
            source="dom_structural" if avatar_raw else "default",
            confidence=round(avatar_out.confidence, 2),
            raw=str(avatar_raw or ""),
            outlier=bool(avatar_out.issues),
            issues=avatar_out.issues,
            type="Text",
            formatted=FIELD_SPECS["avatar_url"].type.format(avatar_out.value),
        )

        title_out = FIELD_SPECS["title"].type.parse(title_raw)
        field_metas["title"] = FieldMeta(
            source="dom_structural" if title_raw != "مستقل" else "default",
            confidence=round(title_out.confidence, 2),
            raw=str(title_raw or ""),
            outlier=bool(title_out.issues),
            issues=title_out.issues,
            type="Text",
            formatted=FIELD_SPECS["title"].type.format(title_out.value),
        )

        loc_out = FIELD_SPECS["location"].type.parse(location_raw)
        field_metas["location"] = FieldMeta(
            source="dom_structural" if location_raw != "غير محدد" else "default",
            confidence=round(loc_out.confidence, 2),
            raw=str(location_raw or ""),
            outlier=bool(loc_out.issues),
            issues=loc_out.issues,
            type="Text",
            formatted=FIELD_SPECS["location"].type.format(loc_out.value),
        )

        bio_out = FIELD_SPECS["bio"].type.parse(bio_raw)
        field_metas["bio"] = FieldMeta(
            source="dom_structural" if bio_raw else "default",
            confidence=round(bio_out.confidence, 2),
            raw=str(bio_raw or ""),
            outlier=bool(bio_out.issues),
            issues=bio_out.issues,
            type="Text",
            formatted=FIELD_SPECS["bio"].type.format(bio_out.value),
        )

        skills_out = FIELD_SPECS["skills"].type.parse(skills_raw)
        field_metas["skills"] = FieldMeta(
            source="dom_structural" if skills_raw else "default",
            confidence=round(skills_out.confidence, 2),
            raw=str(skills_raw or ""),
            outlier=bool(skills_out.issues),
            issues=skills_out.issues,
            type="ListOf(Text)",
            formatted=f"{len(skills_out.value)} skills",
        )

        field_metas["skills_count"] = FieldMeta(
            source="derived",
            confidence=1.0,
            raw=str(len(skills_out.value)),
            outlier=False,
            issues=[],
            type="Count",
            formatted=str(len(skills_out.value)),
        )

        field_metas["skills_str"] = FieldMeta(
            source="derived",
            confidence=1.0,
            raw=", ".join(skills_out.value),
            outlier=False,
            issues=[],
            type="Text",
            formatted=", ".join(skills_out.value),
        )

        verif_out = FIELD_SPECS["verifications"].type.parse(verifications_raw)
        field_metas["verifications"] = FieldMeta(
            source="dom_structural" if verifications_raw else "default",
            confidence=round(verif_out.confidence, 2),
            raw=str(verifications_raw or ""),
            outlier=bool(verif_out.issues),
            issues=verif_out.issues,
            type="ListOf(Text)",
            formatted=", ".join(verif_out.value),
        )

        badges_out = FIELD_SPECS["badges"].type.parse(badges_raw)
        field_metas["badges"] = FieldMeta(
            source="dom_structural" if badges_raw else "default",
            confidence=round(badges_out.confidence, 2),
            raw=str(badges_raw or ""),
            outlier=bool(badges_out.issues),
            issues=badges_out.issues,
            type="ListOf(Text)",
            formatted=", ".join(badges_out.value),
        )

        port_out = FIELD_SPECS["portfolio_count"].type.parse(portfolio_raw)
        field_metas["portfolio_count"] = FieldMeta(
            source="dom_structural" if portfolio_raw > 0 else "default",
            confidence=round(port_out.confidence, 2),
            raw=str(portfolio_raw or ""),
            outlier=("above_soft_max" in port_out.issues or "above_hard_max" in port_out.issues),
            issues=port_out.issues,
            type="Count",
            formatted=str(port_out.value),
        )

        rating_out = FIELD_SPECS["rating"].type.parse(rating_raw)
        field_metas["rating"] = FieldMeta(
            source="dom_structural" if rating_raw > 0.0 else "default",
            confidence=round(rating_out.confidence, 2),
            raw=str(rating_raw or ""),
            outlier=("above_max" in rating_out.issues or "below_min" in rating_out.issues),
            issues=rating_out.issues,
            type="Rating",
            formatted=FIELD_SPECS["rating"].type.format(rating_out.value),
        )

        rev_out = FIELD_SPECS["reviews_count"].type.parse(reviews_count_raw)
        field_metas["reviews_count"] = FieldMeta(
            source="dom_structural" if reviews_count_raw > 0 else "default",
            confidence=round(rev_out.confidence, 2),
            raw=str(reviews_count_raw or ""),
            outlier=("above_soft_max" in rev_out.issues or "above_hard_max" in rev_out.issues),
            issues=rev_out.issues,
            type="Count",
            formatted=str(rev_out.value),
        )

        # 2. Multi-Tier Stats Extraction
        stats_raw, provenance_map = self._extract_stats_multi_tier(soup)

        # 3. Stats Parsing & Normalization
        comp_proj_raw = stats_raw.get("total_completed_projects")
        comp_proj_out = FIELD_SPECS["total_completed_projects"].type.parse(comp_proj_raw)
        total_completed = float(comp_proj_out.value)
        
        if is_placeholder(comp_proj_raw):
            comp_proj_src = "derived"
        else:
            comp_proj_src = provenance_map.get("total_completed_projects", "derived" if total_completed == 0 else "default")

        field_metas["total_completed_projects"] = FieldMeta(
            source=comp_proj_src,
            confidence=round(comp_proj_out.confidence, 2),
            raw=str(comp_proj_raw or ""),
            outlier=("above_soft_max" in comp_proj_out.issues or "above_hard_max" in comp_proj_out.issues),
            issues=comp_proj_out.issues,
            type="Count",
            formatted=str(comp_proj_out.value),
        )

        active_proj_raw = stats_raw.get("active_projects")
        active_proj_out = FIELD_SPECS["active_projects"].type.parse(active_proj_raw)
        active_proj = float(active_proj_out.value)

        if is_placeholder(active_proj_raw):
            active_proj_src = "derived"
        else:
            active_proj_src = provenance_map.get("active_projects", "derived" if active_proj == 0 else "default")

        field_metas["active_projects"] = FieldMeta(
            source=active_proj_src,
            confidence=round(active_proj_out.confidence, 2),
            raw=str(active_proj_raw or ""),
            outlier=("above_soft_max" in active_proj_out.issues or "above_hard_max" in active_proj_out.issues),
            issues=active_proj_out.issues,
            type="Count",
            formatted=str(active_proj_out.value),
        )

        # Rates Parsing
        rate_keys = [
            ("completion_rate", "completion_rate"),
            ("ontime_delivery_rate", "ontime_delivery_rate"),
            ("rehire_rate", "rehire_rate"),
            ("communication_success_rate", "communication_success_rate"),
        ]
        rates_parsed: Dict[str, float] = {}

        for stat_key, spec_key in rate_keys:
            raw_val = stats_raw.get(stat_key)
            if raw_val is not None and is_placeholder(raw_val):
                out = FIELD_SPECS[spec_key].type.parse("0.0")
                src = "derived"
            elif total_completed > 0:
                out = FIELD_SPECS[spec_key].type.parse(raw_val if raw_val is not None else "100.0")
                src = provenance_map.get(stat_key, "dom_structural" if raw_val else "derived")
            else:
                out = FIELD_SPECS[spec_key].type.parse(raw_val if raw_val is not None else "0.0")
                src = provenance_map.get(stat_key, "derived")

            rates_parsed[stat_key] = float(out.value)
            field_metas[stat_key] = FieldMeta(
                source=src,
                confidence=round(out.confidence, 2),
                raw=str(raw_val or ""),
                outlier=("above_max" in out.issues or "below_min" in out.issues),
                issues=out.issues,
                type="Percentage",
                formatted=FIELD_SPECS[spec_key].type.format(out.value),
            )

        # Employment rate
        emp_raw = stats_raw.get("employment_rate")
        if emp_raw is not None and is_placeholder(emp_raw):
            emp_out = FIELD_SPECS["employment_rate"].type.parse("0.0")
            emp_src = "derived"
        elif emp_raw is not None and not is_placeholder(emp_raw):
            emp_out = FIELD_SPECS["employment_rate"].type.parse(emp_raw)
            emp_src = provenance_map.get("employment_rate", "dom_structural")
        else:
            if total_completed > 0:
                emp_val = min(100.0, round((rates_parsed["completion_rate"] + rates_parsed["rehire_rate"]) / 2.0, 2))
                emp_out = FIELD_SPECS["employment_rate"].type.parse(str(emp_val))
                emp_src = "derived"
            else:
                emp_out = FIELD_SPECS["employment_rate"].type.parse("0.0")
                emp_src = "derived"

        rates_parsed["employment_rate"] = float(emp_out.value)
        field_metas["employment_rate"] = FieldMeta(
            source=emp_src,
            confidence=round(emp_out.confidence, 2),
            raw=str(emp_raw or ""),
            outlier=("above_max" in emp_out.issues or "below_min" in emp_out.issues),
            issues=emp_out.issues,
            type="Percentage",
            formatted=FIELD_SPECS["employment_rate"].type.format(emp_out.value),
        )

        # Received projects
        recv_raw = stats_raw.get("received_projects")
        if recv_raw and not is_placeholder(recv_raw):
            recv_out = FIELD_SPECS["received_projects"].type.parse(recv_raw)
            recv_src = provenance_map.get("received_projects", "dom_structural")
        else:
            recv_val = total_completed + active_proj
            recv_out = FIELD_SPECS["received_projects"].type.parse(str(recv_val))
            recv_src = "derived"

        field_metas["received_projects"] = FieldMeta(
            source=recv_src,
            confidence=round(recv_out.confidence, 2),
            raw=str(recv_raw or ""),
            outlier=("above_soft_max" in recv_out.issues or "above_hard_max" in recv_out.issues),
            issues=recv_out.issues,
            type="Count",
            formatted=str(recv_out.value),
        )
        received_projects_val = float(recv_out.value)

        # Financial deals
        deals_raw = stats_raw.get("financial_deals")
        if deals_raw and not is_placeholder(deals_raw):
            deals_out = FIELD_SPECS["financial_deals"].type.parse(deals_raw)
            deals_src = provenance_map.get("financial_deals", "dom_structural")
        else:
            deals_out = FIELD_SPECS["financial_deals"].type.parse(str(total_completed))
            deals_src = "derived"

        field_metas["financial_deals"] = FieldMeta(
            source=deals_src,
            confidence=round(deals_out.confidence, 2),
            raw=str(deals_raw or ""),
            outlier=("above_soft_max" in deals_out.issues or "above_hard_max" in deals_out.issues),
            issues=deals_out.issues,
            type="Count",
            formatted=str(deals_out.value),
        )
        financial_deals_val = float(deals_out.value)

        # Response Time
        resp_raw = stats_raw.get("avg_response_time_raw") or "غير محدد"
        resp_out = FIELD_SPECS["avg_response_time_raw"].type.parse(resp_raw)
        field_metas["avg_response_time_raw"] = FieldMeta(
            source=provenance_map.get("avg_response_time_raw", "default"),
            confidence=round(resp_out.confidence, 2),
            raw=str(resp_raw or ""),
            outlier=bool(resp_out.issues and "placeholder" not in resp_out.issues),
            issues=resp_out.issues,
            type="RelativeTime",
            formatted=resp_out.value,
        )

        resp_mins_out = FIELD_SPECS["avg_response_time_minutes"].type.parse(resp_raw)
        field_metas["avg_response_time_minutes"] = FieldMeta(
            source="derived",
            confidence=round(resp_mins_out.confidence, 2),
            raw=str(resp_raw or ""),
            outlier=("above_max" in resp_mins_out.issues or "below_min" in resp_mins_out.issues),
            issues=resp_mins_out.issues,
            type="Duration",
            formatted=f"{resp_mins_out.value} mins",
        )
        avg_resp_mins = float(resp_mins_out.value)

        # Registration Date
        reg_raw = stats_raw.get("registration_date_raw") or "2021-01-01"
        reg_out = FIELD_SPECS["registration_date"].type.parse(reg_raw)
        field_metas["registration_date"] = FieldMeta(
            source=provenance_map.get("registration_date_raw", "default"),
            confidence=round(reg_out.confidence, 2),
            raw=str(reg_raw or ""),
            outlier=bool(reg_out.issues and "placeholder" not in reg_out.issues),
            issues=reg_out.issues,
            type="ArabicDate",
            formatted=reg_out.value,
        )
        reg_iso = reg_out.value

        field_metas["registration_date_str"] = FieldMeta(
            source="derived",
            confidence=1.0,
            raw=reg_iso,
            outlier=False,
            issues=[],
            type="Text",
            formatted=reg_iso,
        )

        # Activity
        last_act_raw = stats_raw.get("last_active_raw") or "منذ يوم"
        last_act_out = FIELD_SPECS["last_active"].type.parse(last_act_raw)
        field_metas["last_active"] = FieldMeta(
            source=provenance_map.get("last_active_raw", "default"),
            confidence=round(last_act_out.confidence, 2),
            raw=str(last_act_raw or ""),
            outlier=bool(last_act_out.issues and "placeholder" not in last_act_out.issues),
            issues=last_act_out.issues,
            type="RelativeTime",
            formatted=last_act_out.value,
        )

        field_metas["last_seen"] = FieldMeta(
            source=provenance_map.get("last_active_raw", "default"),
            confidence=round(last_act_out.confidence, 2),
            raw=str(last_act_raw or ""),
            outlier=bool(last_act_out.issues and "placeholder" not in last_act_out.issues),
            issues=last_act_out.issues,
            type="RelativeTime",
            formatted=last_act_out.value,
        )

        field_metas["member_since"] = FieldMeta(
            source=provenance_map.get("registration_date_raw", "default"),
            confidence=round(reg_out.confidence, 2),
            raw=str(reg_raw or ""),
            outlier=False,
            issues=[],
            type="Text",
            formatted=str(reg_raw),
        )

        # 4. Check Coherence & Outliers
        stats_dict = {
            "total_completed_projects": total_completed,
            "active_projects": active_proj,
            "received_projects": received_projects_val,
            "financial_deals": financial_deals_val,
            "completion_rate": rates_parsed["completion_rate"],
            "ontime_delivery_rate": rates_parsed["ontime_delivery_rate"],
            "rehire_rate": rates_parsed["rehire_rate"],
            "communication_success_rate": rates_parsed["communication_success_rate"],
            "employment_rate": rates_parsed["employment_rate"],
            "rating": float(rating_out.value),
            "reviews_count": int(rev_out.value),
            "response_time": resp_out.value,
            "avg_response_time_raw": resp_out.value,
            "avg_response_time_minutes": avg_resp_mins,
            "last_seen": last_act_out.value,
            "last_active": last_act_out.value,
            "member_since": str(reg_raw),
            "registration_date": reg_iso,
            "registration_date_str": reg_iso,
        }

        coherence_issues = check_record_coherence(stats_dict)
        for issue in coherence_issues:
            if "completed" in issue or "received" in issue:
                field_metas["received_projects"] = field_metas["received_projects"].model_copy(
                    update={"issues": field_metas["received_projects"].issues + [issue], "outlier": True}
                )
            elif "rates" in issue:
                for rk in ["completion_rate", "ontime_delivery_rate", "rehire_rate", "communication_success_rate"]:
                    field_metas[rk] = field_metas[rk].model_copy(
                        update={"issues": field_metas[rk].issues + [issue], "outlier": True}
                    )
            elif "reviews" in issue:
                field_metas["rating"] = field_metas["rating"].model_copy(
                    update={"issues": field_metas["rating"].issues + [issue], "outlier": True}
                )

        # Collect outlier fields
        for fname, fmeta in field_metas.items():
            if fmeta.outlier or any(iss in fmeta.issues for iss in ["above_soft_max", "above_hard_max", "above_max", "below_min"]):
                outlier_fields.append(fname)

        if outlier_fields:
            log.warning(f"Outlier detected for {url} on fields: {outlier_fields}")

        # Quality determination
        all_issues = [iss for fm in field_metas.values() for iss in fm.issues]
        if any(iss in all_issues for iss in ["above_hard_max", "below_min", "internal_error"]):
            quality: Literal["ok", "suspect", "bad", "quarantine"] = "bad"
        elif any(iss in all_issues for iss in ["above_soft_max", "above_max", "incoherent_received_less_than_completed", "incoherent_rates_with_zero_projects", "incoherent_rating_with_zero_reviews"]):
            quality = "suspect"
        else:
            quality = "ok"

        metadata = ProfileMetadata(
            quality=quality,
            schema_version="2.0",
            parse_signals=signals,
            outlier_fields=list(set(outlier_fields)),
            fields=field_metas,
        )

        profile_stats = ProfileStats(
            rating=float(rating_out.value),
            reviews_count=int(rev_out.value),
            completion_rate=rates_parsed["completion_rate"],
            ontime_delivery_rate=rates_parsed["ontime_delivery_rate"],
            rehire_rate=rates_parsed["rehire_rate"],
            communication_success_rate=rates_parsed["communication_success_rate"],
            employment_rate=rates_parsed["employment_rate"],
            total_completed_projects=total_completed,
            active_projects=active_proj,
            received_projects=received_projects_val,
            financial_deals=financial_deals_val,
            response_time=resp_out.value,
            avg_response_time_raw=resp_out.value,
            avg_response_time_minutes=avg_resp_mins,
            last_seen=last_act_out.value,
            last_active=last_act_out.value,
            member_since=str(reg_raw),
            registration_date=reg_iso,
            registration_date_str=reg_iso,
        )

        return ProfileDetails(
            name=name_out.value,
            profile_url=url,
            avatar_url=avatar_out.value,
            category="development",
            title=title_out.value,
            location=loc_out.value,
            bio=bio_out.value,
            skills=skills_out.value,
            skills_count=float(len(skills_out.value)),
            skills_str=", ".join(skills_out.value),
            portfolio_count=float(port_out.value),
            verifications=verif_out.value,
            badges=badges_out.value,
            stats=profile_stats,
            metadata=metadata,
            rank=1,
            scraped_at=datetime.now().isoformat(),
        )

    def _extract_avatar(self, soup: BeautifulSoup) -> str:
        """Extract profile avatar / picture URL with fallback for missing/default avatar."""
        selectors = [
            ".profile-card--avatar img",
            "img.profile-avatar",
            "img.uavatar",
            ".user-avatar img",
            ".profile-header img",
            "img[class*='avatar']",
        ]
        for sel in selectors:
            for img in soup.select(sel):
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
                src = src.strip()
                if not src:
                    continue
                # Ignore badge SVGs or other icons if matched accidentally
                if not src.endswith(".svg") and "badge" not in src:
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://mostaql.com" + src
                    return src
                elif any(k in src for k in ["avatars.hsoubcdn.com", "user-avatar", "avatar"]):
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://mostaql.com" + src
                    return src

        # General fallback for any img with hsoub avatar cdn
        for img in soup.find_all("img"):
            src = (img.get("src") or img.get("data-src") or "").strip()
            if "avatars.hsoubcdn.com" in src:
                if src.startswith("//"):
                    src = "https:" + src
                return src

        return ""

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

    def _extract_bio(self, soup: BeautifulSoup) -> str:
        """Extract profile biography / about section with paragraph structure preserved."""
        def format_bio_element(el) -> str:
            if not el:
                return ""
            paragraphs = []
            p_tags = el.find_all(["p", "li"])
            if p_tags:
                for p in p_tags:
                    for br in p.find_all(["br", "hr"]):
                        br.replace_with("\n")
                    p_text = p.get_text().strip()
                    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in p_text.split("\n")]
                    p_clean = "\n".join(l for l in lines if l)
                    if p_clean:
                        paragraphs.append(p_clean)
                if paragraphs:
                    return "\n\n".join(paragraphs)

            for br in el.find_all(["br", "hr"]):
                br.replace_with("\n")
            text = el.get_text().strip()
            lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
            text = "\n".join(lines)
            return re.sub(r"\n{3,}", "\n\n", text).strip()

        # Check specific bio containers
        selectors = [
            "#about_content",
            ".profile-about",
            ".user-bio",
            ".profile-bio",
            ".carda__content",
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                txt = format_bio_element(el)
                if txt:
                    return txt

        # Check section headed by 'نبذة عني' or 'عني' or 'About'
        for h in soup.find_all(["h2", "h3", "h4", "h5"]):
            header_txt = h.get_text(strip=True)
            if any(k in header_txt for k in ["نبذة عني", "عني", "نبذة"]):
                parent_card = h.find_parent("div", class_=lambda c: c and any(x in c for x in ["card", "panel", "widget"]))
                if parent_card:
                    body = parent_card.select_one(".carda__content, .card__body, .panel-body, .widget__content")
                    if body:
                        txt = format_bio_element(body)
                        if txt:
                            return txt
                
                # Check next sibling elements
                curr = h.find_next_sibling()
                bio_parts = []
                while curr and curr.name not in ["h1", "h2", "h3", "h4", "h5"]:
                    t = format_bio_element(curr)
                    if t:
                        bio_parts.append(t)
                    curr = curr.find_next_sibling()
                if bio_parts:
                    return "\n\n".join(bio_parts).strip()

        return ""

    def _extract_verifications(self, soup: BeautifulSoup) -> List[str]:
        """Extract verified items (e.g. email, phone, identity)."""
        verifications = []
        for h in soup.find_all(["h2", "h3", "h4", "h5"]):
            if "توثيق" in h.get_text():
                container = h.find_parent("div", class_=lambda c: c and any(x in c for x in ["card", "panel", "widget"]))
                if not container:
                    container = h.parent.parent if h.parent else None
                if container:
                    # Look only at leaf table cells or list items
                    for td in container.find_all(["td", "li"]):
                        # If it has a checkmark or success class
                        if td.find("i", class_=lambda c: c and any(x in str(c) for x in ["fa-check", "text-success", "verified"])):
                            t = td.get_text(strip=True)
                            if t and t not in verifications and "توثيق" not in t:
                                verifications.append(t)
                break
        return verifications

    def _extract_badges(self, soup: BeautifulSoup) -> List[str]:
        """Extract profile badges / achievements."""
        badges = []
        # Badges list container
        badges_container = soup.select_one("ul.badges, .badges-list, .user-badges")
        if badges_container:
            for img in badges_container.find_all("img"):
                alt = img.get("alt") or img.get("title")
                if alt and alt.strip() and alt.strip() not in badges:
                    badges.append(alt.strip())
            for li in badges_container.find_all("li"):
                txt = li.get_text(strip=True)
                if txt and txt not in badges:
                    badges.append(txt)

        if not badges:
            for h in soup.find_all(["h2", "h3", "h4", "h5"]):
                if "أوسمة" in h.get_text() or "اوسمة" in h.get_text():
                    container = h.find_parent("div", class_=lambda c: c and any(x in c for x in ["card", "panel", "widget"]))
                    if not container:
                        container = h.parent.parent if h.parent else None
                    if container:
                        for img in container.find_all("img"):
                            alt = img.get("alt") or img.get("title")
                            if alt and alt.strip() and alt.strip() not in badges:
                                badges.append(alt.strip())
                    break
        return badges

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

    def _extract_stats_multi_tier(self, soup: BeautifulSoup) -> Tuple[Dict[str, Any], Dict[str, Source]]:
        """Combine Structural (Tier 1), Label Adjacency (Tier 2), and Token Inference (Tier 3) with provenance tags."""
        provenance: Dict[str, Source] = {}

        # Tier 1: Structural Panel Extract
        structural_results = structural_profile_extract(soup)

        # Tier 2: Label-Driven DOM Adjacency Scanning
        label_results, _ = label_driven_extract(soup)

        # Track sources: Label driven first, then overwrite with structural
        merged = {}
        for k, v in label_results.items():
            merged[k] = v
            provenance[k] = "dom_label"

        for k, v in structural_results.items():
            merged[k] = v
            provenance[k] = "dom_structural"

        # Check if key stats are missing; if so, run Tier 3 Inference Engine ONLY for un-extracted fields (not placeholders)
        has_uncalculated_rates = any(
            is_placeholder(merged.get(r))
            for r in ["completion_rate", "ontime_delivery_rate", "rehire_rate", "employment_rate"]
            if r in merged
        )
        if has_uncalculated_rates:
            if "total_completed_projects" not in merged:
                merged["total_completed_projects"] = "0"
                provenance["total_completed_projects"] = "derived"
            if "active_projects" not in merged:
                merged["active_projects"] = "0"
                provenance["active_projects"] = "derived"

        needed_fields = [
            "completion_rate", "ontime_delivery_rate", "rehire_rate",
            "communication_success_rate", "total_completed_projects",
            "active_projects", "avg_response_time_raw", "registration_date_raw",
            "last_active_raw",
        ]
        missing = [f for f in needed_fields if f not in merged]
        if missing:
            inference_results = infer_fields(soup, target_fields=missing)
            for f in missing:
                if f in inference_results and inference_results[f].get("value"):
                    val = inference_results[f]["value"]
                    if not is_placeholder(val) and inference_results[f].get("confidence", 0) >= 0.20:
                        merged[f] = val
                        provenance[f] = "inferred"

        return merged, provenance

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
