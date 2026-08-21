"""Statistical distributions, histograms, ECDF, and outlier analysis."""

from typing import Any, Dict, List, Optional
import pandas as pd
from ..db.connection import DashboardDatabase
from ..db.schema import FieldCapabilityChecker, SchemaInspector
from ..config import get_default_config


def get_project_count_ranges(
    db: DashboardDatabase,
    table_name: str,
    bins: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """Calculate user counts across predefined project volume ranges."""
    config = db.config or get_default_config()
    bin_defs = bins or config.project_bins
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()

    case_branches = []
    sort_branches = []
    for idx, b in enumerate(bin_defs):
        min_v = b["min"]
        max_v = b["max"]
        label = b["label"]
        order = idx + 1
        if max_v is None:
            case_branches.append(f"WHEN {proj_expr} >= {min_v} THEN '{label}'")
        elif min_v == max_v:
            case_branches.append(f"WHEN {proj_expr} = {min_v} THEN '{label}'")
        else:
            case_branches.append(
                f"WHEN {proj_expr} BETWEEN {min_v} AND {max_v} THEN '{label}'"
            )
        sort_branches.append(f"WHEN project_range = '{label}' THEN {order}")

    case_expr = f"CASE {' '.join(case_branches)} ELSE 'Other' END"
    sort_expr = f"CASE {' '.join(sort_branches)} ELSE 99 END"

    sql = f"""
        WITH binned AS (
            SELECT {case_expr} AS project_range
            FROM {table_name}
        )
        SELECT 
            project_range,
            COUNT(*) AS user_count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM binned), 2) AS percentage,
            {sort_expr} AS sort_order
        FROM binned
        GROUP BY project_range
        ORDER BY sort_order ASC;
    """
    return db.query_df(sql)


def get_project_count_histogram(
    db: DashboardDatabase,
    table_name: str,
    num_bins: Optional[int] = None,
) -> pd.DataFrame:
    """Calculate dynamic SQL-computed histogram bins across project counts."""
    config = db.config or get_default_config()
    bins_count = num_bins or config.histogram_bins_default
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()

    # Determine min and max project counts
    bounds_sql = f"""
        SELECT 
            COALESCE(MIN({proj_expr}), 0) AS min_val,
            COALESCE(MAX({proj_expr}), 0) AS max_val,
            COUNT(*) AS total_rows
        FROM {table_name};
    """
    bounds_df = db.query_df(bounds_sql)
    if bounds_df.empty or bounds_df.iloc[0]["total_rows"] == 0:
        return pd.DataFrame(columns=["bin_start", "bin_end", "bin_label", "count", "percentage"])

    min_val = float(bounds_df.iloc[0]["min_val"])
    max_val = float(bounds_df.iloc[0]["max_val"])
    total_rows = int(bounds_df.iloc[0]["total_rows"])

    if max_val <= min_val:
        return pd.DataFrame([{
            "bin_start": min_val,
            "bin_end": max_val,
            "bin_label": f"{int(min_val)}",
            "count": total_rows,
            "percentage": 100.0
        }])

    step = (max_val - min_val) / bins_count

    sql = f"""
        WITH binned AS (
            SELECT 
                FLOOR(({proj_expr} - {min_val}) / {step}) AS bin_idx
            FROM {table_name}
        ),
        bounded AS (
            SELECT 
                CASE WHEN bin_idx >= {bins_count} THEN {bins_count - 1} ELSE bin_idx END AS bin_idx
            FROM binned
        )
        SELECT 
            CAST(bin_idx AS INTEGER) AS bin_idx,
            {min_val} + (bin_idx * {step}) AS bin_start,
            {min_val} + ((bin_idx + 1) * {step}) AS bin_end,
            COUNT(*) AS count,
            ROUND(COUNT(*) * 100.0 / {total_rows}, 2) AS percentage
        FROM bounded
        GROUP BY bin_idx
        ORDER BY bin_idx ASC;
    """
    df = db.query_df(sql)
    if not df.empty:
        df["bin_label"] = df.apply(lambda r: f"{int(r['bin_start'])}-{int(r['bin_end'])}", axis=1)
    return df


def get_log_scale_distribution(db: DashboardDatabase, table_name: str) -> pd.DataFrame:
    """Compute project count distribution grouped on logarithmic powers-of-two scale."""
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()

    sql = f"""
        WITH categorized AS (
            SELECT 
                CASE 
                    WHEN {proj_expr} = 0 THEN '0'
                    WHEN {proj_expr} = 1 THEN '1'
                    WHEN {proj_expr} BETWEEN 2 AND 3 THEN '2 - 3'
                    WHEN {proj_expr} BETWEEN 4 AND 7 THEN '4 - 7'
                    WHEN {proj_expr} BETWEEN 8 AND 15 THEN '8 - 15'
                    WHEN {proj_expr} BETWEEN 16 AND 31 THEN '16 - 31'
                    WHEN {proj_expr} BETWEEN 32 AND 63 THEN '32 - 63'
                    WHEN {proj_expr} BETWEEN 64 AND 127 THEN '64 - 127'
                    WHEN {proj_expr} BETWEEN 128 AND 255 THEN '128 - 255'
                    ELSE '256+'
                END AS log_bin,
                CASE 
                    WHEN {proj_expr} = 0 THEN 1
                    WHEN {proj_expr} = 1 THEN 2
                    WHEN {proj_expr} BETWEEN 2 AND 3 THEN 3
                    WHEN {proj_expr} BETWEEN 4 AND 7 THEN 4
                    WHEN {proj_expr} BETWEEN 8 AND 15 THEN 5
                    WHEN {proj_expr} BETWEEN 16 AND 31 THEN 6
                    WHEN {proj_expr} BETWEEN 32 AND 63 THEN 7
                    WHEN {proj_expr} BETWEEN 64 AND 127 THEN 8
                    WHEN {proj_expr} BETWEEN 128 AND 255 THEN 9
                    ELSE 10
                END AS sort_order
            FROM {table_name}
        )
        SELECT 
            log_bin,
            COUNT(*) AS user_count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM categorized), 2) AS percentage,
            sort_order
        FROM categorized
        GROUP BY log_bin, sort_order
        ORDER BY sort_order ASC;
    """
    return db.query_df(sql)


def get_cumulative_user_distribution(db: DashboardDatabase, table_name: str) -> pd.DataFrame:
    """Compute empirical cumulative distribution function (ECDF) for user project counts."""
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()
    total_records = SchemaInspector.get_total_records(db, table_name)
    if total_records == 0:
        return pd.DataFrame(columns=["project_count", "user_count", "cum_users", "cum_percentage"])

    sql = f"""
        WITH counts AS (
            SELECT 
                {proj_expr} AS project_count,
                COUNT(*) AS users_at_count
            FROM {table_name}
            GROUP BY {proj_expr}
        ),
        cumulative AS (
            SELECT 
                project_count,
                users_at_count,
                SUM(users_at_count) OVER (ORDER BY project_count ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_users,
                {total_records} AS total_users
            FROM counts
        )
        SELECT 
            project_count,
            users_at_count AS user_count,
            cum_users,
            ROUND((cum_users * 100.0) / total_users, 2) AS cum_percentage
        FROM cumulative
        ORDER BY project_count ASC;
    """
    return db.query_df(sql)


def get_project_activity_outliers(db: DashboardDatabase, table_name: str) -> Dict[str, Any]:
    """Calculate Quartiles (Q1, Median, Q3), IQR, and outlier thresholds via SQL percentiles."""
    cap = FieldCapabilityChecker(db, table_name)
    proj_expr = cap.get_clean_projects_expr()
    total_records = SchemaInspector.get_total_records(db, table_name)
    if total_records == 0:
        return {
            "q1": 0.0, "median": 0.0, "q3": 0.0, "iqr": 0.0,
            "upper_fence": 0.0, "extreme_fence": 0.0, "outlier_count": 0, "outlier_percentage": 0.0
        }

    sql_stats = f"""
        SELECT 
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {proj_expr}) AS q1,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {proj_expr}) AS median,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {proj_expr}) AS q3,
            MAX({proj_expr}) AS max_val,
            MIN({proj_expr}) AS min_val
        FROM {table_name};
    """
    df_stats = db.query_df(sql_stats)
    if df_stats.empty:
        return {}

    q1 = float(df_stats.iloc[0]["q1"])
    median = float(df_stats.iloc[0]["median"])
    q3 = float(df_stats.iloc[0]["q3"])
    iqr = max(0.0, q3 - q1)
    upper_fence = q3 + (1.5 * iqr)
    extreme_fence = q3 + (3.0 * iqr)

    sql_outliers = f"""
        SELECT 
            COUNT(CASE WHEN {proj_expr} > {upper_fence} THEN 1 END) AS outlier_count,
            COUNT(CASE WHEN {proj_expr} > {extreme_fence} THEN 1 END) AS extreme_count
        FROM {table_name};
    """
    df_out = db.query_df(sql_outliers)
    outlier_count = int(df_out.iloc[0]["outlier_count"]) if not df_out.empty else 0
    extreme_count = int(df_out.iloc[0]["extreme_count"]) if not df_out.empty else 0

    return {
        "q1": q1,
        "median": median,
        "q3": q3,
        "iqr": iqr,
        "upper_fence": upper_fence,
        "extreme_fence": extreme_fence,
        "outlier_count": outlier_count,
        "outlier_percentage": round((outlier_count * 100.0) / total_records, 2) if total_records > 0 else 0.0,
        "extreme_outlier_count": extreme_count,
    }
