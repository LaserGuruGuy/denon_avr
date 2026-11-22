"""The transports structurally satisfy the service ports (names and parameters)."""

from __future__ import annotations

import inspect

import conftest  # noqa: F401  # pyflakes: ignore - loads the avr package
from avr.services.ports import CommandSink, ConfigDocumentPort
from avr.transport.telnet import TelnetClient
from avr.transport.webconfig import WebConfigClient


def _assert_conforms(impl: type, port: type) -> None:
    for name, member in vars(port).items():
        if name.startswith("_") or not callable(member):
            continue
        assert hasattr(impl, name), f"{impl.__name__} lacks {name} required by {port.__name__}"
        want = list(inspect.signature(member).parameters)
        have = list(inspect.signature(getattr(impl, name)).parameters)
        assert have[: len(want)] == want, f"{impl.__name__}.{name}{have} != {port.__name__}.{name}{want}"
        assert inspect.iscoroutinefunction(getattr(impl, name)) == inspect.iscoroutinefunction(member)


def test_webconfig_client_is_a_config_document_port():
    _assert_conforms(WebConfigClient, ConfigDocumentPort)


def test_telnet_client_is_a_command_sink():
    _assert_conforms(TelnetClient, CommandSink)
