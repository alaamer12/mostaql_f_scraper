"""Chart styling helpers, metadata wrappers, and layout formatters."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
import plotly.graph_objects as go
import plotly.express as px
from ..config import get_default_config, DashboardConfig


@dataclass
class ChartCard:
    """Standard container for single-column dashboard cards [Title, Description, Chart]."""
    title: str
    description: str
    figure: go.Figure
    section: str = "General"
    card_id: str = ""
    extra_kpis: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "title": self.title,
            "description": self.description,
            "figure": self.figure,
            "section": self.section,
            "card_id": self.card_id or self.title.lower().replace(" ", "_"),
            "extra_kpis": self.extra_kpis or {},
        }


def apply_standard_layout(
    fig: go.Figure,
    title: str = "",
    xaxis_title: str = "",
    yaxis_title: str = "",
    height: int = 440,
    config: Optional[DashboardConfig] = None,
) -> go.Figure:
    """Apply consistent modern dark theme styling across all Plotly figures."""
    cfg = config or get_default_config()
    theme = cfg.theme

    fig.update_layout(
        template=theme.get("plotly_template", "plotly_dark"),
        paper_bgcolor=theme.get("card_bg", "#1e293b"),
        plot_bgcolor="rgba(15, 23, 42, 0.6)", # semi-transparent slate
        font=dict(
            family="Inter, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
            size=13,
            color=theme.get("text_primary", "#f8fafc"),
        ),
        title=dict(
            text=f"<b>{title}</b>" if title else None,
            font=dict(size=15, color=theme.get("text_primary", "#f8fafc")),
            x=0.02,
            xanchor="left",
            y=0.96,
        ) if title else None,
        xaxis=dict(
            title=xaxis_title if xaxis_title else None,
            gridcolor=theme.get("card_border", "#334155"),
            zerolinecolor=theme.get("card_border", "#334155"),
            showline=True,
            linecolor=theme.get("card_border", "#334155"),
            tickfont=dict(color=theme.get("text_secondary", "#94a3b8")),
            title_font=dict(size=12, color=theme.get("text_secondary", "#94a3b8")),
        ),
        yaxis=dict(
            title=yaxis_title if yaxis_title else None,
            gridcolor=theme.get("card_border", "#334155"),
            zerolinecolor=theme.get("card_border", "#334155"),
            showline=True,
            linecolor=theme.get("card_border", "#334155"),
            tickfont=dict(color=theme.get("text_secondary", "#94a3b8")),
            title_font=dict(size=12, color=theme.get("text_secondary", "#94a3b8")),
        ),
        height=height,
        margin=dict(l=55, r=25, t=45, b=50),
        hoverlabel=dict(
            bgcolor=theme.get("card_bg", "#1e293b"),
            font_size=13,
            font_family="Inter, sans-serif",
            bordercolor=theme.get("accent_primary", "#38bdf8"),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1.0,
            font=dict(size=11, color=theme.get("text_secondary", "#94a3b8")),
        ),
    )
    return fig


def create_empty_figure(message: str = "No data available for this visualization.") -> go.Figure:
    """Create a placeholder figure when required fields are missing."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=14, color="#94a3b8"),
    )
    return apply_standard_layout(fig, height=220)
