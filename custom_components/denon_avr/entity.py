"""Base entity for the Denon AVR integration.

All entities share the device identity (built from discovery data) and become
unavailable together when the telnet link drops. Entity names come from the
receiver, so the entity ids naturally get the model based prefix (for example
`sensor.denon_avr_x3600h_sample_rate`) through Home Assistant's has_entity_name.
"""

from __future__ import annotations

import logging

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DenonAvrCoordinator
from . import catalog

_LOGGER = logging.getLogger(__name__)

# Display names for the logical sub-devices grouped under the receiver. Each
# keeps a cohesive subsystem on its own device page (mirroring the receiver's
# setup menus) instead of crowding the main receiver device. Routing of a
# control to one of these lives in helpers.control_sub_device.
SUB_DEVICE_NAMES = {
    "audio": "Audio",
    "video": "Video",
    "picture": "Picture",
    "speakers": "Speakers",
    "eq": "Graphic EQ",
    "inputs": "Inputs",
}


class DenonAvrEntity(CoordinatorEntity[DenonAvrCoordinator]):
    """Base class providing device info and availability for all entities."""

    _attr_has_entity_name = True
    # Set by subclasses / the catalogue (see available).
    _web_backed: bool = False
    _required_channels: tuple[str, ...] = ()
    _eq_gated: bool = False     # graphic EQ controls: also need the EQ menu settable

    def __init__(
        self, coordinator: DenonAvrCoordinator, key: str, sub_device: str | None = None
    ) -> None:
        super().__init__(coordinator)
        self._device = coordinator.device
        device_info = self._device.discovery.device
        # Prefer the MAC as the stable identifier; then the serial number (also
        # stable), and only as a last resort the host, which can change.
        identifier = catalog.device_identifier(
            device_info.mac_address, device_info.serial_number, self._device.host
        )
        self._attr_unique_id = catalog.unique_id(identifier, key)
        if sub_device:
            # A logical sub-device linked to the main receiver by its registry id
            # (via_device_id); the deprecated via_device identifier tuple is
            # removed in HA 2027.8.0. The main device is registered up front in
            # __init__ so its id is available here.
            suffix = SUB_DEVICE_NAMES.get(sub_device, sub_device)
            model = device_info.model_name or "Denon AVR"
            info = DeviceInfo(
                identifiers={(DOMAIN, f"{identifier}_{sub_device}")},
                manufacturer=device_info.manufacturer or "Denon",
                model=device_info.model_name,
                # Translated via strings.json "device.<sub_device>" ({model} placeholder).
                translation_key=sub_device,
                translation_placeholders={"model": model},
                name=f"{model} {suffix}",
            )
            if coordinator.main_device_id:
                info["via_device_id"] = coordinator.main_device_id
            self._attr_device_info = info
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
            configuration_url=self._device.configuration_url or f"http://{self._device.host}/",
        )

    @property
    def available(self) -> bool:
        """Availability per channel, following the user's setup.

        * Telnet-backed entities (the default) need the telnet link.
        * Web-backed entities (``_web_backed``) need the setup web interface
          instead, so they stay usable when only telnet is down.
        * Entities for specific speaker channels (``_required_channels``, set from
          the catalogue) are unavailable while none of those channels is in the
          receiver's current speaker setup; they are never removed.
        """

        if not super().available:
            return False
        if self._web_backed:
            link = self.coordinator.device.web_reachable is not False
        else:
            link = self.coordinator.available
        if not link:
            return False
        if self._eq_gated and not self.coordinator.device.graphic_eq.settable:
            return False
        if self._required_channels:
            configured = self.coordinator.device.discovery.configured_channels
            if configured and not any(c in configured for c in self._required_channels):
                return False
        return True


def catalogue_entities(coordinator, platform: str, factories: dict, entries=None) -> list:
    """Entities for one platform, as the catalogue lists them.

    The catalogue is the single source of every unique_id: an entity whose own
    key differs from its catalogue entry gets the catalogue key (and a warning),
    so the id recorded in the registries can never drift silently. Entry params
    ``channels`` become the entity's required channels (availability).
    """

    device = coordinator.device
    info = device.discovery.device
    identifier = catalog.device_identifier(info.mac_address, info.serial_number, device.host)
    if entries is None:
        entries = catalog.for_platform(device, platform, coordinator.known_keys)
    entities = []
    for entry in entries:
        entity = factories[entry.kind](entry)
        expected = catalog.unique_id(identifier, entry.key)
        if entity.unique_id != expected:
            _LOGGER.warning("Entity key %s differs from the catalogue %s; using the catalogue", entity.unique_id, expected)
            entity._attr_unique_id = expected
        channels = entry.params.get("channels")
        if channels:
            entity._required_channels = tuple(channels)
        # The catalogue is also the source of the default enablement.
        entity._attr_entity_registry_enabled_default = entry.enabled_default
        entities.append(entity)
        coordinator.created_keys.add(entry.key)
    return entities


def setup_catalogue_platform(coordinator, platform: str, factories: dict, async_add_entities) -> None:
    """Add a platform's entities now, and remember how to add more later.

    When the receiver reports a new capability after setup (a web menu that
    appears, a speaker group that starts reporting), the coordinator adds the new
    catalogue entries through the stored callback, without a reload.
    """

    coordinator.platforms[platform] = (factories, async_add_entities)
    async_add_entities(catalogue_entities(coordinator, platform, factories))
