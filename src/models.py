from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
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
    """Detailed information scraped during Phase 3."""
    name: str
    profile_url: str
    category: str
    title: Optional[str] = None
    location: Optional[str] = None
    rating: float = 0.0
    reviews_count: int = 0
    completion_rate: Optional[str] = None
    rehire_rate: Optional[str] = None
    response_time: Optional[str] = None
    last_seen: Optional[str] = None
    member_since: Optional[str] = None
    parse_confidence: Optional[str] = None
    skills: List[str] = field(default_factory=list)
    portfolio_count: int = 0
    stats: Dict[str, Any] = field(default_factory=dict)
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass(frozen=True)
class PageCountItem:
    """Milestone emitted by the discovery stage for one solved combination."""
    label: str
    combo: Dict[str, Any]
    last_page: int

@dataclass(frozen=True)
class RawProfileRecord:
    """Milestone emitted by the fetch stage: raw, unparsed profile HTML."""
    profile_url: str
    html: Optional[str] = None
    portfolio_html: Optional[str] = None

@dataclass
class ScrapeConfig:
    """Global configuration for the scraping pipeline."""
    base_url: str = "https://mostaql.com/freelancers"
    max_pages: int = -1
    profile_concurrency: int = 10
    dir_concurrency: int = 3
    rate_limit_burst: int = 6
    rate_limit_period: float = 2.0
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

    # Checkpoints
    checkpoint_json: str = "checkpoint.json"
    checkpoint_profiles_json: str = "checkpoint_profiles.jsonl"
    checkpoint_fetch_json: str = "checkpoint_fetch.jsonl"
    pagination_cache: str = "pagination_cache.json"
    
    # Phase specific
    binary_search_initial: int = 100
    min_confidence: int = 2

    user_agents: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ])

    def resolve_path(self, attr_name: str) -> str:
        """Resolves a file path attribute with dynamic placeholders."""
        raw_path = getattr(self, attr_name)
        return TimeFormatter.format_path(raw_path)
