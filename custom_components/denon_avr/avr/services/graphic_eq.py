"""Graphic-EQ subsystem: read, per-channel edit and apply through a config-document port."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ..models import Availability, GraphicEqState
from ..protocol.webdoc import parse_availability, parse_xml
from .ports import ConfigDocumentPort
from ..protocol.eq_doc import (
    adjust_payload,
    band_tag,
    curve_copy_payload,
    parse,
    set_defaults_payload,
    speaker_selection_payload,
)


class GraphicEqController:
    """Owns the manual graphic-EQ subsystem: read, per-channel edit and apply."""

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
        # The channel the bands in ``state`` were read for; None = not read (yet).
        self._read_channel: int | None = None
        # grayed while a room correction is active.
        self.availability: Availability | None = None

    @property
    def grammar(self) -> dict[str, Any]:
        return self._grammar

    @property
    def supported(self) -> bool:
        return bool(self._grammar) and self._is_supported()

    @property
    def settable(self) -> bool:
        """True unless the receiver reports the EQ menu as not settable now (unknown availability
        keeps the previous behaviour: settable)."""

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

    async def refresh(self) -> bool:
        """Read the selected channel's curve into state (non-disruptive)."""

        if not self.supported:
            return False
        channel = self._channel
        await self._read_availability()
        param = self._grammar.get("channel_read_param", "opt1")
        xml = await self._webconfig.async_get(
            self._section(), self._type(), {param: str(channel), "opt2": "0"}
        )
        if xml is None:
            return False
        new_state = parse(xml, self._grammar)
        if new_state != self.state:
            self.state = new_state
            self._on_update()
        self._read_channel = channel if new_state.bands else None
        return True

    async def _apply_payload(self, payload: str) -> bool:
        if not self.supported:
            return False
        ok = await self._webconfig.async_set(self._section(), self._type(), payload)
        # The receiver shows a written value only a moment later.
        await asyncio.sleep(float(self._grammar.get("write_settle_seconds", 0)))
        # Re-read so state reflects exactly what the receiver applied.
        await self.refresh()
        return ok

    async def set_band(self, tag: str, db: float) -> None:
        # Stage the edit; apply() writes the whole block for the channel.
        self._pending[tag] = db
        self._on_update()

    async def apply(self) -> bool:
        """Write the whole block."""

        if not self._pending:
            return True
        tags = {band_tag(self._grammar, label) for label in self._grammar.get("bands", [])}
        if self._read_channel != self._channel or not tags <= set(self.state.bands):
            await self.refresh()
            if self._read_channel != self._channel or not tags <= set(self.state.bands):
                return False
        pending = dict(self._pending)
        bands = {**self.state.bands, **pending}
        ok = await self._apply_payload(adjust_payload(self._grammar, self._channel, bands))
        if ok:
            # An edit made meanwhile stays staged.
            self._pending = {t: v for t, v in self._pending.items() if pending.get(t) != v}
            self._on_update()
        return ok

    def _select(self, index: int) -> None:
        # A different channel has its own curve; drop staged edits and the old curve.
        self._channel = index
        self._pending.clear()
        self._read_channel = None
        if self.state.bands:
            self.state = replace(self.state, bands={})
            self._on_update()

    async def set_channel(self, index: int) -> bool:
        self._select(index)
        return await self.refresh()

    async def set_speaker_selection(self, code: str) -> bool:
        # A new mode re-scopes the channel list; start from the first channel.
        self._select(0)
        return await self._apply_payload(speaker_selection_payload(self._grammar, code))

    async def curve_copy(self) -> bool:
        return await self._apply_payload(curve_copy_payload(self._grammar))

    async def set_defaults(self) -> bool:
        return await self._apply_payload(set_defaults_payload(self._grammar))
