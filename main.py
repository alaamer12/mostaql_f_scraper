import asyncio
import sys
from typing import Optional
import typer
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

# Import internal modules
from src.models import ScrapeConfig
from src.services.orchestrator import ScraperOrchestrator
from src.pipeline import cli_chain

app = typer.Typer(
    name="mostaql-scraper",
    help=(
        "Professional Mostaql Freelancer Scraper Pipeline.\n\n"
        "A four-phase, checkpointed and rate-limited scraping system:\n"
        "Discovery -> Extraction -> Fetch -> Parse. Every phase can run "
        "independently, resume from its own checkpoint, and export its "
        "own results to JSON/CSV. Run 'python main.py examples' for a "
        "quick-start cookbook of common invocations.\n\n"
        "Phases can also be chained into a concurrent streaming pipeline "
        "with the --pipelined separator, e.g. "
        "'python main.py discovery --pipelined extract --pipelined fetch'. "
        "Each stage starts as soon as its upstream produces its first item."
    ),
    epilog="Run [bold]python main.py examples[/] for full usage examples.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


def get_orchestrator() -> ScraperOrchestrator:
    config = ScrapeConfig()
    return ScraperOrchestrator(config)


@app.command(epilog="Example: [green]python main.py discovery --new[/]")
def discovery(
    new: bool = typer.Option(
        False, "--new", help="Ignore the pagination cache and re-run the binary search for every combination from scratch."
    ),
    resume: bool = typer.Option(
        True,
        "--continue/--no-continue",
        help="Resume using previously cached combo/page-count data instead of starting over (default: enabled).",
    ),
):
    """Phase 1 - Discovery: find the page count of every filter combination.

    Performs a binary search over each category/subcategory/budget/skill
    combination to determine how many listing pages it has, and writes the
    result to the pagination cache so Phase 2 (extract) can reuse it without
    repeating the search.

    Pipelined positions: start. Discovery always seeds itself from the
    combo list, so it can only open a chain; every solved combination is
    streamed downstream immediately.

    Examples:
        python main.py discovery
        python main.py discovery --new
        python main.py discovery --no-continue
        python main.py discovery --pipelined extract
    """
    orch = get_orchestrator()
    asyncio.run(orch.run_discovery(use_continue=resume and not new))
    orch.print_session_summary()


@app.command(epilog="Example: [green]python main.py extract --continue[/]")
def extract(
    new: bool = typer.Option(
        False, "--new", help="Ignore any previously extracted output and start the freelancer URL list from empty."
    ),
    resume: bool = typer.Option(
        True,
        "--continue/--no-continue",
        help="Resume by merging with existing extracted output instead of starting over (default: enabled).",
    ),
):
    """Phase 2 - URL Extraction: collect unique freelancer name/URL records.

    Uses the page counts discovered in Phase 1 to walk every listing page and
    extract a de-duplicated set of freelancer names and profile URLs, saved
    as both JSON and CSV for downstream phases.

    Pipelined positions: start, middle, end. In start position it seeds
    from pagination_cache.json (``--new``/``--continue`` apply to the
    existing URL list); otherwise it consumes combos streamed by discovery.

    Examples:
        python main.py extract
        python main.py extract --new
        python main.py discovery --pipelined extract --pipelined fetch
    """
    orch = get_orchestrator()
    asyncio.run(orch.run_extraction(use_continue=resume and not new))
    orch.print_session_summary()


@app.command(epilog="Example: [green]python main.py fetch --limit 100 --continue[/]")
def fetch(
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Cap the number of profile URLs to fetch in this run (useful for quick tests)."
    ),
    resume: bool = typer.Option(
        True,
        "--continue/--no-continue",
        help="Resume from the fetch checkpoint, skipping URLs already downloaded (default: enabled).",
    ),
):
    """Phase 3 - Fetch: download raw profile and portfolio HTML.

    Downloads the main profile page and the portfolio tab for every
    extracted URL and caches the raw HTML to disk (no parsing yet), so
    Phase 4 (parse) can be re-run repeatedly without re-hitting the network.

    Pipelined positions: start, middle, end. In start position it seeds
    from the extracted URL list; otherwise it consumes freelancers streamed
    by extract. ``--limit`` caps the number of profiles in either position.

    Examples:
        python main.py fetch
        python main.py fetch --limit 100
        python main.py fetch --no-continue
        python main.py fetch --limit 500 --pipelined parse
    """
    orch = get_orchestrator()
    asyncio.run(orch.run_fetch(limit=limit, use_continue=resume))
    orch.print_session_summary()


@app.command(epilog="Example: [green]python main.py parse[/]")
def parse(
    resume: bool = typer.Option(
        True,
        "--continue/--no-continue",
        help="Reserved for future incremental parsing support (currently always parses the full raw HTML cache).",
    ),
):
    """Phase 4 - Parse: turn cached raw HTML into structured profile data.

    Reads the raw HTML cached by Phase 3 (fetch) and parses it (CPU-only,
    no network calls) into structured ``ProfileDetails`` records, exported
    to JSON and CSV.

    Pipelined positions: start, middle, end. In start position it seeds
    from checkpoint_fetch.jsonl; otherwise it parses raw records streamed
    by fetch as they are downloaded.

    Examples:
        python main.py parse
        python main.py fetch --pipelined parse
    """
    orch = get_orchestrator()
    orch.run_parse(use_continue=resume)
    orch.print_session_summary()


@app.command(epilog="Example: [green]python main.py deep-scrape --limit 100 --continue[/]")
def deep_scrape(
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Cap the number of profiles processed by both the fetch and parse steps."
    ),
    resume: bool = typer.Option(
        True,
        "--continue/--no-continue",
        help="Resume both fetch and parse from their respective checkpoints (default: enabled).",
    ),
):
    """Composite - Deep Scrape: run Phase 3 (Fetch) then Phase 4 (Parse).

    Convenience command that chains the raw-HTML download and the parsing
    step in a single call, producing fully structured profile details
    without needing two separate invocations.

    Examples:
        python main.py deep-scrape
        python main.py deep-scrape --limit 50 --continue
    """
    orch = get_orchestrator()
    asyncio.run(orch.run_deep_scrape(limit=limit, use_continue=resume))
    orch.print_session_summary()


@app.command(epilog="Example: [green]python main.py scrape --deep --limit 200[/]")
def scrape(
    new: bool = typer.Option(
        False, "--new", help="Force a completely fresh run, ignoring the pagination cache and all checkpoints."
    ),
    resume: bool = typer.Option(
        True,
        "--continue/--no-continue",
        help="Continue from the last checkpoint of every phase that runs (default: enabled).",
    ),
    deep: bool = typer.Option(
        False, "--deep", help="Also run Phase 3 & 4 (Fetch + Parse) right after extraction, producing full profile details."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Cap the number of profiles processed by the fetch/parse phases (only used with --deep)."
    ),
):
    """Composite - Full Pipeline: Discovery -> Extraction [-> Fetch -> Parse].

    Runs Phase 1 (discovery) and Phase 2 (extraction) sequentially, and
    optionally continues into Phase 3 (fetch) and Phase 4 (parse) when
    ``--deep`` is passed, producing a complete, ready-to-use dataset.

    Examples:
        python main.py scrape
        python main.py scrape --new
        python main.py scrape --deep --limit 200
    """
    orch = get_orchestrator()
    console.print("[bold blue]Starting full pipeline...[/]")

    asyncio.run(orch.run_discovery(use_continue=not new))
    asyncio.run(orch.run_extraction(use_continue=not new))

    if deep:
        console.print("[bold blue]Starting deep scrape (fetch + parse)...[/]")
        asyncio.run(orch.run_deep_scrape(limit=limit, use_continue=resume))

    orch.print_session_summary()


@app.command(epilog="Tip: run this command anytime you forget a flag combination.")
def examples(
    sample: bool = typer.Option(
        False, "--sample", help="Also run a tiny live smoke test (fetch + parse a couple of real profiles) to verify the pipeline actually works."
    ),
    limit: int = typer.Option(
        2, "--limit", help="Number of profiles to fetch/parse during the --sample smoke test."
    ),
):
    """Show a cookbook of common command invocations for every use case.

    Prints ready-to-copy examples covering the full pipeline, running each
    phase independently, statistics, the dashboard, and cleanup. Pass
    ``--sample`` to additionally run a quick live smoke test that fetches
    and parses a couple of real profiles, confirming the scraper still
    works end-to-end without writing to any cache or checkpoint file.

    Examples:
        python main.py examples
        python main.py examples --sample
        python main.py examples --sample --limit 3
    """
    console.print(Panel(
        "[bold cyan]Full Pipeline (all 4 phases):[/]\n"
        "[green]python main.py scrape --deep[/]\n\n"

        "[bold cyan]Run each phase independently:[/]\n"
        "[green]python main.py discovery --new[/]\n"
        "[green]python main.py extract[/]\n"
        "[green]python main.py fetch --limit 100[/]\n"
        "[green]python main.py parse[/]\n\n"

        "[bold cyan]Fetch + Parse together (Deep Scrape):[/]\n"
        "[green]python main.py deep-scrape --limit 100 --continue[/]\n\n"

        "[bold cyan]Pipelined (all stages stream concurrently):[/]\n"
        "[green]python main.py discovery --pipelined extract --pipelined fetch --pipelined parse[/]\n"
        "[green]python main.py extract --pipelined fetch[/]\n"
        "[green]python main.py fetch --limit 500 --pipelined parse[/]\n"
        "  discovery: start  |  extract/fetch/parse: start, middle, end\n\n"

        "[bold cyan]Statistics & Analytics:[/]\n"
        "[green]python main.py stats[/]\n"
        "[green]python main.py dashboard[/]\n\n"

        "[bold cyan]Cleanup Temporary Files:[/]\n"
        "[green]python main.py cleanup[/]",
        title="[bold white]Mostaql Scraper Examples[/]",
        expand=False
    ))

    if sample:
        console.print("\n[bold blue]Running live sample smoke test...[/]")
        orch = get_orchestrator()
        ok = asyncio.run(orch.run_sample(limit=limit))
        orch.print_session_summary()
        if ok:
            console.print("[bold green]Sample check passed: the scraper works end-to-end.[/]")
        else:
            console.print("[bold red]Sample check failed: could not fetch/parse a working profile. Check network/logs.[/]")
            raise typer.Exit(code=1)


@app.command(epilog="Example: [green]python main.py cleanup[/]")
def cleanup():
    """Remove all temporary files, checkpoints, caches, and exported data.

    Deletes the pagination cache, fetch/parse checkpoints, the scraper log,
    and every exported JSON/CSV output, giving you a clean slate to start a
    brand-new scrape with ``--new``.
    """
    orch = get_orchestrator()
    config = orch.config

    files_to_remove = [
        config.resolve_path("checkpoint_profiles_json"),
        config.resolve_path("checkpoint_fetch_json"),
        config.resolve_path("pagination_cache"),
        "scraper.log",
        "pipeline.log",
        config.resolve_path("output_json"),
        config.resolve_path("output_csv"),
        config.resolve_path("profiles_json"),
        config.resolve_path("profiles_csv"),
    ]

    for filename in files_to_remove:
        Path(filename).unlink(missing_ok=True)

    console.print("[green]Cleanup complete. All checkpoints and output files removed.[/]")


@app.command(epilog="Example: [green]python main.py stats[/]")
def stats():
    """Show quick statistics about the currently collected data.

    Reports the number of parsed profiles and categories found, falling
    back to a simple discovered-URL count if detailed profile data is not
    yet available.
    """
    orch = get_orchestrator()
    config = orch.config

    try:
        from src.dashboard import load_data
        df = load_data(config.resolve_path("profiles_json"))
        console.print(Panel.fit(
            f"Total Profiles Scraped: [bold cyan]{len(df)}[/]\n"
            f"Categories found: {', '.join(df['category'].unique() if 'category' in df.columns else ['N/A'])}\n",
            title="Database Stats"
        ))
    except Exception:
        try:
            data = orch.storage.load_json(config.resolve_path("output_json"))
            if data:
                console.print(f"Total Discovered URLs: [bold green]{len(data)}[/]")
            else:
                console.print("[red]No data found. Run a scrape first.[/]")
        except Exception:
            console.print("[red]No data found. Run a scrape first.[/]")


@app.command(epilog="Example: [green]python main.py dashboard[/]")
def dashboard():
    """Launch the interactive analytics dashboard (requires 'dash').

    Starts a local web server at http://127.0.0.1:8050 with charts and
    tables built from the exported profile data.
    """
    console.print("[bold blue]Launching dashboard at http://127.0.0.1:8050...[/]")
    try:
        from src.dashboard import app as dash_app
        dash_app.run(debug=False, host="0.0.0.0", port=8050)
    except ImportError:
        console.print("[red]Dash or Bootstrap components not installed. Run: pip install dash dash-bootstrap-components[/]")


def main() -> None:
    """Entry point: dispatch to the pipelined runner or to plain Typer."""
    argv = sys.argv[1:]
    if not cli_chain.is_pipelined(argv):
        app()
        return

    from src.pipeline.runner import PipelineRunner

    try:
        stages = cli_chain.parse_chain(argv, app)
    except cli_chain.ChainError as exc:
        console.print(f"[bold red]Invalid pipeline:[/] {exc}")
        sys.exit(2)

    console.print(f"[bold blue]Pipelined run:[/] {cli_chain.format_chain(stages)}")
    runner = PipelineRunner(get_orchestrator(), stages, live_display=True)
    sys.exit(runner.run())


if __name__ == "__main__":
    main()
