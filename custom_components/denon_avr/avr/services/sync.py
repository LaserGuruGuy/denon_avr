"""State synchronisation: telnet events -> AvrState, resync, refresh groups, HTTP reconcile."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping

from ..models import AvrState, Discovery, NowPlaying
from ..protocol import commands
from ..protocol.profile import ProtocolProfile
from ..protocol.telnet_parser import TelnetParser
from .ports import CommandSink
from .runtime import BackgroundTasks

# Delay of the safety-net OPSML re-query after a sound mode change.
_SOUND_MODES_RECHECK = 0.8

_LOGGER = logging.getLogger(__name__)

MAX_RUNS = 15
RUN_WINDOW = 60.0


class StateSync:
    """Keeps AvrState current from telnet push events and periodic HTTP reconciliation."""

    def __init__(
        self,
        profile: ProtocolProfile,
        discovery: Discovery,
        state: AvrState,
        parser: TelnetParser,
        sink: CommandSink,
        tasks: BackgroundTasks,
        notify: Callable[[], None],
        web_refreshers: Mapping[str, Callable[[], Awaitable[None]]] | None = None,
    ) -> None:
        self._profile = profile
        self._discovery = discovery
        self._state = state
        self._parser = parser
        self._sink = sink
        self._tasks = tasks
        self._notify_hook = notify
        # Data driven coalesced re-query groups (profile 'refresh' section).
        self._refresh_groups = profile.refresh_groups
        self._refresh_scheduled: set[str] = set()
        # Web documents have no push: groups may name web sections ("web") to re-read when a
        # related telnet event arrives.
        self._web_refreshers = dict(web_refreshers or {})
        self._web_running: set[str] = set()
        self._web_dirty: set[str] = set()
        self._seen_triggers: set[str] = set()
        self._runs: dict[str, deque[float]] = {}
        self._braked: set[str] = set()
        self._unmodelled: dict[tuple[str, str], str] = {}
        self._again: dict[str, asyncio.TimerHandle] = {}
        # Fingerprint of what entities can see; a pushed line that leaves it unchanged (the
        # periodic signal cluster, list lines before END) does not notify Home Assistant.
        self._fingerprint = self._snapshot()
        self._expected: list[tuple[str, asyncio.Future]] = []   # awaited answers
        # Diagnostics counters (since start): what the push/refresh machinery did.
        self.stats: dict[str, object] = {
            "lines": 0, "lines_unknown": 0, "notifies": 0, "polls": 0,
            "refresh_groups": {}, "web_refreshes": {},
        }

    async def async_answer(self, query: str, prefix: str, timeout: float = 2.0) -> bool:
        """Send a query and wait for a line with this prefix; False on silence."""

        entry = (prefix, asyncio.get_running_loop().create_future())
        self._expected.append(entry)
        try:
            await self._sink.async_send(query)
            await asyncio.wait_for(entry[1], timeout)
        except (TimeoutError, OSError):
            return False
        finally:
            self._expected.remove(entry)
        return True

    def _notify(self) -> None:
        self.stats["notifies"] += 1
        self._notify_hook()

    def _snapshot(self) -> str:
        return repr((self._state, self._discovery))

    # Telnet events ---------------------------------------------------------

    def handle_line(self, line: str) -> None:
        """Feed a received telnet line to the parser and notify on change."""

        for prefix, future in self._expected:
            if line.startswith(prefix) and not future.done():
                future.set_result(None)
        changed = False
        understood = self._parser.understands(line)
        self.stats["lines"] += 1
        if not understood:
            self.stats["lines_unknown"] += 1
        if self._parser.feed(line, self._state):
            snapshot = self._snapshot()
            if snapshot != self._fingerprint:
                self._fingerprint = snapshot
                changed = True
                self._notify()
        # The receiver pushes OPSMLALL (all groups) when the sound mode context changes, but never
        # pushes OPSML (the current group's selectable modes).
        all_modes = self._profile.introspection_prefix("sound_mode_list")
        if all_modes and line.startswith(all_modes):
            remainder = line[len(all_modes) :].strip()
            if remainder.startswith(self._profile.list_terminator):
                self._tasks.create(self.query_current_sound_modes())
        # The first modelled line of a prefix answers our own resync.
        hits = {
            group_id: line.partition(" ")[0] if group.get("per_item") else prefix
            for group_id, group in self._refresh_groups.items()
            if (prefix := next((p for p in group.get("on") or () if line.startswith(p)), None)) is not None
        }
        fresh = {prefix for prefix in hits.values() if prefix not in self._seen_triggers}
        self._seen_triggers |= fresh
        if understood and not changed:
            return
        for group_id, prefix in hits.items():
            if understood:
                due = prefix not in fresh
            else:
                # Repeated text is no news: it may answer the group's own query.
                due = self._unmodelled.get((group_id, prefix)) != line
                self._unmodelled[(group_id, prefix)] = line
            if due:
                self._schedule_refresh(group_id)

    def handle_now_playing(self, snapshot: dict) -> None:
        self._state.now_playing = NowPlaying(**snapshot)
        fingerprint = self._snapshot()
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self._notify()

    # Resync ----------------------------------------------------------------

    def resync_queries(self) -> list[str]:
        return commands.resync_queries(self._profile, self._discovery)

    async def resynchronise(self) -> None:
        """Query the full state after connecting so nothing is stale."""

        for command in self.resync_queries():
            await self._sink.async_send(command)

    # Sound mode lists --------------------------------------------------------

    async def query_current_sound_modes(self) -> None:
        query = self._profile.introspection_query("current_sound_modes")
        if query:
            await self._sink.async_send(query)

    async def refresh_current_sound_modes(self) -> None:
        """Re-query the current-context mode list after a mode/group change."""

        query = self._profile.introspection_query("current_sound_modes")
        if not query:
            return
        await self._sink.async_send(query)
        self._schedule_delayed_query(query, _SOUND_MODES_RECHECK)

    def _schedule_delayed_query(self, query: str, delay: float) -> None:
        async def _later() -> None:
            try:
                await asyncio.sleep(delay)
                await self._sink.async_send(query)
            except asyncio.CancelledError:
                pass

        self._tasks.create(_later())

    def _schedule_refresh(self, group_id: str) -> None:
        """Coalesce a refresh group's re-query into one delayed send."""

        if group_id in self._refresh_scheduled:
            return
        group = self._refresh_groups.get(group_id)
        if not group:
            return
        if self._too_often(group_id):
            return
        self._refresh_scheduled.add(group_id)
        groups = self.stats["refresh_groups"]
        groups[group_id] = groups.get(group_id, 0) + 1
        queries = list(group.get("queries", []))
        web_sections = [w for w in group.get("web", []) if w in self._web_refreshers]
        debounce = float(group.get("debounce", 1.0))
        again = float(group.get("again", 0))

        async def _refresh() -> None:
            try:
                await asyncio.sleep(debounce)
                for query in queries:
                    await self._sink.async_send(query)
                for section in web_sections:
                    await self.refresh_web(section)
                if again:
                    self._schedule_again(group_id, queries, again)
            except asyncio.CancelledError:
                pass
            finally:
                self._refresh_scheduled.discard(group_id)

        self._tasks.create(_refresh())

    def _too_often(self, group_id: str) -> bool:
        """Brake for a group that keeps triggering itself."""

        now = asyncio.get_running_loop().time()
        runs = self._runs.setdefault(group_id, deque(maxlen=MAX_RUNS))
        if len(runs) == MAX_RUNS and now - runs[0] < RUN_WINDOW:
            if group_id not in self._braked:
                self._braked.add(group_id)
                _LOGGER.warning("Refresh group %s runs too often; slowing it down", group_id)
            return True
        runs.append(now)
        return False

    def _schedule_again(self, group_id: str, queries: list[str], delay: float) -> None:
        """One more read after a handshake; a newer trigger restarts the wait."""

        if (timer := self._again.pop(group_id, None)) is not None:
            timer.cancel()

        async def _send() -> None:
            for query in queries:
                await self._sink.async_send(query)

        def _fire() -> None:
            self._again.pop(group_id, None)
            self._tasks.create(_send())

        self._again[group_id] = asyncio.get_running_loop().call_later(delay, _fire)

    def cancel(self) -> None:
        """Drop the pending second reads (unload)."""

        for timer in self._again.values():
            timer.cancel()
        self._again.clear()

    async def refresh_web(self, section: str) -> None:
        """Re-read one web section now; coalesces triggers that arrive meanwhile."""

        refresh = self._web_refreshers.get(section)
        if refresh is None:
            return
        if section in self._web_running:
            self._web_dirty.add(section)
            return
        self._web_running.add(section)
        web = self.stats["web_refreshes"]
        web[section] = web.get(section, 0) + 1
        try:
            while True:
                self._web_dirty.discard(section)
                await refresh()
                if section not in self._web_dirty:
                    break
        finally:
            self._web_running.discard(section)

    # HTTP reconciliation ----------------------------------------------------

    async def async_reconcile(
        self,
        fetch_status: Callable[[str], Awaitable[dict[str, object] | None]],
        telnet_connected: Callable[[], bool],
        refreshables: Iterable[Callable[[], Awaitable[None]]],
    ) -> None:
        """Reconcile over HTTP; also asks what telnet does not push."""

        self.stats["polls"] += 1
        main_path = self._profile.zones.get(
            "status_lite_main", "/goform/formMainZone_MainZoneXmlStatusLite.xml"
        )
        if telnet_connected() and self._state.zone("main").power:
            for query in self._profile.poll_queries:
                await self._sink.async_send(query)
        status = await fetch_status(main_path)
        if status is None:
            for refresh in refreshables:
                await refresh()
            return
        display = status.get("volume_display")
        if display is not None and display != self._state.volume_display:
            self._state.volume_display = str(display)
            self._fingerprint = self._snapshot()
            self._notify()
        if not telnet_connected():
            self._apply_http_status("main", status)
            self._fingerprint = self._snapshot()
            self._notify()
        for refresh in refreshables:
            await refresh()

    def _apply_http_status(self, zone_id: str, status: dict[str, object]) -> None:
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
