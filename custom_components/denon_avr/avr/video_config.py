"""Video setup config controller for the setup config API (see transport.webconfig).

The receiver's Video setup menus - HDMI setup (HDMI Control/CEC, ARC, pass
through, power off/saving, smart menu), on screen display, 4K signal format and
TV format - have no telnet command token, so they are read and written through
the same HTTPS config API the graphic EQ uses (``/ajax/video/{get,set}_config``).
This is the telnet-less fallback, used only because these items are genuinely not
reachable over telnet.

The controller is pure orchestration around :class:`WebConfigClient`: it reads
the availability map (``type=1``) plus each advertised menu, exposes the current
value / editability / options of every control the profile lists, and writes a
single field back. It holds no Home Assistant dependency and no protocol
constants of its own - every id, config type, XML tag and option map comes from
the profile's ``grammar.video_web`` block. All calls are best effort and no-ops
when the receiver has no video config API.

Menu ``display`` flags mirror the setup UI: 1 = not available (hidden), 2 = grayed
(present but not settable in the current state, e.g. ARC until HDMI Control is on),
3 = selectable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

# Per-field display flags used by the setup config API.
_NOT_AVAILABLE = 1
_GRAYOUT = 2
_SELECTABLE = 3


def _parse(xml_text: str | None) -> ET.Element | None:
    if not xml_text:
        return None
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def _int(text: str | None, default: int = 0) -> int:
    try:
        return int((text or "").strip())
    except (TypeError, ValueError):
        return default


class VideoConfigController:
    """Owns the /ajax video setup subsystem: availability, read and write."""

    def __init__(self, webconfig, grammar: dict[str, Any], on_update) -> None:
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

    def _menu_root_tag(self, type_id: object) -> str | None:
        return self._grammar.get("menus", {}).get(str(type_id))

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
            if self._avail.get(key, _NOT_AVAILABLE) >= _GRAYOUT
        }

    async def discover(self) -> None:
        """Read the availability map and the advertised menus (at connect)."""

        if not self.supported:
            return
        await self._read_availability()
        await self._read_menus()

    async def refresh(self) -> None:
        """Re-read the advertised menus (non-disruptive), used by the poll."""

        if not self.supported:
            return
        await self._read_menus()
        self._on_update()

    async def _read_availability(self) -> None:
        type_id = self._grammar.get("availability_type", 1)
        root = _parse(await self._webconfig.async_get(self._section, type_id))
        if root is None:
            return
        self._avail = {child.tag: _int(child.text, _NOT_AVAILABLE) for child in root}

    async def _read_menus(self) -> None:
        fields: dict[str, dict[str, Any]] = {}
        for type_id, root_tag in self._grammar.get("menus", {}).items():
            # Only fetch a menu the receiver advertises, to avoid the HTTP 500 an
            # absent menu returns.
            if self._avail.get(root_tag, _NOT_AVAILABLE) < _GRAYOUT:
                continue
            root = _parse(await self._webconfig.async_get(self._section, int(type_id)))
            if root is None:
                continue
            for control in self.controls():
                if str(control["type"]) == str(type_id):
                    self._parse_control(control, root, fields)
        self._fields = fields

    def _parse_control(
        self, control: dict[str, Any], root: ET.Element, fields: dict[str, Any]
    ) -> None:
        tag = control["tag"]
        if tag == root.tag:
            # Single-value menu (e.g. TVFormat): the value is the root's text and
            # the menu-level availability is its only gate, so treat as settable.
            fields[control["id"]] = {
                "value": (root.text or "").strip(),
                "display": _int(root.get("display"), _SELECTABLE),
                "options": None,
            }
            return
        element = root.find(tag)
        if element is None:
            return
        display = _int(element.get("display"), _SELECTABLE)
        if control.get("dynamic"):
            # A device-supplied source list (Pass Through Source): the selected
            # value is in <field>, the offered items are the <list> children keyed
            # by a source tag whose wire value comes from the profile map.
            value = (element.findtext(control.get("field", "Source")) or "").strip()
            options: dict[str, str] = {}
            wire_map = self._grammar.get("pass_through_values", {})
            container = element.find(control.get("list", "List"))
            if container is not None:
                for item in container:
                    wire = wire_map.get(item.tag)
                    if wire:
                        options[wire] = (item.text or item.tag).strip()
            fields[control["id"]] = {
                "value": value,
                "display": display,
                "options": options,
            }
        else:
            fields[control["id"]] = {
                "value": (element.text or "").strip(),
                "display": display,
                "options": None,
            }

    # Home Assistant facing accessors ------------------------------------

    def present(self, control_id: str) -> bool:
        """True when the control exists on this receiver (not hidden)."""

        field = self._fields.get(control_id)
        return field is not None and field["display"] >= _GRAYOUT

    def available(self, control_id: str) -> bool:
        """True when the control is settable now (not grayed out)."""

        field = self._fields.get(control_id)
        return field is not None and field["display"] >= _SELECTABLE

    def value(self, control_id: str) -> str | None:
        field = self._fields.get(control_id)
        return field["value"] if field else None

    def options(self, control_id: str) -> dict[str, str]:
        """Wire value -> label map (device-supplied list if any, else profile)."""

        field = self._fields.get(control_id)
        if field and field.get("options"):
            return field["options"]
        control = self._control(control_id) or {}
        return control.get("options", {})

    # Write --------------------------------------------------------------

    async def async_set(self, control_id: str, wire: str) -> None:
        control = self._control(control_id)
        if not self.supported or control is None:
            return
        root_tag = self._menu_root_tag(control["type"])
        tag = control["tag"]
        if tag == root_tag:
            payload = f"<{root_tag}>{wire}</{root_tag}>"
        elif control.get("dynamic"):
            field = control.get("field", "Source")
            payload = f"<{root_tag}><{tag}><{field}>{wire}</{field}></{tag}></{root_tag}>"
        else:
            payload = f"<{root_tag}><{tag}>{wire}</{tag}></{root_tag}>"
        await self._webconfig.async_set(self._section, int(control["type"]), payload)
        # Re-read so state reflects exactly what the receiver applied.
        await self.refresh()
