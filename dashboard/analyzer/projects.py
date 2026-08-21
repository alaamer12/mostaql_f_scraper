"""Project volume, top user rankings, and concentration analyzers."""

from typing import Any, Dict, List, Optional
import pandas as pd
from ..db.connection import DashboardDatabase
from ..db.schema import FieldCapabilityChecker, SchemaInspector
from ..config import get_default_config


def get_top_users_by_projects(
    db: DashboardDatabase,
    table_name: str,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """Retrieve top N users ordered by completed project volume."""
    config = db.config or get_default_config()
    n = limit or config.top_users_limit
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()
    has_name = cap.has_column("name")
    has_title = cap.has_column("title")
    has_score = cap.has_column("success_score")
    has_comp_rate = cap.has_column("completion_rate")

    name_expr = "name" if has_name else "CONCAT('User #', CAST(ROW_NUMBER() OVER () AS VARCHAR)) AS name"
    title_expr = "title" if has_title else "'' AS title"
    score_expr = f"{cap.get_clean_numeric_expr('success_score')} AS success_score" if has_score else "0.0 AS success_score"
    rate_expr = f"{cap.get_clean_numeric_expr('completion_rate')} AS completion_rate" if has_comp_rate else "0.0 AS completion_rate"

    sql = f"""
        SELECT 
            ROW_NUMBER() OVER (ORDER BY {proj_expr} DESC) AS rank,
            {name_expr},
            {title_expr},
            {proj_expr} AS project_count,
            {score_expr},
            {rate_expr}
        FROM {table_name}
        WHERE {proj_expr} > 0
        ORDER BY project_count DESC
        LIMIT {n};
    """
    return db.query_df(sql)


def get_project_concentration_percentiles(
    db: DashboardDatabase,
    table_name: str,
) -> pd.DataFrame:
    """Calculate percentage of total projects held by Top 1%, Top 5%, Top 10%, Top 20%, and Bottom 80%."""
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()
    total_records = SchemaInspector.get_total_records(db, table_name)
    if total_records == 0:
        return pd.DataFrame(columns=["tier", "user_share_pct", "project_share_pct", "total_projects", "user_count"])

    sql = f"""
        WITH ranked AS (
            SELECT 
                {proj_expr} AS projects,
                ROW_NUMBER() OVER (ORDER BY {proj_expr} DESC) AS user_rank,
                COUNT(*) OVER () AS total_users,
                SUM({proj_expr}) OVER () AS total_projects_all
            FROM {table_name}
        ),
        tiered AS (
            SELECT 
                projects,
                user_rank,
                total_users,
                total_projects_all,
                CASE 
                    WHEN user_rank <= CEIL(total_users * 0.01) THEN 'Top 1%'
                    WHEN user_rank <= CEIL(total_users * 0.05) THEN 'Top 5%'
                    WHEN user_rank <= CEIL(total_users * 0.10) THEN 'Top 10%'
                    WHEN user_rank <= CEIL(total_users * 0.20) THEN 'Top 20%'
                    ELSE 'Bottom 80%'
                END AS tier,
                CASE 
                    WHEN user_rank <= CEIL(total_users * 0.01) THEN 1
                    WHEN user_rank <= CEIL(total_users * 0.05) THEN 2
                    WHEN user_rank <= CEIL(total_users * 0.10) THEN 3
                    WHEN user_rank <= CEIL(total_users * 0.20) THEN 4
                    ELSE 5
                END AS sort_order
            FROM ranked
        )
        SELECT 
            tier,
            COUNT(*) AS user_count,
            ROUND(COUNT(*) * 100.0 / MIN(total_users), 2) AS user_share_pct,
            SUM(projects) AS tier_projects,
            ROUND(SUM(projects) * 100.0 / NULLIF(MIN(total_projects_all), 0), 2) AS project_share_pct,
            sort_order
        FROM tiered
        GROUP BY tier, sort_order
        ORDER BY sort_order ASC;
    """
    return db.query_df(sql)


def get_pareto_project_activity(
    db: DashboardDatabase,
    table_name: str,
    sample_points: int = 100,
) -> pd.DataFrame:
    """Compute cumulative user population percentage vs cumulative project volume percentage (Lorenz/Pareto curve)."""
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()
    total_records = SchemaInspector.get_total_records(db, table_name)
    if total_records == 0:
        return pd.DataFrame(columns=["cum_user_pct", "cum_project_pct", "equality_line"])

    sql = f"""
        WITH ranked AS (
            SELECT 
                {proj_expr} AS projects,
                ROW_NUMBER() OVER (ORDER BY {proj_expr} DESC) AS user_rank,
                COUNT(*) OVER () AS total_users,
                SUM({proj_expr}) OVER () AS total_projects_all
            FROM {table_name}
        ),
        cumulative AS (
            SELECT 
                user_rank,
                ROUND((user_rank * 100.0) / total_users, 2) AS cum_user_pct,
                ROUND((SUM(projects) OVER (ORDER BY user_rank ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) * 100.0) / NULLIF(total_projects_all, 0), 2) AS cum_project_pct,
                NTILE({sample_points}) OVER (ORDER BY user_rank) AS bucket
            FROM ranked
        )
        SELECT 
            MAX(cum_user_pct) AS cum_user_pct,
            MAX(cum_project_pct) AS cum_project_pct,
            MAX(cum_user_pct) AS equality_line
        FROM cumulative
        GROUP BY bucket
        ORDER BY bucket ASC;
    """
    df = db.query_df(sql)
    # Ensure (0,0) baseline is present
    baseline = pd.DataFrame([{"cum_user_pct": 0.0, "cum_project_pct": 0.0, "equality_line": 0.0}])
    return pd.concat([baseline, df], ignore_index=True)
