"""Graphic-EQ subsystem: read, per-channel edit and apply through a config-document port."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import Availability, GraphicEqState
from ..protocol.webdoc import parse_availability, parse_xml
from .ports import ConfigDocumentPort
from ..protocol.eq_doc import (
    adjust_payload,
    curve_copy_payload,
    parse,
    set_defaults_payload,
    speaker_selection_payload,
)


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
        webconfig: ConfigDocumentPort,
        grammar: dict[str, Any],
        supported: Callable[[], bool],
        on_update: Callable[[], None],
    ) -> None:
        self._webconfig = webconfig
        self._grammar = grammar
        self._is_supported = supported
        self._on_update = on_update
        self.state = GraphicEqState()
        self._pending: dict[str, float] = {}
        self._channel = 0
        # The receiver's own availability of the graphic EQ menu (audio
        # listSetupMenu), e.g. grayed while a room correction is active.
        self.availability: Availability | None = None

    @property
    def grammar(self) -> dict[str, Any]:
        return self._grammar

    @property
    def supported(self) -> bool:
        return bool(self._grammar) and self._is_supported()

    @property
    def settable(self) -> bool:
        """True unless the receiver reports the EQ menu as not settable now
        (unknown availability keeps the previous behaviour: settable)."""

        return self.availability is None or self.availability.settable

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

    async def _read_availability(self) -> None:
        spec = self._grammar.get("availability")
        if not spec:
            return
        root = parse_xml(await self._webconfig.async_get(self._section(), int(spec["type"])))
        if root is None:
            return              # silence: keep the last known availability
        availability = parse_availability(root).get(spec["menu"])
        if availability != self.availability:
            self.availability = availability
            self._on_update()

    async def refresh(self) -> None:
        """Read the selected channel's curve into state (non-disruptive)."""

        if not self.supported:
            return
        await self._read_availability()
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
