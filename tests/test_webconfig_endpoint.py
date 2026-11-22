"""Locating the setup web interface: redirect chain, safety, fallback, time budget, re-probe."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

import conftest  # noqa: F401  # pyflakes: ignore - loads the avr package
from avr.transport.webconfig import WebConfigClient, async_discover_endpoint
from avr.transport.webconfig import client as client_module
from avr.transport.webconfig import endpoint as endpoint_module
from avr.transport.webconfig.endpoint import endpoint_from_location


class _Resp:
    def __init__(self, status, headers=None, body="", delay=0.0, gauge=None):
        self.status, self.headers, self._body, self._delay, self._gauge = status, headers or {}, body, delay, gauge

    async def text(self):
        return self._body

    async def __aenter__(self):
        if self._gauge is not None:
            self._gauge["now"] += 1
            self._gauge["max"] = max(self._gauge["max"], self._gauge["now"])
        await asyncio.sleep(self._delay)
        return self

    async def __aexit__(self, *exc):
        if self._gauge is not None:
            self._gauge["now"] -= 1
        return False


class _Session:
    """URL (without query) -> (status, headers[, body]) or None (connection refused)."""

    def __init__(self, routes, delay=0.0, gauge=None):
        self.routes, self.delay, self.gauge = routes, delay, gauge
        self.urls: list[str] = []

    def get(self, url, **_kw):
        self.urls.append(url)
        hit = self.routes.get(url.split("?")[0])
        if hit is None:
            raise aiohttp.ClientConnectionError(url)
        return _Resp(*hit, delay=self.delay, gauge=self.gauge)


LEGACY = {"http://h/": (301, {"Location": "https://h:10443"}), "https://h/": (301, {"Location": "https://h:10443"}), "https://h:10443/": (200, {})}
WEB_CONTROL = {"http://h/": (301, {"Location": "http://h:11080"}), "https://h/": (301, {"Location": "http://h:11080"}), "http://h:11080/": (200, {})}


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("https://10.0.0.5:10443", "https://10.0.0.5:10443"),
        ("http://10.0.0.5:11080/", "http://10.0.0.5:11080"),
        ("https://10.0.0.5/", None),               # a front port is never the interface
        ("https://evil.example:10443/", None),     # never leave the receiver
        ("http://10.0.0.5:23/", None),             # never HTTP into the telnet session
        ("http://10.0.0.5:8080/", None),
        ("http://10.0.0.5:0/", None),
        ("ftp://10.0.0.5:21/", None),
        ("//10.0.0.5:11080/", "http://10.0.0.5:11080"),   # scheme-relative
        ("", None),
        (None, None),
    ],
)
def test_endpoint_from_location(location, expected):
    endpoint = endpoint_from_location(location, "10.0.0.5")
    assert (endpoint.base_url if endpoint else None) == expected


def test_ipv6_host():
    endpoint = endpoint_from_location("http://[fe80::1]:11080/", "fe80::1")
    assert endpoint is not None and endpoint.base_url == "http://[fe80::1]:11080"


@pytest.mark.parametrize(("routes", "expected"), [(LEGACY, "https://h:10443"), (WEB_CONTROL, "http://h:11080")])
async def test_both_generations(routes, expected):
    endpoint = await async_discover_endpoint(_Session(routes), "h")
    assert endpoint.base_url == expected and endpoint.source == "redirect"


async def test_nginx_chain_http_to_https_to_interface():
    s = _Session({
        "http://h/": (301, {"Location": "https://h/"}),
        "https://h/": (301, {"Location": "https://h:10443"}),
        "https://h:443/": (301, {"Location": "https://h:10443"}),
        "https://h:10443/": (200, {}),
    })
    endpoint = await async_discover_endpoint(s, "h")
    assert endpoint.base_url == "https://h:10443"


async def test_relative_location():
    s = _Session({"http://h/": (302, {"Location": "/"}), "https://h/": (301, {"Location": "http://h:11080/"}), "http://h:11080/": (200, {})})
    assert (await async_discover_endpoint(s, "h")).base_url == "http://h:11080"


async def test_target_must_answer():
    s = _Session({"http://h/": (301, {"Location": "http://h:11080"}), "https://h/": (301, {"Location": "http://h:11080"})})
    assert await async_discover_endpoint(s, "h") is None


async def test_foreign_redirect_falls_back_to_candidates():
    s = _Session({
        "http://h/": (301, {"Location": "http://elsewhere:11080"}),
        "https://h/": (301, {"Location": "https://elsewhere/"}),
        "https://h:10443/": (200, {}),
    })
    endpoint = await async_discover_endpoint(s, "h")
    assert endpoint.base_url == "https://h:10443" and endpoint.source == "candidate"
    assert not any("elsewhere" in u for u in s.urls)


async def test_no_web_interface():
    assert await async_discover_endpoint(_Session({}), "h") is None


async def test_discovery_time_budget(monkeypatch):
    monkeypatch.setattr(endpoint_module, "DISCOVERY_BUDGET", 0.2)
    slow = _Session({"http://h/": (301, {"Location": "http://h:11080"}), "http://h:11080/": (200, {})}, delay=5.0)
    loop = asyncio.get_running_loop()
    start = loop.time()
    assert await async_discover_endpoint(slow, "h") is None
    assert loop.time() - start < 1.0


async def test_requests_are_serialised():
    gauge = {"now": 0, "max": 0}
    routes = dict(WEB_CONTROL, **{"http://h:11080/ajax/video/get_config": (200, {}, "<x/>")})
    web = WebConfigClient(_Session(routes, delay=0.01, gauge=gauge), "h")
    await web.async_get("video", 1)              # locate first
    gauge["max"] = 0
    results = await asyncio.gather(*(web.async_get("video", t) for t in range(5)))
    assert results == ["<x/>"] * 5 and gauge["max"] == 1


async def test_client_reprobes_after_failure_but_rate_limited(monkeypatch):
    clock = [1000.0]
    real = asyncio.get_running_loop()

    class _Loop:
        def time(self):
            return clock[0]

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(client_module.asyncio, "get_running_loop", lambda: _Loop())
    routes = dict(WEB_CONTROL, **{"http://h:11080/ajax/video/get_config": (200, {}, "<x/>")})
    s = _Session(routes)
    web = WebConfigClient(s, "h")
    assert await web.async_get("video", 1) == "<x/>"
    # the interface disappears: the endpoint is dropped after the transport failure ...
    del routes["http://h:11080/ajax/video/get_config"]
    assert await web.async_get("video", 1) is None and web.endpoint is None
    # ... and re-located on the next request once the interval has passed
    clock[0] += client_module.REPROBE_INTERVAL + 1
    routes["http://h:11080/ajax/video/get_config"] = (200, {}, "<y/>")
    assert await web.async_get("video", 1) == "<y/>"
    # with no interface at all, discovery runs at most once per interval
    s2 = _Session({})
    web2 = WebConfigClient(s2, "h")
    for _ in range(5):
        assert await web2.async_get("video", 1) is None
    assert len(s2.urls) == 4          # one discovery (2 fronts + 2 candidates), no repeats


@pytest.mark.parametrize(("fixture", "url", "scheme", "port"), [
    ("avr_x3600h", "https://192.0.2.10:10443/", "https", 10443),
    ("avc_x3900h", "http://192.0.2.10:11080/", "http", 11080),
])
async def test_device_configuration_url(make_device, fixture, url, scheme, port):
    dev, _session, _telnet, _fx = make_device(fixture)
    assert dev.configuration_url is None and dev.setup_interface is None
    await dev.async_discover()
    assert dev.configuration_url == url
    assert dev.setup_interface == {"scheme": scheme, "port": port, "source": "redirect"}


async def test_reachable_semantics():
    routes = dict(WEB_CONTROL, **{"http://h:11080/ajax/video/get_config": (500, {}, "")})
    web = WebConfigClient(_Session(routes), "h")
    assert web.reachable is None
    assert await web.async_get("video", 1) is None and web.reachable is True     # answered (HTTP 500)
    del routes["http://h:11080/ajax/video/get_config"]
    assert await web.async_get("video", 1) is None and web.reachable is False    # transport failure
    nothing = WebConfigClient(_Session({}), "h")
    assert await nothing.async_get("video", 1) is None and nothing.reachable is False


async def test_offline_receiver_makes_web_unreachable(make_device, monkeypatch):
    """goform AND web gone: the poll still re-judges the web, so web entities do not
    stay available with stale values."""
    dev, session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    assert dev.web_reachable is True
    monkeypatch.setattr(type(fx), "http", lambda self, url: None)                # everything refuses
    await dev.async_poll()
    assert dev.web_reachable is False
