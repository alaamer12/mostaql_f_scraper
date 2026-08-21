"""Dataset overview, KPI summaries, and data quality analyzers."""

from typing import Any, Dict, List, Optional
import pandas as pd
from ..db.connection import DashboardDatabase
from ..db.schema import SchemaInspector, FieldCapabilityChecker


def get_dataset_kpis(db: DashboardDatabase, table_name: str) -> Dict[str, Any]:
    """Compute top-level KPI metrics across the entire dataset via SQL aggregations."""
    cap = FieldCapabilityChecker(db, table_name)
    total_records = SchemaInspector.get_total_records(db, table_name)
    if total_records == 0:
        return {
            "total_users": 0,
            "total_completed_projects": 0,
            "total_active_projects": 0,
            "avg_success_score": 0.0,
            "median_projects_per_user": 0.0,
            "max_projects_by_user": 0,
            "min_projects_by_user": 0,
            "users_with_projects": 0,
            "users_without_projects": 0,
            "unique_categories": 0,
            "overall_completeness_rate": 0.0,
        }

    proj_expr = cap.get_clean_projects_expr()
    active_expr = cap.get_clean_active_projects_expr()
    has_active = cap.has_column("active_projects")
    has_success = cap.has_column("success_score")
    has_category = cap.has_column("category")

    select_parts = [
        "COUNT(*) AS total_users",
        f"COALESCE(SUM({proj_expr}), 0) AS total_completed_projects",
        f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {proj_expr}) AS median_projects_per_user",
        f"COALESCE(MAX({proj_expr}), 0) AS max_projects_by_user",
        f"COALESCE(MIN({proj_expr}), 0) AS min_projects_by_user",
        f"COUNT(CASE WHEN {proj_expr} > 0 THEN 1 END) AS users_with_projects",
        f"COUNT(CASE WHEN {proj_expr} = 0 THEN 1 END) AS users_without_projects",
    ]

    if has_active:
        select_parts.append(f"COALESCE(SUM({active_expr}), 0) AS total_active_projects")
    else:
        select_parts.append("0 AS total_active_projects")

    if has_success:
        score_clean = cap.get_clean_numeric_expr("success_score")
        select_parts.append(f"COALESCE(AVG({score_clean}), 0.0) AS avg_success_score")
    else:
        select_parts.append("0.0 AS avg_success_score")

    if has_category:
        select_parts.append("COUNT(DISTINCT category) AS unique_categories")
    else:
        select_parts.append("0 AS unique_categories")

    sql = f"SELECT {', '.join(select_parts)} FROM {table_name};"
    df = db.query_df(sql)
    res = df.to_dict(orient="records")[0] if not df.empty else {}

    # Calculate overall completeness rate across all columns
    missing_df = get_missing_data_by_field(db, table_name)
    if not missing_df.empty:
        res["overall_completeness_rate"] = round(float(missing_df["completeness_percentage"].mean()), 2)
    else:
        res["overall_completeness_rate"] = 100.0

    return res


def get_missing_data_by_field(db: DashboardDatabase, table_name: str) -> pd.DataFrame:
    """Calculate non-null and missing data rates for all schema columns."""
    columns = SchemaInspector.get_column_names(db, table_name)
    if not columns:
        return pd.DataFrame(columns=["field", "total_count", "non_null_count", "missing_count", "missing_percentage", "completeness_percentage"])

    # Avoid inspecting huge struct columns like 'stats' in profiles
    filtered_cols = [c for c in columns if c not in ("stats", "parse_signals")]
    if not filtered_cols:
        filtered_cols = columns

    count_exprs = []
    for c in filtered_cols:
        count_exprs.append(f"COUNT({c}) AS count_{c}")

    sql = f"SELECT COUNT(*) AS total_records, {', '.join(count_exprs)} FROM {table_name};"
    df = db.query_df(sql)
    if df.empty:
        return pd.DataFrame()

    row = df.iloc[0]
    total_records = int(row["total_records"])
    if total_records == 0:
        return pd.DataFrame()

    results = []
    for c in filtered_cols:
        non_null = int(row[f"count_{c}"])
        missing = total_records - non_null
        missing_pct = round((missing / total_records) * 100.0, 2)
        completeness_pct = round((non_null / total_records) * 100.0, 2)
        results.append({
            "field": c,
            "total_count": total_records,
            "non_null_count": non_null,
            "missing_count": missing,
            "missing_percentage": missing_pct,
            "completeness_percentage": completeness_pct,
        })

    res_df = pd.DataFrame(results)
    return res_df.sort_values(by="missing_percentage", ascending=False).reset_index(drop=True)


def get_data_completeness_distribution(
    db: DashboardDatabase,
    table_name: str,
    key_fields: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Compute profile completeness distribution across non-null key fields."""
    columns = set(SchemaInspector.get_column_names(db, table_name))
    if not columns:
        return pd.DataFrame(columns=["completeness_bucket", "user_count", "percentage"])

    if key_fields is None:
        preferred = [
            "name", "title", "category", "location", "skills",
            "total_completed_projects", "completion_rate", "registration_date",
            "portfolio_count", "success_score"
        ]
        fields = [f for f in preferred if f in columns]
    else:
        fields = [f for f in key_fields if f in columns]

    if not fields:
        return pd.DataFrame(columns=["completeness_bucket", "user_count", "percentage"])

    field_checks = [f"CASE WHEN {f} IS NOT NULL THEN 1 ELSE 0 END" for f in fields]
    score_expr = f"(({' + '.join(field_checks)}) * 100.0 / {len(fields)})"

    sql = f"""
        WITH scored AS (
            SELECT {score_expr} AS completeness_score
            FROM {table_name}
        ),
        binned AS (
            SELECT 
                CASE 
                    WHEN completeness_score <= 20 THEN '0-20%'
                    WHEN completeness_score <= 40 THEN '21-40%'
                    WHEN completeness_score <= 60 THEN '41-60%'
                    WHEN completeness_score <= 80 THEN '61-80%'
                    ELSE '81-100%'
                END AS completeness_bucket,
                CASE 
                    WHEN completeness_score <= 20 THEN 1
                    WHEN completeness_score <= 40 THEN 2
                    WHEN completeness_score <= 60 THEN 3
                    WHEN completeness_score <= 80 THEN 4
                    ELSE 5
                END AS sort_order
            FROM scored
        )
        SELECT 
            completeness_bucket,
            COUNT(*) AS user_count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM scored), 2) AS percentage
        FROM binned
        GROUP BY completeness_bucket, sort_order
        ORDER BY sort_order ASC;
    """
    return db.query_df(sql)


def get_parse_confidence_distribution(db: DashboardDatabase, table_name: str) -> pd.DataFrame:
    """Compute breakdown of parser confidence levels (ok, warning, low)."""
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_column("parse_confidence"):
        return pd.DataFrame(columns=["confidence_level", "count", "percentage"])

    sql = f"""
        SELECT 
            COALESCE(parse_confidence, 'unknown') AS confidence_level,
            COUNT(*) AS count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM {table_name}), 2) AS percentage
        FROM {table_name}
        GROUP BY parse_confidence
        ORDER BY count DESC;
    """
    return db.query_df(sql)
