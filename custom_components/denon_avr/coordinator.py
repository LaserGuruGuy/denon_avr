"""Data update coordinator for the Denon AVR integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .avr import AvrState, DenonAvrDevice
from .const import (
    DOMAIN,
    RECONCILE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# The typed config entry carries the coordinator as its runtime data.
type DenonAvrConfigEntry = ConfigEntry[DenonAvrCoordinator]


class DenonAvrCoordinator(DataUpdateCoordinator[AvrState]):
    """Coordinate updates from a single Denon AVR receiver."""

    def __init__(self, hass: HomeAssistant, entry: DenonAvrConfigEntry, host: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {host}",
            update_interval=RECONCILE_INTERVAL,
            config_entry=entry,
        )
        session = async_get_clientsession(hass)
        self.device = DenonAvrDevice(session, host)
        self.device.register_update_callback(self._handle_device_update)
        # Registry id of the main receiver device, set once at setup so the sub-devices can link
        # to it via `via_device_id` (HA 2026.8+).
        self.main_device_id: str | None = None
        self.known_keys: set[str] = set()
        self.created_keys: set[str] = set()
        self.platforms: dict[str, tuple[dict, object]] = {}

    async def async_setup(self) -> None:
        """Discover the receiver and start the telnet transport."""

        await self.device.async_discover()
        expected = self.config_entry.unique_id
        mac = self.device.discovery.device.mac_address
        if expected and mac != expected:
            # Every entity would be registered again under another id (no MAC), or under the wrong
            # receiver (swapped addresses).
            raise ConnectionError(
                "the address belongs to another receiver; use Reconfigure to set the right address"
                if mac
                else "the receiver did not report its MAC address"
            )
        await self.device.async_start()
        # Give the initial resync a moment so per channel entities can be built.
        await self.device.async_await_ready()

    async def async_shutdown(self) -> None:
        """Stop the telnet transport when the entry is unloaded."""

        await self.device.async_stop()
        await super().async_shutdown()

    async def _async_update_data(self) -> AvrState:
        """Run the reconciliation poll and return the current state."""

        try:
            await self.device.async_poll()
        except Exception as err:  # noqa: BLE001 - surface as an update failure
            raise UpdateFailed(f"Error polling Denon AVR: {err}") from err
        return self.device.state

    @callback
    def _handle_device_update(self) -> None:
        """Forward a pushed state change to the entities immediately."""

        self.data = self.device.state
        # A push proves the receiver is reachable: do not leave the entities unavailable until the
        # next poll because one poll failed.
        if self.device.available:
            self.last_update_success = True
        self.async_update_listeners()

    @property
    def available(self) -> bool:
        """Return whether the telnet link to the receiver is currently up."""

        return self.device.available
