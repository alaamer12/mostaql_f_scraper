"""Single-Column Analytics Dashboard Orchestrator and HTML Generator."""

import os
import sys
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import plotly.io as pio

# Windows console encoding safeguard
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .config import DashboardConfig, get_default_config
from .db.connection import DashboardDatabase
from .db.sources import DatasetSourceManager
from .db.schema import FieldCapabilityChecker, SchemaInspector
from .plotter.helpers import ChartCard
from . import analyzer
from . import plotter

logger = logging.getLogger("dashboard.orchestrator")


class DuckDBAnalyticsDashboard:
    """Orchestrates dataset discovery, analytical aggregations, and single-column rendering."""

    def __init__(
        self,
        config: Optional[DashboardConfig] = None,
        db: Optional[DashboardDatabase] = None,
    ):
        self.config = config or get_default_config()
        self.db = db or DashboardDatabase(config=self.config)
        self.sources = DatasetSourceManager(config=self.config)
        self._registered_tables: Dict[str, str] = {}

    def prepare_standard_datasets(self, use_parquet_cache: Optional[bool] = None) -> Dict[str, str]:
        """Register default datasets into the DuckDB analytical engine."""
        self._registered_tables = self.sources.register_standard_datasets(
            self.db, use_parquet_cache=use_parquet_cache
        )
        return self._registered_tables

    def register_custom_dataset(
        self,
        view_name: str,
        json_path: Union[str, Path, List[Union[str, Path]]],
        use_parquet_cache: Optional[bool] = None,
    ) -> str:
        """Register a custom JSON file or list of JSON files as an analytical table."""
        if isinstance(json_path, (list, tuple)):
            name = self.sources.register_datasets(
                self.db, view_name, list(json_path), use_parquet_cache=use_parquet_cache
            )
        else:
            name = self.sources.register_dataset(
                self.db, view_name, json_path, use_parquet_cache=use_parquet_cache
            )
        self._registered_tables[view_name] = name
        return name

    def register_inputs(
        self,
        json_inputs: List[Union[str, Path]],
        view_name: str = "dataset",
        use_parquet_cache: Optional[bool] = None,
    ) -> str:
        """Register multiple JSON input files (concatenated/unioned) into DuckDB."""
        return self.register_custom_dataset(
            view_name=view_name,
            json_path=json_inputs,
            use_parquet_cache=use_parquet_cache,
        )

    def generate_all_cards(self, table_name: str = "analysis") -> List[ChartCard]:
        """Dynamically discover capabilities and generate the complete single-column chart catalog."""
        if not self.db.table_exists(table_name):
            # Attempt auto-registering standard datasets
            self.prepare_standard_datasets()

        if not self.db.table_exists(table_name):
            available = self.db.list_tables()
            if available:
                table_name = available[0]
            else:
                raise ValueError(f"No valid queryable table found for analytics (requested '{table_name}').")

        cap = FieldCapabilityChecker(self.db, table_name)
        cards: List[ChartCard] = []

        # =========================================================================
        # SECTION 1 — Overview
        # =========================================================================
        # 1. Dataset Overview KPI Cards
        kpi_data = analyzer.get_dataset_kpis(self.db, table_name)
        cards.append(plotter.plot_dataset_kpis(kpi_data))

        # 2. Missing Data by Field
        missing_df = analyzer.get_missing_data_by_field(self.db, table_name)
        if not missing_df.empty:
            cards.append(plotter.plot_missing_data_by_field(missing_df))

        # =========================================================================
        # SECTION 2 — User Activity & Project Distributions
        # =========================================================================
        # 3. Users vs. Number of Projects (Scattergl)
        scatter_df = analyzer.get_user_project_scatter_data(self.db, table_name)
        if not scatter_df.empty:
            cards.append(plotter.plot_user_project_scatter(scatter_df))

        # 4. Distribution of Users by Number of Projects
        ranges_df = analyzer.get_project_count_ranges(self.db, table_name)
        if not ranges_df.empty:
            cards.append(plotter.plot_project_count_ranges(ranges_df))

        # 5. Project Count Histogram
        hist_df = analyzer.get_project_count_histogram(self.db, table_name)
        if not hist_df.empty:
            cards.append(plotter.plot_project_count_histogram(hist_df))

        # 6. Cumulative Distribution of Users by Project Count (ECDF)
        ecdf_df = analyzer.get_cumulative_user_distribution(self.db, table_name)
        if not ecdf_df.empty:
            cards.append(plotter.plot_cumulative_user_distribution(ecdf_df))

        # 7. User Activity Segments
        seg_df = analyzer.get_user_activity_segments(self.db, table_name)
        if not seg_df.empty:
            cards.append(plotter.plot_user_activity_segments(seg_df))

        # 8. Project Distribution — Logarithmic Scale
        log_df = analyzer.get_log_scale_distribution(self.db, table_name)
        if not log_df.empty:
            cards.append(plotter.plot_log_scale_distribution(log_df))

        # =========================================================================
        # SECTION 3 — Concentration & Outlier Analysis
        # =========================================================================
        # 9. Top Users by Number of Projects
        top_users_df = analyzer.get_top_users_by_projects(self.db, table_name)
        if not top_users_df.empty:
            cards.append(plotter.plot_top_users_by_projects(top_users_df))

        # 10. Project Concentration Across Users
        conc_df = analyzer.get_project_concentration_percentiles(self.db, table_name)
        if not conc_df.empty:
            cards.append(plotter.plot_project_concentration_percentiles(conc_df))

        # 11. 80/20 Pareto Project Activity Analysis
        pareto_df = analyzer.get_pareto_project_activity(self.db, table_name)
        if not pareto_df.empty:
            cards.append(plotter.plot_pareto_project_activity(pareto_df))

        # 12. Project Activity Outliers
        outlier_stats = analyzer.get_project_activity_outliers(self.db, table_name)
        if outlier_stats:
            cards.append(plotter.plot_project_activity_outliers(outlier_stats))

        # =========================================================================
        # SECTION 4 — Category Analysis (Graceful Fallback if missing)
        # =========================================================================
        if cap.has_category():
            # 13. Projects by Category
            cat_proj_df = analyzer.get_projects_by_category(self.db, table_name)
            if not cat_proj_df.empty:
                cards.append(plotter.plot_projects_by_category(cat_proj_df))

            # 14. Users by Category
            cat_users_df = analyzer.get_users_by_category(self.db, table_name)
            if not cat_users_df.empty:
                cards.append(plotter.plot_users_by_category(cat_users_df))

            # 15. Average Projects per User by Category
            cat_ratio_df = analyzer.get_avg_projects_per_user_by_category(self.db, table_name)
            if not cat_ratio_df.empty:
                cards.append(plotter.plot_avg_projects_per_user_by_category(cat_ratio_df))

            # 16. Category Concentration
            cat_pareto_df = analyzer.get_category_concentration_pareto(self.db, table_name)
            if not cat_pareto_df.empty:
                cards.append(plotter.plot_category_concentration_pareto(cat_pareto_df))

            # 17. User Activity by Category
            cat_comp_df = analyzer.get_user_vs_project_category_comparison(self.db, table_name)
            if not cat_comp_df.empty:
                cards.append(plotter.plot_user_vs_project_category_comparison(cat_comp_df))

        # =========================================================================
        # SECTION 5 — Skills Analysis (Graceful Fallback if missing)
        # =========================================================================
        if cap.has_skills():
            # 18. Most Common Skills
            skills_df = analyzer.get_most_common_skills(self.db, table_name)
            if not skills_df.empty:
                cards.append(plotter.plot_most_common_skills(skills_df))

            # 19. Distribution of Skills per User
            skills_dist_df = analyzer.get_skills_per_user_distribution(self.db, table_name)
            if not skills_dist_df.empty:
                cards.append(plotter.plot_skills_per_user_distribution(skills_dist_df))

            # 20. Skills Count vs. Project Activity
            skills_act_df = analyzer.get_skills_vs_project_activity(self.db, table_name)
            if not skills_act_df.empty:
                cards.append(plotter.plot_skills_vs_project_activity(skills_act_df))

        # =========================================================================
        # SECTION 6 — Temporal Analysis (Graceful Fallback if missing)
        # =========================================================================
        if cap.has_temporal():
            # 21. Projects Over Time
            temp_proj_df = analyzer.get_temporal_project_activity(self.db, table_name)
            if not temp_proj_df.empty:
                cards.append(plotter.plot_temporal_project_activity(temp_proj_df))

            # 22. User/Profile Creation Over Time
            growth_df = analyzer.get_user_growth_over_time(self.db, table_name)
            if not growth_df.empty:
                cards.append(plotter.plot_user_growth_over_time(growth_df))

            # 23. Project Activity Over Time by Category
            if cap.has_category():
                temp_cat_df = analyzer.get_temporal_category_activity(self.db, table_name)
                if not temp_cat_df.empty:
                    cards.append(plotter.plot_temporal_category_activity(temp_cat_df))

        # =========================================================================
        # SECTION 7 — Geographic Analysis (Graceful Fallback if missing)
        # =========================================================================
        if cap.has_location():
            # 24. Geographic Distribution of Users
            geo_users_df = analyzer.get_geographic_user_distribution(self.db, table_name)
            if not geo_users_df.empty:
                cards.append(plotter.plot_geographic_user_distribution(geo_users_df))

            # 25. Project Activity by Location
            geo_act_df = analyzer.get_geographic_project_activity(self.db, table_name)
            if not geo_act_df.empty:
                cards.append(plotter.plot_geographic_project_activity(geo_act_df))

        # =========================================================================
        # SECTION 8 — Data Quality & Completeness
        # =========================================================================
        # 26. Profile Completeness Score Distribution
        comp_dist_df = analyzer.get_data_completeness_distribution(self.db, table_name)
        if not comp_dist_df.empty:
            cards.append(plotter.plot_data_completeness_distribution(comp_dist_df))

        # 27. Parse Confidence & Signal Distribution
        if cap.has_column("parse_confidence"):
            conf_df = analyzer.get_parse_confidence_distribution(self.db, table_name)
            if not conf_df.empty:
                cards.append(plotter.plot_parse_confidence_distribution(conf_df))

        # =========================================================================
        # SECTION 9 — Advanced Numeric Insights
        # =========================================================================
        # 28. Numeric Feature Correlations
        corr_df = analyzer.get_numeric_correlations(self.db, table_name)
        if not corr_df.empty:
            cards.append(plotter.plot_numeric_correlations(corr_df))

        # 29. Key Bivariate Relationships
        if cap.has_column("portfolio_count") and cap.has_projects():
            biv_df = analyzer.get_bivariate_relationship_samples(
                self.db, table_name, "portfolio_count", cap.get_projects_column()
            )
            if not biv_df.empty:
                cards.append(plotter.plot_bivariate_relationship_samples(
                    biv_df,
                    "portfolio_count",
                    cap.get_projects_column(),
                    title="Portfolio Count vs. Total Completed Projects",
                    description="Examines whether showcasing a higher volume of portfolio works directly correlates with securing and delivering more freelance contracts.",
                ))

        return cards

    def render_html(
        self,
        cards: List[ChartCard],
        page_title: str = "Mostaql Analytics Dashboard",
        dataset_name: str = "Mostaql Freelancers Analytics",
    ) -> str:
        """Generate a complete standalone, responsive single-column HTML dashboard."""
        sections_map: Dict[str, List[ChartCard]] = {}
        for c in cards:
            sections_map.setdefault(c.section, []).append(c)

        # Generate HTML snippet for each chart card
        cards_html_list = []
        for card in cards:
            chart_div = pio.to_html(
                card.figure,
                full_html=False,
                include_plotlyjs=False,
                config={"responsive": True, "displayModeBar": True},
            )
            card_html = f"""
            <div class="card-container" id="{card.card_id}">
                <div class="card-header">
                    <span class="card-section-badge">{card.section}</span>
                    <h3 class="card-title">{card.title}</h3>
                    <p class="card-description">{card.description}</p>
                </div>
                <div class="card-chart">
                    {chart_div}
                </div>
            </div>
            """
            cards_html_list.append(card_html)

        cards_body = "\n".join(cards_html_list)

        # Build Navigation links
        nav_links = []
        for sec in sections_map.keys():
            sec_id = sec.lower().replace(" ", "_")
            first_card = sections_map[sec][0]
            nav_links.append(f'<a href="#{first_card.card_id}" class="nav-chip">{sec}</a>')
        nav_html = "\n".join(nav_links)

        total_charts = len(cards)

        html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{page_title}</title>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #0f172a;
            --card-bg: #1e293b;
            --card-border: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent-sky: #38bdf8;
            --accent-indigo: #818cf8;
            --accent-emerald: #34d399;
            --accent-amber: #fbbf24;
            --accent-rose: #f43f5e;
            --card-radius: 12px;
            --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            background-color: var(--bg);
            color: var(--text-primary);
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            padding: 0;
            margin: 0;
            -webkit-font-smoothing: antialiased;
        }}

        /* Header */
        header {{
            background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%);
            border-bottom: 1px solid var(--card-border);
            padding: 40px 24px 30px;
            text-align: center;
        }}

        .header-content {{
            max-width: 1100px;
            margin: 0 auto;
        }}

        h1 {{
            font-size: 2.2rem;
            font-weight: 800;
            letter-spacing: -0.025em;
            background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 8px;
        }}

        .subtitle {{
            font-size: 1.05rem;
            color: var(--text-secondary);
            max-width: 750px;
            margin: 0 auto 20px;
        }}

        .meta-badges {{
            display: flex;
            justify-content: center;
            gap: 12px;
            flex-wrap: wrap;
        }}

        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(56, 189, 248, 0.1);
            color: var(--accent-sky);
            border: 1px solid rgba(56, 189, 248, 0.25);
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 0.82rem;
            font-weight: 600;
        }}

        .badge-green {{
            background: rgba(52, 211, 153, 0.1);
            color: var(--accent-emerald);
            border-color: rgba(52, 211, 153, 0.25);
        }}

        /* Navigation Bar */
        .nav-bar {{
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--card-border);
            padding: 12px 24px;
            overflow-x: auto;
            white-space: nowrap;
            display: flex;
            justify-content: center;
            gap: 8px;
        }}

        .nav-chip {{
            color: var(--text-secondary);
            text-decoration: none;
            padding: 6px 14px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 500;
            transition: all 0.2s ease;
            background: rgba(30, 41, 59, 0.5);
            border: 1px solid transparent;
        }}

        .nav-chip:hover {{
            color: var(--text-primary);
            background: var(--card-bg);
            border-color: var(--accent-sky);
        }}

        /* Single-Column Main Container */
        main {{
            max-width: 1150px;
            margin: 32px auto;
            padding: 0 20px;
            display: flex;
            flex-direction: column;
            gap: 32px;
        }}

        /* Card Container [Title, Description, Chart] */
        .card-container {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--card-radius);
            box-shadow: var(--shadow);
            padding: 24px 28px;
            transition: transform 0.2s ease, border-color 0.2s ease;
            scroll-margin-top: 80px;
            width: 100%;
        }}

        .card-container:hover {{
            border-color: rgba(56, 189, 248, 0.4);
        }}

        .card-header {{
            margin-bottom: 16px;
        }}

        .card-section-badge {{
            display: inline-block;
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--accent-sky);
            margin-bottom: 6px;
        }}

        .card-title {{
            font-size: 1.35rem;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 6px;
        }}

        .card-description {{
            font-size: 0.95rem;
            color: var(--text-secondary);
            line-height: 1.5;
        }}

        .card-chart {{
            width: 100%;
            border-radius: 8px;
            overflow: hidden;
            background: rgba(15, 23, 42, 0.4);
            border: 1px solid rgba(51, 65, 85, 0.5);
            padding: 8px;
        }}

        footer {{
            text-align: center;
            padding: 40px 20px;
            color: var(--text-secondary);
            font-size: 0.85rem;
            border-top: 1px solid var(--card-border);
            margin-top: 40px;
        }}
    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <h1>{page_title}</h1>
            <p class="subtitle">High-performance DuckDB SQL analytics across large freelance datasets. Single-column exploratory reporting and visualization.</p>
            <div class="meta-badges">
                <span class="badge">Engine: DuckDB In-Process</span>
                <span class="badge badge-green">Visualizations: {total_charts} Charts</span>
                <span class="badge">Dataset: {dataset_name}</span>
            </div>
        </div>
    </header>

    <div class="nav-bar">
        {nav_html}
    </div>

    <main>
        {cards_body}
    </main>

    <footer>
        <p>Generated by Mostaql Analytics Sub-Module &bull; DuckDB Vectorized Analytics Engine</p>
    </footer>
</body>
</html>
"""
        return html_template

    def save_html(
        self,
        output_path: Union[str, Path],
        table_name: str = "analysis",
        page_title: str = "Mostaql Analytics Dashboard",
    ) -> Path:
        """Generate cards and save the single-column HTML dashboard directly to a file."""
        p = Path(output_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        cards = self.generate_all_cards(table_name=table_name)
        html_content = self.render_html(cards, page_title=page_title, dataset_name=table_name)
        p.write_text(html_content, encoding="utf-8")
        logger.info(f"Dashboard successfully generated at {p} ({len(cards)} charts)")
        return p


def serve_dashboard(
    html_path: Union[str, Path],
    host: str = "127.0.0.1",
    port: int = 8050,
    open_browser: bool = True,
) -> None:
    """Start a lightweight local HTTP server to host the single-column dashboard."""
    import http.server
    import socketserver
    import webbrowser
    import threading

    target_file = Path(html_path).resolve()
    if not target_file.exists():
        raise FileNotFoundError(f"Dashboard HTML file not found: {target_file}")

    serve_dir = target_file.parent
    file_name = target_file.name

    class DashboardHTTPHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(serve_dir), **kwargs)

        def do_GET(self):
            if self.path in ("/", ""):
                self.path = f"/{file_name}"
            return super().do_GET()

        def log_message(self, format, *args):
            # Suppress routine log spam
            pass

    # Find an available port starting at requested port
    current_port = port
    max_attempts = 20
    httpd = None

    for attempt in range(max_attempts):
        try:
            httpd = socketserver.TCPServer((host, current_port), DashboardHTTPHandler)
            break
        except OSError:
            current_port += 1

    if httpd is None:
        raise RuntimeError(f"Could not bind HTTP server to {host} on ports {port}-{current_port}")

    url = f"http://{host}:{current_port}/"
    print("\n" + "=" * 70)
    print(" 🚀 MOSTAQL DUCKDB ANALYTICS DASHBOARD SERVER")
    print("=" * 70)
    print(f" 📊 Dashboard File : {target_file}")
    print(f" 🌐 Local URL      : {url}")
    print("=" * 70)
    print(" Press Ctrl+C to terminate the dashboard server.\n")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\nStopping dashboard server...")
    finally:
        httpd.server_close()


def run_cli():
    """CLI entry point for flat -i/--input file parsing and full flow execution."""
    import argparse
    import glob

    parser = argparse.ArgumentParser(
        prog="python -m dashboard",
        description="High-performance DuckDB Analytics Dashboard CLI for large JSON datasets.",
    )
    parser.add_argument(
        "-i", "--input",
        nargs="+",
        action="append",
        help="Path(s) to input JSON dataset file(s). If multiple files are given, they will be concatenated.",
    )
    parser.add_argument(
        "-o", "--out",
        default="dashboards/analytics_dashboard.html",
        help="Output HTML dashboard file path (default: dashboards/analytics_dashboard.html)",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=8050,
        help="Port to serve the dashboard on (default: 8050)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind local server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="Optional table name if using pre-registered standard datasets (analysis/profiles)",
    )
    parser.add_argument(
        "--title",
        default="Mostaql Analytics Dashboard",
        help="Custom title displayed on the dashboard header",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Disable automatic opening of web browser",
    )
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="Generate HTML dashboard without starting the local HTTP server",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass Parquet cache and execute queries directly on JSON",
    )

    args = parser.parse_args()

    # Flatten input paths
    raw_inputs: List[str] = []
    if args.input:
        for item in args.input:
            if isinstance(item, list):
                for sub in item:
                    # Split comma-separated inputs if any
                    raw_inputs.extend([s.strip() for s in sub.split(",") if s.strip()])
            elif isinstance(item, str):
                raw_inputs.extend([s.strip() for s in item.split(",") if s.strip()])

    # Expand globs
    resolved_files: List[Path] = []
    for raw in raw_inputs:
        if any(char in raw for char in ["*", "?", "["]):
            matched = [Path(m) for m in glob.glob(raw, recursive=True)]
            resolved_files.extend(matched)
        else:
            resolved_files.append(Path(raw))

    dashboard = DuckDBAnalyticsDashboard()
    table_to_analyze = "dataset"

    if resolved_files:
        print(f"Loading and analyzing {len(resolved_files)} JSON dataset file(s)...")
        for f in resolved_files:
            print(f"  - {f}")
        table_to_analyze = dashboard.register_inputs(
            json_inputs=resolved_files,
            view_name="dataset",
            use_parquet_cache=not args.no_cache,
        )
    else:
        # Fallback to standard datasets if no -i passed
        std_table = args.table or "analysis"
        print(f"No custom input passed. Using standard dataset: '{std_table}'")
        dashboard.prepare_standard_datasets(use_parquet_cache=not args.no_cache)
        table_to_analyze = std_table

    total_rows = SchemaInspector.get_total_records(dashboard.db, table_to_analyze)
    print(f"Dataset successfully registered into DuckDB ({total_rows:,} total records).")
    print("Computing statistical aggregations and generating single-column chart catalog...")

    out_file = dashboard.save_html(
        output_path=args.out,
        table_name=table_to_analyze,
        page_title=args.title,
    )
    print(f"✓ Standalone HTML dashboard written to: {out_file}")

    if not args.no_serve:
        serve_dashboard(
            html_path=out_file,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )


def main():
    """Entry point when executed as script."""
    run_cli()


if __name__ == "__main__":
    main()
