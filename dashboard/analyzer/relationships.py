"""Skills unnesting, temporal aggregations, geography, and numeric correlations."""

from typing import Any, Dict, List, Optional
import pandas as pd
from ..db.connection import DashboardDatabase
from ..db.schema import FieldCapabilityChecker, SchemaInspector
from ..config import get_default_config


def get_most_common_skills(
    db: DashboardDatabase,
    table_name: str,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """Extract top unnested skills with frequency and user coverage percentage."""
    config = db.config or get_default_config()
    n = top_n or config.top_skills_limit
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_skills():
        return pd.DataFrame(columns=["skill", "count", "percentage"])

    total_records = SchemaInspector.get_total_records(db, table_name)
    if total_records == 0:
        return pd.DataFrame(columns=["skill", "count", "percentage"])

    col_types = SchemaInspector.get_column_types(db, table_name)
    has_array_skills = cap.has_column("skills") and "[]" in col_types.get("skills", "")

    if has_array_skills:
        sql = f"""
            WITH unnested AS (
                SELECT UNNEST(skills) AS skill
                FROM {table_name}
                WHERE skills IS NOT NULL
            ),
            cleaned AS (
                SELECT TRIM(skill) AS skill
                FROM unnested
                WHERE TRIM(skill) != ''
            )
            SELECT 
                skill,
                COUNT(*) AS count,
                ROUND(COUNT(*) * 100.0 / {total_records}, 2) AS percentage
            FROM cleaned
            GROUP BY skill
            ORDER BY count DESC
            LIMIT {n};
        """
    elif cap.has_column("skills_str"):
        sql = f"""
            WITH split_skills AS (
                SELECT UNNEST(STRING_SPLIT(skills_str, ',')) AS skill
                FROM {table_name}
                WHERE skills_str IS NOT NULL
            ),
            cleaned AS (
                SELECT TRIM(skill) AS skill
                FROM split_skills
                WHERE TRIM(skill) != ''
            )
            SELECT 
                skill,
                COUNT(*) AS count,
                ROUND(COUNT(*) * 100.0 / {total_records}, 2) AS percentage
            FROM cleaned
            GROUP BY skill
            ORDER BY count DESC
            LIMIT {n};
        """
    else:
        return pd.DataFrame(columns=["skill", "count", "percentage"])

    return db.query_df(sql)


def get_skills_per_user_distribution(db: DashboardDatabase, table_name: str) -> pd.DataFrame:
    """Compute frequency distribution of skills count per profile with representative sample skills."""
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_skills():
        return pd.DataFrame(columns=["skills_count", "user_count", "percentage", "sample_skills"])

    col_types = SchemaInspector.get_column_types(db, table_name)
    has_array_skills = cap.has_column("skills") and "[]" in col_types.get("skills", "")

    if cap.has_column("skills_count"):
        skills_expr = "COALESCE(TRY_CAST(skills_count AS INTEGER), 0)"
    elif cap.has_column("skills"):
        skills_expr = "COALESCE(LEN(skills), 0)"
    else:
        return pd.DataFrame(columns=["skills_count", "user_count", "percentage", "sample_skills"])

    total_records = SchemaInspector.get_total_records(db, table_name)
    if total_records == 0:
        return pd.DataFrame(columns=["skills_count", "user_count", "percentage", "sample_skills"])

    if has_array_skills:
        sql = f"""
            WITH profile_skills AS (
                SELECT 
                    {skills_expr} AS skills_count,
                    UNNEST(skills) AS skill
                FROM {table_name}
                WHERE skills IS NOT NULL
            ),
            skill_counts_per_tier AS (
                SELECT 
                    skills_count,
                    skill,
                    COUNT(*) AS cnt,
                    ROW_NUMBER() OVER (PARTITION BY skills_count ORDER BY COUNT(*) DESC) AS rnk
                FROM profile_skills
                WHERE TRIM(skill) != ''
                GROUP BY skills_count, skill
            ),
            top_skills_agg AS (
                SELECT 
                    skills_count,
                    STRING_AGG(skill, ', ') AS top_skills_sample
                FROM skill_counts_per_tier
                WHERE rnk <= 3
                GROUP BY skills_count
            ),
            tier_totals AS (
                SELECT 
                    {skills_expr} AS skills_count,
                    COUNT(*) AS user_count
                FROM {table_name}
                GROUP BY 1
            )
            SELECT 
                t.skills_count,
                t.user_count,
                ROUND(t.user_count * 100.0 / NULLIF({total_records}, 0), 2) AS percentage,
                COALESCE(s.top_skills_sample, 'None') AS sample_skills
            FROM tier_totals t
            LEFT JOIN top_skills_agg s ON t.skills_count = s.skills_count
            ORDER BY t.skills_count ASC;
        """
    else:
        sql = f"""
            WITH counts AS (
                SELECT {skills_expr} AS skills_count
                FROM {table_name}
            )
            SELECT 
                skills_count,
                COUNT(*) AS user_count,
                ROUND(COUNT(*) * 100.0 / NULLIF({total_records}, 0), 2) AS percentage,
                'N/A' AS sample_skills
            FROM counts
            GROUP BY skills_count
            ORDER BY skills_count ASC;
        """
    return db.query_df(sql)


def get_skills_vs_project_activity(db: DashboardDatabase, table_name: str) -> pd.DataFrame:
    """Compute mean and median project counts grouped by user skills count."""
    cap = FieldCapabilityChecker(db, table_name)
    if not cap.has_skills():
        return pd.DataFrame(columns=["skills_bin", "user_count", "avg_projects", "median_projects", "sort_order"])

    proj_expr = cap.get_clean_projects_expr()
    if cap.has_column("skills_count"):
        skills_expr = "COALESCE(TRY_CAST(skills_count AS INTEGER), 0)"
    elif cap.has_column("skills"):
        skills_expr = "COALESCE(LEN(skills), 0)"
    else:
        return pd.DataFrame(columns=["skills_bin", "user_count", "avg_projects", "median_projects", "sort_order"])

    sql = f"""
        WITH binned AS (
            SELECT 
                CASE 
                    WHEN {skills_expr} = 0 THEN '0 skills'
                    WHEN {skills_expr} BETWEEN 1 AND 3 THEN '1-3 skills'
                    WHEN {skills_expr} BETWEEN 4 AND 7 THEN '4-7 skills'
                    WHEN {skills_expr} BETWEEN 8 AND 15 THEN '8-15 skills'
                    WHEN {skills_expr} BETWEEN 16 AND 25 THEN '16-25 skills'
                    ELSE '26+ skills'
                END AS skills_bin,
                CASE 
                    WHEN {skills_expr} = 0 THEN 1
                    WHEN {skills_expr} BETWEEN 1 AND 3 THEN 2
                    WHEN {skills_expr} BETWEEN 4 AND 7 THEN 3
                    WHEN {skills_expr} BETWEEN 8 AND 15 THEN 4
                    WHEN {skills_expr} BETWEEN 16 AND 25 THEN 5
                    ELSE 6
                END AS sort_order,
                {proj_expr} AS projects
            FROM {table_name}
        )
        SELECT 
            skills_bin,
            COUNT(*) AS user_count,
            ROUND(AVG(projects), 2) AS avg_projects,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY projects), 2) AS median_projects,
            sort_order
        FROM binned
        GROUP BY skills_bin, sort_order
        ORDER BY sort_order ASC;
    """
    return db.query_df(sql)


def get_temporal_project_activity(
    db: DashboardDatabase,
    table_name: str,
    group_by: str = "month",
) -> pd.DataFrame:
    """Aggregate project count volume trends over monthly registration periods."""
    cap = FieldCapabilityChecker(db, table_name)
    date_col = cap.get_temporal_column()
    if not date_col:
        return pd.DataFrame(columns=["period", "project_count", "new_users"])

    proj_expr = cap.get_clean_projects_expr()

    sql = f"""
        WITH parsed AS (
            SELECT 
                TRY_CAST({date_col} AS TIMESTAMP) AS ts,
                {proj_expr} AS projects
            FROM {table_name}
            WHERE {date_col} IS NOT NULL
        ),
        valid AS (
            SELECT 
                DATE_TRUNC('{group_by}', ts) AS period_date,
                STRFTIME(DATE_TRUNC('{group_by}', ts), '%Y-%m') AS period,
                projects
            FROM parsed
            WHERE ts IS NOT NULL AND ts >= '2010-01-01' AND ts <= '2030-01-01'
        )
        SELECT 
            period,
            COUNT(*) AS new_users,
            SUM(projects) AS project_count,
            ROUND(SUM(projects) / NULLIF(COUNT(*), 0), 2) AS avg_projects_per_user
        FROM valid
        GROUP BY period, period_date
        ORDER BY period_date ASC;
    """
    return db.query_df(sql)


def get_user_growth_over_time(db: DashboardDatabase, table_name: str) -> pd.DataFrame:
    """Compute monthly user registration volume and cumulative user growth."""
    cap = FieldCapabilityChecker(db, table_name)
    date_col = cap.get_temporal_column()
    if not date_col:
        return pd.DataFrame(columns=["period", "new_users", "cumulative_users"])

    sql = f"""
        WITH parsed AS (
            SELECT TRY_CAST({date_col} AS TIMESTAMP) AS ts
            FROM {table_name}
            WHERE {date_col} IS NOT NULL
        ),
        valid AS (
            SELECT 
                DATE_TRUNC('month', ts) AS period_date,
                STRFTIME(DATE_TRUNC('month', ts), '%Y-%m') AS period
            FROM parsed
            WHERE ts IS NOT NULL AND ts >= '2010-01-01' AND ts <= '2030-01-01'
        ),
        monthly AS (
            SELECT 
                period_date,
                period,
                COUNT(*) AS new_users
            FROM valid
            GROUP BY period_date, period
        )
        SELECT 
            period,
            new_users,
            SUM(new_users) OVER (ORDER BY period_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_users
        FROM monthly
        ORDER BY period_date ASC;
    """
    return db.query_df(sql)


def get_temporal_category_activity(
    db: DashboardDatabase,
    table_name: str,
    top_n: int = 5,
) -> pd.DataFrame:
    """Track monthly project activity over time across top N categories."""
    cap = FieldCapabilityChecker(db, table_name)
    date_col = cap.get_temporal_column()
    if not date_col or not cap.has_category():
        return pd.DataFrame(columns=["period", "category", "project_count"])

    proj_expr = cap.get_clean_projects_expr()

    sql = f"""
        WITH top_cats AS (
            SELECT COALESCE(NULLIF(TRIM(category), ''), 'Unspecified') AS category
            FROM {table_name}
            GROUP BY COALESCE(NULLIF(TRIM(category), ''), 'Unspecified')
            ORDER BY SUM({proj_expr}) DESC
            LIMIT {top_n}
        ),
        parsed AS (
            SELECT 
                TRY_CAST({date_col} AS TIMESTAMP) AS ts,
                COALESCE(NULLIF(TRIM(category), ''), 'Unspecified') AS category,
                {proj_expr} AS projects
            FROM {table_name}
            WHERE {date_col} IS NOT NULL
        ),
        valid AS (
            SELECT 
                DATE_TRUNC('month', ts) AS period_date,
                STRFTIME(DATE_TRUNC('month', ts), '%Y-%m') AS period,
                category,
                projects
            FROM parsed
            WHERE ts IS NOT NULL AND ts >= '2010-01-01' AND ts <= '2030-01-01'
              AND category IN (SELECT category FROM top_cats)
        )
        SELECT 
            period,
            category,
            SUM(projects) AS project_count
        FROM valid
        GROUP BY period_date, period, category
        ORDER BY period_date ASC, category ASC;
    """
    return db.query_df(sql)


def get_geographic_user_distribution(
    db: DashboardDatabase,
    table_name: str,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """Extract top user locations / countries."""
    config = db.config or get_default_config()
    n = top_n or config.top_locations_limit
    cap = FieldCapabilityChecker(db, table_name)
    loc_col = cap.get_location_column()
    if not loc_col:
        return pd.DataFrame(columns=["location", "user_count", "percentage"])

    total_records = SchemaInspector.get_total_records(db, table_name)
    sql = f"""
        WITH locs AS (
            SELECT 
                COALESCE(NULLIF(TRIM({loc_col}), ''), 'Unspecified') AS location,
                COUNT(*) AS user_count
            FROM {table_name}
            GROUP BY COALESCE(NULLIF(TRIM({loc_col}), ''), 'Unspecified')
        )
        SELECT 
            location,
            user_count,
            ROUND(user_count * 100.0 / NULLIF({total_records}, 0), 2) AS percentage
        FROM locs
        ORDER BY user_count DESC
        LIMIT {n};
    """
    return db.query_df(sql)


def get_geographic_project_activity(
    db: DashboardDatabase,
    table_name: str,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """Extract completed project counts aggregated by geographic location."""
    config = db.config or get_default_config()
    n = top_n or config.top_locations_limit
    cap = FieldCapabilityChecker(db, table_name)
    loc_col = cap.get_location_column()
    if not loc_col:
        return pd.DataFrame(columns=["location", "project_count", "user_count", "avg_projects"])

    proj_expr = cap.get_clean_projects_expr()
    sql = f"""
        WITH locs AS (
            SELECT 
                COALESCE(NULLIF(TRIM({loc_col}), ''), 'Unspecified') AS location,
                COUNT(*) AS user_count,
                SUM({proj_expr}) AS project_count
            FROM {table_name}
            GROUP BY COALESCE(NULLIF(TRIM({loc_col}), ''), 'Unspecified')
        )
        SELECT 
            location,
            project_count,
            user_count,
            ROUND(project_count / NULLIF(user_count, 0), 2) AS avg_projects
        FROM locs
        ORDER BY project_count DESC
        LIMIT {n};
    """
    return db.query_df(sql)


def get_numeric_correlations(
    db: DashboardDatabase,
    table_name: str,
    candidate_cols: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Calculate pairwise Pearson correlation matrix for numeric features in DuckDB."""
    cap = FieldCapabilityChecker(db, table_name)
    preferred_order = [
        "success_score",
        "total_completed_projects",
        "completion_rate",
        "ontime_delivery_rate",
        "rehire_rate",
        "communication_success_rate",
        "skills_count",
        "avg_response_time_minutes",
        "portfolio_count",
        "rating",
        "reviews_count",
    ]
    if candidate_cols:
        cols = [c for c in candidate_cols if cap.has_column(c)]
    else:
        cols = [c for c in preferred_order if cap.has_column(c)]

    if len(cols) < 2:
        return pd.DataFrame()

    corr_matrix = pd.DataFrame(index=cols, columns=cols, dtype=float)

    # Compute correlation expressions in single DuckDB SQL query
    select_items = []
    pair_keys = []
    for i, col1 in enumerate(cols):
        for j, col2 in enumerate(cols):
            if i <= j:
                alias = f"corr_{i}_{j}"
                c1_expr = cap.get_clean_numeric_expr(col1)
                c2_expr = cap.get_clean_numeric_expr(col2)
                select_items.append(
                    f"COALESCE(CORR({c1_expr}, {c2_expr}), 0.0) AS {alias}"
                )
                pair_keys.append((i, j, col1, col2, alias))

    sql = f"SELECT {', '.join(select_items)} FROM {table_name};"
    df_res = db.query_df(sql)
    if df_res.empty:
        return corr_matrix

    row = df_res.iloc[0]
    for i, j, col1, col2, alias in pair_keys:
        val = float(row[alias])
        # Force diagonal to 1.0
        if col1 == col2:
            val = 1.0
        # Ensure bounded range [-1, 1]
        val = max(-1.0, min(1.0, round(val, 3)))
        corr_matrix.loc[col1, col2] = val
        corr_matrix.loc[col2, col1] = val

    return corr_matrix


def get_bivariate_relationship_samples(
    db: DashboardDatabase,
    table_name: str,
    x_col: str,
    y_col: str,
    sample_limit: Optional[int] = None,
) -> pd.DataFrame:
    """Extract sampled clean pairs (x, y) for bivariate relationship scatterplot."""
    config = db.config or get_default_config()
    limit = sample_limit or config.scatter_sample_limit
    cap = FieldCapabilityChecker(db, table_name)
    if not (cap.has_column(x_col) and cap.has_column(y_col)):
        return pd.DataFrame(columns=[x_col, y_col, "name", "title"])

    has_name = cap.has_column("name")
    has_title = cap.has_column("title")
    name_expr = "name" if has_name else "'' AS name"
    title_expr = "title" if has_title else "'' AS title"

    x_expr = cap.get_clean_numeric_expr(x_col)
    y_expr = cap.get_clean_numeric_expr(y_col)

    sql = f"""
        SELECT 
            {x_expr} AS {x_col},
            {y_expr} AS {y_col},
            {name_expr},
            {title_expr}
        FROM {table_name}
        WHERE {x_col} IS NOT NULL AND {y_col} IS NOT NULL
        LIMIT {limit};
    """
    return db.query_df(sql)
