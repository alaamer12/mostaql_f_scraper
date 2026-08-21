"""Pandas DataFrame schema and vectorized validation for Mostaql profiles."""

from typing import Any, Dict, List, Optional
import pandas as pd
from pandas.api.types import CategoricalDtype

from src.schema.spec import FIELD_SPECS


def pandas_dtypes() -> Dict[str, Any]:
    """Return dictionary of column -> pandas dtype derived from FIELD_SPECS."""
    dtypes: Dict[str, Any] = {}
    for name, spec in FIELD_SPECS.items():
        dtypes[name] = spec.type.pandas_dtype
    return dtypes


def apply_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Safely apply declared pandas dtypes to a DataFrame."""
    df_out = df.copy()
    type_map = pandas_dtypes()

    for col, dtype in type_map.items():
        if col not in df_out.columns:
            continue
        try:
            if isinstance(dtype, CategoricalDtype):
                df_out[col] = df_out[col].astype(dtype)
            elif dtype in ["Int64", "int64"]:
                df_out[col] = pd.to_numeric(df_out[col], errors="coerce").round().astype("Int64")
            elif dtype in ["Float64", "float64"]:
                df_out[col] = pd.to_numeric(df_out[col], errors="coerce").astype("Float64")
            elif dtype in ["string", "str"]:
                df_out[col] = df_out[col].astype("string")
        except Exception:
            # Fallback gracefully
            pass

    return df_out


def validate_frame(df: pd.DataFrame) -> Dict[str, Any]:
    """Perform vectorized validation on a profile DataFrame.
    
    Returns summary statistics, outlier masks, null counts, and integrity flags.
    """
    report: Dict[str, Any] = {
        "total_rows": len(df),
        "column_reports": {},
        "coherence_violations": 0,
        "outlier_counts": {},
    }

    if df.empty:
        return report

    # 1. Per-column bounds and null checks
    for col, spec in FIELD_SPECS.items():
        if col not in df.columns:
            continue

        s = df[col]
        null_count = int(s.isna().sum())
        col_rep: Dict[str, Any] = {
            "dtype": str(s.dtype),
            "null_count": null_count,
            "null_pct": round((null_count / len(df)) * 100, 2),
            "outliers": 0,
        }

        # Numeric bounds
        val_type = spec.type
        if hasattr(val_type, "min") and hasattr(val_type, "max") and val_type.max is not None:
            num_s = pd.to_numeric(s, errors="coerce")
            valid_mask = num_s.notna()
            out_mask = valid_mask & (~num_s.between(val_type.min, val_type.max))
            out_count = int(out_mask.sum())
            col_rep["outliers"] = out_count
            report["outlier_counts"][col] = out_count

        if hasattr(val_type, "soft_max") and val_type.soft_max is not None:
            num_s = pd.to_numeric(s, errors="coerce")
            valid_mask = num_s.notna()
            out_mask = valid_mask & (num_s > val_type.soft_max)
            out_count = int(out_mask.sum())
            col_rep["soft_max_exceeded"] = out_count

        report["column_reports"][col] = col_rep

    # 2. Relational coherence checks
    coherence_issues = 0
    if "total_completed_projects" in df.columns and "received_projects" in df.columns:
        comp = pd.to_numeric(df["total_completed_projects"], errors="coerce").fillna(0)
        recv = pd.to_numeric(df["received_projects"], errors="coerce").fillna(0)
        incoherent = recv < comp
        coherence_issues += int(incoherent.sum())

    report["coherence_violations"] = coherence_issues
    return report
