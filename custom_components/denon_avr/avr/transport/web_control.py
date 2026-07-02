"""Read-only client for the receiver's HTTPS web control /ajax config API.

Kept isolated from the goform HTTP client (port 8080) and the telnet channel. It
performs one non-disruptive read at discovery: the selectable speaker crossover
frequency set. That set is not obtainable over the other channels — telnet
(SSCFR) and the length-framed TCP status reads report only the *current*
crossover, never the allowed values (verified live) — so this web config, which
the receiver's own Speakers page uses, is the only non-hardcoded source. All
control still goes over telnet; this module only ever reads.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import aiohttp

from ..const import HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# The receiver's HTTPS web control app uses a self signed certificate. Its /ajax
# speaker config is the same API the built in Speakers page uses; type=6 selects
# the crossover config, whose <SelectableValue> lists the allowed frequencies.
_PORT = 10443
_SPEAKER_CONFIG_PATH = "/ajax/speakers/get_config"
_CROSSOVER_CONFIG_TYPE = "6"


class WebControlClient:
    """Minimal read-only client for the HTTPS web control /ajax config API."""

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        self._session = session
        self._host = host

    async def async_get_crossover_values(self) -> list[int] | None:
        """Return the selectable crossover frequencies (Hz), or None on failure.

        None is returned on any error or when the setup is locked (HTTP 423), so
        the caller can fall back. The '0' item (the web app's "Full Band" pseudo
        option, i.e. no crossover) is dropped: that is a speaker size, not a
        crossover frequency the SSCFR command can set.
        """

        url = (
            f"https://{self._host}:{_PORT}{_SPEAKER_CONFIG_PATH}"
            f"?type={_CROSSOVER_CONFIG_TYPE}"
        )
        try:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            async with self._session.get(url, timeout=timeout, ssl=False) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "Web control GET %s returned HTTP %s", url, response.status
                    )
                    return None
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Web control GET %s failed: %s", url, err)
            return None
        return self._parse_crossover_values(text)

    @staticmethod
    def _parse_crossover_values(text: str) -> list[int] | None:
        """Parse the crossover config XML into the selectable Hz value list."""

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None
        values: list[int] = []
        for item in root.findall("SelectableValue/List/Item"):
            token = (item.text or "").strip()
            if token.isdigit() and int(token) > 0:
                values.append(int(token))
        return values or None
