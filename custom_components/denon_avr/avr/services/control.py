"""Control service: user intents -> telnet commands (built by protocol.commands)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..models import Discovery
from ..protocol import commands
from ..protocol.profile import ProtocolProfile
from .ports import CommandSink


class ControlService:
    """Every setter of the facade. Sends nothing when the profile lacks the grammar."""

    def __init__(
        self,
        profile: ProtocolProfile,
        discovery: Discovery,
        sink: CommandSink,
        after_sound_mode_change: Callable[[], Awaitable[None]],
        effective_max: Callable[[str], float],
        reported: Callable[[str], str | None] = lambda _control_id: None,
    ) -> None:
        self._profile = profile
        self._discovery = discovery
        self._sink = sink
        self._after_sound_mode_change = after_sound_mode_change
        self._effective_max = effective_max
        self._reported = reported

    async def _send(self, command: str | None) -> None:
        if command is not None:
            await self._sink.async_send(command)

    def _prefix(self, zone_id: str) -> str | None:
        zone = next((z for z in self._discovery.zones if z.id == zone_id), None)
        return commands.zone_prefix(self._profile, zone)

    async def set_power(self, zone_id: str, on: bool) -> None:
        await self._send(commands.power(self._profile, self._prefix(zone_id), on))

    async def set_system_power(self, on: bool) -> None:
        await self._send(commands.system_power(self._profile, on))

    def level_to_raw(self, zone_id: str, level: float) -> float:
        """A 0..1 level on the raw volume scale, using the reachable maximum."""

        level = max(0.0, min(1.0, level))
        return round(level * self._effective_max(zone_id) * 2) / 2

    async def set_volume_level(self, zone_id: str, level: float) -> None:
        await self.set_volume_raw(zone_id, self.level_to_raw(zone_id, level))

    async def set_volume_raw(self, zone_id: str, raw: float) -> None:
        raw = max(0.0, min(raw, self._effective_max(zone_id)))
        await self._send(commands.volume(self._profile, self._prefix(zone_id), raw))

    async def volume_step(self, zone_id: str, up: bool) -> None:
        if zone_id != "main":
            return
        await self._send(commands.volume_step(self._profile, up))

    async def set_mute(self, zone_id: str, muted: bool) -> None:
        await self._send(commands.mute(self._profile, self._prefix(zone_id), muted))

    async def select_source(self, zone_id: str, name: str) -> None:
        code = self._discovery.source_code(name) or name
        await self._send(commands.source(self._profile, self._prefix(zone_id), code))

    async def select_sound_mode(self, name: str) -> None:
        command = commands.sound_mode(self._profile, name)
        if command is None:
            return
        await self._send(command)
        await self._after_sound_mode_change()

    async def select_quick_select(self, number: int) -> None:
        await self._send(commands.quick_select(self._profile, number))

    async def select_sound_mode_group(self, code: str) -> None:
        command = commands.sound_mode_group(self._profile, code)
        if command is None:
            return
        await self._send(command)
        await self._after_sound_mode_change()

    async def set_control(self, control_id: str, value: object) -> None:
        await self._send(commands.control(self._profile, control_id, value, self._reported(control_id)))

    async def set_channel_trim(self, code: str, db: float) -> None:
        await self._send(commands.channel_trim(self._profile, code, db))

    async def set_channel_distance(self, code: str, meters: float) -> None:
        await self._send(commands.channel_distance(self._profile, code, meters))

    async def set_crossover(self, group: str, hertz: int) -> None:
        await self._send(commands.crossover(self._profile, group, hertz))

    async def set_crossover_mode(self, mode: str) -> None:
        await self._send(commands.crossover_mode(self._profile, mode))

    async def set_speaker_size(self, group: str, token: str) -> None:
        await self._send(commands.speaker_size(self._profile, group, token))
