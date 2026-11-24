"""Decode the receiver's channel grids (OPINFINS / OPINFASP)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ACTIVE = "2"
NONE = "---"


@dataclass(frozen=True)
class ChannelMap:
    """The active channels and their layout, e.g. "5.1" or "7.1.2"."""

    layout: str | None
    active: tuple[str, ...] = ()


def decode(text: str | None, spec: dict[str, Any]) -> ChannelMap:
    if not text:
        return ChannelMap(None)
    names = spec.get("positions", {})
    lfe = set(spec.get("lfe", ()))
    height = set(spec.get("height", ()))
    positions = [str(i) for i, c in enumerate(text.strip()) if c == ACTIVE]
    active = tuple(names.get(p, f"ch{p}") for p in positions)
    if not active:
        return ChannelMap(NONE)
    lfe_count = sum(1 for c in active if c in lfe)
    height_count = sum(1 for c in active if c in height)
    unnamed = sum(1 for p in positions if p not in names)
    bed = len(active) - lfe_count - height_count - unnamed
    layout = f"{bed}.{lfe_count}" + (f".{height_count}" if height_count else "")
    return ChannelMap(layout + (f"+{unnamed}" if unnamed else ""), active)
