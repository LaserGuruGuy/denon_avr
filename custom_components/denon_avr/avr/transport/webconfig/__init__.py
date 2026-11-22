"""Setup web interface transport: endpoint discovery + the /ajax document client."""

from __future__ import annotations

from .client import WebConfigClient
from .endpoint import WebEndpoint, async_discover_endpoint

__all__ = ["WebConfigClient", "WebEndpoint", "async_discover_endpoint"]
