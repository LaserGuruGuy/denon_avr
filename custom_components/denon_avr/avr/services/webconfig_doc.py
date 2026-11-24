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
from collections.abc import Awaitable, Callable
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

    async def async_set(self, control_id: str, wire: str) -> bool:
        """Write one setting. False when it is unknown, or not accepted and the
        receiver does not show it afterwards either (an answer can get lost while
        the write was applied)."""

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
        """The receiver shows a written value only a moment after it accepted the
        write (0.3 s measured), so wait before reading it back."""

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

    def option_key(self, collection_id: str, option: str) -> str:
        """Stable, translatable key of an option id (``option_<id>`` when unknown)."""

        spec = self.collection_spec(collection_id) or {}
        return spec.get("option_keys", {}).get(option, f"option_{option}")

    async def async_set_item(
        self, collection_id: str, slot: str, option: str, *, staged_apply: bool = False
    ) -> bool:
        """Write one item's option. Refused (False, nothing sent) unless the receiver
        reports the item as settable and allows that option for it. Collections
        declared ``writable: "staged"`` (speaker layout) are only written by an
        explicit, confirmed apply (``staged_apply=True``)."""

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
        if not staged_apply:   # a staged apply waits and verifies itself
            await self._settle()
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

    def __init__(
        self,
        section: WebConfigSectionController,
        collection_id: str,
        get_control: Callable[[str], Any] = lambda _cid: None,
        set_control: Callable[[str, Any], Awaitable[None]] | None = None,
    ) -> None:
        self._section = section
        self._collection = collection_id
        # Telnet controls an apply also resets (profile ``restore_controls``).
        self._get_control = get_control
        self._set_control = set_control
        self._pending: dict[str, str] = {}
        # Positions that differ from before the first apply: slot -> (before, after).
        self.side_effects: dict[str, tuple[str | None, str | None]] = {}
        self._baseline: dict[str, str | None] = {}
        self._baseline_items: dict[str, str | None] = {}   # the staged items themselves
        self._baseline_controls: dict[str, Any] = {}

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

    def _positions(self, spec: dict[str, Any]) -> dict[str, str | None]:
        """Value per related position (the choices an assign-mode change resets)."""

        related = spec.get("side_effects_of")
        return {k: i.value for k, i in self._section.collection(related).items()} if related else {}

    def _shown(self, pending: dict[str, str]) -> bool:
        items = self._section.collection(self._collection)
        return all(slot in items and items[slot].value == option for slot, option in pending.items())

    def _track_side_effects(self, spec: dict[str, Any]) -> None:
        after = self._positions(spec)
        if not after:
            return   # the collection could not be read: nothing can be said
        base = self._baseline
        self.side_effects = {
            slot: (base.get(slot), after.get(slot))
            for slot in sorted(set(base) | set(after), key=lambda x: (len(x), x))
            if base.get(slot) != after.get(slot)
        }

    def discard(self) -> None:
        self._pending.clear()
        self._forget()

    def _forget(self) -> None:
        self._baseline = {}
        self._baseline_items = {}
        self._baseline_controls = {}
        self.side_effects = {}

    @property
    def restorable(self) -> bool:
        """An apply was done and what the receiver had before it is known."""

        return bool(self._baseline)

    async def async_restore(self) -> list[str]:
        """Put everything back as it was before the first apply.

        The receiver does not do this itself: going back to an assign mode leaves
        the other positions on their defaults, and one position can bring others
        back. So the mode first, then position by position, re-reading after each
        pass, until nothing differs or nothing changes any more. Only values the
        receiver currently allows are written. Returns what could not be restored.
        """

        if not self._baseline:
            return []
        spec = self._section.collection_spec(self._collection) or {}
        self._pending.clear()
        related = spec.get("side_effects_of")
        await self._section.refresh()
        for collection, wanted in ((self._collection, self._baseline_items), (related, self._baseline)):
            for _ in range(int(spec.get("restore_passes", 3))):
                todo = self._restore_todo(collection, wanted)
                if not todo:
                    break
                before = self._values(collection)
                for slot in todo:
                    if slot not in self._restore_todo(collection, wanted):
                        continue   # an earlier write in this pass brought it back
                    await self._section.async_set_item(collection, slot, wanted[slot], staged_apply=True)
                    await asyncio.sleep(float(spec.get("apply_settle_seconds", 2.0)))
                    await self._section.refresh()
                if self._values(collection) == before:
                    break   # no progress
        left = [
            f"{slot}" for slot, value in self._baseline.items() if self._values(related).get(slot) != value
        ]
        items = self._values(self._collection)
        left += [slot for slot, value in self._baseline_items.items() if items.get(slot) != value]
        if not left and self._set_control is not None:
            for control_id, value in self._baseline_controls.items():
                if value is not None and self._get_control(control_id) != value:
                    await self._set_control(control_id, value)
        if left:
            self._track_side_effects(spec)
        else:
            self._forget()
        return left

    def _values(self, collection: str) -> dict[str, str | None]:
        return {k: i.value for k, i in self._section.collection(collection).items()}

    def _restore_todo(self, collection: str, wanted: dict[str, str | None]) -> list[str]:
        """Slots that differ and that the receiver lets us set back right now."""

        items = self._section.collection(collection)
        return [
            slot
            for slot in sorted(wanted, key=lambda x: (len(x), x))
            if slot in items
            and wanted[slot] is not None
            and items[slot].value != wanted[slot]
            and items[slot].availability.settable
            and wanted[slot] in items[slot].options
        ]

    async def async_apply(self) -> dict[str, bool]:
        """Write every staged item, then verify it: the receiver rebuilds the
        layout after a change (its own UI re-reads after about 2 s), so wait, re-read
        and count an item as accepted only when the receiver now reports the value.
        Returns slot -> accepted."""

        pending = dict(self._pending)
        self._pending.clear()
        spec = self._section.collection_spec(self._collection) or {}
        # The baseline of the side effects is what the receiver had before the FIRST
        # apply; it stays until the positions are back, or ``discard``. Read it now:
        # the cached poll can be a minute old.
        if pending and not self._baseline:
            await self._section.refresh()
            self._baseline = self._positions(spec)
            self._baseline_items = {k: v for k, v in self._values(self._collection).items() if k in pending}
            self._baseline_controls = {c: self._get_control(c) for c in spec.get("restore_controls", ())}
        sent = {}
        for slot, option in pending.items():
            sent[slot] = await self._section.async_set_item(
                self._collection, slot, option, staged_apply=True
            )
        if any(sent.values()):
            for _ in range(int(spec.get("apply_attempts", 1))):
                await asyncio.sleep(float(spec.get("apply_settle_seconds", 2.0)))
                await self._section.refresh()
                if self._shown(pending):
                    break
            self._track_side_effects(spec)
        items = self._section.collection(self._collection)
        return {
            slot: sent[slot] and items.get(slot) is not None and items[slot].value == option
            for slot, option in pending.items()
        }
