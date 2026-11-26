"""Sensor platform for the Denon AVR integration."""

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

from .avr import ChannelMap, decode_channels, AvrState
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
        "speaker_connection": lambda e: DenonAvrSpeakerConnectionSensor(coordinator, e.params["slot"]),
        "readonly": lambda e: _readonly_class(coordinator.device.profile.readonly[e.params["control_id"]])(
            coordinator, e.params["control_id"]
        ),
        "sound_mode": lambda e: DenonAvrSoundModeSensor(coordinator),
        "volume_db": lambda e: DenonAvrVolumeSensor(coordinator),
        "amp_mode": lambda e: DenonAvrAmpModeSensor(coordinator),
        "subwoofer_setup": lambda e: DenonAvrSubwooferSetupSensor(coordinator, e.params["which"]),
    }
    setup_catalogue_platform(coordinator, "sensor", factories, async_add_entities)


def _readonly_class(spec: dict) -> type:
    """The sensor class for a read only field, by the kind of value it carries."""

    if spec.get("kind") == "channels":
        return DenonAvrChannelsSensor
    return DenonAvrCodeSensor if spec.get("value_keys") else DenonAvrReadonlySensor


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
        # Show the level relative to the reference, matching the receiver's relative (dB) display,
        # for example -38.0 dB.
        return round(raw - self.coordinator.device.volume_reference, 1)


class _EnumKeySensor(DenonAvrEntity, SensorEntity):
    """A read-only enum sensor whose state is a stable, translated key."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _known: list[str] = []

    @property
    def options(self) -> list[str]:
        current = self.native_value
        known = list(dict.fromkeys(self._known))
        return known + [current] if current and current not in known else known


class DenonAvrCodeSensor(_EnumKeySensor):
    """A read only field the receiver reports as a code."""

    def __init__(self, coordinator: DenonAvrCoordinator, control_id: str) -> None:
        super().__init__(coordinator, f"sensor_{control_id}")
        self._control_id = control_id
        self._attr_translation_key = control_id
        spec = coordinator.device.profile.readonly[control_id]
        self._keys: dict[str, str] = spec["value_keys"]
        self._unknown: str = spec.get("unknown_key", "code")
        self._known = list(self._keys.values())

    @property
    def _code(self) -> str | None:
        return self.coordinator.data.readonly.get(self._control_id)

    @property
    def native_value(self) -> str | None:
        code = self._code
        return self._keys.get(code, f"{self._unknown}_{code}") if code else None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"code": self._code}


class DenonAvrChannelsSensor(DenonAvrEntity, SensorEntity):
    """The active channels of a channel grid as a layout, e.g. 5.1 or 5.1.4."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DenonAvrCoordinator, control_id: str) -> None:
        super().__init__(coordinator, f"sensor_{control_id}")
        self._control_id = control_id
        self._attr_translation_key = control_id
        self._spec = coordinator.device.profile.readonly[control_id]

    def _map(self) -> ChannelMap:
        return decode_channels(self.coordinator.data.readonly.get(self._control_id), self._spec)

    @property
    def native_value(self) -> str | None:
        return self._map().layout

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        return {"channels": list(self._map().active)}


class DenonAvrAmpModeSensor(_EnumKeySensor):
    """The amplifier assign mode."""

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
    """Web Control subwoofer mode (Standard / Directional) or layout."""

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


class DenonAvrSpeakerConnectionSensor(_EnumKeySensor):
    """How a speaker position is connected now: speakers and pre-out, or pre-out only."""

    _web_backed = True   # setup web interface, not telnet

    def __init__(self, coordinator: DenonAvrCoordinator, slot: str) -> None:
        super().__init__(coordinator, f"sensor_connection_{slot}", sub_device="speakers")
        self._slot = slot
        spec = coordinator.device.speaker_config.collection_spec("speaker_connection") or {}
        self._keys: dict[str, str] = spec.get("value_keys", {})
        self._known = list(self._keys.values())
        self._attr_translation_key = f"speaker_connection_{spec['position_keys'][slot]}"

    def _item(self):
        return self.coordinator.device.speaker_config.collection("speaker_connection").get(self._slot)

    @property
    def available(self) -> bool:
        item = self._item()
        return super().available and item is not None and item.availability.present

    @property
    def native_value(self) -> str | None:
        item = self._item()
        return self._keys.get(item.value, f"option_{item.value}") if item is not None and item.value else None
