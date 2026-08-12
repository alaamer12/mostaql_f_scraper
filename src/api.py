from enum import Enum
import asyncio
import logging
from typing import Any, List, Optional, Sequence, Union

from .models import ScrapeConfig
from .services.orchestrator import ScraperOrchestrator
from .pipeline.runner import PipelineRunner
from .pipeline.cli_chain import ParsedStage
from .pipeline.spec import STAGE_REGISTRY, StagePosition

log = logging.getLogger(__name__)

# Alias for convenience as requested by the user
Configuration = ScrapeConfig


class Commands(str, Enum):
    """Enumeration of all available scraper commands/phases."""
    DISCOVERY = "discovery"
    EXTRACT = "extract"
    FETCH = "fetch"
    PARSE = "parse"
    DEEP_SCRAPE = "deep_scrape"
    SCRAPE = "scrape"
    CLEANUP = "cleanup"
    STATS = "stats"
    SAMPLE = "sample"

    def __str__(self) -> str:
        return self.value


class Pipeline:
    """Programmatic Pipeline runner for executing scraper phases and chains

    Designed for Python scripts, Jupyter notebooks, and Google Colab where
    CLI execution is cumbersome or unavailable.
    """

    def __init__(self, config: Optional[Configuration] = None):
        self.config = config or Configuration()
        self.orchestrator = ScraperOrchestrator(self.config)

    def run(
        self,
        command: Union[Commands, str],
        *,
        new: bool = False,
        resume: bool = True,
        deep: bool = False,
        limit: Optional[int] = None,
        sample: bool = False,
        chain: Optional[Sequence[Union[Commands, str]]] = None,
        live_display: bool = False,
    ) -> Any:
        """Run a scraper command or a pipelined chain of commands.

        Args:
            command: The command or phase to run (e.g. Commands.DISCOVERY or "scrape").
            new: Ignore cache/checkpoints and start fresh.
            resume: Resume from existing cache/checkpoints (default: True).
            deep: Run deep scrape (fetch + parse) in composite commands.
            limit: Optional cap on the number of profiles processed.
            sample: Run live sample smoke test (for sample command).
            chain: Optional sequence of stages for concurrent pipelined execution.
            live_display: Enable rich live multi-bar display during pipelined run.

        Returns:
            The result of the command/pipeline (exit code for pipelines, or phase outputs).
        """
        cmd_str = str(command) if isinstance(command, Commands) else str(command).lower()

        if chain:
            return self._run_pipelined_chain(chain, live_display=live_display)

        if cmd_str == Commands.DISCOVERY:
            res = asyncio.run(self.orchestrator.run_discovery(use_continue=resume and not new))
            self.orchestrator.print_session_summary()
            return res

        elif cmd_str == Commands.EXTRACT:
            res = asyncio.run(self.orchestrator.run_extraction(use_continue=resume and not new))
            self.orchestrator.print_session_summary()
            return res

        elif cmd_str == Commands.FETCH:
            res = asyncio.run(self.orchestrator.run_fetch(limit=limit, use_continue=resume))
            self.orchestrator.print_session_summary()
            return res

        elif cmd_str == Commands.PARSE:
            res = self.orchestrator.run_parse(use_continue=resume)
            self.orchestrator.print_session_summary()
            return res

        elif cmd_str == Commands.DEEP_SCRAPE:
            res = asyncio.run(self.orchestrator.run_deep_scrape(limit=limit, use_continue=resume))
            self.orchestrator.print_session_summary()
            return res

        elif cmd_str == Commands.SCRAPE:
            asyncio.run(self.orchestrator.run_discovery(use_continue=not new))
            asyncio.run(self.orchestrator.run_extraction(use_continue=not new))
            if deep:
                asyncio.run(self.orchestrator.run_deep_scrape(limit=limit, use_continue=resume))
            self.orchestrator.print_session_summary()
            return True

        elif cmd_str == Commands.CLEANUP:
            files_to_remove = [
                self.config.resolve_path("checkpoint_profiles_json"),
                self.config.resolve_path("checkpoint_fetch_json"),
                self.config.resolve_path("pagination_cache"),
                "scraper.log",
                "pipeline.log",
                self.config.resolve_path("output_json"),
                self.config.resolve_path("output_csv"),
                self.config.resolve_path("profiles_json"),
                self.config.resolve_path("profiles_csv"),
            ]
            from pathlib import Path
            for filename in files_to_remove:
                Path(filename).unlink(missing_ok=True)
            return True

        elif cmd_str == Commands.STATS:
            try:
                from .dashboard import load_data
                df = load_data(self.config.resolve_path("profiles_json"))
                stats_dict = {
                    "total_profiles": len(df),
                    "categories": list(df["category"].unique()) if "category" in df.columns else []
                }
                return stats_dict
            except Exception:
                try:
                    data = self.orchestrator.storage.load_json(self.config.resolve_path("output_json"))
                    return {"total_discovered_urls": len(data) if data else 0}
                except Exception:
                    return {"total_discovered_urls": 0}

        elif cmd_str == Commands.SAMPLE:
            ok = asyncio.run(self.orchestrator.run_sample(limit=limit or 2))
            self.orchestrator.print_session_summary()
            return ok

        else:
            raise ValueError(f"Unknown command: {cmd_str}. Supported commands: {[c.value for c in Commands]}")

    def _run_pipelined_chain(self, chain: Sequence[Union[Commands, str]], live_display: bool = False) -> int:
        stages: List[ParsedStage] = []
        last = len(chain) - 1
        for index, item in enumerate(chain):
            name = str(item) if isinstance(item, Commands) else str(item).lower()
            spec = STAGE_REGISTRY.get(name)
            if spec is None:
                known = ", ".join(sorted(STAGE_REGISTRY))
                raise ValueError(f"'{name}' cannot be pipelined. Pipelinable commands: {known}.")

            if index == 0:
                position = StagePosition.START
            elif index == last:
                position = StagePosition.END
            else:
                position = StagePosition.MIDDLE

            if not spec.supports(position):
                raise ValueError(f"'{name}' cannot run in {position} position.")

            stages.append(
                ParsedStage(
                    name=name,
                    spec=spec,
                    position=position,
                    options={},
                    explicit_options=set(),
                )
            )

        runner = PipelineRunner(self.orchestrator, stages, live_display=live_display)
        return runner.run()
