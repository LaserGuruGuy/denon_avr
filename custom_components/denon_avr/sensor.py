"""Sensor platform for the Denon AVR integration.

Creates read only sensors from what the receiver reports: the audio input
information (sample rate, decoder, format, signal), the current sound mode, and
the per channel calibration levels. Which sensors exist depends on the device.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfSoundPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .avr import AvrState
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, setup_catalogue_platform
from .helpers import amp_mode, is_preamplifier, subwoofer_setup


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the sensors the entity catalogue lists for this receiver."""

    coordinator = entry.runtime_data
    factories = {
        "readonly": lambda e: DenonAvrReadonlySensor(coordinator, e.params["control_id"]),
        "sound_mode": lambda e: DenonAvrSoundModeSensor(coordinator),
        "volume_db": lambda e: DenonAvrVolumeSensor(coordinator),
        "amp_mode": lambda e: DenonAvrAmpModeSensor(coordinator),
        "subwoofer_setup": lambda e: DenonAvrSubwooferSetupSensor(coordinator, e.params["which"]),
    }
    setup_catalogue_platform(coordinator, "sensor", factories, async_add_entities)


class DenonAvrReadonlySensor(DenonAvrEntity, SensorEntity):
    """A sensor for a single read only audio information field."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DenonAvrCoordinator, control_id: str) -> None:
        super().__init__(coordinator, f"sensor_{control_id}")
        self._control_id = control_id
        self._attr_translation_key = control_id

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.readonly.get(self._control_id)


class DenonAvrSoundModeSensor(DenonAvrEntity, SensorEntity):
    """A diagnostic sensor exposing the current sound mode."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "sensor_sound_mode")
        self._attr_translation_key = "sound_mode"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.values.get("sound_mode")
        return str(value) if value is not None else None


class DenonAvrVolumeSensor(DenonAvrEntity, SensorEntity):
    """Read only master volume in dB (relative to the receiver's 0 dB reference)."""

    _attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "sensor_volume_db")
        self._attr_translation_key = "volume"

    @property
    def native_value(self) -> float | None:
        raw = self.coordinator.data.zone("main").volume_raw
        if raw is None:
            return None
        # Show the level relative to the reference, matching the receiver's
        # relative (dB) display, for example -38.0 dB.
        return round(raw - self.coordinator.device.volume_reference, 1)


class _EnumKeySensor(DenonAvrEntity, SensorEntity):
    """A read-only enum sensor whose state is a stable, translated key.

    The options are the profile's known keys plus, if the receiver reports an
    id the profile does not know yet, that ``mode_<id>`` / ``option_<id>`` key,
    so an unknown value is shown instead of dropped (silence is not absence).
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _known: list[str] = []

    @property
    def options(self) -> list[str]:
        current = self.native_value
        known = list(dict.fromkeys(self._known))
        return known + [current] if current and current not in known else known


class DenonAvrAmpModeSensor(_EnumKeySensor):
    """The amplifier assign mode, incl. 'preamplifier' (the internal amplifiers off,
    the receiver used only as a pre-amplifier). Read-only."""

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "sensor_amp_mode", sub_device="speakers")
        self._attr_translation_key = "amp_mode"
        grammar = coordinator.device.profile.grammar.get("speakers_web", {})
        self._known = list(grammar.get("amp_modes", {}).get("keys", {}).values())

    @property
    def native_value(self) -> str | None:
        return amp_mode(self.coordinator.device)[1]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        mode, _key = amp_mode(self.coordinator.device)
        return {"mode_id": mode, "preamplifier": is_preamplifier(self.coordinator.device)}


class DenonAvrSubwooferSetupSensor(_EnumKeySensor):
    """Web Control subwoofer mode (Standard / Directional) or layout. Unavailable
    while the receiver hides it (e.g. with a single subwoofer); never removed."""

    def __init__(self, coordinator: DenonAvrCoordinator, which: str) -> None:
        super().__init__(coordinator, f"sensor_subwoofer_{which}", sub_device="speakers")
        self._which = which
        self._attr_translation_key = f"subwoofer_{which}"
        spec = coordinator.device.profile.grammar.get("speakers_web", {}).get("subwoofer_setup", {})
        self._known = list(spec.get(f"{which}_keys", {}).values())

    @property
    def available(self) -> bool:
        return super().available and subwoofer_setup(self.coordinator.device, self._which) is not None

    @property
    def native_value(self) -> str | None:
        return subwoofer_setup(self.coordinator.device, self._which)
