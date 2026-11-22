"""Pure grammar for the setup web interface documents (``/ajax/<section>/{get,set}_config``).

Translates the XML documents into control fields and back into write payloads.
No I/O and no state: the orchestration (which documents to read, when, and the
write-then-reread) lives in services.webconfig_doc. Every id, type, tag and option
map comes from the profile's grammar section (video_web, speakers_web).

Menu ``display`` flags mirror the setup UI: 1 = not available (hidden), 2 = grayed
(present but not settable in the current state), 3 = selectable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

# Per-field display flags used by the setup config API.
NOT_AVAILABLE = 1
GRAYOUT = 2
SELECTABLE = 3


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


def parse_availability(root: ET.Element) -> dict[str, int]:
    """A listSetupMenu document as {menu tag: display flag}."""

    return {child.tag: to_int(child.text, NOT_AVAILABLE) for child in root}


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
            "options": None,
        }
        return
    element = root.find(tag)
    if element is None:
        return
    display = to_int(element.get("display"), SELECTABLE)
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
            "options": None,
            "value_list": allowed,
        }
    else:
        field = {
            "value": (element.text or "").strip(),
            "display": display,
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

    # Most sections send ONLY the field element (the ``type`` selects the
    # menu); wrapping in the menu root then makes the receiver reject the
    # write (HTTP 501). The speakers amp-assign menu is the exception - it
    # requires the field wrapped in its root tag (grammar ``wrap_root``);
    # sending it bare returns HTTP 500. Verified live per section.
    tag = control["tag"]
    if control.get("dynamic"):
        field = control.get("field", "Source")
        payload = f"<{tag}><{field}>{wire}</{field}></{tag}>"
    else:
        payload = f"<{tag}>{wire}</{tag}>"
    root = grammar.get("menus", {}).get(str(control["type"]))
    if grammar.get("wrap_root") and root:
        payload = f"<{root}>{payload}</{root}>"
    return payload
