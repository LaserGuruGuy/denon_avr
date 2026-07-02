"""Shared transport timing constants for the Denon AVR client library.

Only timing and backoff facts live here, shared across the transports. Each
transport owns its own endpoint (TCP port and paths); see the modules in the
`transport` subpackage. All protocol grammar (command tokens, enum values,
introspection queries) lives in the external data file protocol_profile.json,
and all device specific configuration is discovered at runtime from the receiver.
"""

from __future__ import annotations

from typing import Final

# Telnet needs a small gap between commands or it will silently drop them, so
# outgoing commands are serialised through a queue with COMMAND_SPACING seconds
# between each one. 0.1 s is a safe compromise between reliability and how
# quickly the full resync completes.
COMMAND_SPACING: Final = 0.10
CONNECT_TIMEOUT: Final = 5.0
HTTP_TIMEOUT: Final = 8.0

# If nothing arrives for this long we send a light probe to keep the socket
# alive and to detect a silently dropped link.
KEEPALIVE_INTERVAL: Final = 30.0

# Reconnect backoff grows from MIN to MAX after each failed attempt.
RECONNECT_BACKOFF_MIN: Final = 1.0
RECONNECT_BACKOFF_MAX: Final = 60.0
