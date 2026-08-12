"""Declarative description of every pipelinable stage.

The registry is the single source of truth for:

* which positions (``start`` / ``middle`` / ``end``) a stage supports,
* which item type it consumes and produces (used to validate a chain),
* which orchestrator coroutine implements its streaming form,
* which CLI options only make sense in ``start`` position.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Type

from ..models import Freelancer, PageCountItem, KeywordItem, ProfileDetails, RawProfileRecord


class StagePosition(str, Enum):
    """Where a stage is allowed to sit inside a ``--pipelined`` chain."""

    START = "start"
    MIDDLE = "middle"
    END = "end"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class StageSpec:
    """Static capabilities of one pipeline stage."""

    name: str
    positions: FrozenSet[StagePosition]
    method: str
    input_type: Optional[Type[Any]] = None
    output_type: Optional[Type[Any]] = None
    concurrency_attr: str = "dir_concurrency"
    start_only_options: FrozenSet[str] = frozenset()
    options: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    seed_note: str = ""

    def supports(self, position: StagePosition) -> bool:
        return position in self.positions

    def positions_label(self) -> str:
        order = [StagePosition.START, StagePosition.MIDDLE, StagePosition.END]
        return ", ".join(str(p) for p in order if p in self.positions)


STAGE_REGISTRY: Dict[str, StageSpec] = {
    "followup": StageSpec(
        name="followup",
        positions=frozenset({StagePosition.START}),
        method="stream_followup",
        input_type=None,
        output_type=KeywordItem,
        description="Extract unique names from existing data and prepare search keywords.",
        seed_note="Always seeds itself from mostaql_development_all_users.json.",
    ),
    "discovery": StageSpec(
        name="discovery",
        positions=frozenset({StagePosition.START}),
        method="stream_discovery",
        input_type=None,
        output_type=PageCountItem,
        concurrency_attr="dir_concurrency",
        start_only_options=frozenset(),
        description="Binary search every filter combination for its page count.",
        seed_note="Always seeds itself from the combo list; it can only open a chain.",
    ),
    "extract": StageSpec(
        name="extract",
        positions=frozenset({StagePosition.START, StagePosition.MIDDLE, StagePosition.END}),
        method="stream_extraction",
        input_type=Any,  # Supports PageCountItem or KeywordItem
        output_type=Freelancer,
        concurrency_attr="dir_concurrency",
        start_only_options=frozenset(),
        description="Walk listing pages and collect unique freelancer URLs.",
        seed_note="In start position it seeds from pagination_cache.json; otherwise it consumes combos streamed by discovery.",
    ),
    "fetch": StageSpec(
        name="fetch",
        positions=frozenset({StagePosition.START, StagePosition.MIDDLE, StagePosition.END}),
        method="stream_fetch",
        input_type=Freelancer,
        output_type=RawProfileRecord,
        concurrency_attr="profile_concurrency",
        start_only_options=frozenset(),
        description="Download raw profile and portfolio HTML.",
        seed_note="In start position it seeds from the extracted URL list; otherwise it consumes freelancers streamed by extract.",
    ),
    "parse": StageSpec(
        name="parse",
        positions=frozenset({StagePosition.START, StagePosition.MIDDLE, StagePosition.END}),
        method="stream_parse",
        input_type=RawProfileRecord,
        output_type=ProfileDetails,
        concurrency_attr="profile_concurrency",
        start_only_options=frozenset(),
        description="Turn cached raw HTML into structured profile records.",
        seed_note="In start position it seeds from checkpoint_fetch.jsonl; otherwise it consumes raw records streamed by fetch.",
    ),
}


def get_spec(name: str) -> StageSpec:
    """Look up a stage spec by command name, raising a clear error."""
    try:
        return STAGE_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(STAGE_REGISTRY))
        raise KeyError(f"'{name}' is not a pipelinable command (known: {known})") from None
