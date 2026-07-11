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

    async def async_get(self, section: str, type_id: int) -> str | None:
        """GET one config group as raw XML text, or None on any failure."""

        url = f"{self._base}/ajax/{section}/get_config"
        try:
            async with self._session.get(
                url,
                params={"type": str(type_id)},
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "get_config %s type=%s returned HTTP %s",
                        section,
                        type_id,
                        response.status,
                    )
                    return None
                return await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("get_config %s type=%s failed: %s", section, type_id, err)
            return None

    async def async_set(self, section: str, type_id: int, xml: str) -> bool:
        """Apply an XML fragment for one config group. Return True on HTTP 200.

        The setup UI issues this as a GET with the payload in the query string
        (its jQuery call defaults to GET), not a POST; a POST is rejected with
        HTTP 400. The XML is percent-encoded as a single query parameter.
        """

        url = f"{self._base}/ajax/{section}/set_config"
        query = f"type={type_id}&data={quote(xml, safe='')}"
        try:
            async with self._session.get(
                f"{url}?{query}",
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "set_config %s type=%s returned HTTP %s",
                        section,
                        type_id,
                        response.status,
                    )
                return response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("set_config %s type=%s failed: %s", section, type_id, err)
            return False
