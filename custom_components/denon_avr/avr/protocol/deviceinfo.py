"""Parser for the goform Deviceinfo document.

parse_device_info turns the HTTP Deviceinfo XML into a Discovery object with the
device identity, the advertised features, the channel list (codes plus display
names), the option labels for enum controls and the numeric metadata for level
controls. All of this is device published.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from ..models import (
    ChannelDescriptor,
    Discovery,
    SourceDescriptor,
    ZoneDescriptor,
)

_LOGGER = logging.getLogger(__name__)


def _format_mac(raw: str | None) -> str | None:
    """Format a bare MAC such as '0005CD000001' as '00:05:cd:00:00:01'."""

    if not raw:
        return None
    raw = re.sub(r"[:\-.\s]", "", raw)
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", raw) or int(raw, 16) == 0:
        return None
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2)).lower()


def parse_device_info(
    xml_text: str,
    feature_names: set[str] | None = None,
    enum_features: set[str] | None = None,
    receiver_type: dict[str, str] | None = None,
) -> Discovery:
    """Parse the Deviceinfo XML into a Discovery object.

    `feature_names` is the set of receiver feature names (from the profile) whose
    display title and, for those also in `enum_features`, option labels should be
    read. Passing them keeps the wire grammar in the profile while the titles,
    labels, ranges and everything else come from the document.

    The parser is deliberately tolerant: any missing node simply leaves the
    corresponding piece of information unset. Nothing here is hardcoded per
    model; every name, range and option comes from the document.
    """

    discovery = Discovery()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        _LOGGER.warning("Could not parse Deviceinfo XML: %s", err)
        return discovery

    device = discovery.device
    # Some models report a region-variant marker '*' in the name; strip it, as
    # the official integration does, so the model reads cleanly.
    device.model_name = (root.findtext("ModelName") or "").strip().replace("*", "") or None
    device.mac_address = _format_mac(root.findtext("MacAddress"))
    # Classify the receiver type (the device's hardware version) the way the
    # official library does: an AVR-X family device reached over the 8080
    # interface is "avr-x-2016", otherwise "avr". A device is AVR-X when its
    # CommApiVers OR its ModelName matches the library's patterns. This is read
    # from the equipment and works for any model (no per-model table).
    if receiver_type:
        commapi = (root.findtext("CommApiVers") or "").strip()
        model = device.model_name or ""
        is_avr_x = bool(
            (commapi and re.search(receiver_type.get("commapi_pattern", ""), commapi))
            or (model and re.search(receiver_type.get("avr_x_pattern", ""), model))
        )
        device.hardware_type = receiver_type.get(
            "avr_x_type" if is_avr_x else "default_type"
        )
    zones_text = root.findtext("DeviceZones")
    if zones_text and zones_text.strip().isdigit():
        device.zone_count = max(1, int(zones_text.strip()))

    # Build the zone list from the discovered count. The main zone (index 1)
    # uses the core control tokens; additional zones use the 'Z<index>' prefix.
    discovery.zones.append(
        ZoneDescriptor(id="main", index=1, name=None, is_main=True)
    )
    for index in range(2, device.zone_count + 1):
        discovery.zones.append(
            ZoneDescriptor(id=f"zone{index}", index=index, name=f"Zone {index}")
        )

    # Advertised features: every FuncName token anywhere in the tree.
    discovery.features = {
        (node.text or "").strip()
        for node in root.iter("FuncName")
        if node.text and node.text.strip()
    }

    # Channel list with codes, display names and per channel dB ranges, all
    # read from the receiver's Channel Level section.
    for ch in root.iter("Ch"):
        code = (ch.findtext("Name") or "").strip()
        name = (ch.findtext("DispName") or "").strip()
        if not code:
            continue
        # The receiver includes a synthetic "Reset" entry with a zero range;
        # skip anything that is not a real, adjustable channel.
        min_range = ch.findtext("MinRange")
        max_range = ch.findtext("MaxRange")
        if name.lower() == "reset" or min_range == max_range:
            continue
        discovery.channels.append(ChannelDescriptor(code=code, name=name or code))
        meta = _range_from_block(ch)
        if meta:
            discovery.numeric_meta[code] = meta

    # Tone control dB ranges, read from the receiver's tone control block.
    tone = root.find(".//ToneControlSet_AVR")
    if tone is not None:
        for feature in ("Bass", "Treble"):
            meta = _range_from_tone(tone, feature)
            if meta:
                discovery.numeric_meta[feature] = meta

    # Titles, option labels and numeric ranges for feature controls. For each
    # feature the profile knows, locate the matching block and read what the
    # receiver publishes: the title (first DispName), the ordered option labels
    # (for enum controls) and the numeric range (for level controls).
    enum_features = enum_features or set()
    for feature in feature_names or ():
        block = root.find(f".//{feature}")
        if block is None:
            continue
        labels = [
            (node.text or "").strip()
            for node in block.iter("DispName")
            if node.text and node.text.strip()
        ]
        if labels:
            discovery.feature_names[feature] = labels[0]
            if feature in enum_features and len(labels) >= 2:
                discovery.option_labels[feature] = labels[1:]
        meta = _range_from_block(block)
        if meta:
            discovery.numeric_meta[feature] = meta

    # Quick select presets (capability and default names), when controllable.
    quick = root.find(".//QuickSelect")
    if quick is not None and (quick.findtext("Control") or "").strip() == "1":
        for child in quick:
            number = (child.findtext("No") or "").strip()
            name = (child.findtext("Name") or "").strip()
            if child.tag.startswith("QuickSelect") and number.isdigit() and name:
                discovery.quick_select_names[int(number)] = name

    # Number of subwoofers the receiver supports (capability, not user setup).
    sub_count = root.findtext(".//SubwooferLevel/SubwooferNum")
    if sub_count and sub_count.strip().isdigit():
        discovery.subwoofer_max = int(sub_count.strip())

    # Sound mode genre names (Movie/Music/Game/Pure), read from the receiver's
    # SoundMode genre list. Keyed by the first three upper case letters so they
    # can be correlated with the 3 letter genre codes used by OPSMLALL.
    genre_list = root.find(".//SoundMode/Genre/List")
    if genre_list is not None:
        for value in genre_list.findall("Value"):
            disp = (value.findtext("DispName") or "").strip()
            if disp:
                discovery.sound_mode_genres[disp[:3].upper()] = disp.title()

    # Master volume scale, read from the receiver's Volume block: the absolute
    # maximum, the step, and the reference (the absolute value shown as 0.0 dB).
    volume = root.find(".//Volume")
    if volume is not None:
        scale: dict[str, float] = {}
        max_value = _num(volume, "MaxValue")
        step_value = _num(volume, "StepValue")
        if max_value is not None:
            scale["absolute_max"] = max_value
        if step_value is not None:
            scale["step"] = step_value
        # The reference is the Absolute value whose Relative label is "0.0dB".
        for param in volume.findall(".//Param"):
            relative = (param.findtext("Relative") or "").strip().lower()
            if relative in ("0.0db", "0db"):
                reference = _num(param, "Absolute")
                if reference is not None:
                    scale["reference"] = reference
                break
        if scale:
            discovery.volume = scale

    # Fallback source list from the InputSource tree (used only when the telnet
    # SSFUN/SSSOD introspection is unavailable). The path leaf is the name.
    for source in root.iter("Source"):
        path = source.findtext("SourcePath") or ""
        leaf = path.rsplit("/", 1)[-1].strip()
        if leaf and leaf.upper() != "SOURCE":
            if not any(s.code == leaf for s in discovery.sources):
                discovery.sources.append(SourceDescriptor(code=leaf, name=leaf))

    return discovery


def _num(element: ET.Element, tag: str) -> float | None:
    """Read a numeric child value, or None when absent or not numeric."""

    text = element.findtext(tag)
    try:
        return float(text) if text is not None and text.strip() != "" else None
    except ValueError:
        return None


def _to_db_range(low: float, high: float, default: float, step: float) -> dict[str, float]:
    """Convert a raw min/max/default/step to a dB range around the default.

    The receiver expresses levels as a raw value where the default is 0 dB and
    each raw unit is `step` dB, so the dB offset is (raw - default) * step. This
    matches both the tone control scale (0..12, default 6, step 1 -> +/- 6 dB)
    and the channel/subwoofer scale (0..48, default 24, step 0.5 -> +/- 12 dB).
    """

    return {
        "min": (low - default) * step,
        "max": (high - default) * step,
        "step": step,
    }


def _range_from_block(block: ET.Element) -> dict[str, float] | None:
    """Derive a dB range from a control block that publishes MinRange etc."""

    low = _num(block, "MinRange")
    high = _num(block, "MaxRange")
    default = _num(block, "DefaultValue")
    step = _num(block, "Step")
    if None in (low, high, default, step):
        return None
    return _to_db_range(low, high, default, step)


def _range_from_tone(tone: ET.Element, prefix: str) -> dict[str, float] | None:
    """Derive a dB range from the tone control block for bass or treble."""

    low = _num(tone, f"{prefix}Min")
    high = _num(tone, f"{prefix}Max")
    default = _num(tone, f"{prefix}Default")
    step = _num(tone, f"{prefix}Step")
    if None in (low, high, default, step):
        return None
    return _to_db_range(low, high, default, step)
