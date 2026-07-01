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
        and spec.scope == "feature"
        and discovery.supports(spec.feature or "")
    ]
    async_add_entities(entities)


class DenonAvrSwitch(DenonAvrEntity, SwitchEntity):
    """A switch backed by a profile on/off control."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator, spec: ControlSpec) -> None:
        super().__init__(coordinator, f"switch_{spec.id}")
        self._spec = spec
        self._attr_name = control_name(coordinator.device.discovery, spec)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.values.get(self._spec.id)
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.device.async_set_control(self._spec.id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.device.async_set_control(self._spec.id, False)
