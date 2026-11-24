"""Constants for the Denon AVR integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import CONF_HOST, Platform

DOMAIN = "denon_avr"



# Platforms this integration provides.
PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
]

# How often the HTTP reconciliation poll runs.
RECONCILE_INTERVAL = timedelta(seconds=60)
