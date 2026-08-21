"""Unit and integration tests for the DuckDB analytics dashboard sub-module."""

import os
import json
import tempfile
import pytest
import pandas as pd
from pathlib import Path

from dashboard.config import DashboardConfig, get_default_config
from dashboard.db.connection import DashboardDatabase
from dashboard.db.sources import DatasetSourceManager
from dashboard.db.schema import SchemaInspector, FieldCapabilityChecker
from dashboard import analyzer
from dashboard import plotter
from dashboard.dashboard import DuckDBAnalyticsDashboard


@pytest.fixture
def sample_json_path(tmp_path):
    """Create a mock JSON dataset fixture."""
    data = [
        {
            "rank": 1,
            "name": "Alice Developer",
            "title": "Full Stack Engineer",
            "category": "development",
            "location": "Egypt",
            "total_completed_projects": 50.0,
            "active_projects": 2.0,
            "completion_rate": 100.0,
            "ontime_delivery_rate": 95.0,
            "rehire_rate": 40.0,
            "communication_success_rate": 90.0,
            "success_score": 96.5,
            "avg_response_time_minutes": 30.0,
            "portfolio_count": 12.0,
            "skills": ["Python", "FastAPI", "React", "SQL"],
            "skills_count": 4.0,
            "parse_confidence": "ok",
            "registration_date": "2022-01-15T00:00:00",
        },
        {
            "rank": 2,
            "name": "Bob Designer",
            "title": "UI/UX Designer",
            "category": "design",
            "location": "Saudi Arabia",
            "total_completed_projects": 20.0,
            "active_projects": 1.0,
            "completion_rate": 90.0,
            "ontime_delivery_rate": 85.0,
            "rehire_rate": 25.0,
            "communication_success_rate": 80.0,
            "success_score": 88.0,
            "avg_response_time_minutes": 60.0,
            "portfolio_count": 25.0,
            "skills": ["Figma", "UI UX", "Photoshop"],
            "skills_count": 3.0,
            "parse_confidence": "ok",
            "registration_date": "2022-06-20T00:00:00",
        },
        {
            "rank": 3,
            "name": "Charlie Writer",
            "title": "Content Creator",
            "category": "writing",
            "location": "Egypt",
            "total_completed_projects": 0.0,
            "active_projects": 0.0,
            "completion_rate": None,
            "ontime_delivery_rate": None,
            "rehire_rate": None,
            "communication_success_rate": None,
            "success_score": 50.0,
            "avg_response_time_minutes": 180.0,
            "portfolio_count": 2.0,
            "skills": ["Copywriting", "SEO"],
            "skills_count": 2.0,
            "parse_confidence": "warning",
            "registration_date": "2023-03-10T00:00:00",
        },
    ]
    file_path = tmp_path / "mock_dataset.json"
    file_path.write_text(json.dumps(data), encoding="utf-8")
    return file_path


def test_duckdb_connection_and_parquet_cache(tmp_path, sample_json_path):
    """Verify DuckDB connection, SQL execution, and one-time Parquet conversion cache."""
    cfg = DashboardConfig(
        base_dir=tmp_path,
        collected_dir=tmp_path,
        cache_dir=tmp_path / "cache",
    )
    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        parquet_path = src.ensure_parquet_cache(db, sample_json_path)
        assert parquet_path.exists()
        assert parquet_path.suffix == ".parquet"

        # Register view from parquet
        view_name = src.register_dataset(db, "test_table", sample_json_path, use_parquet_cache=True)
        assert view_name == "test_table"
        assert db.table_exists("test_table")

        total = db.query_scalar("SELECT COUNT(*) FROM test_table")
        assert total == 3


def test_schema_discovery_and_fallback(tmp_path, sample_json_path):
    """Verify dynamic schema inspection and capability checks."""
    cfg = DashboardConfig(base_dir=tmp_path, cache_dir=tmp_path / "cache")
    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        src.register_dataset(db, "test_table", sample_json_path, use_parquet_cache=False)

        cols = SchemaInspector.get_column_names(db, "test_table")
        assert "rank" in cols
        assert "name" in cols
        assert "category" in cols

        cap = FieldCapabilityChecker(db, "test_table")
        assert cap.has_projects()
        assert cap.has_category()
        assert cap.has_skills()
        assert cap.has_temporal()
        assert cap.has_location()
        assert len(cap.get_numeric_columns()) >= 5


def test_overview_and_kpi_analyzer(tmp_path, sample_json_path):
    """Verify KPI extraction, missing data, and completeness scoring in DuckDB."""
    cfg = DashboardConfig(base_dir=tmp_path, cache_dir=tmp_path / "cache")
    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        src.register_dataset(db, "test_table", sample_json_path, use_parquet_cache=False)

        kpis = analyzer.get_dataset_kpis(db, "test_table")
        assert kpis["total_users"] == 3
        assert kpis["total_completed_projects"] == 70.0
        assert kpis["users_with_projects"] == 2
        assert kpis["users_without_projects"] == 1
        assert kpis["unique_categories"] == 3

        missing_df = analyzer.get_missing_data_by_field(db, "test_table")
        assert not missing_df.empty
        assert "missing_percentage" in missing_df.columns

        comp_df = analyzer.get_data_completeness_distribution(db, "test_table")
        assert not comp_df.empty


def test_distribution_and_binning_analyzer(tmp_path, sample_json_path):
    """Verify SQL-calculated range binning, histograms, Pareto, and outlier calculations."""
    cfg = DashboardConfig(base_dir=tmp_path, cache_dir=tmp_path / "cache")
    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        src.register_dataset(db, "test_table", sample_json_path, use_parquet_cache=False)

        ranges_df = analyzer.get_project_count_ranges(db, "test_table")
        assert not ranges_df.empty
        assert "project_range" in ranges_df.columns
        assert ranges_df["user_count"].sum() == 3

        hist_df = analyzer.get_project_count_histogram(db, "test_table", num_bins=5)
        assert not hist_df.empty

        ecdf_df = analyzer.get_cumulative_user_distribution(db, "test_table")
        assert not ecdf_df.empty
        assert ecdf_df.iloc[-1]["cum_percentage"] == 100.0

        pareto_df = analyzer.get_pareto_project_activity(db, "test_table")
        assert not pareto_df.empty
        assert pareto_df.iloc[-1]["cum_project_pct"] == 100.0

        outliers = analyzer.get_project_activity_outliers(db, "test_table")
        assert "median" in outliers
        assert "iqr" in outliers


def test_category_and_skills_unnest_analyzer(tmp_path, sample_json_path):
    """Verify category aggregations and list unnesting in DuckDB."""
    cfg = DashboardConfig(base_dir=tmp_path, cache_dir=tmp_path / "cache")
    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        src.register_dataset(db, "test_table", sample_json_path, use_parquet_cache=False)

        cat_proj = analyzer.get_projects_by_category(db, "test_table")
        assert not cat_proj.empty
        assert "development" in cat_proj["category"].values

        skills_df = analyzer.get_most_common_skills(db, "test_table", top_n=10)
        assert not skills_df.empty
        assert "Python" in skills_df["skill"].values

        corr_df = analyzer.get_numeric_correlations(db, "test_table")
        assert not corr_df.empty


def test_plotter_metadata_and_layout(tmp_path, sample_json_path):
    """Verify that every plotter component returns a valid ChartCard with metadata."""
    cfg = DashboardConfig(base_dir=tmp_path, cache_dir=tmp_path / "cache")
    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        src.register_dataset(db, "test_table", sample_json_path, use_parquet_cache=False)

        kpi_card = plotter.plot_dataset_kpis(analyzer.get_dataset_kpis(db, "test_table"))
        assert kpi_card.title
        assert kpi_card.description
        assert kpi_card.figure is not None
        assert kpi_card.section == "Overview"

        scatter_card = plotter.plot_user_project_scatter(
            analyzer.get_user_project_scatter_data(db, "test_table")
        )
        assert scatter_card.title == "Users vs. Number of Projects"
        assert scatter_card.section == "User Activity"


def test_full_dashboard_render(tmp_path, sample_json_path):
    """Verify orchestrator discovers schema, runs all 29 charts, and writes standalone single-column HTML."""
    out_html = tmp_path / "test_dashboard.html"
    dashboard = DuckDBAnalyticsDashboard()
    dashboard.register_custom_dataset("test_data", sample_json_path, use_parquet_cache=False)

    saved_path = dashboard.save_html(out_html, table_name="test_data", page_title="Test Analytics Dashboard")
    assert saved_path.exists()
    content = saved_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "Test Analytics Dashboard" in content
    assert "Users vs. Number of Projects" in content
    assert "plotly-2.35.2.min.js" in content


def test_production_collected_analysis():
    """Verify analytics execute cleanly on collected/analysis.json if present."""
    cfg = get_default_config()
    if not cfg.analysis_json.exists():
        pytest.skip("collected/analysis.json not found")

    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        src.register_dataset(db, "analysis", cfg.analysis_json, use_parquet_cache=False)

        kpis = analyzer.get_dataset_kpis(db, "analysis")
        assert kpis["total_users"] > 0
        assert kpis["total_completed_projects"] > 0

        skills = analyzer.get_most_common_skills(db, "analysis", top_n=5)
        assert not skills.empty


def test_large_dataset_profiles_zero_high_memory():
    """Verify analytics execute with minimal memory directly against multi-million row profiles.json."""
    cfg = get_default_config()
    if not cfg.profiles_json.exists():
        pytest.skip("collected/profiles.json not found")

    import tracemalloc
    tracemalloc.start()

    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        src.register_dataset(db, "profiles", cfg.profiles_json, use_parquet_cache=False)

        kpis = analyzer.get_dataset_kpis(db, "profiles")
        assert kpis["total_users"] > 0

        ranges = analyzer.get_project_count_ranges(db, "profiles")
        assert not ranges.empty

        current_ram, peak_ram = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Ensure Python process peak RAM during SQL execution stayed well under 256MB limit (e.g. < 50MB)
        assert peak_ram < 256 * 1024 * 1024


def test_dataset_concatenation_multiple_inputs(tmp_path):
    """Verify that multiple JSON input files are concatenated and unioned cleanly."""
    file1 = tmp_path / "part1.json"
    file2 = tmp_path / "part2.json"
    
    data1 = [{"rank": 1, "name": "User 1", "total_completed_projects": 10.0}]
    data2 = [{"rank": 2, "name": "User 2", "total_completed_projects": 25.0}]
    
    file1.write_text(json.dumps(data1), encoding="utf-8")
    file2.write_text(json.dumps(data2), encoding="utf-8")
    
    cfg = DashboardConfig(base_dir=tmp_path, cache_dir=tmp_path / "cache")
    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        view = src.register_datasets(db, "concat_view", [file1, file2], use_parquet_cache=True)
        assert view == "concat_view"
        
        count = db.query_scalar("SELECT COUNT(*) FROM concat_view")
        assert count == 2
        
        total_projects = db.query_scalar("SELECT SUM(total_completed_projects) FROM concat_view")
        assert total_projects == 35.0


def test_robust_fingerprint_and_cache_invalidation(tmp_path):
    """Verify that the file fingerprinting accurately detects modifications and invalidates cache."""
    json_file = tmp_path / "dynamic_data.json"
    data_initial = [{"rank": 1, "name": "Initial User", "total_completed_projects": 5.0}]
    json_file.write_text(json.dumps(data_initial), encoding="utf-8")
    
    cfg = DashboardConfig(base_dir=tmp_path, cache_dir=tmp_path / "cache")
    with DashboardDatabase(config=cfg) as db:
        src = DatasetSourceManager(config=cfg)
        
        # 1. First build creates cache
        parquet1 = src.ensure_parquet_cache(db, json_file)
        assert parquet1.exists()
        meta1 = src.get_meta_cache_path(parquet1)
        assert meta1.exists()
        
        # 2. Re-verifying unchanged file uses valid cache without rebuild
        assert src.is_cache_valid(parquet1, [json_file])
        
        # 3. Modifying file content & size changes fingerprint
        data_updated = [
            {"rank": 1, "name": "Initial User", "total_completed_projects": 5.0},
            {"rank": 2, "name": "New User Added", "total_completed_projects": 15.0},
        ]
        json_file.write_text(json.dumps(data_updated), encoding="utf-8")
        
        # Invalidation check must detect change
        assert not src.is_cache_valid(parquet1, [json_file])
        
        # Rebuilding updates the cache
        parquet2 = src.ensure_parquet_cache(db, json_file)
        assert src.is_cache_valid(parquet2, [json_file])
