"""Base entity for the Denon AVR integration.

All entities share the device identity (built from discovery data) and become
unavailable together when the telnet link drops. Entity names come from the
receiver, so the entity ids naturally get the model based prefix (for example
`sensor.denon_avr_x3600h_sample_rate`) through Home Assistant's has_entity_name.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DenonAvrCoordinator


class DenonAvrEntity(CoordinatorEntity[DenonAvrCoordinator]):
    """Base class providing device info and availability for all entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DenonAvrCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._device = coordinator.device
        device_info = self._device.discovery.device
        # Prefer the MAC as the stable identifier; fall back to the host.
        identifier = device_info.mac_address or self._device.host
        self._attr_unique_id = f"{identifier}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=device_info.manufacturer,
            model=device_info.model_name,
            name=device_info.model_name or "Denon AVR",
            sw_version=device_info.firmware_version,
            # The receiver's own web control page, useful as a device link.
            configuration_url=f"https://{self._device.host}:10443/",
        )

    @property
    def available(self) -> bool:
        """Entities are available only while the telnet link is up."""

        return super().available and self.coordinator.available
