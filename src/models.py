from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from .utils.formatting import TimeFormatter

@dataclass(frozen=True)
class Freelancer:
    """Basic information found during Phase 2 discovery."""
    name: str
    profile_url: str
    avatar_url: Optional[str] = None
    title: Optional[str] = None
    rank: Optional[str] = None

@dataclass(frozen=True)
class ProfileDetails:
    """Detailed information scraped during Phase 3 & 4."""
    name: str
    profile_url: str
    category: str = "development"
    title: str = "مستقل"
    location: str = "غير محدد"
    rating: float = 0.0
    reviews_count: int = 0
    completion_rate: float = 100.0
    ontime_delivery_rate: float = 100.0
    rehire_rate: float = 100.0
    communication_success_rate: float = 100.0
    employment_rate: float = 100.0
    total_completed_projects: float = 0.0
    active_projects: float = 0.0
    received_projects: float = 0.0
    financial_deals: float = 0.0
    response_time: str = "خلال يوم"
    avg_response_time_raw: str = "خلال يوم"
    avg_response_time_minutes: float = 1440.0
    last_seen: str = "منذ يوم"
    last_active: str = "منذ يوم"
    member_since: str = "2021-01-01"
    registration_date: str = "2021-01-01T00:00:00"
    registration_date_str: str = "2021-01-01T00:00:00"
    parse_confidence: str = "ok"
    parse_signals: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    skills_count: float = 0.0
    skills_str: str = ""
    portfolio_count: float = 0.0
    success_score: float = 0.0
    rank: int = 1
    stats: Dict[str, Any] = field(default_factory=dict)
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())

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
    """Global configuration for the scraping pipeline.
    
    Can be overridden via environment variables or a .env file.
    Example: MOSTAQL_MAX_PAGES=10
    """
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
    
    # Discovery uses its own, deliberately gentler per-worker budget
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
