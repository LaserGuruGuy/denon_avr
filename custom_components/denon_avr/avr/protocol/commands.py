"""Pure telnet command builders."""

from __future__ import annotations

from ..models import Discovery, ZoneDescriptor
from .codec import encode_centered, encode_half_step
from .profile import ControlSpec, ProtocolProfile


def zone_prefix(profile: ProtocolProfile, zone: ZoneDescriptor | None) -> str | None:
    """The 'Z<index>' prefix of an additional zone, or None for the main zone."""

    if zone is None or zone.is_main:
        return None
    return f"{profile.zones.get('additional_prefix', 'Z')}{zone.index}"


def power(profile: ProtocolProfile, prefix: str | None, on: bool) -> str | None:
    if prefix is None:
        spec = profile.control("main_power")
        if spec is None:
            return None
        return f"{spec.prefix}{spec.get('on', 'ON') if on else spec.get('off', 'OFF')}"
    zones = profile.zones
    return f"{prefix}{zones.get('power_on', 'ON') if on else zones.get('power_off', 'OFF')}"


def system_power(profile: ProtocolProfile, on: bool) -> str | None:
    spec = profile.control("system_power")
    if spec is None:
        return None
    return f"{spec.prefix}{spec.get('on', 'ON') if on else spec.get('off', 'STANDBY')}"


def volume(profile: ProtocolProfile, prefix: str | None, raw: float) -> str | None:
    """Absolute volume on the raw scale (already clamped by the caller)."""

    encoded = encode_half_step(raw)
    if prefix is None:
        spec = profile.control("main_volume")
        return f"{spec.prefix}{encoded}" if spec is not None else None
    # Additional zones set the volume through their overloaded prefix.
    return f"{prefix}{encoded}"


def volume_step(profile: ProtocolProfile, up: bool) -> str | None:
    spec = profile.control("main_volume")
    if spec is None:
        return None
    return (spec.get("up") if up else spec.get("down")) or None


def mute(profile: ProtocolProfile, prefix: str | None, muted: bool) -> str | None:
    if prefix is None:
        spec = profile.control("main_mute")
        if spec is None:
            return None
        return f"{spec.prefix}{spec.get('on', 'ON') if muted else spec.get('off', 'OFF')}"
    # Additional zones mute through their overloaded prefix (Z2MUON/OFF).
    zones = profile.zones
    on_off = zones.get("mute_on", "ON") if muted else zones.get("mute_off", "OFF")
    return f"{prefix}{zones.get('mute_token', 'MU')}{on_off}"


def source(profile: ProtocolProfile, prefix: str | None, code: str) -> str | None:
    if prefix is None:
        spec = profile.control("main_source")
        return f"{spec.prefix}{code}" if spec is not None else None
    return f"{prefix}{code}"


def sound_mode_wire(profile: ProtocolProfile, name: str) -> str:
    """MS token for a display name: the profile override, else the upper-cased name."""

    return profile.sound_mode_wire_overrides.get(name) or name.upper()


def sound_mode(profile: ProtocolProfile, name: str) -> str | None:
    spec = profile.control("sound_mode")
    if spec is None:
        return None
    return f"{spec.prefix}{sound_mode_wire(profile, name)}"


def sound_mode_group(profile: ProtocolProfile, code: str) -> str | None:
    wire = profile.sound_mode_genre_commands.get(code)
    spec = profile.control("sound_mode")
    if not wire or spec is None:
        return None
    return f"{spec.prefix}{wire}"


def quick_select(profile: ProtocolProfile, number: int) -> str | None:
    prefix = profile.introspection_prefix("quick_select")
    return f"{prefix}{int(number)}" if prefix else None


def encode_control_value(profile: ProtocolProfile, spec: ControlSpec, value: object) -> str | None:
    """Encode a Python value into the wire argument for a control."""

    kind = spec.kind
    if kind == "onoff":
        return spec.get("on", "ON") if value else spec.get("off", "OFF")
    if kind == "enum":
        return str(value)
    if kind == "level":
        raw = profile.level_reference + float(value)  # type: ignore[arg-type]
        return encode_half_step(raw)
    if kind == "centered":
        # Fixed-width integer offset around a centre (picture controls).
        width = int(spec.get("width", 3))
        return encode_centered(float(value), int(spec.get("center", 0)), width)
    if kind == "signed_int":
        return str(int(value))  # type: ignore[arg-type]
    if kind == "integer":
        # Zero pad to the wire width the receiver expects (e.g. 3 for PSDELAY).
        width = int(spec.get("width", 0) or 0)
        return str(int(value)).zfill(width)  # type: ignore[arg-type]
    if kind == "minutes":
        minutes = int(value)  # type: ignore[arg-type]
        if minutes <= 0:
            return spec.get("off", "OFF")
        return f"{minutes:03d}"
    return str(value)


def control(profile: ProtocolProfile, control_id: str, value: object) -> str | None:
    spec = profile.control(control_id)
    if spec is None:
        return None
    argument = encode_control_value(profile, spec, value)
    if argument is None:
        return None
    return f"{spec.prefix}{argument}"


def _introspection_prefix(profile: ProtocolProfile, key: str, default: str) -> str:
    spec = profile.introspection.get(key, {})
    return spec.get("set_prefix") or spec.get("prefix") or default


def channel_trim(profile: ProtocolProfile, code: str, db: float) -> str | None:
    spec = profile.control("channel_level")
    if spec is None:
        return None
    raw = profile.level_reference + db
    return f"{spec.get('set_prefix', 'CV')}{code} {encode_half_step(raw)}"


def channel_distance(profile: ProtocolProfile, code: str, meters: float) -> str:
    divisor = profile.distance.get("divisor", 100) or 100
    raw = max(0, int(round(meters * divisor)))
    return f"{_introspection_prefix(profile, 'channel_distances', 'SSSDE')}{code} {raw:04d}"


def crossover(profile: ProtocolProfile, group: str, hertz: int) -> str:
    """Zero padded to the protocol width (80 -> '080'); off-grid values are ignored by the receiver."""

    width = int(profile.crossover.get("width", 3) or 3)
    tokens = {v: k for k, v in profile.introspection.get("crossover", {}).get("value_tokens", {}).items()}
    value = tokens.get(int(hertz), f"{int(hertz):0{width}d}")      # 0 -> FUL (full range)
    return f"{_introspection_prefix(profile, 'crossover', 'SSCFR')}{group} {value}"


def crossover_mode(profile: ProtocolProfile, mode: str) -> str:
    """'<prefix> <mode>' (e.g. 'SSCFR IDV'): per-group crossovers or the single All crossover."""

    return f"{_introspection_prefix(profile, 'crossover', 'SSCFR')} {mode}"


def speaker_size(profile: ProtocolProfile, group: str, token: str) -> str:
    return f"{_introspection_prefix(profile, 'speaker_config', 'SSSPC')}{group} {token}"


def introspection_queries(profile: ProtocolProfile) -> list[str]:
    return [spec["query"] for spec in profile.introspection.values() if spec.get("query")]


def resync_queries(profile: ProtocolProfile, discovery: Discovery) -> list[str]:
    """The ordered, de-duplicated query list that resynchronises the full state."""

    queries: list[str] = []

    def add(query: str | None) -> None:
        if query and query not in queries:
            queries.append(query)

    # Introspection first so the source and channel lists (which entities are built from) arrive
    # as early as possible after connecting.
    for spec in profile.introspection.values():
        add(spec.get("query"))
    # Core controls (main zone) are always present.
    for spec in profile.controls_by_scope("core"):
        add(spec.query)
    # Additional zones use the 'Z<index>' query, one per discovered zone.
    prefix = profile.zones.get("additional_prefix", "Z")
    for zone in discovery.zones:
        if not zone.is_main:
            add(f"{prefix}{zone.index}?")
    # Read only audio information.
    for spec in profile.readonly.values():
        add(spec.get("query"))
    # Feature controls, only those the receiver advertises.
    for spec in profile.feature_controls():
        if spec.feature and discovery.supports(spec.feature):
            add(spec.query)
            add(spec.get("list_query"))
            add(spec.get("read_query"))
    return queries
