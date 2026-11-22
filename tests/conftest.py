"""Shared test infrastructure for the HA-free ``avr`` library (characterization tests).

The library is imported as the top-level package ``avr`` (its modules use relative
imports), so these tests never import Home Assistant. Network I/O is replaced at the
lowest seam: a fake aiohttp session serves recorded documents by URL, and a fake
telnet client records every command string and replays recorded telnet lines. The
real transports, parser, profile and device facade run unchanged, so the goldens pin
today's end-to-end behaviour.

Goldens live in tests/golden/<fixture>/*.json. Set DENON_UPDATE_GOLDEN=1 to (re)write
them; otherwise a mismatch fails the test with a diff.
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import importlib.util
import json
import os
import pathlib
import socket
import sys
from urllib.parse import urlsplit

import aiohttp
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden"
LIB = ROOT / "custom_components" / "denon_avr" / "avr"


def _load_library() -> None:
    """Import the library as top-level ``avr`` WITHOUT putting its parent on sys.path.

    The parent (custom_components/denon_avr) holds select.py, number.py, sensor.py ...
    which would shadow the standard library modules of the same name.
    """
    if "avr" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location("avr", LIB / "__init__.py", submodule_search_locations=[str(LIB)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["avr"] = module
    spec.loader.exec_module(module)


_load_library()

HOST = "192.0.2.10"


# --------------------------------------------------------------------------- golden
def to_jsonable(obj):
    """Deterministic JSON view of library objects (dataclasses, sets, enums)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return to_jsonable(obj.value)
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (set, frozenset)):
        return sorted((to_jsonable(v) for v in obj), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


def assert_golden(fixture: str, name: str, value) -> None:
    path = GOLDEN / fixture / f"{name}.json"
    text = json.dumps(to_jsonable(value), indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if os.environ.get("DENON_UPDATE_GOLDEN") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if os.environ.get("DENON_UPDATE_GOLDEN") != "1":
            pytest.fail(f"golden {path.relative_to(ROOT)} did not exist; written - review and commit it")
        return
    expected = path.read_text(encoding="utf-8")
    assert text == expected, f"golden {path.relative_to(ROOT)} changed (DENON_UPDATE_GOLDEN=1 to accept)"


# --------------------------------------------------------------------------- fixtures
class FixtureSet:
    """Recorded responses of one receiver (tests/fixtures/<name>/)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.dir = FIXTURES / name
        meta = self.dir / "fixture.json"
        self.meta = json.loads(meta.read_text()) if meta.exists() else {}

    def file(self, name: str) -> str | None:
        path = self.dir / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    @property
    def telnet_corpus(self) -> list[dict]:
        return json.loads(self.file("telnet_corpus.json") or "[]")

    def http(self, url: str) -> tuple[int, str] | None:
        """Response for a URL, or None when the endpoint is unreachable (no listener)."""
        parts = urlsplit(url)
        port = parts.port
        web = self.meta.get("webconfig")          # {"scheme": .., "port": ..} or absent
        if port in (8080,):
            body = self.file("goform_" + parts.path.rsplit("/", 1)[-1])
        elif port == 60006:
            body = self.file("upnp_aios_device.xml")
        elif web and parts.scheme == web["scheme"] and port == web["port"] and parts.path.startswith("/ajax/"):
            section, verb = parts.path.split("/")[2:4]
            if verb != "get_config":
                raise AssertionError(f"tests must never write to a receiver: {url}")
            query = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
            body = self.file(f"ajax_{section}_{query.get('type')}.xml")
            if body is None:
                return 500, ""
        else:
            return None
        return (200, body) if body is not None else (404, "")


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Minimal aiohttp.ClientSession stand-in serving a FixtureSet.

    Logs every request with the transport options that matter on the wire (TLS
    verification, timeout), so dropping ``ssl=False`` or a timeout shows in a golden.
    Writes (``set_config``) are logged and answered 200 but never reach a receiver.
    """

    def __init__(self, fixtures: FixtureSet) -> None:
        self.fixtures = fixtures
        self.requests: list[dict] = []

    def get(self, url, **kwargs):
        url = str(url)
        timeout = kwargs.get("timeout")
        self.requests.append({
            "url": url,
            "ssl": kwargs.get("ssl", "default"),
            "timeout_total": getattr(timeout, "total", timeout),
        })
        resp = self.fixtures.http(url.replace("/set_config", "/get_config") if "/set_config" in url else url)
        if resp is not None and "/set_config" in url:
            resp = (200, "")                            # reachable interface: accept, never forward
        if resp is None:
            raise aiohttp.ClientConnectionError(f"connection refused: {url}")
        return _FakeResponse(*resp)


class FakeTelnet:
    """Replaces transport.telnet.TelnetClient: records commands, replays lines on demand."""

    instances: list["FakeTelnet"] = []

    def __init__(self, host, on_line=None, on_connected=None, on_availability=None, **_kw) -> None:
        self.sent: list[str] = []
        self._on_line = on_line
        self._on_connected = on_connected
        self._on_availability = on_availability
        self._connected = False
        FakeTelnet.instances.append(self)

    @property
    def connected(self) -> bool:
        return self._connected

    async def async_start(self) -> None:
        self._connected = True
        if self._on_availability:
            self._on_availability(True)
        if self._on_connected:
            await self._on_connected()

    async def async_stop(self) -> None:
        self._connected = False

    async def async_send(self, command: str) -> None:
        self.sent.append(command)

    def feed(self, line: str) -> None:
        self._on_line(line)

    def drop(self) -> None:
        """Simulate the link going down (as the real client reports it)."""
        self._connected = False
        if self._on_availability:
            self._on_availability(False)


class FakeHeos:
    """Replaces transport.heos.HeosClient: records media commands."""

    instances: list["FakeHeos"] = []

    def __init__(self, *_a, **_kw) -> None:
        self.calls: list[str] = []
        FakeHeos.instances.append(self)

    def __getattr__(self, name):
        if not name.startswith("async_"):
            raise AttributeError(name)

        async def _call(*args, **kwargs):
            self.calls.append(name + (repr(args) if args else ""))

        return _call


class FakeProbe:
    """Replaces transport.telnet.async_probe: logs the queries, feeds the recorded answers."""

    def __init__(self, fixtures: FixtureSet) -> None:
        self.fixtures = fixtures
        self.queries: list[list[str]] = []

    async def __call__(self, host, queries, on_line, is_complete, **_kw):
        self.queries.append(list(queries))
        wanted = set(queries)
        for rec in self.fixtures.telnet_corpus:
            if rec["after"] in wanted:
                on_line(rec["line"])
                if is_complete():
                    return


def replace_everywhere(monkeypatch, original, fake) -> int:
    """Swap ``original`` for ``fake`` in every loaded avr module that holds a reference.

    Patching where the object is *defined* plus every importer keeps the fakes valid
    when a refactor moves the construction to another module (the seam is the
    transport object, not the name in device.py).
    """
    count = 0
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "avr" or name.startswith("avr.")):
            continue
        for attr, value in list(vars(module).items()):
            if value is original:
                monkeypatch.setattr(module, attr, fake)
                count += 1
    return count


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch a network: any real connect attempt fails loudly."""

    def _refuse(*args, **_kw):
        raise AssertionError(f"real network access attempted in a test: {args[1:] or args}")

    async def _refuse_async(*args, **_kw):
        _refuse(None, *args)

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(asyncio, "open_connection", _refuse_async)
    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "create_connection", _refuse_async)


@pytest.fixture
def make_device(monkeypatch):
    """Build a real DenonAvrDevice wired to fakes for the given fixture set."""
    import avr.device as device_module
    import avr.transport.heos as heos_module
    import avr.transport.telnet as telnet_module

    def _make(fixture_name: str):
        fixtures = FixtureSet(fixture_name)
        FakeTelnet.instances.clear()
        FakeHeos.instances.clear()
        probe = FakeProbe(fixtures)
        assert replace_everywhere(monkeypatch, telnet_module.TelnetClient, FakeTelnet)
        assert replace_everywhere(monkeypatch, heos_module.HeosClient, FakeHeos)
        assert replace_everywhere(monkeypatch, telnet_module.async_probe, probe)
        session = FakeSession(fixtures)
        dev = device_module.DenonAvrDevice(session, HOST)
        dev.test_probe = probe
        dev.test_heos = FakeHeos.instances[-1]
        return dev, session, FakeTelnet.instances[-1], fixtures

    return _make


async def drain(device, rounds: int = 20) -> None:
    """Let scheduled tasks/debounce timers run so state settles deterministically."""
    for _ in range(rounds):
        pending = [t for t in getattr(device, "_pending_tasks", set()) if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


FIXTURE_SETS = sorted(p.name for p in FIXTURES.iterdir() if (p / "fixture.json").exists()) if FIXTURES.exists() else []
