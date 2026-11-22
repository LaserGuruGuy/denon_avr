"""Telnet event parser.

TelnetParser turns incoming telnet lines into updates of the Discovery (for the
SS introspection responses) and the volatile AvrState. Decoding of values is
driven by the protocol profile so the wire grammar stays in one place.
"""

from __future__ import annotations

import logging
import re

from .codec import decode_centered, decode_half_step
from ..models import (
    AvrState,
    Discovery,
    SourceDescriptor,
)
from .profile import ProtocolProfile

_LOGGER = logging.getLogger(__name__)


class TelnetParser:
    """Stateful parser that applies telnet lines to a Discovery and AvrState."""

    def __init__(self, profile: ProtocolProfile, discovery: Discovery) -> None:
        self._profile = profile
        self._discovery = discovery
        # Accumulators for the OPSML (current context) and OPSMLALL (all groups)
        # responses; each is swapped into discovery when its 'END' line arrives,
        # so the published lists are always a clean, complete single response.
        self._opsml_pending: list[str] = []
        self._opsmlall_pending: list[str] = []
        # Per genre group accumulator (MOV/MUS/GAM/PUR -> modes), rebuilt each
        # OPSMLALL cycle so a signal change cannot leave a group with a mode the
        # receiver no longer offers.
        self._opsmlall_groups_pending: dict[str, list[str]] = {}
        # SSSPC: per group {kind: value} (last value of each kind wins).
        self._spc_kinds: dict[str, dict[str, str]] = {}
        self._spc_last_group: str | None = None
        # Lists that end with an END line and have started but not ended yet
        # (longest prefix first: OPSMLALL before OPSML).
        self._terminated = sorted(
            (spec["prefix"] for spec in profile.introspection.values() if spec.get("terminated") and spec.get("prefix")),
            key=len,
            reverse=True,
        )
        self._open_lists: set[str] = set()
        # Every line prefix this parser models (for "understood" vs "unknown").
        known = {"PW", "ZM", "MV", "MU", "SI", "MS", "SV", "CV", "Z"}
        known.update(spec.prefix for spec in profile.controls.values() if spec.prefix)
        known.update(spec.get("prefix", "") for spec in profile.readonly.values())
        known.update(spec.get("prefix", "") for spec in profile.introspection.values())
        known.discard("")
        self._known_prefixes = tuple(sorted(known, key=len, reverse=True))
        # OPSML / OPSMLALL accumulate (index, name) to judge completeness on END.
        self._opsml_indices: list[int] = []
        self._opsmlall_indices: list[int] = []
        # Precompute the generic feature and read only matchers, longest prefix
        # first so that specific tokens win over shorter ones.
        # Feature controls decode generically from their prefix. Core enum/on-off
        # settings that have no FuncName gate (volume scale/limit, muting level,
        # all-zone stereo, subwoofer mode) decode the same way, so they join the
        # generic matcher instead of each needing a bespoke dispatch branch. The
        # overloaded core controls (power/volume/mute/source/sound mode) keep
        # their explicit handling and are excluded here (kind or zone bound).
        self._feature_matchers = sorted(
            (
                (spec.prefix, spec)
                for spec in profile.controls.values()
                if spec.prefix
                and spec.kind != "channel_set"
                and (
                    spec.scope == "feature"
                    or (
                        spec.scope == "core"
                        and spec.kind in ("enum", "onoff")
                        and not spec.zone
                    )
                )
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        self._readonly_matchers = sorted(
            profile.readonly.items(),
            key=lambda item: len(item[1].get("prefix", "")),
            reverse=True,
        )

    @property
    def open_lists(self) -> frozenset[str]:
        """Prefixes of END-terminated lists that started and have not ended yet."""

        return frozenset(self._open_lists)

    def understands(self, line: str) -> bool:
        """True when this parser models the line's command (whether or not it changed
        anything); False for commands it does not know or deliberately does not model yet."""

        if not line.startswith(self._known_prefixes):
            return False
        # Zone sub-commands (Z2CV..., Z3QUICK1, ...) are deliberately not modelled yet.
        if len(line) > 2 and line[0] == "Z" and line[1].isdigit():
            return not line[2:].startswith(tuple(self._profile.zones.get("sub_commands", [])))
        return True

    def _track_list(self, line: str) -> None:
        for prefix in self._terminated:
            if line.startswith(prefix):
                if line[len(prefix):].strip().startswith(self._profile.list_terminator):
                    self._open_lists.discard(prefix)
                else:
                    self._open_lists.add(prefix)
                return

    def feed(self, line: str, state: AvrState) -> bool:
        """Apply a single telnet line to the state. Return True if it changed."""

        line = line.rstrip("\r\n")
        if not line:
            return False
        self._track_list(line)
        try:
            return self._dispatch(line, state)
        except (ValueError, IndexError) as err:
            _LOGGER.debug("Ignoring unparsable line %r: %s", line, err)
            return False

    # Dispatch order matters because several prefixes overlap. The core zone
    # controls and the overloaded/list style responses are handled explicitly;
    # everything else is decoded generically from the profile.
    def _dispatch(self, line: str, state: AvrState) -> bool:
        if line.startswith("PW"):
            return self._set_system_power(line[2:], state)
        if line.startswith("ZM"):
            on = line[2:].strip() == "ON"
            state.zone("main").power = on
            # Mirror into values so the (optional) main-power switch reads state.
            state.values["main_power"] = on
            return True
        if line.startswith("MVMAX"):
            return self._set_volume_max("main", line[5:], state)
        if line.startswith("MV") and not line.startswith(("MVUP", "MVDOWN")):
            return self._set_volume("main", line[2:], state)
        if line.startswith("MU"):
            on = line[2:].strip() == "ON"
            state.zone("main").muted = on
            # Mirror into values so the (optional) main-mute switch reads state.
            state.values["main_mute"] = on
            return True
        if line.startswith("MSQUICK"):
            # Current main zone quick select preset number (0 = none).
            token = line[7:].strip()
            if token.isdigit():
                state.values["quick_select"] = int(token)
                return True
            return False
        if line.startswith("MS"):
            return self._set_sound_mode(line[2:], state)
        if line.startswith("SI"):
            state.zone("main").source = line[2:].strip()
            return True
        if line.startswith("SV"):
            state.values["video_select"] = line[2:].strip()
            return True
        if line.startswith("SSLEV"):
            return self._set_channel(line[5:], state.channel_levels)
        if line.startswith("SSSDE"):
            return self._set_distance(line[5:], state.channel_distances)
        if line.startswith("SSCFR"):
            return self._set_crossover(line[5:], state)
        if line.startswith("CV"):
            return self._set_channel(line[2:], state.channel_trims)
        if len(line) >= 2 and line[0] == "Z" and line[1].isdigit():
            return self._set_zone(int(line[1]), line[2:], state)
        if line.startswith("SSFUN"):
            return self._set_source_name(line[5:])
        if line.startswith("SSSOD"):
            return self._set_source_visibility(line[5:])
        if line.startswith("SSSMG"):
            # Current sound mode genre group (MOV/MUS/GAM/PUR), reported directly.
            group = line[5:].strip()
            if group and not group.startswith(self._profile.list_terminator):
                state.values["sound_mode_genre"] = group
                return True
            return False
        if line.startswith("SSQSNZMA"):
            return self._set_quick_select_name(line[8:])
        if line.startswith("SSSPC"):
            return self._set_speaker_config(line[5:], state)
        # SSSWM (subwoofer mode), MNZST (all-zone stereo) and SSVCTZMA* (volume
        # scale/limit, muting level) are core settings with no FuncName gate;
        # they now decode through the generic feature matcher below.
        if line.startswith("OPSMLALL"):
            return self._set_sound_mode_list(line[8:], state=state)
        if line.startswith("OPSML"):
            return self._set_current_sound_mode(line[5:], state)

        # Read only audio information.
        for control_id, spec in self._readonly_matchers:
            prefix = spec.get("prefix", "")
            if prefix and line.startswith(prefix):
                return self._set_readonly(control_id, spec, line[len(prefix):], state)

        # Generic feature controls.
        for prefix, spec in self._feature_matchers:
            if line.startswith(prefix):
                remainder = line[len(prefix):]
                # A control with a short, overloaded prefix (e.g. picture mode
                # 'PV', shared with the video-adjust tokens PVBR/PVCN/...) opts
                # into exact matching: it only claims the line when the value is
                # one of its known enum tokens, so it never swallows a sibling.
                if spec.get("exact_enum") and remainder.strip() not in spec.values:
                    continue
                return self._set_feature(spec, remainder, state)

        return False

    def _set_system_power(self, remainder: str, state: AvrState) -> bool:
        state.system_power = remainder.strip() == "ON"
        return True

    def _set_volume(self, zone_id: str, remainder: str, state: AvrState) -> bool:
        value = decode_half_step(remainder)
        if value is None:
            return False
        state.zone(zone_id).volume_raw = value
        return True

    def _set_volume_max(self, zone_id: str, remainder: str, state: AvrState) -> bool:
        value = decode_half_step(remainder)
        if value is None:
            return False
        state.zone(zone_id).volume_max_raw = value
        return True

    def _set_sound_mode(self, remainder: str, state: AvrState) -> bool:
        remainder = remainder.strip()
        # MS is also the prefix for quick select and smart select echoes; those
        # are not sound modes, so ignore them here.
        if remainder.startswith(("QUICK", "SMART")):
            return False
        # This is the active mode's wire token (for example "DOLBY AUDIO-DSUR").
        state.values["sound_mode"] = remainder
        # All Zone Stereo is reflected as a sound mode ("ALL ZONE STEREO"), so
        # mirror it here to give the All Zone Stereo switch a known on/off state
        # (its own MNZST '?' query returns nothing; MS? is answered on resync).
        state.values["all_zone_stereo"] = remainder == "ALL ZONE STEREO"
        return True

    @staticmethod
    def _complete(indices: list[int]) -> bool:
        """A list response is complete when its entry numbers run 1..n without a gap."""

        return bool(indices) and indices == list(range(1, len(indices) + 1))

    @staticmethod
    def _merge(existing: list[str], new: list[str]) -> list[str]:
        """Partial response: keep what we had, add what is new (never drop)."""

        return list(existing) + [name for name in new if name not in existing]

    def _set_sound_mode_list(self, remainder: str, state: AvrState) -> bool:
        """Parse an OPSMLALL entry (all genre groups) into the flat mode set.

        Entries look like 'OPSMLALL MOV071Virtual': a 3 letter genre code, a
        running entry number (07), a one digit flag and the display name. Entry
        number 1 starts a new response. On END the response REPLACES the lists
        only when it is complete (numbers 1..n without gap); a partial one (the
        connection started mid-stream, a line was lost) is MERGED so nothing the
        receiver offers is dropped. The selected mode comes from OPSML.
        """

        remainder = remainder.strip()
        if remainder.startswith(self._profile.list_terminator):
            if self._complete(self._opsmlall_indices):
                self._discovery.all_sound_modes = list(self._opsmlall_pending)
                self._discovery.sound_mode_groups = self._opsmlall_groups_pending
            else:
                self._discovery.all_sound_modes = self._merge(
                    self._discovery.all_sound_modes, self._opsmlall_pending
                )
                groups = {g: list(m) for g, m in self._discovery.sound_mode_groups.items()}
                for genre, modes in self._opsmlall_groups_pending.items():
                    groups[genre] = self._merge(groups.get(genre, []), modes)
                self._discovery.sound_mode_groups = groups
            self._opsmlall_pending = []
            self._opsmlall_groups_pending = {}
            self._opsmlall_indices = []
            return True
        if not remainder:
            return False
        genre = remainder[:3]
        match = re.match(r"^(\d+)(.*)$", remainder[3:])
        if not match:
            return False
        digits, name = match.group(1), match.group(2).strip()
        if not name:
            return False
        index = int(digits[:-1] or 0)
        if index == 1:
            self._opsmlall_pending, self._opsmlall_groups_pending, self._opsmlall_indices = [], {}, []
        self._opsmlall_indices.append(index)
        if name not in self._opsmlall_pending:
            self._opsmlall_pending.append(name)
        modes = self._opsmlall_groups_pending.setdefault(genre, [])
        if name not in modes:
            modes.append(name)
        return True

    def _set_current_sound_mode(self, remainder: str, state: AvrState) -> bool:
        """Parse an OPSML entry (modes selectable in the CURRENT context/group).

        Entries look like '011Direct': a running entry number (01), the
        "selected" flag digit and the name. As for OPSMLALL: entry number 1
        starts a new response; END replaces the list only when it is complete,
        otherwise merges.
        """

        remainder = remainder.strip()
        if remainder.startswith(self._profile.list_terminator):
            if self._complete(self._opsml_indices):
                self._discovery.current_sound_modes = list(self._opsml_pending)
            else:
                self._discovery.current_sound_modes = self._merge(
                    self._discovery.current_sound_modes, self._opsml_pending
                )
            self._opsml_pending = []
            self._opsml_indices = []
            return True
        match = re.match(r"^(\d+)(.*)$", remainder)
        if not match:
            return False
        digits, name = match.group(1), match.group(2).strip()
        if not name:
            return False
        index = int(digits[:-1] or 0)
        if index == 1:
            self._opsml_pending, self._opsml_indices = [], []
        self._opsml_indices.append(index)
        if name not in self._opsml_pending:
            self._opsml_pending.append(name)
        # The trailing digit is the "currently selected" flag; this is the mode
        # the user selected, which can differ from the resolved MS mode.
        if digits and digits[-1] == "1":
            state.values["sound_mode_display"] = name
        return True

    def _set_channel(self, remainder: str, target: dict[str, float]) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder == self._profile.list_terminator:
            return False
        parts = remainder.split()
        if len(parts) != 2:
            return False
        code, raw = parts
        value = decode_half_step(raw)
        if value is None:
            return False
        target[code] = value - self._profile.level_reference
        return True

    def _set_distance(self, remainder: str, target: dict[str, float]) -> bool:
        """Parse an 'SSSDE<CODE> <value>' speaker distance line into `target`.

        The value is the distance times the profile divisor (e.g. '0310' at
        divisor 100 is 3.10 m). The 'STP' unit line and the 'END' terminator are
        ignored.
        """

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        parts = remainder.split()
        if len(parts) != 2:
            return False
        code, raw = parts
        if code == "STP" or not raw.isdigit():
            return False
        divisor = self._profile.distance.get("divisor", 100) or 100
        target[code] = int(raw) / divisor
        return True

    def _set_crossover(self, remainder: str, state: AvrState) -> bool:
        """Parse an 'SSCFR...' crossover line.

        Handles the per group 'SSCFR<GROUP> <Hz>' values (into state.crossovers),
        the speaker-selection mode 'SSCFR IDV'/'SSCFR ALL' and the 'SSCFRALL <Hz>'
        aggregate value (into state.values, driving the crossover speaker-
        selection and All-crossover selects). The 'END' terminator is ignored.
        """

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        parts = remainder.split()
        all_group = self._profile.introspection.get("crossover", {}).get(
            "all_group", "ALL"
        )
        if len(parts) == 1:
            # Speaker-selection mode: IDV (per-group) or ALL (one crossover).
            if parts[0] in ("IDV", "ALL"):
                state.values["crossover_speaker_selection"] = parts[0]
                return True
            return False
        if len(parts) != 2:
            return False
        group, raw = parts
        tokens = self._profile.introspection.get("crossover", {}).get("value_tokens", {})
        if raw in tokens:
            raw = str(tokens[raw])        # e.g. FUL (full range) -> 0, as the web list has it
        if not raw.isdigit():
            return False
        if group == all_group:
            state.values["crossover_all"] = int(raw)
            return True
        state.crossovers[group] = int(raw)
        return True

    def _set_zone(self, index: int, remainder: str, state: AvrState) -> bool:
        remainder = remainder.strip()
        if not remainder:
            return False
        zone = state.zone(f"zone{index}")
        if remainder in ("ON", "OFF"):
            zone.power = remainder == "ON"
            return True
        if remainder.startswith("MU"):
            zone.muted = remainder[2:] == "ON"
            return True
        if remainder.isdigit():
            value = decode_half_step(remainder)
            if value is not None:
                zone.volume_raw = value
                return True
            return False
        # Zone sub-commands (channel volume, tone, sleep, quick select ...) are
        # not a source; they are not modelled yet (profile zones.sub_commands).
        if remainder.startswith(tuple(self._profile.zones.get("sub_commands", []))):
            return False
        # Anything else is the zone's source code.
        zone.source = remainder
        return True

    def _set_source_name(self, remainder: str) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, name = remainder.partition(" ")
        name = name.rstrip() or code
        self._upsert_source(code, name=name)
        return True

    def _set_source_visibility(self, remainder: str) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, flag = remainder.partition(" ")
        visible_token = self._profile.introspection["source_visibility"].get(
            "visible_token", "USE"
        )
        self._upsert_source(code, visible=flag.strip() == visible_token)
        return True

    def _upsert_source(
        self, code: str, *, name: str | None = None, visible: bool | None = None
    ) -> None:
        """Create or update a discovered source, keeping earlier attributes."""

        for source in self._discovery.sources:
            if source.code == code:
                if name is not None:
                    source.name = name
                if visible is not None:
                    source.visible = visible
                return
        self._discovery.sources.append(
            SourceDescriptor(
                code=code,
                name=name if name is not None else code,
                visible=visible if visible is not None else True,
            )
        )

    def _set_quick_select_name(self, remainder: str) -> bool:
        """Parse a 'QS<n> <name>' quick select entry into the discovery model."""

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, name = remainder.partition(" ")
        name = name.strip()
        # code looks like 'QS1'; take the trailing digits as the preset number.
        digits = "".join(ch for ch in code if ch.isdigit())
        if not digits or not name:
            return False
        self._discovery.quick_select_names[int(digits)] = name
        return True

    def _set_speaker_config(self, remainder: str, state: AvrState) -> bool:
        """One SSSPC line: '<GROUP> <value>'. There is no END line on any generation.

        Each value is classified (presence / size / count / other, profile
        ``speakers.tokens``). Consecutive lines of one group form its record
        (e.g. 'SBK NO' + 'SBK 2SP'); a new run for that group replaces the record,
        keeping the count if the run has none. So a push or a repeated response
        overwrites instead of accumulating.

        Silence is not absence: a group the receiver stops mentioning is KEPT. A
        group only becomes absent through an explicit absent token (NO / NON). A
        response can be cut off, or a group can be silent in the current setup,
        and that must never remove a real speaker group.
        """

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        group, _, value = remainder.partition(" ")
        value = value.strip()
        if not group or not value:
            return False
        if group != self._spc_last_group:
            # A new run of lines for this group replaces its record (so 'NON'
            # followed later by '1SP' is not stuck as absent); the count is kept
            # when the new run does not mention one.
            previous = self._spc_kinds.get(group, {})
            self._spc_kinds[group] = {"count": previous["count"]} if "count" in previous else {}
            self._spc_last_group = group
        kinds = self._spc_kinds[group]
        kinds[self._speaker_token_kind(value)] = value
        # discovery.speaker_config keeps the group's current values (one per
        # kind); state.speaker_sizes keeps the latest value for the live selects.
        self._discovery.speaker_config[group] = set(kinds.values())
        state.speaker_sizes[group] = value
        self._recompute_configured_channels()
        return True

    def _speaker_token_kind(self, value: str) -> str:
        tokens = self._profile.speakers.get("tokens", {})
        if value in tokens.get("presence", {}):
            return "presence"
        if value in tokens.get("size", []):
            return "size"
        if re.match(tokens.get("count_pattern", r"^(\d+)SP$"), value):
            return "count"
        return "other"

    def _recompute_configured_channels(self) -> None:
        """Derive the configured channel codes from the SSSPC speaker config.

        A group is present unless an explicit token says absent: its presence line
        (NO on newer firmware) or, without one, its size (NON on older firmware). Count groups contribute the channel list of their
        reported count. The maps come from the profile (protocol grammar); which
        groups are present comes entirely from the receiver.
        """

        speakers = self._profile.speakers
        tokens = speakers.get("tokens", {})
        absent = set(tokens.get("absent", []))
        pattern = tokens.get("count_pattern", r"^(\d+)SP$")
        groups = speakers.get("groups", {})
        count_groups = speakers.get("count_groups", {})

        configured: set[str] = set()
        for group, kinds in self._spc_kinds.items():
            # Absent only by an explicit token: the presence line (YES/NO), or the
            # size when there is no presence line (NON on older firmware).
            if kinds.get("presence", kinds.get("size")) in absent:
                continue
            if group in count_groups:
                by_count = count_groups[group].get("by_count", {})
                match = re.match(pattern, kinds.get("count", ""))
                count = match.group(1) if match else "1"
                configured.update(by_count.get(count, by_count.get("1", [])))
            elif group in groups:
                configured.update(groups[group])
        self._discovery.configured_channels = configured

    def _set_readonly(
        self, control_id: str, spec: dict, remainder: str, state: AvrState
    ) -> bool:
        kind = spec.get("kind", "passthrough")
        if kind == "sample_rate":
            token = remainder.strip()
            state.readonly[control_id] = (
                None if token == self._profile.no_signal_token else token
            )
        elif kind == "trimmed":
            state.readonly[control_id] = remainder.rstrip() or None
        else:
            state.readonly[control_id] = remainder.strip() or None
        return True

    def _set_feature(self, spec, remainder: str, state: AvrState) -> bool:
        kind = spec.kind
        remainder = remainder.strip()
        if kind == "onoff":
            state.values[spec.id] = remainder == spec.get("on", "ON")
        elif kind == "enum":
            state.values[spec.id] = remainder
        elif kind == "level":
            value = decode_half_step(remainder)
            if value is None:
                return False
            state.values[spec.id] = value - self._profile.level_reference
        elif kind == "centered":
            value = decode_centered(remainder, int(spec.get("center", 0)))
            if value is None:
                return False
            state.values[spec.id] = value
        elif kind in ("signed_int", "integer"):
            try:
                state.values[spec.id] = int(remainder)
            except ValueError:
                return False
        elif kind == "minutes":
            off = spec.get("off", "OFF")
            if remainder == off:
                state.values[spec.id] = 0
            elif remainder.isdigit():
                state.values[spec.id] = int(remainder)
            else:
                return False
        else:
            state.values[spec.id] = remainder
        return True
