"""Tests for schema specification, coherence rules, and dataframe operations."""

import pandas as pd
import pytest

from src.schema.spec import FIELD_SPECS, check_record_coherence
from src.schema.frame import pandas_dtypes, apply_dtypes, validate_frame


def test_field_specs_completeness():
    # Verify all expected keys are declared in FIELD_SPECS
    expected_keys = [
        "name", "profile_url", "title", "location", "bio", "skills",
        "skills_count", "portfolio_count", "verifications", "badges",
        "rating", "reviews_count", "completion_rate", "ontime_delivery_rate",
        "rehire_rate", "communication_success_rate", "employment_rate",
        "total_completed_projects", "active_projects", "received_projects",
        "financial_deals", "avg_response_time_minutes", "registration_date",
        "rank", "scraped_at", "parse_confidence",
    ]
    for k in expected_keys:
        assert k in FIELD_SPECS
        assert FIELD_SPECS[k].type is not None


def test_coherence_rules():
    # 1. Normal coherent stats
    coherent_stats = {
        "total_completed_projects": 10,
        "received_projects": 12,
        "reviews_count": 8,
        "rating": 4.5,
        "completion_rate": 90.0,
        "ontime_delivery_rate": 100.0,
        "rehire_rate": 20.0,
        "communication_success_rate": 80.0,
    }
    assert check_record_coherence(coherent_stats) == []

    # 2. Incoherent: received < completed
    incoherent_recv = {
        "total_completed_projects": 10,
        "received_projects": 5,
        "reviews_count": 5,
        "rating": 4.0,
    }
    issues = check_record_coherence(incoherent_recv)
    assert "incoherent_received_less_than_completed" in issues

    # 3. Incoherent: 0 completed projects but fabricated non-zero rates
    incoherent_rates = {
        "total_completed_projects": 0,
        "received_projects": 0,
        "reviews_count": 0,
        "rating": 0.0,
        "completion_rate": 100.0,
    }
    issues = check_record_coherence(incoherent_rates)
    assert "incoherent_rates_with_zero_projects" in issues

    # 4. Incoherent: 0 reviews but non-zero rating
    incoherent_rating = {
        "total_completed_projects": 5,
        "received_projects": 5,
        "reviews_count": 0,
        "rating": 4.5,
    }
    issues = check_record_coherence(incoherent_rating)
    assert "incoherent_rating_with_zero_reviews" in issues


def test_frame_dtypes_and_validation():
    data = [
        {
            "name": "Freelancer A",
            "profile_url": "https://mostaql.com/u/user1",
            "total_completed_projects": 15,
            "received_projects": 20,
            "completion_rate": 95.0,
            "rating": 4.8,
            "reviews_count": 10,
        },
        {
            "name": "Freelancer B",
            "profile_url": "https://mostaql.com/u/user2",
            "total_completed_projects": 600,  # exceeds soft_max
            "received_projects": 500,  # incoherent: received < completed
            "completion_rate": 150.0,  # exceeds max 100.0
            "rating": 6.0,  # exceeds max 5.0
            "reviews_count": 50,
        },
    ]
    df = pd.DataFrame(data)
    df_typed = apply_dtypes(df)
    
    assert df_typed["total_completed_projects"].dtype.name == "Int64"
    assert df_typed["completion_rate"].dtype.name == "Float64"

    rep = validate_frame(df_typed)
    assert rep["total_rows"] == 2
    assert rep["coherence_violations"] == 1
    assert rep["outlier_counts"]["completion_rate"] == 1
    assert rep["outlier_counts"]["rating"] == 1
    assert rep["column_reports"]["total_completed_projects"]["soft_max_exceeded"] == 1
