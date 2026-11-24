"""Switch platform for the Denon AVR integration.

One switch is created per on/off feature control the receiver advertises (for
example Tone Control, Dynamic EQ, Loudness Management, Cinema EQ).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .avr import ControlSpec
from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, setup_catalogue_platform
from .helpers import control_sub_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the switches the entity catalogue lists for this receiver."""

    coordinator = entry.runtime_data
    factories = {
        "control": lambda e: DenonAvrSwitch(coordinator, e.params["spec"]),
        "web_switch": lambda e: DenonAvrVideoSwitch(coordinator, e.params["control"]),
    }
    setup_catalogue_platform(coordinator, "switch", factories, async_add_entities)


class DenonAvrSwitch(DenonAvrEntity, SwitchEntity):
    """A switch backed by a profile on/off control."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator, spec: ControlSpec) -> None:
        # Route to a logical sub-device (Audio/Video/Speakers/EQ) based on the
        # control's command group, honouring any explicit profile override.
        sub_device = control_sub_device(spec)
        super().__init__(coordinator, f"switch_{spec.id}", sub_device=sub_device)
        self._spec = spec
        self._attr_translation_key = spec.id
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


class DenonAvrVideoSwitch(DenonAvrEntity, SwitchEntity):
    """An on/off video setup item, backed by the /ajax video controller.

    Its state comes from the receiver via the config API (device.video_config);
    a grayed-out item (e.g. ARC until HDMI Control is on) reports itself
    unavailable rather than settable.
    """

    _web_backed = True   # setup web interface, not telnet

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator, control: dict) -> None:
        super().__init__(coordinator, f"switch_{control['id']}", sub_device="video")
        self._id = control["id"]
        self._on = control["on"]
        self._off = control["off"]
        self._attr_translation_key = control["id"]

    @property
    def _video(self):
        return self.coordinator.device.video_config

    @property
    def available(self) -> bool:
        return super().available and self._video.available(self._id)

    @property
    def is_on(self) -> bool:
        return self._video.value(self._id) == self._on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(self._on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(self._off)

    async def _set(self, wire: str) -> None:
        if not await self._video.async_set(self._id, wire):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="web_setting_not_applied")
