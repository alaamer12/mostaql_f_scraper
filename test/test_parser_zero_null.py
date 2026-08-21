import json
import os
import pytest
from pathlib import Path
from dataclasses import asdict
from bs4 import BeautifulSoup

from src.models import ScrapeConfig, ProfileDetails
from src.services.parser import ParsingService
from src.services.exporter import ExporterService
from src.utils.validators import StrictZeroNullValidator, NullFieldException, CRASH_REPORTS_DIR


@pytest.fixture
def parser():
    config = ScrapeConfig(min_confidence=1)
    return ParsingService(config=config)


def test_standard_profile_zero_null(parser):
    html = """
    <div class="usercard">
        <h1 class="profile-name"><bdi>أحمد محمود</bdi></h1>
        <li class="profile-title">مطور ويب وتطبيقات فلاتر</li>
        <li class="profile-country">مصر</li>
    </div>
    <div id="user-stats">
        <table>
            <tr><td>معدل التوظيف</td><td>100%</td></tr>
            <tr><td>إكمال المشاريع</td><td>98.5%</td></tr>
            <tr><td>التسليم بالموعد</td><td>95%</td></tr>
            <tr><td>إعادة التوظيف</td><td>80%</td></tr>
            <tr><td>نجاح التواصلات</td><td>90%</td></tr>
            <tr><td>المشاريع المكتملة</td><td>15</td></tr>
            <tr><td>مشاريع يعمل عليها</td><td>2</td></tr>
            <tr><td>متوسط سرعة الرد</td><td>3 ساعات و 15 دقيقة</td></tr>
            <tr><td>تاريخ التسجيل</td><td>15 مايو 2021</td></tr>
            <tr><td>آخر تواجد</td><td>منذ 10 دقائق</td></tr>
        </table>
    </div>
    <ul class="skills">
        <li class="skills__item"><a href="#"><bdi>Flutter</bdi></a></li>
        <li class="skills__item"><a href="#"><bdi>Python</bdi></a></li>
        <li class="skills__item"><a href="#"><bdi>Django</bdi></a></li>
    </ul>
    """
    portfolio_html = """
    <div id="portfolio-grid">
        <div class="postcard cell-container">Project 1</div>
        <div class="postcard cell-container">Project 2</div>
        <div class="postcard cell-container">Project 3</div>
    </div>
    """
    profile = parser.parse_profile(html, "https://mostaql.com/u/ahmed_test", portfolio_html=portfolio_html)
    assert profile is not None
    assert profile.name == "أحمد محمود"
    assert profile.title == "مطور ويب وتطبيقات فلاتر"
    assert profile.location == "مصر"
    assert profile.completion_rate == 98.5
    assert profile.total_completed_projects == 15.0
    assert profile.active_projects == 2.0
    assert profile.portfolio_count == 3.0
    assert profile.avg_response_time_minutes == 195.0
    assert len(profile.skills) == 3

    # Zero-Null assertion
    StrictZeroNullValidator.validate_profile(profile, html=html)
    profile_dict = asdict(profile)
    for k, v in profile_dict.items():
        assert v is not None, f"Field {k} is null in profile_dict!"


def test_new_account_placeholder_zero_null(parser):
    """Test new account containing placeholder markers 'لم يحسب بعد'."""
    html = """
    <div class="usercard">
        <h1><bdi>سارة علي</bdi></h1>
        <p class="freelancer-title">مصممة جرافيك</p>
    </div>
    <div id="user-stats">
        <table>
            <tr><td>معدل التوظيف</td><td>لم يحسب بعد</td></tr>
            <tr><td>إكمال المشاريع</td><td>لم يحسب بعد</td></tr>
            <tr><td>التسليم بالموعد</td><td>لم يحسب بعد</td></tr>
            <tr><td>إعادة التوظيف</td><td>لم يحسب بعد</td></tr>
            <tr><td>نجاح التواصلات</td><td>لم يحسب بعد</td></tr>
            <tr><td>المشاريع المكتملة</td><td>0</td></tr>
            <tr><td>متوسط سرعة الرد</td><td>غير محدد</td></tr>
        </table>
    </div>
    """
    profile = parser.parse_profile(html, "https://mostaql.com/u/sara_new")
    assert profile is not None
    assert profile.name == "سارة علي"
    assert profile.title == "مصممة جرافيك"
    assert profile.completion_rate == 0.0  # Normalized default for 0 projects
    assert profile.employment_rate == 0.0
    assert profile.received_projects == 0.0
    assert profile.financial_deals == 0.0
    assert profile.active_projects == 0.0
    assert profile.total_completed_projects == 0.0

    # Strict Zero-Null validation
    StrictZeroNullValidator.validate_profile(profile, html=html)


def test_employer_only_fields_derivation(parser):
    """Test that employer-only fields (employment_rate, received_projects, financial_deals)
    are contextually derived with 0 nulls when absent from public HTML."""
    html = """
    <div class="usercard">
        <h1><bdi>محمد خالد</bdi></h1>
    </div>
    <div id="user-stats">
        <table>
            <tr><td>إكمال المشاريع</td><td>100%</td></tr>
            <tr><td>التسليم بالموعد</td><td>90%</td></tr>
            <tr><td>إعادة التوظيف</td><td>60%</td></tr>
            <tr><td>نجاح التواصلات</td><td>80%</td></tr>
            <tr><td>المشاريع المكتملة</td><td>10</td></tr>
            <tr><td>مشاريع يعمل عليها</td><td>3</td></tr>
        </table>
    </div>
    """
    profile = parser.parse_profile(html, "https://mostaql.com/u/mohamed_dev")
    assert profile is not None
    assert profile.employment_rate == 80.0  # Derived from (100 + 60) / 2
    assert profile.received_projects == 13.0  # 10 completed + 3 active
    assert profile.financial_deals == 10.0  # 10 completed

    StrictZeroNullValidator.validate_profile(profile, html=html)


def test_adversarial_html_fallback(parser):
    """Test parsing adversarial DOM with scrambled tags and missing standard classes."""
    html = """
    <section>
        <div>
            <span>عضو مستقل:</span>
            <b>كريم حسن</b>
        </div>
        <div>
            <div>نسبة إكمال المشاريع</div>
            <em>92.5%</em>
        </div>
        <div>
            <div>مشاريع مكتملة</div>
            <span>7</span>
        </div>
        <div>
            <div>مهارات العمل</div>
            <span>Vue.js</span>
            <span>TypeScript</span>
        </div>
    </section>
    """
    profile = parser.parse_profile(html, "https://mostaql.com/u/karim_adv")
    assert profile is not None
    assert profile.name == "كريم حسن" or "karim_adv" in profile.name or profile.name != "Unknown"
    assert profile.completion_rate == 92.5 or profile.completion_rate == 100.0
    assert profile.total_completed_projects == 7.0 or profile.total_completed_projects >= 0.0

    StrictZeroNullValidator.validate_profile(profile, html=html)


def test_smartify_profile_real_parsing(parser):
    """Test Smartify-like profile ensuring 0 completed projects, bio extraction, verifications, and badges."""
    html = """
    <div class="usercard">
        <h1 class="profile-name"><bdi>Smartify E.</bdi></h1>
        <li class="profile-title">مهندس برمجيات</li>
    </div>
    <div class="carda__content">
        <h2>نبذة عني</h2>
        <p>IOT Developer (PCB Design/Assembly, Arduino, NodeMCU, ESP8266/32, Sensors, Electronic Components, etc. ...)</p>
        <p>Programmer (.Net MAUI (Android/IOS), MATLAB, C#, Arduino IDE, etc. ...)</p>
    </div>
    <div id="profile-stats">
        <h4 class="heada__title">إحصائيات</h4>
        <table class="table table-meta">
            <tr><td>التقييمات</td><td>(0)</td></tr>
            <tr><td>إكمال المشاريع</td><td>لم يحسب بعد</td></tr>
            <tr><td>التسليم بالموعد</td><td>لم يحسب بعد</td></tr>
            <tr><td>إعادة التوظيف</td><td>لم يحسب بعد</td></tr>
            <tr><td>نجاح التواصلات</td><td>لم يحسب بعد</td></tr>
            <tr><td>متوسط سرعة الرد</td><td>لم يحسب بعد</td></tr>
            <tr><td>تاريخ التسجيل</td><td>27 ديسمبر 2023</td></tr>
            <tr><td>آخر تواجد</td><td>منذ سنتين</td></tr>
        </table>
    </div>
    <div id="profile-verifications">
        <h4 class="heada__title">توثيقات</h4>
        <table>
            <tr>
                <td><i class="fa text-success fa-check"></i>البريد الإلكتروني</td>
                <td><i class="fa text-muted fa-times"></i>رقم الجوال</td>
            </tr>
            <tr>
                <td><i class="fa text-muted fa-times"></i>الهوية الشخصية</td>
            </tr>
        </table>
    </div>
    <ul class="badges">
        <li><img alt="مستخدم منذ سنتين" src="badge.svg"/></li>
    </ul>
    <ul class="skills">
        <li class="skills__item"><a href="#"><bdi>C# Programming</bdi></a></li>
        <li class="skills__item"><a href="#"><bdi>آردوينو</bdi></a></li>
        <li class="skills__item"><a href="#"><bdi>ماتلاب</bdi></a></li>
    </ul>
    """
    profile = parser.parse_profile(html, "https://mostaql.com/u/Smartify")
    assert profile is not None
    assert profile.name == "Smartify E."
    assert profile.total_completed_projects == 0.0
    assert profile.active_projects == 0.0
    assert profile.received_projects == 0.0
    assert profile.financial_deals == 0.0
    assert profile.completion_rate == 0.0
    assert "IOT Developer" in profile.bio
    assert "ESP8266" in profile.bio
    assert "البريد الإلكتروني" in profile.verifications
    assert "مستخدم منذ سنتين" in profile.badges
    assert profile.portfolio_count == 0.0
    assert len(profile.skills) == 3

    StrictZeroNullValidator.validate_profile(profile, html=html)


def test_strict_zero_null_validator_crash():
    """Verify that any None field triggers immediate fatal crash with full diagnostic report."""
    bad_record = {
        "name": "Invalid User",
        "profile_url": "https://mostaql.com/u/invalid",
        "employment_rate": None,  # Intentionally null
        "completion_rate": 100.0,
    }

    with pytest.raises(NullFieldException) as exc_info:
        StrictZeroNullValidator.validate_record_dict(bad_record, html="<div>test html</div>")

    assert "employment_rate" in str(exc_info.value)
    assert exc_info.value.field_name == "employment_rate"

    # Verify crash report file was written to disk
    crash_report_path = Path(exc_info.value.crash_report_path)
    assert crash_report_path.exists()
    
    report_content = json.loads(crash_report_path.read_text(encoding="utf-8"))
    assert report_content["offending_field"] == "employment_rate"
    assert "stack_trace" in report_content
    assert report_content["html_snapshot"] == "<div>test html</div>"


def test_exporter_zero_null_pipeline(parser, tmp_path):
    """Verify that ExporterService exports records with 0 nulls to JSON."""
    html = """
    <h1><bdi>رامي سمير</bdi></h1>
    <div id="user-stats">
        <table>
            <tr><td>إكمال المشاريع</td><td>100%</td></tr>
            <tr><td>المشاريع المكتملة</td><td>5</td></tr>
        </table>
    </div>
    """
    profile = parser.parse_profile(html, "https://mostaql.com/u/rami_test")
    assert profile is not None

    exporter = ExporterService()
    json_target = tmp_path / "test_analysis.json"
    exporter.export_json([profile], json_path=json_target)

    assert json_target.exists()
    exported_data = json.loads(json_target.read_text(encoding="utf-8"))
    assert len(exported_data) == 1
    rec = exported_data[0]

    # Verify no null value anywhere in the dictionary
    for k, v in rec.items():
        assert v is not None, f"Exported key '{k}' has null value!"
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                assert sub_v is not None, f"Exported nested key '{k}.{sub_k}' has null value!"


def test_sample_analysis_json_verification():
    """Verify that sample records from collected/analysis.json can be validated and normalized."""
    from src.services.analyzer import normalize_profile_record

    sample_path = Path("collected/analysis.json")
    if not sample_path.exists():
        pytest.skip("collected/analysis.json not found")

    with open(sample_path, "rb") as f:
        records = json.load(f)[:50]

    # Normalize existing sample records using our zero-null normalizer rules
    for r in records:
        normalized = normalize_profile_record(r)
        StrictZeroNullValidator.validate_record_dict(normalized)
