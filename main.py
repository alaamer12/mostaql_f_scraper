import asyncio
import typer
from pathlib import Path
import json
from rich.console import Console
from rich.panel import Panel

# Import internal modules
from bruteforce_scraper import run as run_bruteforce, cleanup_artifacts as cleanup_bruteforce
from profile_scraper import run_phase3, CHECKPOINT_FILE as PROFILE_CHECKPOINT, TEMP_DIR as PROFILE_TEMP_DIR
import config

try:
    from dashboard import load_data
except ImportError:
    load_data = None

app = typer.Typer(
    name="mostaql-scraper",
    help="Professional Mostaql Freelancer Scraper Pipeline",
    add_completion=False,
)
console = Console()

@app.command()
def scrape(
    new: bool = typer.Option(False, "--new", help="Force a fresh discovery phase (ignores cache/checkpoints)"),
    resume: bool = typer.Option(True, "--continue/--no-continue", help="Continue from last checkpoint (default: True)"),
    deep: bool = typer.Option(False, "--deep", help="Run Phase 3 (Deep Profile Scrape) after extraction"),
    limit: int = typer.Option(None, "--limit", help="Limit number of profiles to scrape in deep-scrape phase"),
):
    """
    Run the full scraper pipeline: Discovery -> URL Extraction [-> Deep Scrape].
    """
    # If user explicitly says --new, we don't resume. 
    # If they say --continue, we resume.
    # If they say both, --new usually wins in these logic flows.
    force_new = new or (not resume)
    asyncio.run(run_bruteforce(force_new=force_new, deep=deep, limit=limit))

@app.command()
def discovery(
    new: bool = typer.Option(False, "--new", help="Force fresh discovery even if cache exists"),
    resume: bool = typer.Option(True, "--continue/--no-continue", help="Continue from last checkpoint (default: True)"),
):
    """
    Run Phase 1 (Discovery) and Phase 2 (URL Extraction) only.
    """
    force_new = new or (not resume)
    asyncio.run(run_bruteforce(force_new=force_new, deep=False))

@app.command()
def deep_scrape(
    new: bool = typer.Option(False, "--new", help="Force fresh deep scrape (ignores profile checkpoints)"),
    resume: bool = typer.Option(True, "--continue/--no-continue", help="Continue from last checkpoint (default: True)"),
    limit: int = typer.Option(None, "--limit", help="Limit number of profiles to scrape"),
):
    """
    Run Phase 3 (Deep Profile Scrape) using previously discovered URLs.
    """
    should_resume = resume and (not new)
    if not should_resume:
        Path(PROFILE_CHECKPOINT).unlink(missing_ok=True)
        temp_dir = Path(PROFILE_TEMP_DIR)
        if temp_dir.exists():
            for p in temp_dir.glob("*.jsonl"):
                p.unlink()
    asyncio.run(run_phase3(resume=should_resume, limit=limit))

@app.command()
def cleanup():
    """
    Remove all temporary files, checkpoints, and caches.
    """
    cleanup_bruteforce()
    # Cleanup profiles
    Path(PROFILE_CHECKPOINT).unlink(missing_ok=True)
    temp_dir = Path(PROFILE_TEMP_DIR)
    if temp_dir.exists():
        for p in temp_dir.glob("*.jsonl"):
            p.unlink()
        try:
            temp_dir.rmdir()
        except OSError:
            pass
    
    # Cache
    from pagination_discovery import CACHE_FILE
    Path(CACHE_FILE).unlink(missing_ok=True)
    
    console.print("[green]Cleanup complete. All checkpoints and temporary files removed.[/]")

@app.command()
def stats():
    """
    Show current statistics of collected data.
    """
    if load_data is None:
        console.print("[yellow]Visualization libraries (plotly/dash) not found. Stats might be limited.[/]")
        
    try:
        if load_data:
            df = load_data(config.CONFIG["PROFILES_JSON"])
            console.print(Panel.fit(
                f"Total Profiles Scraped: [bold cyan]{len(df)}[/]\n"
                f"Categories found: {', '.join(df['category'].unique() if 'category' in df.columns else ['N/A'])}\n",
                title="Database Stats"
            ))
        else:
            raise ImportError
    except Exception:
        # Try discovery data
        try:
            with open(config.CONFIG["OUTPUT_JSON"], "r", encoding="utf-8") as f:
                data = json.load(f)
            console.print(f"Total Discovered URLs: [bold green]{len(data)}[/]")
        except:
            console.print("[red]No data found. Run a scrape first.[/]")

if __name__ == "__main__":
    app()
