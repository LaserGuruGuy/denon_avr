"""Button platform for the Denon AVR integration.

Momentary actions that do not map to a stateful entity. Currently the manual
graphic-EQ helper on the EQ sub-device: copy the reference (Audyssey / flat)
curve into the manual equaliser as a starting point. Only created when the
receiver advertises a graphic EQ this profile can drive.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, catalogue_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the buttons the entity catalogue lists for this receiver."""

    coordinator = entry.runtime_data
    factories = {
        "eq_apply": lambda e: DenonAvrEqApply(coordinator),
        "eq_curve_copy": lambda e: DenonAvrEqCurveCopy(coordinator),
        "eq_set_defaults": lambda e: DenonAvrEqSetDefaults(coordinator),
    }
    async_add_entities(catalogue_entities(coordinator, "button", factories))


class DenonAvrEqApply(DenonAvrEntity, ButtonEntity):
    """Write the edited band curve to the receiver.

    The band sliders stage their values locally (the receiver accepts only a
    full band block, not a single band); pressing this applies the whole curve.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:content-save"

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_apply", sub_device="eq")
        self._attr_translation_key = "eq_apply"

    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.apply()


class DenonAvrEqCurveCopy(DenonAvrEntity, ButtonEntity):
    """Copy the reference curve into the manual graphic EQ as a starting point."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:content-copy"

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_curve_copy", sub_device="eq")
        self._attr_translation_key = "eq_copy_curve"

    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.curve_copy()


class DenonAvrEqSetDefaults(DenonAvrEntity, ButtonEntity):
    """Reset the manual graphic EQ to its defaults (a flat curve)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restore"

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_set_defaults", sub_device="eq")
        self._attr_translation_key = "eq_default"

    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.set_defaults()
