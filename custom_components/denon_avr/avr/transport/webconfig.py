"""Setup-UI configuration transport (HTTPS port 10443).

The receiver's setup web interface reads and writes audio configuration through
a small config API that is distinct from telnet and goform: a GET of
``/ajax/<section>/get_config?type=<id>`` returns an XML document, and a POST to
``/ajax/<section>/set_config`` with a form body ``type=<id>&data=<url-encoded
XML>`` applies a change. Sections are ``audio`` and ``globals``; each config
``type`` selects one settings group (for example the graphic equaliser).

Neither the read nor the write enters a calibration session, so both are
non-disruptive and safe during playback. This transport owns that HTTPS endpoint
only; it does not know the meaning of any config id or XML tag (that lives with
the caller). The endpoint uses a self-signed certificate, so verification is
disabled. Every failure is swallowed and surfaced as ``None``/``False`` so a
setup-UI problem can never affect the telnet control path.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import aiohttp

from ..const import HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# The setup web interface listens on this HTTPS port with a self-signed cert.
DEFAULT_PORT = 10443


class WebConfigClient:
    """Minimal async client for the setup-UI /ajax config API."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, port: int = DEFAULT_PORT
    ) -> None:
        self._session = session
        self._base = f"https://{host}:{port}"

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
        url = f"{self._base}{path}?{query}"
        try:
            async with self._session.get(
                url, ssl=False, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            ) as response:
                if response.status != 200:
                    _LOGGER.debug("%s returned HTTP %s", path, response.status)
                    return None
                return await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("GET %s failed: %s", path, err)
            return None
