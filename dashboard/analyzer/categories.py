"""Category distribution, user vs. project activity ratios, and concentration analyzers."""

from typing import Any, Dict, List, Optional
import pandas as pd
from ..db.connection import DashboardDatabase
from ..db.schema import FieldCapabilityChecker, SchemaInspector
from ..config import get_default_config


def get_projects_by_category(
    db: DashboardDatabase,
    table_name: str,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """Aggregate total completed projects across top categories with residual grouped as 'Other'."""
    config = db.config or get_default_config()
    n = top_n or config.top_categories_limit
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_category():
        return pd.DataFrame(columns=["category", "project_count", "percentage"])

    proj_expr = cap.get_clean_projects_expr()

    sql = f"""
        WITH cat_projects AS (
            SELECT 
                COALESCE(NULLIF(TRIM(category), ''), 'Unspecified') AS category,
                SUM({proj_expr}) AS project_count,
                ROW_NUMBER() OVER (ORDER BY SUM({proj_expr}) DESC) AS rank
            FROM {table_name}
            GROUP BY COALESCE(NULLIF(TRIM(category), ''), 'Unspecified')
        ),
        total AS (
            SELECT SUM(project_count) AS total_projects FROM cat_projects
        ),
        grouped AS (
            SELECT 
                CASE WHEN rank <= {n} THEN category ELSE 'Other' END AS category_group,
                CASE WHEN rank <= {n} THEN rank ELSE {n + 1} END AS sort_rank,
                SUM(project_count) AS project_count
            FROM cat_projects
            GROUP BY 
                CASE WHEN rank <= {n} THEN category ELSE 'Other' END,
                CASE WHEN rank <= {n} THEN rank ELSE {n + 1} END
        )
        SELECT 
            category_group AS category,
            project_count,
            ROUND(project_count * 100.0 / NULLIF((SELECT total_projects FROM total), 0), 2) AS percentage
        FROM grouped
        ORDER BY sort_rank ASC;
    """
    return db.query_df(sql)


def get_users_by_category(
    db: DashboardDatabase,
    table_name: str,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """Aggregate user distribution across top categories with residual grouped as 'Other'."""
    config = db.config or get_default_config()
    n = top_n or config.top_categories_limit
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_category():
        return pd.DataFrame(columns=["category", "user_count", "percentage"])

    sql = f"""
        WITH cat_users AS (
            SELECT 
                COALESCE(NULLIF(TRIM(category), ''), 'Unspecified') AS category,
                COUNT(*) AS user_count,
                ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS rank
            FROM {table_name}
            GROUP BY COALESCE(NULLIF(TRIM(category), ''), 'Unspecified')
        ),
        total AS (
            SELECT SUM(user_count) AS total_users FROM cat_users
        ),
        grouped AS (
            SELECT 
                CASE WHEN rank <= {n} THEN category ELSE 'Other' END AS category_group,
                CASE WHEN rank <= {n} THEN rank ELSE {n + 1} END AS sort_rank,
                SUM(user_count) AS user_count
            FROM cat_users
            GROUP BY 
                CASE WHEN rank <= {n} THEN category ELSE 'Other' END,
                CASE WHEN rank <= {n} THEN rank ELSE {n + 1} END
        )
        SELECT 
            category_group AS category,
            user_count,
            ROUND(user_count * 100.0 / NULLIF((SELECT total_users FROM total), 0), 2) AS percentage
        FROM grouped
        ORDER BY sort_rank ASC;
    """
    return db.query_df(sql)


def get_avg_projects_per_user_by_category(
    db: DashboardDatabase,
    table_name: str,
    top_n: Optional[int] = None,
    min_users: int = 1,
) -> pd.DataFrame:
    """Calculate normalized average projects per user (total_projects / user_count) per category."""
    config = db.config or get_default_config()
    n = top_n or config.top_categories_limit
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_category():
        return pd.DataFrame(columns=["category", "user_count", "project_count", "avg_projects_per_user"])

    proj_expr = cap.get_clean_projects_expr()

    sql = f"""
        SELECT 
            COALESCE(NULLIF(TRIM(category), ''), 'Unspecified') AS category,
            COUNT(*) AS user_count,
            SUM({proj_expr}) AS project_count,
            ROUND(SUM({proj_expr}) / NULLIF(COUNT(*), 0), 2) AS avg_projects_per_user
        FROM {table_name}
        GROUP BY COALESCE(NULLIF(TRIM(category), ''), 'Unspecified')
        HAVING COUNT(*) >= {min_users}
        ORDER BY avg_projects_per_user DESC
        LIMIT {n};
    """
    return db.query_df(sql)


def get_category_concentration_pareto(
    db: DashboardDatabase,
    table_name: str,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """Calculate cumulative market share Pareto distribution across top categories."""
    config = db.config or get_default_config()
    n = top_n or config.top_categories_limit
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_category():
        return pd.DataFrame(columns=["category", "project_count", "cum_percentage"])

    proj_expr = cap.get_clean_projects_expr()

    sql = f"""
        WITH cat_totals AS (
            SELECT 
                COALESCE(NULLIF(TRIM(category), ''), 'Unspecified') AS category,
                SUM({proj_expr}) AS project_count
            FROM {table_name}
            GROUP BY COALESCE(NULLIF(TRIM(category), ''), 'Unspecified')
        ),
        cumulative AS (
            SELECT 
                category,
                project_count,
                SUM(project_count) OVER (ORDER BY project_count DESC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_projects,
                SUM(project_count) OVER () AS total_projects
            FROM cat_totals
        )
        SELECT 
            category,
            project_count,
            ROUND(cum_projects * 100.0 / NULLIF(total_projects, 0), 2) AS cum_percentage
        FROM cumulative
        ORDER BY project_count DESC
        LIMIT {n};
    """
    return db.query_df(sql)


def get_user_vs_project_category_comparison(
    db: DashboardDatabase,
    table_name: str,
    top_n: Optional[int] = 15,
) -> pd.DataFrame:
    """Extract comparative user counts and project volumes across top active categories."""
    config = db.config or get_default_config()
    n = top_n or 15
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_category():
        return pd.DataFrame(columns=["category", "user_count", "project_count", "user_share_pct", "project_share_pct", "avg_projects_per_user"])

    proj_expr = cap.get_clean_projects_expr()

    sql = f"""
        WITH totals AS (
            SELECT 
                COUNT(*) AS total_users_all,
                SUM({proj_expr}) AS total_projects_all
            FROM {table_name}
        ),
        cat_stats AS (
            SELECT 
                COALESCE(NULLIF(TRIM(category), ''), 'Unspecified') AS category,
                COUNT(*) AS user_count,
                SUM({proj_expr}) AS project_count
            FROM {table_name}
            GROUP BY COALESCE(NULLIF(TRIM(category), ''), 'Unspecified')
        )
        SELECT 
            c.category,
            c.user_count,
            c.project_count,
            ROUND(c.user_count * 100.0 / NULLIF(t.total_users_all, 0), 2) AS user_share_pct,
            ROUND(c.project_count * 100.0 / NULLIF(t.total_projects_all, 0), 2) AS project_share_pct,
            ROUND(c.project_count / NULLIF(c.user_count, 0), 2) AS avg_projects_per_user
        FROM cat_stats c
        CROSS JOIN totals t
        ORDER BY c.project_count DESC
        LIMIT {n};
    """
    return db.query_df(sql)
