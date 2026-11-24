"""Setup web interface document client (``/ajax/<section>/{get,set}_config``).

The receiver's setup web interface reads and writes configuration that has no
telnet command through a small document API, distinct from telnet and goform: a
GET of ``/ajax/<section>/get_config?type=<id>`` returns an XML document, and a GET
of ``/ajax/<section>/set_config?type=<id>&data=<url-encoded XML>`` applies a change
(the interface rejects POST). This is the SECONDARY channel: telnet remains the
primary, documented control interface; this one only supplements it.

The endpoint differs per interface generation and is located by
:func:`.endpoint.async_discover_endpoint` on first use. Requests are serialised
(one at a time towards the receiver). When the receiver stops answering at the
transport level, the endpoint is re-located, rate-limited by ``REPROBE_DELAYS``,
because a firmware update can move the interface. This client does not know the
meaning of any section, config id or XML tag. An HTTPS endpoint uses a self-signed
certificate, so verification is disabled. Every failure is swallowed and surfaced
as ``None``/``False``, so a problem here can never affect the telnet control path.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import aiohttp

from ...const import HTTP_TIMEOUT
from .endpoint import WebEndpoint, async_discover_endpoint

_LOGGER = logging.getLogger(__name__)

# Minimum time between two endpoint discoveries (seconds), by consecutive failures
# (a missed discovery or a transport failure): soon again after the first, since a
# busy receiver can miss one, then less often.
# The first delay exceeds a slow setup, so setup never runs discovery twice; the
# poll (every 60 s) is what retries, hence just under its multiples.
REPROBE_DELAYS = (50.0, 110.0, 300.0)


class WebConfigClient:
    """Minimal async client for the setup-UI /ajax config API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        endpoint: WebEndpoint | None = None,
    ) -> None:
        self._session = session
        self._host = host
        # A given endpoint is fixed (no discovery); otherwise it is located lazily.
        self._fixed = endpoint is not None
        self._endpoint = endpoint
        self._probed_at: float | None = None
        self._misses = 0          # consecutive failures (nothing found / transport error)
        # One request at a time towards the receiver; discovery has its own lock so
        # a slow discovery never holds up requests on an already known endpoint.
        self._request_lock = asyncio.Lock()
        self._discovery_lock = asyncio.Lock()
        # Outcome of the last request at the transport level: True = the
        # interface answered (any HTTP status), False = it did not / none found,
        # None = not tried yet.
        self._reachable: bool | None = None
        # Last outcome per document path (diagnostics): HTTP status or "error".
        self.last_status: dict[str, object] = {}

    @property
    def reachable(self) -> bool | None:
        """Whether the setup web interface answered the last request."""

        return self._reachable

    @property
    def endpoint(self) -> WebEndpoint | None:
        """The located setup web interface, or None (not located / not present)."""

        return self._endpoint

    async def async_get(
        self, section: str, type_id: int, extra: dict[str, str] | None = None
    ) -> str | None:
        """Read one config group as raw XML text, or None on any failure.

        ``extra`` adds query parameters, e.g. ``{"opt1": "1", "opt2": "0"}`` to
        select the graphic-EQ channel to read.
        """

        params = {"type": type_id}
        if extra:
            params.update(extra)
        return await self._get(f"/ajax/{section}/get_config", params)

    async def async_set(self, section: str, type_id: int, xml: str) -> bool:
        """Apply an XML fragment for one config group. Return True on HTTP 200.

        Both reads and writes are GETs on this interface (the setup UI's jQuery
        call defaults to GET); a POST is rejected with HTTP 400. The XML is just
        another query parameter, so this shares the same request path as a read.
        """

        return await self._get(
            f"/ajax/{section}/set_config", {"type": type_id, "data": xml}
        ) is not None

    async def _get(self, path: str, params: dict[str, object]) -> str | None:
        """Issue a GET and return the body text, or None on error/non-200.

        The one request path for both reads and writes. Parameters are
        percent-encoded fully (encodeURIComponent-equivalent, ``safe=''``), which
        the setup interface expects for the XML payload and is harmless for the
        simple read parameters.
        """

        query = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params.items())
        endpoint = await self._ensure_endpoint()
        if endpoint is None:
            self._reachable = False
            return None
        url = f"{endpoint.base_url}{path}?{query}"
        async with self._request_lock:
            try:
                async with self._session.get(
                    url, ssl=False, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
                ) as response:
                    self._reachable = True
                    self._misses = 0
                    self.last_status[f"{path}?type={params.get('type')}"] = response.status
                    if response.status != 200:
                        _LOGGER.debug("%s returned HTTP %s", path, response.status)
                        return None
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.debug("GET %s failed: %s", path, err)
                self._reachable = False
                self.last_status[f"{path}?type={params.get('type')}"] = "error"
                self._misses += 1
                self._invalidate(endpoint)
                return None

    async def _ensure_endpoint(self) -> WebEndpoint | None:
        """The endpoint, located on first use and re-located after it went stale."""

        if self._endpoint is not None or self._fixed:
            return self._endpoint
        async with self._discovery_lock:
            if self._endpoint is not None:      # located while we waited
                return self._endpoint
            now = asyncio.get_running_loop().time()
            delay = REPROBE_DELAYS[min(max(self._misses, 1), len(REPROBE_DELAYS)) - 1]
            if self._probed_at is not None and now - self._probed_at < delay:
                return None
            self._probed_at = now   # also when the discovery is cancelled
            self._endpoint = await async_discover_endpoint(self._session, self._host)
            self._probed_at = asyncio.get_running_loop().time()
            if self._endpoint is None:
                self._misses += 1
                _LOGGER.debug("No setup web interface found on %s", self._host)
            else:
                _LOGGER.debug("Setup web interface of %s: %s (%s)", self._host, self._endpoint.base_url, self._endpoint.source)
            return self._endpoint

    def _invalidate(self, failed: WebEndpoint) -> None:
        """Forget a discovered endpoint after a transport failure (re-located later)."""

        if not self._fixed and self._endpoint == failed:
            self._endpoint = None
