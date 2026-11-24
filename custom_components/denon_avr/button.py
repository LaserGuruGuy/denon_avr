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
    layout = coordinator.device.speaker_layout
    changes = layout.side_effects
    if not changes or layout.stopped:
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


def _update_kept_issue(hass: HomeAssistant, coordinator: DenonAvrCoordinator, kept: tuple[str, ...]) -> None:
    """A notice about choices a restore left as the user changed them."""

    issue_id = f"speaker_layout_kept_{coordinator.config_entry.entry_id}"
    if not kept:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key="speaker_layout_kept", translation_placeholders={"positions": ", ".join(kept)},
    )


async def _checked(action) -> None:
    """Run a web write; a refused one is an error the user sees."""

    if not await action:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="web_setting_not_applied")


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
        await _checked(self.coordinator.device.graphic_eq.apply())


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
        await _checked(self.coordinator.device.graphic_eq.curve_copy())


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
        await _checked(self.coordinator.device.graphic_eq.set_defaults())


class _SpeakerLayoutButton(DenonAvrEntity, ButtonEntity):
    """Base of the speaker layout buttons: not pressable while one of them works."""

    _attr_entity_category = EntityCategory.CONFIG
    _web_backed = True

    def __init__(self, coordinator: DenonAvrCoordinator, action: str) -> None:
        super().__init__(coordinator, f"button_speaker_layout_{action}", sub_device="speakers")
        self._attr_translation_key = f"speaker_layout_{action}"

    @property
    def _layout(self):
        return self.coordinator.device.speaker_layout

    def _ready(self) -> bool:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return super().available and not self._layout.busy and self._ready()

    async def _run(self, action):
        """Run one operation; entities and the repair notice follow even on an error."""

        self.coordinator.async_update_listeners()
        try:
            return await action
        finally:
            _update_layout_issue(self.hass, self.coordinator)
            self.coordinator.async_update_listeners()

    def _error(self, key: str, **placeholders: str) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders or None
        )


class DenonAvrSpeakerLayoutApply(_SpeakerLayoutButton):
    """Write the staged speaker layout to the receiver (the confirming step).

    Changing the speaker layout re-assigns the amplifier channels; after it the
    receiver's room correction (Audyssey / Dirac) may need a new measurement.
    """

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "apply")

    def _ready(self) -> bool:
        return bool(self._layout.pending)

    async def async_press(self) -> None:
        results = await self._run(self._layout.async_apply())
        if not results or not all(results.values()):
            raise self._error("speaker_layout_not_applied")


class DenonAvrSpeakerLayoutRestore(_SpeakerLayoutButton):
    """Put the speaker setup back as it was before the applied layout(s): assign
    mode, positions and front speaker A/B; choices made after that are kept."""

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "restore")

    def _ready(self) -> bool:
        return self._layout.restorable

    async def async_press(self) -> None:
        result = await self._run(self._layout.async_restore())
        _update_kept_issue(self.hass, self.coordinator, result.newer)
        if result.refused:
            raise self._error(f"speaker_layout_restore_{result.refused}")
        if result.left:
            raise self._error("speaker_layout_not_restored", positions=", ".join(result.left))


class DenonAvrSpeakerLayoutDiscard(_SpeakerLayoutButton):
    """Drop the staged change; without one, keep the applied layout as it is (give up
    the restore point and dismiss the notice). Nothing is sent to the receiver."""

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "discard")

    def _ready(self) -> bool:
        return bool(self._layout.pending or self._layout.restorable)

    async def async_press(self) -> None:
        self._layout.discard()
        _update_layout_issue(self.hass, self.coordinator)
        self.coordinator.async_update_listeners()
