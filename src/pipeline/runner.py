"""Concurrent execution of a validated ``--pipelined`` stage chain."""

import asyncio
import inspect
import logging
from typing import Any, List, Optional, Sequence

from .channel import Channel, NullChannel
from .cli_chain import ParsedStage

log = logging.getLogger(__name__)


class StageFailure(Exception):
    """Wraps the first stage exception so the failing stage can be named."""

    def __init__(self, stage_name: str, error: BaseException):
        self.stage_name = stage_name
        self.error = error
        super().__init__(f"stage '{stage_name}' failed: {error}")


class PipelineRunner:
    """Runs every stage of a chain concurrently, linked by bounded channels.

    Each link is a bounded :class:`Channel`, which gives natural
    backpressure: a fast upstream blocks once the downstream queue is full
    instead of buffering without limit.
    """

    DEFAULT_CHANNEL_SIZE = 200

    def __init__(self, orchestrator: Any, stages: Sequence[ParsedStage],
                 channel_size: int = DEFAULT_CHANNEL_SIZE, display: Any = None,
                 live_display: bool = False):
        self.orchestrator = orchestrator
        self.stages = list(stages)
        self.channel_size = channel_size
        self.display = display
        self.live_display = live_display
        self.channels: List[Channel] = []
        self.failed_stage: Optional[str] = None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self) -> int:
        """Execute the pipeline; returns a process exit code."""
        from ..services.network import disable_shared_limiter, enable_shared_limiter

        config = getattr(self.orchestrator, "config", None)
        if config is not None:
            enable_shared_limiter(config)
        try:
            return asyncio.run(self._run())
        except KeyboardInterrupt:
            log.warning("Pipeline interrupted by user; checkpoints remain resumable.")
            return 130
        finally:
            if config is not None:
                disable_shared_limiter()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _run(self) -> int:
        self.channels = [
            Channel(name=f"{a.name}->{b.name}", maxsize=self.channel_size)
            for a, b in zip(self.stages, self.stages[1:])
        ]

        self._start_display()

        tasks: List[asyncio.Task] = []
        for index, stage in enumerate(self.stages):
            inp = self.channels[index - 1] if index > 0 else None
            out = self.channels[index] if index < len(self.channels) else NullChannel()
            coro = self._build_stage_coroutine(stage, inp, out)
            tasks.append(asyncio.create_task(coro, name=stage.name))

        exit_code = 0
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            failure = next((t for t in done if not t.cancelled() and t.exception()), None)
            if failure is not None:
                self.failed_stage = failure.get_name()
                error = failure.exception()
                log.error("Stage '%s' failed: %s", self.failed_stage, error)
                await self._cancel(pending)
                exit_code = 1
            elif pending:
                await asyncio.gather(*pending)
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self._cancel(tasks)
            raise
        finally:
            await self._close_channels()
            self._stop_display()
            self._print_summary()

        return exit_code

    def _build_stage_coroutine(self, stage: ParsedStage, inp: Optional[Channel], out: Channel):
        """Bind a stage's streaming method to its channels and CLI options."""
        method = getattr(self.orchestrator, stage.spec.method)
        params = inspect.signature(method).parameters
        options = stage.options or {}

        kwargs = {}
        if "inp" in params:
            kwargs["inp"] = inp
        if "out" in params:
            kwargs["out"] = out
        if "use_continue" in params:
            resume = options.get("resume", True)
            new = options.get("new", False)
            kwargs["use_continue"] = bool(resume) and not bool(new)
        if "limit" in params:
            kwargs["limit"] = options.get("limit")
        return method(**kwargs)

    # -- live display --------------------------------------------------
    def _start_display(self) -> None:
        """Swap the per-phase tqdm bars for a single live multi-bar view."""
        if self.display is None and self.live_display:
            from ..utils.reporting import PipelineDisplay

            self.display = PipelineDisplay(
                [s.name for s in self.stages], channels=self.channels
            )
        if self.display is None:
            return
        self.display.channels = list(self.channels)
        self.display.start()
        self.orchestrator.progress_factory = self.display.bar_factory

    def _stop_display(self) -> None:
        if self.display is None:
            return
        self.display.stop()
        self.orchestrator.progress_factory = None

    @staticmethod
    async def _cancel(tasks) -> None:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_channels(self) -> None:
        for channel in self.channels:
            try:
                await channel.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def _print_summary(self) -> None:
        registry = getattr(self.orchestrator, "registry", None)
        if registry is not None and hasattr(registry, "set_order"):
            registry.set_order([s.name for s in self.stages])
        summary = getattr(self.orchestrator, "print_session_summary", None)
        if summary is not None:
            summary()
        if self.failed_stage:
            log.error("Pipeline aborted; failing stage: %s", self.failed_stage)
