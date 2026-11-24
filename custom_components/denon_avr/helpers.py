"""Naming and option helpers shared by the entity platforms.

These functions turn the profile control specs and the device published metadata
into the display names and option lists the entities need. Names always prefer
what the receiver published; a humanised id is only a last resort.
"""

from __future__ import annotations

from .avr import ControlSpec, Discovery


def humanize(text: str) -> str:
    """Turn an id such as 'dynamic_eq' into a readable 'Dynamic Eq'."""

    return text.replace("_", " ").replace(":", "").strip().title()


# Protocol command group -> HA sub-device card. Presentation only: this mirrors
# the receiver's own setup menus (Audio / Video / Speakers) so a large control
# set is split across cohesive device pages instead of crowding one device.
# Groups absent here (power, volume, source, system) keep their entities on the
# main receiver device with the daily-use playback controls.
_GROUP_SUB_DEVICE: dict[str, str] = {
    "audio": "audio",
    "audyssey": "audio",
    "tone": "audio",
    "surround": "audio",
    "video": "video",
    "speaker": "speakers",
}

# Controls whose card differs from their command group's default. All Zone Stereo
# is a whole-house playback toggle (its state is derived from the sound mode), so
# it stays with the playback controls on the main device rather than the Audio
# settings card.
_MAIN_DEVICE_CONTROLS = {"all_zone_stereo"}


def control_sub_device(spec: ControlSpec) -> str | None:
    """Return the sub-device card an entity for this control belongs on.

    None means the main receiver device. Resolution order: an explicit profile
    ``subdevice`` wins (e.g. the graphic EQ on/off lives with the EQ bands), then
    a per-control exception, then the control's command group.
    """

    explicit = spec.get("subdevice")
    if explicit is not None:
        return explicit
    if spec.id in _MAIN_DEVICE_CONTROLS:
        return None
    return _GROUP_SUB_DEVICE.get(spec.group)


def control_name(discovery: Discovery, spec: ControlSpec) -> str:
    """Return the display name for a control, preferring the device title."""

    if spec.feature and spec.feature in discovery.feature_names:
        return discovery.feature_names[spec.feature]
    # Some controls carry a name in the profile (data, not code); use it next.
    profile_name = spec.get("name")
    if profile_name:
        return str(profile_name)
    return humanize(spec.id)


def group_name(discovery: Discovery, channels: list[str]) -> str:
    """Return a display name for a speaker group from its channel names.

    A group's crossover applies to a speaker pair (or single). The receiver
    names the individual channels ('Front L', 'Front R'), so derive the group
    label from those: for a pair, drop the trailing side token to get the shared
    base ('Front'); for a single channel, use its name. Prefer the channels the
    receiver actually configured (they carry real names), falling back to the
    protocol channel codes only when none are configured.
    """

    configured = {ch.code for ch in discovery.channels}
    codes = [c for c in channels if c in configured] or channels
    names = [discovery.channel_name(c) for c in codes]
    if not names:
        return channels[0] if channels else ""
    if len(names) == 1:
        return names[0]
    bases = [n.rsplit(" ", 1)[0] for n in names if " " in n]
    if bases and len(set(bases)) == 1:
        return bases[0]
    return names[0]


def enum_options(discovery: Discovery, spec: ControlSpec) -> tuple[
    list[str], dict[str, str], dict[str, str]
]:
    """Build the option list and label/value maps for an enum control.

    The wire values come from the profile in canonical order; the display labels
    come from the device in the same order. When the counts differ (a model with
    a different option set) the wire tokens are used as labels so a mismatch can
    never send a wrong command.
    """

    values = spec.values
    # Prefer the device-published labels; then any profile-provided labels (for
    # controls the device does not describe); finally a humanised wire token.
    labels = discovery.option_labels.get(spec.feature or "", [])
    if not (labels and len(labels) == len(values)):
        labels = spec.get("labels") or []
    if labels and len(labels) == len(values):
        pairs = list(zip(labels, values))
    else:
        pairs = [(humanize(value), value) for value in values]
    options = [label for label, _ in pairs]
    label_to_value = {label: value for label, value in pairs}
    value_to_label = {value: label for label, value in pairs}
    return options, label_to_value, value_to_label


FULL_RANGE = "Full Range"


def crossover_values(device, group: str | None = None) -> list[int]:
    """The crossover frequencies to offer: the receiver's own list (setup web
    document, e.g. including 0 = Full Range and 70 Hz) when it reports one,
    otherwise the profile's protocol constant.

    Full Range (0) per the owner's manual: the front group may use it; any other
    group only while the front group is Full Range (or when it is its current
    value already)."""

    reported = device.speaker_config.value_list("crossover")
    if reported:
        values = [int(v) for v in reported if v.isdigit()]
    else:
        values = [int(v) for v in device.profile.crossover.get("values", [])]
    if group is not None and 0 in values:
        front = device.profile.speakers.get("front_group", "FRO")
        crossovers = device.state.crossovers
        if group != front and crossovers.get(front) != 0 and crossovers.get(group) != 0:
            values = [v for v in values if v != 0]
    return values


def crossover_label(hertz: int, unit: str) -> str:
    return FULL_RANGE if hertz == 0 else f"{hertz} {unit}"


def crossover_from_label(label: str) -> int:
    return 0 if label == FULL_RANGE else int(label.split()[0])


def _absent_token_of_generation(device) -> str | None:
    """The absent token this receiver's speaker grammar uses, judged from the front
    group (always reported): a presence token (YES/NO) means the presence
    generation ('NO'), a size token (LAR/SMA) the size generation ('NON')."""

    speakers = device.profile.speakers
    tokens = speakers.get("tokens", {})
    front = device.state.speaker_sizes.get(speakers.get("front_group", "FRO"))
    if front in tokens.get("presence", {}):
        return next((t for t in tokens.get("absent", []) if t in tokens.get("presence", {})), None)
    if front in tokens.get("size", []):
        return next((t for t in tokens.get("absent", []) if t in tokens.get("size", [])), None)
    return None


def subwoofer_count_options(device) -> dict[str, str]:
    """Wire token -> label for the number of subwoofers, derived from the receiver.

    The counts run up to what the receiver supports (Deviceinfo SubwooferNum,
    else the profile's count map). "None" uses the absent token of the
    receiver's speaker grammar generation (NO with presence tokens, NON with
    size tokens); when that is not known yet, "None" is not offered.
    """

    speakers = device.profile.speakers
    by_count = speakers.get("count_groups", {}).get("SWF", {}).get("by_count", {})
    highest = device.discovery.subwoofer_max or max((int(n) for n in by_count), default=2)
    options: dict[str, str] = {}
    none_token = _absent_token_of_generation(device)
    if none_token:
        options[none_token] = "None"
    for n in range(1, highest + 1):
        options[f"{n}SP"] = str(n)
    return options


def is_size_token(device, token: str | None) -> bool:
    """True when the receiver reports a speaker SIZE (older firmware: LAR/SMA/NON);
    newer firmware reports presence (YES/NO) instead, where a size select does not apply."""

    return token in device.profile.speakers.get("tokens", {}).get("size", [])


def _speakers_grammar(device) -> dict:
    return device.profile.grammar.get("speakers_web", {})


def amp_mode(device) -> tuple[str | None, str | None]:
    """(assign mode id, stable key) as the receiver reports it: the Web Control
    speaker layout (item 1) or the legacy Amp Assign field; (None, None) if unknown."""

    speakers = device.speaker_config
    item = speakers.collection("speaker_layout_assign").get("1")
    mode = item.value if item is not None else speakers.value("amp_assign")
    if not mode:
        return None, None
    keys = _speakers_grammar(device).get("amp_modes", {}).get("keys", {})
    return mode, keys.get(mode, f"mode_{mode}")


def is_preamplifier(device) -> bool | None:
    mode, _key = amp_mode(device)
    if mode is None:
        return None
    return mode in _speakers_grammar(device).get("amp_modes", {}).get("preamplifier", [])


def _subwoofer_count(device) -> int:
    by_count = device.profile.speakers.get("count_groups", {}).get("SWF", {}).get("by_count", {})
    configured = device.discovery.configured_channels
    return max((int(n) for n, codes in by_count.items() if codes and all(c in configured for c in codes)), default=0)


def has_subwoofer_setup(device) -> bool:
    """Capability: the receiver reports its subwoofer mode/layout (telnet SSSPCSWM /
    SSSPCSWL, telnet first) or offers the Web Control speaker layout."""

    telnet = _speakers_grammar(device).get("subwoofer_setup", {}).get("telnet", {})
    sizes = device.state.speaker_sizes
    return telnet.get("mode_group") in sizes or telnet.get("layout_group") in sizes or has_speaker_layout(device)


def subwoofer_setup(device, which: str) -> str | None:
    """Stable key of the subwoofer mode ("mode") or layout ("layout"), or None
    while it does not apply (the manual: mode only with 2+ subwoofers, layout only
    in Directional mode). Telnet first, the Web Control document second."""

    spec = _speakers_grammar(device).get("subwoofer_setup", {})
    telnet = spec.get("telnet", {})
    if _subwoofer_count(device) < int(telnet.get("min_subwoofers", 2)):
        return None
    if which == "layout" and subwoofer_setup(device, "mode") != "directional":
        return None
    token = device.state.speaker_sizes.get(telnet.get(f"{which}_group", ""))
    # The telnet layout token is only meaningful for some counts (profile).
    counts = telnet.get(f"{which}_counts")
    if token and (counts is None or _subwoofer_count(device) in counts):
        return telnet.get(f"{which}_tokens", {}).get(token, f"option_{token}")
    item = device.speaker_config.collection("speaker_layout_config").get(spec.get(f"{which}_item", ""))
    if item is None or not item.availability.present or not item.value:
        return None
    return spec.get(f"{which}_keys", {}).get(item.value, f"option_{item.value}")


def has_speaker_layout(device) -> bool:
    """The receiver reports the Web Control speaker layout menu (capability)."""

    availability = device.speaker_config.availability_map().get("SpeakerLayout")
    return availability is not None and availability.present


def option_key(device, collection_id: str, option: str) -> str:
    """Stable, translatable key of a per-input option id (``option_<id>`` if unknown)."""

    return device.video_config.option_key(collection_id, option)


def current_input_slot(device, collection_id: str, zone_id: str = "main") -> str | None:
    """The receiver's input slot of the zone's current source, telnet first:
    the SI code -> its name (SSFUN) -> the per-input item with that name. None
    when it cannot be decided unambiguously (unknown, or duplicate names)."""

    code = device.state.zone(zone_id).source
    if not code:
        return None
    name = next((s.name for s in device.discovery.sources if s.code == code), None)
    if not name:
        return None
    matches = [slot for slot, item in device.video_config.collection(collection_id).items() if item.name == name]
    return matches[0] if len(matches) == 1 else None
