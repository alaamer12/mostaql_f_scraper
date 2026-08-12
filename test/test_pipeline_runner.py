"""Tests for PipelineRunner using fake stages (no network, no files)."""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipeline.cli_chain import ParsedStage  # noqa: E402
from src.pipeline.runner import PipelineRunner  # noqa: E402
from src.pipeline.spec import StagePosition, StageSpec  # noqa: E402


def _spec(name: str, method: str) -> StageSpec:
    return StageSpec(name=name, positions=frozenset({StagePosition.START}), method=method)


def _stages(*pairs):
    return [
        ParsedStage(name=name, spec=_spec(name, method), position=StagePosition.START)
        for name, method in pairs
    ]


class FakeRegistry:
    def __init__(self):
        self.order = None
        self.printed = False

    def set_order(self, names):
        self.order = list(names)


class FakeOrchestrator:
    """Minimal stand-in exposing the attributes PipelineRunner relies on."""

    def __init__(self):
        self.registry = FakeRegistry()
        self.events = []

    def print_session_summary(self):
        self.registry.printed = True

    async def stream_source(self, out, use_continue=True):
        for i in range(5):
            await asyncio.sleep(0.01)
            await out.send(i)
        self.events.append(("source_done", time.monotonic()))
        await out.close()

    async def stream_sink(self, inp, out, use_continue=True):
        async for item in inp:
            self.events.append((f"sink_{item}", time.monotonic()))
        await out.close()

    async def stream_boom(self, inp, out, use_continue=True):
        await asyncio.sleep(0.01)
        raise RuntimeError("stage exploded")

    async def stream_slow_sink(self, inp, out, use_continue=True):
        async for _ in inp:
            await asyncio.sleep(0.02)
        await out.close()

    async def stream_burst(self, out, use_continue=True):
        self.max_depth = 0
        for i in range(20):
            await out.send(i)
            self.max_depth = max(self.max_depth, out.depth)
        await out.close()

    async def stream_forever(self, inp, out, use_continue=True):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.events.append(("cancelled", time.monotonic()))
            raise


def test_stages_overlap_instead_of_running_sequentially():
    orch = FakeOrchestrator()
    runner = PipelineRunner(orch, _stages(("source", "stream_source"), ("sink", "stream_sink")))
    assert runner.run() == 0

    names = [name for name, _ in orch.events]
    assert names.count("source_done") == 1
    # The sink consumed its first item before the source finished producing.
    assert names.index("sink_0") < names.index("source_done")


def test_backpressure_caps_queue_depth():
    orch = FakeOrchestrator()
    runner = PipelineRunner(
        orch, _stages(("burst", "stream_burst"), ("slow", "stream_slow_sink")), channel_size=3
    )
    assert runner.run() == 0
    assert orch.max_depth <= 3


def test_failure_cancels_the_rest_and_names_the_stage():
    orch = FakeOrchestrator()
    runner = PipelineRunner(orch, _stages(("boom", "stream_boom"), ("forever", "stream_forever")))
    assert runner.run() == 1
    assert runner.failed_stage == "boom"
    assert ("cancelled" in [name for name, _ in orch.events])


def test_empty_upstream_closes_downstream_cleanly():
    class EmptyOrchestrator(FakeOrchestrator):
        async def stream_source(self, out, use_continue=True):
            await out.close()

    orch = EmptyOrchestrator()
    runner = PipelineRunner(orch, _stages(("source", "stream_source"), ("sink", "stream_sink")))
    assert runner.run() == 0
    assert orch.events == []


def test_metrics_registry_ordered_by_pipeline_position():
    orch = FakeOrchestrator()
    runner = PipelineRunner(orch, _stages(("source", "stream_source"), ("sink", "stream_sink")))
    runner.run()
    assert orch.registry.order == ["source", "sink"]
    assert orch.registry.printed is True


def test_options_are_translated_into_stage_kwargs():
    orch = FakeOrchestrator()
    stage = ParsedStage(
        name="sink",
        spec=_spec("sink", "stream_sink"),
        position=StagePosition.START,
        options={"resume": True, "new": True},
    )
    runner = PipelineRunner(orch, [stage])
    coro = runner._build_stage_coroutine(stage, None, None)
    assert coro.cr_frame.f_locals["use_continue"] is False
    coro.close()


def test_environment_detection_and_plain_mode():
    from src.utils.reporting import is_colab_or_notebook, is_interactive_tty, PipelineDisplay

    # Detection functions return booleans without throwing exceptions
    assert isinstance(is_colab_or_notebook(), bool)
    assert isinstance(is_interactive_tty(), bool)

    display = PipelineDisplay(["stage1"])
    display.start()
    # In non-interactive test runner, display._available should be False
    assert display._available is False
    display.stop()
