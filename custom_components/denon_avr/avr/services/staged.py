"""Staged edits to a setup web collection (speaker layout): prepare, apply, restore.

Nothing reaches the receiver until ``async_apply``. An apply also resets other
choices on the receiver that going back does NOT restore (measured: other layout
positions, front speaker A/B), so the state before the FIRST apply is kept as a
restore point. ``async_restore`` puts it back, but never over a choice that was made
after the apply, never into another speaker preset, and never position by position
while the mode itself is not back. One operation at a time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .webconfig_doc import WebConfigSectionController

# The name a problem with the staged items themselves (the mode) is reported by.
MODE = "mode"


@dataclass
class _Snapshot:
    """What the receiver showed: staged items, related positions, telnet controls."""

    items: dict[str, str | None]
    positions: dict[str, tuple[str | None, bool]]        # value, settable
    controls: dict[str, Any] = field(default_factory=dict)
    guards: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RestoreResult:
    """Outcome: ``left`` could not be restored, ``newer`` was changed after the apply
    and left alone, ``refused`` says why nothing was done (unavailable / stale)."""

    left: tuple[str, ...] = ()
    newer: tuple[str, ...] = ()
    refused: str | None = None


async def _no_control(_control_id: str) -> Any:
    return None


class StagedChanges:
    """Edits to a staged collection, kept locally until applied."""

    def __init__(
        self,
        section: WebConfigSectionController,
        collection_id: str,
        read_control: Callable[[str], Awaitable[Any]] = _no_control,
        set_control: Callable[[str, Any], Awaitable[None]] | None = None,
    ) -> None:
        self._section = section
        self._collection = collection_id
        # A fresh value from the receiver (telnet), None when it does not answer.
        self._read_control = read_control
        self._set_control = set_control
        self._pending: dict[str, str] = {}
        self._before: _Snapshot | None = None     # before the first apply
        self._after: _Snapshot | None = None      # after the last apply
        self._lock = asyncio.Lock()
        self._stopped = False
        # Positions that differ from before the first apply: slot -> (before, after).
        self.side_effects: dict[str, tuple[str | None, str | None]] = {}

    # State ----------------------------------------------------------------

    @property
    def _spec(self) -> dict[str, Any]:
        return self._section.collection_spec(self._collection) or {}

    @property
    def pending(self) -> dict[str, str]:
        return dict(self._pending)

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def restorable(self) -> bool:
        """What the receiver had before an apply is known and differs (as far as seen)."""

        return self._before is not None

    def stop(self) -> None:
        """Write nothing any more (the integration is being unloaded)."""

        self._stopped = True

    def stage(self, slot: str, option: str) -> bool:
        item = self._section.collection(self._collection).get(slot)
        if self.busy or item is None or not item.availability.settable or option not in item.options:
            return False
        if option == item.value:
            self._pending.pop(slot, None)
        else:
            self._pending[slot] = option
        return True

    def discard(self) -> bool:
        """Drop the staged change; without one, give up the restore point. False while busy."""

        if self.busy:
            return False
        if self._pending:
            self._pending.clear()
        else:
            self._forget()
        return True

    def _forget(self) -> None:
        self._before = None
        self._after = None
        self.side_effects = {}

    # Reading ---------------------------------------------------------------

    def _items(self) -> dict[str, str | None]:
        return {k: i.value for k, i in self._section.collection(self._collection).items()}

    def _positions(self) -> dict[str, tuple[str | None, bool]]:
        related = self._spec.get("side_effects_of")
        if not related:
            return {}
        return {k: (i.value, i.availability.settable) for k, i in self._section.collection(related).items()}

    async def _read(self) -> bool:
        """Read the layout document now; False when the receiver did not give it."""

        return await self._section.async_read_collections(int(self._spec["type"]))

    async def _controls(self, key: str) -> dict[str, Any]:
        return {c: await self._read_control(c) for c in self._spec.get(key, ())}

    async def _snapshot(self) -> _Snapshot:
        return _Snapshot(
            self._items(), self._positions(),
            await self._controls("restore_controls"), await self._controls("restore_guards"),
        )

    async def _wait(self, shown: Callable[[], bool]) -> bool:
        """Until the receiver shows it: it rebuilds the layout after a change (2 s)."""

        for _ in range(int(self._spec.get("apply_attempts", 1))):
            await asyncio.sleep(float(self._spec.get("apply_settle_seconds", 2.0)))
            if self._stopped:
                return False
            if await self._read() and shown():
                return True
        return False

    # Apply -----------------------------------------------------------------

    async def async_apply(self) -> dict[str, bool]:
        """Write every staged item, then verify it. Returns slot -> accepted. Nothing is
        written (and the change stays staged) when the state before cannot be read."""

        if self.busy:
            return {slot: False for slot in self._pending}
        async with self._lock:
            pending = dict(self._pending)
            if not pending:
                return {}
            if not await self._read():
                return {slot: False for slot in pending}
            fresh = self._before is None
            before = await self._snapshot()
            if fresh:
                self._before = before
            self._pending.clear()
            for slot, option in pending.items():
                if self._stopped:
                    break
                await self._section.async_set_item(self._collection, slot, option, staged_apply=True)
            await self._wait(lambda: all(self._items().get(s) == o for s, o in pending.items()))
            items = self._items()
            result = {slot: items.get(slot) == option for slot, option in pending.items()}
            if fresh and items == before.items and self._positions() == before.positions and not any(result.values()):
                self._forget()          # nothing happened: nothing to restore
            else:
                self._after = await self._snapshot()
                self._track()
            return result

    def _track(self) -> None:
        after = self._positions()
        if not after or self._before is None:
            return   # the collection could not be read: nothing can be said
        base = self._before.positions
        self.side_effects = {
            slot: (base.get(slot, (None, False))[0], after.get(slot, (None, False))[0])
            for slot in sorted(set(base) | set(after), key=_order)
            if base.get(slot, (None, False))[0] != after.get(slot, (None, False))[0]
        }

    # Restore ---------------------------------------------------------------

    async def async_restore(self) -> RestoreResult:
        """Put back what the receiver had before the first apply."""

        if self.busy or self._before is None or self._after is None:
            return RestoreResult(refused="unavailable")
        async with self._lock:
            before, after = self._before, self._after
            try:
                async with asyncio.timeout(float(self._spec.get("restore_timeout", 60))):
                    result = await self._restore(before, after)
            except TimeoutError:
                result = RestoreResult(left=("timeout",))
            if result.refused == "stale":
                self._forget()          # another preset or mode since: not ours to undo
            elif result.refused is None and not result.left:
                self._forget()
            elif result.refused is None:
                self._track()
                self._after = _Snapshot(self._items(), self._positions(), after.controls, after.guards)
            return result

    async def _restore(self, before: _Snapshot, after: _Snapshot) -> RestoreResult:
        if not await self._read():
            return RestoreResult(refused="unavailable")
        guards = await self._controls("restore_guards")
        if any(v is not None and guards.get(k) is not None and guards[k] != v for k, v in after.guards.items()):
            return RestoreResult(refused="stale")
        if any(self._items().get(slot) != value for slot, value in after.items.items()):
            return RestoreResult(refused="stale")
        # Choices made after the apply are left alone (judged before anything is written).
        now = self._positions()
        newer = {slot for slot in before.positions if now.get(slot) != after.positions.get(slot)}
        fresh = await self._controls("restore_controls")
        newer_controls = {
            c for c, v in after.controls.items() if v is not None and fresh.get(c) is not None and fresh[c] != v
        }
        # 1. the mode, confirmed; no position is written into another layout.
        for slot, value in before.items.items():
            if value is None or self._items().get(slot) == value:
                continue
            if self._stopped:
                return RestoreResult(left=(MODE,))
            await self._section.async_set_item(self._collection, slot, value, staged_apply=True)
            if not await self._wait(lambda s=slot, v=value: self._items().get(s) == v):
                return RestoreResult(left=(MODE,), newer=tuple(sorted(newer, key=_order)))
        # 2. the positions; one can bring another back, so look again every time.
        tried: set[str] = set()
        while not self._stopped:
            slot = next((s for s in self._todo(before, newer) if s not in tried), None)
            if slot is None:
                break
            tried.add(slot)
            wanted = before.positions[slot][0]
            await self._section.async_set_item(self._spec["side_effects_of"], slot, wanted, staged_apply=True)
            if not await self._wait(lambda s=slot, v=wanted: self._positions().get(s, (None,))[0] == v):
                if not await self._read():
                    return RestoreResult(left=("unreachable",), newer=tuple(sorted(newer, key=_order)))
        # 3. the telnet controls, written even when the cache looks right, then read.
        left = list(self._remaining(before, newer))
        for control_id, value in before.controls.items():
            if value is None or control_id in newer_controls or self._set_control is None or self._stopped:
                continue
            if await self._read_control(control_id) is None:
                left.append(control_id)          # telnet does not answer: do not queue it
                continue
            await self._set_control(control_id, value)
            if await self._read_control(control_id) != value:
                left.append(control_id)
        return RestoreResult(tuple(left), tuple(sorted(newer, key=_order)) + tuple(sorted(newer_controls)))

    def _todo(self, before: _Snapshot, newer: set[str]) -> list[str]:
        items = self._section.collection(self._spec["side_effects_of"]) if self._spec.get("side_effects_of") else {}
        return [
            slot
            for slot in sorted(before.positions, key=_order)
            if slot in items and slot not in newer
            and before.positions[slot][0] is not None
            and items[slot].value != before.positions[slot][0]
            and items[slot].availability.settable
            and before.positions[slot][0] in items[slot].options
        ]

    def _remaining(self, before: _Snapshot, newer: set[str]) -> list[str]:
        """Visible differences: settable before and now other, or newly settable."""

        now = self._positions()
        return [
            slot
            for slot in sorted(set(before.positions) | set(now), key=_order)
            if slot not in newer
            and (before.positions.get(slot, (None, False))[1] or now.get(slot, (None, False))[1])
            and before.positions.get(slot, (None, False))[0] != now.get(slot, (None, False))[0]
        ]


def _order(slot: str) -> tuple[int, str]:
    return len(slot), slot
