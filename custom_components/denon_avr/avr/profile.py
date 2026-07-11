"""Loader and typed accessor for the external protocol profile.

The profile (protocol_profile.json) is the single place that holds the fixed
Denon telnet wire grammar. This module loads it once and exposes convenient
lookups. It contains engine logic only, never device configuration.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# The profile file lives next to this module inside the avr package.
_PROFILE_PATH = Path(__file__).parent / "protocol_profile.json"


class ControlSpec:
    """Typed view over a single control entry from the profile."""

    def __init__(self, control_id: str, data: dict[str, Any]) -> None:
        self.id = control_id
        self._data = data

    @property
    def kind(self) -> str:
        """The value kind that drives encoding/decoding (power, onoff, ...)."""

        return self._data["kind"]

    @property
    def scope(self) -> str:
        """One of 'core', 'zone2' or 'feature'."""

        return self._data.get("scope", "feature")

    @property
    def group(self) -> str | None:
        """The command family this control belongs to.

        One of the profile's command groups (power, volume, source, surround,
        audyssey, tone, audio, speaker, video, system). Purely a classification
        for organising the growing control set; it does not affect the wire
        protocol.
        """

        return self._data.get("group")

    @property
    def zone(self) -> str | None:
        """The zone this control belongs to, if any."""

        return self._data.get("zone")

    @property
    def feature(self) -> str | None:
        """The receiver FuncName that gates a feature control, if any."""

        return self._data.get("feature")

    @property
    def prefix(self) -> str:
        """The telnet token that prefixes both queries and set commands."""

        return self._data.get("prefix", "")

    @property
    def query(self) -> str | None:
        """The full query string that asks the receiver for the current value."""

        return self._data.get("query")

    @property
    def values(self) -> list[str]:
        """The ordered wire values for an enum control (empty otherwise)."""

        return list(self._data.get("values", []))

    def get(self, key: str, default: Any = None) -> Any:
        """Return a raw profile attribute (for kind specific extras)."""

        return self._data.get(key, default)


class ProtocolProfile:
    """The parsed protocol profile with grammar, controls and introspection."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self.grammar: dict[str, Any] = raw.get("grammar", {})
        self.zones: dict[str, Any] = raw.get("zones", {})
        self.speakers: dict[str, Any] = raw.get("speakers", {})
        self.sound_mode_genre_commands: dict[str, str] = {
            code: value
            for code, value in raw.get("sound_mode_genre_commands", {}).items()
            if code != "doc"
        }
        self.sound_mode_wire_overrides: dict[str, str] = {
            name: value
            for name, value in raw.get("sound_mode_wire_overrides", {}).items()
            if name != "doc"
        }
        self.introspection: dict[str, dict[str, Any]] = raw.get("introspection", {})
        self.readonly: dict[str, dict[str, Any]] = raw.get("readonly", {})
        self.refresh: dict[str, Any] = raw.get("refresh", {})
        # Deviceinfo generation code -> hardware type name (e.g. "avr-x-2016"),
        # matching the naming the official Denon integration reports.
        self.receiver_generations: dict[str, str] = {
            code: value
            for code, value in raw.get("receiver_generations", {}).items()
            if code != "doc"
        }
        self.controls: dict[str, ControlSpec] = {
            control_id: ControlSpec(control_id, data)
            for control_id, data in raw.get("controls", {}).items()
        }

    # Grammar helpers. These read the numeric protocol facts from the profile so
    # that even the reference points are data driven.
    @property
    def line_terminator(self) -> str:
        return self.grammar.get("line_terminator", "\r")

    @property
    def list_terminator(self) -> str:
        return self.grammar.get("list_terminator", "END")

    @property
    def no_signal_token(self) -> str:
        return self.grammar.get("no_signal_token", "NON")

    @property
    def volume_reference(self) -> float:
        return float(self.grammar.get("volume_reference", 80.0))

    @property
    def level_reference(self) -> float:
        return float(self.grammar.get("level_reference", 50.0))

    @property
    def volume_max_fallback(self) -> float:
        return float(self.grammar.get("volume_max_fallback", 98.0))

    @property
    def distance(self) -> dict[str, Any]:
        """Speaker distance grammar (divisor, min, max, step, unit)."""

        return self.grammar.get("distance", {})

    @property
    def crossover(self) -> dict[str, Any]:
        """Speaker crossover grammar (width, unit, discrete Hz value set)."""

        return self.grammar.get("crossover", {})

    def group_channels(self, group: str) -> list[str]:
        """Return the channel codes that make up a speaker group.

        Groups map to a fixed pair/single of channels (protocol grammar). The
        count groups (subwoofer, surround back) can be single or double; both
        possible members are returned so the group can be named/matched whether
        the receiver configured it as one speaker or two.
        """

        groups = self.speakers.get("groups", {})
        if group in groups:
            return list(groups[group])
        count = self.speakers.get("count_groups", {})
        if group in count:
            members = list(count[group].get("single", []))
            for code in count[group].get("double", []):
                if code not in members:
                    members.append(code)
            return members
        return []

    @property
    def refresh_groups(self) -> dict[str, dict[str, Any]]:
        """Coalesced re-query groups keyed by id.

        Each group has 'queries' (commands to re-send), 'on' (line prefixes
        whose arrival schedules the re-query) and 'debounce' (seconds). This
        keeps derived state the receiver does not push on every relevant change
        (audio format, channel/speaker layout, signal-dependent mode lists) in
        sync, fully data driven from the profile's refresh section.
        """

        return self.refresh.get("groups", {})

    def introspection_query(self, key: str) -> str | None:
        """Return the query string for an introspection item, or None."""

        return self.introspection.get(key, {}).get("query")

    def control(self, control_id: str) -> ControlSpec | None:
        """Return a control spec by id, or None when unknown."""

        return self.controls.get(control_id)

    def controls_by_scope(self, scope: str) -> list[ControlSpec]:
        """Return all control specs with the given scope."""

        return [spec for spec in self.controls.values() if spec.scope == scope]

    def controls_by_group(self, group: str) -> list[ControlSpec]:
        """Return all control specs classified under the given command group."""

        return [spec for spec in self.controls.values() if spec.group == group]

    def feature_controls(self) -> list[ControlSpec]:
        """Return all control specs that are gated by a receiver feature."""

        return [spec for spec in self.controls.values() if spec.feature]


@lru_cache(maxsize=1)
def load_profile() -> ProtocolProfile:
    """Load and cache the protocol profile bundled with the package."""

    with _PROFILE_PATH.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return ProtocolProfile(raw)
