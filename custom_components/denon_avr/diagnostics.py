"""Diagnostics support for the Denon AVR integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import DenonAvrConfigEntry

TO_REDACT = {"mac_address", "serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DenonAvrConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    coordinator = entry.runtime_data
    device = coordinator.device
    discovery = device.discovery
    state = device.state

    return {
        "available": device.available,
        "setup_web_interface": device.setup_interface,
        "web_reachable": device.web_reachable,
        "stats": device.stats,
        "entities": {"known": len(coordinator.known_keys), "created": len(coordinator.created_keys)},
        "setup_web": {
            name: {
                "availability": {
                    tag: {"display": av.display, "gray": av.gray}
                    for tag, av in section.availability_map().items()
                },
                "collections": {
                    spec["id"]: {slot: asdict(item) for slot, item in section.collection(spec["id"]).items()}
                    for spec in section.collection_specs()
                },
                "value_lists": {spec["id"]: section.value_list(spec["id"]) for spec in section.value_list_specs()},
            }
            for name, section in (("video", device.video_config), ("speakers", device.speaker_config))
        },
        "device": async_redact_data(asdict(discovery.device), TO_REDACT),
        "features": sorted(discovery.features),
        "zones": [asdict(zone) for zone in discovery.zones],
        "sources": [asdict(source) for source in discovery.sources],
        "channels": [asdict(channel) for channel in discovery.channels],
        "feature_names": discovery.feature_names,
        "option_labels": discovery.option_labels,
        "numeric_meta": discovery.numeric_meta,
        "all_sound_modes": discovery.all_sound_modes,
        "current_sound_modes": discovery.current_sound_modes,
        "state": {
            "system_power": state.system_power,
            "zones": {zone_id: asdict(zone) for zone_id, zone in state.zones.items()},
            "values": state.values,
            "readonly": state.readonly,
            "channel_levels": state.channel_levels,
            "channel_trims": state.channel_trims,
        },
    }
