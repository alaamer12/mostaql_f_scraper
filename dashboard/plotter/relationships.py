"""Skills visualizations, temporal series, geographic breakdown, and correlation heatmaps."""

from typing import Any, Dict, Optional
import pandas as pd
import plotly.graph_objects as go
from .helpers import ChartCard, apply_standard_layout, create_empty_figure


def plot_most_common_skills(df: pd.DataFrame) -> ChartCard:
    """Horizontal bar chart showing highest-frequency skills."""
    if df.empty:
        fig = create_empty_figure("No skills data available.")
    else:
        df_sorted = df.sort_values(by="count", ascending=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=df_sorted["skill"],
            x=df_sorted["count"],
            orientation="h",
            marker=dict(color="#38bdf8", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({c:,})" for p, c in zip(df_sorted["percentage"], df_sorted["count"])],
            textposition="auto",
            hovertemplate="<b>Skill Name:</b> %{y}<br><b>Profiles Count:</b> %{x:,}<br><b>Market Coverage:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df_sorted["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Number of Freelancers Listing This Skill",
            yaxis_title="Skill Tag",
            height=max(420, len(df_sorted) * 26),
        )

    return ChartCard(
        title="Most Common Skills",
        description="Top unnested skills listed on freelancer profiles, showing skill frequency and user coverage percentage.",
        figure=fig,
        section="Skills Analysis",
        card_id="most_common_skills",
    )


def plot_skills_per_user_distribution(df: pd.DataFrame) -> ChartCard:
    """Histogram / bar chart showing frequency of skills count per profile."""
    if df.empty:
        fig = create_empty_figure("No skills count distribution data.")
    else:
        labels = [f"{sc} skill{'s' if sc != 1 else ''}" for sc in df["skills_count"]]
        samples = df["sample_skills"].tolist() if "sample_skills" in df.columns else ["N/A"] * len(df)
        custom_data = list(zip(df["percentage"], samples))

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=labels,
            y=df["user_count"],
            marker=dict(color="#818cf8", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}%" for p in df["percentage"]],
            textposition="auto",
            customdata=custom_data,
            hovertemplate=(
                "<b>Portfolio Skill Depth:</b> %{x}<br>"
                "<b>Total Freelancers:</b> %{y:,} profiles<br>"
                "<b>Share of Freelancers:</b> %{customdata[0]:.1f}%<br>"
                "<b>Top Sample Skills in Tier:</b> %{customdata[1]}"
                "<extra></extra>"
            ),
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Number of Skills Listed on Profile",
            yaxis_title="Freelancer Profiles Count",
            height=420,
        )

    return ChartCard(
        title="Distribution of Skills per User",
        description="Frequency breakdown detailing how many skills freelancers typically list on their profiles, with representative skills for each portfolio depth tier.",
        figure=fig,
        section="Skills Analysis",
        card_id="skills_per_user_distribution",
    )


def plot_skills_vs_project_activity(df: pd.DataFrame) -> ChartCard:
    """Grouped bar chart showing mean and median project counts grouped by skills tier."""
    if df.empty:
        fig = create_empty_figure("No skills vs activity data.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["skills_bin"],
            y=df["avg_projects"],
            name="Mean Projects",
            marker=dict(color="#38bdf8"),
            hovertemplate="<b>Skills Tier:</b> %{x}<br><b>Mean Projects:</b> %{y:.1f}<br><b>Users:</b> %{customdata:,}<extra></extra>",
            customdata=df["user_count"],
        ))

        fig.add_trace(go.Bar(
            x=df["skills_bin"],
            y=df["median_projects"],
            name="Median Projects",
            marker=dict(color="#fbbf24"),
            hovertemplate="<b>Skills Tier:</b> %{x}<br><b>Median Projects:</b> %{y:.1f}<extra></extra>",
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Skills Tier",
            yaxis_title="Projects per User",
            height=410,
        )
        fig.update_layout(barmode="group", legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center"))

    return ChartCard(
        title="Skills Count vs. Project Activity",
        description="Evaluates whether freelancers with broader skill sets exhibit higher mean or median completed project volumes.",
        figure=fig,
        section="Skills Analysis",
        card_id="skills_vs_project_activity",
    )


def plot_temporal_project_activity(df: pd.DataFrame) -> ChartCard:
    """Time-series line and bar chart tracking monthly project volume and registrations."""
    if df.empty:
        fig = create_empty_figure("No timestamped activity data available.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["period"],
            y=df["project_count"],
            name="Completed Projects",
            marker=dict(color="#38bdf8", opacity=0.7),
            yaxis="y",
            hovertemplate="<b>Period:</b> %{x}<br><b>Projects:</b> %{y:,}<extra></extra>",
        ))

        fig.add_trace(go.Scatter(
            x=df["period"],
            y=df["avg_projects_per_user"],
            name="Avg Projects/User",
            mode="lines+markers",
            marker=dict(color="#34d399", size=6),
            line=dict(color="#34d399", width=2),
            yaxis="y2",
            hovertemplate="<b>Avg Projects/User:</b> %{y:.2f}<extra></extra>",
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Registration Cohort (Year-Month)",
            yaxis_title="Total Completed Projects",
            height=430,
        )
        fig.update_layout(
            yaxis2=dict(
                title="Avg Projects / User",
                overlaying="y",
                side="right",
                gridcolor="rgba(0,0,0,0)",
                tickfont=dict(color="#34d399"),
                title_font=dict(color="#34d399"),
            ),
            legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center"),
        )
        fig.update_xaxes(tickangle=-45)

    return ChartCard(
        title="Projects Over Time",
        description="Time series tracking project completion volume and average project ratios grouped by user registration cohorts.",
        figure=fig,
        section="Temporal Analysis",
        card_id="temporal_project_activity",
    )


def plot_user_growth_over_time(df: pd.DataFrame) -> ChartCard:
    """Line and area chart showing new registrations and cumulative user base growth."""
    if df.empty:
        fig = create_empty_figure("No user growth timeline data available.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["period"],
            y=df["new_users"],
            name="New Registrations",
            marker=dict(color="#818cf8", opacity=0.6),
            yaxis="y",
            hovertemplate="<b>Month:</b> %{x}<br><b>New Users:</b> %{y:,}<extra></extra>",
        ))

        fig.add_trace(go.Scatter(
            x=df["period"],
            y=df["cumulative_users"],
            name="Cumulative Users",
            mode="lines",
            line=dict(color="#38bdf8", width=3),
            yaxis="y2",
            hovertemplate="<b>Cumulative Total:</b> %{y:,}<extra></extra>",
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Year-Month",
            yaxis_title="Monthly Registrations",
            height=430,
        )
        fig.update_layout(
            yaxis2=dict(
                title="Cumulative User Base",
                overlaying="y",
                side="right",
                gridcolor="rgba(0,0,0,0)",
                tickfont=dict(color="#38bdf8"),
                title_font=dict(color="#38bdf8"),
            ),
            legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center"),
        )
        fig.update_xaxes(tickangle=-45)

    return ChartCard(
        title="User/Profile Creation Over Time",
        description="Historical growth curve showing monthly freelancer onboarding pace alongside total cumulative platform scale.",
        figure=fig,
        section="Temporal Analysis",
        card_id="user_growth_over_time",
    )


def plot_temporal_category_activity(df: pd.DataFrame) -> ChartCard:
    """Multi-line / stacked area chart tracking category trends over time."""
    if df.empty:
        fig = create_empty_figure("No temporal category data available.")
    else:
        fig = go.Figure()
        categories = df["category"].unique()
        palette = ["#38bdf8", "#818cf8", "#34d399", "#fbbf24", "#f472b6", "#a78bfa"]

        for idx, cat in enumerate(categories):
            cat_df = df[df["category"] == cat]
            fig.add_trace(go.Scatter(
                x=cat_df["period"],
                y=cat_df["project_count"],
                name=cat,
                mode="lines+markers",
                line=dict(color=palette[idx % len(palette)], width=2),
                marker=dict(size=5),
                hovertemplate=f"<b>{cat}</b> (%{{x}}): %{{y:,}} projects<extra></extra>",
            ))

        apply_standard_layout(
            fig,
            xaxis_title="Cohort Period (Year-Month)",
            yaxis_title="Completed Projects",
            height=430,
        )
        fig.update_xaxes(tickangle=-45)

    return ChartCard(
        title="Project Activity Over Time by Category",
        description="Multi-series tracking showing the historical trajectory of completed projects across top specialized categories.",
        figure=fig,
        section="Temporal Analysis",
        card_id="temporal_category_activity",
    )


def plot_geographic_user_distribution(df: pd.DataFrame) -> ChartCard:
    """Horizontal bar chart showing user geographic distribution."""
    if df.empty:
        fig = create_empty_figure("No geographic location data available.")
    else:
        df_sorted = df.sort_values(by="user_count", ascending=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=df_sorted["location"],
            x=df_sorted["user_count"],
            orientation="h",
            marker=dict(color="#38bdf8", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({c:,})" for p, c in zip(df_sorted["percentage"], df_sorted["user_count"])],
            textposition="auto",
            hovertemplate="<b>Location:</b> %{y}<br><b>Users:</b> %{x:,}<br><b>Share:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df_sorted["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Freelancers Count",
            yaxis_title="Geographic Location",
            height=max(380, len(df_sorted) * 26),
        )

    return ChartCard(
        title="Geographic Distribution of Users",
        description="Geographic breakdown of registered freelancers by detected country or metropolitan region.",
        figure=fig,
        section="Geographic Analysis",
        card_id="geographic_user_distribution",
    )


def plot_geographic_project_activity(df: pd.DataFrame) -> ChartCard:
    """Horizontal bar chart showing project volume aggregated by geographic location."""
    if df.empty:
        fig = create_empty_figure("No geographic project activity data available.")
    else:
        df_sorted = df.sort_values(by="project_count", ascending=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=df_sorted["location"],
            x=df_sorted["project_count"],
            orientation="h",
            marker=dict(color="#34d399", line=dict(color="#0f172a", width=1)),
            text=[f"{int(p):,} projects" for p in df_sorted["project_count"]],
            textposition="auto",
            hovertemplate="<b>Location:</b> %{y}<br><b>Completed Projects:</b> %{x:,}<br><b>Avg Projects/User:</b> %{customdata:.1f}<extra></extra>",
            customdata=df_sorted["avg_projects"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Completed Projects Volume",
            yaxis_title="Geographic Location",
            height=max(380, len(df_sorted) * 26),
        )

    return ChartCard(
        title="Project Activity by Location",
        description="Distribution of completed projects across geographic regions, highlighting leading freelance hubs.",
        figure=fig,
        section="Geographic Analysis",
        card_id="geographic_project_activity",
    )


def plot_numeric_correlations(df_corr: pd.DataFrame) -> ChartCard:
    """Correlation matrix heatmap for numeric features."""
    if df_corr.empty:
        fig = create_empty_figure("Insufficient numeric columns for correlation analysis.")
    else:
        # Clean labels
        labels = [c.replace("_", " ").title() for c in df_corr.columns]
        fig = go.Figure()
        fig.add_trace(go.Heatmap(
            z=df_corr.values,
            x=labels,
            y=labels,
            colorscale="RdBu",
            zmin=-1.0,
            zmax=1.0,
            colorbar=dict(title="Pearson r", thickness=12, len=0.8),
            text=[[f"{val:.2f}" for val in row] for row in df_corr.values],
            texttemplate="%{text}",
            textfont=dict(size=11),
            hovertemplate="<b>%{y}</b> ↔ <b>%{x}</b><br>Correlation (r): %{z:.3f}<extra></extra>",
        ))

        apply_standard_layout(
            fig,
            height=max(460, len(labels) * 45),
        )
        fig.update_xaxes(tickangle=-40)

    return ChartCard(
        title="Numeric Feature Correlations",
        description="Heatmap correlation matrix (Pearson r) calculated across key quantitative performance and volume metrics.",
        figure=fig,
        section="Advanced Insights",
        card_id="numeric_correlations",
    )


def plot_bivariate_relationship_samples(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    title: str = "",
    description: str = "",
) -> ChartCard:
    """Sampled scatterplot exploring bivariate relationships."""
    if df.empty or x_col not in df.columns or y_col not in df.columns:
        fig = create_empty_figure("No sample data available for relationship analysis.")
    else:
        x_label = x_col.replace("_", " ").title()
        y_label = y_col.replace("_", " ").title()
        chart_title = title or f"{x_label} vs. {y_label}"

        fig = go.Figure()
        fig.add_trace(go.Scattergl(
            x=df[x_col],
            y=df[y_col],
            mode="markers",
            marker=dict(size=5, color="#38bdf8", opacity=0.7, line=dict(width=0)),
            text=df.get("name", ""),
            customdata=df.get("title", ""),
            hovertemplate=f"<b>Name:</b> %{{text}}<br><b>{x_label}:</b> %{{x}}<br><b>{y_label}:</b> %{{y}}<extra></extra>",
        ))

        apply_standard_layout(
            fig,
            xaxis_title=x_label,
            yaxis_title=y_label,
            height=430,
        )

    return ChartCard(
        title=title or f"{x_col.replace('_', ' ').title()} vs. {y_col.replace('_', ' ').title()}",
        description=description or "Sampled bivariate distribution showing the relationship between these two numeric variables.",
        figure=fig,
        section="Advanced Insights",
        card_id=f"bivariate_{x_col}_{y_col}",
    )
