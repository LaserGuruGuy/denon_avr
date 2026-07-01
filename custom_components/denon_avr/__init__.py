"""The Denon AVR integration.

Sets up a single receiver from a config entry: it discovers the device over
HTTP, starts the telnet push transport, and forwards the platforms. The
coordinator (stored as the entry runtime data) owns the device connection.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .avr.profile import load_profile
from .const import CONF_HOST, PLATFORMS
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator

_LOGGER = logging.getLogger(__name__)

# One time marker (stored in the entry options) so the channel default disabling
# runs only once and never overrides the user's later manual choices.
_CHANNELS_INITIALIZED = "channels_initialized"
# Unique id fragment identifying per channel entities and how to read the code.
_CHANNEL_MARKER = "_channel_trim_"


async def async_setup_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> bool:
    """Set up Denon AVR from a config entry."""

    host = entry.data[CONF_HOST]
    # Warm the protocol profile cache off the event loop so constructing the
    # device (which reads the bundled profile) does no blocking I/O in the loop.
    await hass.async_add_executor_job(load_profile)
    coordinator = DenonAvrCoordinator(hass, entry, host)

    # async_setup starts the telnet transport; if anything after that fails we
    # must stop it, otherwise the reconnecting task would leak (unload is not
    # called for a failed setup and runtime_data is never assigned).
    try:
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()
    except ConnectionError as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(f"Cannot reach Denon AVR at {host}: {err}") from err
    except Exception:
        await coordinator.async_shutdown()
        raise

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _apply_channel_defaults(hass, entry, coordinator)
    # Reload when the options (e.g. sound mode learning) change so they take effect.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """Reload the config entry when its options change."""

    await hass.config_entries.async_reload(entry.entry_id)


def _apply_channel_defaults(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    coordinator: DenonAvrCoordinator,
) -> None:
    """Disable per channel entities for speakers the receiver has not configured.

    Home Assistant keeps entity registry entries across a remove/re-add (keyed by
    unique id), so `entity_registry_enabled_default` cannot retroactively disable
    already registered channels. This runs once (guarded by a stored flag) and
    explicitly disables the non configured channels, while never touching a
    channel again afterwards so the user's manual enable/disable choices stick.
    """

    if entry.options.get(_CHANNELS_INITIALIZED):
        return
    configured = coordinator.device.discovery.configured_channels
    if not configured:
        # Speaker configuration unknown; do not disable anything (safe fallback).
        return

    registry = er.async_get(hass)
    for regentry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if _CHANNEL_MARKER not in regentry.unique_id:
            continue
        code = regentry.unique_id.split(_CHANNEL_MARKER)[-1]
        if code not in configured and regentry.disabled_by is None:
            registry.async_update_entity(
                regentry.entity_id,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, _CHANNELS_INITIALIZED: True}
    )


async def async_unload_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> bool:
    """Unload a config entry and stop the telnet transport."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
