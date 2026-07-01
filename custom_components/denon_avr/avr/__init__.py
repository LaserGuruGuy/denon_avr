"""Own, dependency light client library for Denon AVR receivers.

This subpackage talks to the receiver directly over telnet (primary, with push)
and the goform HTTP API (discovery and reconciliation). It has no Home Assistant
dependency so it can be reused or published as a standalone library.

The public entry point is DenonAvrDevice. Everything device specific (names,
ranges, options, which controls exist) is discovered from the receiver at
runtime; the only fixed protocol grammar lives in protocol_profile.json.
"""

from __future__ import annotations

from .device import DenonAvrDevice
from .models import (
    AvrState,
    ChannelDescriptor,
    Discovery,
    DeviceInfo,
    SourceDescriptor,
    ZoneDescriptor,
    ZoneState,
)

__all__ = [
    "DenonAvrDevice",
    "AvrState",
    "ChannelDescriptor",
    "Discovery",
    "DeviceInfo",
    "SourceDescriptor",
    "ZoneDescriptor",
    "ZoneState",
]
