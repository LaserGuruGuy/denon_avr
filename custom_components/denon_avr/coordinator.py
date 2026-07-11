"""Data update coordinator for the Denon AVR integration.

The receiver pushes state changes over telnet, so this coordinator is push
driven: the device calls back whenever anything changes and the coordinator
forwards the new state to the entities. The periodic update is only a light HTTP
reconciliation poll that confirms the receiver is reachable and recovers state
if the telnet link is temporarily down.
"""

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

    async def async_setup(self) -> None:
        """Discover the receiver and start the telnet transport.

        Raises ConnectionError when the receiver cannot be reached so the config
        entry setup is retried by Home Assistant.
        """

        await self.device.async_discover()
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
        """Forward a pushed state change to the entities."""

        self.async_set_updated_data(self.device.state)

    @property
    def available(self) -> bool:
        """Return whether the telnet link to the receiver is currently up."""

        return self.device.available
