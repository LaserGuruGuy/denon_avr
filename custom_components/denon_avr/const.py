"""Constants for the Denon AVR integration.

Only Home Assistant level constants live here. All protocol grammar is in
avr/protocol_profile.json and all device specific configuration is discovered
from the receiver at runtime.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "denon_avr"

# The config entry stores just the receiver address; everything else is
# discovered from the device.
CONF_HOST = "host"

# Options (set via the integration's Configure dialog).
# Adaptive sound mode wire learning: when on, the integration may learn the MS
# wire token for a mode from what the receiver reports. Off by default for
# predictability; deterministic profile overrides + the display name are used.
CONF_SOUND_MODE_LEARNING = "sound_mode_learning"
DEFAULT_SOUND_MODE_LEARNING = False

# Platforms this integration provides.
PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
]

# How often the HTTP reconciliation poll runs. Real time updates arrive over
# telnet push; this poll only confirms reachability and recovers state when the
# push channel is temporarily down.
RECONCILE_INTERVAL = timedelta(seconds=60)
