"""Per-input collections (setup web interface): parsing, allowed options, safe writes."""

from __future__ import annotations

import pytest

import conftest  # noqa: F401  # pyflakes: ignore - loads the avr package
from avr.protocol.profile import load_profile
from avr.protocol.webdoc import build_collection_payload, parse_collection, parse_xml

SPECS = {c["id"]: c for c in load_profile().grammar["video_web"]["collections"]}


def _doc(fixture: str, type_id: int):
    return parse_xml(conftest.FixtureSet(fixture).file(f"ajax_video_{type_id}.xml"))


def test_signal_format_per_input_from_real_capture():
    items = parse_collection(SPECS["input_signal_format"], _doc("avc_x3900h", 14))
    assert list(items) == ["1", "5", "3", "4", "7", "8", "6", "9"]      # the receiver's order
    bd = items["1"]
    assert (bd.name, bd.value, bd.options) == ("AAP", "2", ("1", "2", "4"))   # no Custom: MaxResolution=0
    assert bd.availability.settable
    tv = items["6"]
    assert tv.name == "TV Audio" and not tv.availability.settable and tv.availability.gray_reason == "1"


def test_custom_only_where_the_input_allows_it():
    root = parse_xml('<FourKEightKSignalFormatPerInput><Source display="3" index="4" gray="0">'
                     '<Name>Game</Name><Format>2</Format><MaxResolution>1</MaxResolution></Source>'
                     '</FourKEightKSignalFormatPerInput>')
    assert parse_collection(SPECS["input_signal_format"], root)["4"].options == ("1", "2", "3", "4")


def test_hdcp_per_input_from_real_capture():
    items = parse_collection(SPECS["input_hdcp"], _doc("avc_x3900h", 11))
    assert items["1"].options == ("1", "2", "3")        # Limit=0: no Custom
    assert items["6"].availability.display == 2 and not items["6"].availability.settable


def test_wrong_root_is_ignored():
    assert parse_collection(SPECS["input_hdcp"], _doc("avc_x3900h", 14)) == {}


def test_payload_is_the_receivers_own_shape():
    assert build_collection_payload(SPECS["input_signal_format"], "4", "1") == '<Format index="4">1</Format>'
    assert build_collection_payload(SPECS["input_hdcp"], '1"><x', "<b>") == '<Value index="1&quot;&gt;&lt;x">&lt;b&gt;</Value>'


@pytest.mark.parametrize(("slot", "option", "sent"), [
    ("1", "4", True),        # settable input, allowed option
    ("1", "3", False),       # Custom not allowed for this input
    ("6", "1", False),       # grayed input
    ("99", "1", False),      # unknown slot
])
async def test_set_item_is_refused_unless_allowed(make_device, slot, option, sent):
    dev, session, _telnet, _fx = make_device("avc_x3900h")
    await dev.async_discover()
    before = len(session.requests)
    ok = await dev.video_config.async_set_item("input_signal_format", slot, option)
    writes = [r["url"] for r in session.requests[before:] if "set_config" in r["url"]]
    assert ok is sent and bool(writes) is sent
    if sent:
        assert writes == ["http://192.0.2.10:11080/ajax/video/set_config?type=14&data=%3CFormat%20index%3D%221%22%3E4%3C%2FFormat%3E"]


async def test_legacy_receiver_has_no_per_input_collections(make_device):
    dev, _session, _telnet, _fx = make_device("avr_x3600h")
    await dev.async_discover()
    assert dev.video_config.collection("input_signal_format") == {}
    assert dev.video_config.collection("input_hdcp") == {}


async def test_speaker_layout_is_not_written_directly(make_device):
    dev, session, _telnet, _fx = make_device("avc_x3900h")
    await dev.async_discover()
    assign = dev.speaker_config.collection("speaker_layout_assign")
    assert assign["1"].value == "6" and assign["1"].options == ("18", "1", "2", "7", "14", "6", "17", "19")
    before = len(session.requests)
    assert await dev.speaker_config.async_set_item("speaker_layout_assign", "1", "1") is False
    assert not [r for r in session.requests[before:] if "set_config" in r["url"]]


def test_value_lists_come_from_the_receiver():
    from avr.protocol.webdoc import parse_value_list

    spec = next(v for v in load_profile().grammar["speakers_web"]["value_lists"] if v["id"] == "crossover")
    new = parse_value_list(spec, parse_xml(conftest.FixtureSet("avc_x3900h").file("ajax_speakers_6.xml")))
    old = parse_value_list(spec, parse_xml(conftest.FixtureSet("avr_x3600h").file("ajax_speakers_6.xml")))
    assert new[:4] == ("0", "40", "60", "70") and "70" not in old and "0" not in old


async def test_speaker_layout_is_staged_and_applied_only_explicitly(make_device, monkeypatch):
    import avr.services.webconfig_doc as wd

    async def no_wait(_s):
        return None

    monkeypatch.setattr(wd.asyncio, "sleep", no_wait)
    dev, session, _telnet, _fx = make_device("avc_x3900h")
    await dev.async_discover()
    layout = dev.speaker_layout
    before = len(session.requests)
    assert layout.stage("1", "17") is True                     # Preamplifier, allowed by the receiver
    assert layout.stage("1", "99") is False                    # not in the receiver's list
    assert layout.pending == {"1": "17"}
    assert not [r for r in session.requests[before:] if "set_config" in r["url"]]   # nothing sent yet
    assert await dev.speaker_config.async_set_item("speaker_layout_assign", "1", "17") is False  # no direct write
    layout.discard()
    assert layout.pending == {}
    layout.stage("1", "17")
    results = await layout.async_apply()
    writes = [r["url"] for r in session.requests[before:] if "set_config" in r["url"]]
    # The recorded receiver document does not change, so the apply is NOT confirmed:
    # success is only reported when the receiver shows the new mode after the write.
    assert results == {"1": False}
    assert writes == ["http://192.0.2.10:11080/ajax/speakers/set_config?type=15&data=%3CConfig%20index%3D%221%22%3E17%3C%2FConfig%3E"]
    assert layout.pending == {}


async def test_speaker_layout_apply_confirmed_when_the_receiver_shows_it(make_device, monkeypatch):
    import avr.services.webconfig_doc as wd

    async def no_wait(_s):
        return None

    monkeypatch.setattr(wd.asyncio, "sleep", no_wait)
    dev, session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    dev.speaker_layout.stage("1", "17")
    original = type(fx).file
    changed = original(fx, "ajax_speakers_15.xml").replace(
        '<Item index="1" display="3"><Value>6</Value>', '<Item index="1" display="3"><Value>17</Value>', 1
    )
    monkeypatch.setattr(type(fx), "file", lambda self, n: changed if n == "ajax_speakers_15.xml" else original(self, n))
    assert await dev.speaker_layout.async_apply() == {"1": True}
    assert dev.speaker_config.collection("speaker_layout_assign")["1"].value == "17"
