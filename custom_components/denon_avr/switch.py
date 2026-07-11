"""Switch platform for the Denon AVR integration.

One switch is created per on/off feature control the receiver advertises (for
example Tone Control, Dynamic EQ, Loudness Management, Cinema EQ).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .avr.profile import ControlSpec
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity
from .helpers import control_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a switch for each supported on/off feature control."""

    coordinator = entry.runtime_data
    discovery = coordinator.device.discovery
    entities = [
        DenonAvrSwitch(coordinator, spec)
        for spec in coordinator.device.profile.controls.values()
        if spec.kind == "onoff"
        and (spec.scope != "feature" or discovery.supports(spec.feature or ""))
    ]
    async_add_entities(entities)


class DenonAvrSwitch(DenonAvrEntity, SwitchEntity):
    """A switch backed by a profile on/off control."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator, spec: ControlSpec) -> None:
        # A control may opt into a logical sub-device (e.g. the graphic EQ on/off
        # lives on the EQ sub-device alongside its bands).
        super().__init__(
            coordinator, f"switch_{spec.id}", sub_device=spec.get("subdevice")
        )
        self._spec = spec
        # On a sub-device the receiver's feature name would double the device
        # name (e.g. "Graphic EQ Graphic EQ"), so a sub-device control may give a
        # short profile name (e.g. "Enabled") to read cleanly under its device.
        override = spec.get("name") if spec.get("subdevice") else None
        self._attr_name = override or control_name(coordinator.device.discovery, spec)
        # Optional profile-provided icon (e.g. a speaker/mute icon for main mute).
        icon = spec.get("icon")
        if icon:
            self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        # Treat a not-yet-reported value as off so every toggle renders as a
        # single slider for a consistent look. A switch left at "unknown"
        # otherwise shows as a split on/off pair, which looks inconsistent next
        # to the others. Disconnection is handled by the entity's availability,
        # not here, so this only affects a control the receiver has not (yet)
        # reported a value for (e.g. auto lip sync with no active video source).
        return bool(self.coordinator.data.values.get(self._spec.id))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.device.async_set_control(self._spec.id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.device.async_set_control(self._spec.id, False)
