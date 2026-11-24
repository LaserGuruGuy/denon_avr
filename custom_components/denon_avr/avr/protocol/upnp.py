"""Parser for the UPnP/AIOS device description (firmware, serial, module versions)."""

from __future__ import annotations

import xml.etree.ElementTree as ET


# UPnP description local tag name -> the DeviceInfo attribute it fills.
_UPNP_TAGS: dict[str, str] = {
    "manufacturer": "manufacturer",
    "serialNumber": "serial_number",
    "firmware_version": "firmware_version",
    "modelNumber": "network_module_version",
    "firmwareRevision": "firmware_revision",
}
_VERSION_TAGS = ("capability_version", "moduleType")


def parse_upnp_versions(xml_text: str) -> dict[str, str]:
    """Extract the reported versions of the network module."""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    found: dict[str, str] = {}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        text = (element.text or "").strip()
        if tag in _VERSION_TAGS and text:
            found.setdefault(tag, text)
    return found


def parse_upnp_description(xml_text: str) -> dict[str, str]:
    """Extract identity/version fields from a UPnP device description."""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    result: dict[str, str] = {}
    for element in root.iter():
        attr = _UPNP_TAGS.get(element.tag.rsplit("}", 1)[-1])
        if not attr or attr in result:
            continue
        text = (element.text or "").strip()
        if text:
            result[attr] = text
    return result
