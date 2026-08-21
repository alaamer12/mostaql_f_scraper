"""Analyzer Sub-module with DuckDB SQL Aggregations."""

from .overview import (
    get_dataset_kpis,
    get_missing_data_by_field,
    get_data_completeness_distribution,
    get_parse_confidence_distribution,
)
from .users import (
    get_user_project_scatter_data,
    get_user_activity_segments,
)
from .projects import (
    get_top_users_by_projects,
    get_project_concentration_percentiles,
    get_pareto_project_activity,
)
from .distributions import (
    get_project_count_ranges,
    get_project_count_histogram,
    get_log_scale_distribution,
    get_cumulative_user_distribution,
    get_project_activity_outliers,
)
from .categories import (
    get_projects_by_category,
    get_users_by_category,
    get_avg_projects_per_user_by_category,
    get_category_concentration_pareto,
    get_user_vs_project_category_comparison,
)
from .relationships import (
    get_most_common_skills,
    get_skills_per_user_distribution,
    get_skills_vs_project_activity,
    get_temporal_project_activity,
    get_user_growth_over_time,
    get_temporal_category_activity,
    get_geographic_user_distribution,
    get_geographic_project_activity,
    get_numeric_correlations,
    get_bivariate_relationship_samples,
)

__all__ = [
    # Overview
    "get_dataset_kpis",
    "get_missing_data_by_field",
    "get_data_completeness_distribution",
    "get_parse_confidence_distribution",
    # Users
    "get_user_project_scatter_data",
    "get_user_activity_segments",
    # Projects
    "get_top_users_by_projects",
    "get_project_concentration_percentiles",
    "get_pareto_project_activity",
    # Distributions
    "get_project_count_ranges",
    "get_project_count_histogram",
    "get_log_scale_distribution",
    "get_cumulative_user_distribution",
    "get_project_activity_outliers",
    # Categories
    "get_projects_by_category",
    "get_users_by_category",
    "get_avg_projects_per_user_by_category",
    "get_category_concentration_pareto",
    "get_user_vs_project_category_comparison",
    # Relationships
    "get_most_common_skills",
    "get_skills_per_user_distribution",
    "get_skills_vs_project_activity",
    "get_temporal_project_activity",
    "get_user_growth_over_time",
    "get_temporal_category_activity",
    "get_geographic_user_distribution",
    "get_geographic_project_activity",
    "get_numeric_correlations",
    "get_bivariate_relationship_samples",
]
