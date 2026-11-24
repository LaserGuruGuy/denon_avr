"""HA-free client library for Denon/Marantz receivers.

This module is the ONLY import surface for the Home Assistant layer. Layers:
transport/ (I/O per wire protocol) -> protocol/ (pure grammar) -> services/
(subsystems over ports) -> device.py (facade and composition root).
"""

from __future__ import annotations

from .device import DenonAvrDevice
from .models import (
    Availability,
    AvrState,
    ChannelDescriptor,
    CollectionItem,
    DeviceInfo,
    Discovery,
    SourceDescriptor,
    ZoneDescriptor,
    ZoneState,
)
from .protocol.channel_map import ChannelMap, decode as decode_channels
from .protocol.eq_doc import band_tag as eq_band_tag
from .protocol.eq_doc import channel_options as eq_channel_options
from .protocol.profile import ControlSpec, ProtocolProfile, load_profile

__all__ = [
    "Availability",
    "AvrState",
    "ChannelDescriptor",
    "CollectionItem",
    "ControlSpec",
    "DenonAvrDevice",
    "DeviceInfo",
    "Discovery",
    "ProtocolProfile",
    "SourceDescriptor",
    "ZoneDescriptor",
    "ZoneState",
    "ChannelMap",
    "decode_channels",
    "eq_band_tag",
    "eq_channel_options",
    "load_profile",
]
