"""Wire transports for the Denon AVR client library.

Each module owns exactly one way of talking to the receiver, isolated from the
others so a change to one channel cannot affect the rest:

* telnet      - the primary control channel (port 23), stateful with push
* goform      - the goform HTTP API (port 8080): discovery + reconciliation poll
* upnp        - the UPnP/AIOS device description (port 60006): firmware + serial
* tcp_client  - the length-framed JSON protocol (port 1256), setup/status reads
* web_control - the HTTPS web /ajax config (port 10443), read-only, last resort
                for data no other channel exposes (the crossover selectable set)

Each transport knows only its own wire format and endpoint (it owns its own port
and paths); none of them knows the protocol grammar (that lives in the profile
and parser) or Home Assistant.
"""

from __future__ import annotations

from .goform import GoformClient
from .tcp_client import TcpClient
from .telnet import TelnetClient, async_probe
from .upnp import UpnpClient
from .web_control import WebControlClient

__all__ = [
    "GoformClient",
    "TcpClient",
    "TelnetClient",
    "UpnpClient",
    "WebControlClient",
    "async_probe",
]
