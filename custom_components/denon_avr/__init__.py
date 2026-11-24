"""The Denon AVR integration."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC

from . import catalog
from .avr import load_profile
from .const import CONF_HOST, DOMAIN, PLATFORMS
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import catalogue_entities

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> bool:
    """Set up Denon AVR from a config entry."""

    _clear_layout_issues(hass, entry)

    host = entry.data[CONF_HOST]
    # Warm the protocol profile cache off the event loop so constructing the device (which reads
    # the bundled profile) does no blocking I/O in the loop.
    await hass.async_add_executor_job(load_profile)
    coordinator = DenonAvrCoordinator(hass, entry, host)

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
    registry = er.async_get(hass)
    coordinator.known_keys = _registered_keys(registry, entry, coordinator)
    _register_main_device(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _follow_setup() -> None:
        _add_new_entities(coordinator)
        _enable_used_entities(hass, entry, coordinator)

    _follow_setup()
    entry.async_on_unload(coordinator.async_add_listener(_follow_setup))
    return True


def _register_main_device(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    coordinator: DenonAvrCoordinator,
) -> None:
    """Create/refresh the main receiver device and record its registry id."""

    device = coordinator.device.discovery.device
    connections = set()
    if device.mac_address:
        connections.add((CONNECTION_NETWORK_MAC, device.mac_address))
    entry_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections=connections,
        identifiers={(DOMAIN, coordinator.device.identifier)},
        manufacturer=device.manufacturer or "Denon",
        model=device.model_name,
        name=device.model_name or "Denon AVR",
        sw_version=device.firmware_version,
        hw_version=device.hardware_label,
        serial_number=device.serial_number,
        configuration_url=coordinator.device.configuration_url or f"http://{coordinator.device.host}/",
    )
    coordinator.main_device_id = entry_device.id


def _identifier(coordinator: DenonAvrCoordinator) -> str:
    info = coordinator.device.discovery.device
    return catalog.device_identifier(info.mac_address, info.serial_number, coordinator.device.host)


def _registered_keys(registry: er.EntityRegistry, entry: DenonAvrConfigEntry, coordinator) -> set[str]:
    """Catalogue keys of the entities Home Assistant already has for this entry."""

    prefix = f"{_identifier(coordinator)}_"
    return {
        regentry.unique_id[len(prefix):]
        for regentry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if regentry.unique_id.startswith(prefix)
    }


@callback
def _add_new_entities(coordinator: DenonAvrCoordinator) -> None:
    """Add catalogue entries that appeared after setup (new capability reported)."""

    entries = catalog.build(coordinator.device, coordinator.known_keys)
    for platform, (factories, async_add_entities) in coordinator.platforms.items():
        new = [e for e in entries if e.platform == platform and e.key not in coordinator.created_keys]
        if new:
            _LOGGER.debug("Adding %d new %s entities: %s", len(new), platform, [e.key for e in new])
            async_add_entities(catalogue_entities(coordinator, platform, factories, new))


@callback
def _enable_used_entities(hass: HomeAssistant, entry: DenonAvrConfigEntry, coordinator) -> None:
    """Re-enable entities the integration disabled earlier once the receiver's setup uses them
    (e.g. a second subwoofer switched on)."""

    registry = er.async_get(hass)
    prefix = f"{_identifier(coordinator)}_"
    disabled = {
        regentry.unique_id[len(prefix):]: regentry.entity_id
        for regentry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if regentry.unique_id.startswith(prefix)
        and regentry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    }
    if not disabled:
        return
    entries = catalog.build(coordinator.device, coordinator.known_keys)
    for key in catalog.keys_to_enable(entries, set(disabled), coordinator.device.discovery.configured_channels):
        _LOGGER.info("Enabling %s: the receiver's speaker setup now uses it", disabled[key])
        registry.async_update_entity(disabled[key], disabled_by=None)


def _clear_layout_issues(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """The restore point lives in memory: without it these notices mean nothing."""

    for issue in ("speaker_layout_changed", "speaker_layout_kept"):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")


async def async_unload_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> bool:
    """Unload a config entry and stop the telnet transport."""

    # First stop adding entities: a push during the platform unload must not add to a platform
    # that is being torn down.
    entry.runtime_data.platforms.clear()
    # Then no further layout writes: a running apply or restore stops by itself.
    entry.runtime_data.device.speaker_layout.stop()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        _clear_layout_issues(hass, entry)
        await entry.runtime_data.async_shutdown()
    return unloaded
