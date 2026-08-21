"""Golden expectation tests driven by the real HTML fixture corpus."""

import os
import pytest
from src.models import ScrapeConfig
from src.services.parser import ParsingService
from src.utils.validators import StrictZeroNullValidator, SchemaValidator


@pytest.fixture
def parser():
    config = ScrapeConfig(min_confidence=1)
    return ParsingService(config=config)


def load_fixture(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "profiles", f"{name}.html")
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def test_smartify_fixture_golden(parser):
    html = load_fixture("smartify_empty")
    profile = parser.parse_profile(html, "https://mostaql.com/u/Smartify")

    assert profile is not None
    assert profile.name == "Smartify E."
    assert profile.avatar_url == "https://avatars.hsoubcdn.com/a89e3c087eae6b3490a70eb8442bbe57?s=256"
    assert profile.metadata.fields["avatar_url"].source == "dom_structural"
    assert profile.title == "مهندس برمجيات"
    assert "IOT Developer" in profile.bio
    assert "ESP8266" in profile.bio

    # Numeric metrics MUST be zero and never take numbers from bio
    assert profile.stats.total_completed_projects == 0.0
    assert profile.stats.active_projects == 0.0
    assert profile.stats.received_projects == 0.0
    assert profile.stats.financial_deals == 0.0
    assert profile.stats.completion_rate == 0.0
    assert profile.stats.ontime_delivery_rate == 0.0
    assert profile.stats.rehire_rate == 0.0
    assert profile.stats.communication_success_rate == 0.0
    assert profile.stats.employment_rate == 0.0
    assert profile.stats.rating == 0.0
    assert profile.stats.reviews_count == 0

    # Verifications & Badges
    assert "البريد الإلكتروني" in profile.verifications
    assert any("سنتين" in b for b in profile.badges)

    # Metadata & Quality
    assert profile.metadata.quality == "ok"
    assert "8266" not in [str(v) for v in profile.stats.model_dump().values()]

    StrictZeroNullValidator.validate_profile(profile, html=html)
    assert len(SchemaValidator.validate_profile(profile)) == 0


def test_david_heavy_fixture_golden(parser):
    html = load_fixture("david_heavy")
    profile = parser.parse_profile(html, "https://mostaql.com/u/DavidLabib")

    assert profile is not None
    assert "ديفيد" in profile.name
    assert profile.stats.total_completed_projects == 113.0
    assert profile.stats.completion_rate == 100.0
    assert profile.stats.ontime_delivery_rate == 100.0
    assert profile.stats.rehire_rate == 41.33
    assert profile.stats.avg_response_time_minutes == 44.0
    assert profile.metadata.quality == "ok"

    StrictZeroNullValidator.validate_profile(profile, html=html)
    assert len(SchemaValidator.validate_profile(profile)) == 0


def test_dalia_mid_fixture_golden(parser):
    html = load_fixture("dalia_mid")
    profile = parser.parse_profile(html, "https://mostaql.com/u/dalia1010")

    assert profile is not None
    assert profile.name == "Dalia A."
    assert profile.stats.total_completed_projects == 47.0
    assert profile.stats.active_projects == 1.0
    assert profile.stats.received_projects == 48.0
    assert profile.stats.completion_rate == 97.92
    assert profile.stats.avg_response_time_minutes == 82.0
    assert profile.metadata.quality == "ok"

    StrictZeroNullValidator.validate_profile(profile, html=html)
    assert len(SchemaValidator.validate_profile(profile)) == 0


def test_basel_low_fixture_golden(parser):
    html = load_fixture("basel_low")
    profile = parser.parse_profile(html, "https://mostaql.com/u/basel_amin_77")

    assert profile is not None
    assert profile.name == "Basel A."
    assert profile.stats.total_completed_projects == 7.0
    assert profile.stats.completion_rate == 100.0
    assert profile.stats.avg_response_time_minutes == 228.0
    assert profile.metadata.quality == "ok"

    StrictZeroNullValidator.validate_profile(profile, html=html)
    assert len(SchemaValidator.validate_profile(profile)) == 0


def test_starmido_top_fixture_golden(parser):
    html = load_fixture("starmido_top")
    profile = parser.parse_profile(html, "https://mostaql.com/u/starmidopro")

    assert profile is not None
    assert "محمد" in profile.name
    assert profile.stats.total_completed_projects == 106.0
    assert profile.stats.completion_rate == 92.17
    assert profile.stats.avg_response_time_minutes == 400.0
    assert profile.metadata.quality == "ok"

    StrictZeroNullValidator.validate_profile(profile, html=html)
    assert len(SchemaValidator.validate_profile(profile)) == 0


def test_bio_token_leak_regression(parser):
    """Crafted HTML with random tokens and numbers in bio that must never leak into stats."""
    crafted_html = """
    <div class="usercard">
        <h1 class="profile-name">Test User</h1>
        <li class="profile-title">Developer</li>
    </div>
    <div id="about_content">
        <p>I built ESP8266 modules in 2019 with 100% success across 9999 devices and 8266 microchips!</p>
    </div>
    <div id="profile-stats">
        <table class="table table-meta">
            <tr><td>التقييمات</td><td>(0)</td></tr>
            <tr><td>إكمال المشاريع</td><td>لم يحسب بعد</td></tr>
            <tr><td>التسليم بالموعد</td><td>لم يحسب بعد</td></tr>
            <tr><td>إعادة التوظيف</td><td>لم يحسب بعد</td></tr>
            <tr><td>نجاح التواصلات</td><td>لم يحسب بعد</td></tr>
            <tr><td>متوسط سرعة الرد</td><td>لم يحسب بعد</td></tr>
            <tr><td>تاريخ التسجيل</td><td>15 يناير 2024</td></tr>
        </table>
    </div>
    """
    profile = parser.parse_profile(crafted_html, "https://mostaql.com/u/test_crafted")
    assert profile is not None
    assert profile.stats.total_completed_projects == 0.0
    assert profile.stats.completion_rate == 0.0
    assert profile.stats.rehire_rate == 0.0
    assert profile.stats.active_projects == 0.0
    assert profile.stats.received_projects == 0.0
    assert profile.stats.financial_deals == 0.0
    assert "ESP8266" in profile.bio
    assert profile.metadata.quality == "ok"


def test_outlier_keep_raw_and_flag_policy(parser):
    """When out-of-bounds or extreme values are present in DOM, keep raw and flag."""
    crafted_html = """
    <div class="usercard">
        <h1 class="profile-name">Outlier User</h1>
    </div>
    <div id="profile-stats">
        <table class="table table-meta">
            <tr><td>المشاريع المكتملة</td><td>9999</td></tr>
            <tr><td>إكمال المشاريع</td><td>150%</td></tr>
        </table>
    </div>
    """
    profile = parser.parse_profile(crafted_html, "https://mostaql.com/u/outlier_user")
    assert profile is not None
    # Kept as-is
    assert profile.stats.total_completed_projects == 9999.0
    assert profile.stats.completion_rate == 150.0

    # Quality downgraded and outlier flagged
    assert profile.metadata.quality in ["suspect", "bad"]
    assert "total_completed_projects" in profile.metadata.outlier_fields
    assert "completion_rate" in profile.metadata.outlier_fields


def test_avatar_extraction_and_missing_fallback(parser):
    """Test avatar extraction for present, relative, and missing avatar profiles."""
    # 1. Profile with avatar in standard container
    html_with_avatar = """
    <div class="usercard">
        <h1 class="profile-name">User Avatar</h1>
        <div class="profile-card--avatar">
            <img class="profile-avatar uavatar" src="https://avatars.hsoubcdn.com/testavatar123?s=256"/>
        </div>
    </div>
    """
    p1 = parser.parse_profile(html_with_avatar, "https://mostaql.com/u/user1")
    assert p1 is not None
    assert p1.avatar_url == "https://avatars.hsoubcdn.com/testavatar123?s=256"
    assert p1.metadata.fields["avatar_url"].source == "dom_structural"

    # 2. Profile with protocol-relative avatar URL
    html_relative = """
    <div class="usercard">
        <h1 class="profile-name">User Relative</h1>
        <img class="uavatar" src="//avatars.hsoubcdn.com/relativeavatar?s=256"/>
    </div>
    """
    p2 = parser.parse_profile(html_relative, "https://mostaql.com/u/user2")
    assert p2 is not None
    assert p2.avatar_url == "https://avatars.hsoubcdn.com/relativeavatar?s=256"

    # 3. Profile without avatar (e.g., placeholder or no img tag)
    html_no_avatar = """
    <div class="usercard">
        <h1 class="profile-name">No Avatar User</h1>
        <div class="profile-card--avatar">
            <i class="fa fa-user"></i>
        </div>
    </div>
    """
    p3 = parser.parse_profile(html_no_avatar, "https://mostaql.com/u/1Abdulrahman_IT")
    assert p3 is not None
    assert p3.avatar_url == ""
    assert p3.metadata.fields["avatar_url"].source == "default"
    StrictZeroNullValidator.validate_profile(p3, html=html_no_avatar)
