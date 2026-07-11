"""Select platform for the Denon AVR integration.

One select entity is created per enum control the receiver advertises (for
example Dynamic Compression, Dynamic Volume, MultEQ, Restorer). The option
labels come from the receiver; the wire values come from the profile.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .avr import graphic_eq
from .avr.profile import ControlSpec
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity
from .helpers import control_name, enum_options, group_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a select entity for each supported enum control."""

    coordinator = entry.runtime_data
    discovery = coordinator.device.discovery
    entities: list[SelectEntity] = [
        DenonAvrSelect(coordinator, spec)
        for spec in coordinator.device.profile.controls.values()
        if spec.kind == "enum"
        and (spec.scope != "feature" or discovery.supports(spec.feature or ""))
    ]
    # Two coupled sound mode selects: one picks the genre group (Movie/Music/
    # Game/Pure), the other lists the modes within the selected group. Both are
    # driven entirely by what the receiver reports. Only created when the
    # receiver advertises sound mode genres.
    if discovery.sound_mode_genres:
        entities.append(DenonAvrSoundModeGroupSelect(coordinator))
        entities.append(DenonAvrSoundModeSelect(coordinator))
    # Quick select presets, if the receiver reported any names.
    if discovery.quick_select_names:
        entities.append(DenonAvrQuickSelect(coordinator))
    # One crossover select per speaker group the receiver reports a crossover
    # for (SSCFR). Groups whose speakers are configured are enabled; the rest are
    # registered but disabled by default, mirroring the channel trims/distances.
    configured = discovery.configured_channels
    profile = coordinator.device.profile
    for group in sorted(coordinator.data.crossovers):
        channels = profile.group_channels(group)
        enabled = (not configured) or any(c in configured for c in channels)
        entities.append(DenonAvrCrossover(coordinator, group, channels, enabled))
    # One size select (Large/Small) per regular speaker group the receiver
    # reports (the count groups - subwoofer, surround back - are not sizes).
    size_groups = profile.speakers.get("groups", {})
    for group in sorted(g for g in coordinator.data.speaker_sizes if g in size_groups):
        channels = profile.group_channels(group)
        enabled = (not configured) or any(c in configured for c in channels)
        entities.append(DenonAvrSpeakerSize(coordinator, group, channels, enabled))
    # Manual graphic-EQ selects, on the EQ sub-device, when the receiver has a
    # graphic EQ this profile can drive: the speaker-selection mode and the
    # channel being adjusted (read per-channel via the config API's opt1 index).
    if coordinator.device.graphic_eq.supported:
        entities.append(DenonAvrEqSpeakerSelection(coordinator))
        entities.append(DenonAvrEqChannel(coordinator))
    async_add_entities(entities)


class DenonAvrSelect(DenonAvrEntity, SelectEntity):
    """A select entity backed by a profile enum control."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator, spec: ControlSpec) -> None:
        super().__init__(coordinator, f"select_{spec.id}")
        self._spec = spec
        self._attr_name = control_name(coordinator.device.discovery, spec)
        options, self._label_to_value, self._value_to_label = enum_options(
            coordinator.device.discovery, spec
        )
        self._attr_options = options

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.values.get(self._spec.id)
        if value is None:
            return None
        # Map the wire value back to its display label; return None (rather than
        # an unknown string) when the device reports a value outside our options,
        # so Home Assistant does not log an "invalid current option" warning.
        return self._value_to_label.get(str(value))

    async def async_select_option(self, option: str) -> None:
        value = self._label_to_value.get(option)
        if value is not None:
            await self.coordinator.device.async_set_control(self._spec.id, value)


class DenonAvrSoundModeGroupSelect(DenonAvrEntity, SelectEntity):
    """Selects which sound mode genre group to browse (Movie/Music/Game/Pure).

    The groups and their names come from the receiver. This select follows the
    receiver's active group by default; picking a different group lets the user
    browse that group's modes in the companion sound mode select, without
    changing anything on the receiver until a mode is actually chosen.
    """

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_sound_mode_group")
        self._attr_translation_key = "sound_mode_group"

    @property
    def _genres(self) -> dict[str, str]:
        return self.coordinator.device.discovery.sound_mode_genres

    @property
    def options(self) -> list[str]:
        return list(self._genres.values())

    @property
    def current_option(self) -> str | None:
        # The current group is reported directly by the receiver (SSSMG).
        code = self.coordinator.data.values.get("sound_mode_genre")
        return self._genres.get(code) if code else None

    async def async_select_option(self, option: str) -> None:
        # Selecting a group switches the receiver to that genre; the receiver
        # then reports the resolved mode, which updates the sound mode select.
        for code, display in self._genres.items():
            if display == option:
                await self.coordinator.device.async_select_sound_mode_group(code)
                return


class DenonAvrSoundModeSelect(DenonAvrEntity, SelectEntity):
    """Lists and selects the sound modes within the active genre group.

    The modes come from the receiver (OPSMLALL) for the currently active group.
    Selecting a mode sends the wire token (a deterministic profile override when
    it differs, else the upper-cased name), which may differ from what the
    receiver reports back as the active mode (expected on Denon, for example
    'Dolby Audio - Dolby Surround').
    """

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_sound_mode")
        self._attr_translation_key = "sound_mode"

    @property
    def options(self) -> list[str]:
        # The receiver's current-context list (OPSML) is authoritative: it holds
        # exactly what the active group offers now, including modes OPSMLALL omits
        # such as Auto. Fall back to the static per genre group if unavailable.
        discovery = self.coordinator.device.discovery
        if discovery.current_sound_modes:
            return list(discovery.current_sound_modes)
        code = self.coordinator.data.values.get("sound_mode_genre")
        return discovery.sound_mode_groups.get(code, []) if code else []

    @property
    def current_option(self) -> str | None:
        current = self.coordinator.data.values.get("sound_mode_display")
        return current if current in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.device.async_select_sound_mode(option)


class DenonAvrCrossover(DenonAvrEntity, SelectEntity):
    """A select entity for one speaker group's crossover frequency.

    The allowed frequencies are a discrete, non-uniform Denon protocol set (from
    the profile), so this is a select rather than a stepped number. Values are
    shown with their 'Hz' unit; the receiver reports the current value per group.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        group: str,
        channels: list[str],
        enabled: bool,
    ) -> None:
        super().__init__(coordinator, f"select_crossover_{group}")
        self._group = group
        self._attr_entity_registry_enabled_default = enabled
        discovery = coordinator.device.discovery
        crossover = coordinator.device.profile.crossover
        self._unit = crossover.get("unit", "Hz")
        # The crossover grid is a fixed Denon protocol constant (same across the
        # lineup; verified live). It is not device-variable, so it lives in the
        # profile like every other enum's wire values - no runtime fetch needed.
        self._values = [int(v) for v in crossover.get("values", [])]
        self._attr_options = [self._format(v) for v in self._values]
        self._attr_name = (
            f"{group_name(coordinator.device.discovery, channels)} Crossover"
        )

    def _format(self, hertz: int) -> str:
        return f"{hertz} {self._unit}"

    @property
    def current_option(self) -> str | None:
        hertz = self.coordinator.data.crossovers.get(self._group)
        # Only report a value the receiver actually offers, so Home Assistant
        # never logs an "invalid current option" warning.
        return self._format(hertz) if hertz in self._values else None

    async def async_select_option(self, option: str) -> None:
        hertz = int(option.split()[0])
        await self.coordinator.device.async_set_crossover(self._group, hertz)


class DenonAvrSpeakerSize(DenonAvrEntity, SelectEntity):
    """A select entity for one speaker group's size (Large/Small).

    The size is a fixed two-value protocol enum (like the receiver's other enum
    controls); the wire tokens and labels come from the profile. The current
    value is read over telnet (SSSPC); groups the receiver reports as absent map
    to no option and are disabled by default.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        group: str,
        channels: list[str],
        enabled: bool,
    ) -> None:
        super().__init__(coordinator, f"select_speaker_size_{group}")
        self._group = group
        self._attr_entity_registry_enabled_default = enabled
        sizes = coordinator.device.profile.speakers.get("sizes", {}).get("options", {})
        self._token_to_label = dict(sizes)
        self._label_to_token = {label: token for token, label in sizes.items()}
        self._attr_options = list(sizes.values())
        self._attr_name = (
            f"{group_name(coordinator.device.discovery, channels)} Speaker Size"
        )

    @property
    def current_option(self) -> str | None:
        token = self.coordinator.data.speaker_sizes.get(self._group)
        # Absent/count tokens map to no option, so no "invalid option" warning.
        return self._token_to_label.get(token)

    async def async_select_option(self, option: str) -> None:
        token = self._label_to_token.get(option)
        if token:
            await self.coordinator.device.async_set_speaker_size(self._group, token)


class DenonAvrQuickSelect(DenonAvrEntity, SelectEntity):
    """Recall the receiver's main zone quick select presets.

    The preset names are discovered from the receiver (SSQSNZMA); the currently
    recalled preset is reported by MSQUICK (0 means none/modified).
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_quick_select")
        self._attr_translation_key = "quick_select"

    @property
    def _names(self) -> dict[int, str]:
        return self.coordinator.device.discovery.quick_select_names

    @property
    def options(self) -> list[str]:
        return [name for _, name in sorted(self._names.items())]

    @property
    def current_option(self) -> str | None:
        number = self.coordinator.data.values.get("quick_select")
        if not number:
            return None
        return self._names.get(int(number))

    async def async_select_option(self, option: str) -> None:
        for number, name in self._names.items():
            if name == option:
                await self.coordinator.device.async_select_quick_select(number)
                return


class DenonAvrEqSpeakerSelection(DenonAvrEntity, SelectEntity):
    """Graphic-EQ speaker selection: one curve for all, the L/R pair, or each."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_eq_speaker_selection", sub_device="eq")
        self._attr_name = "Speaker Selection"
        # code -> label, from the fixed graphic-EQ grammar.
        self._by_code: dict[str, str] = dict(
            coordinator.device.graphic_eq.grammar.get("speaker_selection", {})
        )
        self._by_label = {label: code for code, label in self._by_code.items()}
        self._attr_options = list(self._by_code.values())

    @property
    def current_option(self) -> str | None:
        code = self.coordinator.device.graphic_eq.state.speaker_selection
        return self._by_code.get(code) if code else None

    async def async_select_option(self, option: str) -> None:
        code = self._by_label.get(option)
        if code:
            await self.coordinator.device.graphic_eq.set_speaker_selection(code)


class DenonAvrEqChannel(DenonAvrEntity, SelectEntity):
    """Graphic-EQ channel being read and adjusted.

    Each channel has its own curve, read via the config API's channel index
    (opt1); switching the channel loads that channel's own bands. The available
    channels and their labels depend on the speaker-selection mode (one shared
    curve in 'All', L/R pairs, or individual channels in 'Each').
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_eq_channel", sub_device="eq")
        self._attr_name = "Channel"
        self._grammar = coordinator.device.graphic_eq.grammar

    def _options(self) -> list[tuple[int, str]]:
        eq = self.coordinator.device.graphic_eq.state
        return graphic_eq.channel_options(
            self._grammar, eq.speaker_selection, eq.selectable
        )

    @property
    def options(self) -> list[str]:
        return [label for _, label in self._options()]

    @property
    def current_option(self) -> str | None:
        options = self._options()
        index = self.coordinator.device.graphic_eq.channel
        for i, label in options:
            if i == index:
                return label
        # eq_channel not in the current mode's list (e.g. just after a mode
        # switch): show the first channel rather than an unknown state.
        return options[0][1] if options else None

    async def async_select_option(self, option: str) -> None:
        for index, label in self._options():
            if label == option:
                await self.coordinator.device.graphic_eq.set_channel(index)
                return
