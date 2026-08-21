"""Configuration settings for DuckDB Dashboard Analytics."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any


@dataclass
class DashboardConfig:
    """Central configuration for database, limits, styling, and caching."""

    # Base paths
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    collected_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "collected")
    cache_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "collected" / "cache")
    
    # Dataset files
    analysis_json: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "collected" / "analysis.json")
    profiles_json: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "collected" / "profiles.json")

    # DuckDB engine configuration
    memory_limit: str = "2GB"
    threads: int = 4
    enable_parquet_cache: bool = True
    
    # Visualization & Query limits
    scatter_sample_limit: int = 50000
    top_categories_limit: int = 20
    top_users_limit: int = 20
    top_skills_limit: int = 25
    top_locations_limit: int = 20
    histogram_bins_default: int = 40
    
    # Project range bins definitions
    # (min_inclusive, max_inclusive, label)
    project_bins: List[Dict[str, Any]] = field(default_factory=lambda: [
        {"min": 0, "max": 0, "label": "0"},
        {"min": 1, "max": 5, "label": "1-5"},
        {"min": 6, "max": 10, "label": "6-10"},
        {"min": 11, "max": 20, "label": "11-20"},
        {"min": 21, "max": 50, "label": "21-50"},
        {"min": 51, "max": 100, "label": "51-100"},
        {"min": 101, "max": None, "label": "100+"},
    ])
    
    # Activity segment definitions
    activity_segments: List[Dict[str, Any]] = field(default_factory=lambda: [
        {"min": 0, "max": 0, "label": "Inactive (0)"},
        {"min": 1, "max": 5, "label": "Low (1-5)"},
        {"min": 6, "max": 20, "label": "Medium (6-20)"},
        {"min": 21, "max": 100, "label": "High (21-100)"},
        {"min": 101, "max": None, "label": "Very High (100+)"},
    ])

    # Theme & Styling
    theme: Dict[str, str] = field(default_factory=lambda: {
        "bg": "#0f172a",          # Slate 900
        "card_bg": "#1e293b",     # Slate 800
        "card_border": "#334155", # Slate 700
        "text_primary": "#f8fafc",# Slate 50
        "text_secondary": "#94a3b8", # Slate 400
        "accent_primary": "#38bdf8", # Sky 400
        "accent_secondary": "#818cf8", # Indigo 400
        "accent_success": "#34d399", # Emerald 400
        "accent_warning": "#fbbf24", # Amber 400
        "accent_danger": "#f87171",  # Red 400
        "chart_palette": [
            "#38bdf8", "#818cf8", "#34d399", "#fbbf24",
            "#f472b6", "#a78bfa", "#2dd4bf", "#fb923c"
        ],
        "plotly_template": "plotly_dark",
    })

    def ensure_directories(self) -> None:
        """Ensure necessary cache and data directories exist."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)


def get_default_config() -> DashboardConfig:
    """Instantiate and prepare default configuration."""
    cfg = DashboardConfig()
    cfg.ensure_directories()
    return cfg
