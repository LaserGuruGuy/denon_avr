"""HTTP transport for the Denon AVR client library.

The goform HTTP API is used for two things only:

* Discovery: fetch the Deviceinfo XML once at setup so the receiver can describe
  its identity and capabilities.
* Reconciliation: poll the compact StatusLite endpoints so the integration can
  confirm the receiver is reachable and recover core state if the telnet push
  channel is temporarily down.

The heavy lifting (control and real time updates) is done over telnet; HTTP is
the stateless safety net.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import aiohttp

from .const import HTTP_DEVICEINFO_PATH, HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class HttpClient:
    """Minimal async client for the goform HTTP API."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int) -> None:
        self._session = session
        self._host = host
        self._base = f"http://{host}:{port}"

    async def async_get_device_info(self) -> str | None:
        """Fetch the raw Deviceinfo XML, or None on failure."""

        return await self._get_text(HTTP_DEVICEINFO_PATH)

    async def async_get_upnp_description(self, port: int, path: str) -> str | None:
        """Fetch a UPnP device description (holds firmware + serial number).

        This lives on a different port than the goform API, so the URL is built
        from the receiver host and the given UPnP port/path.
        """

        return await self._get_url(f"http://{self._host}:{port}{path}")

    async def async_get_status(self, path: str) -> dict[str, object] | None:
        """Fetch and parse the StatusLite snapshot at the given path.

        Returns a dict with any of the keys 'power', 'source', 'volume_db' and
        'muted', or None when the endpoint is unavailable.
        """

        text = await self._get_text(path)
        if text is None:
            return None
        return self._parse_status(text)

    async def _get_text(self, path: str) -> str | None:
        """Perform a GET against the goform base and return the body text."""

        return await self._get_url(f"{self._base}{path}")

    async def _get_url(self, url: str) -> str | None:
        """Perform a GET against an absolute URL, returning body text or None."""

        try:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            async with self._session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    _LOGGER.debug("GET %s returned HTTP %s", url, response.status)
                    return None
                return await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("GET %s failed: %s", url, err)
            return None

    @staticmethod
    def _parse_status(text: str) -> dict[str, object] | None:
        """Parse a StatusLite XML document into a partial state dict."""

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None

        def value_of(tag: str) -> str | None:
            node = root.find(f"{tag}/value")
            return node.text.strip() if node is not None and node.text else None

        result: dict[str, object] = {}
        power = value_of("Power")
        if power is not None:
            result["power"] = power.upper() == "ON"
        display = value_of("VolumeDisplay")
        if display is not None:
            result["volume_display"] = display
        source = value_of("InputFuncSelect")
        if source is not None:
            result["source"] = source
        mute = value_of("Mute")
        if mute is not None:
            result["muted"] = mute.lower() == "on"
        volume = value_of("MasterVolume")
        if volume is not None:
            try:
                # StatusLite reports the volume relative to the 0 dB reference.
                result["volume_db"] = float(volume)
            except ValueError:
                pass
        return result
