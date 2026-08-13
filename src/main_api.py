import os
import sys
import time
import logging
import collections
import hashlib
import asyncio
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .models import ScrapeConfig
from .services.orchestrator import ScraperOrchestrator
from .pipeline.channel import Channel, NullChannel
from .utils.formatting import TimeFormatter
from .utils.reporting import WORKERS

# Windows consoles default to cp1252, which crashes the logging handler on any
# non-latin1 character (the old "UnicodeEncodeError: '\u2192'" log spam).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Every uuid-scoped run (whether started by an upload or a scrape command)
# gets its own sandboxed outsourcing/<uuid>/{uploads,downloads,logs} folder,
# so uploaded input files, generated output files and the run's log file
# all live together under one identifier.
OUTSOURCE_DIR = os.path.join(BASE_DIR, "outsourcing")
os.makedirs(OUTSOURCE_DIR, exist_ok=True)


def _outsource_subdir(run_id: str, subdir: str) -> str:
    path = os.path.join(OUTSOURCE_DIR, run_id, subdir)
    os.makedirs(path, exist_ok=True)
    return path

# ----------------------------------------------------------------------
# Logging: keep the last 50 lines in memory so the web UI can show them.
# ----------------------------------------------------------------------
LOG_BUFFER: "collections.deque[str]" = collections.deque(maxlen=50)


class BufferLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            LOG_BUFFER.append(self.format(record))
        except Exception:
            pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("server.log"),
    ],
)
_buffer_handler = BufferLogHandler()
_buffer_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(_buffer_handler)

log = logging.getLogger("mostaql_api")


def _attach_run_log_handler(run_id: str) -> logging.FileHandler:
    """Attach a per-run log file at outsourcing/<run_id>/logs/<run_id>.log,
    named after the same uuid used for that run's downloads/uploads."""
    logs_dir = _outsource_subdir(run_id, "logs")
    handler = logging.FileHandler(os.path.join(logs_dir, f"{run_id}.log"))
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler


def _detach_run_log_handler(handler: logging.FileHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()

app = FastAPI(
    title="Mostaql Scraper API",
    description="Production-ready web UI + API for the Mostaql Freelancer Scraper",
    version="2.0.0",
)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Global configuration and orchestrator instances
config = ScrapeConfig()
orchestrator = ScraperOrchestrator(config)


# ----------------------------------------------------------------------
# Progress tracking: hooked into orchestrator._make_bar via progress_factory
# ----------------------------------------------------------------------
# Progress debugging: set MOSTAQL_DEBUG_PROGRESS=0 to silence the very
# verbose per-update bar tracing (on by default so problems are visible).
DEBUG_PROGRESS = os.environ.get("MOSTAQL_DEBUG_PROGRESS", "1") not in ("0", "false", "False", "")
dbg = logging.getLogger("mostaql_api.progress")


def _dbg(msg: str) -> None:
    if DEBUG_PROGRESS:
        dbg.info(f"[PROGRESS-DEBUG] {msg}")


class ProgressState:
    def __init__(self) -> None:
        self.active_bars: Dict[int, "TrackedBar"] = {}
        self.snapshot_count = 0

    def debug_dump(self) -> Dict[str, Any]:
        """Raw, unfiltered view of every live bar (for /api/debug/progress)."""
        now = time.time()
        return {
            "debug_enabled": DEBUG_PROGRESS,
            "snapshot_count": self.snapshot_count,
            "active_bar_count": len(self.active_bars),
            "bars": [
                {
                    "id": b.id,
                    "desc": b.desc,
                    "n": b.n,
                    "total": b.total,
                    "updates": b.update_count,
                    "created_ago": round(now - b.created_at, 2),
                    "last_update_ago": round(now - b.last_update, 2) if b.last_update else None,
                }
                for b in sorted(self.active_bars.values(), key=lambda b: b.created_at)
            ],
        }

    def snapshot(self) -> Dict[str, Any]:
        # Sort bars so the UI layout is consistent (e.g. by creation order or description)
        self.snapshot_count += 1
        sorted_bars = sorted(self.active_bars.values(), key=lambda b: b.created_at)
        if not sorted_bars:
            _dbg("snapshot: NO active bars (nothing registered a progress bar yet)")
            return {"desc": None, "n": 0, "total": None, "sub_bars": []}

        # The main bar must be the one that is actually doing work right now.
        # Picking the first-created bar was wrong: in pipelined phases (e.g.
        # followup) the first bar only feeds a bounded channel and therefore
        # crawls along at the consumer's pace, making the top bar look frozen
        # while the real worker bar was demoted to a sub-bar.
        main = max(sorted_bars, key=lambda b: (b.last_update, b.created_at))
        subs = [
            {"desc": b.desc, "n": round(b.n, 2), "total": b.total}
            for b in sorted_bars if b is not main
        ]
        _dbg(
            f"snapshot #{self.snapshot_count}: main={main.desc!r} n={main.n} total={main.total} "
            f"updates={main.update_count} | others="
            + ", ".join(f"{b.desc!r} {b.n}/{b.total}" for b in sorted_bars if b is not main)
        )
        return {
            "desc": main.desc,
            "n": round(main.n, 2),
            "total": main.total,
            "sub_bars": subs,
            "debug": self.debug_dump(),
        }

progress_state = ProgressState()

class TrackedBar:
    """A tqdm-like proxy that reports its progress into `progress_state`."""

    def __init__(self, desc: str, total: Optional[int] = None, initial: int = 0):
        self.desc = desc
        self.total = total
        self.n = initial
        self.created_at = time.time()
        self.last_update = 0.0
        self.update_count = 0
        self.id = id(self)
        progress_state.active_bars[self.id] = self
        _dbg(f"bar CREATED id={self.id} desc={desc!r} total={total} initial={initial} "
             f"(live bars now: {len(progress_state.active_bars)})")

    def update(self, delta: int = 1) -> None:
        self.n += delta
        self.last_update = time.time()
        self.update_count += 1
        _dbg(f"bar UPDATE id={self.id} desc={self.desc!r} +{delta} -> n={self.n} total={self.total}")
        # We don't need a separate _sync because we are storing the reference

    def close(self) -> None:
        _dbg(f"bar CLOSED id={self.id} desc={self.desc!r} final n={self.n} total={self.total} "
             f"updates={self.update_count}")
        if self.id in progress_state.active_bars:
            del progress_state.active_bars[self.id]


orchestrator.progress_factory = lambda desc, total=None, initial=0: TrackedBar(desc, total, initial)


# ----------------------------------------------------------------------
# Task status tracking
# ----------------------------------------------------------------------
class TaskStatus(BaseModel):
    is_running: bool = False
    current_command: Optional[str] = None
    current_run_id: Optional[str] = None
    current_output_files: List[str] = []
    last_run_at: Optional[str] = None
    error: Optional[str] = None

# Track recent runs for the results list
RUN_HISTORY: List[Dict[str, Any]] = []


scrape_status = TaskStatus()

# Handle to the currently-running asyncio background task, so the Stop
# button can actually cancel it (BackgroundTasks doesn't expose a handle).
CURRENT_TASK: Optional["asyncio.Task"] = None


async def run_scraper_task(command: str, **kwargs) -> None:
    """Internal helper to run scraper commands in background."""
    global scrape_status, CURRENT_TASK
    # Reuse the uuid generated at upload time (if the UI forwarded one), so
    # an uploaded input file and this run's downloads/logs share one folder:
    # outsourcing/<uuid>/{uploads,downloads,logs}.
    run_id = kwargs.get("run_id") or str(uuid.uuid4())
    run_dir = _outsource_subdir(run_id, "downloads")
    run_log_handler = _attach_run_log_handler(run_id)

    scrape_status.is_running = True
    scrape_status.current_command = command
    scrape_status.current_run_id = run_id
    scrape_status.current_output_files = []

    # Helper to resolve output path within the unique run directory
    def resolve_run_path(val: Optional[str], default_attr: str) -> str:
        path_str = val or config.resolve_path(default_attr)
        # Ensure it's relative to run_dir if it's not absolute
        if not os.path.isabs(path_str):
            full_path = os.path.join(run_dir, path_str)
        else:
            # If user provided absolute path, we still want to sandbox it?
            # For Railway/Production, we should probably force it into run_dir
            full_path = os.path.join(run_dir, os.path.basename(path_str))
        
        # Ensure subdirectories exist if the path is nested
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        return full_path

    # Identify and resolve expected output files for this run
    cmd_spec = COMMANDS_BY_SLUG.get(command, {})
    
    # We'll map the UI kwargs to their resolved paths in run_dir
    resolved_args = kwargs.copy()
    
    for attr in cmd_spec.get("output_attrs", []):
        short_name = "output_json" if "output_json" in attr else ("output_csv" if "output_csv" in attr else attr)
        # Check if it's one of the profile ones
        if "profiles_json" in attr: short_name = "profiles_json"
        elif "profiles_csv" in attr: short_name = "profiles_csv"

        val = kwargs.get(short_name) or kwargs.get(attr)
        resolved_path = resolve_run_path(val, attr)
        
        # Update the args we pass to orchestrator
        resolved_args[short_name] = resolved_path
        scrape_status.current_output_files.append(resolved_path)

    scrape_status.error = None
    progress_state.active_bars.clear()
    WORKERS.clear()

    try:
        log.info(f"Starting background task: {command} with args {kwargs}")

        resume = kwargs.get("resume", True)
        fresh = kwargs.get("new", False)
        use_continue = resume and not fresh

        if command == "discovery":
            await orchestrator.run_discovery(use_continue=use_continue)
        elif command == "extract":
            await orchestrator.run_extraction(
                use_continue=use_continue,
                output_json=resolved_args.get("output_json"),
                output_csv=resolved_args.get("output_csv"),
            )
        elif command == "fetch":
            await orchestrator.run_fetch(limit=kwargs.get("limit"), use_continue=use_continue)
        elif command == "deep_scrape":
            await orchestrator.run_deep_scrape(
                limit=kwargs.get("limit"),
                use_continue=use_continue,
                output_json=resolved_args.get("profiles_json"),
                output_csv=resolved_args.get("profiles_csv"),
            )
        elif command == "followup":
            followup_channel = Channel(name="followup")
            followup_task = asyncio.create_task(
                orchestrator.stream_followup(
                    followup_channel,
                    input_path=kwargs.get("input_file"),
                    use_continue=use_continue,
                )
            )
            await orchestrator.stream_extraction(
                followup_channel,
                NullChannel(),
                use_continue=use_continue,
                output_json=resolved_args.get("output_json"),
                output_csv=resolved_args.get("output_csv"),
            )
            await followup_task
        elif command == "fixup":
            await orchestrator.run_fixup(
                input_path=kwargs.get("input_file"),
                use_continue=use_continue,
                output_json=resolved_args.get("output_json"),
                output_csv=resolved_args.get("output_csv"),
            )
        elif command == "scrape":
            # Discovery + Extraction + Optional Deep Scrape
            await orchestrator.run_discovery(use_continue=use_continue)
            await orchestrator.run_extraction(
                use_continue=use_continue,
                output_json=resolved_args.get("output_json"),
                output_csv=resolved_args.get("output_csv"),
            )
            if kwargs.get("deep"):
                await orchestrator.run_deep_scrape(
                    limit=kwargs.get("limit"),
                    use_continue=use_continue,
                    output_json=resolved_args.get("profiles_json"),
                    output_csv=resolved_args.get("profiles_csv"),
                )

        scrape_status.last_run_at = time.strftime("%Y-%m-%d %H:%M:%S")
        log.info(f"Background task {command} completed successfully.")
        # Save to history
        RUN_HISTORY.append({
            "run_id": run_id,
            "command": command,
            "files": scrape_status.current_output_files.copy(),
            "timestamp": time.time()
        })
        # Keep only last 10 runs in history
        if len(RUN_HISTORY) > 10:
            RUN_HISTORY.pop(0)
    except asyncio.CancelledError:
        log.warning(f"Background task {command} was stopped by the user.")
        scrape_status.error = "Stopped by user."
    except Exception as e:
        log.exception(f"Error in background task {command}: {str(e)}")
        scrape_status.error = str(e)
    finally:
        scrape_status.is_running = False
        scrape_status.current_command = None
        CURRENT_TASK = None
        _detach_run_log_handler(run_log_handler)


# ----------------------------------------------------------------------
# Command registry: describes every "page" route (mirrors old CLI commands)
# ----------------------------------------------------------------------
COMMANDS: List[Dict[str, Any]] = [
    {
        "slug": "scrape",
        "title": "Full Scrape",
        "description": "Run Discovery + URL Extraction, optionally followed by a Deep Scrape (Fetch + Parse).",
        "needs_file": False,
        "file_field": None,
        "output_attrs": ["pagination_cache", "output_json", "output_csv", "profiles_json", "profiles_csv"],
        "fields": [
            {"name": "output_json", "label": "Output JSON (--output-json/-o, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_users.json"},
            {"name": "output_csv", "label": "Output CSV (--output-csv, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_users.csv"},
            {"name": "profiles_json", "label": "Profiles JSON (--profiles-json, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_profiles.json"},
            {"name": "profiles_csv", "label": "Profiles CSV (--profiles-csv, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_profiles.csv"},
            {"name": "new", "label": "Fresh run (--new: ignore pagination cache and all checkpoints)", "type": "checkbox", "default": False},
            {"name": "resume", "label": "Continue (--continue/--no-continue: resume from last checkpoint)", "type": "checkbox", "default": True},
            {"name": "deep", "label": "Also run Deep Scrape (--deep: Fetch + Parse)", "type": "checkbox", "default": False},
            {"name": "limit", "label": "Limit (--limit, optional, only used with --deep)", "type": "number", "default": None, "placeholder": "e.g. 100"},
        ],
    },
    {
        "slug": "discovery",
        "title": "Discovery",
        "description": "Binary search over every filter combination to find its page count.",
        "needs_file": False,
        "file_field": None,
        "output_attrs": ["pagination_cache"],
        "fields": [
            {"name": "new", "label": "Fresh run (--new: ignore pagination cache, re-run every combo from scratch)", "type": "checkbox", "default": False},
            {"name": "resume", "label": "Continue (--continue/--no-continue: reuse cached combo/page-count data)", "type": "checkbox", "default": True},
        ],
    },
    {
        "slug": "extract",
        "title": "URL Extraction",
        "description": "Scrape listing pages (using the pagination cache) to collect unique freelancer URLs.",
        "needs_file": False,
        "file_field": None,
        "output_attrs": ["output_json", "output_csv"],
        "fields": [
            {"name": "output_json", "label": "Output JSON (--output-json/-o, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_users.json"},
            {"name": "output_csv", "label": "Output CSV (--output-csv, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_users.csv"},
            {"name": "new", "label": "Fresh run (--new: start freelancer URL list from empty)", "type": "checkbox", "default": False},
            {"name": "resume", "label": "Continue (--continue/--no-continue: merge with existing output)", "type": "checkbox", "default": True},
        ],
    },
    {
        "slug": "fetch",
        "title": "Fetch",
        "description": "Download raw profile + portfolio HTML and cache it to disk, without parsing.",
        "needs_file": False,
        "file_field": None,
        "output_attrs": ["raw_html_json", "checkpoint_fetch_json"],
        "fields": [
            {"name": "limit", "label": "Limit (--limit, cap profile URLs fetched this run)", "type": "number", "default": None, "placeholder": "e.g. 50"},
            {"name": "resume", "label": "Continue (--continue/--no-continue: skip already-fetched profiles)", "type": "checkbox", "default": True},
        ],
    },
    {
        "slug": "deep_scrape",
        "title": "Deep Scrape",
        "description": "Convenience wrapper chaining Fetch and Parse phases.",
        "needs_file": False,
        "file_field": None,
        "output_attrs": ["raw_html_json", "checkpoint_fetch_json", "profiles_json", "profiles_csv"],
        "fields": [
            {"name": "profiles_json", "label": "Profiles JSON (--profiles-json, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_profiles.json"},
            {"name": "profiles_csv", "label": "Profiles CSV (--profiles-csv, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_profiles.csv"},
            {"name": "limit", "label": "Limit (--limit, cap profiles processed by fetch + parse)", "type": "number", "default": None, "placeholder": "e.g. 50"},
            {"name": "resume", "label": "Continue (--continue/--no-continue: resume fetch and parse checkpoints)", "type": "checkbox", "default": True},
        ],
    },
    {
        "slug": "followup",
        "title": "Followup",
        "description": "Extract names from existing data and search for more freelancers that share those names.",
        "needs_file": True,
        "file_field": "input_file",
        "output_attrs": ["followup_output_json", "followup_output_csv"],
        "fields": [
            {"name": "output_json", "label": "Output JSON (--output-json/-o, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_followup_users.json"},
            {"name": "output_csv", "label": "Output CSV (--output-csv, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_followup_users.csv"},
            {"name": "resume", "label": "Continue (--continue/--no-continue: resume by reading existing unique names)", "type": "checkbox", "default": True},
        ],
    },
    {
        "slug": "fixup",
        "title": "Fixup",
        "description": "Scan existing data and fill in missing titles/ranks by re-fetching profiles.",
        "needs_file": True,
        "file_field": "input_file",
        "output_attrs": ["output_json", "output_csv"],
        "fields": [
            {"name": "output_json", "label": "Output JSON (--output-json/-o, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_users_fixed.json"},
            {"name": "output_csv", "label": "Output CSV (--output-csv, optional custom path)", "type": "text", "default": None, "placeholder": "e.g. mostaql_users_fixed.csv"},
            {"name": "resume", "label": "Continue (--continue/--no-continue)", "type": "checkbox", "default": True},
        ],
    },
]
COMMANDS_BY_SLUG = {c["slug"]: c for c in COMMANDS}


# ----------------------------------------------------------------------
# Root & health
# ----------------------------------------------------------------------
@app.get("/health")
async def health_check():
    """Basic health check for Railway."""
    return {"status": "healthy", "service": "mostaql-scraper"}


@app.get("/")
async def index(request: Request):
    """Guiding home page listing every available command page."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {"commands": COMMANDS, "status": scrape_status},
    )


# ----------------------------------------------------------------------
# Per-command HTML pages
# ----------------------------------------------------------------------
@app.get("/{slug}")
async def command_page(slug: str, request: Request):
    cmd = COMMANDS_BY_SLUG.get(slug)
    if not cmd:
        raise HTTPException(status_code=404, detail="Unknown command page.")
    return templates.TemplateResponse(request, "command.html", {"cmd": cmd})


# ----------------------------------------------------------------------
# JSON API
# ----------------------------------------------------------------------
@app.get("/api/stats")
async def get_stats():
    """Returns current scraping progress and metrics (polled by every command page)."""
    phases_report = {}
    for phase in orchestrator.registry.phases:
        phases_report[phase.phase_name] = {
            "urls_discovered": phase.urls_discovered,
            "profiles_fetched": phase.profiles_fetched,
            "profiles_parsed": phase.profiles_parsed,
            "parse_success": phase.parse_success,
            "parse_failed": phase.parse_failed,
            "errors": phase.errors,
            "duration_seconds": round(phase.duration_seconds, 2),
        }

    progress = progress_state.snapshot()

    # When the top-level bar looks "stalled" (no new completions), surface
    # *why* to the UI instead of leaving it looking frozen/broken: if every
    # active worker is currently sitting in a rate-limit cooldown, the bar
    # genuinely cannot move yet because no page fetch has completed.
    worker_snapshot = WORKERS.snapshot()
    cooling_workers = [w for w in worker_snapshot if w.cooldown_remaining > 0]
    progress["workers_total"] = len(worker_snapshot)
    progress["workers_in_cooldown"] = len(cooling_workers)
    progress["max_cooldown_remaining"] = round(max((w.cooldown_remaining for w in cooling_workers), default=0.0), 1)

    stats = {
        "task_status": scrape_status.dict(),
        "progress": progress,
        "metrics": {"phases": phases_report},
    }

    try:
        from .dashboard import load_data
        profiles_path = config.resolve_path("profiles_json")
        if os.path.exists(profiles_path):
            df = load_data(profiles_path)
            stats["file_stats"] = {
                "profiles_on_disk": len(df),
                "last_modified": os.path.getmtime(profiles_path),
            }
    except Exception:
        pass

    return stats


@app.get("/api/debug/progress")
async def debug_progress():
    """Raw progress-bar debugging dump: every live bar, its update count and
    how long ago it last moved. Use this to tell "the bar isn't updating"
    apart from "no work has completed yet"."""
    dump = progress_state.debug_dump()
    dump["task_status"] = scrape_status.dict()
    dump["workers"] = [
        {
            "stage": w.stage,
            "worker_id": w.worker_id,
            "status": w.status,
            "detail": w.detail,
            "requests": w.requests,
            "rate_limits": w.rate_limits,
            "cooldown_remaining": round(w.cooldown_remaining, 1),
        }
        for w in WORKERS.snapshot()
    ]
    log.info(f"[PROGRESS-DEBUG] /api/debug/progress -> {dump['active_bar_count']} live bars")
    return dump


@app.get("/api/logs")
async def get_logs():
    """Returns the last 50 log lines for the live log section."""
    return {"logs": list(LOG_BUFFER)}


@app.post("/api/check-overwrite/{slug}")
async def check_overwrite(slug: str, request: Request):
    """Checks whether starting this command with the given payload would overwrite
    existing output files on disk, so the UI can warn the user before kick-off."""
    cmd = COMMANDS_BY_SLUG.get(slug)
    if not cmd:
        raise HTTPException(status_code=404, detail="Unknown command.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    override_json = payload.get("output_json") or None
    override_csv = payload.get("output_csv") or None
    override_profiles_json = payload.get("profiles_json") or None
    override_profiles_csv = payload.get("profiles_csv") or None

    existing: List[Dict[str, str]] = []
    for attr in cmd.get("output_attrs", []):
        override = None
        # Handle cases where attr is 'followup_output_json' but field is 'output_json'
        if "output_json" in attr and override_json:
            override = override_json
        elif "output_csv" in attr and override_csv:
            override = override_csv
        elif "profiles_json" in attr and override_profiles_json:
            override = override_profiles_json
        elif "profiles_csv" in attr and override_profiles_csv:
            override = override_profiles_csv

        if not (override or getattr(config, attr, None)):
            continue
        resolved = TimeFormatter.format_path(override) if override else config.resolve_path(attr)
        if os.path.exists(resolved):
            existing.append({"attr": attr, "path": resolved})

    return {"will_overwrite": len(existing) > 0, "files": existing}


@app.post("/api/run/{slug}")
async def run_command(slug: str, request: Request):
    """Generic trigger endpoint used by every command page's Start button."""
    global CURRENT_TASK
    cmd = COMMANDS_BY_SLUG.get(slug)
    if not cmd:
        raise HTTPException(status_code=404, detail="Unknown command.")

    if scrape_status.is_running:
        raise HTTPException(status_code=400, detail=f"A task ({scrape_status.current_command}) is already running.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    kwargs: Dict[str, Any] = {}
    if "resume" in payload:
        kwargs["resume"] = bool(payload.get("resume"))
    if "new" in payload:
        kwargs["new"] = bool(payload.get("new"))
    if "deep" in payload:
        kwargs["deep"] = bool(payload.get("deep"))
    if payload.get("limit"):
        try:
            kwargs["limit"] = int(payload["limit"])
        except (TypeError, ValueError):
            pass
    if payload.get("output_json"):
        kwargs["output_json"] = str(payload["output_json"]).strip()
    if payload.get("output_csv"):
        kwargs["output_csv"] = str(payload["output_csv"]).strip()
    if payload.get("profiles_json"):
        kwargs["profiles_json"] = str(payload["profiles_json"]).strip()
    if payload.get("profiles_csv"):
        kwargs["profiles_csv"] = str(payload["profiles_csv"]).strip()
    if cmd["needs_file"]:
        input_file = payload.get(cmd["file_field"])
        if input_file:
            kwargs["input_file"] = input_file
    # If the input file was uploaded, reuse its uuid so this run's
    # outsourcing/<uuid>/{downloads,logs} share the folder with the
    # outsourcing/<uuid>/uploads/ file that was just used.
    if payload.get("upload_run_id"):
        kwargs["run_id"] = str(payload["upload_run_id"]).strip()

    CURRENT_TASK = asyncio.create_task(run_scraper_task(slug, **kwargs))
    return {"message": f"Started {cmd['title']} in background.", "config": kwargs}


@app.post("/api/stop")
async def stop_task():
    """Cancels the currently-running background task, if any."""
    global CURRENT_TASK
    if not scrape_status.is_running or CURRENT_TASK is None:
        raise HTTPException(status_code=400, detail="No task is currently running.")

    CURRENT_TASK.cancel()
    try:
        await CURRENT_TASK
    except asyncio.CancelledError:
        pass
    return {"message": "Stop requested. The task has been cancelled."}


@app.get("/api/workers")
async def get_workers():
    """Live per-worker state (status/cooldown timers/requests), grouped by stage,
    so the UI can render an accordion with rate-limit countdown indicators."""
    workers = []
    for w in WORKERS.snapshot():
        workers.append({
            "stage": w.stage,
            "worker_id": w.worker_id,
            "status": w.status,
            "detail": w.detail,
            "requests": w.requests,
            "rate_limits": w.rate_limits,
            "cooldown_remaining": round(w.cooldown_remaining, 1),
            "cooldown_total": max(w.cooldown_until - w.since, 0.0) if w.cooldown_until else 0.0,
            "elapsed": round(w.elapsed, 1),
            "describe": w.describe(),
        })
    return {"workers": workers}


# Backward-compatible alias for the original single trigger endpoint.
@app.post("/scrape")
async def trigger_scrape(
    command: str = "scrape",
    resume: bool = True,
    deep: bool = False,
    limit: Optional[int] = None,
):
    global CURRENT_TASK
    if scrape_status.is_running:
        raise HTTPException(status_code=400, detail=f"A task ({scrape_status.current_command}) is already running.")

    CURRENT_TASK = asyncio.create_task(run_scraper_task(command, resume=resume, deep=deep, limit=limit))
    return {"message": f"Started {command} in background.", "config": {"resume": resume, "deep": deep, "limit": limit}}


@app.get("/stats")
async def stats_alias():
    return await get_stats()


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Handles the drag & drop / file-explorer uploads used by followup & fixup.

    Files are named after the SHA-256 hash of their content, so uploading the
    same file multiple times reuses the same stored path instead of creating
    a new duplicate copy each time, while different content always gets a
    unique name.
    """
    if not file.filename.lower().endswith((".json",)):
        raise HTTPException(status_code=400, detail="Only .json files are supported.")

    content = await file.read()
    content_hash = hashlib.sha256(content).hexdigest()
    original_name = os.path.basename(file.filename)
    safe_name = f"{content_hash}_{original_name}"

    # Every upload gets its own uuid-scoped outsourcing/<uuid>/uploads/
    # folder; the frontend forwards this uuid back to /api/run/{slug} so
    # the eventual run's downloads/logs land in that same outsourcing/<uuid>/.
    run_id = str(uuid.uuid4())
    upload_dir = _outsource_subdir(run_id, "uploads")
    dest_path = os.path.join(upload_dir, safe_name)

    if os.path.exists(dest_path):
        log.info(f"Upload matches existing file by hash, reusing {dest_path}")
    else:
        with open(dest_path, "wb") as f:
            f.write(content)
        log.info(f"Uploaded file saved to {dest_path}")

    return {"name": file.filename, "path": dest_path, "hash": content_hash, "run_id": run_id}


@app.get("/api/results")
async def list_results():
    """List generated JSON/CSV files from history and defaults."""
    available_files = []
    seen_paths = set()

    # Priority 1: Files for the CURRENTLY RUNNING task
    if scrape_status.is_running:
        for f in scrape_status.current_output_files:
            abs_p = os.path.abspath(f)
            exists = os.path.exists(abs_p)
            run_downloads_dir = _outsource_subdir(scrape_status.current_run_id, "downloads")
            rel_to_downloads = os.path.relpath(abs_p, run_downloads_dir)
            available_files.append({
                "name": os.path.basename(abs_p),
                "display_name": rel_to_downloads.replace(os.sep, "/"),
                "path": abs_p,
                "download_url": f"/results/download/{scrape_status.current_run_id}/{rel_to_downloads.replace(os.sep, '/')}",
                "size": os.path.getsize(abs_p) if exists else 0,
                "modified": os.path.getmtime(abs_p) if exists else 0,
                "exists": exists,
                "is_current": True
            })
            seen_paths.add(abs_p)

    # Priority 2: Files from RUN_HISTORY (most recent first)
    for run in reversed(RUN_HISTORY):
        for f in run["files"]:
            abs_p = os.path.abspath(f)
            if abs_p in seen_paths:
                continue
            exists = os.path.exists(abs_p)
            if not exists:
                continue # Only show historical files if they actually exist

            run_downloads_dir = _outsource_subdir(run["run_id"], "downloads")
            rel_to_downloads = os.path.relpath(abs_p, run_downloads_dir)
            available_files.append({
                "name": os.path.basename(abs_p),
                "display_name": rel_to_downloads.replace(os.sep, "/"),
                "path": abs_p,
                "download_url": f"/results/download/{run['run_id']}/{rel_to_downloads.replace(os.sep, '/')}",
                "size": os.path.getsize(abs_p),
                "modified": os.path.getmtime(abs_p),
                "exists": True,
                "is_current": (run["run_id"] == scrape_status.current_run_id)
            })
            seen_paths.add(abs_p)

    # Priority 3: Default files from config (only if they exist)
    default_paths = {
        config.resolve_path("output_json"),
        config.resolve_path("output_csv"),
        config.resolve_path("profiles_json"),
        config.resolve_path("profiles_csv"),
        config.resolve_path("followup_output_json"),
        config.resolve_path("followup_output_csv"),
    }
    for f in default_paths:
        abs_p = os.path.abspath(f)
        if abs_p not in seen_paths and os.path.exists(abs_p):
            available_files.append({
                "name": os.path.basename(abs_p),
                "display_name": os.path.basename(abs_p),
                "path": abs_p,
                "download_url": f"/results/download/legacy/{os.path.basename(abs_p)}",
                "size": os.path.getsize(abs_p),
                "modified": os.path.getmtime(abs_p),
                "exists": True,
                "is_current": False
            })
            seen_paths.add(abs_p)

    # Sort: is_current first, then modified time
    available_files.sort(key=lambda x: (x["is_current"], x["modified"]), reverse=True)
    return {"files": available_files}


@app.get("/results")
async def results_alias():
    return await list_results()


@app.get("/results/download/legacy/{filename}")
async def download_legacy_result(filename: str):
    """Download a specific result file from root/config paths."""
    allowed_attrs = ["output_json", "output_csv", "profiles_json", "profiles_csv", "followup_output_json", "followup_output_csv"]
    for attr in allowed_attrs:
        path = config.resolve_path(attr)
        if os.path.basename(path) == filename and os.path.exists(path):
            return FileResponse(path=path, filename=filename)
    raise HTTPException(status_code=404, detail="File not found.")


@app.get("/results/download/{run_id}/{filename:path}")
async def download_result(run_id: str, filename: str):
    """Download a specific result file from a unique run directory."""
    run_downloads_dir = os.path.abspath(os.path.join(OUTSOURCE_DIR, run_id, "downloads"))
    full_path = os.path.abspath(os.path.join(run_downloads_dir, filename))

    # Security check: ensure path is inside this run's downloads directory
    if not full_path.startswith(run_downloads_dir):
        raise HTTPException(status_code=403, detail="Access denied.")

    if os.path.exists(full_path) and os.path.isfile(full_path):
        return FileResponse(path=full_path, filename=os.path.basename(full_path))

    raise HTTPException(status_code=404, detail="File not found.")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
