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

    # The enable flags live under a mode-specific sub-element: Each (individual)
    # or LR (pairs); All mode has none (a single grayed curve).
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
    """Return the adjustable channels as (index, label) for the current mode.

    In "All" mode there is a single curve. In "Left / Right" and "Each" modes the
    index is the ``opt1`` value used to read/write that channel; the label comes
    from the matching fixed map, and only channels flagged enabled in the
    ``selectable`` string are offered.
    """

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


def speaker_selection_payload(grammar: dict[str, Any], code: str) -> str:
    """Build the set_config XML to set the speaker-selection mode."""

    return _wrap(grammar["root_tag"], f"<SpeakerSelection>{code}</SpeakerSelection>")


def curve_copy_payload(grammar: dict[str, Any]) -> str:
    """Build the set_config XML to copy the reference curve into the manual EQ."""

    return _wrap(grammar["root_tag"], "<CurveCopy>1</CurveCopy>")


def set_defaults_payload(grammar: dict[str, Any]) -> str:
    """Build the set_config XML to reset the manual EQ to its defaults (flat)."""

    return _wrap(grammar["root_tag"], "<SetDefaults>1</SetDefaults>")


class GraphicEqController:
    """Owns the manual graphic-EQ subsystem: read, per-channel edit and apply.

    Keeps the EQ cohesive and off the device's transport-orchestration surface.
    It talks only to the setup config transport (get/set) and reports changes
    through an ``on_update`` callback; band edits are staged locally and written
    as one full block by ``apply`` (the receiver rejects a partial block). All
    calls are best effort and no-ops when the receiver has no graphic EQ.
    """

    def __init__(
        self,
        webconfig,
        grammar: dict[str, Any],
        supported,
        on_update,
    ) -> None:
        self._webconfig = webconfig
        self._grammar = grammar
        self._is_supported = supported
        self._on_update = on_update
        self.state = GraphicEqState()
        self._pending: dict[str, float] = {}
        self._channel = 0

    @property
    def grammar(self) -> dict[str, Any]:
        return self._grammar

    @property
    def supported(self) -> bool:
        return bool(self._grammar) and self._is_supported()

    @property
    def channel(self) -> int:
        return self._channel

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def band_value(self, tag: str) -> float | None:
        """The band gain to show: a staged edit if any, else the read value."""

        if tag in self._pending:
            return self._pending[tag]
        return self.state.bands.get(tag)

    def _section(self) -> str:
        return self._grammar.get("config_section", "audio")

    def _type(self) -> int:
        return int(self._grammar.get("config_type", 0))

    async def refresh(self) -> None:
        """Read the selected channel's curve into state (non-disruptive)."""

        if not self.supported:
            return
        param = self._grammar.get("channel_read_param", "opt1")
        xml = await self._webconfig.async_get(
            self._section(), self._type(), {param: str(self._channel), "opt2": "0"}
        )
        if xml is None:
            return
        new_state = parse(xml, self._grammar)
        # Only notify on an actual change: this runs on every reconcile poll, so
        # an unconditional callback would write HA state every interval for no
        # reason (GraphicEqState is a dataclass, so equality is a value compare).
        if new_state != self.state:
            self.state = new_state
            self._on_update()

    async def _apply_payload(self, payload: str) -> None:
        if not self.supported:
            return
        await self._webconfig.async_set(self._section(), self._type(), payload)
        # Re-read so state reflects exactly what the receiver applied.
        await self.refresh()

    async def set_band(self, tag: str, db: float) -> None:
        # Stage the edit; apply() writes the whole block for the channel.
        self._pending[tag] = db
        self._on_update()

    async def apply(self) -> None:
        # Never write a full block from nothing: without a prior read every band
        # would default to 0 and silently flatten the curve.
        if not self.state.bands and not self._pending:
            return
        bands = dict(self.state.bands)
        bands.update(self._pending)
        await self._apply_payload(adjust_payload(self._grammar, self._channel, bands))
        self._pending.clear()

    async def set_channel(self, index: int) -> None:
        # A different channel has its own curve; drop staged edits and reload.
        self._channel = index
        self._pending.clear()
        await self.refresh()

    async def set_speaker_selection(self, code: str) -> None:
        # A new mode re-scopes the channel list; start from the first channel.
        self._channel = 0
        self._pending.clear()
        await self._apply_payload(speaker_selection_payload(self._grammar, code))

    async def curve_copy(self) -> None:
        await self._apply_payload(curve_copy_payload(self._grammar))

    async def set_defaults(self) -> None:
        await self._apply_payload(set_defaults_payload(self._grammar))
