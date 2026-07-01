"""Asynchronous telnet transport for the Denon AVR client library.

This class owns the raw telnet socket only. It knows nothing about the protocol
grammar; it just delivers received lines to a callback and serialises outgoing
commands. Robustness features:

* A single background manager task keeps the connection up and reconnects with
  exponential backoff after any failure.
* Outgoing commands go through a queue and are written with a small fixed gap,
  because the receiver drops commands that arrive too quickly.
* A keepalive probe is sent when the link is idle so a silently dropped socket
  is detected quickly.
* On every (re)connection a callback is invoked so the owner can resynchronise
  the full state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .const import (
    COMMAND_SPACING,
    CONNECT_TIMEOUT,
    KEEPALIVE_INTERVAL,
    RECONNECT_BACKOFF_MAX,
    RECONNECT_BACKOFF_MIN,
)

_LOGGER = logging.getLogger(__name__)

# The receiver terminates every line with a carriage return.
_TERMINATOR = b"\r"
# A harmless query used to keep the link alive and to detect a dead socket.
_KEEPALIVE_COMMAND = "PW?"


class TelnetClient:
    """Manage a resilient telnet connection to the receiver."""

    def __init__(
        self,
        host: str,
        port: int,
        on_line: Callable[[str], None],
        on_connected: Callable[[], Awaitable[None]] | None = None,
        on_availability: Callable[[bool], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_line = on_line
        self._on_connected = on_connected
        self._on_availability = on_availability

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._manager_task: asyncio.Task | None = None
        self._connected = False
        self._stopped = False
        self._last_activity = 0.0

    @property
    def connected(self) -> bool:
        """Return True when the telnet link is currently up."""

        return self._connected

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

    async def async_send(self, command: str) -> None:
        """Queue a command for delivery to the receiver."""

        await self._queue.put(command)

    # Connection lifecycle -------------------------------------------------

    async def _manage(self) -> None:
        """Keep the connection up, reconnecting with exponential backoff."""

        backoff = RECONNECT_BACKOFF_MIN
        while not self._stopped:
            try:
                await self._connect()
                backoff = RECONNECT_BACKOFF_MIN
                await self._run_session()
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.debug("Telnet connection to %s failed: %s", self._host, err)
            finally:
                await self._set_connected(False)
                await self._close_socket()

            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    async def _connect(self) -> None:
        """Open the telnet socket."""

        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=CONNECT_TIMEOUT
        )
        self._last_activity = asyncio.get_running_loop().time()
        await self._set_connected(True)
        _LOGGER.debug("Telnet connected to %s:%s", self._host, self._port)
        # Let the owner resynchronise the full state on (re)connect.
        if self._on_connected is not None:
            await self._on_connected()

    async def _run_session(self) -> None:
        """Run the reader, writer and keepalive concurrently until one ends."""

        reader_task = asyncio.ensure_future(self._read_loop())
        writer_task = asyncio.ensure_future(self._write_loop())
        keepalive_task = asyncio.ensure_future(self._keepalive_loop())
        tasks = [reader_task, writer_task, keepalive_task]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            # Surface an exception (for example a dropped socket) if one occurred.
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_socket(self) -> None:
        """Close the writer and drop the streams."""

        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = None
        self._writer = None

    async def _set_connected(self, connected: bool) -> None:
        """Update and broadcast the connection state."""

        if connected == self._connected:
            return
        self._connected = connected
        if self._on_availability is not None:
            self._on_availability(connected)

    # I/O loops ------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Read carriage return terminated lines and hand them to the callback."""

        assert self._reader is not None
        while True:
            try:
                raw = await self._reader.readuntil(_TERMINATOR)
            except asyncio.IncompleteReadError as err:
                if err.partial:
                    self._deliver(err.partial)
                raise ConnectionError("Telnet stream closed")
            except asyncio.LimitOverrunError as err:
                # The oversized data stays in the buffer, so retrying would spin.
                # Treat it as a broken stream and let the manager reconnect.
                raise ConnectionError("Telnet line exceeded read buffer") from err
            self._last_activity = asyncio.get_running_loop().time()
            self._deliver(raw)

    def _deliver(self, raw: bytes) -> None:
        """Decode and forward a received line."""

        line = raw.decode("utf-8", errors="replace").strip("\r\n")
        if line:
            self._on_line(line)

    async def _write_loop(self) -> None:
        """Write queued commands with a small gap between them."""

        assert self._writer is not None
        while True:
            command = await self._queue.get()
            data = command.encode("utf-8") + _TERMINATOR
            self._writer.write(data)
            await self._writer.drain()
            self._last_activity = asyncio.get_running_loop().time()
            await asyncio.sleep(COMMAND_SPACING)

    async def _keepalive_loop(self) -> None:
        """Send a light probe when the link has been idle for too long."""

        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL / 2)
            idle = asyncio.get_running_loop().time() - self._last_activity
            if idle >= KEEPALIVE_INTERVAL:
                await self._queue.put(_KEEPALIVE_COMMAND)
