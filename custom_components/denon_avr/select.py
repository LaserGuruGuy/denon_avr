"""Select platform for the Denon AVR integration.

One select entity is created per enum control the receiver advertises (for
example Dynamic Compression, Dynamic Volume, MultEQ, Restorer). The option
labels come from the receiver; the wire values come from the profile.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .avr import ControlSpec, eq_channel_options
from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, setup_catalogue_platform
from .helpers import (
    control_sub_device,
    crossover_from_label,
    crossover_label,
    crossover_values,
    current_input_slot,
    option_key,
    enum_options,
    group_name,
    is_size_token,
    subwoofer_count_options,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the select entities the entity catalogue lists for this receiver."""

    coordinator = entry.runtime_data
    factories = {
        "control": lambda e: DenonAvrSelect(coordinator, e.params["spec"]),
        "sound_mode_group": lambda e: DenonAvrSoundModeGroupSelect(coordinator),
        "sound_mode": lambda e: DenonAvrSoundModeSelect(coordinator),
        "quick_select": lambda e: DenonAvrQuickSelect(coordinator),
        "crossover": lambda e: DenonAvrCrossover(coordinator, e.params["group"], e.params["channels"], e.enabled_default),
        "crossover_speaker_selection": lambda e: DenonAvrCrossoverSpeakerSelection(coordinator),
        "crossover_all": lambda e: DenonAvrCrossoverAll(coordinator),
        "speaker_size": lambda e: DenonAvrSpeakerSize(coordinator, e.params["group"], e.params["channels"], e.enabled_default),
        "subwoofer_count": lambda e: DenonAvrSubwooferCount(coordinator),
        "eq_speaker_selection": lambda e: DenonAvrEqSpeakerSelection(coordinator),
        "eq_channel": lambda e: DenonAvrEqChannel(coordinator),
        "speaker_layout_assign": lambda e: DenonAvrSpeakerLayoutAssign(coordinator),
        "input_setting": lambda e: DenonAvrInputSetting(coordinator, e.params["collection"], e.params["slot"]),
        "current_input_setting": lambda e: DenonAvrCurrentInputSetting(coordinator, e.params["collection"]),
        "web_select": lambda e: DenonAvrVideoSelect(
            coordinator,
            e.params["control"],
            controller_attr=e.params["controller_attr"],
            sub_device=e.sub_device,
        ),
    }
    setup_catalogue_platform(coordinator, "select", factories, async_add_entities)


class DenonAvrSelect(DenonAvrEntity, SelectEntity):
    """A select entity backed by a profile enum control."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator, spec: ControlSpec) -> None:
        # Route to a logical sub-device (Audio/Video/Speakers) by command group.
        super().__init__(
            coordinator, f"select_{spec.id}", sub_device=control_sub_device(spec)
        )
        self._spec = spec
        self._attr_translation_key = spec.id
        options, self._label_to_value, self._value_to_label = enum_options(
            coordinator.device.discovery, spec
        )
        self._attr_options = options

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.values.get(self._spec.id)
        if value is None:
            return None
        # Map the wire value back to its display label; return None (rather than
        # an unknown string) when the device reports a value outside our options,
        # so Home Assistant does not log an "invalid current option" warning.
        return self._value_to_label.get(str(value))

    async def async_select_option(self, option: str) -> None:
        value = self._label_to_value.get(option)
        if value is not None:
            await self.coordinator.device.async_set_control(self._spec.id, value)


class DenonAvrSoundModeGroupSelect(DenonAvrEntity, SelectEntity):
    """Selects which sound mode genre group to browse (Movie/Music/Game/Pure).

    The groups and their names come from the receiver. This select follows the
    receiver's active group by default; picking a different group lets the user
    browse that group's modes in the companion sound mode select, without
    changing anything on the receiver until a mode is actually chosen.
    """

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_sound_mode_group")
        self._attr_translation_key = "sound_mode_group"

    @property
    def _genres(self) -> dict[str, str]:
        return self.coordinator.device.discovery.sound_mode_genres

    @property
    def options(self) -> list[str]:
        return list(self._genres.values())

    @property
    def current_option(self) -> str | None:
        # The current group is reported directly by the receiver (SSSMG).
        code = self.coordinator.data.values.get("sound_mode_genre")
        return self._genres.get(code) if code else None

    async def async_select_option(self, option: str) -> None:
        # Selecting a group switches the receiver to that genre; the receiver
        # then reports the resolved mode, which updates the sound mode select.
        for code, display in self._genres.items():
            if display == option:
                await self.coordinator.device.async_select_sound_mode_group(code)
                return


class DenonAvrSoundModeSelect(DenonAvrEntity, SelectEntity):
    """Lists and selects the sound modes within the active genre group.

    The modes come from the receiver (OPSMLALL) for the currently active group.
    Selecting a mode sends the wire token (a deterministic profile override when
    it differs, else the upper-cased name), which may differ from what the
    receiver reports back as the active mode (expected on Denon, for example
    'Dolby Audio - Dolby Surround').
    """

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_sound_mode")
        self._attr_translation_key = "sound_mode"

    @property
    def options(self) -> list[str]:
        # The receiver's current-context list (OPSML) is authoritative: it holds
        # exactly what the active group offers now, including modes OPSMLALL omits
        # such as Auto. Fall back to the static per genre group if unavailable.
        discovery = self.coordinator.device.discovery
        if discovery.current_sound_modes:
            return list(discovery.current_sound_modes)
        code = self.coordinator.data.values.get("sound_mode_genre")
        return discovery.sound_mode_groups.get(code, []) if code else []

    @property
    def current_option(self) -> str | None:
        current = self.coordinator.data.values.get("sound_mode_display")
        return current if current in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.device.async_select_sound_mode(option)


class DenonAvrCrossover(DenonAvrEntity, SelectEntity):
    """A select entity for one speaker group's crossover frequency.

    The allowed frequencies are a discrete, non-uniform Denon protocol set (from
    the profile), so this is a select rather than a stepped number. Values are
    shown with their 'Hz' unit; the receiver reports the current value per group.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        group: str,
        channels: list[str],
        enabled: bool,
    ) -> None:
        super().__init__(
            coordinator, f"select_crossover_{group}", sub_device="speakers"
        )
        self._group = group
        self._attr_entity_registry_enabled_default = enabled
        crossover = coordinator.device.profile.crossover
        self._unit = crossover.get("unit", "Hz")
        self._attr_name = (
            f"{group_name(coordinator.device.discovery, channels)} Crossover"
        )

    def _format(self, hertz: int) -> str:
        return crossover_label(hertz, self._unit)

    @property
    def _values(self) -> list[int]:
        # The receiver's own grid when it reports one (incl. 0 = Full Range),
        # else the profile constant; re-evaluated so it follows the device.
        return crossover_values(self.coordinator.device, self._group)

    @property
    def options(self) -> list[str]:
        return [self._format(v) for v in self._values]

    @property
    def available(self) -> bool:
        # In "All" crossover speaker-selection mode the single All crossover
        # applies, so the per-group ones are inactive.
        return (
            super().available
            and self.coordinator.data.values.get("crossover_speaker_selection") != "ALL"
        )

    @property
    def current_option(self) -> str | None:
        hertz = self.coordinator.data.crossovers.get(self._group)
        # Only report a value the receiver actually offers, so Home Assistant
        # never logs an "invalid current option" warning.
        return self._format(hertz) if hertz in self._values else None

    async def async_select_option(self, option: str) -> None:
        hertz = crossover_from_label(option)
        await self.coordinator.device.async_set_crossover(self._group, hertz)


class DenonAvrCrossoverSpeakerSelection(DenonAvrEntity, SelectEntity):
    """Crossover speaker selection: All (one shared crossover) or Individual.

    This gates the crossover controls: in "All" the single All Crossover applies
    and the per-group crossovers go unavailable; in "Individual" the per-group
    ones apply and All Crossover goes unavailable.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(
            coordinator, "select_crossover_speaker_selection", sub_device="speakers"
        )
        self._attr_translation_key = "crossover_speaker_selection"
        # Wire tokens -> labels come from the profile grammar (kept out of this
        # HA layer like every other enum's tokens).
        self._labels = coordinator.device.profile.crossover.get(
            "speaker_selection", {"IDV": "Individual", "ALL": "All"}
        )
        self._attr_options = list(self._labels.values())

    @property
    def current_option(self) -> str | None:
        return self._labels.get(
            self.coordinator.data.values.get("crossover_speaker_selection")
        )

    async def async_select_option(self, option: str) -> None:
        for token, label in self._labels.items():
            if label == option:
                await self.coordinator.device.async_set_crossover_mode(token)
                return


class DenonAvrCrossoverAll(DenonAvrEntity, SelectEntity):
    """The single 'All' crossover frequency, used in All speaker-selection mode."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_crossover_all", sub_device="speakers")
        self._attr_translation_key = "all_crossover"
        self._unit = coordinator.device.profile.crossover.get("unit", "Hz")

    @property
    def _values(self) -> list[int]:
        return crossover_values(self.coordinator.device)

    @property
    def options(self) -> list[str]:
        return [crossover_label(v, self._unit) for v in self._values]

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data.values.get("crossover_speaker_selection") == "ALL"
        )

    @property
    def current_option(self) -> str | None:
        hertz = self.coordinator.data.values.get("crossover_all")
        return crossover_label(hertz, self._unit) if hertz in self._values else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.device.async_set_crossover("ALL", crossover_from_label(option))


class DenonAvrSpeakerSize(DenonAvrEntity, SelectEntity):
    """A select entity for one speaker group's size (Large/Small).

    The size is a fixed two-value protocol enum (like the receiver's other enum
    controls); the wire tokens and labels come from the profile. The current
    value is read over telnet (SSSPC); groups the receiver reports as absent map
    to no option and are disabled by default.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        group: str,
        channels: list[str],
        enabled: bool,
    ) -> None:
        super().__init__(
            coordinator, f"select_speaker_size_{group}", sub_device="speakers"
        )
        self._group = group
        self._attr_entity_registry_enabled_default = enabled
        sizes = coordinator.device.profile.speakers.get("sizes", {}).get("options", {})
        self._token_to_label = dict(sizes)
        self._label_to_token = {label: token for token, label in sizes.items()}
        self._attr_options = list(sizes.values())
        self._attr_name = (
            f"{group_name(coordinator.device.discovery, channels)} Speaker Size"
        )

    @property
    def available(self) -> bool:
        # Only where the receiver reports a size: newer firmware reports presence
        # (YES/NO) instead, so the entity stays (never removed) but is unavailable.
        token = self.coordinator.data.speaker_sizes.get(self._group)
        return super().available and is_size_token(self.coordinator.device, token)

    @property
    def current_option(self) -> str | None:
        token = self.coordinator.data.speaker_sizes.get(self._group)
        # Absent/count tokens map to no option, so no "invalid option" warning.
        return self._token_to_label.get(token)

    async def async_select_option(self, option: str) -> None:
        token = self._label_to_token.get(option)
        if token:
            await self.coordinator.device.async_set_speaker_size(self._group, token)


class DenonAvrSubwooferCount(DenonAvrEntity, SelectEntity):
    """Number of subwoofers the receiver is configured for.

    A telnet count group in the SSSPC speaker config (SSSPCSWF <n>SP), settable
    like a speaker size. The options follow the receiver: up to the number of
    subwoofers it supports, and "None" with the absent token it uses itself.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_subwoofer_count", sub_device="speakers")
        self._attr_translation_key = "subwoofer_count"
    @property
    def _labels(self) -> dict[str, str]:
        return subwoofer_count_options(self.coordinator.device)

    @property
    def options(self) -> list[str]:
        return list(self._labels.values())

    @property
    def _label_to_token(self) -> dict[str, str]:
        return {v: k for k, v in self._labels.items()}

    @property
    def current_option(self) -> str | None:
        return self._labels.get(self.coordinator.data.speaker_sizes.get("SWF"))

    async def async_select_option(self, option: str) -> None:
        token = self._label_to_token.get(option)
        if token:
            await self.coordinator.device.async_set_speaker_size("SWF", token)


class DenonAvrQuickSelect(DenonAvrEntity, SelectEntity):
    """Recall the receiver's main zone quick select presets.

    The preset names are discovered from the receiver (SSQSNZMA); the currently
    recalled preset is reported by MSQUICK (0 means none/modified).
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_quick_select")
        self._attr_translation_key = "quick_select"

    @property
    def _names(self) -> dict[int, str]:
        return self.coordinator.device.discovery.quick_select_names

    @property
    def options(self) -> list[str]:
        return [name for _, name in sorted(self._names.items())]

    @property
    def current_option(self) -> str | None:
        number = self.coordinator.data.values.get("quick_select")
        if not number:
            return None
        return self._names.get(int(number))

    async def async_select_option(self, option: str) -> None:
        for number, name in self._names.items():
            if name == option:
                await self.coordinator.device.async_select_quick_select(number)
                return


class DenonAvrEqSpeakerSelection(DenonAvrEntity, SelectEntity):
    """Graphic-EQ speaker selection: one curve for all, the L/R pair, or each."""

    _web_backed = True   # setup web interface, not telnet
    _eq_gated = True

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_eq_speaker_selection", sub_device="eq")
        self._attr_translation_key = "eq_speaker_selection"
        # code -> label, from the fixed graphic-EQ grammar.
        self._by_code: dict[str, str] = dict(
            coordinator.device.graphic_eq.grammar.get("speaker_selection", {})
        )
        self._by_label = {label: code for code, label in self._by_code.items()}
        self._attr_options = list(self._by_code.values())

    @property
    def current_option(self) -> str | None:
        code = self.coordinator.device.graphic_eq.state.speaker_selection
        return self._by_code.get(code) if code else None

    async def async_select_option(self, option: str) -> None:
        code = self._by_label.get(option)
        if code:
            await self.coordinator.device.graphic_eq.set_speaker_selection(code)


class DenonAvrEqChannel(DenonAvrEntity, SelectEntity):
    """Graphic-EQ channel being read and adjusted.

    Each channel has its own curve, read via the config API's channel index
    (opt1); switching the channel loads that channel's own bands. The available
    channels and their labels depend on the speaker-selection mode (one shared
    curve in 'All', L/R pairs, or individual channels in 'Each').
    """

    _web_backed = True   # setup web interface, not telnet
    _eq_gated = True

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_eq_channel", sub_device="eq")
        self._attr_translation_key = "eq_channel"
        self._grammar = coordinator.device.graphic_eq.grammar

    def _options(self) -> list[tuple[int, str]]:
        eq = self.coordinator.device.graphic_eq.state
        return eq_channel_options(
            self._grammar, eq.speaker_selection, eq.selectable
        )

    @property
    def options(self) -> list[str]:
        return [label for _, label in self._options()]

    @property
    def current_option(self) -> str | None:
        options = self._options()
        index = self.coordinator.device.graphic_eq.channel
        for i, label in options:
            if i == index:
                return label
        # eq_channel not in the current mode's list (e.g. just after a mode
        # switch): show the first channel rather than an unknown state.
        return options[0][1] if options else None

    async def async_select_option(self, option: str) -> None:
        for index, label in self._options():
            if label == option:
                await self.coordinator.device.graphic_eq.set_channel(index)
                return


class DenonAvrVideoSelect(DenonAvrEntity, SelectEntity):
    """A multi-option video setup item, backed by the /ajax video controller.

    Its options and current value come from the receiver via the config API
    (device.video_config); a grayed-out item (e.g. Pass Through Source until
    HDMI Pass Through is on) reports itself unavailable rather than settable.
    """

    _web_backed = True   # setup web interface, not telnet

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        control: dict,
        controller_attr: str = "video_config",
        sub_device: str = "video",
    ) -> None:
        super().__init__(coordinator, f"select_{control['id']}", sub_device=sub_device)
        self._id = control["id"]
        self._attr_translation_key = control["id"]
        self._controller_attr = controller_attr

    @property
    def _ctrl(self):
        # The /ajax config controller backing this select (video or speakers).
        return getattr(self.coordinator.device, self._controller_attr)

    @property
    def available(self) -> bool:
        return super().available and self._ctrl.available(self._id)

    @property
    def options(self) -> list[str]:
        return list(self._ctrl.options(self._id).values())

    @property
    def current_option(self) -> str | None:
        return self._ctrl.options(self._id).get(self._ctrl.value(self._id))

    async def async_select_option(self, option: str) -> None:
        for wire, label in self._ctrl.options(self._id).items():
            if label == option:
                await self._ctrl.async_set(self._id, wire)
                return


class _CollectionSelect(DenonAvrEntity, SelectEntity):
    """A per-input setting from a setup web collection (e.g. 4K/8K format, HDCP).

    Options are stable keys (translated via ``entity.select.<key>.state``); the
    allowed options come per input from the receiver. Writes go through the
    library, which refuses anything the receiver does not allow for that input.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _web_backed = True

    def __init__(self, coordinator: DenonAvrCoordinator, key: str, collection: str, sub_device: str) -> None:
        super().__init__(coordinator, key, sub_device=sub_device)
        self._collection = collection

    def _slot(self) -> str | None:
        raise NotImplementedError

    def _item(self):
        slot = self._slot()
        if slot is None:
            return None
        return self.coordinator.device.video_config.collection(self._collection).get(slot)

    @property
    def available(self) -> bool:
        item = self._item()
        return super().available and item is not None and item.availability.settable

    @property
    def options(self) -> list[str]:
        item = self._item()
        if item is None:
            return []
        return [option_key(self.coordinator.device, self._collection, o) for o in item.options]

    @property
    def current_option(self) -> str | None:
        item = self._item()
        if item is None or not item.value:
            return None
        key = option_key(self.coordinator.device, self._collection, item.value)
        return key if key in self.options else None

    async def async_select_option(self, option: str) -> None:
        item = self._item()
        wire = None
        if item is not None:
            wire = next((w for w in item.options if option_key(self.coordinator.device, self._collection, w) == option), None)
        accepted = wire is not None and await self.coordinator.device.video_config.async_set_item(
            self._collection, item.slot, wire
        )
        if not accepted:
            # The library refuses what the receiver does not allow for this input
            # now; tell the user instead of failing silently.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="input_option_refused",
                translation_placeholders={"option": option, "input": item.name if item else "?"},
            )


class DenonAvrInputSetting(_CollectionSelect):
    """One input's setting, on the Inputs sub-device. The name follows the input's
    name on the receiver ({input} placeholder), the identity its slot."""

    def __init__(self, coordinator: DenonAvrCoordinator, collection: str, slot: str) -> None:
        super().__init__(coordinator, f"select_input_{slot}_{collection}", collection, "inputs")
        self._slot_id = slot
        self._attr_translation_key = collection
        self._attr_translation_placeholders = {"input": self._input_name()}

    def _slot(self) -> str | None:
        return self._slot_id

    def _input_name(self) -> str:
        item = self._item()
        return item.name if item is not None and item.name else f"#{self._slot_id}"

    def _handle_coordinator_update(self) -> None:
        # Follow a rename on the receiver: assigning the placeholder attribute
        # invalidates Home Assistant's cached entity name.
        name = self._input_name()
        if self._attr_translation_placeholders.get("input") != name:
            self._attr_translation_placeholders = {"input": name}
        super()._handle_coordinator_update()


class DenonAvrCurrentInputSetting(_CollectionSelect):
    """The setting of the input that is playing now (follows the SI push), on Video."""

    def __init__(self, coordinator: DenonAvrCoordinator, collection: str) -> None:
        super().__init__(coordinator, f"select_current_{collection}", collection, "video")
        self._attr_translation_key = f"current_{collection}"

    def _slot(self) -> str | None:
        return current_input_slot(self.coordinator.device, self._collection)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        item = self._item()
        return {"input": item.name if item is not None else None, "slot": item.slot if item is not None else None}


class DenonAvrSpeakerLayoutAssign(DenonAvrEntity, SelectEntity):
    """Speaker layout assign mode (Web Control), STAGED: choosing an option only
    prepares it; nothing is written until "Apply speaker layout" is pressed.

    The options are the modes this receiver allows (its own list), shown with
    the translated amplifier-mode keys. While a change is staged the select
    shows the staged mode and the ``pending`` attribute says so.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _web_backed = True

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_speaker_layout_assign", sub_device="speakers")
        self._attr_translation_key = "speaker_layout_assign"
        self._keys = coordinator.device.profile.grammar.get("speakers_web", {}).get("amp_modes", {}).get("keys", {})

    def _key(self, mode: str) -> str:
        key = self._keys.get(mode, f"mode_{mode}")
        # Two ids with the same key in this receiver's list (e.g. 12 and 18 both
        # "11_1ch"): fall back to the raw id so every option stays distinct.
        item = self._item()
        if item is not None and sum(1 for m in item.options if self._keys.get(m) == key) > 1:
            return f"mode_{mode}"
        return key

    def _item(self):
        return self.coordinator.device.speaker_config.collection("speaker_layout_assign").get("1")

    @property
    def available(self) -> bool:
        item = self._item()
        return super().available and item is not None and item.availability.settable

    @property
    def options(self) -> list[str]:
        item = self._item()
        return [self._key(m) for m in item.options] if item is not None else []

    @property
    def current_option(self) -> str | None:
        item = self._item()
        if item is None:
            return None
        mode = self.coordinator.device.speaker_layout.pending.get("1", item.value)
        key = self._key(mode)
        return key if key in self.options else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        item = self._item()
        pending = self.coordinator.device.speaker_layout.pending.get("1")
        return {
            "pending": pending is not None,
            "receiver_mode": self._key(item.value) if item is not None and item.value else None,
            # After a layout change the room correction (Audyssey / Dirac) must be
            # measured again (owner's manual).
            "remeasure_room_correction": True,
        }

    async def async_select_option(self, option: str) -> None:
        item = self._item()
        mode = next((m for m in (item.options if item else ()) if self._key(m) == option), None)
        if mode is None or not self.coordinator.device.speaker_layout.stage("1", mode):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="speaker_layout_refused",
                translation_placeholders={"option": option},
            )
        # Also the Apply / Discard buttons, whose availability follows the pending change.
        self.coordinator.async_update_listeners()
