"""Diagnostics support for the Denon AVR integration.

Dumps the discovery model and the current state so issues can be debugged
without direct access to the receiver. No credentials are involved; the MAC
address is redacted as a courtesy.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import DenonAvrConfigEntry

TO_REDACT = {"mac_address"}


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
