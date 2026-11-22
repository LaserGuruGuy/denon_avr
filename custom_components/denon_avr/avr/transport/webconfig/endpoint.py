"""Locate the receiver's setup web interface (the ``/ajax`` config endpoint).

Receiver generations serve the setup interface on different endpoints (for example
HTTPS 10443 on older firmware, plain HTTP 11080 on the newer "Web Control" app).
Both put an nginx front on port 80/443 that redirects ``/`` to the real
interface, so following that redirect is the generic, model-independent way to
find it.

Rules:
* A redirect is followed only to the SAME host over http/https, at most
  ``MAX_HOPS`` times (``http://h/`` -> ``https://h/`` -> ``https://h:10443`` is a
  normal nginx chain). A redirect to any other host is never followed.
* The front ports (80/443) are never the result: their ``/ajax`` answers 403.
  Ports of the receiver's other services (telnet, HEOS, JSON, goform, UPnP) are
  refused, so a stray redirect can never send HTTP into the telnet session.
* The target must answer ``GET /`` with 200 before it is accepted.
* Fallback for firmware without the front: the known endpoints in
  ``FALLBACK_CANDIDATES``.
* Both fronts, and then all candidates, are probed concurrently with a short
  per-request timeout, and the whole discovery has a time budget, so a receiver
  without a web interface delays setup by at most ``DISCOVERY_BUDGET`` seconds.

No state; every failure returns None.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

_REDIRECTS = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = {"http": 80, "https": 443}
FRONT_PORTS = frozenset({80, 443})
# Ports of the receiver's other services: never a web-interface target.
RESERVED_PORTS = frozenset({23, 1255, 1256, 8080, 60006})
ENTRY_POINTS = ("http", "https")
FALLBACK_CANDIDATES = (("https", 10443), ("http", 11080))
MAX_HOPS = 3
PROBE_TIMEOUT = 2.0
DISCOVERY_BUDGET = 6.0


@dataclass(frozen=True)
class WebEndpoint:
    """Where the setup web interface lives, and how that was determined."""

    scheme: str
    host: str
    port: int
    source: str  # "redirect" (from the port 80/443 front) or "candidate" (fallback probe)

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{_netloc(self.host)}:{self.port}"


def _netloc(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _same_host(hostname: str | None, host: str) -> bool:
    return bool(hostname) and hostname.lower() == host.strip("[]").lower()


def resolve_location(location: str | None, request_url: str, host: str) -> tuple[str, int] | None:
    """(scheme, port) of a redirect target, only when it stays on ``host`` over http(s)."""

    if not location:
        return None
    parts = urlsplit(urljoin(request_url, location))
    scheme = (parts.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    if not _same_host(parts.hostname, host):
        _LOGGER.debug("Ignoring setup-UI redirect to another host: %s", parts.hostname)
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = _DEFAULT_PORTS[scheme]
    if not 0 < port < 65536 or port in RESERVED_PORTS:
        return None
    return scheme, port


def endpoint_from_location(location: str | None, host: str, request_url: str | None = None) -> WebEndpoint | None:
    """A redirect target as a candidate endpoint (not yet verified), or None."""

    resolved = resolve_location(location, request_url or f"http://{_netloc(host)}/", host)
    if resolved is None or resolved[1] in FRONT_PORTS:
        return None
    return WebEndpoint(resolved[0], host.strip("[]"), resolved[1], "redirect")


async def _head(session: aiohttp.ClientSession, url: str) -> tuple[int, str | None] | None:
    try:
        async with session.get(
            url,
            allow_redirects=False,
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT),
        ) as response:
            return response.status, response.headers.get("Location")
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        _LOGGER.debug("Setup-UI probe %s failed: %s", url, err)
        return None


async def _answers(session: aiohttp.ClientSession, endpoint: WebEndpoint) -> bool:
    result = await _head(session, f"{endpoint.base_url}/")
    return result is not None and result[0] == 200


async def _follow_front(session: aiohttp.ClientSession, host: str, scheme: str) -> WebEndpoint | None:
    url = f"{scheme}://{_netloc(host)}/"
    for _ in range(MAX_HOPS):
        result = await _head(session, url)
        if result is None or result[0] not in _REDIRECTS:
            return None
        resolved = resolve_location(result[1], url, host)
        if resolved is None:
            return None
        target_scheme, port = resolved
        if port in FRONT_PORTS:
            # Front to front (http -> https): follow the chain.
            url = f"{target_scheme}://{_netloc(host)}:{port}/"
            continue
        endpoint = WebEndpoint(target_scheme, host.strip("[]"), port, "redirect")
        return endpoint if await _answers(session, endpoint) else None
    return None


async def _first(tasks: list) -> WebEndpoint | None:
    """The first non-None result in the given ORDER (deterministic preference)."""

    for result in await asyncio.gather(*tasks):
        if result is not None:
            return result
    return None


async def _discover(session: aiohttp.ClientSession, host: str) -> WebEndpoint | None:
    endpoint = await _first([_follow_front(session, host, scheme) for scheme in ENTRY_POINTS])
    if endpoint is not None:
        return endpoint
    candidates = [WebEndpoint(scheme, host.strip("[]"), port, "candidate") for scheme, port in FALLBACK_CANDIDATES]

    async def _check(candidate: WebEndpoint) -> WebEndpoint | None:
        return candidate if await _answers(session, candidate) else None

    return await _first([_check(c) for c in candidates])


async def async_discover_endpoint(session: aiohttp.ClientSession, host: str) -> WebEndpoint | None:
    """Find the setup web interface of ``host``; None when there is none (within the budget)."""

    try:
        return await asyncio.wait_for(_discover(session, host), DISCOVERY_BUDGET)
    except asyncio.TimeoutError:
        _LOGGER.debug("Setup-UI discovery of %s exceeded %.0f s", host, DISCOVERY_BUDGET)
        return None
