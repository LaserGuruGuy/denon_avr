"""Entity catalogue: which entities this receiver gets, as plain data (no Home Assistant).

One pure function maps what the library discovered (capabilities, user setup,
setup web documents) to a list of :class:`EntityEntry`. The platforms only turn
entries into entity objects. Keeping the decision here, free of Home Assistant,
makes it testable against the recorded registries (tests/golden/ha_registry) and
keeps every unique_id stable: an entry's ``key`` IS the entity's unique_id
suffix, and :func:`unique_id` is the one place that builds the full id.

Rules (see doc/ARCHITECTURE.md):
* capability decides whether an entity exists, user setup decides whether it is
  enabled by default / available; nothing is created from model names;
* silence is not absence: data the receiver did not mention never removes an entry
  that the capability implies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .helpers import control_sub_device, has_subwoofer_setup

NUMBER_KINDS = frozenset({"level", "signed_int", "minutes", "integer", "centered"})


@dataclass(frozen=True)
class EntityEntry:
    """One entity to create."""

    platform: str            # "select", "number", "switch", "sensor", ...
    kind: str                # which entity class (platform specific factory key)
    key: str                 # unique_id suffix; never change for an existing entity
    sub_device: str | None = None
    enabled_default: bool = True
    params: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


def device_identifier(mac_address: str | None, serial_number: str | None, host: str) -> str:
    """The stable receiver identifier: MAC, else serial number, else host."""

    return mac_address or serial_number or host


def unique_id(identifier: str, key: str) -> str:
    """The one place a unique_id is built: ``<identifier>_<key>``."""

    return f"{identifier}_{key}"


def channel_codes(device) -> list[str]:
    """Channels to create per-channel entities for, from capabilities only (never
    from the timing of telnet answers): the Deviceinfo channel list, plus the
    subwoofers the receiver supports (Deviceinfo SubwooferNum), which some
    firmware leaves out of its channel list (e.g. SW2 on the AVR-X3600H)."""

    codes = [c.code for c in device.discovery.channels]
    by_count = device.profile.speakers.get("count_groups", {}).get("SWF", {}).get("by_count", {})
    highest = device.discovery.subwoofer_max or 0
    for code in by_count.get(str(highest), []):
        if code not in codes:
            codes.append(code)
    return codes


def build(device, known_keys: frozenset[str] | set[str] = frozenset()) -> list[EntityEntry]:
    """Every entity for this receiver, in a stable order (platform by platform).

    ``known_keys`` are the keys Home Assistant already has for this receiver
    (its entity registry). An entity the receiver does not mention right now
    (silence is not absence, e.g. a web menu hidden in the current setup) but that
    exists in the registry is still created, so it stays (unavailable) instead of
    disappearing after a restart.
    """

    profile = device.profile
    discovery = device.discovery
    state = device.state
    configured = discovery.configured_channels
    entries: list[EntityEntry] = []
    add = entries.append

    def supported(spec) -> bool:
        return spec.scope != "feature" or discovery.supports(spec.feature or "")

    def group_enabled(group: str) -> bool:
        return (not configured) or any(c in configured for c in profile.group_channels(group))

    # binary_sensor ------------------------------------------------------------
    add(EntityEntry("binary_sensor", "connectivity", "binary_sensor_connectivity"))

    # button -------------------------------------------------------------------
    if device.graphic_eq.supported:
        add(EntityEntry("button", "eq_apply", "button_eq_apply", "eq"))
        add(EntityEntry("button", "eq_curve_copy", "button_eq_curve_copy", "eq"))
        add(EntityEntry("button", "eq_set_defaults", "button_eq_set_defaults", "eq"))

    # media_player -------------------------------------------------------------
    for zone in discovery.zones:
        add(EntityEntry("media_player", "zone", f"media_player_{zone.id}", params={"zone": zone}))

    # number -------------------------------------------------------------------
    for spec in profile.controls.values():
        if spec.kind in NUMBER_KINDS and supported(spec):
            add(EntityEntry("number", "control", f"number_{spec.id}", control_sub_device(spec), params={"spec": spec}))
    for code in channel_codes(device):
        enabled = (not configured) or code in configured
        params = {"code": code, "channels": [code]}
        add(EntityEntry("number", "channel_trim", f"number_channel_trim_{code}", "speakers", enabled, params))
        add(EntityEntry("number", "channel_distance", f"number_channel_distance_{code}", "speakers", enabled, params))
    if device.graphic_eq.supported:
        grammar = device.graphic_eq.grammar
        from .avr import eq_band_tag   # local: keeps the module import surface small

        for label in grammar.get("bands", []):
            add(EntityEntry("number", "eq_band", f"number_eq_{eq_band_tag(grammar, label)}", "eq", params={"label": label}))

    # select -------------------------------------------------------------------
    for spec in profile.controls.values():
        if spec.kind == "enum" and supported(spec):
            add(EntityEntry("select", "control", f"select_{spec.id}", control_sub_device(spec), params={"spec": spec}))
    if discovery.sound_mode_genres:
        add(EntityEntry("select", "sound_mode_group", "select_sound_mode_group"))
        add(EntityEntry("select", "sound_mode", "select_sound_mode"))
    if discovery.quick_select_names or "select_quick_select" in known_keys:
        add(EntityEntry("select", "quick_select", "select_quick_select"))
    crossover_groups = set(state.crossovers) | _known_groups(known_keys, "select_crossover_", profile)
    for group in sorted(crossover_groups):
        add(EntityEntry("select", "crossover", f"select_crossover_{group}", "speakers", group_enabled(group),
                        {"group": group, "channels": profile.group_channels(group)}))
    if state.crossovers:
        add(EntityEntry("select", "crossover_speaker_selection", "select_crossover_speaker_selection", "speakers"))
        add(EntityEntry("select", "crossover_all", "select_crossover_all", "speakers"))
    size_groups = profile.speakers.get("groups", {})
    known_sizes = _known_groups(known_keys, "select_speaker_size_", profile)
    for group in sorted(g for g in set(state.speaker_sizes) | known_sizes if g in size_groups):
        add(EntityEntry("select", "speaker_size", f"select_speaker_size_{group}", "speakers", group_enabled(group),
                        {"group": group, "channels": profile.group_channels(group)}))
    if "SWF" in state.speaker_sizes or "select_subwoofer_count" in known_keys:
        add(EntityEntry("select", "subwoofer_count", "select_subwoofer_count", "speakers"))
    if device.graphic_eq.supported:
        add(EntityEntry("select", "eq_speaker_selection", "select_eq_speaker_selection", "eq"))
        add(EntityEntry("select", "eq_channel", "select_eq_channel", "eq"))
    # Per-input settings (setup web collections, e.g. 4K/8K format, HDCP) on the
    # "inputs" sub-device, keyed by the receiver's own input slot (stable across
    # renames), plus one contextual select for the current input on Video.
    video = device.video_config
    for spec in video.collection_specs():
        if not spec.get("writable", True):
            continue
        cid = spec["id"]
        items = video.collection(cid)
        prefix = "select_input_"
        slots = set(items) | {
            key[len(prefix):-(len(cid) + 1)] for key in known_keys
            if key.startswith(prefix) and key.endswith(f"_{cid}")
        }
        for slot in sorted(slots, key=lambda x: (len(x), x)):
            add(EntityEntry("select", "input_setting", f"select_input_{slot}_{cid}", "inputs",
                            params={"collection": cid, "slot": slot}))
        if items or f"select_current_{cid}" in known_keys:
            add(EntityEntry("select", "current_input_setting", f"select_current_{cid}", "video",
                            params={"collection": cid}))
    for attr, sub in (("video_config", "video"), ("speaker_config", "speakers")):
        section = getattr(device, attr)
        if not section.supported:
            continue
        for control in section.controls():
            known = f"select_{control['id']}" in known_keys
            if control.get("kind") == "select" and (section.present(control["id"]) or known):
                add(EntityEntry("select", "web_select", f"select_{control['id']}", sub,
                                params={"control": control, "controller_attr": attr}))

    # sensor -------------------------------------------------------------------
    for control_id in profile.readonly:
        add(EntityEntry("sensor", "readonly", f"sensor_{control_id}", params={"control_id": control_id}))
    add(EntityEntry("sensor", "sound_mode", "sensor_sound_mode"))
    add(EntityEntry("sensor", "volume_db", "sensor_volume_db"))
    if device.speaker_config.supported:
        # Amplifier assign mode incl. "pre-amplifier only" (read-only, diagnostic).
        has_amp_select = device.speaker_config.present("amp_assign")
        add(EntityEntry("sensor", "amp_mode", "sensor_amp_mode", "speakers", not has_amp_select))
        if has_subwoofer_setup(device) or "sensor_subwoofer_mode" in known_keys:
            # Web Control subwoofer setup; unavailable while the receiver hides it.
            add(EntityEntry("sensor", "subwoofer_setup", "sensor_subwoofer_mode", "speakers", params={"which": "mode"}))
            add(EntityEntry("sensor", "subwoofer_setup", "sensor_subwoofer_layout", "speakers", params={"which": "layout"}))

    # switch -------------------------------------------------------------------
    for spec in profile.controls.values():
        if spec.kind == "onoff" and supported(spec):
            add(EntityEntry("switch", "control", f"switch_{spec.id}", control_sub_device(spec), params={"spec": spec}))
    video = device.video_config
    if video.supported:
        for control in video.controls():
            known = f"switch_{control['id']}" in known_keys
            if control.get("kind") == "switch" and (video.present(control["id"]) or known):
                add(EntityEntry("switch", "web_switch", f"switch_{control['id']}", "video", params={"control": control}))
    return entries


def for_platform(
    device, platform: str, known_keys: frozenset[str] | set[str] = frozenset()
) -> list[EntityEntry]:
    return [entry for entry in build(device, known_keys) if entry.platform == platform]


def _known_groups(known_keys, prefix: str, profile) -> set[str]:
    """Speaker groups that have a registry entry with this key prefix."""

    groups = set(profile.speakers.get("groups", {})) | set(profile.speakers.get("count_groups", {}))
    found = {key[len(prefix):] for key in known_keys if key.startswith(prefix)}
    return {g for g in found if g in groups}


def keys_to_enable(
    entries: list[EntityEntry], disabled_by_integration: set[str], configured_channels
) -> set[str]:
    """Keys the integration itself disabled earlier that the receiver's current
    speaker setup now uses (e.g. a second subwoofer was switched on).

    Only speaker-channel entities (entries with ``channels``) are considered: their
    default follows the user's speaker setup. Nothing is decided while the speaker
    setup is unknown (silence is not absence). Entities the USER disabled are
    never in ``disabled_by_integration`` and so are never touched."""

    if not configured_channels:
        return set()
    return {
        e.key for e in entries
        if e.params.get("channels") and e.enabled_default and e.key in disabled_by_integration
    }
