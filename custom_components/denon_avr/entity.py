"""Base entity for the Denon AVR integration.

All entities share the device identity (built from discovery data) and become
unavailable together when the telnet link drops. Entity names come from the
receiver, so the entity ids naturally get the model based prefix (for example
`sensor.denon_avr_x3600h_sample_rate`) through Home Assistant's has_entity_name.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DenonAvrCoordinator

# Display names for the logical sub-devices grouped under the receiver. A
# sub-device keeps a large, cohesive subsystem (e.g. the graphic equaliser) on
# its own device page instead of crowding the main receiver device.
SUB_DEVICE_NAMES = {"eq": "Graphic EQ"}


class DenonAvrEntity(CoordinatorEntity[DenonAvrCoordinator]):
    """Base class providing device info and availability for all entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DenonAvrCoordinator, key: str, sub_device: str | None = None
    ) -> None:
        super().__init__(coordinator)
        self._device = coordinator.device
        device_info = self._device.discovery.device
        # Prefer the MAC as the stable identifier; then the serial number (also
        # stable), and only as a last resort the host, which can change.
        identifier = (
            device_info.mac_address
            or device_info.serial_number
            or self._device.host
        )
        self._attr_unique_id = f"{identifier}_{key}"
        if sub_device:
            # A logical sub-device linked to the main receiver via via_device.
            suffix = SUB_DEVICE_NAMES.get(sub_device, sub_device)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{identifier}_{sub_device}")},
                via_device=(DOMAIN, identifier),
                manufacturer=device_info.manufacturer or "Denon",
                model=device_info.model_name,
                name=f"{device_info.model_name or 'Denon AVR'} {suffix}",
            )
            return
        # Expose the MAC as a device connection (the standard HA convention for
        # network devices) so the device registry can de-duplicate it.
        connections = set()
        if device_info.mac_address:
            connections.add((CONNECTION_NETWORK_MAC, device_info.mac_address))
        self._attr_device_info = DeviceInfo(
            connections=connections,
            identifiers={(DOMAIN, identifier)},
            manufacturer=device_info.manufacturer or "Denon",
            model=device_info.model_name,
            name=device_info.model_name or "Denon AVR",
            sw_version=device_info.firmware_version,
            hw_version=device_info.hardware_type,
            serial_number=device_info.serial_number,
            # The receiver's own web control page, useful as a device link.
            configuration_url=f"https://{self._device.host}:10443/",
        )

    @property
    def available(self) -> bool:
        """Entities are available only while the telnet link is up."""

        return super().available and self.coordinator.available
