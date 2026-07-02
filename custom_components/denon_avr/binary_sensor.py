"""Binary sensor platform for the Denon AVR integration.

Exposes the telnet connectivity as a diagnostic connectivity binary sensor. It
reports the raw link state and therefore stays functional even when the other
entities are unavailable because the link is down.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the connectivity binary sensor."""

    async_add_entities([DenonAvrConnectivitySensor(entry.runtime_data)])


class DenonAvrConnectivitySensor(DenonAvrEntity, BinarySensorEntity):
    """Reports whether the telnet control link is currently up."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "binary_sensor_connectivity")
        self._attr_translation_key = "connectivity"

    @property
    def available(self) -> bool:
        # This sensor must remain available so it can report a lost connection.
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return self.coordinator.available
