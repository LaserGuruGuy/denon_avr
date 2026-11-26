"""Data models for the Denon AVR client library."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeviceInfo:
    """Static device identity, discovered from the receiver."""

    model_name: str | None = None
    # Read from the UPnP description (Denon or Marantz); the HA layer falls back to "Denon" for
    # display when the description is unavailable.
    manufacturer: str | None = None
    mac_address: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    hardware_type: str | None = None
    # HEOS/AIOS network module identifiers, surfaced in diagnostics rather than on the device card
    # (they describe the streaming module, not the AVR).
    network_module_version: str | None = None
    firmware_revision: str | None = None
    firmware_build: str | None = None
    dts_version: str | None = None
    # As reported: DeviceInfoVers, CommApiVers, Gen, BrandCode, capability_version, moduleType.
    versions: dict[str, str] = field(default_factory=dict)
    zone_count: int = 1

    @property
    def firmware_label(self) -> str | None:
        """The firmware version with the DTS decoder version, e.g. '3.139.173 · DTS 3.90.50.82'."""

        if self.firmware_version and self.dts_version:
            return f"{self.firmware_version} · DTS {self.dts_version}"
        return self.firmware_version

    @property
    def hardware_label(self) -> str | None:
        """The network module with the reported protocol version, e.g. 'Aios 6.5V (CommApi 0301)'."""

        api = self.versions.get("CommApiVers")
        module = self.versions.get("moduleType")
        if module and api:
            return f"{module} (CommApi {api})"
        return module or (f"CommApi {api}" if api else None)


@dataclass
class SourceDescriptor:
    """A selectable input source as described by the receiver itself."""

    code: str
    name: str
    visible: bool = True


@dataclass
class ChannelDescriptor:
    """A speaker channel as described by the receiver (code plus display name)."""

    code: str
    name: str


@dataclass
class ZoneDescriptor:
    """A zone as discovered from the receiver."""

    id: str
    index: int
    name: str | None = None
    is_main: bool = False


@dataclass
class Discovery:
    """The receiver's self description, assembled from device published data."""

    device: DeviceInfo = field(default_factory=DeviceInfo)
    features: set[str] = field(default_factory=set)
    sources: list[SourceDescriptor] = field(default_factory=list)
    channels: list[ChannelDescriptor] = field(default_factory=list)
    zones: list[ZoneDescriptor] = field(default_factory=list)
    # Quick select presets: number -> name.
    quick_select_names: dict[int, str] = field(default_factory=dict)
    # Highest number of subwoofers the receiver supports (Deviceinfo SubwooferLevel/SubwooferNum),
    # None when not reported.
    subwoofer_max: int | None = None
    # Raw SSSPC speaker configuration (group code -> set of reported values) and the derived set
    # of configured channel codes.
    speaker_config: dict[str, set[str]] = field(default_factory=dict)
    configured_channels: set[str] = field(default_factory=set)
    # All modes the receiver currently offers across every genre group (from OPSMLALL), rebuilt
    # per response in the receiver's own order.
    all_sound_modes: list[str] = field(default_factory=list)
    # Sound modes grouped by the receiver's genre code (from OPSMLALL), and the display name per
    # genre (correlated with the Deviceinfo genre list).
    sound_mode_groups: dict[str, list[str]] = field(default_factory=dict)
    sound_mode_genres: dict[str, str] = field(default_factory=dict)
    # The modes actually selectable in the CURRENT context/group (from OPSML).
    current_sound_modes: list[str] = field(default_factory=list)
    # Display title the receiver publishes for a feature, keyed by feature name (for example
    # 'MultEq' -> 'MultEQ XT32').
    feature_names: dict[str, str] = field(default_factory=dict)
    # Option display labels the receiver publishes for enum controls, keyed by the receiver
    # feature name (for example 'DynamicCompression').
    option_labels: dict[str, list[str]] = field(default_factory=dict)
    # Numeric metadata (min/max/step/default in dB) the receiver publishes for level style
    # controls, keyed by feature name or channel code.
    numeric_meta: dict[str, dict[str, float]] = field(default_factory=dict)
    # Master volume scale as published by the receiver's Volume block
    volume: dict[str, float] = field(default_factory=dict)

    def supports(self, function_name: str) -> bool:
        """Return True when the receiver advertises the given capability."""

        return function_name in self.features

    def visible_sources(self) -> list[SourceDescriptor]:
        """Return the sources that are not hidden by the user."""

        return [source for source in self.sources if source.visible]

    def source_name(self, code: str) -> str | None:
        """Return the display name for a source code, if known."""

        for source in self.sources:
            if source.code == code:
                return source.name
        return None

    def source_code(self, name: str) -> str | None:
        """Return the source code for a display name, if known."""

        for source in self.sources:
            if source.name == name:
                return source.code
        return None

    def channel_name(self, code: str) -> str:
        """Return the display name for a channel code, falling back to the code."""

        for channel in self.channels:
            if channel.code == code:
                return channel.name
        return code


@dataclass
class ZoneState:
    """Volatile state of a single zone (main zone or zone 2)."""

    power: bool | None = None
    volume_raw: float | None = None
    volume_max_raw: float | None = None
    muted: bool | None = None
    source: str | None = None


@dataclass
class NowPlaying:
    """Now-playing media on the receiver's network player, from the HEOS CLI."""

    state: str | None = None
    media_type: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    image_url: str | None = None

    @property
    def active(self) -> bool:
        """True when the network player is actually playing or paused."""

        return self.state in ("play", "pause")


@dataclass
class GraphicEqState:
    """Manual graphic-EQ state read from the setup config API."""

    speaker_selection: str | None = None
    channel_index: int | None = None
    bands: dict[str, float] = field(default_factory=dict)
    # Per-index enable flags for the adjustable channels (SelectableSpeaker), one character per
    # channel index ("3" = enabled).
    selectable: str = ""


# Availability flags of the setup web interface (``display`` attribute / value), as the receiver's
# own Web Control app interprets them.
DISPLAY_NOT_APPLICABLE = 1   # hidden by the receiver: not applicable now
DISPLAY_GRAYED = 2           # shown, disabled
DISPLAY_SELECTABLE = 3       # shown, active
DISPLAY_ACTIVE_ALT = 4       # shown, active (rendered like 3 by the app)
_SHOWN = frozenset({DISPLAY_GRAYED, DISPLAY_SELECTABLE, DISPLAY_ACTIVE_ALT})
_ACTIVE = frozenset({DISPLAY_SELECTABLE, DISPLAY_ACTIVE_ALT})


@dataclass(frozen=True)
class Availability:
    """What the receiver reports about one setup item."""

    display: int = DISPLAY_SELECTABLE
    gray: str = "0"

    @property
    def present(self) -> bool:
        return self.display in _SHOWN

    @property
    def settable(self) -> bool:
        return self.display in _ACTIVE

    @property
    def gray_reason(self) -> str | None:
        if self.display == DISPLAY_GRAYED and self.gray not in ("", "0"):
            return self.gray
        return None


@dataclass(frozen=True)
class CollectionItem:
    """One entry of a per-input (or per-slot) setup collection."""

    slot: str
    name: str
    value: str
    options: tuple[str, ...]
    availability: Availability


@dataclass
class AvrState:
    """Complete volatile state of the receiver as parsed from telnet events."""

    system_power: bool | None = None
    # The receiver's master volume display mode ('Relative' shows dB around the 0 dB reference,
    # 'Absolute' shows the raw 0..max scale).
    volume_display: str | None = None
    zones: dict[str, ZoneState] = field(default_factory=dict)
    values: dict[str, object] = field(default_factory=dict)
    readonly: dict[str, str | None] = field(default_factory=dict)
    channel_levels: dict[str, float] = field(default_factory=dict)
    channel_trims: dict[str, float] = field(default_factory=dict)
    channel_distances: dict[str, float] = field(default_factory=dict)
    # Per speaker group crossover frequency in Hz, keyed by the receiver's speaker group code
    # (FRO, CEN, SUA, ...).
    crossovers: dict[str, int] = field(default_factory=dict)
    # Per speaker group size token from SSSPC, keyed by group code.
    speaker_sizes: dict[str, str] = field(default_factory=dict)
    # Control id -> the token as the receiver last wrote it (for example '70' or '060').
    wire_forms: dict[str, str] = field(default_factory=dict)
    # Control id -> the prefix variant the receiver answered with (for example 'SSSWO ').
    wire_prefixes: dict[str, str] = field(default_factory=dict)
    # Now-playing media for network sources, from the HEOS CLI (see transport).
    now_playing: NowPlaying = field(default_factory=NowPlaying)
    # Manual graphic-EQ state is owned by the device's GraphicEqController, not here, because it
    # is read from the setup config API rather than telnet.

    def zone(self, zone_id: str) -> ZoneState:
        """Return the ZoneState for a zone id, creating it on first access."""

        if zone_id not in self.zones:
            self.zones[zone_id] = ZoneState()
        return self.zones[zone_id]
