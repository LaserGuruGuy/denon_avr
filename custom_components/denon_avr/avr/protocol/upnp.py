"""Parser for the UPnP/AIOS device description (firmware, serial, module versions)."""

from __future__ import annotations

import xml.etree.ElementTree as ET


# UPnP description local tag name -> the DeviceInfo attribute it fills. The AVR
# firmware and serial go on the device card; the AIOS (HEOS network module)
# version and build revision are captured for diagnostics.
_UPNP_TAGS: dict[str, str] = {
    "manufacturer": "manufacturer",
    "serialNumber": "serial_number",
    "firmware_version": "firmware_version",
    "modelNumber": "network_module_version",
    "firmwareRevision": "firmware_revision",
}


def parse_upnp_description(xml_text: str) -> dict[str, str]:
    """Extract identity/version fields from a UPnP device description.

    The document is namespaced and contains several sub devices, and the Denon
    firmware tag is in a vendor namespace, so match on the local tag name and
    take the first non empty value per field. Returns {} on any parse error.
    """

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
