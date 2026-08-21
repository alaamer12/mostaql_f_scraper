"""Category visualizations, market share comparisons, and category ratio charts."""

from typing import Any, Dict, Optional
import pandas as pd
import plotly.graph_objects as go
from .helpers import ChartCard, apply_standard_layout, create_empty_figure


def plot_projects_by_category(df: pd.DataFrame) -> ChartCard:
    """Horizontal bar chart showing completed project volume per category."""
    if df.empty:
        fig = create_empty_figure("No category project data available.")
    else:
        df_sorted = df.sort_values(by="project_count", ascending=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=df_sorted["category"],
            x=df_sorted["project_count"],
            orientation="h",
            marker=dict(color="#38bdf8", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({int(c):,})" for p, c in zip(df_sorted["percentage"], df_sorted["project_count"])],
            textposition="auto",
            hovertemplate="<b>Category:</b> %{y}<br><b>Completed Projects:</b> %{x:,}<br><b>Market Share:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df_sorted["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Completed Projects Volume",
            yaxis_title="Service Category",
            height=max(380, len(df_sorted) * 28),
        )

    return ChartCard(
        title="Projects by Category",
        description="Distribution of completed project volume across top service categories with smaller categories aggregated into 'Other'.",
        figure=fig,
        section="Category Analysis",
        card_id="projects_by_category",
    )


def plot_users_by_category(df: pd.DataFrame) -> ChartCard:
    """Horizontal bar chart showing user headcounts per category."""
    if df.empty:
        fig = create_empty_figure("No category user data available.")
    else:
        df_sorted = df.sort_values(by="user_count", ascending=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=df_sorted["category"],
            x=df_sorted["user_count"],
            orientation="h",
            marker=dict(color="#818cf8", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({c:,})" for p, c in zip(df_sorted["percentage"], df_sorted["user_count"])],
            textposition="auto",
            hovertemplate="<b>Category:</b> %{y}<br><b>Freelancers:</b> %{x:,}<br><b>Share:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df_sorted["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Freelancer Count",
            yaxis_title="Service Category",
            height=max(380, len(df_sorted) * 28),
        )

    return ChartCard(
        title="Users by Category",
        description="Distribution of registered freelancers across service categories, revealing supply volume per specialization.",
        figure=fig,
        section="Category Analysis",
        card_id="users_by_category",
    )


def plot_avg_projects_per_user_by_category(df: pd.DataFrame) -> ChartCard:
    """Bar chart showing normalized completed projects per user ratio across categories."""
    if df.empty:
        fig = create_empty_figure("No category ratio data available.")
    else:
        df_sorted = df.sort_values(by="avg_projects_per_user", ascending=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=df_sorted["category"],
            x=df_sorted["avg_projects_per_user"],
            orientation="h",
            marker=dict(color="#34d399", line=dict(color="#0f172a", width=1)),
            text=[f"{r:.1f} proj/user" for r in df_sorted["avg_projects_per_user"]],
            textposition="auto",
            hovertemplate="<b>Category:</b> %{y}<br><b>Avg Projects/User:</b> %{x:.2f}<br><b>Total Projects:</b> %{customdata[0]:,}<br><b>Total Users:</b> %{customdata[1]:,}<extra></extra>",
            customdata=list(zip(df_sorted["project_count"], df_sorted["user_count"])),
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Average Projects per Registered User",
            yaxis_title="Service Category",
            height=max(380, len(df_sorted) * 28),
        )

    return ChartCard(
        title="Average Projects per User by Category",
        description="Normalized productivity ratio (total projects / total users) identifying categories with unusually high or low average freelancer engagement.",
        figure=fig,
        section="Category Analysis",
        card_id="avg_projects_per_user_by_category",
    )


def plot_category_concentration_pareto(df: pd.DataFrame) -> ChartCard:
    """Pareto chart showing cumulative category market share."""
    if df.empty:
        fig = create_empty_figure("No category concentration data.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["category"],
            y=df["project_count"],
            name="Projects",
            marker=dict(color="#38bdf8", line=dict(color="#0f172a", width=1)),
            yaxis="y",
            hovertemplate="<b>Category:</b> %{x}<br><b>Projects:</b> %{y:,}<extra></extra>",
        ))

        fig.add_trace(go.Scatter(
            x=df["category"],
            y=df["cum_percentage"],
            name="Cumulative Share (%)",
            mode="lines+markers",
            marker=dict(color="#fbbf24", size=6),
            line=dict(color="#fbbf24", width=2),
            yaxis="y2",
            hovertemplate="<b>Cumulative Share:</b> %{y:.1f}%<extra></extra>",
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Category",
            yaxis_title="Project Volume",
            height=420,
        )
        fig.update_layout(
            yaxis2=dict(
                title="Cumulative Market Share (%)",
                overlaying="y",
                side="right",
                range=[0, 105],
                gridcolor="rgba(0,0,0,0)",
                tickfont=dict(color="#fbbf24"),
                title_font=dict(color="#fbbf24"),
            ),
            legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center"),
        )
        fig.update_xaxes(tickangle=-35)

    return ChartCard(
        title="Category Concentration",
        description="Pareto breakdown detailing how project demand concentrates among dominant market categories.",
        figure=fig,
        section="Category Analysis",
        card_id="category_concentration_pareto",
    )


def plot_user_vs_project_category_comparison(df: pd.DataFrame) -> ChartCard:
    """Grouped bar chart contrasting user supply share vs project demand share."""
    if df.empty:
        fig = create_empty_figure("No comparative category data.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["category"],
            y=df["user_share_pct"],
            name="User Share (%)",
            marker=dict(color="#818cf8"),
            hovertemplate="<b>Category:</b> %{x}<br><b>User Share:</b> %{y:.1f}% (%{customdata:,} users)<extra></extra>",
            customdata=df["user_count"],
        ))

        fig.add_trace(go.Bar(
            x=df["category"],
            y=df["project_share_pct"],
            name="Project Share (%)",
            marker=dict(color="#34d399"),
            hovertemplate="<b>Category:</b> %{x}<br><b>Project Share:</b> %{y:.1f}% (%{customdata:,} projects)<extra></extra>",
            customdata=df["project_count"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Category",
            yaxis_title="Share of Total Platform (%)",
            height=430,
        )
        fig.update_layout(barmode="group", legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center"))
        fig.update_xaxes(tickangle=-35)

    return ChartCard(
        title="User Activity by Category",
        description="Comparative dual-metric chart highlighting supply vs demand disparities between user headcount and completed project volume.",
        figure=fig,
        section="Category Analysis",
        card_id="user_vs_project_category_comparison",
    )
