"""Manual graphic-EQ encoding for the setup config API (see transport.webconfig).

The per-band values are read and written as small XML documents through the
setup interface config endpoint. This module is the pure translation between
those XML documents and the typed :class:`GraphicEqState`; it holds no protocol
constants of its own - the band tags, dB divisor and root tag all come from the
profile's ``grammar.graphic_eq`` section. It has no Home Assistant or transport
dependency, so it is unit-testable in isolation.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .models import GraphicEqState


def band_tag(grammar: dict[str, Any], label: str) -> str:
    """Return the XML tag for a band label, e.g. '1 kHz' -> 'Eq1kHz'."""

    return grammar.get("band_tag_prefix", "Eq") + label.replace(" ", "")


def band_tags(grammar: dict[str, Any]) -> list[str]:
    """Return the ordered band XML tags for all configured bands."""

    return [band_tag(grammar, label) for label in grammar.get("bands", [])]


def parse(xml_text: str, grammar: dict[str, Any]) -> GraphicEqState:
    """Parse a get_config graphic-EQ document into a GraphicEqState.

    Only the currently-selected channel's bands are present in the document, so
    the returned state carries that channel index and its per-band gains (dB).
    Malformed input yields an empty state rather than raising.
    """

    state = GraphicEqState()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return state

    selection = (root.findtext("SpeakerSelection") or "").strip()
    if selection:
        state.speaker_selection = selection

    adjust = root.find("AdjustEQ")
    if adjust is None:
        return state
    channel = (adjust.findtext("Channel") or "").strip()
    if channel.lstrip("-").isdigit():
        state.channel_index = int(channel)

    divisor = float(grammar.get("db_divisor", 10)) or 1.0
    for tag in band_tags(grammar):
        raw = (adjust.findtext(tag) or "").strip()
        if raw.lstrip("-").isdigit():
            state.bands[tag] = int(raw) / divisor
    return state


def _wrap(root_tag: str, inner: str) -> str:
    return f"<{root_tag}>{inner}</{root_tag}>"


def adjust_payload(
    grammar: dict[str, Any], channel_index: int, bands_db: dict[str, float]
) -> str:
    """Build the set_config XML to write a full band block for one channel.

    The receiver rejects a partial AdjustEQ, so every band is sent; ``bands_db``
    maps each band tag to its gain in dB. Bands missing from the map default to
    0 dB so the document is always complete.
    """

    divisor = float(grammar.get("db_divisor", 10)) or 1.0
    parts = [f"<Channel>{channel_index}</Channel>"]
    for tag in band_tags(grammar):
        wire = int(round(float(bands_db.get(tag, 0.0)) * divisor))
        parts.append(f"<{tag}>{wire}</{tag}>")
    return _wrap(grammar["root_tag"], f"<AdjustEQ>{''.join(parts)}</AdjustEQ>")


def channel_payload(grammar: dict[str, Any], channel_index: int) -> str:
    """Build the set_config XML to select the channel being adjusted."""

    return _wrap(
        grammar["root_tag"], f"<AdjustEQ><Channel>{channel_index}</Channel></AdjustEQ>"
    )


def speaker_selection_payload(grammar: dict[str, Any], code: str) -> str:
    """Build the set_config XML to set the speaker-selection mode."""

    return _wrap(grammar["root_tag"], f"<SpeakerSelection>{code}</SpeakerSelection>")


def curve_copy_payload(grammar: dict[str, Any]) -> str:
    """Build the set_config XML to copy the reference curve into the manual EQ."""

    return _wrap(grammar["root_tag"], "<CurveCopy>1</CurveCopy>")
