"""Media player platform for the Denon AVR integration.

One media player entity is created per discovered zone. The main zone also
exposes the sound mode; additional zones do not. The selectable source list and
the sound mode list are both built from what the receiver reports.
"""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .avr.models import ZoneDescriptor
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one media player per discovered zone."""

    coordinator = entry.runtime_data
    entities = [
        DenonAvrMediaPlayer(coordinator, zone)
        for zone in coordinator.device.discovery.zones
    ]
    async_add_entities(entities)


class DenonAvrMediaPlayer(DenonAvrEntity, MediaPlayerEntity):
    """A media player representing one zone of the receiver."""

    def __init__(self, coordinator: DenonAvrCoordinator, zone: ZoneDescriptor) -> None:
        super().__init__(coordinator, f"media_player_{zone.id}")
        self._zone = zone
        # The main zone is the primary device entity (no own name); additional
        # zones carry the discovered zone name.
        self._attr_name = None if zone.is_main else zone.name

        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )
        # Only the main zone carries the surround / sound mode.
        if zone.is_main:
            features |= MediaPlayerEntityFeature.SELECT_SOUND_MODE
        self._attr_supported_features = features

    # State ----------------------------------------------------------------

    @property
    def _zone_state(self):
        return self.coordinator.data.zone(self._zone.id)

    @property
    def state(self) -> MediaPlayerState | None:
        power = self._zone_state.power
        if power is None:
            return None
        return MediaPlayerState.ON if power else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        zone = self._zone_state
        if zone.volume_raw is None:
            return None
        # Use the same ceiling as the set path so a set/read round trip is stable.
        ceiling = self._device.volume_effective_max(self._zone.id)
        if not ceiling:
            return None
        return max(0.0, min(1.0, zone.volume_raw / ceiling))

    @property
    def is_volume_muted(self) -> bool | None:
        return self._zone_state.muted

    @property
    def source(self) -> str | None:
        code = self._zone_state.source
        if code is None:
            return None
        return self._device.discovery.source_name(code) or code

    @property
    def source_list(self) -> list[str]:
        return [source.name for source in self._device.discovery.visible_sources()]

    @property
    def sound_mode(self) -> str | None:
        if not self._zone.is_main:
            return None
        # Prefer the receiver's display name for the active mode; fall back to
        # the raw wire token when the display name is not known yet.
        values = self.coordinator.data.values
        return values.get("sound_mode_display") or values.get("sound_mode")

    @property
    def sound_mode_list(self) -> list[str] | None:
        if not self._zone.is_main:
            return None
        # All modes the receiver currently offers across groups (OPSMLALL), in
        # the receiver's own order, plus any current-context extras (e.g. Auto)
        # and the active mode. Not sorted, to keep a sensible order. The set is
        # signal dependent: the receiver only lists modes valid for the signal.
        discovery = self._device.discovery
        modes = list(discovery.all_sound_modes)
        for mode in discovery.current_sound_modes:
            if mode not in modes:
                modes.append(mode)
        current = self.sound_mode
        if current and current not in modes:
            modes.append(current)
        return modes or None

    # Commands -------------------------------------------------------------

    async def async_turn_on(self) -> None:
        await self._device.async_set_power(self._zone.id, True)

    async def async_turn_off(self) -> None:
        await self._device.async_set_power(self._zone.id, False)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._device.async_set_volume_level(self._zone.id, volume)

    async def async_volume_up(self) -> None:
        if self._zone.is_main:
            await self._device.async_volume_step(self._zone.id, up=True)
        else:
            await self._step_zone_volume(up=True)

    async def async_volume_down(self) -> None:
        if self._zone.is_main:
            await self._device.async_volume_step(self._zone.id, up=False)
        else:
            # Additional zones have no dedicated up/down command, so step by one
            # device volume step on the raw scale.
            await self._step_zone_volume(up=False)

    async def _step_zone_volume(self, up: bool) -> None:
        current = self._zone_state.volume_raw
        if current is None:
            return
        step = self._device.volume_step
        await self._device.async_set_volume_raw(
            self._zone.id, current + step if up else current - step
        )

    async def async_mute_volume(self, mute: bool) -> None:
        await self._device.async_set_mute(self._zone.id, mute)

    async def async_select_source(self, source: str) -> None:
        await self._device.async_select_source(self._zone.id, source)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._device.async_select_sound_mode(sound_mode)
