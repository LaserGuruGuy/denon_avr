"""Data models for the Denon AVR client library.

Two kinds of data are represented here:

* Discovery data (DeviceInfo, SourceDescriptor, ChannelDescriptor, Discovery):
  the receiver's own description of how it is configured. This is populated once
  at connect time (and can be refreshed) by parsing the Deviceinfo XML tree and
  the telnet "SS" introspection responses. It is the single source of truth for
  which inputs and channels exist and what they are called. None of it is
  hardcoded; it all comes from the device.

* Volatile state (ZoneState, AvrState): the live values that change while using
  the receiver. These are updated continuously from telnet push events.

All fields are optional and default to None/empty because the receiver reports
its state incrementally. Consumers must treat None as "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeviceInfo:
    """Static device identity, discovered from the receiver."""

    model_name: str | None = None
    manufacturer: str = "Denon"
    mac_address: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    hardware_type: str | None = None
    # HEOS/AIOS network module identifiers, surfaced in diagnostics rather than
    # on the device card (they describe the streaming module, not the AVR).
    network_module_version: str | None = None
    firmware_revision: str | None = None
    zone_count: int = 1


@dataclass
class SourceDescriptor:
    """A selectable input source as described by the receiver itself.

    `code` is the identifier the receiver uses on the wire (the token after
    SI... and SSFUN...). `name` is the display name the receiver reports, which
    reflects any rename the user configured. `visible` mirrors the receiver's
    USE/DEL flag so hidden inputs can be left out of the selectable list.
    """

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
    """A zone as discovered from the receiver.

    `index` is the 1 based zone number (1 is the main zone). `is_main` marks the
    zone that uses the core control tokens; additional zones use the 'Z<index>'
    prefix. `name` is a generic label derived from the index; the main zone uses
    the device name and carries None here.
    """

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
    # Quick select presets discovered from the receiver (SSQSNZMA): number -> name.
    quick_select_names: dict[int, str] = field(default_factory=dict)
    # Raw SSSPC speaker configuration (group code -> set of reported values) and
    # the derived set of configured channel codes. Both come from the receiver.
    speaker_config: dict[str, set[str]] = field(default_factory=dict)
    configured_channels: set[str] = field(default_factory=set)
    # Available sound mode display names, discovered from the receiver's OPSML
    # list. The wire token used to select a mode is not the same as its display
    # name for every mode, so it is learned by correlation and stored here
    # (display name -> MS wire token).
    # All modes the receiver currently offers across every genre group (from
    # OPSMLALL), rebuilt per response in the receiver's own order. This is the
    # flat "all modes" list for the media player. Note it is signal dependent:
    # the receiver only lists modes applicable to the current audio signal.
    all_sound_modes: list[str] = field(default_factory=list)
    sound_mode_wire: dict[str, str] = field(default_factory=dict)
    # Sound modes grouped by the receiver's genre code (from OPSMLALL), and the
    # display name per genre (correlated with the Deviceinfo genre list). This
    # lets us expose one select per group in addition to the flat list.
    sound_mode_groups: dict[str, list[str]] = field(default_factory=dict)
    sound_mode_genres: dict[str, str] = field(default_factory=dict)
    # The modes actually selectable in the CURRENT context/group (from OPSML).
    # This is context aware and includes modes that OPSMLALL omits, such as Auto.
    current_sound_modes: list[str] = field(default_factory=list)
    # Display title the receiver publishes for a feature, keyed by feature name
    # (for example 'MultEq' -> 'MultEQ XT32'). Used for entity names.
    feature_names: dict[str, str] = field(default_factory=dict)
    # Option display labels the receiver publishes for enum controls, keyed by
    # the receiver feature name (for example 'DynamicCompression'). Ordered.
    option_labels: dict[str, list[str]] = field(default_factory=dict)
    # Numeric metadata (min/max/step/default in dB) the receiver publishes for
    # level style controls, keyed by feature name or channel code.
    numeric_meta: dict[str, dict[str, float]] = field(default_factory=dict)
    # The selectable speaker crossover frequencies (Hz) the receiver advertises
    # via its web control /ajax speaker config. Empty when that API is
    # unavailable/locked, in which case the profile's protocol set is used.
    crossover_values: list[int] = field(default_factory=list)
    # Master volume scale as published by the receiver's Volume block:
    # 'reference' is the absolute value that equals 0.0 dB, 'step' the increment,
    # 'absolute_max' the highest absolute value. Empty when not published.
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
class AvrState:
    """Complete volatile state of the receiver as parsed from telnet events.

    The state is kept generic so it mirrors whatever controls the profile knows
    and the receiver reports, rather than a fixed set of named fields:

    * `zones` holds the per zone power/volume/mute/source (main, zone2).
    * `values` holds every other control keyed by its profile control id
      (for example 'bass', 'multeq', 'sound_mode'); values are already decoded.
    * `readonly` holds the read only audio information keyed by its id
      (for example 'sample_rate', 'decoder').
    * `channel_levels` and `channel_trims` hold per channel dB offsets keyed by
      the receiver channel code.
    * `crossovers` holds the crossover frequency in Hz per speaker group, keyed
      by the receiver's speaker group code.
    """

    system_power: bool | None = None
    # The receiver's master volume display mode ('Relative' shows dB around the
    # 0 dB reference, 'Absolute' shows the raw 0..max scale). From StatusLite.
    volume_display: str | None = None
    zones: dict[str, ZoneState] = field(default_factory=dict)
    values: dict[str, object] = field(default_factory=dict)
    readonly: dict[str, str | None] = field(default_factory=dict)
    channel_levels: dict[str, float] = field(default_factory=dict)
    channel_trims: dict[str, float] = field(default_factory=dict)
    channel_distances: dict[str, float] = field(default_factory=dict)
    # Per speaker group crossover frequency in Hz, keyed by the receiver's
    # speaker group code (FRO, CEN, SUA, ...). Crossover is a group setting, not
    # a per channel one, so this is keyed by group rather than channel.
    crossovers: dict[str, int] = field(default_factory=dict)

    def zone(self, zone_id: str) -> ZoneState:
        """Return the ZoneState for a zone id, creating it on first access."""

        if zone_id not in self.zones:
            self.zones[zone_id] = ZoneState()
        return self.zones[zone_id]
