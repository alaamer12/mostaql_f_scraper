"""Dashboard Analytics Package.

High-performance DuckDB-powered analytics and visualization sub-module for large JSON datasets.
"""

from .config import DashboardConfig, get_default_config

__all__ = ["DashboardConfig", "get_default_config"]
