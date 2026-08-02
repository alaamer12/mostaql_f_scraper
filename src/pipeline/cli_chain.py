"""Parsing and validation of chained ``--pipelined`` command lines.

``python main.py discovery --pipelined extract --pipelined fetch`` is split
into one segment per stage; each segment is then parsed by the *existing*
Typer/Click command so option definitions never have to be duplicated here.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Set

import click
import typer

from .spec import STAGE_REGISTRY, StagePosition, StageSpec

SEPARATOR = "--pipelined"


class ChainError(Exception):
    """Raised when a ``--pipelined`` chain is malformed or impossible."""


@dataclass
class ParsedStage:
    """One resolved segment of a pipelined command line."""

    name: str
    spec: StageSpec
    position: StagePosition
    options: Dict[str, Any] = field(default_factory=dict)
    explicit_options: Set[str] = field(default_factory=set)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"ParsedStage({self.name}, {self.position}, {self.options})"


def is_pipelined(argv: Sequence[str]) -> bool:
    """True when the command line uses the ``--pipelined`` separator."""
    return SEPARATOR in argv


def split_argv(argv: Sequence[str]) -> List[List[str]]:
    """Split an argv list into one segment per stage."""
    segments: List[List[str]] = [[]]
    for token in argv:
        if token == SEPARATOR:
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def parse_chain(argv: Sequence[str], app: typer.Typer) -> List[ParsedStage]:
    """Turn an argv list into an ordered, validated list of stages."""
    segments = split_argv(argv)
    group = typer.main.get_command(app)
    root_ctx = click.Context(group, info_name="main.py", resilient_parsing=False)

    stages: List[ParsedStage] = []
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        if not segment:
            raise ChainError(
                f"Empty command around '{SEPARATOR}' (segment {index + 1}). "
                f"Usage: main.py <command> [options] {SEPARATOR} <command> [options]"
            )

        name = segment[0]
        if name.startswith("-"):
            raise ChainError(
                f"Expected a command name after '{SEPARATOR}', got the option '{name}'."
            )

        spec = STAGE_REGISTRY.get(name)
        if spec is None:
            known = ", ".join(sorted(STAGE_REGISTRY))
            raise ChainError(f"'{name}' cannot be pipelined. Pipelinable commands: {known}.")

        command = group.get_command(root_ctx, name)
        if command is None:  # pragma: no cover - registry/CLI mismatch
            raise ChainError(f"'{name}' is not a known command.")

        try:
            ctx = command.make_context(name, list(segment[1:]), parent=root_ctx)
        except Exception as exc:  # Typer vendors its own click exception classes
            message = getattr(exc, "format_message", None)
            if message is None:
                raise
            raise ChainError(f"Invalid options for '{name}': {message()}") from None

        explicit = {key for key in ctx.params if _from_command_line(ctx, key)}

        if index == 0:
            position = StagePosition.START
        elif index == last:
            position = StagePosition.END
        else:
            position = StagePosition.MIDDLE

        stages.append(
            ParsedStage(
                name=name,
                spec=spec,
                position=position,
                options=dict(ctx.params),
                explicit_options=explicit,
            )
        )

    validate_chain(stages)
    return stages


def _from_command_line(ctx: Any, key: str) -> bool:
    """True when an option value came from argv rather than its default."""
    source = ctx.get_parameter_source(key)
    return getattr(source, "name", "") == "COMMANDLINE"


def validate_chain(stages: List[ParsedStage]) -> None:
    """Check positions, duplicates and adjacent item-type compatibility."""
    if len(stages) < 2:
        raise ChainError(
            f"A pipelined run needs at least two commands separated by '{SEPARATOR}'."
        )

    seen: Set[str] = set()
    for stage in stages:
        if stage.name in seen:
            raise ChainError(f"Command '{stage.name}' appears more than once in the chain.")
        seen.add(stage.name)

    for stage in stages:
        if not stage.spec.supports(stage.position):
            raise ChainError(
                f"'{stage.name}' cannot run in {stage.position} position "
                f"(allowed positions: {stage.spec.positions_label()})."
            )

    for upstream, downstream in zip(stages, stages[1:]):
        produced = upstream.spec.output_type
        consumed = downstream.spec.input_type
        if consumed is None:
            raise ChainError(
                f"'{downstream.name}' does not consume anything, so it cannot follow "
                f"'{upstream.name}' (allowed positions: {downstream.spec.positions_label()})."
            )
        if produced is not consumed:
            produced_name = getattr(produced, "__name__", str(produced))
            consumed_name = getattr(consumed, "__name__", str(consumed))
            raise ChainError(
                f"'{upstream.name}' produces {produced_name} but '{downstream.name}' "
                f"consumes {consumed_name}; they cannot be chained."
            )

    for stage in stages:
        if stage.position is StagePosition.START:
            continue
        ignored = sorted(stage.explicit_options & stage.spec.start_only_options)
        if ignored:
            raise ChainError(
                f"Option(s) {', '.join('--' + o for o in ignored)} of '{stage.name}' only apply "
                f"in start position, but it runs in {stage.position} position."
            )


def describe_positions(name: str) -> str:
    """Help-text fragment describing where a command may sit in a chain."""
    spec = STAGE_REGISTRY.get(name)
    if spec is None:
        return ""
    parts = [f"Pipelined positions: {spec.positions_label()}."]
    if spec.seed_note:
        parts.append(spec.seed_note)
    return " ".join(parts)


def format_chain(stages: Sequence[ParsedStage]) -> str:
    return " -> ".join(f"{s.name}[{s.position}]" for s in stages)
