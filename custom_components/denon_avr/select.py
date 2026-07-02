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

from .avr.profile import ControlSpec
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity
from .helpers import control_name, enum_options


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
    Selecting a mode uses the learned wire token, so the value sent may differ
    from what the receiver reports back as the active mode (which is expected on
    Denon, for example 'Dolby Audio - Dolby Surround').
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
