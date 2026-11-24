"""Manual graphic-EQ encoding for the setup config API (see transport.webconfig)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..models import GraphicEqState


def band_tag(grammar: dict[str, Any], label: str) -> str:
    """Return the XML tag for a band label, e.g. '1 kHz' -> 'Eq1kHz'."""

    return grammar.get("band_tag_prefix", "Eq") + label.replace(" ", "")


def band_tags(grammar: dict[str, Any]) -> list[str]:
    """Return the ordered band XML tags for all configured bands."""

    return [band_tag(grammar, label) for label in grammar.get("bands", [])]


def parse(xml_text: str, grammar: dict[str, Any]) -> GraphicEqState:
    """Parse a get_config graphic-EQ document into a GraphicEqState."""

    state = GraphicEqState()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return state

    selection = (root.findtext("SpeakerSelection") or "").strip()
    if selection:
        state.speaker_selection = selection

    flags = root.findtext("SelectableSpeaker/Each") or root.findtext(
        "SelectableSpeaker/LR"
    )
    if flags:
        state.selectable = flags.strip()

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


def channel_options(
    grammar: dict[str, Any], speaker_selection: str | None, selectable: str
) -> list[tuple[int, str]]:
    """Return the adjustable channels as (index, label) for the current mode."""

    # Mode values (from the setup UI): 1 = All, 2 = Each, 3 = Left/Right.
    if speaker_selection == "1":  # All - one shared curve, no channel pick
        return [(0, "All")]
    key = "channels_each" if speaker_selection == "2" else "channels_lr"
    labels = grammar.get(key, [])
    enable = grammar.get("channel_enable", "3")
    options = [
        (index, labels[index])
        for index, flag in enumerate(selectable)
        if flag == enable and index < len(labels)
    ]
    # Fall back to the first channel so the select always has a valid option.
    if not options and labels:
        options = [(0, labels[0])]
    return options


def _wrap(root_tag: str, inner: str) -> str:
    return f"<{root_tag}>{inner}</{root_tag}>"


def adjust_payload(
    grammar: dict[str, Any], channel_index: int, bands_db: dict[str, float]
) -> str:
    """Build the set_config XML to write a full band block for one channel."""

    divisor = float(grammar.get("db_divisor", 10)) or 1.0
    parts = [f"<Channel>{channel_index}</Channel>"]
    for tag in band_tags(grammar):
        wire = int(round(float(bands_db.get(tag, 0.0)) * divisor))
        parts.append(f"<{tag}>{wire}</{tag}>")
    return _wrap(grammar["root_tag"], f"<AdjustEQ>{''.join(parts)}</AdjustEQ>")


def speaker_selection_payload(grammar: dict[str, Any], code: str) -> str:
    """Build the set_config XML to set the speaker-selection mode."""

    return _wrap(grammar["root_tag"], f"<SpeakerSelection>{code}</SpeakerSelection>")


def curve_copy_payload(grammar: dict[str, Any]) -> str:
    """Build the set_config XML to copy the reference curve into the manual EQ."""

    return _wrap(grammar["root_tag"], "<CurveCopy>1</CurveCopy>")


def set_defaults_payload(grammar: dict[str, Any]) -> str:
    """Build the set_config XML to reset the manual EQ to its defaults (flat)."""

    return _wrap(grammar["root_tag"], "<SetDefaults>1</SetDefaults>")
