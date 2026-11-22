"""Button platform for the Denon AVR integration.

Momentary actions that do not map to a stateful entity. Currently the manual
graphic-EQ helper on the EQ sub-device: copy the reference (Audyssey / flat)
curve into the manual equaliser as a starting point. Only created when the
receiver advertises a graphic EQ this profile can drive.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, setup_catalogue_platform


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
        "speaker_layout_apply": lambda e: DenonAvrSpeakerLayoutApply(coordinator),
        "speaker_layout_discard": lambda e: DenonAvrSpeakerLayoutDiscard(coordinator),
    }
    setup_catalogue_platform(coordinator, "button", factories, async_add_entities)


class DenonAvrEqApply(DenonAvrEntity, ButtonEntity):
    """Write the edited band curve to the receiver.

    The band sliders stage their values locally (the receiver accepts only a
    full band block, not a single band); pressing this applies the whole curve.
    """

    _web_backed = True   # setup web interface, not telnet
    _eq_gated = True

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:content-save"

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_apply", sub_device="eq")
        self._attr_translation_key = "eq_apply"

    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.apply()


class DenonAvrEqCurveCopy(DenonAvrEntity, ButtonEntity):
    """Copy the reference curve into the manual graphic EQ as a starting point."""

    _web_backed = True   # setup web interface, not telnet
    _eq_gated = True

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:content-copy"

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_curve_copy", sub_device="eq")
        self._attr_translation_key = "eq_copy_curve"

    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.curve_copy()


class DenonAvrEqSetDefaults(DenonAvrEntity, ButtonEntity):
    """Reset the manual graphic EQ to its defaults (a flat curve)."""

    _web_backed = True   # setup web interface, not telnet
    _eq_gated = True

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restore"

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_set_defaults", sub_device="eq")
        self._attr_translation_key = "eq_default"

    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.set_defaults()


class DenonAvrSpeakerLayoutApply(DenonAvrEntity, ButtonEntity):
    """Write the staged speaker layout to the receiver (the confirming step).

    Changing the speaker layout re-assigns the amplifier channels; after it the
    receiver's room correction (Audyssey / Dirac) may need a new measurement.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _web_backed = True

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_speaker_layout_apply", sub_device="speakers")
        self._attr_translation_key = "speaker_layout_apply"

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.device.speaker_layout.pending)

    async def async_press(self) -> None:
        try:
            results = await self.coordinator.device.speaker_layout.async_apply()
        finally:
            self.coordinator.async_update_listeners()
        if not all(results.values()):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="speaker_layout_not_applied")


class DenonAvrSpeakerLayoutDiscard(DenonAvrEntity, ButtonEntity):
    """Drop the staged speaker layout change; nothing is sent to the receiver."""

    _attr_entity_category = EntityCategory.CONFIG
    _web_backed = True

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_speaker_layout_discard", sub_device="speakers")
        self._attr_translation_key = "speaker_layout_discard"

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.device.speaker_layout.pending)

    async def async_press(self) -> None:
        self.coordinator.device.speaker_layout.discard()
        self.coordinator.async_update_listeners()
