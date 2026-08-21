"""Overview visualizations, KPI indicator cards, and data quality charts."""

from typing import Any, Dict, Optional
import pandas as pd
import plotly.graph_objects as go
from .helpers import ChartCard, apply_standard_layout, create_empty_figure
from ..config import get_default_config


def plot_dataset_kpis(kpi_data: Dict[str, Any]) -> ChartCard:
    """Render top-level KPI metrics summary with indicator cards."""
    total_users = kpi_data.get("total_users", 0)
    total_projects = kpi_data.get("total_completed_projects", 0)
    total_active = kpi_data.get("total_active_projects", 0)
    avg_score = kpi_data.get("avg_success_score", 0.0)
    med_projects = kpi_data.get("median_projects_per_user", 0.0)
    completeness = kpi_data.get("overall_completeness_rate", 100.0)

    fig = go.Figure()

    # Subplot indicators in 2x3 grid
    indicators = [
        {"title": "Total Users", "val": f"{total_users:,}", "row": 0, "col": 0, "color": "#38bdf8"},
        {"title": "Completed Projects", "val": f"{int(total_projects):,}", "row": 0, "col": 1, "color": "#34d399"},
        {"title": "Active Projects", "val": f"{int(total_active):,}", "row": 0, "col": 2, "color": "#fbbf24"},
        {"title": "Avg Success Score", "val": f"{avg_score:.1f}%", "row": 1, "col": 0, "color": "#818cf8"},
        {"title": "Median Projects/User", "val": f"{med_projects:.1f}", "row": 1, "col": 1, "color": "#f472b6"},
        {"title": "Data Completeness", "val": f"{completeness:.1f}%", "row": 1, "col": 2, "color": "#2dd4bf"},
    ]

    for ind in indicators:
        fig.add_trace(go.Indicator(
            mode="number",
            value=float(str(ind["val"]).replace(",", "").replace("%", "")),
            number={"suffix": "%" if "%" in str(ind["val"]) else "", "font": {"size": 28, "color": ind["color"]}},
            title={"text": f"<b>{ind['title']}</b>", "font": {"size": 13, "color": "#94a3b8"}},
            domain={
                "row": ind["row"],
                "column": ind["col"],
            }
        ))

    fig.update_layout(
        grid={"rows": 2, "columns": 3, "pattern": "independent"},
        height=260,
    )
    apply_standard_layout(fig, height=260)

    return ChartCard(
        title="Dataset Overview KPI Cards",
        description="High-level summary metrics detailing total population size, completed and active project volume, median activity, and average profile health.",
        figure=fig,
        section="Overview",
        card_id="dataset_overview_kpis",
        extra_kpis=kpi_data,
    )


def plot_missing_data_by_field(df: pd.DataFrame) -> ChartCard:
    """Horizontal bar chart showing data completeness and missingness across all schema attributes."""
    if df.empty:
        fig = create_empty_figure("No field metadata available for missingness analysis.")
    else:
        fig = go.Figure()
        # Sort by completeness percentage ascending so most complete are at the top
        df_sorted = df.sort_values(by="completeness_percentage", ascending=True)

        colors = [
            "#34d399" if pct >= 90 else "#38bdf8" if pct >= 70 else "#fbbf24" if pct >= 40 else "#f87171"
            for pct in df_sorted["completeness_percentage"]
        ]

        text_labels = []
        for c_pct, m_cnt, t_cnt in zip(df_sorted["completeness_percentage"], df_sorted["missing_count"], df_sorted["total_count"]):
            if m_cnt == 0:
                text_labels.append(f"100.0% Complete ({t_cnt:,} records)")
            else:
                text_labels.append(f"{c_pct:.1f}% Complete ({m_cnt:,} missing)")

        fig.add_trace(go.Bar(
            y=df_sorted["field"],
            x=df_sorted["completeness_percentage"],
            orientation="h",
            marker=dict(color=colors, line=dict(color="#1e293b", width=1)),
            text=text_labels,
            textposition="auto",
            hovertemplate=(
                "<b>Field:</b> %{y}<br>"
                "<b>Data Completeness:</b> %{x:.2f}%<br>"
                "<b>Non-Null Records:</b> %{customdata[0]:,}<br>"
                "<b>Missing Records:</b> %{customdata[1]:,}<br>"
                "<b>Total Records:</b> %{customdata[2]:,}<extra></extra>"
            ),
            customdata=list(zip(df_sorted["non_null_count"], df_sorted["missing_count"], df_sorted["total_count"])),
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Data Completeness Rate (%)",
            yaxis_title="Schema Attribute",
            height=max(380, len(df_sorted) * 28),
        )
        fig.update_xaxes(range=[0, 105])

    return ChartCard(
        title="Missing Data by Field",
        description="Quantifies data completeness and missingness across each schema attribute to evaluate parsing fidelity and data quality.",
        figure=fig,
        section="Overview",
        card_id="missing_data_by_field",
    )


def plot_data_completeness_distribution(df: pd.DataFrame) -> ChartCard:
    """Histogram / bar chart showing distribution of record completeness scores."""
    if df.empty:
        fig = create_empty_figure("No completeness data available.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["completeness_bucket"],
            y=df["user_count"],
            marker=dict(color="#38bdf8", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({c:,})" for p, c in zip(df["percentage"], df["user_count"])],
            textposition="auto",
            hovertemplate="<b>Completeness Tier:</b> %{x}<br><b>User Count:</b> %{y:,}<br><b>Percentage:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Profile Completeness Score Bucket",
            yaxis_title="Number of Profiles",
            height=380,
        )

    return ChartCard(
        title="Profile Completeness Score Distribution",
        description="Frequency breakdown showing what proportion of profiles possess complete key attributes (e.g., bio, skills, project history, rates).",
        figure=fig,
        section="Data Quality",
        card_id="data_completeness_distribution",
    )


def plot_parse_confidence_distribution(df: pd.DataFrame) -> ChartCard:
    """Breakdown of parser confidence levels (ok, warning, low)."""
    if df.empty:
        fig = create_empty_figure("No parse confidence data available.")
    else:
        color_map = {"ok": "#34d399", "warning": "#fbbf24", "low": "#f87171", "unknown": "#94a3b8"}
        colors = [color_map.get(str(lvl).lower(), "#818cf8") for lvl in df["confidence_level"]]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["confidence_level"],
            y=df["count"],
            marker=dict(color=colors, line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({c:,})" for p, c in zip(df["percentage"], df["count"])],
            textposition="auto",
            hovertemplate="<b>Confidence Level:</b> %{x}<br><b>Count:</b> %{y:,}<br><b>Share:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Parse Confidence Signal",
            yaxis_title="Extracted Profiles Count",
            height=360,
        )

    return ChartCard(
        title="Parse Confidence & Signal Distribution",
        description="Distribution of scraper extraction confidence scores indicating the proportion of cleanly parsed vs. degraded profile HTML structures.",
        figure=fig,
        section="Data Quality",
        card_id="parse_confidence_distribution",
    )
