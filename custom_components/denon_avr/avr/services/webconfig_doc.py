"""Setup web interface section controller (``/ajax/<section>/{get,set}_config``)."""

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
        """Feature names to inject into discovery for the menus that are present."""

        return {
            name
            for key, name in self._grammar.get("features", {}).items()
            if self._avail.get(key, _ABSENT).present
        }

    def availability_map(self) -> dict[str, Availability]:
        """The section's menu availability as last reported (diagnostics)."""

        return dict(self._avail)

    def _menu_present(self, root_tag: str) -> bool:
        # Without an availability map (section has none / not read yet) every menu is read (the
        # 1.1.2 behaviour); otherwise only those reported present.
        return not self._avail or self._avail.get(root_tag, _ABSENT).present

    def _reported_present(self, root_tag: str) -> bool:
        """Strict gate for collections and value lists: only what the receiver actually reported
        as present."""

        if "availability_type" not in self._grammar:
            return True
        return self._avail.get(root_tag, _ABSENT).present

    async def discover(self) -> None:
        """Read the availability map and the advertised menus (at connect)."""

        if not self.supported:
            return
        # Some sections (e.g. speakers amp-assign) have no listSetupMenu; only read the
        # availability map when the grammar declares one.
        if "availability_type" in self._grammar:
            await self._read_availability()
        await self._read_menus(read_value_lists=True)

    async def refresh(self) -> None:
        """Re-read the advertised menus (non-disruptive), used by the poll."""

        if not self.supported:
            return
        previous = (self._avail, self._fields, self._collections, self._value_lists)
        # Re-read the availability map too: menus appear, gray out or disappear with the user's
        # setup, and the entities must follow that live.
        if "availability_type" in self._grammar:
            await self._read_availability()
        # Value lists are a fixed capability: re-read only when availability changed.
        await self._read_menus(read_value_lists=self._avail != previous[0])
        # This runs on every reconcile poll; only notify when a value actually changed so we do
        # not write HA state each interval for no reason.
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
            # Only fetch a menu the receiver advertises, to avoid the HTTP 500 an absent menu
            # returns.
            if not self._menu_present(root_tag):
                continue
            root = await doc(int(type_id))
            for control in self.controls():
                if str(control["type"]) != str(type_id):
                    continue
                if root is None:
                    # Transient read failure: keep the last known field (silence is not absence)
                    # instead of making the control disappear.
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

    async def async_set(self, control_id: str, wire: str) -> bool:
        """Write one setting."""

        control = self._control(control_id)
        if not self.supported or control is None:
            return False
        payload = build_set_payload(self._grammar, control, wire)
        ok = await self._webconfig.async_set(self._section, int(control["type"]), payload)
        await self._settle()
        # Re-read so state reflects exactly what the receiver applied.
        await self.refresh()
        return ok or self.value(control_id) == wire

    async def _settle(self) -> None:
        """The receiver shows a written value only a moment after it accepted the write (0.3 s
        measured), so wait before reading it back."""

        delay = float(self._grammar.get("write_settle_seconds", 0))
        if delay:
            await asyncio.sleep(delay)

    # Per-input collections -----------------------------------------------

    def collection_specs(self) -> list[dict[str, Any]]:
        return self._grammar.get("collections", [])

    def collection_spec(self, collection_id: str) -> dict[str, Any] | None:
        return next((c for c in self.collection_specs() if c["id"] == collection_id), None)

    def collection(self, collection_id: str) -> dict[str, CollectionItem]:
        """{slot: item} as last read; empty when the receiver does not offer it."""

        return dict(self._collections.get(collection_id, {}))

    async def async_read_collections(self, type_id: int) -> bool:
        """Read the collections of one document now. False when it was not given."""

        root = parse_xml(await self._webconfig.async_get(self._section, type_id))
        if root is None:
            return False
        previous = self._collections
        read = {
            spec["id"]: parse_collection(spec, root)
            for spec in self.collection_specs()
            if int(spec["type"]) == type_id
        }
        self._collections = {**previous, **read}
        if self._collections != previous:
            self._on_update()
        return True

    def option_key(self, collection_id: str, option: str) -> str:
        """Stable, translatable key of an option id (``option_<id>`` when unknown)."""

        spec = self.collection_spec(collection_id) or {}
        return spec.get("option_keys", {}).get(option, f"option_{option}")

    async def async_set_item(
        self, collection_id: str, slot: str, option: str, *, staged_apply: bool = False
    ) -> bool:
        """Write one item's option."""

        spec = self.collection_spec(collection_id)
        item = self._collections.get(collection_id, {}).get(slot)
        writable = spec.get("writable", True) if spec is not None else False
        if spec is None or writable is False or (writable == "staged" and not staged_apply):
            return False
        if item is None or not item.availability.settable or option not in item.options:
            return False
        ok = await self._webconfig.async_set(
            self._section, int(spec["type"]), build_collection_payload(spec, slot, option)
        )
        if staged_apply:
            return ok   # it waits for, reads and verifies the result itself
        await self._settle()
        # Re-read so state reflects exactly what the receiver applied.
        await self.refresh()
        return ok

    # Device value lists ----------------------------------------------------

    def value_list_specs(self) -> list[dict[str, Any]]:
        return self._grammar.get("value_lists", [])

    def value_list(self, list_id: str) -> tuple[str, ...] | None:
        """The values the receiver offers for a setting."""

        return self._value_lists.get(list_id)
