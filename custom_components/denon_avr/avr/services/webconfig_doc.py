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

import xml.etree.ElementTree as ET
import asyncio
from collections.abc import Callable
from typing import Any

from ..models import Availability, CollectionItem
from ..protocol.webdoc import (
    NOT_AVAILABLE,
    build_collection_payload,
    build_set_payload,
    field_availability,
    parse_availability,
    parse_collection,
    parse_control,
    parse_value_list,
    parse_xml,
    resolve_options,
)

from .ports import ConfigDocumentPort

_ABSENT = Availability(NOT_AVAILABLE)


class WebConfigSectionController:
    """One setup web interface section (video, speakers ...): availability, read and write."""

    def __init__(
        self, webconfig: ConfigDocumentPort, grammar: dict[str, Any], on_update: Callable[[], None]
    ) -> None:
        self._webconfig = webconfig
        self._grammar = grammar
        self._on_update = on_update
        self._section = grammar.get("config_section", "video")
        # Menu tag -> Availability (from the listSetupMenu document).
        self._avail: dict[str, Availability] = {}
        # Control id -> {"value", "display", "gray", "options", ...}.
        self._fields: dict[str, dict[str, Any]] = {}
        # Collection id -> {slot: CollectionItem} (per-input documents).
        self._collections: dict[str, dict[str, CollectionItem]] = {}
        # Value-list id -> the values the receiver offers (e.g. crossover grid).
        self._value_lists: dict[str, tuple[str, ...]] = {}

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
            if self._avail.get(key, _ABSENT).present
        }

    def availability_map(self) -> dict[str, Availability]:
        """The section's menu availability as last reported (diagnostics)."""

        return dict(self._avail)

    def _menu_present(self, root_tag: str) -> bool:
        # Without an availability map (section has none / not read yet) every
        # menu is read (the 1.1.2 behaviour); otherwise only those reported present.
        return not self._avail or self._avail.get(root_tag, _ABSENT).present

    def _reported_present(self, root_tag: str) -> bool:
        """Strict gate for collections and value lists: only what the receiver
        actually reported as present. A section that declares an availability map
        whose read failed reads none of them (no 500s on receivers without them)."""

        if "availability_type" not in self._grammar:
            return True
        return self._avail.get(root_tag, _ABSENT).present

    async def discover(self) -> None:
        """Read the availability map and the advertised menus (at connect)."""

        if not self.supported:
            return
        # Some sections (e.g. speakers amp-assign) have no listSetupMenu; only
        # read the availability map when the grammar declares one.
        if "availability_type" in self._grammar:
            await self._read_availability()
        await self._read_menus(read_value_lists=True)

    async def refresh(self) -> None:
        """Re-read the advertised menus (non-disruptive), used by the poll."""

        if not self.supported:
            return
        previous = (self._avail, self._fields, self._collections, self._value_lists)
        # Re-read the availability map too: menus appear, gray out or disappear
        # with the user's setup, and the entities must follow that live.
        if "availability_type" in self._grammar:
            await self._read_availability()
        # Value lists are a fixed capability: re-read only when availability changed.
        await self._read_menus(read_value_lists=self._avail != previous[0])
        # This runs on every reconcile poll; only notify when a value actually
        # changed so we do not write HA state each interval for no reason.
        if (self._avail, self._fields, self._collections, self._value_lists) != previous:
            self._on_update()

    async def _read_availability(self) -> None:
        type_id = self._grammar.get("availability_type", 1)
        root = parse_xml(await self._webconfig.async_get(self._section, type_id))
        if root is None:
            return
        self._avail = parse_availability(root)

    async def _read_menus(self, read_value_lists: bool) -> None:
        """One pass over the section: each document is fetched at most once."""

        docs: dict[int, ET.Element | None] = {}

        async def doc(type_id: int) -> ET.Element | None:
            if type_id not in docs:
                docs[type_id] = parse_xml(await self._webconfig.async_get(self._section, type_id))
            return docs[type_id]

        fields: dict[str, dict[str, Any]] = {}
        for type_id, root_tag in self._grammar.get("menus", {}).items():
            # Only fetch a menu the receiver advertises, to avoid the HTTP 500 an
            # absent menu returns.
            if not self._menu_present(root_tag):
                continue
            root = await doc(int(type_id))
            for control in self.controls():
                if str(control["type"]) != str(type_id):
                    continue
                if root is None:
                    # Transient read failure: keep the last known field (silence
                    # is not absence) instead of making the control disappear.
                    if control["id"] in self._fields:
                        fields[control["id"]] = self._fields[control["id"]]
                    continue
                parse_control(self._grammar, control, root, fields)
        self._fields = fields

        collections: dict[str, dict[str, CollectionItem]] = {}
        for spec in self.collection_specs():
            if not self._reported_present(spec["root"]):
                continue        # not applicable (display 1): items go, entities turn unavailable
            root = await doc(int(spec["type"]))
            if root is None:    # transient read failure: keep what we had
                if spec["id"] in self._collections:
                    collections[spec["id"]] = self._collections[spec["id"]]
                continue
            collections[spec["id"]] = parse_collection(spec, root)
        self._collections = collections

        if not read_value_lists:
            return
        value_lists: dict[str, tuple[str, ...]] = {}
        for spec in self.value_list_specs():
            if not self._reported_present(spec.get("menu", spec["root"])):
                continue
            root = await doc(int(spec["type"]))
            values = parse_value_list(spec, root) if root is not None else self._value_lists.get(spec["id"])
            if values:
                value_lists[spec["id"]] = values
        self._value_lists = value_lists

    # Home Assistant facing accessors ------------------------------------

    def present(self, control_id: str) -> bool:
        """True when the control exists on this receiver (not hidden)."""

        field = self._fields.get(control_id)
        return field is not None and field_availability(field).present

    def available(self, control_id: str) -> bool:
        """True when the control is settable now (not grayed out)."""

        field = self._fields.get(control_id)
        return field is not None and field_availability(field).settable

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

    # Per-input collections -----------------------------------------------

    def collection_specs(self) -> list[dict[str, Any]]:
        return self._grammar.get("collections", [])

    def _collection_spec(self, collection_id: str) -> dict[str, Any] | None:
        return next((c for c in self.collection_specs() if c["id"] == collection_id), None)

    def collection(self, collection_id: str) -> dict[str, CollectionItem]:
        """{slot: item} as last read; empty when the receiver does not offer it."""

        return dict(self._collections.get(collection_id, {}))

    def option_key(self, collection_id: str, option: str) -> str:
        """Stable, translatable key of an option id (``option_<id>`` when unknown)."""

        spec = self._collection_spec(collection_id) or {}
        return spec.get("option_keys", {}).get(option, f"option_{option}")

    async def async_set_item(
        self, collection_id: str, slot: str, option: str, *, staged_apply: bool = False
    ) -> bool:
        """Write one item's option. Refused (False, nothing sent) unless the receiver
        reports the item as settable and allows that option for it. Collections
        declared ``writable: "staged"`` (speaker layout) are only written by an
        explicit, confirmed apply (``staged_apply=True``)."""

        spec = self._collection_spec(collection_id)
        item = self._collections.get(collection_id, {}).get(slot)
        writable = spec.get("writable", True) if spec is not None else False
        if spec is None or writable is False or (writable == "staged" and not staged_apply):
            return False
        if item is None or not item.availability.settable or option not in item.options:
            return False
        ok = await self._webconfig.async_set(
            self._section, int(spec["type"]), build_collection_payload(spec, slot, option)
        )
        # Re-read so state reflects exactly what the receiver applied.
        await self.refresh()
        return ok

    # Device value lists ----------------------------------------------------

    def value_list_specs(self) -> list[dict[str, Any]]:
        return self._grammar.get("value_lists", [])

    def value_list(self, list_id: str) -> tuple[str, ...] | None:
        """The values the receiver offers for a setting; None when it does not say
        (the caller then falls back to the profile's protocol constant)."""

        return self._value_lists.get(list_id)


class StagedChanges:
    """Edits to a staged collection (speaker layout), kept locally until applied.

    Nothing reaches the receiver until ``async_apply``; the caller (the UI) asks
    the user to confirm first. Each staged option is checked against what the
    receiver allows for that item when it is staged, and again when applied.
    """

    def __init__(self, section: WebConfigSectionController, collection_id: str) -> None:
        self._section = section
        self._collection = collection_id
        self._pending: dict[str, str] = {}

    @property
    def pending(self) -> dict[str, str]:
        return dict(self._pending)

    def stage(self, slot: str, option: str) -> bool:
        item = self._section.collection(self._collection).get(slot)
        if item is None or not item.availability.settable or option not in item.options:
            return False
        if option == item.value:
            self._pending.pop(slot, None)
        else:
            self._pending[slot] = option
        return True

    def discard(self) -> None:
        self._pending.clear()

    async def async_apply(self) -> dict[str, bool]:
        """Write every staged item, then verify it: the receiver rebuilds the
        layout after a change (its own UI re-reads after about 2 s), so wait, re-read
        and count an item as accepted only when the receiver now reports the value.
        Returns slot -> accepted."""

        pending = dict(self._pending)
        self._pending.clear()
        sent = {}
        for slot, option in pending.items():
            sent[slot] = await self._section.async_set_item(
                self._collection, slot, option, staged_apply=True
            )
        if any(sent.values()):
            spec = self._section._collection_spec(self._collection) or {}
            await asyncio.sleep(float(spec.get("apply_settle_seconds", 2.0)))
            await self._section.refresh()
        items = self._section.collection(self._collection)
        return {
            slot: sent[slot] and items.get(slot) is not None and items[slot].value == option
            for slot, option in pending.items()
        }
