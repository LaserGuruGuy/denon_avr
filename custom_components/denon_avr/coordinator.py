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
        # Registry id of the main receiver device, set once at setup so the
        # sub-devices can link to it via `via_device_id` (HA 2026.8+).
        self.main_device_id: str | None = None
        # Entity catalogue bookkeeping (see entity.setup_catalogue_platform):
        # keys Home Assistant already had for this receiver, keys created in this
        # run, and per platform the factories + add callback for later additions.
        self.known_keys: set[str] = set()
        self.created_keys: set[str] = set()
        self.platforms: dict[str, tuple[dict, object]] = {}

    async def async_setup(self) -> None:
        """Discover the receiver and start the telnet transport.

        Raises ConnectionError when the receiver cannot be reached so the config
        entry setup is retried by Home Assistant.
        """

        await self.device.async_discover()
        if self.config_entry.unique_id and not self.device.discovery.device.mac_address:
            # Without the MAC every entity would be registered again under another id.
            raise ConnectionError("Deviceinfo without a MAC address")
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
        """Forward a pushed state change to the entities immediately.

        Not ``async_set_updated_data``: that also restarts the reconcile
        interval, and a receiver that pushes every few seconds (the AVC-X3900H
        sends its signal cluster about every 5 s) would then never get its
        reconcile poll - the web documents and the HTTP fallback would starve.
        The state object is shared and updated in place, so notifying the
        listeners is enough; the poll keeps its own fixed cadence.
        """

        self.data = self.device.state
        # A push proves the receiver is reachable: do not leave the entities
        # unavailable until the next poll because one poll failed.
        if self.device.available:
            self.last_update_success = True
        self.async_update_listeners()

    @property
    def available(self) -> bool:
        """Return whether the telnet link to the receiver is currently up."""

        return self.device.available
