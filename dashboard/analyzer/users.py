"""User activity, rankings, and scatter data extraction."""

from typing import Any, Dict, List, Optional
import pandas as pd
from ..db.connection import DashboardDatabase
from ..db.schema import FieldCapabilityChecker, SchemaInspector
from ..config import get_default_config


def get_user_project_scatter_data(
    db: DashboardDatabase,
    table_name: str,
    sample_limit: Optional[int] = None,
    active_only: bool = True,
) -> pd.DataFrame:
    """Extract ordered user rank vs. completed project count for Scattergl visualization.
    
    Ranks active freelancers with completed projects to show the true activity distribution curve.
    Uses DuckDB window functions and deterministic SQL sampling when dataset size exceeds sample_limit.
    """
    config = db.config or get_default_config()
    limit = sample_limit or config.scatter_sample_limit
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()
    has_name = cap.has_column("name")
    has_title = cap.has_column("title")
    has_score = cap.has_column("success_score")

    name_expr = "name" if has_name else "CONCAT('User #', CAST(ROW_NUMBER() OVER () AS VARCHAR)) AS name"
    title_expr = "title" if has_title else "'' AS title"
    score_expr = f"{cap.get_clean_numeric_expr('success_score')} AS success_score" if has_score else "0.0 AS success_score"

    total_records = SchemaInspector.get_total_records(db, table_name)
    if total_records == 0:
        return pd.DataFrame(columns=["user_rank", "project_count", "name", "title", "success_score"])

    # Check if there are active freelancers with projects > 0
    active_records_sql = f"SELECT COUNT(*) FROM {table_name} WHERE {proj_expr} > 0;"
    active_count = db.query_scalar(active_records_sql) or 0
    where_clause = f"WHERE {proj_expr} > 0" if (active_only and active_count > 0) else ""
    records_to_rank = active_count if (active_only and active_count > 0) else total_records

    if records_to_rank <= limit:
        sql = f"""
            SELECT 
                ROW_NUMBER() OVER (ORDER BY {proj_expr} DESC) AS user_rank,
                {proj_expr} AS project_count,
                {name_expr},
                {title_expr},
                {score_expr}
            FROM {table_name}
            {where_clause}
            ORDER BY user_rank ASC;
        """
    else:
        # Step-sampled ranked subset to accurately capture the full curve without exceeding limit
        step = max(1, records_to_rank // limit)
        sql = f"""
            WITH ranked AS (
                SELECT 
                    ROW_NUMBER() OVER (ORDER BY {proj_expr} DESC) AS user_rank,
                    {proj_expr} AS project_count,
                    {name_expr},
                    {title_expr},
                    {score_expr}
                FROM {table_name}
                {where_clause}
            )
            SELECT * FROM ranked
            WHERE (user_rank % {step} = 0) OR (user_rank <= 100)
            ORDER BY user_rank ASC
            LIMIT {limit};
        """

    return db.query_df(sql)


def get_user_activity_segments(
    db: DashboardDatabase,
    table_name: str,
    segment_defs: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """Classify users into activity tiers (Inactive, Low, Medium, High, Very High)."""
    config = db.config or get_default_config()
    segments = segment_defs or config.activity_segments
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()

    case_branches = []
    sort_branches = []
    for idx, seg in enumerate(segments):
        min_v = seg["min"]
        max_v = seg["max"]
        label = seg["label"]
        order = idx + 1
        if max_v is None:
            case_branches.append(f"WHEN {proj_expr} >= {min_v} THEN '{label}'")
        elif min_v == max_v:
            case_branches.append(f"WHEN {proj_expr} = {min_v} THEN '{label}'")
        else:
            case_branches.append(
                f"WHEN {proj_expr} BETWEEN {min_v} AND {max_v} THEN '{label}'"
            )
        sort_branches.append(f"WHEN segment = '{label}' THEN {order}")

    case_expr = f"CASE {' '.join(case_branches)} ELSE 'Unknown' END"
    sort_expr = f"CASE {' '.join(sort_branches)} ELSE 99 END"

    sql = f"""
        WITH classified AS (
            SELECT {case_expr} AS segment
            FROM {table_name}
        )
        SELECT 
            segment,
            COUNT(*) AS user_count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM classified), 2) AS percentage,
            {sort_expr} AS sort_order
        FROM classified
        GROUP BY segment
        ORDER BY sort_order ASC;
    """
    return db.query_df(sql)
