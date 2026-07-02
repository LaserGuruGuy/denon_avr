"""Parsers for the Denon AVR client library.

Two responsibilities live here:

* parse_device_info: turn the HTTP Deviceinfo XML into a Discovery object with
  the device identity, the advertised features, the channel list (codes plus
  display names), the option labels for enum controls and the numeric metadata
  for level controls. All of this is device published.

* TelnetParser: turn incoming telnet lines into updates of the Discovery (for
  the SS introspection responses) and the volatile AvrState. Decoding of values
  is driven by the protocol profile so the wire grammar stays in one place.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from .codec import decode_half_step
from .models import (
    AvrState,
    ChannelDescriptor,
    Discovery,
    SourceDescriptor,
    ZoneDescriptor,
)
from .profile import ProtocolProfile

_LOGGER = logging.getLogger(__name__)


def _format_mac(raw: str | None) -> str | None:
    """Format a bare MAC such as '0005CDBDFD0C' as '00:05:cd:bd:fd:0c'."""

    if not raw:
        return None
    raw = raw.strip()
    if len(raw) != 12:
        return raw
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2)).lower()


# UPnP description local tag name -> the DeviceInfo attribute it fills. The AVR
# firmware and serial go on the device card; the AIOS (HEOS network module)
# version and build revision are captured for diagnostics.
_UPNP_TAGS: dict[str, str] = {
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


def parse_device_info(
    xml_text: str,
    feature_names: set[str] | None = None,
    enum_features: set[str] | None = None,
    generations: dict[str, str] | None = None,
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
    device.model_name = (root.findtext("ModelName") or "").strip() or None
    device.mac_address = _format_mac(root.findtext("MacAddress"))
    # Derive the hardware type from the receiver's generation code using the
    # profile's code -> name map (grammar, not hardcoded). Unmapped codes leave
    # the hardware type unset rather than guessing.
    gen = (root.findtext("Gen") or "").strip()
    if gen and generations:
        device.hardware_type = generations.get(gen)
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


class TelnetParser:
    """Stateful parser that applies telnet lines to a Discovery and AvrState."""

    def __init__(self, profile: ProtocolProfile, discovery: Discovery) -> None:
        self._profile = profile
        self._discovery = discovery
        # Accumulators for the OPSML (current context) and OPSMLALL (all groups)
        # responses; each is swapped into discovery when its 'END' line arrives,
        # so the published lists are always a clean, complete single response.
        self._opsml_pending: list[str] = []
        self._opsmlall_pending: list[str] = []
        # Per genre group accumulator (MOV/MUS/GAM/PUR -> modes), rebuilt each
        # OPSMLALL cycle so a signal change cannot leave a group with a mode the
        # receiver no longer offers.
        self._opsmlall_groups_pending: dict[str, list[str]] = {}
        # Precompute the generic feature and read only matchers, longest prefix
        # first so that specific tokens win over shorter ones.
        self._feature_matchers = sorted(
            (
                (spec.prefix, spec)
                for spec in profile.controls.values()
                if spec.scope == "feature" and spec.prefix and spec.kind != "channel_set"
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        self._readonly_matchers = sorted(
            profile.readonly.items(),
            key=lambda item: len(item[1].get("prefix", "")),
            reverse=True,
        )

    def feed(self, line: str, state: AvrState) -> bool:
        """Apply a single telnet line to the state. Return True if it changed."""

        line = line.rstrip("\r\n")
        if not line:
            return False
        try:
            return self._dispatch(line, state)
        except (ValueError, IndexError) as err:
            _LOGGER.debug("Ignoring unparsable line %r: %s", line, err)
            return False

    # Dispatch order matters because several prefixes overlap. The core zone
    # controls and the overloaded/list style responses are handled explicitly;
    # everything else is decoded generically from the profile.
    def _dispatch(self, line: str, state: AvrState) -> bool:
        if line.startswith("PW"):
            return self._set_system_power(line[2:], state)
        if line.startswith("ZM"):
            state.zone("main").power = line[2:].strip() == "ON"
            return True
        if line.startswith("MVMAX"):
            return self._set_volume_max("main", line[5:], state)
        if line.startswith("MV") and not line.startswith(("MVUP", "MVDOWN")):
            return self._set_volume("main", line[2:], state)
        if line.startswith("MU"):
            state.zone("main").muted = line[2:].strip() == "ON"
            return True
        if line.startswith("MSQUICK"):
            # Current main zone quick select preset number (0 = none).
            token = line[7:].strip()
            if token.isdigit():
                state.values["quick_select"] = int(token)
                return True
            return False
        if line.startswith("MS"):
            return self._set_sound_mode(line[2:], state)
        if line.startswith("SI"):
            state.zone("main").source = line[2:].strip()
            return True
        if line.startswith("SV"):
            state.values["video_select"] = line[2:].strip()
            return True
        if line.startswith("SSLEV"):
            return self._set_channel(line[5:], state.channel_levels)
        if line.startswith("SSSDE"):
            return self._set_distance(line[5:], state.channel_distances)
        if line.startswith("SSCFR"):
            return self._set_crossover(line[5:], state.crossovers)
        if line.startswith("CV"):
            return self._set_channel(line[2:], state.channel_trims)
        if len(line) >= 2 and line[0] == "Z" and line[1].isdigit():
            return self._set_zone(int(line[1]), line[2:], state)
        if line.startswith("SSFUN"):
            return self._set_source_name(line[5:])
        if line.startswith("SSSOD"):
            return self._set_source_visibility(line[5:])
        if line.startswith("SSSMG"):
            # Current sound mode genre group (MOV/MUS/GAM/PUR), reported directly.
            group = line[5:].strip()
            if group and not group.startswith(self._profile.list_terminator):
                state.values["sound_mode_genre"] = group
                return True
            return False
        if line.startswith("SSQSNZMA"):
            return self._set_quick_select_name(line[8:])
        if line.startswith("SSSPC"):
            return self._set_speaker_config(line[5:], state)
        if line.startswith("SSSWM"):
            # Subwoofer mode: a core enum with no FuncName, decoded like a feature.
            spec = self._profile.control("subwoofer_mode")
            return self._set_feature(spec, line[5:], state) if spec else False
        if line.startswith("MNZST"):
            # All Zone Stereo: a core on/off with no FuncName, decoded like a feature.
            spec = self._profile.control("all_zone_stereo")
            return self._set_feature(spec, line[5:], state) if spec else False
        if line.startswith("OPSMLALL"):
            return self._set_sound_mode_list(line[8:], state=state)
        if line.startswith("OPSML"):
            return self._set_current_sound_mode(line[5:], state)

        # Read only audio information.
        for control_id, spec in self._readonly_matchers:
            prefix = spec.get("prefix", "")
            if prefix and line.startswith(prefix):
                return self._set_readonly(control_id, spec, line[len(prefix):], state)

        # Generic feature controls.
        for prefix, spec in self._feature_matchers:
            if line.startswith(prefix):
                return self._set_feature(spec, line[len(prefix):], state)

        return False

    def _set_system_power(self, remainder: str, state: AvrState) -> bool:
        state.system_power = remainder.strip() == "ON"
        return True

    def _set_volume(self, zone_id: str, remainder: str, state: AvrState) -> bool:
        value = decode_half_step(remainder)
        if value is None:
            return False
        state.zone(zone_id).volume_raw = value
        return True

    def _set_volume_max(self, zone_id: str, remainder: str, state: AvrState) -> bool:
        value = decode_half_step(remainder)
        if value is None:
            return False
        state.zone(zone_id).volume_max_raw = value
        return True

    def _set_sound_mode(self, remainder: str, state: AvrState) -> bool:
        remainder = remainder.strip()
        # MS is also the prefix for quick select and smart select echoes; those
        # are not sound modes, so ignore them here.
        if remainder.startswith(("QUICK", "SMART")):
            return False
        # This is the active mode's wire token (for example "DOLBY AUDIO-DSUR").
        state.values["sound_mode"] = remainder
        # If we already know the current mode's display name, learn the mapping
        # from display name to wire token so selection can use the right token.
        display = state.values.get("sound_mode_display")
        if isinstance(display, str):
            self._discovery.sound_mode_wire[display] = remainder
        return True

    def _set_sound_mode_list(self, remainder: str, state: AvrState) -> bool:
        """Parse an OPSMLALL entry (all genre groups) into the flat mode set.

        Entries look like 'OPSMLALL MOV071Virtual': a 3 letter genre code, a two
        digit group, a one digit flag and the display name. This builds the full
        cross group set of modes and the per genre groups. The currently selected
        mode is taken from OPSML (current context), not here, because OPSMLALL
        omits context modes such as Auto.
        """

        remainder = remainder.strip()
        if remainder.startswith(self._profile.list_terminator):
            # End of the list: publish the accumulated set (receiver order) and
            # swap in the freshly rebuilt per group map so no stale modes remain.
            self._discovery.all_sound_modes = list(self._opsmlall_pending)
            self._discovery.sound_mode_groups = self._opsmlall_groups_pending
            self._opsmlall_pending = []
            self._opsmlall_groups_pending = {}
            return True
        if not remainder:
            return False
        genre = remainder[:3]
        match = re.match(r"^(\d+)(.*)$", remainder[3:])
        if not match:
            return False
        name = match.group(2).strip()
        if not name:
            return False
        if name not in self._opsmlall_pending:
            self._opsmlall_pending.append(name)
        modes = self._opsmlall_groups_pending.setdefault(genre, [])
        if name not in modes:
            modes.append(name)
        return True

    def _set_current_sound_mode(self, remainder: str, state: AvrState) -> bool:
        """Parse an OPSML entry (modes selectable in the CURRENT context/group).

        OPSML reflects exactly what the active group offers now, including modes
        OPSMLALL leaves out (such as Auto). Entries look like '011Direct'; the
        trailing flag digit marks the selected mode. The full response is
        accumulated and swapped in on the terminating 'OPSML END' line.
        """

        remainder = remainder.strip()
        if remainder.startswith(self._profile.list_terminator):
            # End of the list: publish the accumulated set as the current options.
            self._discovery.current_sound_modes = list(self._opsml_pending)
            self._opsml_pending = []
            return True
        match = re.match(r"^(\d+)(.*)$", remainder)
        if not match:
            return False
        digits, name = match.group(1), match.group(2).strip()
        if not name:
            return False
        self._opsml_pending.append(name)
        # The trailing digit is the "currently selected" flag; this is the mode
        # the user selected, which can differ from the resolved MS mode.
        if digits and digits[-1] == "1":
            state.values["sound_mode_display"] = name
            # Do NOT learn a wire token for "Auto": it is a meta mode whose wire
            # is always "AUTO", but MS reports the RESOLVED mode (e.g. Stereo).
            # Learning here would map Auto -> the resolved token and break its
            # selection. Concrete modes (Direct, Dolby Audio-…) report their own
            # token, so learning stays correct for them.
            wire = state.values.get("sound_mode")
            if isinstance(wire, str) and name.strip().lower() != "auto":
                self._discovery.sound_mode_wire[name] = wire
        return True

    def _set_channel(self, remainder: str, target: dict[str, float]) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder == self._profile.list_terminator:
            return False
        parts = remainder.split()
        if len(parts) != 2:
            return False
        code, raw = parts
        value = decode_half_step(raw)
        if value is None:
            return False
        target[code] = value - self._profile.level_reference
        return True

    def _set_distance(self, remainder: str, target: dict[str, float]) -> bool:
        """Parse an 'SSSDE<CODE> <value>' speaker distance line into `target`.

        The value is the distance times the profile divisor (e.g. '0310' at
        divisor 100 is 3.10 m). The 'STP' unit line and the 'END' terminator are
        ignored.
        """

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        parts = remainder.split()
        if len(parts) != 2:
            return False
        code, raw = parts
        if code == "STP" or not raw.isdigit():
            return False
        divisor = self._profile.distance.get("divisor", 100) or 100
        target[code] = int(raw) / divisor
        return True

    def _set_crossover(self, remainder: str, target: dict[str, int]) -> bool:
        """Parse an 'SSCFR<GROUP> <Hz>' crossover line into `target`.

        The value is the crossover frequency in Hz. The 'IDV' mode token (no
        value), the 'SSCFRALL' aggregate and the 'END' terminator are ignored;
        the per group values are the authoritative ones.
        """

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        parts = remainder.split()
        if len(parts) != 2:
            return False
        group, raw = parts
        all_group = self._profile.introspection.get("crossover", {}).get(
            "all_group", "ALL"
        )
        if group == all_group or not raw.isdigit():
            return False
        target[group] = int(raw)
        return True

    def _set_zone(self, index: int, remainder: str, state: AvrState) -> bool:
        remainder = remainder.strip()
        if not remainder:
            return False
        zone = state.zone(f"zone{index}")
        if remainder in ("ON", "OFF"):
            zone.power = remainder == "ON"
            return True
        if remainder.startswith("MU"):
            zone.muted = remainder[2:] == "ON"
            return True
        if remainder.isdigit():
            value = decode_half_step(remainder)
            if value is not None:
                zone.volume_raw = value
                return True
            return False
        # Anything else is the zone 2 source code.
        zone.source = remainder
        return True

    def _set_source_name(self, remainder: str) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, name = remainder.partition(" ")
        name = name.rstrip() or code
        self._upsert_source(code, name=name)
        return True

    def _set_source_visibility(self, remainder: str) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, flag = remainder.partition(" ")
        visible_token = self._profile.introspection["source_visibility"].get(
            "visible_token", "USE"
        )
        self._upsert_source(code, visible=flag.strip() == visible_token)
        return True

    def _upsert_source(
        self, code: str, *, name: str | None = None, visible: bool | None = None
    ) -> None:
        """Create or update a discovered source, keeping earlier attributes."""

        for source in self._discovery.sources:
            if source.code == code:
                if name is not None:
                    source.name = name
                if visible is not None:
                    source.visible = visible
                return
        self._discovery.sources.append(
            SourceDescriptor(
                code=code,
                name=name if name is not None else code,
                visible=visible if visible is not None else True,
            )
        )

    def _set_quick_select_name(self, remainder: str) -> bool:
        """Parse a 'QS<n> <name>' quick select entry into the discovery model."""

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, name = remainder.partition(" ")
        name = name.strip()
        # code looks like 'QS1'; take the trailing digits as the preset number.
        digits = "".join(ch for ch in code if ch.isdigit())
        if not digits or not name:
            return False
        self._discovery.quick_select_names[int(digits)] = name
        return True

    def _set_speaker_config(self, remainder: str, state: AvrState) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        group, _, value = remainder.partition(" ")
        value = value.strip()
        if not group or not value:
            return False
        # discovery.speaker_config accumulates every reported value per group (it
        # drives the configured-channel derivation); state.speaker_sizes keeps the
        # latest single value per group so the size selects have a live value.
        self._discovery.speaker_config.setdefault(group, set()).add(value)
        state.speaker_sizes[group] = value
        self._recompute_configured_channels()
        return True

    def _recompute_configured_channels(self) -> None:
        """Derive the configured channel codes from the SSSPC speaker config.

        A group counts as present only when none of its reported values is the
        absent token. The channel/count maps come from the profile (protocol
        grammar); which groups are present comes entirely from the receiver.
        """

        speakers = self._profile.speakers
        absent = speakers.get("absent_token", "NON")
        double = speakers.get("double_token", "2SP")
        groups = speakers.get("groups", {})
        count_groups = speakers.get("count_groups", {})

        configured: set[str] = set()
        for group, values in self._discovery.speaker_config.items():
            if absent in values:
                continue
            if group in count_groups:
                key = "double" if double in values else "single"
                configured.update(count_groups[group].get(key, []))
            elif group in groups:
                configured.update(groups[group])
        self._discovery.configured_channels = configured

    def _set_readonly(
        self, control_id: str, spec: dict, remainder: str, state: AvrState
    ) -> bool:
        kind = spec.get("kind", "passthrough")
        if kind == "sample_rate":
            token = remainder.strip()
            state.readonly[control_id] = (
                None if token == self._profile.no_signal_token else token
            )
        elif kind == "trimmed":
            state.readonly[control_id] = remainder.rstrip() or None
        else:
            state.readonly[control_id] = remainder.strip() or None
        return True

    def _set_feature(self, spec, remainder: str, state: AvrState) -> bool:
        kind = spec.kind
        remainder = remainder.strip()
        if kind == "onoff":
            state.values[spec.id] = remainder == spec.get("on", "ON")
        elif kind == "enum":
            state.values[spec.id] = remainder
        elif kind == "level":
            value = decode_half_step(remainder)
            if value is None:
                return False
            state.values[spec.id] = value - self._profile.level_reference
        elif kind in ("signed_int", "integer"):
            try:
                state.values[spec.id] = int(remainder)
            except ValueError:
                return False
        elif kind == "minutes":
            off = spec.get("off", "OFF")
            if remainder == off:
                state.values[spec.id] = 0
            elif remainder.isdigit():
                state.values[spec.id] = int(remainder)
            else:
                return False
        else:
            state.values[spec.id] = remainder
        return True
