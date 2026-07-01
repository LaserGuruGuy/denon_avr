"""Sensor platform for the Denon AVR integration.

Creates read only sensors from what the receiver reports: the audio input
information (sample rate, decoder, format, signal), the current sound mode, and
the per channel calibration levels. Which sensors exist depends on the device.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfSoundPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .avr.models import AvrState
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity
from .helpers import humanize


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the read only sensors discovered for this receiver."""

    coordinator = entry.runtime_data
    device = coordinator.device
    entities: list[SensorEntity] = []

    # Audio information sensors, one per read only field in the profile.
    for control_id, spec in device.profile.readonly.items():
        name = spec.get("name") or humanize(control_id)
        entities.append(
            DenonAvrReadonlySensor(coordinator, control_id, name)
        )

    # The current sound mode as a diagnostic sensor (also on the media player).
    entities.append(DenonAvrSoundModeSensor(coordinator))

    # The current master volume as a read only dB value. The media player keeps
    # the standard percentage control; this shows the actual level in dB.
    entities.append(DenonAvrVolumeSensor(coordinator))

    async_add_entities(entities)


class DenonAvrReadonlySensor(DenonAvrEntity, SensorEntity):
    """A sensor for a single read only audio information field."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: DenonAvrCoordinator, control_id: str, name: str
    ) -> None:
        super().__init__(coordinator, f"sensor_{control_id}")
        self._control_id = control_id
        self._attr_name = name

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.readonly.get(self._control_id)


class DenonAvrSoundModeSensor(DenonAvrEntity, SensorEntity):
    """A diagnostic sensor exposing the current sound mode."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "sensor_sound_mode")
        self._attr_name = "Sound Mode"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.values.get("sound_mode")
        return str(value) if value is not None else None


class DenonAvrVolumeSensor(DenonAvrEntity, SensorEntity):
    """Read only master volume in dB (relative to the receiver's 0 dB reference)."""

    _attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "sensor_volume_db")
        self._attr_name = "Volume"

    @property
    def native_value(self) -> float | None:
        raw = self.coordinator.data.zone("main").volume_raw
        if raw is None:
            return None
        # Show the level relative to the reference, matching the receiver's
        # relative (dB) display, for example -38.0 dB.
        return round(raw - self.coordinator.device.volume_reference, 1)
