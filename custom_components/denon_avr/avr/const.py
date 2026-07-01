"""Transport level constants for the Denon AVR client library.

This module intentionally holds only transport facts: which TCP ports to use and
the timing/backoff values for the connection. All protocol grammar (command
tokens, enum values, introspection queries) lives in the external data file
protocol_profile.json, and all device specific configuration (names, ranges,
options, which controls exist) is discovered at runtime from the receiver.
"""

from __future__ import annotations

from typing import Final

# Network ports. Port 23 is the telnet control channel (primary transport with
# push). Port 8080 exposes the goform HTTP API (used for discovery and polling).
TELNET_PORT: Final = 23
HTTP_PORT: Final = 8080

# The Deviceinfo discovery endpoint. The per zone StatusLite paths are described
# in the protocol profile because their count depends on the discovered zones.
HTTP_DEVICEINFO_PATH: Final = "/goform/Deviceinfo.xml"

# Timing. The Denon telnet server needs a small gap between commands or it will
# silently drop them, so outgoing commands are serialised through a queue with
# COMMAND_SPACING seconds between each one. 0.1 s is a safe compromise between
# reliability and how quickly the full resync completes.
COMMAND_SPACING: Final = 0.10
CONNECT_TIMEOUT: Final = 5.0
HTTP_TIMEOUT: Final = 8.0

# If nothing arrives for this long we send a light probe to keep the socket
# alive and to detect a silently dropped link.
KEEPALIVE_INTERVAL: Final = 30.0

# Reconnect backoff grows from MIN to MAX after each failed attempt.
RECONNECT_BACKOFF_MIN: Final = 1.0
RECONNECT_BACKOFF_MAX: Final = 60.0
