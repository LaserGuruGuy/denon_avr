"""Discovery service: identity, capabilities and the telnet-introspected setup model.

Reads the goform Deviceinfo document, enriches the identity from the UPnP
description, discovers the web-config subsystems and fills the user-setup parts
(sources, speaker configuration, sound modes, quick selects) through a short
dedicated telnet probe. Every document is fetched through an injected callable,
so this service has no transport dependency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..models import AvrState, DeviceInfo, Discovery
from ..protocol import commands
from ..protocol.deviceinfo import parse_device_info
from ..protocol.profile import ProtocolProfile
from ..protocol.telnet_parser import TelnetParser
from ..protocol.upnp import parse_upnp_description
from .webconfig_doc import WebConfigSectionController

TextFetch = Callable[[], Awaitable[str | None]]
Probe = Callable[[list[str], Callable[[str], None], Callable[[], bool]], Awaitable[None]]


class DiscoveryService:
    def __init__(
        self,
        host: str,
        profile: ProtocolProfile,
        discovery: Discovery,
        state: AvrState,
        parser: TelnetParser,
        fetch_device_info: TextFetch,
        fetch_description: TextFetch,
        probe: Probe,
        web_subsystems: tuple[WebConfigSectionController, WebConfigSectionController],
    ) -> None:
        self._host = host
        self._profile = profile
        self._discovery = discovery
        self._state = state
        self._parser = parser
        self._fetch_device_info = fetch_device_info
        self._fetch_description = fetch_description
        self._probe = probe
        self._video_config, self._speaker_config = web_subsystems
        # Deviceinfo derived source list, used only if telnet introspection
        # yields no sources (see discover / await_ready).
        self._fallback_sources: list = []

    async def fetch_identity(self) -> DeviceInfo:
        """Just the device identity (model, MAC) over HTTP; raises ConnectionError."""

        xml_text = await self._fetch_device_info()
        if xml_text is None:
            raise ConnectionError(f"No Deviceinfo response from {self._host}")
        device = parse_device_info(xml_text).device
        self._require_identity(device)
        return device

    def _require_identity(self, device: DeviceInfo) -> None:
        """A document that does not parse, or names neither a model nor a MAC, is not
        a receiver's Deviceinfo (another host, a truncated answer): not reachable."""

        if device.model_name is None and device.mac_address is None:
            raise ConnectionError(f"No usable Deviceinfo document from {self._host}")

    async def discover(self) -> Discovery:
        """Populate the discovery model; raises ConnectionError when unreachable."""

        xml_text = await self._fetch_device_info()
        if xml_text is None:
            raise ConnectionError(f"No Deviceinfo response from {self._host}")
        # Tell the parser which feature blocks to read titles/labels from, using
        # the feature names the profile associates with its controls.
        controls = self._profile.controls.values()
        feature_names = {spec.feature for spec in controls if spec.feature}
        enum_features = {spec.feature for spec in controls if spec.kind == "enum" and spec.feature}
        discovered = parse_device_info(
            xml_text,
            feature_names,
            enum_features,
            receiver_type=self._profile.receiver_type,
        )
        self._require_identity(discovered.device)
        # Copy into the shared discovery object so the parser and entities keep
        # referencing a single instance.
        target = self._discovery
        target.device = discovered.device
        # Firmware version and serial: the goform document omits them, the UPnP
        # description carries them.
        await self._fetch_version_info()
        target.features = discovered.features
        target.channels = discovered.channels
        target.zones = discovered.zones
        target.feature_names = discovered.feature_names
        target.option_labels = discovered.option_labels
        target.numeric_meta = discovered.numeric_meta
        target.volume = discovered.volume
        target.sound_mode_genres = discovered.sound_mode_genres
        target.quick_select_names = dict(discovered.quick_select_names)
        target.subwoofer_max = discovered.subwoofer_max
        # Video setup availability + values; menus that are present become
        # discovery features (the telnet picture controls gate on PictureAdjust).
        await self._video_config.discover()
        target.features |= self._video_config.feature_flags()
        await self._speaker_config.discover()
        target.features |= self._speaker_config.feature_flags()
        # The authoritative source list comes from the telnet SSFUN/SSSOD
        # introspection; keep the Deviceinfo list only as a fallback.
        self._fallback_sources = discovered.sources
        target.sources = []
        await self._probe_introspection()
        return target

    async def _fetch_version_info(self) -> None:
        """Best effort; a missing field is left unset rather than guessed."""

        xml_text = await self._fetch_description()
        if not xml_text:
            return
        device = self._discovery.device
        for attr, value in parse_upnp_description(xml_text).items():
            if getattr(device, attr, None) is None:
                setattr(device, attr, value)

    async def _probe_introspection(self) -> None:
        """Fill the telnet-discovered model over a short-lived connection.

        Separate from the persistent connection so discovery is deterministic at
        setup time. Complete when the sentinel query has answered and no list
        that ends with END is still open (the probe deadline still bounds it).
        """

        sentinel = self._profile.introspection_sentinel
        queries = commands.introspection_queries(self._profile)
        done = [False]

        def on_line(line: str) -> None:
            self._parser.feed(line, self._state)
            if sentinel and line.startswith(sentinel["prefix"]):
                done[0] = True

        if sentinel:
            queries.append(sentinel["query"])
        # Complete when the sentinel has answered AND no END-terminated list is
        # still streaming: the sound mode lists stream asynchronously, between
        # other replies, so answer order alone is not enough.
        await self._probe(queries, on_line, lambda: done[0] and not self._parser.open_lists)

    async def await_ready(self, timeout: float) -> None:
        """Best effort wait until the initial resync populated the state.

        Waits for the core state, the channel calibration list (SSLEV) AND the
        speaker configuration (SSSPC), which decides which channel entities are
        enabled by default. Never raises.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if (
                self._state.system_power is not None
                and self._state.channel_levels
                and self._discovery.configured_channels
            ):
                break
            await asyncio.sleep(0.1)
        if not self._discovery.sources and self._fallback_sources:
            self._discovery.sources = self._fallback_sources
