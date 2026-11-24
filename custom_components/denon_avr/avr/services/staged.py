"""Staged edits to a setup web collection (speaker layout): prepare, apply, restore."""

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
    """Outcome."""

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
        cached_control: Callable[[str], Any] = lambda _control_id: None,
    ) -> None:
        self._section = section
        self._collection = collection_id
        # A fresh value from the receiver (telnet), None when it does not answer.
        self._read_control = read_control
        self._set_control = set_control
        # The last known value, used for the "before" state when it does not answer now.
        self._cached_control = cached_control
        self._pending: dict[str, str] = {}
        self._before: _Snapshot | None = None     # before the first apply
        self._after: _Snapshot | None = None      # after the last apply, when it was seen
        self._newer: set[str] = set()             # chosen by the user since the first apply
        self._newer_controls: set[str] = set()
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

    @property
    def stopped(self) -> bool:
        return self._stopped

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
        self._newer = set()
        self._newer_controls = set()
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

    async def _guard(self) -> str | None:
        """Why the restore point may not be used now."""

        known = {
            k: self._before.guards.get(k) if self._before.guards.get(k) is not None
            else (self._after.guards.get(k) if self._after else None)
            for k in self._spec.get("restore_guards", ())
        }
        for control_id, value in known.items():
            if value is None:
                continue            # it never answered: this receiver has no such control
            now = await self._read_control(control_id)
            if now is None:
                now = await self._read_control(control_id)
            if now is None:
                return "unavailable"
            if now != value:
                return "stale"
        return None

    def _note_newer(self, now: _Snapshot) -> None:
        """Remember what the user changed since our last apply: it is never overwritten."""

        if self._before is None or self._after is None:
            return
        self._newer |= {s for s in self._before.positions if now.positions.get(s) != self._after.positions.get(s)}
        self._newer_controls |= {
            c for c, v in self._after.controls.items()
            if v is not None and now.controls.get(c) is not None and now.controls[c] != v
        }

    async def _wait(self, shown: Callable[[], bool]) -> bool:
        """Until the receiver shows it: it rebuilds the layout after a change (2 s)."""

        for _ in range(int(self._spec.get("apply_attempts", 1))):
            await asyncio.sleep(float(self._spec.get("apply_settle_seconds", 2.0)))
            if self._stopped:
                return False
            if await self._read() and shown():
                return True
        return False

    async def _stable(self) -> bool:
        """Read until two reads in a row agree: a layout change is applied in steps."""

        last: tuple | None = None
        for _ in range(int(self._spec.get("apply_attempts", 1)) + 2):
            if self._stopped or not await self._read():
                return False
            now = (self._items(), self._positions())
            if now == last:
                return True
            last = now
            await asyncio.sleep(float(self._spec.get("apply_settle_seconds", 2.0)) / 2)
        return False

    # Apply -----------------------------------------------------------------

    async def async_apply(self) -> dict[str, bool]:
        """Write every staged item, then verify it."""

        if self.busy:
            return {slot: False for slot in self._pending}
        async with self._lock:
            pending = dict(self._pending)
            if not pending:
                return {}
            if not await self._read():
                return {slot: False for slot in pending}
            before = await self._snapshot()
            if self._before is not None:
                guard = await self._guard()
                if guard == "unavailable":
                    return {slot: False for slot in pending}
                if guard == "stale":
                    self._forget()      # another preset: this apply gets its own restore point
                else:
                    self._note_newer(before)
            fresh = self._before is None
            if fresh:
                for control_id, value in before.controls.items():
                    if value is None:
                        before.controls[control_id] = self._cached_control(control_id)
                self._before = before
            self._after = None          # until it is seen; also when this is cancelled
            self._pending.clear()
            for slot, option in pending.items():
                if self._stopped:
                    break
                await self._section.async_set_item(self._collection, slot, option, staged_apply=True)
            await self._wait(lambda: all(self._items().get(s) == o for s, o in pending.items()))
            seen = await self._stable()
            items = self._items()
            result = {slot: seen and items.get(slot) == option for slot, option in pending.items()}
            if seen and fresh and (items, self._positions()) == (before.items, before.positions):
                self._forget()          # seen: nothing happened, nothing to restore
            elif seen:
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

        if self.busy or self._before is None:
            return RestoreResult(refused="unavailable")
        async with self._lock:
            try:
                async with asyncio.timeout(float(self._spec.get("restore_timeout", 60))):
                    result = await self._restore(self._before)
            except TimeoutError:
                result = RestoreResult(left=("timeout",))
            # A refusal keeps the restore point: the preset may be switched back, the receiver may
            # answer again.
            if result.refused is None and not result.left:
                self._forget()
            elif result.refused is None and await self._read():
                self._after = await self._snapshot()
                self._track()
            return result

    async def _restore(self, before: _Snapshot) -> RestoreResult:
        if not await self._read():
            return RestoreResult(refused="unavailable")
        if (guard := await self._guard()) is not None:
            return RestoreResult(refused=guard)
        if self._after is None:
            # The apply was interrupted or its result not seen: what is there now is it.
            if not await self._stable():
                return RestoreResult(refused="unavailable")
            self._after = await self._snapshot()
        after = self._after
        if any(self._items().get(slot) != value for slot, value in after.items.items()):
            return RestoreResult(refused="stale")
        # Choices made after the apply are left alone (judged before anything is written).
        self._note_newer(_Snapshot(self._items(), self._positions(), await self._controls("restore_controls")))
        newer, newer_controls = set(self._newer), set(self._newer_controls)
        kept = tuple(sorted(newer, key=_order)) + tuple(sorted(newer_controls))
        # 1. the mode, confirmed; no position is written into another layout.
        for slot, value in before.items.items():
            if value is None or self._items().get(slot) == value:
                continue
            if self._stopped:
                return RestoreResult(left=(MODE,))
            await self._section.async_set_item(self._collection, slot, value, staged_apply=True)
            if not await self._wait(lambda s=slot, v=value: self._items().get(s) == v):
                return RestoreResult(left=(MODE,), newer=kept)
            if not await self._stable():
                return RestoreResult(left=("unreachable",), newer=kept)
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
                    return RestoreResult(left=("unreachable",), newer=kept)
        # 3. the telnet controls, written even when the cache looks right, then read.
        left = list(self._remaining(before, newer))
        for control_id, value in before.controls.items():
            if control_id in newer_controls or self._set_control is None or self._stopped:
                continue
            if value is None:
                left.append(control_id)          # unknown before: cannot be put back
                continue
            if await self._read_control(control_id) is None:
                left.append(control_id)          # telnet does not answer: do not queue it
                continue
            await self._set_control(control_id, value)
            if await self._read_control(control_id) != value:
                left.append(control_id)
        return RestoreResult(tuple(left), kept)

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
