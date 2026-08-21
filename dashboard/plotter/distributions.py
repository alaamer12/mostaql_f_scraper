"""Distribution charts, Scattergl point plots, histograms, and concentration visualizations."""

from typing import Any, Dict, Optional
import pandas as pd
import plotly.graph_objects as go
from .helpers import ChartCard, apply_standard_layout, create_empty_figure


def plot_user_project_scatter(df: pd.DataFrame) -> ChartCard:
    """Scattergl plot showing user rank ordered by completed project volume."""
    if df.empty:
        fig = create_empty_figure("No user project data available.")
    else:
        fig = go.Figure()
        names = df["name"].tolist() if "name" in df.columns else [""] * len(df)
        titles = df["title"].tolist() if "title" in df.columns else [""] * len(df)
        scores = df["success_score"].tolist() if "success_score" in df.columns else [0.0] * len(df)
        custom_data = list(zip(names, titles, scores))

        fig.add_trace(go.Scattergl(
            x=df["user_rank"],
            y=df["project_count"],
            mode="markers",
            marker=dict(
                size=6,
                color=scores if any(s > 0 for s in scores) else df["project_count"],
                colorscale="Viridis",
                showscale=True,
                colorbar=dict(
                    title=dict(text="Success Score", side="right"),
                    thickness=12,
                    len=0.75,
                ),
                opacity=0.8,
            ),
            customdata=custom_data,
            hovertemplate=(
                "<b>Rank:</b> #%{x:,}<br>"
                "<b>Name:</b> %{customdata[0]}<br>"
                "<b>Title:</b> %{customdata[1]}<br>"
                "<b>Completed Projects:</b> %{y:,}<br>"
                "<b>Success Score:</b> %{customdata[2]:.1f}%"
                "<extra></extra>"
            ),
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Freelancer Rank (Ordered by Completed Projects)",
            yaxis_title="Total Completed Projects",
            height=460,
        )

    return ChartCard(
        title="Users vs. Number of Projects",
        description="Interactive WebGL scatter plot illustrating project activity across ranked active freelancers, highlighting power users and long-tail distribution.",
        figure=fig,
        section="User Activity",
        card_id="user_project_scatter",
    )


def plot_project_count_ranges(df: pd.DataFrame) -> ChartCard:
    """Bar chart displaying user distribution across project volume ranges."""
    if df.empty:
        fig = create_empty_figure("No range data available.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["project_range"],
            y=df["user_count"],
            marker=dict(color="#38bdf8", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({c:,})" for p, c in zip(df["percentage"], df["user_count"])],
            textposition="auto",
            hovertemplate="<b>Project Range:</b> %{x}<br><b>Users:</b> %{y:,}<br><b>Share:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Completed Projects Range",
            yaxis_title="Freelancer Count",
            height=400,
        )

    return ChartCard(
        title="Distribution of Users by Number of Projects",
        description="Aggregated user breakdown across discrete activity buckets (0, 1-5, 6-10, 11-20, 21-50, 51-100, 100+).",
        figure=fig,
        section="User Activity",
        card_id="project_count_ranges",
    )


def plot_project_count_histogram(df: pd.DataFrame) -> ChartCard:
    """Dynamic SQL-computed frequency histogram of project counts."""
    if df.empty:
        fig = create_empty_figure("No project count histogram data.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["bin_label"],
            y=df["count"],
            marker=dict(color="#818cf8", line=dict(color="#0f172a", width=1)),
            text=[f"{c:,}" for c in df["count"]],
            textposition="auto",
            hovertemplate="<b>Bin Range:</b> %{x}<br><b>Frequency:</b> %{y:,}<br><b>Share:</b> %{customdata:.2f}%<extra></extra>",
            customdata=df["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Project Count Intervals",
            yaxis_title="Number of Users",
            height=400,
        )
        fig.update_xaxes(tickangle=-45)

    return ChartCard(
        title="Project Count Histogram",
        description="Frequency distribution of completed projects per user calculated directly in SQL to identify skewness and population density.",
        figure=fig,
        section="User Activity",
        card_id="project_count_histogram",
    )


def plot_cumulative_user_distribution(df: pd.DataFrame) -> ChartCard:
    """Empirical Cumulative Distribution Function (ECDF) line chart."""
    if df.empty:
        fig = create_empty_figure("No cumulative distribution data.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["project_count"],
            y=df["cum_percentage"],
            mode="lines",
            line=dict(color="#34d399", width=3),
            fill="tozeroy",
            fillcolor="rgba(52, 211, 153, 0.15)",
            hovertemplate="<b>Projects:</b> ≤ %{x:,}<br><b>Cumulative Population:</b> %{y:.2f}%<br><b>Users:</b> %{customdata:,}<extra></extra>",
            customdata=df["cum_users"],
        ))

        # Add 50%, 80%, and 90% benchmark lines
        for pct, color, label in [(50, "#fbbf24", "50th Percentile"), (80, "#f472b6", "80th Percentile"), (95, "#f87171", "95th Percentile")]:
            fig.add_hline(
                y=pct,
                line_dash="dash",
                line_color=color,
                annotation_text=label,
                annotation_position="bottom right",
                annotation_font=dict(size=11, color=color),
            )

        apply_standard_layout(
            fig,
            xaxis_title="Completed Projects Threshold (N)",
            yaxis_title="Cumulative Users (% ≤ N)",
            height=420,
        )
        fig.update_yaxes(range=[0, 105])

    return ChartCard(
        title="Cumulative Distribution of Users by Project Count",
        description="ECDF curve showing the proportion of users possessing at most N completed projects, exposing population density benchmarks.",
        figure=fig,
        section="User Activity",
        card_id="cumulative_user_distribution",
    )


def plot_user_activity_segments(df: pd.DataFrame) -> ChartCard:
    """Bar chart showing classified user activity tiers."""
    if df.empty:
        fig = create_empty_figure("No segment data available.")
    else:
        palette = ["#94a3b8", "#38bdf8", "#818cf8", "#fbbf24", "#f472b6"]
        colors = [palette[i % len(palette)] for i in range(len(df))]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["segment"],
            y=df["user_count"],
            marker=dict(color=colors, line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({c:,})" for p, c in zip(df["percentage"], df["user_count"])],
            textposition="auto",
            hovertemplate="<b>Activity Tier:</b> %{x}<br><b>Users:</b> %{y:,}<br><b>Share:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Activity Segment",
            yaxis_title="Freelancer Count",
            height=380,
        )

    return ChartCard(
        title="User Activity Segments",
        description="Classification of user base into standard activity tiers (Inactive: 0, Low: 1-5, Medium: 6-20, High: 21-100, Very High: 100+).",
        figure=fig,
        section="User Activity",
        card_id="user_activity_segments",
    )


def plot_log_scale_distribution(df: pd.DataFrame) -> ChartCard:
    """Log-scale grouped distribution chart highlighting long-tail behavior."""
    if df.empty:
        fig = create_empty_figure("No log scale data available.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["log_bin"],
            y=df["user_count"],
            marker=dict(color="#a78bfa", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% ({c:,})" for p, c in zip(df["percentage"], df["user_count"])],
            textposition="auto",
            hovertemplate="<b>Log Bin (Projects):</b> %{x}<br><b>Users:</b> %{y:,}<br><b>Share:</b> %{customdata:.1f}%<extra></extra>",
            customdata=df["percentage"],
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Projects Volume (Power-of-Two Intervals)",
            yaxis_title="User Count",
            height=400,
        )

    return ChartCard(
        title="Project Distribution — Logarithmic Scale",
        description="Log-binned distribution chart exposing the long-tail behavior between casual users and high-volume top producers.",
        figure=fig,
        section="User Activity",
        card_id="log_scale_distribution",
    )


def plot_top_users_by_projects(df: pd.DataFrame) -> ChartCard:
    """Horizontal bar chart showing highest-producing freelancers."""
    if df.empty:
        fig = create_empty_figure("No top user data available.")
    else:
        df_sorted = df.sort_values(by="project_count", ascending=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=[f"#{r} {n}" for r, n in zip(df_sorted["rank"], df_sorted["name"])],
            x=df_sorted["project_count"],
            orientation="h",
            marker=dict(
                color=df_sorted["success_score"],
                colorscale="Viridis",
                showscale=True,
                colorbar=dict(title="Success Score", thickness=10, len=0.75),
                line=dict(color="#0f172a", width=1),
            ),
            text=[f"{int(p):,} projects" for p in df_sorted["project_count"]],
            textposition="auto",
            customdata=list(zip(df_sorted["title"], df_sorted["success_score"], df_sorted["completion_rate"])),
            hovertemplate=(
                "<b>Freelancer:</b> %{y}<br>"
                "<b>Title:</b> %{customdata[0]}<br>"
                "<b>Completed Projects:</b> %{x:,}<br>"
                "<b>Success Score:</b> %{customdata[1]:.1f}%<br>"
                "<b>Completion Rate:</b> %{customdata[2]:.1f}%"
                "<extra></extra>"
            ),
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Completed Projects Count",
            yaxis_title="Freelancer",
            height=max(400, len(df_sorted) * 28),
        )

    return ChartCard(
        title="Top Users by Number of Projects",
        description="Identifies the highest-volume freelancers on the platform sorted by completed project count and colored by success rating.",
        figure=fig,
        section="Concentration",
        card_id="top_users_by_projects",
    )


def plot_project_concentration_percentiles(df: pd.DataFrame) -> ChartCard:
    """Bar chart showing project volume held by top population percentiles."""
    if df.empty:
        fig = create_empty_figure("No concentration percentile data.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=df["tier"],
            y=df["project_share_pct"],
            marker=dict(color="#e94560", line=dict(color="#0f172a", width=1)),
            text=[f"{p:.1f}% of projects<br>({u:.1f}% users)" for p, u in zip(df["project_share_pct"], df["user_share_pct"])],
            textposition="auto",
            hovertemplate="<b>Tier:</b> %{x}<br><b>Project Share:</b> %{y:.2f}%<br><b>User Share:</b> %{customdata[0]:.2f}%<br><b>Tier Projects:</b> %{customdata[1]:,}<extra></extra>",
            customdata=list(zip(df["user_share_pct"], df["tier_projects"])),
        ))

        apply_standard_layout(
            fig,
            xaxis_title="User Population Tier",
            yaxis_title="Share of Total Completed Projects (%)",
            height=400,
        )
        fig.update_yaxes(range=[0, 105])

    return ChartCard(
        title="Project Concentration Across Users",
        description="Quantifies project concentration held by top percentiles (Top 1%, 5%, 10%, 20%) compared to the remaining 80% of users.",
        figure=fig,
        section="Concentration",
        card_id="project_concentration_percentiles",
    )


def plot_pareto_project_activity(df: pd.DataFrame) -> ChartCard:
    """Pareto / Lorenz curve chart showing cumulative user % vs cumulative project %."""
    if df.empty:
        fig = create_empty_figure("No Pareto curve data available.")
    else:
        fig = go.Figure()

        # Cumulative projects curve
        fig.add_trace(go.Scatter(
            x=df["cum_user_pct"],
            y=df["cum_project_pct"],
            mode="lines",
            name="Cumulative Projects",
            line=dict(color="#38bdf8", width=3),
            fill="tozeroy",
            fillcolor="rgba(56, 189, 248, 0.15)",
            hovertemplate="<b>Top %{x:.1f}% of Users</b> generate <b>%{y:.1f}%</b> of all completed projects<extra></extra>",
        ))

        # Equality line
        fig.add_trace(go.Scatter(
            x=df["cum_user_pct"],
            y=df["equality_line"],
            mode="lines",
            name="Line of Perfect Equality",
            line=dict(color="#94a3b8", width=2, dash="dash"),
            hovertemplate="Perfect Equality: %{x:.1f}%<extra></extra>",
        ))

        apply_standard_layout(
            fig,
            xaxis_title="Top Cumulative User Population (%)",
            yaxis_title="Cumulative Completed Projects Share (%)",
            height=430,
        )
        fig.update_xaxes(range=[0, 100])
        fig.update_yaxes(range=[0, 105])

    return ChartCard(
        title="80/20 Pareto Project Activity Analysis",
        description="Lorenz concentration curve showing what proportion of all platform completed projects is driven by top-ranking user percentiles.",
        figure=fig,
        section="Concentration",
        card_id="pareto_project_activity",
    )


def plot_project_activity_outliers(stats: Dict[str, Any]) -> ChartCard:
    """Summary card and box-range indicator for statistical project activity outliers."""
    if not stats or stats.get("q1") is None:
        fig = create_empty_figure("No outlier metrics available.")
    else:
        q1 = stats.get("q1", 0.0)
        med = stats.get("median", 0.0)
        q3 = stats.get("q3", 0.0)
        upper_fence = stats.get("upper_fence", 0.0)
        outlier_count = stats.get("outlier_count", 0)
        outlier_pct = stats.get("outlier_percentage", 0.0)

        fig = go.Figure()

        # Horizontal Box representation
        fig.add_trace(go.Box(
            x=[q1, med, q3, upper_fence],
            q1=[q1],
            median=[med],
            q3=[q3],
            upperfence=[upper_fence],
            lowerfence=[q1],
            orientation="h",
            name="Project Activity",
            marker_color="#818cf8",
            boxpoints=False,
            hovertemplate="<b>Q1 (25%):</b> %{q1:.1f}<br><b>Median (50%):</b> %{median:.1f}<br><b>Q3 (75%):</b> %{q3:.1f}<br><b>IQR Upper Fence:</b> %{upperfence:.1f}<extra></extra>",
        ))

        fig.add_annotation(
            text=f"<b>Statistical Outliers (>{upper_fence:.1f} projects):</b> {outlier_count:,} users ({outlier_pct:.2f}% of population)",
            xref="paper",
            yref="paper",
            x=0.5,
            y=1.12,
            showarrow=False,
            font=dict(size=13, color="#fbbf24"),
        )

        apply_standard_layout(
            fig,
            xaxis_title="Projects Volume Scale",
            yaxis_title="",
            height=280,
        )

    return ChartCard(
        title="Project Activity Outliers",
        description="Statistical quartile analysis (Q1, Median, Q3, IQR) identifying high-performing statistical outliers exceeding standard activity fences.",
        figure=fig,
        section="Concentration",
        card_id="project_activity_outliers",
        extra_kpis=stats,
    )
