"""High level device API for the Denon AVR client library.

DenonAvrDevice is the public entry point. It combines the telnet transport, the
HTTP transport, the protocol profile and the parser into a single object that:

* discovers the receiver's identity and capabilities,
* keeps a live AvrState updated from telnet push events,
* resynchronises the full state on every (re)connection,
* exposes high level, profile driven control methods,
* notifies a single callback whenever the state or availability changes.

All command encoding uses the profile, so no wire tokens live in this file.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import aiohttp

from .codec import encode_half_step
from .models import AvrState, Discovery
from .parser import TelnetParser, parse_device_info, parse_upnp_description
from .profile import ProtocolProfile, load_profile
from .transport import (
    GoformClient,
    TelnetClient,
    UpnpClient,
    WebControlClient,
    async_probe,
)

_LOGGER = logging.getLogger(__name__)

# Debounce window for collapsing a burst of telnet lines into one notification.
_UPDATE_DEBOUNCE = 0.1


class DenonAvrDevice:
    """Own, dependency light client for a Denon AVR over telnet and HTTP."""

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        self._host = host
        self._profile: ProtocolProfile = load_profile()
        self._discovery = Discovery()
        self._state = AvrState()
        self._parser = TelnetParser(self._profile, self._discovery)

        # Each wire protocol is a separate, isolated transport (see transport/):
        # goform HTTP for discovery/poll, UPnP for firmware/serial, the web
        # control /ajax read for the crossover set, and telnet for live control.
        self._goform = GoformClient(session, host)
        self._upnp = UpnpClient(session, host)
        self._web = WebControlClient(session, host)
        self._telnet = TelnetClient(
            host,
            on_line=self._handle_line,
            on_connected=self._resynchronise,
            on_availability=self._handle_availability,
        )

        self._update_callback: Callable[[], None] | None = None
        self._update_handle: asyncio.TimerHandle | None = None
        # Strong references to fire and forget tasks (delayed queries, OPSML
        # refreshes). Without these, asyncio only keeps a weak reference and the
        # task can be garbage collected before it runs.
        self._pending_tasks: set[asyncio.Task] = set()
        # Line prefixes that signal an audio format change (decoder, input
        # signal, sample rate, audio format). The sound mode lists are signal
        # dependent, so these trigger a coalesced re-query. Coalescing avoids a
        # query storm when the receiver pushes several of them at once.
        self._refresh_trigger_prefixes = self._profile.sound_mode_refresh_prefixes
        self._mode_refresh_scheduled = False
        self._available = False
        # Adaptive sound mode wire learning. Off by default for predictability;
        # the coordinator sets this from the config entry option. When off, wire
        # tokens are resolved deterministically (profile override, then the
        # upper cased display name).
        self.learning_enabled = False
        # Deviceinfo derived source list, used only if telnet introspection
        # yields no sources (see async_discover / async_await_ready).
        self._fallback_sources: list = []

    # Public properties ----------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

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

    # Master volume scale, preferring the values the receiver published in its
    # Volume block and falling back to the protocol defaults from the profile.
    @property
    def volume_reference(self) -> float:
        return self._discovery.volume.get("reference", self._profile.volume_reference)

    @property
    def volume_step(self) -> float:
        return self._discovery.volume.get("step", 0.5)

    @property
    def volume_absolute_max(self) -> float:
        return self._discovery.volume.get(
            "absolute_max", self._profile.volume_max_fallback
        )

    def volume_effective_max(self, zone_id: str = "main") -> float:
        """The highest reachable raw volume: the receiver limit (MVMAX) or scale max."""

        raw_max = self._state.zone(zone_id).volume_max_raw
        return raw_max if raw_max else self.volume_absolute_max

    @property
    def volume_display(self) -> str:
        """The receiver's volume display mode; defaults to Relative (dB)."""

        return self._state.volume_display or "Relative"

    def register_update_callback(self, callback: Callable[[], None]) -> None:
        """Register the single callback invoked on state/availability changes."""

        self._update_callback = callback

    # Lifecycle ------------------------------------------------------------

    async def async_fetch_identity(self):
        """Fetch just the device identity (model name, MAC) over HTTP.

        Used by the config flow (including SSDP discovery) to validate the
        receiver and obtain the unique id without the heavier telnet probe that
        full discovery performs. Raises ConnectionError when unreachable.
        """

        xml_text = await self._goform.async_get_device_info()
        if xml_text is None:
            raise ConnectionError(f"No Deviceinfo response from {self._host}")
        return parse_device_info(xml_text).device

    async def async_discover(self) -> Discovery:
        """Fetch the Deviceinfo document and populate the discovery model.

        Raises ConnectionError when the receiver cannot be reached, so the
        config flow can report a clear failure.
        """

        xml_text = await self._goform.async_get_device_info()
        if xml_text is None:
            raise ConnectionError(f"No Deviceinfo response from {self._host}")
        # Tell the parser which feature blocks to read titles/labels from, using
        # the feature names the profile associates with its controls.
        feature_names = {
            spec.feature for spec in self._profile.controls.values() if spec.feature
        }
        enum_features = {
            spec.feature
            for spec in self._profile.controls.values()
            if spec.kind == "enum" and spec.feature
        }
        discovered = parse_device_info(
            xml_text,
            feature_names,
            enum_features,
            generations=self._profile.receiver_generations,
        )
        # Copy discovered data into our shared discovery object so the parser and
        # entities keep referencing a single instance.
        self._discovery.device = discovered.device
        # Enrich the device identity with the firmware version and serial number,
        # which the goform document omits but the UPnP description carries.
        await self._async_fetch_version_info()
        self._discovery.features = discovered.features
        self._discovery.channels = discovered.channels
        self._discovery.zones = discovered.zones
        self._discovery.feature_names = discovered.feature_names
        self._discovery.option_labels = discovered.option_labels
        self._discovery.numeric_meta = discovered.numeric_meta
        self._discovery.volume = discovered.volume
        self._discovery.sound_mode_genres = discovered.sound_mode_genres
        # The selectable speaker crossover set is not enumerated by goform or
        # telnet; read it (non-disruptively) from the web control /ajax config.
        # Best effort: if it is unavailable the profile's protocol set is used.
        crossover_values = await self._web.async_get_crossover_values()
        if crossover_values:
            self._discovery.crossover_values = crossover_values
        # The authoritative source list comes from the telnet SSFUN/SSSOD
        # introspection (correct codes, names and visibility). Keep the Deviceinfo
        # source list only as a fallback for models without that introspection.
        self._fallback_sources = discovered.sources
        self._discovery.sources = []
        # Reliably populate the telnet-discovered parts of the model (sources,
        # channels/speaker config, sound modes, quick selects) with a short
        # dedicated telnet session, so entities are built correctly regardless of
        # the persistent connection's later timing.
        await self._async_probe_introspection()
        return self._discovery

    async def _async_fetch_version_info(self) -> None:
        """Read firmware/serial and AIOS module versions from the UPnP document.

        Best effort: the goform Deviceinfo document omits these, but the UPnP
        (AIOS) description carries them. Any failure or missing field is left
        unset rather than guessed.
        """

        xml_text = await self._upnp.async_get_description()
        if not xml_text:
            return
        device = self._discovery.device
        for attr, value in parse_upnp_description(xml_text).items():
            if getattr(device, attr, None) is None:
                setattr(device, attr, value)

    async def _async_probe_introspection(self) -> None:
        """Populate the telnet-discovered model over a short lived connection.

        Separate from the persistent connection so discovery is deterministic at
        setup time. The raw socket handling lives in the telnet transport; this
        just supplies the queries and feeds the replies to the parser, stopping
        once the sources and speaker configuration have arrived.
        """

        queries = [
            spec["query"]
            for spec in self._profile.introspection.values()
            if spec.get("query")
        ]
        await async_probe(
            self._host,
            queries,
            lambda line: self._parser.feed(line, self._state),
            lambda: bool(
                self._discovery.configured_channels and self._discovery.sources
            ),
        )

    async def async_start(self) -> None:
        """Start the telnet transport (which triggers the first resync)."""

        await self._telnet.async_start()

    async def async_await_ready(self, timeout: float = 8.0) -> None:
        """Wait, best effort, until the initial resync has populated state.

        This lets the platforms build the right entities at setup time. We wait
        for the core state, the channel calibration list (SSLEV) AND the speaker
        configuration (SSSPC), because the latter decides which channel entities
        are enabled by default. It never raises; on timeout it returns with
        whatever has arrived so far (falling back to "all channels enabled").
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if (
                self._state.system_power is not None
                and self._state.channel_levels
                and self._discovery.configured_channels
            ):
                break
            await asyncio.sleep(0.1)
        # If the telnet introspection produced no sources, fall back to the
        # Deviceinfo source list so the media player still has a source list.
        if not self._discovery.sources and self._fallback_sources:
            self._discovery.sources = self._fallback_sources

    async def async_stop(self) -> None:
        """Stop the telnet transport and cancel any pending notification."""

        if self._update_handle is not None:
            self._update_handle.cancel()
            self._update_handle = None
        for task in list(self._pending_tasks):
            task.cancel()
        self._pending_tasks.clear()
        await self._telnet.async_stop()

    async def async_poll(self) -> None:
        """HTTP reconciliation poll, used by the coordinator as a safety net.

        When telnet is connected this only confirms reachability. When telnet is
        down it recovers the core zone state so entities do not go blank.
        """

        main_path = self._profile.zones.get(
            "status_lite_main", "/goform/formMainZone_MainZoneXmlStatusLite.xml"
        )
        status = await self._goform.async_get_status(main_path)
        if status is None:
            # HTTP unreachable; rely on the telnet availability signal.
            return
        # The volume display mode (Absolute/Relative) is only exposed over HTTP,
        # so always capture it, even while telnet is the source for everything else.
        display = status.get("volume_display")
        if display is not None and display != self._state.volume_display:
            self._state.volume_display = str(display)
            self._schedule_update()
        if not self._telnet.connected:
            self._apply_http_status("main", status)
            self._schedule_update()

    # Telnet callbacks -----------------------------------------------------

    def _handle_line(self, line: str) -> None:
        """Feed a received telnet line to the parser and notify on change."""

        if self._parser.feed(line, self._state):
            self._schedule_update()
        # The receiver pushes OPSMLALL (all groups) when the sound mode context
        # changes, e.g. after a genre group switch, but never pushes OPSML (the
        # current group's selectable modes). When an OPSMLALL push completes,
        # re-query OPSML so the per group Sound Mode list follows the change.
        if line.startswith("OPSMLALL"):
            remainder = line[len("OPSMLALL") :].strip()
            if remainder.startswith(self._profile.list_terminator):
                self._create_task(self._query_current_sound_modes())
        # An audio format change (new stream/decoder, even on the same source)
        # changes which sound modes the receiver offers. Most changes are pushed
        # by the receiver, but re-query the lists to stay in sync regardless.
        if self._refresh_trigger_prefixes and line.startswith(
            self._refresh_trigger_prefixes
        ):
            self._schedule_mode_list_refresh()

    def _handle_availability(self, available: bool) -> None:
        """React to the telnet link going up or down."""

        self._available = available
        self._schedule_update()

    async def _resynchronise(self) -> None:
        """Query the full state after connecting so nothing is stale."""

        for command in self._resync_queries():
            await self._telnet.async_send(command)

    def _resync_queries(self) -> list[str]:
        """Build the list of query commands used to resynchronise state."""

        queries: list[str] = []

        def add(query: str | None) -> None:
            if query and query not in queries:
                queries.append(query)

        # Introspection first so the source and channel lists (which entities
        # are built from) arrive as early as possible after connecting.
        for spec in self._profile.introspection.values():
            add(spec.get("query"))
        # Core controls (main zone) are always present.
        for spec in self._profile.controls_by_scope("core"):
            add(spec.query)
        # Additional zones use the 'Z<index>' query, one per discovered zone.
        prefix = self._profile.zones.get("additional_prefix", "Z")
        for zone in self._discovery.zones:
            if not zone.is_main:
                add(f"{prefix}{zone.index}?")
        # Read only audio information.
        for spec in self._profile.readonly.values():
            add(spec.get("query"))
        # Feature controls, only those the receiver advertises. Some controls
        # have several queries (a list query and a read query, e.g. channel
        # calibration via SSLEV plus live trims via CV); send all of them.
        for spec in self._profile.feature_controls():
            if spec.feature and self._discovery.supports(spec.feature):
                add(spec.query)
                add(spec.get("list_query"))
                add(spec.get("read_query"))
        return queries

    # Notification ---------------------------------------------------------

    def _schedule_update(self) -> None:
        """Debounce state notifications so a burst becomes a single update."""

        if self._update_callback is None:
            return
        loop = asyncio.get_running_loop()
        if self._update_handle is not None:
            self._update_handle.cancel()
        self._update_handle = loop.call_later(_UPDATE_DEBOUNCE, self._fire_update)

    def _fire_update(self) -> None:
        self._update_handle = None
        if self._update_callback is not None:
            self._update_callback()

    def _apply_http_status(self, zone_id: str, status: dict[str, object]) -> None:
        """Apply a parsed StatusLite snapshot to a zone (fallback path)."""

        zone = self._state.zone(zone_id)
        if "power" in status:
            zone.power = bool(status["power"])
        if "muted" in status:
            zone.muted = bool(status["muted"])
        if "source" in status:
            zone.source = str(status["source"])
        if "volume_db" in status:
            zone.volume_raw = self._profile.volume_reference + float(
                status["volume_db"]  # type: ignore[arg-type]
            )

    # Control methods ------------------------------------------------------

    async def _send(self, command: str) -> None:
        await self._telnet.async_send(command)

    def _zone(self, zone_id: str):
        """Return the discovered ZoneDescriptor for a zone id, if any."""

        for zone in self._discovery.zones:
            if zone.id == zone_id:
                return zone
        return None

    def _additional_zone_prefix(self, zone_id: str) -> str | None:
        """Return the 'Z<index>' prefix for a non main zone, or None."""

        zone = self._zone(zone_id)
        if zone is None or zone.is_main:
            return None
        base = self._profile.zones.get("additional_prefix", "Z")
        return f"{base}{zone.index}"

    async def async_set_power(self, zone_id: str, on: bool) -> None:
        """Turn a zone on or off using that zone's power control."""

        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None:
            spec = self._profile.control("main_power")
            if spec is None:
                return
            token = spec.get("on", "ON") if on else spec.get("off", "OFF")
            await self._send(f"{spec.prefix}{token}")
            return
        zones = self._profile.zones
        token = zones.get("power_on", "ON") if on else zones.get("power_off", "OFF")
        await self._send(f"{prefix}{token}")

    async def async_set_system_power(self, on: bool) -> None:
        """Set the overall system standby power."""

        spec = self._profile.control("system_power")
        if spec is None:
            return
        token = spec.get("on", "ON") if on else spec.get("off", "STANDBY")
        await self._send(f"{spec.prefix}{token}")

    async def async_set_volume_level(self, zone_id: str, level: float) -> None:
        """Set the volume for a zone from a 0..1 level."""

        raw = self._level_to_raw(zone_id, level)
        await self.async_set_volume_raw(zone_id, raw)

    async def async_set_volume_raw(self, zone_id: str, raw: float) -> None:
        """Set a zone's volume to a raw scale value (clamped to the reachable max)."""

        raw = max(0.0, min(raw, self.volume_effective_max(zone_id)))
        encoded = encode_half_step(raw)
        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None:
            spec = self._profile.control("main_volume")
            if spec is not None:
                await self._send(f"{spec.prefix}{encoded}")
        else:
            # Additional zones set the volume through their overloaded prefix.
            await self._send(f"{prefix}{encoded}")

    async def async_volume_step(self, zone_id: str, up: bool) -> None:
        """Nudge the main zone volume up or down by one step."""

        if zone_id != "main":
            return
        spec = self._profile.control("main_volume")
        if spec is None:
            return
        token = spec.get("up") if up else spec.get("down")
        if token:
            await self._send(token)

    async def async_set_mute(self, zone_id: str, muted: bool) -> None:
        """Mute or unmute a zone."""

        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None:
            spec = self._profile.control("main_mute")
            if spec is None:
                return
            token = spec.get("on", "ON") if muted else spec.get("off", "OFF")
            await self._send(f"{spec.prefix}{token}")
        else:
            # Additional zones mute through their overloaded prefix (Z2MUON/OFF).
            zones = self._profile.zones
            mute_token = zones.get("mute_token", "MU")
            on_off = zones.get("mute_on", "ON") if muted else zones.get("mute_off", "OFF")
            await self._send(f"{prefix}{mute_token}{on_off}")

    async def async_select_source(self, zone_id: str, name: str) -> None:
        """Select an input source by its display name."""

        code = self._discovery.source_code(name) or name
        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None:
            spec = self._profile.control("main_source")
            if spec is not None:
                await self._send(f"{spec.prefix}{code}")
        else:
            await self._send(f"{prefix}{code}")

    async def async_select_sound_mode(self, name: str) -> None:
        """Select a surround / sound mode by its display name.

        The wire token is often not the plain display name (for example
        'Dolby Audio - Dolby Surround' is sent as 'DOLBY AUDIO-DSUR'). Use the
        token learned by correlation when known, otherwise fall back to the
        upper cased display name, which works for the simple modes.
        """

        spec = self._profile.control("sound_mode")
        if spec is None:
            return
        await self._send(f"{spec.prefix}{self._resolve_sound_mode_wire(name)}")
        await self._refresh_current_sound_modes()

    def _resolve_sound_mode_wire(self, name: str) -> str:
        """Resolve the MS wire token for a sound mode display name.

        Order: an adaptively learned token (only when learning is enabled), then
        the deterministic profile override, then the upper cased display name.
        """

        if self.learning_enabled:
            learned = self._discovery.sound_mode_wire.get(name)
            if learned:
                return learned
        override = self._profile.sound_mode_wire_overrides.get(name)
        if override:
            return override
        return name.upper()

    def _current_sound_modes_query(self) -> str | None:
        """The OPSML query token from the profile, or None if unavailable."""

        return self._profile.introspection_query("current_sound_modes")

    async def _query_current_sound_modes(self) -> None:
        """Send a single OPSML query to refresh the current-context mode list."""

        query = self._current_sound_modes_query()
        if query:
            await self._send(query)

    async def _query_sound_mode_lists(self) -> None:
        """Re-query both the all groups (OPSMLALL) and current (OPSML) lists."""

        for query in (
            self._profile.introspection_query("sound_mode_list"),
            self._current_sound_modes_query(),
        ):
            if query:
                await self._send(query)

    def _schedule_mode_list_refresh(self) -> None:
        """Coalesce audio format change events into one delayed list re-query.

        The receiver emits several signal events (decoder, sample rate, ...) for
        a single format change; a short debounce collapses them into one refresh
        and lets the receiver settle on the new format before querying.
        """

        if self._mode_refresh_scheduled:
            return
        self._mode_refresh_scheduled = True

        async def _refresh() -> None:
            try:
                await asyncio.sleep(1.0)
                await self._query_sound_mode_lists()
            except asyncio.CancelledError:
                pass
            finally:
                self._mode_refresh_scheduled = False

        self._create_task(_refresh())

    async def _refresh_current_sound_modes(self) -> None:
        """Re-query the current-context mode list after a mode/group change.

        The receiver pushes OPSMLALL (not OPSML) on a change, so the OPSML list
        must be pulled. A query sent immediately can race the receiver still
        switching; the OPSMLALL push handler (see _handle_line) re-queries once
        the switch settles, and a delayed query here is a further safety net.
        Both are best effort.
        """

        query = self._current_sound_modes_query()
        if not query:
            return
        await self._send(query)
        self._schedule_delayed_query(query, 0.8)

    def _create_task(self, coro) -> None:
        """Schedule a fire and forget coroutine, keeping a strong reference.

        asyncio holds only a weak reference to bare tasks, so a task started
        without keeping its reference can be garbage collected before it runs.
        Tracking it here (and discarding on completion) keeps it alive and lets
        async_stop cancel anything still pending.
        """

        task = asyncio.ensure_future(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _schedule_delayed_query(self, query: str, delay: float) -> None:
        """Fire and forget a telnet query after a short delay."""

        async def _later() -> None:
            try:
                await asyncio.sleep(delay)
                await self._send(query)
            except asyncio.CancelledError:
                pass

        self._create_task(_later())

    async def async_select_quick_select(self, number: int) -> None:
        """Recall a main zone quick select preset by its number."""

        await self._send(f"MSQUICK{int(number)}")

    async def async_select_sound_mode_group(self, code: str) -> None:
        """Switch the receiver to a sound mode genre group by its code.

        Uses the genre command from the profile (MOVIE/MUSIC/GAME/PURE DIRECT).
        The receiver then auto resolves the actual mode for the current signal.
        """

        wire = self._profile.sound_mode_genre_commands.get(code)
        spec = self._profile.control("sound_mode")
        if wire and spec is not None:
            await self._send(f"{spec.prefix}{wire}")
            await self._refresh_current_sound_modes()

    async def async_set_control(self, control_id: str, value: object) -> None:
        """Set any profile driven feature control to a new value.

        The `value` interpretation depends on the control kind: a bool for
        on/off, a wire token for enum, a dB offset for level, an int for
        signed_int, and minutes (0 = off) for minutes.
        """

        spec = self._profile.control(control_id)
        if spec is None:
            return
        argument = self._encode_control_value(spec, value)
        if argument is None:
            return
        await self._send(f"{spec.prefix}{argument}")

    async def async_set_channel_trim(self, code: str, db: float) -> None:
        """Set a per channel volume trim in dB via the CV command."""

        spec = self._profile.control("channel_level")
        if spec is None:
            return
        raw = self._profile.level_reference + db
        set_prefix = spec.get("set_prefix", "CV")
        await self._send(f"{set_prefix}{code} {encode_half_step(raw)}")

    async def async_set_channel_distance(self, code: str, meters: float) -> None:
        """Set a per channel speaker distance via the SSSDE command."""

        spec = self._profile.introspection.get("channel_distances", {})
        set_prefix = spec.get("set_prefix") or spec.get("prefix") or "SSSDE"
        divisor = self._profile.distance.get("divisor", 100) or 100
        raw = max(0, int(round(meters * divisor)))
        await self._send(f"{set_prefix}{code} {raw:04d}")

    async def async_set_crossover(self, group: str, hertz: int) -> None:
        """Set a speaker group's crossover frequency via the SSCFR command.

        The frequency is zero padded to the protocol width (e.g. 80 -> '080').
        The receiver only accepts values from its discrete grid; sending one off
        the grid is silently ignored, so callers pass a value from the profile's
        crossover set.
        """

        spec = self._profile.introspection.get("crossover", {})
        set_prefix = spec.get("set_prefix") or spec.get("prefix") or "SSCFR"
        width = int(self._profile.crossover.get("width", 3) or 3)
        await self._send(f"{set_prefix}{group} {int(hertz):0{width}d}")

    # Encoding helpers -----------------------------------------------------

    def _level_to_raw(self, zone_id: str, level: float) -> float:
        """Convert a 0..1 level to the raw volume scale using the reported max."""

        level = max(0.0, min(1.0, level))
        ceiling = self.volume_effective_max(zone_id)
        return round(level * ceiling * 2) / 2

    def _encode_control_value(self, spec, value: object) -> str | None:
        """Encode a Python value into the wire argument for a control."""

        kind = spec.kind
        if kind == "onoff":
            return spec.get("on", "ON") if value else spec.get("off", "OFF")
        if kind == "enum":
            return str(value)
        if kind == "level":
            raw = self._profile.level_reference + float(value)  # type: ignore[arg-type]
            return encode_half_step(raw)
        if kind == "signed_int":
            return str(int(value))  # type: ignore[arg-type]
        if kind == "integer":
            # Zero pad to the wire width the receiver expects (e.g. 3 for PSDELAY).
            width = int(spec.get("width", 0) or 0)
            return str(int(value)).zfill(width)  # type: ignore[arg-type]
        if kind == "minutes":
            minutes = int(value)  # type: ignore[arg-type]
            if minutes <= 0:
                return spec.get("off", "OFF")
            return f"{minutes:03d}"
        return str(value)
