"""Schema inspection and dynamic capability discovery."""

from typing import Any, Dict, List, Optional, Set
import pandas as pd
from .connection import DashboardDatabase


class SchemaInspector:
    """Introspects table structures, field types, and column statistics via DuckDB."""

    @staticmethod
    def get_columns_info(db: DashboardDatabase, table_name: str) -> List[Dict[str, Any]]:
        """Retrieve column names, data types, and nullability information."""
        try:
            df = db.query_df(f"DESCRIBE {table_name};")
            # DuckDB DESCRIBE columns: column_name, column_type, null, key, default, extra
            return df.to_dict(orient="records")
        except Exception:
            return []

    @staticmethod
    def get_column_names(db: DashboardDatabase, table_name: str) -> List[str]:
        """Return list of column names for given table/view."""
        cols_info = SchemaInspector.get_columns_info(db, table_name)
        return [c.get("column_name") for c in cols_info if "column_name" in c]

    @staticmethod
    def get_column_types(db: DashboardDatabase, table_name: str) -> Dict[str, str]:
        """Return mapping of column name to DuckDB type string."""
        cols_info = SchemaInspector.get_columns_info(db, table_name)
        return {c.get("column_name"): c.get("column_type", "").upper() for c in cols_info if "column_name" in c}

    @staticmethod
    def get_total_records(db: DashboardDatabase, table_name: str) -> int:
        """Get the total row count of the table."""
        val = db.query_scalar(f"SELECT COUNT(*) FROM {table_name};")
        return int(val) if val is not None else 0


class FieldCapabilityChecker:
    """Checks whether specific analytical sections and charts can be rendered based on schema."""

    def __init__(self, db: DashboardDatabase, table_name: str):
        self.db = db
        self.table_name = table_name
        self._types: Dict[str, str] = SchemaInspector.get_column_types(db, table_name)
        self._columns: Set[str] = set(self._types.keys())

    @property
    def columns(self) -> Set[str]:
        return self._columns

    def has_column(self, col_name: str) -> bool:
        """Check if column exists in table."""
        return col_name in self._columns

    def has_columns(self, col_names: List[str]) -> bool:
        """Check if all columns in list exist."""
        return all(c in self._columns for c in col_names)

    def has_any_column(self, col_names: List[str]) -> bool:
        """Check if at least one column from list exists."""
        return any(c in self._columns for c in col_names)

    def get_first_available_column(self, candidates: List[str]) -> Optional[str]:
        """Return the first candidate column present in the dataset."""
        for c in candidates:
            if c in self._columns:
                return c
        return None

    def has_projects(self) -> bool:
        """Check if dataset contains project metric columns."""
        return self.has_any_column(["total_completed_projects", "projects", "completed_projects"])

    def get_projects_column(self) -> str:
        """Get canonical project count column name."""
        return self.get_first_available_column(
            ["total_completed_projects", "completed_projects", "projects"]
        ) or "total_completed_projects"

    def get_clean_projects_expr(self, col_name: Optional[str] = None) -> str:
        """Returns a SQL expression that casts and sanitizes project counts.
        
        Guards against corrupt data (phone numbers >= 10,000, 4-digit years 1900-2099, negative numbers)
        so that extreme parsing artifacts do not distort visualizations.
        """
        target = col_name or self.get_projects_column()
        return (
            f"CASE "
            f"WHEN TRY_CAST({target} AS DOUBLE) IS NULL THEN 0.0 "
            f"WHEN TRY_CAST({target} AS DOUBLE) < 0 THEN 0.0 "
            f"WHEN TRY_CAST({target} AS DOUBLE) >= 1950 AND TRY_CAST({target} AS DOUBLE) <= 2099 THEN 0.0 "
            f"WHEN TRY_CAST({target} AS DOUBLE) >= 10000 THEN 0.0 "
            f"ELSE TRY_CAST({target} AS DOUBLE) "
            f"END"
        )

    def get_clean_active_projects_expr(self) -> str:
        """SQL expression for clean, sanitized active project counts."""
        return (
            f"CASE "
            f"WHEN TRY_CAST(active_projects AS DOUBLE) IS NULL THEN 0.0 "
            f"WHEN TRY_CAST(active_projects AS DOUBLE) < 0 THEN 0.0 "
            f"WHEN TRY_CAST(active_projects AS DOUBLE) >= 1950 AND TRY_CAST(active_projects AS DOUBLE) <= 2099 THEN 0.0 "
            f"WHEN TRY_CAST(active_projects AS DOUBLE) >= 10000 THEN 0.0 "
            f"ELSE TRY_CAST(active_projects AS DOUBLE) "
            f"END"
        )

    def get_clean_numeric_expr(self, col_name: str) -> str:
        """SQL expression for clean numeric values across known schema metrics."""
        if col_name in ("total_completed_projects", "projects", "completed_projects"):
            return self.get_clean_projects_expr(col_name)
        elif col_name == "active_projects":
            return self.get_clean_active_projects_expr()
        elif col_name in (
            "completion_rate", "ontime_delivery_rate", "rehire_rate",
            "communication_success_rate", "employment_rate", "success_score"
        ):
            return (
                f"CASE "
                f"WHEN TRY_CAST({col_name} AS DOUBLE) IS NULL THEN 0.0 "
                f"WHEN TRY_CAST({col_name} AS DOUBLE) < 0 THEN 0.0 "
                f"WHEN TRY_CAST({col_name} AS DOUBLE) > 100.0 THEN 100.0 "
                f"ELSE TRY_CAST({col_name} AS DOUBLE) "
                f"END"
            )
        elif col_name == "portfolio_count":
            return (
                f"CASE "
                f"WHEN TRY_CAST(portfolio_count AS DOUBLE) IS NULL THEN 0.0 "
                f"WHEN TRY_CAST(portfolio_count AS DOUBLE) < 0 THEN 0.0 "
                f"WHEN TRY_CAST(portfolio_count AS DOUBLE) >= 10000 THEN 0.0 "
                f"ELSE TRY_CAST(portfolio_count AS DOUBLE) "
                f"END"
            )
        elif col_name == "avg_response_time_minutes":
            return (
                f"CASE "
                f"WHEN TRY_CAST(avg_response_time_minutes AS DOUBLE) IS NULL THEN 0.0 "
                f"WHEN TRY_CAST(avg_response_time_minutes AS DOUBLE) < 0 THEN 0.0 "
                f"WHEN TRY_CAST(avg_response_time_minutes AS DOUBLE) > 100000 THEN 0.0 "
                f"ELSE TRY_CAST(avg_response_time_minutes AS DOUBLE) "
                f"END"
            )
        else:
            return f"COALESCE(TRY_CAST({col_name} AS DOUBLE), 0.0)"

    def has_category(self) -> bool:
        """Check if category information exists."""
        return self.has_column("category")

    def has_skills(self) -> bool:
        """Check if skills list or string exists."""
        return self.has_any_column(["skills", "skills_str", "skills_count"])

    def has_temporal(self) -> bool:
        """Check if timestamp/date column exists."""
        return self.has_any_column([
            "registration_date", "registration_date_str", "member_since",
            "created_at", "scraped_at", "date"
        ])

    def get_temporal_column(self) -> Optional[str]:
        """Return canonical date/timestamp column."""
        return self.get_first_available_column([
            "registration_date", "registration_date_str", "created_at", "scraped_at"
        ])

    def has_location(self) -> bool:
        """Check if geographic location column exists."""
        return self.has_any_column(["location", "country", "city"])

    def get_location_column(self) -> Optional[str]:
        """Return location column name."""
        return self.get_first_available_column(["location", "country", "city"])

    def get_numeric_columns(self) -> List[str]:
        """Return all detected numeric column names."""
        numeric_types = ("INT", "BIGINT", "HUGEINT", "SMALLINT", "TINYINT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")
        result = []
        for col, col_type in self._types.items():
            if any(nt in col_type for nt in numeric_types):
                result.append(col)
        return result
