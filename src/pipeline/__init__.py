"""Streaming pipeline support for the ``--pipelined`` CLI mode."""

from .channel import Channel, NullChannel, SeededChannel
from .spec import StagePosition, StageSpec, STAGE_REGISTRY

__all__ = [
    "Channel",
    "NullChannel",
    "SeededChannel",
    "StagePosition",
    "StageSpec",
    "STAGE_REGISTRY",
]
