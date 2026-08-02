"""Tests for --pipelined chain validation rules."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import app  # noqa: E402
from src.pipeline import cli_chain  # noqa: E402
from src.pipeline.spec import STAGE_REGISTRY, StagePosition  # noqa: E402


def _chain(*argv):
    return cli_chain.parse_chain(list(argv), app)


def test_valid_full_chain():
    stages = _chain("discovery", "--pipelined", "extract", "--pipelined", "fetch", "--pipelined", "parse")
    assert [s.name for s in stages] == ["discovery", "extract", "fetch", "parse"]


def test_discovery_cannot_be_last():
    with pytest.raises(cli_chain.ChainError) as exc:
        _chain("parse", "--pipelined", "discovery")
    message = str(exc.value)
    assert "discovery" in message
    assert "start" in message


def test_duplicate_stage_rejected():
    with pytest.raises(cli_chain.ChainError) as exc:
        _chain("extract", "--pipelined", "fetch", "--pipelined", "extract")
    assert "more than once" in str(exc.value)


def test_incompatible_types_rejected():
    with pytest.raises(cli_chain.ChainError) as exc:
        _chain("extract", "--pipelined", "parse")
    message = str(exc.value)
    assert "Freelancer" in message
    assert "RawProfileRecord" in message


def test_single_stage_is_not_a_pipeline():
    with pytest.raises(cli_chain.ChainError):
        cli_chain.validate_chain(_stage_list("extract"))


def _stage_list(*names):
    stages = []
    last = len(names) - 1
    for i, name in enumerate(names):
        position = (
            StagePosition.START if i == 0
            else StagePosition.END if i == last
            else StagePosition.MIDDLE
        )
        stages.append(cli_chain.ParsedStage(name=name, spec=STAGE_REGISTRY[name], position=position))
    return stages


def test_start_only_options_rejected_downstream():
    stages = _stage_list("extract", "fetch")
    object.__setattr__(STAGE_REGISTRY["fetch"], "start_only_options", frozenset({"limit"}))
    try:
        stages[1].explicit_options = {"limit"}
        with pytest.raises(cli_chain.ChainError) as exc:
            cli_chain.validate_chain(stages)
        assert "--limit" in str(exc.value)
    finally:
        object.__setattr__(STAGE_REGISTRY["fetch"], "start_only_options", frozenset())


def test_position_labels():
    assert STAGE_REGISTRY["discovery"].positions_label() == "start"
    assert STAGE_REGISTRY["parse"].positions_label() == "start, middle, end"
