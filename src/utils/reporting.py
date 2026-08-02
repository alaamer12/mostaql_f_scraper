"""Console output helpers and metrics for the scraper pipeline."""

from collections import deque
from dataclasses import dataclass, field, fields
from typing import Any, Deque, Dict, List, Optional, Sequence, Set
import logging
import threading
import sys
from pathlib import Path
import pandas as pd
from tqdm import tqdm


def metric_field(default=0, overlappable: bool = True):
    """Declare a metric field with an ``overlappable`` flag.

    ``overlappable`` marks whether this metric represents a cumulative
    resource (e.g. network requests) that is safe to sum across phases
    when aggregating a multi-phase run. Phase-specific counters
    (e.g. urls discovered) are marked ``overlappable=False`` so the
    aggregator reports them per-phase instead of blindly summing them.
    """
    return field(default=default, metadata={"overlappable": overlappable})


@dataclass
class PhaseMetrics:
    """Thread-safe metrics container shared by every phase."""
    phase_name: str = ""

    # Network stats - shared/cumulative across all phases.
    requests: int = metric_field(overlappable=True)
    retries: int = metric_field(overlappable=True)
    errors: int = metric_field(overlappable=True)
    rate_limit_hits: int = metric_field(overlappable=True)
    not_found_404: int = metric_field(overlappable=True)
    retry_wait_seconds: float = metric_field(default=0.0, overlappable=True)

    # Cross-cutting timing (wall-clock time spent in the phase).
    duration_seconds: float = metric_field(default=0.0, overlappable=True)

    # Resume-related (any phase that supports --continue can report these).
    skipped_resumed: int = metric_field(overlappable=False)

    # Phase 1: Discovery (binary search for page counts)
    combos_processed: int = metric_field(overlappable=False)
    pages_found: int = metric_field(overlappable=False)
    empty_combos: int = metric_field(overlappable=False)
    max_pages_seen: int = metric_field(overlappable=False)
    min_pages_seen: int = metric_field(overlappable=False)

    # Phase 2: URL Extraction
    urls_discovered: int = metric_field(overlappable=False)
    pages_scraped: int = metric_field(overlappable=False)
    duplicate_urls_skipped: int = metric_field(overlappable=False)
    unknown_names: int = metric_field(overlappable=False)

    # Phase 3: Fetching (raw HTML download)
    profiles_fetched: int = metric_field(overlappable=False)
    portfolios_fetched: int = metric_field(overlappable=False)
    fetch_failed: int = metric_field(overlappable=False)
    portfolio_fetch_failed: int = metric_field(overlappable=False)
    bytes_downloaded: int = metric_field(overlappable=False)

    # Phase 4: Parsing
    profiles_parsed: int = metric_field(overlappable=False)
    parse_success: int = metric_field(overlappable=False)
    parse_failed: int = metric_field(overlappable=False)
    portfolios_parsed: int = metric_field(overlappable=False)
    fields_missing: int = metric_field(overlappable=False)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def increment(self, metric: str, count: int = 1) -> None:
        with self._lock:
            val = getattr(self, metric)
            setattr(self, metric, val + count)

    @classmethod
    def overlappable_fields(cls) -> Set[str]:
        return {f.name for f in fields(cls) if f.metadata.get("overlappable")}

    @classmethod
    def non_overlappable_fields(cls) -> Set[str]:
        skip = {"phase_name"}
        return {
            f.name for f in fields(cls)
            if not f.metadata.get("overlappable", False) and f.name not in skip and not f.name.startswith("_")
        }

    def get_network_summary(self) -> dict:
        return {
            "requests": self.requests,
            "retries": self.retries,
            "429s": self.rate_limit_hits,
            "errors": self.errors,
            "404s": self.not_found_404,
            "retry_wait_seconds": self.retry_wait_seconds,
        }

    def get_throughput(self, count: int) -> float:
        """Items processed per second, based on ``duration_seconds``."""
        if self.duration_seconds <= 0:
            return 0.0
        return count / self.duration_seconds


class MetricsRegistry:
    """Collects PhaseMetrics from every phase run in a session for aggregated reporting."""

    def __init__(self) -> None:
        self._phases: List[PhaseMetrics] = []
        self._order: List[str] = []

    def register(self, metrics: PhaseMetrics) -> None:
        self._phases.append(metrics)

    def set_order(self, stage_names: Sequence[str]) -> None:
        """Report phases in pipeline order rather than completion order."""
        self._order = [name.lower() for name in stage_names]

    def _order_key(self, metrics: PhaseMetrics) -> int:
        name = metrics.phase_name.lower()
        for index, stage in enumerate(self._order):
            if stage in name or name.startswith(stage):
                return index
        return len(self._order)

    @property
    def phases(self) -> List[PhaseMetrics]:
        if not self._order:
            return list(self._phases)
        return sorted(self._phases, key=self._order_key)

    def print_aggregate(self) -> None:
        """Print a combined summary across all phases run in this session."""
        if len(self._phases) < 2:
            return

        phases = self.phases
        write_banner("AGGREGATED SUMMARY (ALL PHASES)")
        write_line(f"  Phases run: {', '.join(p.phase_name for p in phases)}")

        overlappable = PhaseMetrics.overlappable_fields() - {"phase_name"}
        non_overlappable = PhaseMetrics.non_overlappable_fields()

        write_line("  -- Cumulative (summed, safe to overlap) --")
        for name in sorted(overlappable):
            total = sum(getattr(p, name) for p in phases)
            write_line(f"    {name:20s}: {total}")

        write_line("  -- Per-phase (not summed, may double-count if aggregated) --")
        for name in sorted(non_overlappable):
            per_phase = {p.phase_name: getattr(p, name) for p in phases if getattr(p, name)}
            if per_phase:
                parts = ", ".join(f"{k}={v}" for k, v in per_phase.items())
                write_line(f"    {name:20s}: {parts}")
        write_line("")


class RollingLogHandler(logging.Handler):
    """Keeps the last N log records so they can be shown in a live panel."""

    def __init__(self, capacity: int = 8):
        super().__init__()
        self.records: Deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:  # pragma: no cover - never break the run on logging
            pass


class _StageBar:
    """tqdm-compatible handle for one task of the live pipeline display."""

    def __init__(self, display: "PipelineDisplay", task_id: Any, total: Optional[int]):
        self._display = display
        self._task_id = task_id
        self._total = total
        self.completed = 0

    @property
    def total(self) -> Optional[int]:
        return self._total

    @total.setter
    def total(self, value: Optional[int]) -> None:
        self._total = value
        self._display.set_total(self._task_id, value)

    def update(self, n: int = 1) -> None:
        self.completed += n
        self._display.advance(self._task_id, n)

    def refresh(self) -> None:
        pass

    def close(self) -> None:
        self._display.finish(self._task_id)


class PipelineDisplay:
    """Live multi-bar view of a pipelined run.

    One progress bar per stage (created lazily through :meth:`bar_factory`,
    which the orchestrator uses instead of ``tqdm``), plus a panel showing
    inbound queue depth per link and a bounded rolling log. Every log
    record is also mirrored to ``pipeline.log`` so nothing is lost.
    """

    def __init__(self, stage_names: Sequence[str], channels: Optional[Sequence[Any]] = None,
                 log_path: str = "pipeline.log", log_lines: int = 8):
        self.stage_names = list(stage_names)
        self.channels = list(channels or [])
        self.log_path = log_path
        self._live = None
        self._progress = None
        self._tasks: Dict[Any, Optional[int]] = {}
        self._rolling = RollingLogHandler(capacity=log_lines)
        self._file_handler: Optional[logging.Handler] = None
        self._available = True

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        try:
            from rich.live import Live
            from rich.progress import (
                BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
                TextColumn, TimeElapsedColumn,
            )
        except Exception:  # pragma: no cover - rich is a hard dependency in practice
            self._available = False
            return

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.fields[stage]}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("{task.fields[rate]}"),
            TimeElapsedColumn(),
        )
        self._live = Live(self._render(), refresh_per_second=4, transient=False)
        self._live.start()
        self._install_logging()

    def stop(self) -> None:
        self._remove_logging()
        if self._live is not None:
            try:
                self._live.update(self._render())
                self._live.stop()
            except Exception:  # pragma: no cover - defensive
                pass
            self._live = None

    def __enter__(self) -> "PipelineDisplay":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # -- progress API used by the orchestrator -------------------------
    def bar_factory(self, desc: str, total: Optional[int] = None, initial: int = 0):
        if not self._available or self._progress is None:
            return tqdm(total=total, initial=initial, desc=desc)
        task_id = self._progress.add_task(desc, total=total, completed=initial, stage=desc, rate="")
        self._tasks[task_id] = total
        return _StageBar(self, task_id, total)

    def set_total(self, task_id: Any, total: Optional[int]) -> None:
        if self._progress is not None:
            self._progress.update(task_id, total=total)
            self._tasks[task_id] = total
        self._refresh()

    def advance(self, task_id: Any, amount: int = 1) -> None:
        if self._progress is not None:
            self._progress.advance(task_id, amount)
        self._refresh()

    def finish(self, task_id: Any) -> None:
        if self._progress is None:
            return
        task = next((t for t in self._progress.tasks if t.id == task_id), None)
        if task is not None and task.total is None:
            self._progress.update(task_id, total=task.completed)
        self._refresh()

    # -- rendering -----------------------------------------------------
    def _refresh(self) -> None:
        if self._live is not None:
            try:
                self._live.update(self._render())
            except Exception:  # pragma: no cover - defensive
                pass

    def _render(self):
        from rich.console import Group
        from rich.panel import Panel

        parts = []
        if self._progress is not None:
            self._update_rates()
            parts.append(self._progress)
        queues = self._queue_line()
        if queues:
            parts.append(Panel(queues, title="queues", expand=False))
        if self._rolling.records:
            parts.append(Panel("\n".join(self._rolling.records), title="log", expand=False))
        return Group(*parts)

    def _update_rates(self) -> None:
        for task in self._progress.tasks:
            speed = task.speed or 0.0
            self._progress.update(task.id, rate=f"{speed:5.1f}/s")

    def _queue_line(self) -> str:
        if not self.channels:
            return ""
        return "   ".join(
            f"{getattr(ch, 'name', '?')}: {ch.depth}/{ch.maxsize}" for ch in self.channels
        )

    # -- logging -------------------------------------------------------
    def _install_logging(self) -> None:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
        self._rolling.setFormatter(formatter)
        root = logging.getLogger()
        root.addHandler(self._rolling)
        try:
            self._file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
            self._file_handler.setFormatter(formatter)
            root.addHandler(self._file_handler)
        except Exception:  # pragma: no cover - read-only filesystem
            self._file_handler = None

    def _remove_logging(self) -> None:
        root = logging.getLogger()
        for handler in (self._rolling, self._file_handler):
            if handler is not None and handler in root.handlers:
                root.removeHandler(handler)
        if self._file_handler is not None:
            self._file_handler.close()
            self._file_handler = None


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def write_line(msg: str = "") -> None:
    tqdm.write(msg)


def write_banner(title: str, width: int = 62) -> None:
    write_line("=" * width)
    write_line(f"  {title}")
    write_line("=" * width)


def print_scraper_header(max_pages: int | str, concurrency: int, rate: str) -> None:
    write_banner("MOSTAQL FREELANCERS SCRAPER")
    write_line(f"  max_pages={max_pages}  concurrency={concurrency}  rate={rate}")


def print_scrape_summary(total: int, ok: int, failed: int, http_stats: str) -> None:
    if failed:
        write_line(f"\n  !  {failed} profiles had fetch/parse issues — see scraper.log")
    write_line(f"\n  OK {total} profiles scraped  ({ok} ok, {failed} failed)")
    write_line(f"\n  HTTP stats: {http_stats}")


def print_completion_paths(json_path: str | Path, csv_path: str | Path, count: int) -> None:
    write_line("")
    write_banner("COMPLETE")
    write_line(f"  {count} freelancers indexed and saved")
    write_line(f"    JSON -> {json_path}")
    write_line(f"    CSV  -> {csv_path}")


def print_top_freelancers(df: pd.DataFrame, n: int = 10) -> None:
    if df.empty:
        return
    cols = [
        "name", "title", "completion_rate", "total_completed_projects", "rating",
    ]
    available = [c for c in cols if c in df.columns]
    print(f"\n-- Top {n} by Rating & Activity --")
    print(df[available].head(n).to_string())


def print_phase_stats(phase_name: str, total: int, success: int, failed: int, metrics: PhaseMetrics) -> None:
    """Print unified statistics for a single phase, wrapped in clear separators."""
    write_line("")
    write_banner(f"PHASE REPORT: {phase_name.upper()}")
    write_line(f"  Processed : {total}")
    write_line(f"  Success   : {success}")
    write_line(f"  Failed    : {failed}")

    if metrics.combos_processed or metrics.pages_found:
        write_line(f"  Combos    : {metrics.combos_processed}")
        write_line(f"  Pages     : {metrics.pages_found}")

    if metrics.urls_discovered:
        write_line(f"  Discovered: {metrics.urls_discovered} unique URLs")

    if metrics.profiles_fetched or metrics.portfolios_fetched:
        write_line(f"  Fetched   : {metrics.profiles_fetched} profiles, {metrics.portfolios_fetched} portfolios")

    if metrics.profiles_parsed:
        write_line(f"  Parsed    : {metrics.profiles_parsed} ({metrics.parse_success} ok, {metrics.parse_failed} failed)")

    if metrics.empty_combos or metrics.max_pages_seen or metrics.min_pages_seen:
        write_line(f"  Empty combos: {metrics.empty_combos}   Pages seen: min {metrics.min_pages_seen} / max {metrics.max_pages_seen}")

    if metrics.pages_scraped or metrics.duplicate_urls_skipped:
        write_line(f"  Pages scraped: {metrics.pages_scraped}   Duplicates skipped: {metrics.duplicate_urls_skipped}")

    if metrics.unknown_names:
        write_line(f"  Unknown names: {metrics.unknown_names}")

    if metrics.bytes_downloaded:
        write_line(f"  Downloaded: {metrics.bytes_downloaded / 1024:.1f} KB")

    if metrics.portfolio_fetch_failed:
        write_line(f"  Portfolio fetch failed: {metrics.portfolio_fetch_failed}")

    if metrics.portfolios_parsed or metrics.fields_missing:
        avg_missing = metrics.fields_missing / metrics.parse_success if metrics.parse_success else 0
        write_line(f"  Portfolios parsed: {metrics.portfolios_parsed}   Fields missing: {metrics.fields_missing} (avg {avg_missing:.1f}/profile)")

    if metrics.skipped_resumed:
        write_line(f"  Skipped (resumed): {metrics.skipped_resumed}")

    if metrics.duration_seconds:
        throughput = metrics.get_throughput(total)
        write_line(f"  Duration  : {metrics.duration_seconds:.1f}s  ({throughput:.2f} items/s)")

    net = metrics.get_network_summary()
    write_line(f"  Network   : {net['requests']} reqs, {net['retries']} retries, {net['429s']} rate-limits (429)")
    if net["errors"]:
        write_line(f"  Errors    : {net['errors']}")
    if net["404s"]:
        write_line(f"  Not Found : {net['404s']} (404)")
    if net["retry_wait_seconds"]:
        write_line(f"  Rate-limit wait: {net['retry_wait_seconds']:.1f}s")
    write_line("=" * 62)


def print_followup_report(
    *,
    input_path: Path,
    output_path: Path,
    total_in_file: int,
    failed_before: int,
    attempted: int,
    fixed: int,
    still_failed: int,
    unchanged_ok: int,
    http_stats: str,
    failed_urls: list[str] | None = None,
) -> None:
    write_banner("FOLLOW-UP REPORT")
    write_line(f"  Input file       : {input_path}")
    write_line(f"  Output file      : {output_path}")
    write_line(f"  Total records    : {total_in_file}")
    write_line(f"  Failed before    : {failed_before}")
    write_line("")
    write_line(f"  Attempted repair : {attempted}")
    write_line(f"  Fixed            : {fixed}")
    write_line(f"  Still failed     : {still_failed}")
    write_line(f"  Unchanged (ok)   : {unchanged_ok}")
    write_line(f"  Failed after     : {failed_before - fixed}")
    write_line("")
    write_line(f"  HTTP stats       : {http_stats}")

    if failed_urls:
        write_line("")
        write_line("  Still-failing profiles:")
        for url in failed_urls[:20]:
            slug = url.rstrip("/").split("/")[-1]
            write_line(f"    - {slug}  ({url})")
        if len(failed_urls) > 20:
            write_line(f"    ... and {len(failed_urls) - 20} more")

    write_line("")
    success_rate = (fixed / attempted * 100) if attempted else 100.0
    write_line(f"  Repair success rate: {success_rate:.1f}%")
