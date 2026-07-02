"""Calibration MultEQ transport for the Denon AVR client library.

This is a separate, opt-in transport from the telnet control channel. It speaks
the Calibration MultEQ Editor protocol on TCP port 1256, which is the only way to
read and write the manual speaker setup (amp assignment, speaker size/crossover,
distances) and the Calibration calibration filters. The receiver has no push on
this port and entering an Calibration session interrupts playback, so this module
is used only for those setup operations and never for live state monitoring;
that stays on telnet (see device.py / telnet_client.py).

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
import logging
from collections.abc import Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

TCP_PORT = 1256

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
            _LOGGER.debug("Calibration frame checksum mismatch; resynchronising")
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


class CalibrationClient:
    """Minimal async client for the Calibration MultEQ transport (port 1256).

    Connection and framing only; the session protocol (ENTER_AUDY … EXIT_AUDMD)
    and the AvrInfo/AvrStatus JSON schemas are layered on top in a later step.
    """

    def __init__(
        self,
        host: str,
        port: int = TCP_PORT,
        on_message: Callable[[Message], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_message = on_message
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._decoder = FrameDecoder()
        self._read_task: asyncio.Task | None = None

    async def connect(self, timeout: float = 5.0) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout
        )
        self._read_task = asyncio.ensure_future(self._read_loop())

    async def send(self, command: str, data: bytes = b"") -> None:
        if self._writer is None:
            raise ConnectionError("Calibration client is not connected")
        self._writer.write(pack(command, data))
        await self._writer.drain()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while chunk := await self._reader.read(4096):
                for message in self._decoder.feed(chunk):
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
