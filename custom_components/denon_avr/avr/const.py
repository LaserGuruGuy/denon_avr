"""Shared transport timing constants for the Denon AVR client library."""

from __future__ import annotations

from typing import Final

# Telnet needs a small gap between commands or it will silently drop them, so outgoing commands
# are serialised through a queue with COMMAND_SPACING seconds between each one.
COMMAND_SPACING: Final = 0.10
CONNECT_TIMEOUT: Final = 5.0
HTTP_TIMEOUT: Final = 8.0

# If nothing arrives for this long we send a light probe to keep the socket alive and to detect a
# silently dropped link.
KEEPALIVE_INTERVAL: Final = 30.0
# A probe waits this long for any line, and this many unanswered probes end the link.
KEEPALIVE_TIMEOUT: Final = 10.0
KEEPALIVE_MISSES: Final = 2

# Reconnect backoff grows from MIN to MAX after each failed attempt.
RECONNECT_BACKOFF_MIN: Final = 1.0
RECONNECT_BACKOFF_MAX: Final = 60.0
