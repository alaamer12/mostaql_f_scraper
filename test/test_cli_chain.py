"""Tests for splitting and parsing chained --pipelined command lines."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import typer
from src.pipeline import cli_chain
from src.pipeline.spec import StagePosition

# Build Typer app for testing CLI chain parsing
app = typer.Typer()

@app.command()
def discovery(new: bool = False):
    pass

@app.command()
def extract(new: bool = False, limit: int = None):
    pass

@app.command()
def fetch(limit: int = None, resume: bool = True):
    pass

@app.command()
def parse(resume: bool = True):
    pass

@app.command()
def stats():
    pass


def test_is_pipelined():
    assert cli_chain.is_pipelined(["discovery", "--pipelined", "extract"]) is True
    assert cli_chain.is_pipelined(["discovery", "--new"]) is False


def test_split_argv():
    argv = ["fetch", "--limit", "5", "--pipelined", "parse"]
    assert cli_chain.split_argv(argv) == [["fetch", "--limit", "5"], ["parse"]]


def test_parse_chain_positions():
    stages = cli_chain.parse_chain(
        ["discovery", "--pipelined", "extract", "--pipelined", "fetch"], app
    )
    assert [s.name for s in stages] == ["discovery", "extract", "fetch"]
    assert [s.position for s in stages] == [
        StagePosition.START, StagePosition.MIDDLE, StagePosition.END
    ]


def test_parse_chain_uses_command_options():
    stages = cli_chain.parse_chain(["fetch", "--limit", "5", "--pipelined", "parse"], app)
    assert stages[0].options["limit"] == 5
    assert stages[0].options["resume"] is True
    assert "limit" in stages[0].explicit_options
    assert "resume" not in stages[0].explicit_options


def test_parse_chain_rejects_unknown_option():
    with pytest.raises(cli_chain.ChainError):
        cli_chain.parse_chain(["fetch", "--nope", "--pipelined", "parse"], app)


def test_parse_chain_rejects_non_pipelinable_command():
    with pytest.raises(cli_chain.ChainError):
        cli_chain.parse_chain(["stats", "--pipelined", "parse"], app)


def test_parse_chain_rejects_empty_segment():
    with pytest.raises(cli_chain.ChainError):
        cli_chain.parse_chain(["extract", "--pipelined"], app)


def test_parse_chain_rejects_option_instead_of_command():
    with pytest.raises(cli_chain.ChainError):
        cli_chain.parse_chain(["extract", "--pipelined", "--limit"], app)


def test_format_chain():
    stages = cli_chain.parse_chain(["extract", "--pipelined", "fetch"], app)
    assert cli_chain.format_chain(stages) == "extract[start] -> fetch[end]"


def test_describe_positions_mentions_allowed_positions():
    text = cli_chain.describe_positions("discovery")
    assert "start" in text
    assert "middle" not in text
