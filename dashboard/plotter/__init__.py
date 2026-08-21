"""Plotter Sub-module for Single-Column Chart Components."""

from .helpers import (
    ChartCard,
    apply_standard_layout,
    create_empty_figure,
)
from .overview import (
    plot_dataset_kpis,
    plot_missing_data_by_field,
    plot_data_completeness_distribution,
    plot_parse_confidence_distribution,
)
from .distributions import (
    plot_user_project_scatter,
    plot_project_count_ranges,
    plot_project_count_histogram,
    plot_cumulative_user_distribution,
    plot_user_activity_segments,
    plot_log_scale_distribution,
    plot_top_users_by_projects,
    plot_project_concentration_percentiles,
    plot_pareto_project_activity,
    plot_project_activity_outliers,
)
from .categories import (
    plot_projects_by_category,
    plot_users_by_category,
    plot_avg_projects_per_user_by_category,
    plot_category_concentration_pareto,
    plot_user_vs_project_category_comparison,
)
from .relationships import (
    plot_most_common_skills,
    plot_skills_per_user_distribution,
    plot_skills_vs_project_activity,
    plot_temporal_project_activity,
    plot_user_growth_over_time,
    plot_temporal_category_activity,
    plot_geographic_user_distribution,
    plot_geographic_project_activity,
    plot_numeric_correlations,
    plot_bivariate_relationship_samples,
)

__all__ = [
    "ChartCard",
    "apply_standard_layout",
    "create_empty_figure",
    # Overview
    "plot_dataset_kpis",
    "plot_missing_data_by_field",
    "plot_data_completeness_distribution",
    "plot_parse_confidence_distribution",
    # Distributions & Users
    "plot_user_project_scatter",
    "plot_project_count_ranges",
    "plot_project_count_histogram",
    "plot_cumulative_user_distribution",
    "plot_user_activity_segments",
    "plot_log_scale_distribution",
    "plot_top_users_by_projects",
    "plot_project_concentration_percentiles",
    "plot_pareto_project_activity",
    "plot_project_activity_outliers",
    # Categories
    "plot_projects_by_category",
    "plot_users_by_category",
    "plot_avg_projects_per_user_by_category",
    "plot_category_concentration_pareto",
    "plot_user_vs_project_category_comparison",
    # Relationships
    "plot_most_common_skills",
    "plot_skills_per_user_distribution",
    "plot_skills_vs_project_activity",
    "plot_temporal_project_activity",
    "plot_user_growth_over_time",
    "plot_temporal_category_activity",
    "plot_geographic_user_distribution",
    "plot_geographic_project_activity",
    "plot_numeric_correlations",
    "plot_bivariate_relationship_samples",
]
