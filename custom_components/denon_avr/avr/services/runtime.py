"""Runtime helpers shared by the services: tracked background tasks and debounced notification."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

# Debounce window for collapsing a burst of telnet lines into one notification.
UPDATE_DEBOUNCE = 0.1


class BackgroundTasks:
    """Fire-and-forget coroutines with strong references, cancellable as a group."""

    def __init__(self) -> None:
        self.pending: set[asyncio.Task] = set()

    def create(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(coro)
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    def cancel_all(self) -> None:
        for task in list(self.pending):
            task.cancel()
        self.pending.clear()


class UpdateNotifier:
    """Debounce state notifications so a burst becomes a single callback."""

    def __init__(self) -> None:
        self._callback: Callable[[], None] | None = None
        self._handle: asyncio.TimerHandle | None = None

    def register(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    def schedule(self) -> None:
        if self._callback is None:
            return
        loop = asyncio.get_running_loop()
        if self._handle is not None:
            self._handle.cancel()
        self._handle = loop.call_later(UPDATE_DEBOUNCE, self._fire)

    def cancel(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _fire(self) -> None:
        self._handle = None
        if self._callback is not None:
            self._callback()
