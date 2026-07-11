"""HEOS CLI transport (port 1255) for now-playing media and album art.

The goform now-playing endpoints are disabled on HEOS-era firmware (they answer
403), so the receiver's network now-playing metadata - track/artist/album, the
transport state and a real album-art image URL - is read from the HEOS command
line interface instead. This transport owns that TCP socket only; it parses the
newline-delimited JSON messages and hands a plain snapshot dict to a callback.

It is strictly auxiliary: any failure here leaves core telnet control untouched,
so every error is swallowed and simply results in no now-playing data. The
connection registers for HEOS change events and re-queries on the ones that
matter (now-playing changed, transport state changed), ignoring the
once-a-second progress events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from urllib.parse import parse_qs

from ..const import (
    CONNECT_TIMEOUT,
    RECONNECT_BACKOFF_MAX,
    RECONNECT_BACKOFF_MIN,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 1255
_TERMINATOR = b"\r\n"
# HEOS tolerates back-to-back commands, but a tiny gap keeps ordering sane.
_COMMAND_SPACING = 0.05


class HeosClient:
    """Read now-playing media from the receiver's HEOS CLI, with events."""

    def __init__(
        self,
        host: str,
        on_update: Callable[[dict], None],
        port: int = DEFAULT_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._on_update = on_update

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._manager_task: asyncio.Task | None = None
        self._stopped = False
        self._pid: int | None = None
        # Current snapshot; only re-broadcast when something actually changes.
        self._snapshot: dict = {
            "state": None,
            "media_type": None,
            "title": None,
            "artist": None,
            "album": None,
            "image_url": None,
        }

    async def async_start(self) -> None:
        """Start the background connection manager."""

        self._stopped = False
        if self._manager_task is None or self._manager_task.done():
            self._manager_task = asyncio.ensure_future(self._manage())

    async def async_stop(self) -> None:
        """Stop the connection manager and close the socket."""

        self._stopped = True
        if self._manager_task is not None:
            self._manager_task.cancel()
            try:
                await self._manager_task
            except asyncio.CancelledError:
                pass
            self._manager_task = None
        await self._close_socket()

    # Transport controls (no-ops until a player id is known) ---------------

    async def async_play(self) -> None:
        await self._send_player("set_play_state", "&state=play")

    async def async_pause(self) -> None:
        await self._send_player("set_play_state", "&state=pause")

    async def async_stop_playback(self) -> None:
        await self._send_player("set_play_state", "&state=stop")

    async def async_next(self) -> None:
        await self._send_player("play_next", "")

    async def async_previous(self) -> None:
        await self._send_player("play_previous", "")

    async def _send_player(self, command: str, extra: str) -> None:
        if self._pid is None:
            return
        await self._queue.put(f"heos://player/{command}?pid={self._pid}{extra}")

    # Connection lifecycle -------------------------------------------------

    async def _manage(self) -> None:
        """Keep the HEOS connection up, reconnecting with backoff. Never raises."""

        backoff = RECONNECT_BACKOFF_MIN
        while not self._stopped:
            try:
                await self._connect()
                backoff = RECONNECT_BACKOFF_MIN
                await self._run_session()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - auxiliary, must not propagate
                _LOGGER.debug("HEOS connection to %s failed: %s", self._host, err)
            finally:
                await self._close_socket()
            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    async def _connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=CONNECT_TIMEOUT
        )
        _LOGGER.debug("HEOS connected to %s:%s", self._host, self._port)
        # Drain any stale queued commands from a previous session, then bootstrap.
        while not self._queue.empty():
            self._queue.get_nowait()
        await self._queue.put("heos://system/register_for_change_events?enable=on")
        await self._queue.put("heos://player/get_players")

    async def _run_session(self) -> None:
        reader_task = asyncio.ensure_future(self._read_loop())
        writer_task = asyncio.ensure_future(self._write_loop())
        tasks = [reader_task, writer_task]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_socket(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = None
        self._writer = None

    # I/O loops ------------------------------------------------------------

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                raw = await self._reader.readuntil(_TERMINATOR)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as err:
                raise ConnectionError("HEOS stream closed") from err
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._handle_message(line)

    async def _write_loop(self) -> None:
        assert self._writer is not None
        while True:
            command = await self._queue.get()
            self._writer.write(command.encode("utf-8") + _TERMINATOR)
            await self._writer.drain()
            await asyncio.sleep(_COMMAND_SPACING)

    # Message handling -----------------------------------------------------

    def _handle_message(self, line: str) -> None:
        """Parse one HEOS JSON message and react to it. Never raises."""

        try:
            message = json.loads(line)
        except ValueError:
            return
        heos = message.get("heos", {})
        command = heos.get("command", "")
        attrs = _parse_message(heos.get("message", ""))

        if command == "player/get_players":
            players = message.get("payload") or []
            if players:
                self._pid = players[0].get("pid")
                # Bootstrap the current media and transport state.
                self._request_now_playing()
                self._request_play_state()
            return

        if command == "player/get_now_playing_media":
            self._apply_now_playing(message.get("payload") or {})
            return

        if command == "player/get_play_state":
            self._apply_state(attrs.get("state"))
            return

        if command == "event/player_state_changed":
            self._apply_state(attrs.get("state"))
            return

        if command == "event/player_now_playing_changed":
            self._request_now_playing()
            return

        # event/player_now_playing_progress and everything else: ignore.

    def _request_now_playing(self) -> None:
        if self._pid is not None:
            self._queue.put_nowait(
                f"heos://player/get_now_playing_media?pid={self._pid}"
            )

    def _request_play_state(self) -> None:
        if self._pid is not None:
            self._queue.put_nowait(f"heos://player/get_play_state?pid={self._pid}")

    def _apply_now_playing(self, payload: dict) -> None:
        image = (payload.get("image_url") or "").strip() or None
        self._update(
            media_type=payload.get("type"),
            title=payload.get("song") or payload.get("station"),
            artist=payload.get("artist"),
            album=payload.get("album"),
            image_url=image,
        )

    def _apply_state(self, state: str | None) -> None:
        if state:
            self._update(state=state)

    def _update(self, **fields) -> None:
        """Merge fields into the snapshot and broadcast if anything changed."""

        changed = False
        for key, value in fields.items():
            if self._snapshot.get(key) != value:
                self._snapshot[key] = value
                changed = True
        if changed:
            self._on_update(dict(self._snapshot))


def _parse_message(message: str) -> dict[str, str]:
    """Parse a HEOS 'message' field ('pid=1&state=play') into a flat dict."""

    if not message:
        return {}
    return {key: values[0] for key, values in parse_qs(message).items() if values}
