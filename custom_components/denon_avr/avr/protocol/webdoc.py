"""Pure grammar for the setup web interface documents (``/ajax/<section>/{get,set}_config``).

Translates the XML documents into control fields and back into write payloads.
No I/O and no state: the orchestration (which documents to read, when, and the
write-then-reread) lives in services.webconfig_doc. Every id, type, tag and option
map comes from the profile's grammar section (video_web, speakers_web).

Availability semantics live in ONE place, :class:`~..models.Availability`, as the
receiver's own UI reads them: ``display`` 2/3/4 shown, 3/4 active, 2 disabled;
``gray`` is only the reason text code of a disabled item. Per-input collections (one element
per input slot, keyed by the receiver's own index) are described generically by
the profile's ``collections`` entries.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape

from ..models import (
    DISPLAY_GRAYED,
    DISPLAY_NOT_APPLICABLE,
    DISPLAY_SELECTABLE,
    Availability,
    CollectionItem,
)

# Kept names for the display flags (see models.Availability).
NOT_AVAILABLE = DISPLAY_NOT_APPLICABLE
GRAYOUT = DISPLAY_GRAYED
SELECTABLE = DISPLAY_SELECTABLE


def parse_xml(xml_text: str | None) -> ET.Element | None:
    if not xml_text:
        return None
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def to_int(text: str | None, default: int = 0) -> int:
    try:
        return int((text or "").strip())
    except (TypeError, ValueError):
        return default


def parse_availability(root: ET.Element) -> dict[str, Availability]:
    """A listSetupMenu document as {menu tag: Availability} (display = element text)."""

    return {
        child.tag: Availability(to_int(child.text, NOT_AVAILABLE), child.get("gray", "0"))
        for child in root
    }


def field_availability(field: dict[str, Any]) -> Availability:
    """The Availability of a parsed control field."""

    return Availability(field["display"], field.get("gray", "0"))


def parse_control(
    grammar: dict[str, Any], control: dict[str, Any], root: ET.Element, fields: dict[str, Any]
) -> None:
    """Parse one profile control from its menu document into ``fields`` (pure)."""
    tag = control["tag"]
    if tag == root.tag:
        # Single-value menu (e.g. TVFormat): the value is the root's text and
        # the menu-level availability is its only gate, so treat as settable.
        fields[control["id"]] = {
            "value": (root.text or "").strip(),
            "display": to_int(root.get("display"), SELECTABLE),
            "gray": root.get("gray", "0"),
            "options": None,
        }
        return
    element = root.find(tag)
    if element is None:
        return
    display = to_int(element.get("display"), SELECTABLE)
    gray = element.get("gray", "0")
    if control.get("dynamic"):
        # A device-supplied source list (Pass Through Source): the selected
        # value is in <field>, the offered items are the <list> children keyed
        # by a source tag whose wire value comes from the profile map.
        value = (element.findtext(control.get("field", "Source")) or "").strip()
        options: dict[str, str] = {}
        wire_map = grammar.get("pass_through_values", {})
        container = element.find(control.get("list", "List"))
        if container is not None:
            for item in container:
                wire = wire_map.get(item.tag)
                if wire:
                    options[wire] = (item.text or item.tag).strip()
        fields[control["id"]] = {
            "value": value,
            "display": display,
            "gray": gray,
            "options": options,
        }
    elif control.get("value_list"):
        # A nested field with the current <Value> plus a device <List> of the
        # allowed <Value> ids (amp-assign Floor Layout / Height); labels for
        # those ids come from the control's static options map.
        value = (element.findtext("Value") or "").strip()
        container = element.find("List")
        allowed = (
            [(v.text or "").strip() for v in container.findall("Value")]
            if container is not None
            else []
        )
        fields[control["id"]] = {
            "value": value,
            "display": display,
            "gray": gray,
            "options": None,
            "value_list": allowed,
        }
    else:
        field = {
            "value": (element.text or "").strip(),
            "display": display,
            "gray": gray,
            "options": None,
        }
        # Amp assign: the valid option list depends on the receiver's amp
        # hardware, so capture the sibling AmpType value; options() keys the
        # per-AmpType table on it.
        amptype_tag = control.get("amptype_tag")
        if amptype_tag:
            field["amptype"] = (root.findtext(amptype_tag) or "").strip()
        fields[control["id"]] = field


def resolve_options(control: dict[str, Any], field: dict[str, Any] | None) -> dict[str, str]:
    """Wire value -> label map (device-supplied list if any, else profile)."""

    if field and field.get("options"):
        return field["options"]
    if field is not None and field.get("value_list") is not None:
        # Options are the device-allowed ids, labelled from the static map.
        label_map = control.get("options", {})
        return {v: label_map.get(v, v) for v in field["value_list"]}
    amptype_options = control.get("amptype_options")
    if amptype_options is not None and field is not None:
        # Device-driven: pick the mode list for the receiver's queried
        # AmpType, deduping labels (a few amp types map two ids to one label).
        result: dict[str, str] = {}
        seen: set[str] = set()
        for value, label in amptype_options.get(field.get("amptype", ""), []):
            if label not in seen:
                seen.add(label)
                result[value] = label
        return result
    return control.get("options", {})


def build_set_payload(grammar: dict[str, Any], control: dict[str, Any], wire: str) -> str:
    """The XML payload that writes ``wire`` into one control's field."""

    # Most menus take ONLY the field element (the ``type`` selects the menu):
    # HDMI setup 3, 4K format 8, TV format 9 and the per-input 11/14 are sent bare
    # by the vendor's own web code (both generations), and wrapping makes the
    # receiver reject them. Exceptions, wrapped in the root tag: the speakers
    # amp-assign menu (``wrap_root``; bare = HTTP 500) and the menus in
    # ``wrap_menus`` (On Screen Display; bare = HTTP 501, verified live).
    tag = control["tag"]
    if control.get("dynamic"):
        field = control.get("field", "Source")
        payload = f"<{tag}><{field}>{wire}</{field}></{tag}>"
    else:
        payload = f"<{tag}>{wire}</{tag}>"
    root = grammar.get("menus", {}).get(str(control["type"]))
    wrap = grammar.get("wrap_root") or str(control["type"]) in grammar.get("wrap_menus", ())
    if wrap and root:
        payload = f"<{root}>{payload}</{root}>"
    return payload


# Per-input collections ------------------------------------------------------


def _allowed_options(spec: dict[str, Any], item: ET.Element) -> tuple[str, ...]:
    """The option ids this item allows.

    ``options_from``: the item's own device-supplied list (e.g. ``List/Value``).
    Otherwise the profile's base list, plus conditional ones whose condition (a
    sibling field equal to a value) the receiver reports for this item.
    """

    if "options_from" in spec:
        return tuple(
            (v.text or "").strip() for v in item.findall(spec["options_from"]) if (v.text or "").strip()
        )
    allowed = []
    conditional = spec.get("conditional_options", {})
    for option in spec.get("options", []):
        rule = conditional.get(option)
        if rule and (item.findtext(rule["field"]) or "").strip() != rule["equals"]:
            continue
        allowed.append(option)
    return tuple(allowed)


def parse_collection(spec: dict[str, Any], root: ET.Element) -> dict[str, CollectionItem]:
    """A per-input document as {slot: CollectionItem}, in the receiver's order."""

    if root.tag != spec["root"]:
        return {}
    key_attr = spec.get("key_attr", "index")
    items: dict[str, CollectionItem] = {}
    for element in root.findall(spec.get("item", "Source")):
        slot = (element.get(key_attr) or "").strip()
        value = element.find(spec["value_field"])
        if not slot or value is None:
            continue     # items without this field belong to another collection / are absent
        # Availability: the item's own flags, or those of a sub-element that carries
        # the setting (``availability_from``); the stricter of the two applies.
        own = Availability(to_int(element.get("display"), SELECTABLE), element.get("gray", "0"))
        sub = element.find(spec["availability_from"]) if "availability_from" in spec else None
        if sub is not None:
            inner = Availability(to_int(sub.get("display"), SELECTABLE), sub.get("gray", "0"))
            # The stricter one applies: not shown beats disabled beats active.
            own = min((own, inner), key=lambda a: (a.present, a.settable))
        items[slot] = CollectionItem(
            slot=slot,
            name=(element.findtext(spec.get("name_field", "Name")) or "").strip(),
            value=(value.text or "").strip(),
            options=_allowed_options(spec, element),
            availability=own,
        )
    return items


def build_collection_payload(spec: dict[str, Any], slot: str, wire: str) -> str:
    """Write payload for one item: ``<ValueField index="slot">wire</ValueField>``.

    The shape the receiver's own setup UI sends (no root element; the config
    ``type`` selects the document). Values and the key are XML-escaped.
    """

    tag = spec.get("write_field", spec["value_field"])
    key_attr = spec.get("key_attr", "index")
    attr = escape(slot, {'"': "&quot;"})
    return f'<{tag} {key_attr}="{attr}">{escape(wire)}</{tag}>'


# Device-supplied value lists -------------------------------------------------


def parse_value_list(spec: dict[str, Any], root: ET.Element) -> tuple[str, ...] | None:
    """The values the receiver offers for a setting (e.g. its crossover grid), in its
    order; None when the document does not carry the list."""

    if root.tag != spec["root"]:
        return None
    container = root.find(spec.get("path", "SelectableValue/List"))
    if container is None:
        return None
    return tuple((item.text or "").strip() for item in container if (item.text or "").strip())
