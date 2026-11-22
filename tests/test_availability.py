"""Availability semantics (as the receiver's own UI reads them) and live following."""

from __future__ import annotations

import collections

import pytest

import conftest  # noqa: F401  # pyflakes: ignore - loads the avr package
from avr import Availability


@pytest.mark.parametrize(("display", "gray", "present", "settable", "reason"), [
    (1, "0", False, False, None),     # not applicable
    (2, "0", True, False, None),      # shown, disabled
    (2, "1", True, False, "1"),       # disabled, with the reason code for the UI text
    (3, "0", True, True, None),
    (3, "1", True, True, None),       # gray on an active item is not a lock (GraphicEQ on the X3900H)
    (4, "0", True, True, None),       # rendered like 3 by the receiver's UI
    (0, "0", False, False, None),
    (5, "0", False, False, None),
])
def test_semantics(display, gray, present, settable, reason):
    a = Availability(display, gray)
    assert (a.present, a.settable, a.gray_reason) == (present, settable, reason)


def _override(monkeypatch, fixtures_cls, overrides: dict):
    original = fixtures_cls.file

    def file(self, name):
        if name in overrides:
            return overrides[name]
        return original(self, name)

    monkeypatch.setattr(fixtures_cls, "file", file)


def _menu(fx, tag: str, display: int) -> str:
    """The real video listSetupMenu with one entry's display changed."""
    import re

    text = fx.file("ajax_video_1.xml")
    return re.sub(rf"<{tag}>\d+</{tag}>", f"<{tag}>{display}</{tag}>", text)


async def test_collection_follows_the_receiver_live(make_device, monkeypatch):
    dev, session, _telnet, fx = make_device("avc_x3900h")
    original = {"ajax_video_1.xml": fx.file("ajax_video_1.xml")}
    await dev.async_discover()
    assert dev.video_config.collection("input_signal_format")                 # 3: present
    # 3 -> 1: not applicable any more -> items go (the HA entity turns unavailable)
    _override(monkeypatch, type(fx), {"ajax_video_1.xml": _menu(fx, "FourKEightKSignalFormatPerInput", 1)})
    await dev.video_config.refresh()
    assert dev.video_config.collection("input_signal_format") == {}
    # 1 -> 3: applicable again -> read again
    _override(monkeypatch, type(fx), original)
    await dev.video_config.refresh()
    assert len(dev.video_config.collection("input_signal_format")) == 8


async def test_failed_read_keeps_the_last_values(make_device, monkeypatch):
    dev, _session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    before = dev.video_config.collection("input_hdcp")
    _override(monkeypatch, type(fx), {"ajax_video_11.xml": None})            # -> HTTP 500
    await dev.video_config.refresh()
    assert dev.video_config.collection("input_hdcp") == before


async def test_unknown_availability_reads_no_collections(make_device, monkeypatch):
    dev, session, _telnet, fx = make_device("avr_x3600h")
    _override(monkeypatch, type(fx), {"ajax_speakers_1.xml": None})          # availability read fails
    await dev.async_discover()
    assert not [r for r in session.requests if "speakers/get_config?type=15" in r["url"]]


@pytest.mark.parametrize("fixture", conftest.FIXTURE_SETS)
async def test_each_document_at_most_once_per_pass(make_device, fixture):
    dev, session, _telnet, _fx = make_device(fixture)
    await dev.async_discover()
    before = len(session.requests)
    await dev.async_poll()
    counts = collections.Counter(r["url"] for r in session.requests[before:])
    assert max(counts.values()) == 1, [u for u, n in counts.items() if n > 1]


@pytest.mark.parametrize(("fixture", "settable"), [("avc_x3900h", True), ("avr_x3600h", False)])
async def test_graphic_eq_follows_the_receivers_menu(make_device, fixture, settable):
    """X3900H reports GraphicEQ display 3 (active), the X3600H display 2 (grayed)."""
    dev, _session, _telnet, _fx = make_device(fixture)
    await dev.async_discover()
    await dev.graphic_eq.refresh()
    assert dev.graphic_eq.settable is settable
