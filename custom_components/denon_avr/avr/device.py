"""DenonAvrDevice: the public facade and composition root of the client library."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

import aiohttp

from .models import AvrState, DeviceInfo, Discovery
from .protocol.profile import ProtocolProfile, load_profile
from .protocol.telnet_parser import TelnetParser
from .services.control import ControlService
from .services.discovery import DiscoveryService
from .services.graphic_eq import GraphicEqController
from .services.runtime import BackgroundTasks, UpdateNotifier
from .services.sync import StateSync
from .services.staged import StagedChanges
from .services.webconfig_doc import WebConfigSectionController
from .transport import (
    GoformClient,
    HeosClient,
    TelnetClient,
    UpnpClient,
    WebConfigClient,
    async_probe,
)


class DenonAvrDevice:
    """Own, dependency light client for a Denon AVR over telnet and HTTP."""

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        self._host = host
        self._profile: ProtocolProfile = load_profile()
        self._discovery = Discovery()
        self._state = AvrState()
        self._available = False
        self._notifier = UpdateNotifier()
        self._tasks = BackgroundTasks()
        notify = self._notifier.schedule
        grammar = self._profile.grammar

        # Transports: one isolated client per wire protocol (see transport/).
        self._goform = GoformClient(session, host)
        self._upnp = UpnpClient(session, host)
        self._webconfig = WebConfigClient(session, host)
        # Web-config subsystems behind the ConfigDocumentPort.
        self._graphic_eq = GraphicEqController(
            self._webconfig,
            grammar.get("graphic_eq", {}),
            lambda: self._discovery.supports("GraphicEQ"),
            notify,
        )
        self._video_config = WebConfigSectionController(self._webconfig, grammar.get("video_web", {}), notify)
        self._speaker_config = WebConfigSectionController(self._webconfig, grammar.get("speakers_web", {}), notify)
        self._speaker_layout = StagedChanges(
            self._speaker_config,
            "speaker_layout_assign",
            self._read_control,
            self.async_set_control,
            lambda control_id: self._state.values.get(control_id),
        )

        # The persistent telnet connection is the CommandSink.
        self._telnet = TelnetClient(
            host,
            on_line=lambda line: self._sync.handle_line(line),
            on_connected=lambda: self._sync.resynchronise(),
            on_availability=self._handle_availability,
        )
        parser = TelnetParser(self._profile, self._discovery)
        self._sync = StateSync(
            self._profile,
            self._discovery,
            self._state,
            parser,
            self._telnet,
            self._tasks,
            notify,
            # Web sections a telnet event may trigger a re-read of (profile 'refresh').
            {
                "video": partial(self._refresh_section, self._video_config),
                "speakers": partial(self._refresh_section, self._speaker_config),
                "graphic_eq": self._graphic_eq.refresh,
            },
        )
        self._control = ControlService(
            self._profile,
            self._discovery,
            self._telnet,
            self._sync.refresh_current_sound_modes,
            self.volume_effective_max,
        )
        self._discovery_service = DiscoveryService(
            host,
            self._profile,
            self._discovery,
            self._state,
            parser,
            self._goform.async_get_device_info,
            self._upnp.async_get_description,
            partial(async_probe, host),
            (self._video_config, self._speaker_config),
        )
        # Auxiliary HEOS channel for network now-playing media and album art.
        self._heos = HeosClient(host, on_update=self._sync.handle_now_playing)

    # Public properties ----------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

    @property
    def identifier(self) -> str:
        """Stable device identifier: MAC, else serial number, else host."""

        device = self._discovery.device
        return device.mac_address or device.serial_number or self._host

    @property
    def discovery(self) -> Discovery:
        return self._discovery

    @property
    def state(self) -> AvrState:
        return self._state

    @property
    def available(self) -> bool:
        return self._available

    @property
    def profile(self) -> ProtocolProfile:
        return self._profile

    # Master volume scale, preferring the values the receiver published in its Volume block and
    # falling back to the protocol defaults from the profile.
    @property
    def volume_reference(self) -> float:
        return self._discovery.volume.get("reference", self._profile.volume_reference)

    @property
    def volume_step(self) -> float:
        return self._discovery.volume.get("step", 0.5)

    @property
    def volume_absolute_max(self) -> float:
        return self._discovery.volume.get("absolute_max", self._profile.volume_max_fallback)

    def volume_effective_max(self, zone_id: str = "main") -> float:
        """The highest reachable raw volume: the receiver limit (MVMAX) or scale max."""

        raw_max = self._state.zone(zone_id).volume_max_raw
        return raw_max if raw_max else self.volume_absolute_max

    @property
    def volume_display(self) -> str:
        """The receiver's volume display mode; defaults to Relative (dB)."""

        return self._state.volume_display or "Relative"

    @property
    def configuration_url(self) -> str | None:
        """The receiver's setup web interface, once located (None before / when absent)."""

        endpoint = self._webconfig.endpoint
        return f"{endpoint.base_url}/" if endpoint is not None else None

    @property
    def web_reachable(self) -> bool | None:
        """Whether the setup web interface answered its last request (None: not tried)."""

        return self._webconfig.reachable

    @property
    def stats(self) -> dict[str, object]:
        """Diagnostics counters of the push/refresh machinery and the web documents (no payloads,
        no host)."""

        return {**self._sync.stats, "web_last_status": dict(self._webconfig.last_status)}

    @property
    def setup_interface(self) -> dict[str, object] | None:
        """How the setup web interface was located (diagnostics; no host)."""

        endpoint = self._webconfig.endpoint
        if endpoint is None:
            return None
        return {"scheme": endpoint.scheme, "port": endpoint.port, "source": endpoint.source}

    @property
    def speaker_layout(self) -> StagedChanges:
        """Staged edits of the Web Control speaker layout assign mode; nothing is written until
        ``async_apply`` (after the user confirmed)."""

        return self._speaker_layout

    @property
    def graphic_eq(self) -> GraphicEqController:
        """The manual graphic-EQ subsystem (read/edit/apply)."""

        return self._graphic_eq

    @property
    def video_config(self) -> WebConfigSectionController:
        """The /ajax video setup subsystem (HDMI setup, OSD, TV/4K format)."""

        return self._video_config

    @property
    def speaker_config(self) -> WebConfigSectionController:
        """The /ajax speaker setup subsystem (amp assign)."""

        return self._speaker_config

    def register_update_callback(self, callback: Callable[[], None]) -> None:
        """Register the single callback invoked on state/availability changes."""

        self._notifier.register(callback)

    # Lifecycle ------------------------------------------------------------

    async def async_fetch_identity(self) -> DeviceInfo:
        """Device identity only (config flow / SSDP); raises ConnectionError."""

        return await self._discovery_service.fetch_identity()

    async def async_discover(self) -> Discovery:
        """Populate the discovery model; raises ConnectionError when unreachable."""

        return await self._discovery_service.discover()

    async def async_start(self) -> None:
        """Start the telnet transport (which triggers the first resync)."""

        await self._telnet.async_start()
        # Best effort; the HEOS channel is auxiliary and self-heals on its own.
        await self._heos.async_start()

    async def async_await_ready(self, timeout: float = 8.0) -> None:
        """Best effort wait until the initial resync has populated state."""

        await self._discovery_service.await_ready(timeout)

    async def async_stop(self) -> None:
        """Stop the transports and cancel any pending notification or task."""

        self._speaker_layout.stop()
        self._notifier.cancel()
        self._tasks.cancel_all()
        await self._telnet.async_stop()
        await self._heos.async_stop()

    async def async_poll(self) -> None:
        """HTTP reconciliation poll, used by the coordinator as a safety net."""

        await self._sync.async_reconcile(
            self._goform.async_get_status,
            lambda: self._telnet.connected,
            # Through the single-flight path, so a poll and an event trigger never read the same
            # section twice.
            tuple(partial(self._sync.refresh_web, s) for s in ("graphic_eq", "video", "speakers")),
        )

    async def _read_control(self, control_id: str) -> object | None:
        """A control's value as the receiver answers it NOW; None without an answer."""

        spec = self._profile.controls.get(control_id)
        if spec is None or not spec.query or not self._telnet.connected:
            return None
        if not await self._sync.async_answer(spec.query, spec.prefix):
            return None
        return self._state.values.get(control_id)

    async def _refresh_section(self, section: WebConfigSectionController) -> None:
        """Re-read a web section; menus that appear become features too."""

        await section.refresh()
        new = section.feature_flags() - self._discovery.features
        if not new:
            return
        self._discovery.features |= new
        for spec in self._profile.feature_controls():
            if spec.feature in new:
                for query in (spec.query, spec.get("list_query"), spec.get("read_query")):
                    if query:
                        await self._telnet.async_send(query)
        self._notifier.schedule()

    def _handle_availability(self, available: bool) -> None:
        self._available = available
        self._notifier.schedule()

    def _resync_queries(self) -> list[str]:
        """The resync query list (kept for the characterization tests)."""

        return self._sync.resync_queries()

    # Control (delegates to the ControlService) ------------------------------

    async def async_media_play(self) -> None:
        await self._heos.async_play()

    async def async_media_pause(self) -> None:
        await self._heos.async_pause()

    async def async_media_stop(self) -> None:
        await self._heos.async_stop_playback()

    async def async_media_next(self) -> None:
        await self._heos.async_next()

    async def async_media_previous(self) -> None:
        await self._heos.async_previous()

    async def async_set_power(self, zone_id: str, on: bool) -> None:
        await self._control.set_power(zone_id, on)

    async def async_set_system_power(self, on: bool) -> None:
        await self._control.set_system_power(on)

    async def async_set_volume_level(self, zone_id: str, level: float) -> None:
        await self._control.set_volume_level(zone_id, level)

    async def async_set_volume_raw(self, zone_id: str, raw: float) -> None:
        await self._control.set_volume_raw(zone_id, raw)

    async def async_volume_step(self, zone_id: str, up: bool) -> None:
        await self._control.volume_step(zone_id, up)

    async def async_set_mute(self, zone_id: str, muted: bool) -> None:
        await self._control.set_mute(zone_id, muted)

    async def async_select_source(self, zone_id: str, name: str) -> None:
        await self._control.select_source(zone_id, name)

    async def async_select_sound_mode(self, name: str) -> None:
        await self._control.select_sound_mode(name)

    async def async_select_quick_select(self, number: int) -> None:
        await self._control.select_quick_select(number)

    async def async_select_sound_mode_group(self, code: str) -> None:
        await self._control.select_sound_mode_group(code)

    async def async_set_control(self, control_id: str, value: object) -> None:
        await self._control.set_control(control_id, value)

    async def async_set_channel_trim(self, code: str, db: float) -> None:
        await self._control.set_channel_trim(code, db)

    async def async_set_channel_distance(self, code: str, meters: float) -> None:
        await self._control.set_channel_distance(code, meters)

    async def async_set_crossover(self, group: str, hertz: int) -> None:
        await self._control.set_crossover(group, hertz)

    async def async_set_crossover_mode(self, mode: str) -> None:
        await self._control.set_crossover_mode(mode)

    async def async_set_speaker_size(self, group: str, token: str) -> None:
        await self._control.set_speaker_size(group, token)
