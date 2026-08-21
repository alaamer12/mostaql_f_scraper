"""Field specifications and single source of truth for all profile data attributes."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Tuple, Union

from src.schema.types import (
    ArabicDate,
    Count,
    Duration,
    Enum,
    ListOf,
    Money,
    OneOf,
    Percentage,
    Rating,
    RelativeTime,
    Text,
    ValueType,
)

GroupType = Literal["identity", "stats", "content", "meta"]


@dataclass(frozen=True)
class FieldSpec:
    """Specification defining domain, type, labels, and provenance rules for a field."""
    name: str
    group: GroupType
    type: ValueType
    labels: List[str] = field(default_factory=list)
    derived_from: Tuple[str, ...] = ()
    required: bool = False
    description: str = ""


FIELD_SPECS: Dict[str, FieldSpec] = {
    # --- Identity & Profile Fields ---
    "name": FieldSpec(
        name="name",
        group="identity",
        type=Text(min_len=1, default="مستقل", normalize_arabic=True),
        required=True,
        description="Profile display name",
    ),
    "profile_url": FieldSpec(
        name="profile_url",
        group="identity",
        type=Text(min_len=5, default=""),
        required=True,
        description="Profile canonical URL",
    ),
    "avatar_url": FieldSpec(
        name="avatar_url",
        group="identity",
        type=Text(default=""),
        description="Profile avatar image URL",
    ),
    "category": FieldSpec(
        name="category",
        group="identity",
        type=Enum(
            allowed=[
                "development", "design", "writing", "translation",
                "business", "marketing", "engineering", "support", "all"
            ],
            default="development",
        ),
        description="Primary freelance category",
    ),
    "title": FieldSpec(
        name="title",
        group="identity",
        type=Text(default="مستقل", normalize_arabic=True),
        description="Professional title or headline",
    ),
    "location": FieldSpec(
        name="location",
        group="identity",
        type=Text(default="غير محدد", normalize_arabic=True),
        description="User country / city",
    ),
    "verifications": FieldSpec(
        name="verifications",
        group="identity",
        type=ListOf(Text()),
        labels=["توثيق", "توثيقات"],
        description="Verified identity credentials (email, phone, ID)",
    ),
    "badges": FieldSpec(
        name="badges",
        group="identity",
        type=ListOf(Text()),
        labels=["أوسمة", "اوسمة"],
        description="Achievements and seniority badges",
    ),

    # --- Content & Portfolio ---
    "bio": FieldSpec(
        name="bio",
        group="content",
        type=Text(default=""),
        labels=["نبذة عني", "عني", "نبذة"],
        description="Freelancer self-biography",
    ),
    "skills": FieldSpec(
        name="skills",
        group="content",
        type=ListOf(Text()),
        labels=["مهارات", "المهارات"],
        description="List of declared professional skills",
    ),
    "skills_count": FieldSpec(
        name="skills_count",
        group="content",
        type=Count(min=0, soft_max=100, hard_max=500, default=0),
        derived_from=("skills",),
        description="Total declared skills",
    ),
    "skills_str": FieldSpec(
        name="skills_str",
        group="content",
        type=Text(default=""),
        derived_from=("skills",),
        description="Comma-separated skills string",
    ),
    "portfolio_count": FieldSpec(
        name="portfolio_count",
        group="content",
        type=Count(min=0, soft_max=200, hard_max=2000, default=0),
        labels=["أعمال", "اعمال", "معرض الأعمال"],
        description="Count of portfolio showcase items",
    ),

    # --- Stats (Metrics & Temporal) ---
    "rating": FieldSpec(
        name="rating",
        group="stats",
        type=Rating(min=0.0, max=5.0, default=0.0),
        labels=["التقييمات", "تقييم"],
        description="Average client satisfaction rating out of 5.0",
    ),
    "reviews_count": FieldSpec(
        name="reviews_count",
        group="stats",
        type=Count(min=0, soft_max=500, hard_max=5000, default=0),
        labels=["التقييمات", "تقييم"],
        description="Total client reviews received",
    ),
    "completion_rate": FieldSpec(
        name="completion_rate",
        group="stats",
        type=Percentage(min=0.0, max=100.0, default=0.0),
        labels=["إكمال المشاريع", "اكمال المشاريع", "معدل إكمال المشاريع"],
        description="Project completion rate percentage",
    ),
    "ontime_delivery_rate": FieldSpec(
        name="ontime_delivery_rate",
        group="stats",
        type=Percentage(min=0.0, max=100.0, default=0.0),
        labels=["التسليم بالموعد", "التسليم في الموعد", "معدل التسليم بالموعد"],
        description="On-time delivery percentage",
    ),
    "rehire_rate": FieldSpec(
        name="rehire_rate",
        group="stats",
        type=Percentage(min=0.0, max=100.0, default=0.0),
        labels=["إعادة التوظيف", "اعادة التوظيف", "معدل إعادة التوظيف"],
        description="Client rehire rate percentage",
    ),
    "communication_success_rate": FieldSpec(
        name="communication_success_rate",
        group="stats",
        type=Percentage(min=0.0, max=100.0, default=0.0),
        labels=["نجاح التواصلات", "معدل نجاح التواصل"],
        description="Communication responsiveness rate percentage",
    ),
    "employment_rate": FieldSpec(
        name="employment_rate",
        group="stats",
        type=Percentage(min=0.0, max=100.0, default=0.0),
        labels=["معدل التوظيف"],
        derived_from=("completion_rate", "rehire_rate"),
        description="Employer employment rate percentage",
    ),
    "total_completed_projects": FieldSpec(
        name="total_completed_projects",
        group="stats",
        type=Count(min=0, soft_max=500, hard_max=5000, default=0),
        labels=["المشاريع المكتملة", "مشاريع مكتملة", "المشاريع المنفذة"],
        description="Lifetime completed freelance projects",
    ),
    "active_projects": FieldSpec(
        name="active_projects",
        group="stats",
        type=Count(min=0, soft_max=50, hard_max=500, default=0),
        labels=["المشاريع قيد التنفيذ", "مشاريع قيد التنفيذ", "قيد التنفيذ"],
        description="Currently active open projects",
    ),
    "received_projects": FieldSpec(
        name="received_projects",
        group="stats",
        type=Count(min=0, soft_max=550, hard_max=5500, default=0),
        labels=["المشاريع المستلمة", "مشاريع مستلمة"],
        derived_from=("total_completed_projects", "active_projects"),
        description="Total projects awarded to freelancer",
    ),
    "financial_deals": FieldSpec(
        name="financial_deals",
        group="stats",
        type=Count(min=0, soft_max=500, hard_max=5000, default=0),
        labels=["الصفقات", "صفقات مالية"],
        derived_from=("total_completed_projects",),
        description="Total financial contracts executed",
    ),
    "response_time": FieldSpec(
        name="response_time",
        group="stats",
        type=RelativeTime(default="غير محدد"),
        labels=["متوسط سرعة الرد", "سرعة الرد"],
        description="Observed text response speed",
    ),
    "avg_response_time_raw": FieldSpec(
        name="avg_response_time_raw",
        group="stats",
        type=RelativeTime(default="غير محدد"),
        labels=["متوسط سرعة الرد", "سرعة الرد"],
        description="Raw Arabic response time text",
    ),
    "avg_response_time_minutes": FieldSpec(
        name="avg_response_time_minutes",
        group="stats",
        type=Duration(min=0.0, max=43200.0, default=1440.0),
        derived_from=("avg_response_time_raw",),
        description="Average response time converted to minutes",
    ),
    "last_seen": FieldSpec(
        name="last_seen",
        group="stats",
        type=RelativeTime(default="منذ يوم"),
        labels=["آخر تواجد", "اخر تواجد", "آخر ظهور"],
        description="Last active status text",
    ),
    "last_active": FieldSpec(
        name="last_active",
        group="stats",
        type=RelativeTime(default="منذ يوم"),
        labels=["آخر تواجد", "اخر تواجد", "آخر ظهور"],
        description="Last active status text",
    ),
    "member_since": FieldSpec(
        name="member_since",
        group="stats",
        type=Text(default="2021-01-01"),
        labels=["تاريخ التسجيل"],
        description="Registration text date",
    ),
    "registration_date": FieldSpec(
        name="registration_date",
        group="stats",
        type=ArabicDate(min="2013-01-01", max="now", default="2021-01-01T00:00:00"),
        labels=["تاريخ التسجيل"],
        description="ISO 8601 registration timestamp",
    ),
    "registration_date_str": FieldSpec(
        name="registration_date_str",
        group="stats",
        type=Text(default="2021-01-01T00:00:00"),
        derived_from=("registration_date",),
        description="String representation of registration date",
    ),

    # --- Meta & Ranking ---
    "rank": FieldSpec(
        name="rank",
        group="meta",
        type=Count(min=1, soft_max=100000, default=1),
        description="Scrape rank index",
    ),
    "scraped_at": FieldSpec(
        name="scraped_at",
        group="meta",
        type=Text(default=""),
        description="Scrape execution timestamp ISO string",
    ),
    "parse_confidence": FieldSpec(
        name="parse_confidence",
        group="meta",
        type=Enum(allowed=["ok", "suspect", "bad", "quarantine"], default="ok"),
        description="Record quality verdict",
    ),
    "parse_signals": FieldSpec(
        name="parse_signals",
        group="meta",
        type=ListOf(Text()),
        description="Signal markers observed during DOM parsing",
    ),
    "success_score": FieldSpec(
        name="success_score",
        group="meta",
        type=Percentage(min=0.0, max=100.0, default=0.0),
        description="Calculated freelancer success score",
    ),
}


def check_record_coherence(stats_data: Dict[str, Any]) -> List[str]:
    """Check relational coherence across extracted metrics."""
    issues: List[str] = []

    completed = stats_data.get("total_completed_projects", 0)
    received = stats_data.get("received_projects", completed)
    reviews = stats_data.get("reviews_count", 0)
    rating = stats_data.get("rating", 0.0)

    # 1. Received projects must be >= completed projects
    if received < completed:
        issues.append("incoherent_received_less_than_completed")

    # 2. When completed projects is 0, rates should not be high artificial values
    if completed == 0:
        rates = [
            stats_data.get("completion_rate", 0.0),
            stats_data.get("ontime_delivery_rate", 0.0),
            stats_data.get("rehire_rate", 0.0),
            stats_data.get("communication_success_rate", 0.0),
        ]
        if any(r > 0.0 for r in rates):
            issues.append("incoherent_rates_with_zero_projects")

    # 3. If reviews count is 0, rating should be 0.0
    if reviews == 0 and rating > 0.0:
        issues.append("incoherent_rating_with_zero_reviews")

    return issues
