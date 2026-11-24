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


async def test_osd_write_is_wrapped_and_reports_refusal(make_device, monkeypatch):
    """On Screen Display (menu 7) answers 501 unless the field is wrapped in its root
    (verified live on both receivers, 2026-11-24); a refused write is reported."""

    for fixture in ("avc_x3900h", "avr_x3600h"):
        dev, session, _telnet, _fx = make_device(fixture)
        await dev.async_discover()
        before = len(session.requests)
        assert await dev.video_config.async_set("osd_volume", "2") is True
        writes = [r["url"] for r in session.requests[before:] if "set_config" in r["url"]]
        assert len(writes) == 1
        assert writes[0].endswith("type=7&data=%3COnScreenDisplay%3E%3CVolume%3E2%3C%2FVolume%3E%3C%2FOnScreenDisplay%3E")

        async def refuse(*_a):
            return False

        monkeypatch.setattr(dev.video_config._webconfig, "async_set", refuse)
        assert await dev.video_config.async_set("osd_volume", "2") is False
        # An answer that got lost while the value was applied counts as applied.
        assert await dev.video_config.async_set("osd_volume", dev.video_config.value("osd_volume")) is True


async def test_write_settles_before_the_read_back(make_device, monkeypatch):
    """Write, wait (the receiver shows a value ~0.3 s later), then read back."""

    import avr.services.webconfig_doc as wd

    waits = []
    real = wd.asyncio.sleep

    class _Asyncio:
        def __getattr__(self, name):
            return getattr(asyncio_module, name)

        @staticmethod
        async def sleep(delay, *args):
            waits.append((delay, len(session.requests)))
            await real(0)

    import asyncio as asyncio_module

    monkeypatch.setattr(wd, "asyncio", _Asyncio())
    dev, session, _telnet, _fx = make_device("avr_x3600h")
    await dev.async_discover()
    before = len(session.requests)
    await dev.video_config.async_set("osd_volume", "2")
    assert waits == [(0.8, before + 1)] and len(session.requests) > before + 1
    waits.clear()
    await dev.speaker_config.async_set("amp_assign", dev.speaker_config.value("amp_assign"))
    assert [w[0] for w in waits] == [2.0]                      # amplifier changes take longer


async def test_features_follow_a_late_web_interface(make_device, monkeypatch):
    """The web interface missed at start: its menu features (and the telnet controls
    gated on them) appear once it is found; features are never removed."""

    dev, session, telnet, _fx = make_device("avr_x3600h")
    real = dev._webconfig.async_get

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(dev._webconfig, "async_get", nothing)
    await dev.async_discover()
    await dev.async_start()
    before = set(dev.discovery.features)
    monkeypatch.setattr(dev._webconfig, "async_get", real)
    await dev._sync.refresh_web("video")
    await dev._sync.refresh_web("speakers")
    assert before <= dev.discovery.features
    expected = dev.video_config.feature_flags() | dev.speaker_config.feature_flags()
    assert expected and expected <= dev.discovery.features
    sent = getattr(telnet(), "sent", None) if callable(telnet) else getattr(telnet, "sent", None)
    if sent is not None and "PictureAdjust" in expected - before:
        assert any(q.startswith("PV") for q in sent)
    features = set(dev.discovery.features)
    monkeypatch.setattr(dev._webconfig, "async_get", nothing)
    await dev._sync.refresh_web("video")
    assert features <= dev.discovery.features
    await dev.async_stop()


async def test_layout_baseline_survives_consecutive_applies(make_device, monkeypatch):
    """6 -> 17 -> 6: the first 'before' values stay until they are back or dismissed."""

    import re

    dev, _session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    original = type(fx).file
    doc = original(fx, "ajax_speakers_15.xml")
    now = {"doc": doc}
    monkeypatch.setattr(type(fx), "file", lambda self, n: now["doc"] if n == "ajax_speakers_15.xml" else original(self, n))
    pos5 = '<Item index="5" display="3"><Config display="3"><Value>'
    dev.speaker_layout.stage("1", "17")
    now_17 = re.sub(r'(<Item index="1" display="3"><Value>)6', r"\g<1>17", doc, count=1).replace(pos5 + "5", pos5 + "7", 1)

    async def applied(*_a):
        now["doc"] = now_17
        return True

    monkeypatch.setattr(dev._webconfig, "async_set", applied)
    assert await dev.speaker_layout.async_apply() == {"1": True}
    assert dev.speaker_layout.side_effects == {"5": ("5", "7")}

    async def back(*_a):
        now["doc"] = doc.replace(pos5 + "5", pos5 + "7", 1)
        return True

    monkeypatch.setattr(dev._webconfig, "async_set", back)
    dev.speaker_layout.stage("1", "6")
    assert await dev.speaker_layout.async_apply() == {"1": True}
    assert dev.speaker_layout.side_effects == {"5": ("5", "7")}   # still the original 'before'
    dev.speaker_layout.discard()
    assert dev.speaker_layout.side_effects == {}


async def test_layout_apply_reports_other_positions(make_device, monkeypatch):
    """Applying a layout mode resets other positions (verified live): reported."""

    dev, _session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    positions = dev.speaker_config.collection("speaker_layout_config")
    assert positions["5"].value == "5"
    dev.speaker_layout.stage("1", "17")
    original = type(fx).file
    changed = original(fx, "ajax_speakers_15.xml").replace(
        '<Item index="1" display="3"><Value>6</Value>', '<Item index="1" display="3"><Value>17</Value>', 1
    ).replace('<Item index="5" display="3"><Config display="3"><Value>5</Value>', '<Item index="5" display="3"><Config display="3"><Value>7</Value>', 1)

    async def applied(*_a):
        monkeypatch.setattr(type(fx), "file", lambda self, n: changed if n == "ajax_speakers_15.xml" else original(self, n))
        return True

    monkeypatch.setattr(dev._webconfig, "async_set", applied)
    assert await dev.speaker_layout.async_apply() == {"1": True}
    assert dev.speaker_layout.side_effects == {"5": ("5", "7")}


class _LayoutReceiver:
    """Behaves as measured on the AVC-X3900H: a mode change resets positions 5 and 8;
    writing 5 and 8 puts them back; optional faults for the failure tests."""

    P5 = '<Item index="5" display="3"><Config display="3"><Value>'
    P8 = '<Item index="8" display="3"><Config display="3"><Value>'

    def __init__(self, fx, monkeypatch, dev):
        original = type(fx).file
        self.doc = original(fx, "ajax_speakers_15.xml")
        self.writes: list[tuple[str, str]] = []
        self.ignore: set[str] = set()
        self.late = 0               # reads after a write before it shows
        self._shown = self.doc
        self._lag = 0
        self.down = False
        self.front = "SPB"
        self.preset = "1"
        self.sent: list[tuple[str, object]] = []

        def file(fx_self, name):
            if name != "ajax_speakers_15.xml":
                return original(fx_self, name)
            if self._lag > 0:
                self._lag -= 1
            else:
                self._shown = self.doc
            return self._shown

        monkeypatch.setattr(type(fx), "file", file)
        monkeypatch.setattr(dev._webconfig, "async_set", self._set)
        real_get = dev._webconfig.async_get

        async def get(*args, **kwargs):
            return None if self.down else await real_get(*args, **kwargs)

        monkeypatch.setattr(dev._webconfig, "async_get", get)

        async def read_control(control_id):
            return {"front_speaker": self.front, "speaker_preset": self.preset}[control_id]

        async def set_control(control_id, value):
            self.sent.append((control_id, value))
            self.front = value

        dev.speaker_layout._read_control = read_control
        dev.speaker_layout._set_control = set_control

    def set_position(self, index, value):
        import re

        self.doc = re.sub(
            rf'(<Item index="{index}" display="3"><Config display="3"><Value>)\d+', rf"\g<1>{value}", self.doc, count=1
        )

    async def _set(self, _section, _type, xml):
        import re

        index, value = re.match(r'<Config index="(\d+)">(\d+)</Config>', xml).groups()
        self.writes.append((index, value))
        if index in self.ignore or self.down:
            return False
        if index == "1":
            self.doc = re.sub(r'(<Item index="1" display="3"><Value>)\d+', rf"\g<1>{value}", self.doc, count=1)
            self.set_position("5", "7")
            self.set_position("8", "1")
            self.front = "SPA"
        else:
            self.set_position(index, value)
        self._lag = self.late
        return True


async def _applied(make_device, monkeypatch, **setup):
    dev, _session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    rx = _LayoutReceiver(fx, monkeypatch, dev)
    for key, value in setup.items():
        setattr(rx, key, value)
    layout = dev.speaker_layout
    assert layout.restorable is False
    assert (await layout.async_restore()).refused == "unavailable"
    layout.stage("1", "17")
    assert await layout.async_apply() == {"1": True}
    assert layout.restorable is True and rx.front == "SPA"
    return dev, layout, rx


async def test_layout_restore_puts_everything_back(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    result = await layout.async_restore()
    assert (result.left, result.newer, result.refused) == ((), (), None)
    assert rx.writes == [("1", "17"), ("1", "6"), ("5", "5"), ("8", "0")]
    assert rx.sent == [("front_speaker", "SPB")]
    assert layout.restorable is False and layout.side_effects == {}


async def test_layout_restore_with_a_slow_receiver(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch, late=1)
    assert (await layout.async_restore()).left == ()
    assert rx.writes == [("1", "17"), ("1", "6"), ("5", "5"), ("8", "0")]


async def test_layout_restore_writes_no_position_before_the_mode_is_back(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    rx.ignore = {"1"}
    assert (await layout.async_restore()).left == ("mode",)
    assert rx.writes == [("1", "17"), ("1", "6")] and rx.sent == []
    assert layout.restorable is True


async def test_layout_restore_reports_a_position_that_refuses(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    rx.ignore = {"5"}
    assert (await layout.async_restore()).left == ("5",)
    assert ("8", "0") in rx.writes and rx.sent == [("front_speaker", "SPB")]   # the rest is done
    assert layout.restorable is True


async def test_layout_restore_keeps_newer_choices(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    rx.set_position("5", "6")           # chosen on the receiver after the apply
    rx.front = "A+B"
    result = await layout.async_restore()
    assert result.left == () and set(result.newer) == {"5", "front_speaker"}
    assert ("5", "5") not in rx.writes and rx.sent == []


async def test_layout_restore_refuses_another_preset_or_mode(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    rx.preset = "2"
    assert (await layout.async_restore()).refused == "stale"
    assert rx.writes == [("1", "17")] and layout.restorable is True    # the preset may come back
    rx.preset = "1"
    assert (await layout.async_restore()).left == ()


async def test_layout_restore_without_web_or_telnet(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    rx.down = True
    assert (await layout.async_restore()).refused == "unavailable"
    assert layout.restorable is True    # can be tried again
    rx.down = False

    real = layout._read_control

    async def silent(control_id):
        return None if control_id == "front_speaker" else await real(control_id)

    layout._read_control = silent
    assert (await layout.async_restore()).left == ("front_speaker",)
    assert rx.sent == []                # nothing queued for later


async def test_layout_one_operation_at_a_time_and_discard(make_device, monkeypatch):
    import asyncio

    _dev, layout, rx = await _applied(make_device, monkeypatch)
    first = asyncio.ensure_future(layout.async_restore())
    await asyncio.sleep(0)
    assert layout.busy and layout.discard() is False and layout.stage("1", "7") is False
    assert (await layout.async_restore()).refused == "unavailable"
    assert (await first).left == ()
    assert rx.writes.count(("1", "6")) == 1


async def test_layout_discard_keeps_the_restore_point_first(make_device, monkeypatch):
    _dev, layout, _rx = await _applied(make_device, monkeypatch)
    layout.stage("1", "7")
    layout.discard()
    assert layout.pending == {} and layout.restorable is True
    layout.discard()
    assert layout.restorable is False


async def test_layout_failed_apply_leaves_no_restore_point(make_device, monkeypatch):
    dev, _session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    rx = _LayoutReceiver(fx, monkeypatch, dev)
    rx.ignore = {"1"}
    dev.speaker_layout.stage("1", "17")
    assert await dev.speaker_layout.async_apply() == {"1": False}
    assert dev.speaker_layout.restorable is False
    dev.speaker_layout.stage("1", "17")
    rx.down = True
    writes = len(rx.writes)
    assert await dev.speaker_layout.async_apply() == {"1": False}
    assert len(rx.writes) == writes and dev.speaker_layout.pending == {"1": "17"}   # nothing sent


async def test_layout_unload_stops_writing(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    layout.stop()
    await layout.async_restore()
    assert rx.writes == [("1", "17")] and rx.sent == []


async def test_layout_second_apply_in_another_preset_gets_its_own_restore_point(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    rx.preset = "2"
    layout.stage("1", "7")
    assert await layout.async_apply() == {"1": True}
    writes = len(rx.writes)
    assert (await layout.async_restore()).left == ()
    assert rx.writes[writes:][0] == ("1", "17")        # preset 2 had 17, not preset 1's 6
    assert ("1", "6") not in rx.writes


async def test_layout_unknown_preset_refuses_and_keeps_the_restore_point(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    rx.preset = None
    assert (await layout.async_restore()).refused == "unavailable"
    layout.stage("1", "7")
    assert await layout.async_apply() == {"1": False}
    assert rx.writes == [("1", "17")] and layout.restorable is True and layout.pending == {"1": "7"}


async def test_layout_lost_read_back_keeps_the_restore_point(make_device, monkeypatch):
    dev, _session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    rx = _LayoutReceiver(fx, monkeypatch, dev)
    real = rx._set

    async def set_then_down(*args):
        await real(*args)
        rx.down = True
        return False

    monkeypatch.setattr(dev._webconfig, "async_set", set_then_down)
    layout = dev.speaker_layout
    layout.stage("1", "17")
    assert await layout.async_apply() == {"1": False}
    assert layout.restorable is True
    rx.down = False
    monkeypatch.setattr(dev._webconfig, "async_set", real)
    result = await layout.async_restore()
    assert result.refused is None and result.left == ()
    assert ("1", "6") in rx.writes and rx.sent == [("front_speaker", "SPB")]


async def test_layout_cancelled_apply_can_be_restored(make_device, monkeypatch):
    import asyncio

    dev, _session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    rx = _LayoutReceiver(fx, monkeypatch, dev)
    layout = dev.speaker_layout
    layout.stage("1", "17")
    task = asyncio.ensure_future(layout.async_apply())
    while not rx.writes:
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert layout.busy is False and layout.restorable is True
    assert (await layout.async_restore()).left == ()
    assert rx.writes[-3:] == [("1", "6"), ("5", "5"), ("8", "0")]


async def test_layout_choice_between_two_applies_is_kept(make_device, monkeypatch):
    _dev, layout, rx = await _applied(make_device, monkeypatch)
    rx.set_position("5", "6")
    layout.stage("1", "7")
    assert await layout.async_apply() == {"1": True}
    result = await layout.async_restore()
    assert "5" in result.newer and ("5", "5") not in rx.writes


async def test_layout_front_speaker_silent_at_apply_uses_the_known_value(make_device, monkeypatch):
    dev, _session, _telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    rx = _LayoutReceiver(fx, monkeypatch, dev)
    layout = dev.speaker_layout
    real = layout._read_control

    async def silent(control_id):
        return None if control_id == "front_speaker" else await real(control_id)

    layout._read_control = silent
    layout._cached_control = {"front_speaker": "SPB"}.get
    layout.stage("1", "17")
    await layout.async_apply()
    layout._read_control = real
    assert (await layout.async_restore()).left == ()
    assert rx.sent == [("front_speaker", "SPB")]


EQ_DOC = (
    "<GraphicEQ><Enable>1</Enable><SpeakerSelection>2</SpeakerSelection><AdjustEQ>"
    + "".join(f"<{t}>{v}</{t}>" for t, v in (
        ("Eq63Hz", "10"), ("Eq125Hz", "20"), ("Eq250Hz", "-5"), ("Eq500Hz", "0"), ("Eq1kHz", "5"),
        ("Eq2kHz", "0"), ("Eq4kHz", "15"), ("Eq8kHz", "0"), ("Eq16kHz", "-10")))
    + "</AdjustEQ></GraphicEQ>"
)


async def test_eq_apply_never_writes_unread_bands(make_device, monkeypatch):
    dev, _session, _telnet, _fx = make_device("avr_x3600h")
    await dev.async_discover()
    eq = dev.graphic_eq
    writes, doc, accept = [], {"xml": None}, {"ok": True}

    async def get(section, type_id, extra=None):
        return doc["xml"] if type_id == 10 else None

    async def put(_section, _type, xml):
        writes.append(xml)
        return accept["ok"]

    monkeypatch.setattr(eq._webconfig, "async_get", get)
    monkeypatch.setattr(eq._webconfig, "async_set", put)
    eq.state = type(eq.state)()
    eq._read_channel = None
    await eq.set_band("Eq1kHz", 2.0)
    assert await eq.apply() is False and writes == [] and eq.band_value("Eq1kHz") == 2.0   # nothing read
    doc["xml"] = EQ_DOC
    accept["ok"] = False
    assert await eq.apply() is False and eq.band_value("Eq1kHz") == 2.0                     # refused: kept
    accept["ok"] = True
    assert await eq.apply() is True and not eq.has_pending
    assert "<Eq63Hz>10</Eq63Hz>" in writes[-1].replace(" ", "")                             # other bands kept
    doc["xml"] = None
    assert await eq.set_channel(1) is False
    await eq.set_band("Eq1kHz", 3.0)
    count = len(writes)
    assert await eq.apply() is False and len(writes) == count                               # old channel's curve not copied
