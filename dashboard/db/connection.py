"""DuckDB Connection Manager for analytical queries."""

from typing import Any, Dict, List, Optional, Tuple, Union
import duckdb
import pandas as pd
from ..config import DashboardConfig, get_default_config


class DashboardDatabase:
    """Lightweight DuckDB connection manager for high-performance SQL execution."""

    def __init__(
        self,
        config: Optional[DashboardConfig] = None,
        in_memory: bool = True,
        db_path: Optional[str] = None,
    ):
        self.config = config or get_default_config()
        self.in_memory = in_memory
        self.db_path = ":memory:" if in_memory else (db_path or ":memory:")
        self._con: Optional[duckdb.DuckDBPyConnection] = None
        self._is_closed: bool = False
        self._initialize()

    def _initialize(self) -> None:
        """Initialize the DuckDB connection and configure runtime limits."""
        self._con = duckdb.connect(self.db_path)
        
        # Apply performance configurations
        try:
            self._con.execute(f"SET memory_limit = '{self.config.memory_limit}';")
            self._con.execute(f"SET threads = {self.config.threads};")
            self._con.execute("SET preserve_insertion_order = false;")
        except Exception:
            # Tolerant if specific configuration pragmas vary across versions
            pass

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Get the active DuckDB connection."""
        if self._con is None or self._is_closed:
            self._initialize()
            self._is_closed = False
        return self._con

    def query(
        self,
        sql: str,
        params: Optional[Union[List[Any], Tuple[Any, ...], Dict[str, Any]]] = None,
    ) -> duckdb.DuckDBPyRelation:
        """Execute query and return DuckDB relation for lazy processing."""
        if params is not None:
            return self.connection.execute(sql, params)
        return self.connection.execute(sql)

    def query_df(
        self,
        sql: str,
        params: Optional[Union[List[Any], Tuple[Any, ...], Dict[str, Any]]] = None,
    ) -> pd.DataFrame:
        """Execute query and return the result as a Pandas DataFrame."""
        rel = self.query(sql, params)
        return rel.df()

    def query_records(
        self,
        sql: str,
        params: Optional[Union[List[Any], Tuple[Any, ...], Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute query and return a list of dictionary records."""
        df = self.query_df(sql, params)
        return df.to_dict(orient="records")

    def query_scalar(
        self,
        sql: str,
        params: Optional[Union[List[Any], Tuple[Any, ...], Dict[str, Any]]] = None,
    ) -> Any:
        """Execute query and return a single scalar value (first row, first column)."""
        rel = self.query(sql, params)
        row = rel.fetchone()
        return row[0] if row is not None and len(row) > 0 else None

    def execute(
        self,
        sql: str,
        params: Optional[Union[List[Any], Tuple[Any, ...], Dict[str, Any]]] = None,
    ) -> None:
        """Execute a DDL or DML statement without returning results."""
        if params is not None:
            self.connection.execute(sql, params)
        else:
            self.connection.execute(sql)

    def register_view(self, view_name: str, sql_expression: str) -> None:
        """Register a SQL view inside DuckDB."""
        self.execute(f"CREATE OR REPLACE VIEW {view_name} AS {sql_expression};")

    def table_exists(self, table_or_view: str) -> bool:
        """Check if a table or view exists in the database."""
        try:
            res = self.query_scalar(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [table_or_view],
            )
            return bool(res and res > 0)
        except Exception:
            return False

    def list_tables(self) -> List[str]:
        """List all tables and views available in current session."""
        try:
            df = self.query_df("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'")
            return df["table_name"].tolist() if not df.empty else []
        except Exception:
            return []

    def close(self) -> None:
        """Close the DuckDB connection."""
        if self._con is not None and not self._is_closed:
            try:
                self._con.close()
            except Exception:
                pass
            finally:
                self._is_closed = True
                self._con = None

    def __enter__(self) -> "DashboardDatabase":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
