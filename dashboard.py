"""
=============================================================================
  MOSTAQL FREELANCERS — ANALYTICS LEADERBOARD DASHBOARD
  Stack : Dash + Dash Bootstrap Components (DARKLY) + Plotly Express
  Input : mostaql_freelancers_analytics.json  (produced by scraper.py)
=============================================================================
"""

import json
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import dash
from dash import dcc, html, dash_table, Input, Output, callback
import dash_bootstrap_components as dbc

# ---------------------------------------------------------------------------
# DATA LOADING & PREPARATION
# ---------------------------------------------------------------------------

DATA_FILE = "mostaql_development_profiles.json"


def load_data(filepath: str = DATA_FILE) -> pd.DataFrame:
    """Load JSON dataset and reconstruct correct types."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw)

    # Ensure numeric columns
    numeric_cols = [
        "employment_rate", "received_projects", "financial_deals",
        "completion_rate", "ontime_delivery_rate", "rehire_rate",
        "communication_success_rate", "total_completed_projects",
        "avg_response_time_minutes", "portfolio_count", "skills_count",
        "success_score",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Reconstruct skills list from JSON string if needed
    if "skills" in df.columns:
        df["skills"] = df["skills"].apply(
            lambda x: x if isinstance(x, list)
            else (json.loads(x) if isinstance(x, str) and x.startswith("[") else [])
        )

    # Sort by success_score
    if "success_score" in df.columns:
        df = df.sort_values("success_score", ascending=False).reset_index(drop=True)
        df.index += 1
        df.index.name = "rank"

    return df


df = load_data()


# ---------------------------------------------------------------------------
# COLOUR PALETTE & THEME CONSTANTS
# ---------------------------------------------------------------------------

THEME = {
    "bg"        : "#1a1a2e",
    "card_bg"   : "#16213e",
    "accent1"   : "#0f3460",
    "accent2"   : "#e94560",
    "accent3"   : "#533483",
    "text"      : "#e0e0e0",
    "muted"     : "#8892a4",
    "success"   : "#00d4aa",
    "warning"   : "#ffc107",
    "danger"    : "#e94560",
    "border"    : "#2a2a4a",
}

PLOTLY_TEMPLATE = "plotly_dark"

COLOR_SEQ = [
    "#00d4aa", "#e94560", "#7c5cbf", "#ffc107",
    "#17a2b8", "#fd7e14", "#6f42c1", "#20c997",
]


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def kpi_card(title: str, value: str, subtitle: str = "", color: str = "#00d4aa") -> dbc.Col:
    """Reusable KPI summary card."""
    return dbc.Col(
        dbc.Card([
            dbc.CardBody([
                html.P(title, className="text-muted small mb-1",
                       style={"letterSpacing": "0.08em", "textTransform": "uppercase"}),
                html.H3(value, style={"color": color, "fontWeight": "700", "margin": "4px 0"}),
                html.P(subtitle, className="text-muted small mb-0"),
            ])
        ], style={
            "background"   : THEME["card_bg"],
            "border"       : f"1px solid {THEME['border']}",
            "borderLeft"   : f"4px solid {color}",
            "borderRadius" : "8px",
        }),
        xs=12, sm=6, md=3, className="mb-3"
    )


def section_header(title: str, subtitle: str = "") -> html.Div:
    """Styled section header."""
    return html.Div([
        html.H5(title, style={
            "color"        : THEME["text"],
            "fontWeight"   : "600",
            "marginBottom" : "4px",
        }),
        html.P(subtitle, style={
            "color"  : THEME["muted"],
            "fontSize": "0.82rem",
            "margin" : "0 0 16px 0",
        }) if subtitle else html.Span(),
    ], className="mt-4 mb-3")


def card_wrap(children, padding: str = "20px") -> dbc.Card:
    """Wrap content in a dark card."""
    return dbc.Card(
        dbc.CardBody(children, style={"padding": padding}),
        style={
            "background"  : THEME["card_bg"],
            "border"      : f"1px solid {THEME['border']}",
            "borderRadius": "10px",
            "marginBottom": "20px",
        }
    )


# ---------------------------------------------------------------------------
# COMPUTED AGGREGATES FOR KPI CARDS
# ---------------------------------------------------------------------------

top_score       = f"{df['success_score'].max():.1f}" if not df.empty else "—"
avg_completion  = f"{df['completion_rate'].mean():.1f}%" if not df.empty else "—"
total_indexed   = str(len(df))
top_skill       = "—"

if not df.empty and "skills" in df.columns:
    from collections import Counter
    all_skills = [s for sublist in df["skills"] for s in sublist]
    if all_skills:
        top_skill = Counter(all_skills).most_common(1)[0][0]

top_freelancer_name = df["name"].iloc[0] if not df.empty and "name" in df.columns else "—"


# ---------------------------------------------------------------------------
# CHART BUILDERS
# ---------------------------------------------------------------------------

def build_leaderboard_bar() -> go.Figure:
    """Horizontal grouped bar: completed projects vs portfolio count per user."""
    top = df.head(20).reset_index(drop=True)
    labels = top["name"].fillna(top["profile_url"])

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Completed Projects",
        y=labels,
        x=top["total_completed_projects"],
        orientation="h",
        marker_color=THEME["success"],
        hovertemplate="<b>%{y}</b><br>Completed: %{x}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Portfolio Cards",
        y=labels,
        x=top["portfolio_count"],
        orientation="h",
        marker_color=THEME["accent2"],
        hovertemplate="<b>%{y}</b><br>Portfolio: %{x}<extra></extra>",
    ))
    fig.update_layout(
        title="Top 20 — Completed Projects vs Portfolio Depth",
        barmode="group",
        template=PLOTLY_TEMPLATE,
        paper_bgcolor=THEME["card_bg"],
        plot_bgcolor=THEME["card_bg"],
        height=600,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(autorange="reversed"),
        margin=dict(l=180, r=20, t=60, b=40),
        font=dict(color=THEME["text"]),
    )
    return fig


def build_scatter_matrix() -> go.Figure:
    """
    Scatter: On-Time Delivery vs Completion Rate.
    Bubble size = total completed projects.
    Colour = success_score.
    """
    plot_df = df.copy().reset_index(drop=True)
    plot_df["bubble_size"] = np.sqrt(plot_df["total_completed_projects"].clip(1)) * 4
    plot_df["label"] = plot_df["name"].fillna(plot_df["profile_url"])

    fig = px.scatter(
        plot_df,
        x="ontime_delivery_rate",
        y="completion_rate",
        size="bubble_size",
        color="success_score",
        hover_name="label",
        hover_data={
            "total_completed_projects": True,
            "rehire_rate"             : True,
            "bubble_size"             : False,
        },
        color_continuous_scale="Turbo",
        labels={
            "ontime_delivery_rate": "On-Time Delivery Rate (%)",
            "completion_rate"     : "Project Completion Rate (%)",
            "success_score"       : "Success Score",
        },
        title="Delivery Reliability vs Completion Rate (bubble = volume)",
        template=PLOTLY_TEMPLATE,
    )
    fig.update_layout(
        paper_bgcolor=THEME["card_bg"],
        plot_bgcolor=THEME["card_bg"],
        height=520,
        font=dict(color=THEME["text"]),
        margin=dict(l=60, r=20, t=60, b=60),
    )
    fig.update_traces(marker=dict(opacity=0.8, line=dict(width=0.5, color="white")))
    return fig


def build_skill_frequency() -> go.Figure:
    """Horizontal bar: top 25 skills by frequency across all freelancers."""
    from collections import Counter
    all_skills = [s for sublist in df["skills"] for s in sublist]
    if not all_skills:
        return go.Figure()

    counts = Counter(all_skills).most_common(25)
    skills_list, freq_list = zip(*counts)

    fig = go.Figure(go.Bar(
        x=list(freq_list),
        y=list(skills_list),
        orientation="h",
        marker=dict(
            color=list(freq_list),
            colorscale="Viridis",
            showscale=True,
            colorbar=dict(title="Count"),
        ),
        hovertemplate="<b>%{y}</b><br>Freelancers: %{x}<extra></extra>",
    ))
    fig.update_layout(
        title="Top 25 Platform Skills by Frequency",
        template=PLOTLY_TEMPLATE,
        paper_bgcolor=THEME["card_bg"],
        plot_bgcolor=THEME["card_bg"],
        height=600,
        yaxis=dict(autorange="reversed"),
        margin=dict(l=200, r=60, t=60, b=40),
        font=dict(color=THEME["text"]),
    )
    return fig


def build_skill_completion_rate() -> go.Figure:
    """Bar: average completion rate per skill (top 20 skills)."""
    from collections import Counter

    all_skills = [s for sublist in df["skills"] for s in sublist]
    if not all_skills:
        return go.Figure()

    top_skills = [s for s, _ in Counter(all_skills).most_common(20)]

    rows = []
    for _, row in df.iterrows():
        for skill in row.get("skills", []):
            if skill in top_skills:
                rows.append({
                    "skill"          : skill,
                    "completion_rate": row["completion_rate"],
                    "success_score"  : row["success_score"],
                })

    if not rows:
        return go.Figure()

    skill_df = pd.DataFrame(rows)
    agg = (skill_df.groupby("skill")
                   .agg(avg_completion=("completion_rate", "mean"),
                        avg_score=("success_score", "mean"),
                        count=("completion_rate", "count"))
                   .reset_index()
                   .sort_values("avg_completion", ascending=True))

    fig = go.Figure(go.Bar(
        x=agg["avg_completion"],
        y=agg["skill"],
        orientation="h",
        marker=dict(
            color=agg["avg_score"],
            colorscale="RdYlGn",
            showscale=True,
            colorbar=dict(title="Avg Score"),
        ),
        text=agg["avg_completion"].round(1).astype(str) + "%",
        textposition="outside",
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Avg Completion: %{x:.1f}%<br>"
            "Freelancers: %{customdata}<extra></extra>"
        ),
        customdata=agg["count"],
    ))
    fig.update_layout(
        title="Average Completion Rate by Skill (top 20 skills)",
        template=PLOTLY_TEMPLATE,
        paper_bgcolor=THEME["card_bg"],
        plot_bgcolor=THEME["card_bg"],
        height=600,
        xaxis=dict(range=[0, 115]),
        margin=dict(l=200, r=80, t=60, b=40),
        font=dict(color=THEME["text"]),
    )
    return fig


def build_distribution_histograms() -> go.Figure:
    """3-panel histogram grid: response time, rehire rate, portfolio size."""
    fig = make_subplots(
        rows=1, cols=3,
        subplot_titles=[
            "Response Time Distribution (minutes)",
            "Rehire Rate Distribution (%)",
            "Portfolio Size Distribution",
        ],
    )

    # Panel 1 — Response Time
    rt = df["avg_response_time_minutes"][df["avg_response_time_minutes"] > 0]
    fig.add_trace(go.Histogram(
        x=rt, nbinsx=30,
        marker_color=THEME["success"], name="Response Time",
        hovertemplate="Range: %{x}<br>Count: %{y}<extra></extra>",
    ), row=1, col=1)

    # Panel 2 — Rehire Rate
    rr = df["rehire_rate"][df["rehire_rate"] > 0]
    fig.add_trace(go.Histogram(
        x=rr, nbinsx=25,
        marker_color=THEME["accent2"], name="Rehire Rate",
        hovertemplate="Rate: %{x:.1f}%<br>Count: %{y}<extra></extra>",
    ), row=1, col=2)

    # Panel 3 — Portfolio Size
    ps = df["portfolio_count"][df["portfolio_count"] > 0]
    fig.add_trace(go.Histogram(
        x=ps, nbinsx=20,
        marker_color=THEME["warning"], name="Portfolio Size",
        hovertemplate="Cards: %{x}<br>Count: %{y}<extra></extra>",
    ), row=1, col=3)

    fig.update_layout(
        title_text="Operational Distributions",
        template=PLOTLY_TEMPLATE,
        paper_bgcolor=THEME["card_bg"],
        plot_bgcolor=THEME["card_bg"],
        showlegend=False,
        height=420,
        font=dict(color=THEME["text"]),
        margin=dict(l=40, r=40, t=80, b=40),
    )
    # Style subplot backgrounds
    for i in range(1, 4):
        fig.update_xaxes(gridcolor=THEME["border"], row=1, col=i)
        fig.update_yaxes(gridcolor=THEME["border"], row=1, col=i)

    return fig


def build_success_score_distribution() -> go.Figure:
    """Violin + box overlay of success score distribution."""
    fig = go.Figure()
    fig.add_trace(go.Violin(
        y=df["success_score"],
        box_visible=True,
        meanline_visible=True,
        fillcolor=THEME["accent3"],
        opacity=0.7,
        line_color=THEME["success"],
        name="Success Score",
        hovertemplate="Score: %{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        title="Success Score Distribution Across All Freelancers",
        template=PLOTLY_TEMPLATE,
        paper_bgcolor=THEME["card_bg"],
        plot_bgcolor=THEME["card_bg"],
        height=380,
        showlegend=False,
        font=dict(color=THEME["text"]),
        yaxis=dict(title="Success Score", gridcolor=THEME["border"]),
        margin=dict(l=60, r=40, t=60, b=40),
    )
    return fig


# ---------------------------------------------------------------------------
# DATA TABLE COLUMNS & FORMATTING
# ---------------------------------------------------------------------------

TABLE_COLUMNS = [
    {"name": "Rank",           "id": "rank",                       "type": "numeric"},
    {"name": "Name",           "id": "name",                       "type": "text"},
    {"name": "Title",          "id": "title",                      "type": "text"},
    {"name": "Score",          "id": "success_score",              "type": "numeric",  "format": {"specifier": ".2f"}},
    {"name": "Completion %",   "id": "completion_rate",            "type": "numeric",  "format": {"specifier": ".1f"}},
    {"name": "On-Time %",      "id": "ontime_delivery_rate",       "type": "numeric",  "format": {"specifier": ".1f"}},
    {"name": "Rehire %",       "id": "rehire_rate",                "type": "numeric",  "format": {"specifier": ".1f"}},
    {"name": "Comm. %",        "id": "communication_success_rate", "type": "numeric",  "format": {"specifier": ".1f"}},
    {"name": "Employ. %",      "id": "employment_rate",            "type": "numeric",  "format": {"specifier": ".1f"}},
    {"name": "Completed",      "id": "total_completed_projects",   "type": "numeric"},
    {"name": "Received",       "id": "received_projects",          "type": "numeric"},
    {"name": "Portfolio",      "id": "portfolio_count",            "type": "numeric"},
    {"name": "Skills #",       "id": "skills_count",               "type": "numeric"},
    {"name": "Response (min)", "id": "avg_response_time_minutes",  "type": "numeric"},
    {"name": "Skills",         "id": "skills_str",                 "type": "text"},
]

CONDITIONAL_STYLES = [
    # Success score gradient
    {"if": {"filter_query": "{success_score} >= 80", "column_id": "success_score"},
     "backgroundColor": "#003322", "color": "#00d4aa", "fontWeight": "bold"},
    {"if": {"filter_query": "{success_score} >= 60 && {success_score} < 80", "column_id": "success_score"},
     "backgroundColor": "#1a2a00", "color": "#90ee90"},
    {"if": {"filter_query": "{success_score} < 40", "column_id": "success_score"},
     "backgroundColor": "#2a0000", "color": "#ff6b6b"},
    # Completion rate
    {"if": {"filter_query": "{completion_rate} >= 95", "column_id": "completion_rate"},
     "backgroundColor": "#003322", "color": "#00d4aa"},
    {"if": {"filter_query": "{completion_rate} < 70", "column_id": "completion_rate"},
     "backgroundColor": "#2a0000", "color": "#ff6b6b"},
    # On-time delivery
    {"if": {"filter_query": "{ontime_delivery_rate} >= 80", "column_id": "ontime_delivery_rate"},
     "backgroundColor": "#003322", "color": "#00d4aa"},
    {"if": {"filter_query": "{ontime_delivery_rate} < 50", "column_id": "ontime_delivery_rate"},
     "backgroundColor": "#2a1000", "color": "#ffc107"},
    # Alternating rows
    {"if": {"row_index": "odd"}, "backgroundColor": "#1a1f35"},
]


def prepare_table_data(dataframe: pd.DataFrame) -> list[dict]:
    """Flatten DataFrame for DataTable consumption."""
    display_df = dataframe.reset_index(drop=True)[
        [c["id"] for c in TABLE_COLUMNS if c["id"] in dataframe.reset_index(drop=True).columns]
    ].copy()
    display_df["skills_str"] = display_df.get("skills_str", pd.Series([""] * len(display_df)))
    return display_df.to_dict("records")


# ---------------------------------------------------------------------------
# APP LAYOUT
# ---------------------------------------------------------------------------

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    title="Mostaql Freelancers Analytics",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)

app.layout = dbc.Container(fluid=True, style={"backgroundColor": THEME["bg"], "minHeight": "100vh",
                                               "padding": "0 24px 40px 24px"}, children=[

    # ── Header ───────────────────────────────────────────────────────────────
    dbc.Row([
        dbc.Col([
            html.Div([
                html.H2("Mostaql Freelancers", style={
                    "color": THEME["text"], "fontWeight": "700",
                    "marginBottom": "2px", "marginTop": "28px",
                }),
                html.P("Analytics Leaderboard Dashboard", style={
                    "color": THEME["muted"], "fontSize": "0.9rem",
                    "marginBottom": "24px",
                }),
            ])
        ])
    ]),

    # ── KPI Cards ────────────────────────────────────────────────────────────
    dbc.Row([
        kpi_card("Top Success Score",     top_score,      f"#{top_freelancer_name}",     THEME["success"]),
        kpi_card("Avg Completion Rate",   avg_completion, "Platform-wide average",        THEME["warning"]),
        kpi_card("Freelancers Indexed",   total_indexed,  "Total profiles scraped",       THEME["accent2"]),
        kpi_card("Most Popular Skill",    top_skill,      "By frequency across profiles", "#7c5cbf"),
    ], className="g-3"),

    html.Hr(style={"borderColor": THEME["border"], "margin": "10px 0 20px 0"}),

    # ── Tabs ─────────────────────────────────────────────────────────────────
    dbc.Tabs(id="main-tabs", active_tab="tab-leaderboard", children=[

        # TAB 1 — LEADERBOARD TABLE
        dbc.Tab(label="Leaderboard Grid", tab_id="tab-leaderboard",
                label_style={"color": THEME["muted"]},
                active_label_style={"color": THEME["success"], "fontWeight": "600"}),

        # TAB 2 — PERFORMANCE CHARTS
        dbc.Tab(label="Performance Analysis", tab_id="tab-performance",
                label_style={"color": THEME["muted"]},
                active_label_style={"color": THEME["success"], "fontWeight": "600"}),

        # TAB 3 — SKILLS INTELLIGENCE
        dbc.Tab(label="Skills Intelligence", tab_id="tab-skills",
                label_style={"color": THEME["muted"]},
                active_label_style={"color": THEME["success"], "fontWeight": "600"}),

        # TAB 4 — DISTRIBUTIONS
        dbc.Tab(label="Distributions", tab_id="tab-distributions",
                label_style={"color": THEME["muted"]},
                active_label_style={"color": THEME["success"], "fontWeight": "600"}),
    ], style={"marginBottom": "20px"}),

    # Tab content area
    html.Div(id="tab-content"),

])


# ---------------------------------------------------------------------------
# CALLBACKS
# ---------------------------------------------------------------------------

@callback(Output("tab-content", "children"), Input("main-tabs", "active_tab"))
def render_tab(tab: str) -> html.Div:

    # ── TAB 1: Leaderboard DataTable ─────────────────────────────────────────
    if tab == "tab-leaderboard":
        return html.Div([
            section_header(
                "Interactive Leaderboard Grid",
                "Sort, filter, and paginate across all scraped metrics. "
                "Use column headers to sort; filter row for queries like >80."
            ),
            card_wrap(
                dash_table.DataTable(
                    id="main-table",
                    columns=TABLE_COLUMNS,
                    data=prepare_table_data(df),
                    sort_action="native",
                    sort_mode="multi",
                    filter_action="native",
                    page_action="native",
                    page_size=20,
                    style_table={
                        "overflowX": "auto",
                        "borderRadius": "8px",
                    },
                    style_cell={
                        "backgroundColor": THEME["card_bg"],
                        "color"          : THEME["text"],
                        "border"         : f"1px solid {THEME['border']}",
                        "padding"        : "8px 12px",
                        "fontSize"       : "0.82rem",
                        "fontFamily"     : "Inter, system-ui, sans-serif",
                        "maxWidth"       : "220px",
                        "overflow"       : "hidden",
                        "textOverflow"   : "ellipsis",
                        "whiteSpace"     : "nowrap",
                    },
                    style_header={
                        "backgroundColor": THEME["accent1"],
                        "color"          : THEME["text"],
                        "fontWeight"     : "600",
                        "border"         : f"1px solid {THEME['border']}",
                        "textAlign"      : "center",
                        "fontSize"       : "0.8rem",
                        "letterSpacing"  : "0.04em",
                    },
                    style_data_conditional=CONDITIONAL_STYLES,
                    style_filter={
                        "backgroundColor": "#0d1730",
                        "color"          : THEME["text"],
                    },
                    tooltip_data=[
                        {col["id"]: {"value": str(row.get(col["id"], "")), "type": "markdown"}
                         for col in TABLE_COLUMNS}
                        for row in prepare_table_data(df)
                    ],
                    tooltip_delay=0,
                    tooltip_duration=None,
                )
            ),
        ])

    # ── TAB 2: Performance Charts ─────────────────────────────────────────────
    elif tab == "tab-performance":
        return html.Div([
            section_header(
                "Performance Analysis",
                "Ranking profiles and delivery reliability modelling."
            ),
            card_wrap(dcc.Graph(figure=build_leaderboard_bar(), config={"displayModeBar": True})),
            card_wrap(dcc.Graph(figure=build_scatter_matrix(), config={"displayModeBar": True})),
            card_wrap(dcc.Graph(figure=build_success_score_distribution(), config={"displayModeBar": True})),
        ])

    # ── TAB 3: Skills Intelligence ────────────────────────────────────────────
    elif tab == "tab-skills":
        return html.Div([
            section_header(
                "Skills Intelligence",
                "Platform-wide skill frequency and performance breakdown by skill category."
            ),
            dbc.Row([
                dbc.Col(
                    card_wrap(dcc.Graph(figure=build_skill_frequency(), config={"displayModeBar": True})),
                    md=12
                ),
            ]),
            dbc.Row([
                dbc.Col(
                    card_wrap(dcc.Graph(figure=build_skill_completion_rate(), config={"displayModeBar": True})),
                    md=12
                ),
            ]),
        ])

    # ── TAB 4: Distributions ─────────────────────────────────────────────────
    elif tab == "tab-distributions":
        return html.Div([
            section_header(
                "Operational Distributions",
                "Statistical frequency distributions across key platform metrics."
            ),
            card_wrap(dcc.Graph(figure=build_distribution_histograms(), config={"displayModeBar": True})),

            # Additional distribution: Employment Rate vs Received Projects
            card_wrap(
                dcc.Graph(
                    figure=px.scatter(
                        df.reset_index(drop=True),
                        x="employment_rate",
                        y="received_projects",
                        color="success_score",
                        size=df["total_completed_projects"].clip(1).values,
                        hover_name="name",
                        color_continuous_scale="Plasma",
                        labels={
                            "employment_rate"   : "Employment Rate (%)",
                            "received_projects" : "Received Projects",
                            "success_score"     : "Success Score",
                        },
                        title="Employment Rate vs Received Projects",
                        template=PLOTLY_TEMPLATE,
                    ).update_layout(
                        paper_bgcolor=THEME["card_bg"],
                        plot_bgcolor=THEME["card_bg"],
                        height=420,
                        font=dict(color=THEME["text"]),
                        margin=dict(l=60, r=40, t=60, b=60),
                    ),
                    config={"displayModeBar": True},
                )
            ),
        ])

    return html.Div("Select a tab above.", style={"color": THEME["muted"], "padding": "40px"})


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Mostaql Freelancers Analytics Dashboard")
    print(f"  Dataset loaded: {len(df)} freelancers")
    print("  Open: http://127.0.0.1:8050")
    print("=" * 60 + "\n")

    app.run(debug=False, host="0.0.0.0", port=8050)
