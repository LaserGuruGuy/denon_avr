"""The two ports between services and transports (structural typing, no I/O here)."""

from __future__ import annotations

from typing import Protocol


class CommandSink(Protocol):
    """Where telnet commands go (the persistent control connection)."""

    async def async_send(self, command: str) -> None: ...


class ConfigDocumentPort(Protocol):
    """Read and write setup web interface documents by section and type."""

    async def async_get(
        self, section: str, type_id: int, extra: dict[str, str] | None = None
    ) -> str | None: ...

    async def async_set(self, section: str, type_id: int, xml: str) -> bool: ...
