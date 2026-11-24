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
from homeassistant.helpers import issue_registry as ir
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
        "speaker_layout_restore": lambda e: DenonAvrSpeakerLayoutRestore(coordinator),
        "speaker_layout_discard": lambda e: DenonAvrSpeakerLayoutDiscard(coordinator),
    }
    setup_catalogue_platform(coordinator, "button", factories, async_add_entities)


def _update_layout_issue(hass: HomeAssistant, coordinator: DenonAvrCoordinator) -> None:
    """A repair notice while an applied layout changed other choices as well."""

    issue_id = f"speaker_layout_changed_{coordinator.config_entry.entry_id}"
    changes = coordinator.device.speaker_layout.side_effects
    if not changes:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="speaker_layout_changed",
        translation_placeholders={"positions": ", ".join(changes)},
    )


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
        _update_layout_issue(self.hass, self.coordinator)
        if not all(results.values()):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="speaker_layout_not_applied")


class DenonAvrSpeakerLayoutRestore(DenonAvrEntity, ButtonEntity):
    """Put the speaker setup back as it was before the last applied layout(s):
    assign mode, positions and front speaker A/B."""

    _attr_entity_category = EntityCategory.CONFIG
    _web_backed = True

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_speaker_layout_restore", sub_device="speakers")
        self._attr_translation_key = "speaker_layout_restore"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.device.speaker_layout.restorable

    async def async_press(self) -> None:
        try:
            left = await self.coordinator.device.speaker_layout.async_restore()
        finally:
            self.coordinator.async_update_listeners()
        _update_layout_issue(self.hass, self.coordinator)
        if left:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="speaker_layout_not_restored",
                translation_placeholders={"positions": ", ".join(left)},
            )


class DenonAvrSpeakerLayoutDiscard(DenonAvrEntity, ButtonEntity):
    """Drop the staged speaker layout change (or dismiss the notice about changed
    positions); nothing is sent to the receiver."""

    _attr_entity_category = EntityCategory.CONFIG
    _web_backed = True

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_speaker_layout_discard", sub_device="speakers")
        self._attr_translation_key = "speaker_layout_discard"

    @property
    def available(self) -> bool:
        layout = self.coordinator.device.speaker_layout
        return super().available and bool(layout.pending or layout.restorable)

    async def async_press(self) -> None:
        self.coordinator.device.speaker_layout.discard()
        _update_layout_issue(self.hass, self.coordinator)
        self.coordinator.async_update_listeners()
