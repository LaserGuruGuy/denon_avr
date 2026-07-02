"""Length-framed JSON control transport (port 1256) for the client library.

This is a separate, opt-in transport from the telnet control channel. It speaks
the receiver's length-framed binary/JSON protocol on TCP port 1256, which is the
way to read and write the manual speaker setup (amp assignment, speaker
size/crossover, distances) and the room-correction calibration filters. The
receiver has no push on this port and entering a calibration session interrupts
playback, so this module is used only for those setup operations and never for
live state monitoring; that stays on telnet (see device.py / telnet.py).

Wire framing (reverse engineered, big-endian on the wire):

    marker(1, 'T' outgoing) | total_length(u16) | current_segment(u8) |
    total_segments(u8) | command(10 ASCII) | 0x00 | data_length(u16) |
    data(data_length, JSON ASCII) | checksum(u8)

`total_length` is the full packet size; a packet with no data is 19 bytes. The
checksum is the sum of every byte before it, modulo 256. Large payloads are split
into segments numbered by `current_segment`/`total_segments` (0 total segments
means a single, unsegmented packet); segments may arrive out of order.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

_PORT = 1256

# Every command token is exactly this many ASCII bytes on the wire.
COMMAND_LEN = 10
# Framing bytes excluding the command field and the data payload: marker(1) +
# total_length(2) + current_segment(1) + total_segments(1) + command null(1) +
# data_length(2) + checksum(1).
HEADER_OVERHEAD = 9
_MARKER_TX = ord("T")


def checksum(data: bytes) -> int:
    """The protocol checksum: the sum of all bytes, modulo 256."""

    return sum(data) & 0xFF


def pack(command: str, data: bytes = b"", current: int = 0, total: int = 0) -> bytes:
    """Build a single wire frame for a command and optional JSON data payload."""

    cmd = command.encode("ascii")[:COMMAND_LEN].ljust(COMMAND_LEN, b"\x00")
    total_length = HEADER_OVERHEAD + COMMAND_LEN + len(data)
    frame = bytearray()
    frame.append(_MARKER_TX)
    frame += total_length.to_bytes(2, "big")
    frame.append(current & 0xFF)
    frame.append(total & 0xFF)
    frame += cmd
    frame.append(0)  # null separator after the fixed width command field
    frame += len(data).to_bytes(2, "big")
    frame += data
    frame.append(checksum(frame))
    return bytes(frame)


@dataclass
class Message:
    """A fully received (and reassembled) protocol message."""

    command: str
    data: bytes


class FrameDecoder:
    """Turns a byte stream into complete messages, reassembling segments.

    Feed raw bytes as they arrive; `feed` returns any messages that completed.
    Partial frames are buffered until enough bytes arrive, and multi segment
    payloads are held until every segment has been seen (segments may be out of
    order).
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        # command -> {segment_index: data} while a multi segment transfer is in
        # flight, plus the expected total, so out of order segments reassemble.
        self._pending: dict[str, dict[int, bytes]] = {}

    def feed(self, chunk: bytes) -> list[Message]:
        self._buffer += chunk
        messages: list[Message] = []
        while (frame := self._take_frame()) is not None:
            command, data, current, total = frame
            message = self._reassemble(command, data, current, total)
            if message is not None:
                messages.append(message)
        return messages

    def _take_frame(self) -> tuple[str, bytes, int, int] | None:
        """Extract one complete frame from the buffer, or None if incomplete."""

        buf = self._buffer
        # Need the marker + total_length before we know the packet size.
        if len(buf) < 3:
            return None
        total_length = int.from_bytes(buf[1:3], "big")
        # A valid packet is at least the overhead plus the command field.
        if total_length < HEADER_OVERHEAD + COMMAND_LEN:
            # Corrupt/unaligned; drop one byte and resynchronise.
            del buf[0]
            return None
        if len(buf) < total_length:
            return None
        packet = bytes(buf[:total_length])
        if checksum(packet[:-1]) != packet[-1]:
            _LOGGER.debug("TCP frame checksum mismatch; resynchronising")
            del buf[0]
            return None
        del buf[:total_length]
        current = packet[3]
        total = packet[4]
        command = packet[5 : 5 + COMMAND_LEN].split(b"\x00", 1)[0].decode(
            "ascii", "replace"
        )
        data_length = int.from_bytes(packet[16:18], "big")
        data = packet[18 : 18 + data_length]
        return command, data, current, total

    def _reassemble(
        self, command: str, data: bytes, current: int, total: int
    ) -> Message | None:
        """Combine segments; return a Message once the transfer is complete."""

        if total == 0:
            return Message(command, data)
        segments = self._pending.setdefault(command, {})
        segments[current] = data
        # Segment indices run 0..total, so a complete transfer has total + 1 parts.
        if len(segments) <= total:
            return None
        ordered = b"".join(segments[i] for i in sorted(segments))
        del self._pending[command]
        return Message(command, ordered)


class TcpClient:
    """Minimal async client for the length-framed JSON transport (port 1256).

    Connection and framing only; the session protocol (enter/exit) and the
    device info/status JSON schemas are layered on top in a later step.
    """

    def __init__(
        self,
        host: str,
        port: int = _PORT,
        on_message: Callable[[Message], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_message = on_message
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._decoder = FrameDecoder()
        self._read_task: asyncio.Task | None = None
        # Pending query waiters keyed by command, resolved by the read loop.
        self._pending: dict[str, list[asyncio.Future]] = {}
        # Waiters for the next inbound message of any command (used for SET acks,
        # which may come back as an echo, ACK or NACK).
        self._any_waiters: list[asyncio.Future] = []

    async def connect(self, timeout: float = 5.0) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout
        )
        self._read_task = asyncio.ensure_future(self._read_loop())

    async def send(self, command: str, data: bytes = b"") -> None:
        if self._writer is None:
            raise ConnectionError("TCP client is not connected")
        self._writer.write(pack(command, data))
        await self._writer.drain()

    async def async_query(
        self, command: str, data: bytes = b"", timeout: float = 5.0
    ) -> dict:
        """Send a command and return the parsed JSON of the matching response.

        Reading receiver info (GET_AVRINF) and speaker/amp setup (GET_AVRSTS)
        does not need a session, so this is non disruptive; only the actual
        calibration measurement enters a session.
        """

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending.setdefault(command, []).append(future)
        try:
            await self.send(command, data)
            message = await asyncio.wait_for(future, timeout)
        finally:
            waiters = self._pending.get(command)
            if waiters and future in waiters:
                waiters.remove(future)
        if not message.data:
            return {}
        try:
            return json.loads(message.data.decode("ascii", "replace"))
        except json.JSONDecodeError:
            _LOGGER.debug("%s response was not valid JSON", command)
            return {}

    async def async_read_setup(self) -> dict:
        """Read receiver capabilities and speaker/amp setup (no session needed)."""

        return {
            "info": await self.async_query("GET_AVRINF"),
            "status": await self.async_query("GET_AVRSTS"),
        }

    async def async_set(
        self, command: str, payload: dict | None = None, timeout: float = 5.0
    ) -> "Message | None":
        """Send a SET_* command with a JSON payload and return the ack message.

        The receiver acknowledges with an echo, ACK or NACK, so this waits for
        the next inbound message rather than one keyed to `command`. Returns None
        on timeout. Writing setup (amp assignment, distances, crossover) does not
        require a session; only the calibration measurement does.
        """

        data = b""
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("ascii")
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._any_waiters.append(future)
        try:
            await self.send(command, data)
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            if future in self._any_waiters:
                self._any_waiters.remove(future)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while chunk := await self._reader.read(4096):
                for message in self._decoder.feed(chunk):
                    waiters = self._pending.get(message.command)
                    if waiters:
                        future = waiters.pop(0)
                        if not future.done():
                            future.set_result(message)
                    if self._any_waiters:
                        any_future = self._any_waiters.pop(0)
                        if not any_future.done():
                            any_future.set_result(message)
                    if self._on_message is not None:
                        self._on_message(message)
        except (OSError, asyncio.CancelledError):
            pass

    async def close(self) -> None:
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._reader = None
