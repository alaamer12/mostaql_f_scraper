from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict
from .utils.formatting import TimeFormatter

# -------------------------------------------------------------------------
     # 1. "dom_structural" (Tier 1 Extraction)
     # -------------------------------------------------------------------------
     # HOW IT WORKS:
     #   The parser targets deterministic, fixed HTML structures such as specific
     #   CSS classes, data attributes, or container elements (e.g., header titles,
     #   `.profile-meta`, rating star elements, user rank spans).
     # CONFIDENCE:
     #   Very High (0.95 - 1.0)
     # EXAMPLE:
     #   Extracting user `name` from `<h1 class="profile-header__name">`
     #   Extracting `rating` from `.rating-stars [data-value="4.5"]`
     # -------------------------------------------------------------------------
     # 2. "dom_label" (Tier 2 Extraction)
     # -------------------------------------------------------------------------
     # HOW IT WORKS:
     #   The parser traverses label-value pairs (like sidebar profile cards, table rows,
     #   or definition lists) and matches known Arabic/English label strings
     #   (e.g., "تاريخ التسجيل", "معدل إكمال المشاريع", "آخر تواجد").
     # CONFIDENCE:
     #   High (0.85 - 0.95)
     # EXAMPLE:
     #   Finding label text "تاريخ التسجيل" and parsing the adjacent cell "27 ديسمبر 2023"
     # -------------------------------------------------------------------------
     # 3. "derived"
     # -------------------------------------------------------------------------
     # HOW IT WORKS:
     #   The value was not directly read from a single DOM node as-is, but was
     #   algorithmically computed or converted from other validated fields.
     # CONFIDENCE:
     #   High (0.90 - 1.0)
     # EXAMPLE:
     #   `avg_response_time_minutes` = 1440.0 derived from parsing `avg_response_time_raw` ("خلال يوم")
     #   `received_projects` = `total_completed_projects` + `active_projects`
     #   `skills_count` = `len(skills)`
     # -------------------------------------------------------------------------
     # 4. "inferred" (Tier 3 Heuristic Extraction)
     # -------------------------------------------------------------------------
     # HOW IT WORKS:
     #   When both structural and label-based extraction fail to find a field, the
     #   inference engine scans candidate tokens from remaining page content
     #   (strictly avoiding user bio/content blocks to prevent false matches).
     # CONFIDENCE:
     #   Moderate / Low (0.40 - 0.60)
     # EXAMPLE:
     #   Detecting an isolated percentage pattern in an unlabelled stats section.
     # -------------------------------------------------------------------------
     # 5. "default"
     # -------------------------------------------------------------------------
     # HOW IT WORKS:
     #   The field was explicitly not calculated on Mostaql (e.g., showing placeholder
     #   text "لم يحسب بعد"), missing entirely from the DOM, or unparsable.
     #   The parser safely assigns the field spec's baseline default value.
     # CONFIDENCE:
     #   0.0 (Uncertain / Uncalculated)
     # EXAMPLE:
     #   `completion_rate` = 0.0 when Mostaql displays "لم يحسب بعد"
     #   `total_completed_projects` = 0 when the user has not completed any projects
Source = Literal["dom_structural", "dom_label", "derived", "inferred", "default"]


class FieldMeta(BaseModel):
    """Metadata detailing provenance, confidence, and quality issues for a single field."""
    model_config = ConfigDict(frozen=True)

    source: Source = "default"
    confidence: float = 0.0        # 0.0 .. 1.0
    raw: str = ""                  # original HTML text
    outlier: bool = False
    issues: List[str] = Field(default_factory=list)
    type: str = ""                 # e.g. "Percentage", "Count"
    formatted: str = ""            # display form


class ProfileMetadata(BaseModel):
    """Overall quality and per-field diagnostic metadata block."""
    model_config = ConfigDict(frozen=True)

    quality: Literal["ok", "suspect", "bad", "quarantine"] = "ok"
    schema_version: str = "2.0"
    parse_signals: List[str] = Field(default_factory=list)
    outlier_fields: List[str] = Field(default_factory=list)
    fields: Dict[str, FieldMeta] = Field(default_factory=dict)


class ProfileStats(BaseModel):
    """All numeric, temporal, and metrics data attributes."""
    model_config = ConfigDict(frozen=True)

    rating: float = 0.0
    reviews_count: int = 0
    completion_rate: float = 0.0
    ontime_delivery_rate: float = 0.0
    rehire_rate: float = 0.0
    communication_success_rate: float = 0.0
    employment_rate: float = 0.0
    total_completed_projects: float = 0.0
    active_projects: float = 0.0
    received_projects: float = 0.0
    financial_deals: float = 0.0
    response_time: str = "غير محدد"
    avg_response_time_raw: str = "غير محدد"
    avg_response_time_minutes: float = 1440.0
    last_seen: str = "منذ يوم"
    last_active: str = "منذ يوم"
    member_since: str = "2021-01-01"
    registration_date: str = "2021-01-01T00:00:00"
    registration_date_str: str = "2021-01-01T00:00:00"


class ProfileDetails(BaseModel):
    """Structured freelancer profile with slim top level, nested stats and metadata."""
    model_config = ConfigDict(frozen=True)

    name: str
    profile_url: str
    avatar_url: str = ""
    category: str = "development"
    title: str = "مستقل"
    location: str = "غير محدد"
    bio: str = ""
    skills: List[str] = Field(default_factory=list)
    skills_count: float = 0.0
    skills_str: str = ""
    portfolio_count: float = 0.0
    verifications: List[str] = Field(default_factory=list)
    badges: List[str] = Field(default_factory=list)
    stats: ProfileStats = Field(default_factory=ProfileStats)
    metadata: ProfileMetadata = Field(default_factory=ProfileMetadata)
    rank: int = 1
    scraped_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # --- Backward compatibility accessors ---
    @property
    def rating(self) -> float:
        return self.stats.rating

    @property
    def reviews_count(self) -> int:
        return self.stats.reviews_count

    @property
    def completion_rate(self) -> float:
        return self.stats.completion_rate

    @property
    def ontime_delivery_rate(self) -> float:
        return self.stats.ontime_delivery_rate

    @property
    def rehire_rate(self) -> float:
        return self.stats.rehire_rate

    @property
    def communication_success_rate(self) -> float:
        return self.stats.communication_success_rate

    @property
    def employment_rate(self) -> float:
        return self.stats.employment_rate

    @property
    def total_completed_projects(self) -> float:
        return self.stats.total_completed_projects

    @property
    def active_projects(self) -> float:
        return self.stats.active_projects

    @property
    def received_projects(self) -> float:
        return self.stats.received_projects

    @property
    def financial_deals(self) -> float:
        return self.stats.financial_deals

    @property
    def response_time(self) -> str:
        return self.stats.response_time

    @property
    def avg_response_time_raw(self) -> str:
        return self.stats.avg_response_time_raw

    @property
    def avg_response_time_minutes(self) -> float:
        return self.stats.avg_response_time_minutes

    @property
    def last_seen(self) -> str:
        return self.stats.last_seen

    @property
    def last_active(self) -> str:
        return self.stats.last_active

    @property
    def member_since(self) -> str:
        return self.stats.member_since

    @property
    def registration_date(self) -> str:
        return self.stats.registration_date

    @property
    def registration_date_str(self) -> str:
        return self.stats.registration_date_str

    @property
    def parse_confidence(self) -> str:
        return self.metadata.quality

    @property
    def parse_signals(self) -> List[str]:
        return self.metadata.parse_signals

    @property
    def success_score(self) -> float:
        return self.completion_rate

    def to_dict(self) -> Dict[str, Any]:
        """Return dict representation using Pydantic model_dump."""
        return self.model_dump()

    def to_flat_dict(self) -> Dict[str, Any]:
        """Return flattened dictionary representation for flat exports."""
        d = self.model_dump()
        stats_d = d.pop("stats", {})
        meta_d = d.pop("metadata", {})
        
        flat = {**d, **stats_d}
        flat["parse_confidence"] = meta_d.get("quality", "ok")
        flat["parse_signals"] = meta_d.get("parse_signals", [])
        flat["outlier_fields"] = meta_d.get("outlier_fields", [])
        return flat


@dataclass(frozen=True)
class Freelancer:
    """Basic information found during Phase 2 discovery."""
    name: str
    profile_url: str
    avatar_url: Optional[str] = None
    title: Optional[str] = None
    rank: Optional[str] = None


@dataclass(frozen=True)
class PageCountItem:
    """Milestone emitted by the discovery stage for one solved combination."""
    label: str
    combo: Dict[str, Any]
    last_page: int


@dataclass(frozen=True)
class KeywordItem:
    """Milestone emitted by the followup stage for one search keyword."""
    keyword: str
    combo: Dict[str, Any]


@dataclass(frozen=True)
class RawProfileRecord:
    """Milestone emitted by the fetch stage: raw, unparsed profile HTML."""
    profile_url: str
    html: Optional[str] = None
    portfolio_html: Optional[str] = None


class ScrapeConfig(BaseSettings):
    """Global configuration for the scraping pipeline."""
    model_config = SettingsConfigDict(
        env_prefix="MOSTAQL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    base_url: str = "https://mostaql.com/freelancers"
    max_pages: int = -1
    profile_concurrency: int = 10
    dir_concurrency: int = 3
    rate_limit_burst: int = 6
    rate_limit_period: float = 2.0
    
    discovery_rate_burst: int = 2
    discovery_rate_period: float = 2.5
    max_retries: int = 6
    retry_wait_min: int = 2
    retry_wait_max: int = 90
    timeout: int = 20
    min_html_bytes: int = 20_000
    
    # File paths
    output_json: str = "mostaql_development_all_users.json"
    output_csv: str = "mostaql_development_all_users.csv"
    profiles_json: str = "mostaql_development_profiles.json"
    profiles_csv: str = "mostaql_development_profiles.csv"
    raw_html_json: str = "mostaql_raw_html_cache.jsonl"
    followup_input: str = "mostaql_development_all_users.json"
    followup_output_json: str = "mostaql_followup_users.json"
    followup_output_csv: str = "mostaql_followup_users.csv"

    # Checkpoints
    checkpoint_flush_every: int = 10
    checkpoint_json: str = "checkpoint.json"
    checkpoint_profiles_json: str = "checkpoint_profiles.jsonl"
    checkpoint_fetch_json: str = "checkpoint_fetch.jsonl"
    pagination_cache: str = "pagination_cache.json"
    
    # Phase specific
    binary_search_initial: int = 100
    min_confidence: int = 2

    user_agents: List[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ]

    def resolve_path(self, attr_name: str) -> str:
        """Resolves a file path attribute with dynamic placeholders."""
        raw_path = getattr(self, attr_name)
        return TimeFormatter.format_path(raw_path)
