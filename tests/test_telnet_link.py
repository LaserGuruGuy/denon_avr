"""Telnet link supervision: a dead peer is noticed, old commands are not replayed."""

from __future__ import annotations

import asyncio

import pytest

import avr.transport.telnet as telnet_module
from avr.protocol.deviceinfo import _format_mac, parse_device_info
from avr.services.discovery import DiscoveryService
from avr.transport.telnet import TelnetClient


class _Writer:
    def __init__(self, reader, answer):
        self.reader, self.answer, self.sent = reader, answer, []

    def write(self, data):
        self.sent.append(data)
        if self.answer:
            self.reader.feed_data(b"PWON\r")

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(telnet_module, "KEEPALIVE_INTERVAL", 0.06)
    monkeypatch.setattr(telnet_module, "KEEPALIVE_TIMEOUT", 0.04)
    monkeypatch.setattr(telnet_module, "COMMAND_SPACING", 0.0)


def _client(monkeypatch, answer):
    peers, states = [], []

    async def open_connection(_host, _port):
        reader = asyncio.StreamReader()
        writer = _Writer(reader, answer)
        peers.append(writer)
        return reader, writer

    monkeypatch.setattr(telnet_module.asyncio, "open_connection", open_connection)
    client = TelnetClient("h", on_line=lambda _l: None, on_availability=states.append)
    return client, peers, states


async def test_dead_peer_is_noticed(fast, monkeypatch):
    client, _peers, states = _client(monkeypatch, answer=False)
    await client.async_start()
    await asyncio.sleep(1.6)           # longer than the first backoff
    assert len(_peers) > 1          # the dead link was given up and tried again
    await client.async_stop()
    assert states == [], "a peer that never spoke was never available, so nothing flaps"


async def test_answering_peer_stays_connected(fast, monkeypatch):
    client, peers, states = _client(monkeypatch, answer=True)
    await client.async_start()
    await asyncio.sleep(0.5)
    assert client.connected and states == [True] and len(peers) == 1
    await client.async_stop()


async def test_old_commands_are_not_replayed_after_a_reconnect(fast, monkeypatch):
    client, peers, _states = _client(monkeypatch, answer=True)
    await client.async_send("MV80")                      # queued while not connected
    await client.async_start()
    await asyncio.sleep(0.03)
    await client.async_stop()
    assert b"MV80\r" not in peers[0].sent


async def test_backoff_resets_only_after_an_answer(monkeypatch):
    delays = []
    real = asyncio.sleep
    monkeypatch.setattr(telnet_module, "KEEPALIVE_TIMEOUT", 0.04)    # its sleeps are not the backoff

    async def closing(_host, _port):
        reader = asyncio.StreamReader()
        reader.feed_eof()                                 # accepts and closes (session limit)
        return reader, _Writer(reader, False)

    async def sleep(delay, *_a):
        if delay >= telnet_module.RECONNECT_BACKOFF_MIN:
            delays.append(delay)
        await real(0)

    monkeypatch.setattr(telnet_module.asyncio, "open_connection", closing)
    monkeypatch.setattr(telnet_module.asyncio, "sleep", sleep)
    client = TelnetClient("h", on_line=lambda _l: None)
    await client.async_start()
    while len(delays) < 3:
        await real(0)
    await client.async_stop()
    assert delays[1] > delays[0]


@pytest.mark.parametrize("xml", ["not xml", "<html><body>hi</body></html>", "<Device_Info><ModelName></ModelName>"])
async def test_a_foreign_or_broken_deviceinfo_is_not_a_receiver(xml, monkeypatch):
    service = DiscoveryService.__new__(DiscoveryService)
    service._host = "h"

    async def fetch():
        return xml

    service._fetch_device_info = fetch
    with pytest.raises(ConnectionError):
        await service.fetch_identity()
    assert parse_device_info(xml).device.mac_address is None


async def test_established_link_is_given_up_and_reconnected(fast, monkeypatch):
    client, peers, states = _client(monkeypatch, answer=True)
    await client.async_start()
    while not client.connected or not states:
        await client.async_send("MV?")
        await asyncio.sleep(0.01)
    peers[0].answer = False                               # the plug is pulled
    for _ in range(200):
        await asyncio.sleep(0.01)
        if False in states:
            break
    await client.async_stop()
    assert states[:2] == [True, False]


async def test_backoff_grows_after_a_session_that_answered(monkeypatch):
    delays, rounds = [], []
    real = asyncio.sleep
    monkeypatch.setattr(telnet_module, "KEEPALIVE_TIMEOUT", 0.04)

    async def open_connection(_host, _port):
        rounds.append(1)
        if len(rounds) > 1:
            raise OSError("refused")
        reader = asyncio.StreamReader()
        reader.feed_data(b"PWON\r")
        reader.feed_eof()
        return reader, _Writer(reader, False)

    async def sleep(delay, *_a):
        if delay >= telnet_module.RECONNECT_BACKOFF_MIN:
            delays.append(delay)
        await real(0)

    monkeypatch.setattr(telnet_module.asyncio, "open_connection", open_connection)
    monkeypatch.setattr(telnet_module.asyncio, "sleep", sleep)
    client = TelnetClient("h", on_line=lambda _l: None)
    await client.async_start()
    while len(delays) < 5:
        await real(0)
    await client.async_stop()
    assert delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]


async def test_the_resync_question_is_not_a_probe(fast, monkeypatch):
    monkeypatch.setattr(telnet_module, "KEEPALIVE_INTERVAL", 5.0)
    client, _peers, _states = _client(monkeypatch, answer=False)
    await client.async_start()
    await asyncio.sleep(0)
    await client.async_send("PW?")
    await asyncio.sleep(0.2)
    assert client.connected                               # silence of one question is no miss
    await client.async_stop()


def test_mac_is_canonical_or_none():
    assert _format_mac("0005CD000001") == "00:05:cd:00:00:01"
    assert _format_mac(" 00:05:CD:00:00:01 ") == "00:05:cd:00:00:01"
    for bad in (None, "", " ", "0", "000000000000", "no-mac"):
        assert _format_mac(bad) is None
