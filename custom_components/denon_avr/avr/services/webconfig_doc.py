"""Setup web interface section controller (``/ajax/<section>/{get,set}_config``).

One instance per section the profile describes (``grammar.video_web``,
``grammar.speakers_web``): settings without a telnet token (HDMI setup/CEC, ARC,
pass through, OSD, 4K and TV format; amp assign). It reads the section's
availability map plus each advertised menu, exposes value / editability / options
per control and writes a single field back, all through the ConfigDocumentPort.
The document grammar is in protocol.webdoc; this module only orchestrates. All
calls are best effort and no-ops when the receiver has no such section.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..protocol.webdoc import (
    GRAYOUT,
    NOT_AVAILABLE,
    SELECTABLE,
    build_set_payload,
    parse_availability,
    parse_control,
    parse_xml,
    resolve_options,
)
from .ports import ConfigDocumentPort


class WebConfigSectionController:
    """One setup web interface section (video, speakers ...): availability, read and write."""

    def __init__(
        self, webconfig: ConfigDocumentPort, grammar: dict[str, Any], on_update: Callable[[], None]
    ) -> None:
        self._webconfig = webconfig
        self._grammar = grammar
        self._on_update = on_update
        self._section = grammar.get("config_section", "video")
        # Availability key -> flag (from the type=1 listSetupMenu document).
        self._avail: dict[str, int] = {}
        # Control id -> {"value": str, "display": int, "options": dict|None}.
        self._fields: dict[str, dict[str, Any]] = {}

    @property
    def supported(self) -> bool:
        return bool(self._grammar)

    def controls(self) -> list[dict[str, Any]]:
        return self._grammar.get("controls", [])

    def _control(self, control_id: str) -> dict[str, Any] | None:
        for control in self.controls():
            if control["id"] == control_id:
                return control
        return None

    # Discovery / availability -------------------------------------------

    def feature_flags(self) -> set[str]:
        """Feature names to inject into discovery for the menus that are present.

        Lets telnet controls gated on a menu (the picture controls, gated on
        ``PictureAdjust``) be created at platform-setup time, reusing the normal
        feature gating instead of a bespoke code path.
        """

        return {
            name
            for key, name in self._grammar.get("features", {}).items()
            if self._avail.get(key, NOT_AVAILABLE) >= GRAYOUT
        }

    async def discover(self) -> None:
        """Read the availability map and the advertised menus (at connect)."""

        if not self.supported:
            return
        # Some sections (e.g. speakers amp-assign) have no listSetupMenu; only
        # read the availability map when the grammar declares one.
        if "availability_type" in self._grammar:
            await self._read_availability()
        await self._read_menus()

    async def refresh(self) -> None:
        """Re-read the advertised menus (non-disruptive), used by the poll."""

        if not self.supported:
            return
        previous = (self._avail, self._fields)
        # Re-read the availability map too: menus appear, gray out or disappear
        # with the user's setup, and the entities must follow that live.
        if "availability_type" in self._grammar:
            await self._read_availability()
        await self._read_menus()
        # This runs on every reconcile poll; only notify when a value actually
        # changed so we do not write HA state each interval for no reason.
        if (self._avail, self._fields) != previous:
            self._on_update()

    async def _read_availability(self) -> None:
        type_id = self._grammar.get("availability_type", 1)
        root = parse_xml(await self._webconfig.async_get(self._section, type_id))
        if root is None:
            return
        self._avail = parse_availability(root)

    async def _read_menus(self) -> None:
        fields: dict[str, dict[str, Any]] = {}
        for type_id, root_tag in self._grammar.get("menus", {}).items():
            # Only fetch a menu the receiver advertises, to avoid the HTTP 500 an
            # absent menu returns. When the section has no availability map
            # (self._avail empty), read every menu unconditionally.
            if self._avail and self._avail.get(root_tag, NOT_AVAILABLE) < GRAYOUT:
                continue
            root = parse_xml(await self._webconfig.async_get(self._section, int(type_id)))
            if root is None:
                continue
            for control in self.controls():
                if str(control["type"]) == str(type_id):
                    parse_control(self._grammar, control, root, fields)
        self._fields = fields

    # Home Assistant facing accessors ------------------------------------

    def present(self, control_id: str) -> bool:
        """True when the control exists on this receiver (not hidden)."""

        field = self._fields.get(control_id)
        return field is not None and field["display"] >= GRAYOUT

    def available(self, control_id: str) -> bool:
        """True when the control is settable now (not grayed out)."""

        field = self._fields.get(control_id)
        return field is not None and field["display"] >= SELECTABLE

    def value(self, control_id: str) -> str | None:
        field = self._fields.get(control_id)
        return field["value"] if field else None

    def options(self, control_id: str) -> dict[str, str]:
        """Wire value -> label map (device-supplied list if any, else profile)."""

        return resolve_options(self._control(control_id) or {}, self._fields.get(control_id))

    # Write --------------------------------------------------------------

    async def async_set(self, control_id: str, wire: str) -> None:
        control = self._control(control_id)
        if not self.supported or control is None:
            return
        payload = build_set_payload(self._grammar, control, wire)
        await self._webconfig.async_set(self._section, int(control["type"]), payload)
        # Re-read so state reflects exactly what the receiver applied.
        await self.refresh()
