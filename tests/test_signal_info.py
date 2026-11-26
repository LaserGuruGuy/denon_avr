"""Video, HDR and channel information: both firmware wordings, nothing guessed."""

from __future__ import annotations

import pytest

from avr.protocol.channel_map import decode
from avr.protocol.profile import load_profile

READONLY = load_profile().readonly


@pytest.mark.parametrize(
    ("control", "text", "layout", "active"),
    [   # measured on hardware
        ("input_channels", "22111111111111000000", "2.0", ("FL", "FR")),
        ("input_channels", "22222211111111000000", "5.1", ("FL", "FR", "C", "LFE", "SL", "SR")),
        ("input_channels", "22222222111111000000", "7.1", None),
        ("input_channels", "11111111111111000000", "---", ()),          # answered: none active
        ("active_speakers", "22222200000000000000000000000000", "5.1", None),
        ("active_speakers", "22202200000000000000000000000000", "5.0", None),      # no subwoofer
        ("active_speakers", "22121100000000000000000000000000", "2.1", ("FL", "FR", "SW")),
        ("active_speakers", "22222211022000022000000000000000", "5.1.4", None),    # four height speakers
        ("active_speakers", "22000000000020000000000000000000", "2.0+1", ("FL", "FR", "ch12")),
        ("input_channels", None, None, ()),
    ],
)
def test_channel_layouts(control, text, layout, active):
    result = decode(text, READONLY[control])
    assert result.layout == layout
    if active is not None:
        assert result.active == active


@pytest.mark.parametrize("fixture", ["avc_x3900h", "avr_x3600h"])
async def test_video_and_hdr_wording(make_device, fixture):
    dev, _session, telnet, _fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    for line in (
        "SYSDVIN 1080p:60Hz", "SYSDVOUT 1080p60", "SYSDV END",
        "SSINFSIGHDR I --- ", "SSINFSIGHDR O---",
        "OPINFINS 22222211111111000000", "OPINFASP 22222200000000000000000000000000",
    ):
        dev._sync.handle_line(line)
    r = dev.state.readonly
    assert (r["video_input"], r["video_output"]) == ("1080p:60Hz", "1080p60")
    assert (r["hdr_input"], r["hdr_output"]) == ("---", "---")
    assert r["input_channels"].startswith("222222")
    dev._sync.handle_line("OPINFINS ?")                       # not a grid: ignored
    assert r["input_channels"].startswith("222222")
    dev._sync.handle_line("SSINFSIGHDR IHDR10")
    assert dev.state.readonly["hdr_input"] == "HDR10"
    await dev.async_stop()


ANSWERS = {                                                # measured
    "SYSDV ?": ["SYSDVIN --- ", "SYSDVOUT 1080p60", "SYSDV END"],
    "SSINFSIGHDR ?": ["SSINFSIGHDR I---", "SSINFSIGHDR O---"],
    "SSINFAISSIG ?": ["SSINFAISSIG 02"],
    "SSINFAISFSV ?": ["SSINFAISFSV 48K"],
    "SSINFAISFOR ?": ["SSINFAISFOR 2/0/.0"],
    "SYSDA ?": ["SYSDA PCM                  "],
    "SYSMI ?": ["SYSMI Stereo"],
    "OPINFINS ?": ["OPINFINS 22111111111111000000"],
    "OPINFASP ?": ["OPINFASP 22121100000000000000000000000000"],
    "PSFRONT?": ["PSFRONT SPB"],
    "SPPR ?": ["SPPR 1"],
}


def _recorded(fx):
    """What the receiver answered to each query in the recording, plus the measured ones."""

    answers = {}
    for rec in fx.telnet_corpus:
        answers.setdefault(rec["after"], []).append(rec["line"])
    for query, lines in ANSWERS.items():
        answers.setdefault(query, list(lines))
    return answers


async def _settle(telnet, rounds=12, answers=ANSWERS):
    """Let a receiver that answers everything settle; return what was still sent at the end."""

    import asyncio

    tail: list[str] = []
    done = 0
    for _ in range(rounds):
        await asyncio.sleep(0.12)                              # > the scaled debounces
        tail, done = telnet.sent[done:], len(telnet.sent)
        for query in tail:
            for line in answers.get(query, ()):
                telnet.feed(line)
    return tail


@pytest.mark.parametrize("fixture", ["avc_x3900h", "avr_x3600h"])
async def test_an_answering_receiver_comes_to_rest(make_device, fixture):
    """No refresh group may be triggered by the answers to its own queries."""

    dev, _session, telnet, fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    answers = _recorded(fx)
    for lines in answers.values():
        for line in lines:
            telnet.feed(line)
    await _settle(telnet, answers=answers)
    telnet.sent.clear()
    for line in ("SSFRSDST SPB", "PSFRONT SPB", "SSPAAMOD FRB"):          # pushed by the receiver itself
        telnet.feed(line)
    await _settle(telnet, answers=answers)
    telnet.sent.clear()
    cluster = ["SSINFAISSIG 02", "OPINFINS 22111111111111000000", "SYSDA PCM", "OPINFASP 22121100000000000000000000000000"]
    answers["SSINFAISSIG ?"], answers["SYSDA ?"] = ["SSINFAISSIG 02"], ["SYSDA PCM"]
    answers["SYSMI ?"] = ["SYSMI Stereo"]
    for line in cluster:
        telnet.feed(line)                 # a real change may cost one round of questions
    await _settle(telnet, answers=answers)
    telnet.sent.clear()
    for _ in range(3):
        for line in cluster:
            telnet.feed(line)
    assert await _settle(telnet, answers=answers) == [] and telnet.sent == []
    await dev.async_stop()


async def test_a_signal_change_reads_video_twice_and_stops(make_device):
    dev, _session, telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    for lines in ANSWERS.values():
        for line in lines:
            telnet.feed(line)
    await _settle(telnet)
    telnet.sent.clear()
    telnet.feed("SSINFAISSIG 25")
    await _settle(telnet)
    assert telnet.sent.count("SYSDV ?") == 2                    # after the change, and after the handshake
    await dev.async_stop()


async def test_poll_reads_video_only_while_on(make_device):
    dev, _session, telnet, _fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    for line, expected in (("ZMOFF", 0), ("ZMON", 1)):
        telnet.feed(line)
        await _settle(telnet)
        telnet.sent.clear()
        await dev._sync.async_reconcile(dev._goform.async_get_status, lambda: True, ())
        assert telnet.sent.count("SYSDV ?") == expected, line
    await dev.async_stop()


async def test_an_unmodelled_answer_cannot_loop(make_device):
    """A firmware that answers PSFRONT? with the trigger line itself still comes to rest."""

    dev, _session, telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    answers = _recorded(fx)
    answers["PSFRONT?"] = ["SSPAAMOD FRB", "PSFRONT SPB"]
    for lines in answers.values():
        for line in lines:
            telnet.feed(line)
    await _settle(telnet, answers=answers)
    telnet.sent.clear()
    telnet.feed("SSPAAMOD PRE")
    assert await _settle(telnet, answers=answers) == []
    assert 0 < telnet.sent.count("PSFRONT?") <= 2
    await dev.async_stop()


async def test_a_trigger_during_the_second_read_is_not_lost(make_device):
    import asyncio

    dev, _session, telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    for lines in _recorded(fx).values():
        for line in lines:
            telnet.feed(line)
    await _settle(telnet)
    telnet.sent.clear()
    telnet.feed("SSINFAISSIG 25")
    await asyncio.sleep(0.2)                         # first read done, waiting for the second
    telnet.feed("SSINFAISSIG 02")
    await _settle(telnet)
    assert telnet.sent.count("SYSDV ?") == 3         # two triggers and one read after the last one
    await dev.async_stop()


async def test_hardware_type_does_not_depend_on_the_model_name():
    from avr.protocol.deviceinfo import parse_device_info

    for model in ("AVC-X3900H", "SR5015", "Something new"):
        xml = f"<Device_Info><CommApiVers>0400</CommApiVers><ModelName>{model}</ModelName></Device_Info>"
        assert parse_device_info(xml, interface_type="avr-x-2016").device.hardware_type == "avr-x-2016"


@pytest.mark.parametrize("fixture", ["avc_x3900h", "avr_x3600h"])
async def test_lines_pushed_by_a_web_read_do_not_loop(make_device, fixture):
    """Reading the speakers web document makes the receiver push its SSSPC lines again."""

    import asyncio

    dev, _session, telnet, fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    lines = [r["line"] for r in fx.telnet_corpus if r["line"].startswith("SSSPC")]
    assert any(x.endswith("2SP") for x in lines)
    real = dev._webconfig.async_get

    async def get(section, type_id, extra=None):
        if section == "speakers":
            asyncio.get_running_loop().call_later(0.01, lambda: [telnet.feed(x) for x in lines])
        return await real(section, type_id, extra)

    dev._webconfig.async_get = get
    await dev._sync.refresh_web("speakers")
    await _settle(telnet)
    before = dict(dev._sync.stats["refresh_groups"])
    fired = []
    dev.register_update_callback(lambda: fired.append(1))
    await dev._sync.refresh_web("speakers")
    await _settle(telnet)
    assert dev._sync.stats["refresh_groups"] == before and fired == []
    await dev.async_stop()


async def test_a_group_that_keeps_triggering_itself_is_braked(make_device, monkeypatch, caplog):
    import avr.services.sync as sync_module

    dev, _session, telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    monkeypatch.setattr(sync_module, "MAX_RUNS", 3)
    dev._sync._runs.clear()
    counter = iter(range(1000))
    for _ in range(12):
        telnet.feed(f"SSPAAMOD X{next(counter)}")         # always new text, never modelled
        for group in list(dev._sync._refresh_scheduled):
            dev._sync._refresh_scheduled.discard(group)
    assert dev._sync.stats["refresh_groups"].get("front_speaker", 0) == 3
    assert sum("runs too often" in r.message for r in caplog.records) == 1
    await dev.async_stop()



HDMI_BLOCK = ["SSHOSCON ON", "SSHOSRSS POS", "SSHOSCONPOF ALL", "SSHOSCONSTS LAS", "SSHOSPAS OFF",
              "SSHOSTAS ON", "SSHOSCONARC ON", "SSHOSCONPSV OFF", "SSHOS END"]


@pytest.mark.parametrize("fixture", ["avc_x3900h", "avr_x3600h"])
async def test_hdmi_setup_and_audio_menu_lines_are_modelled_without_loop(make_device, fixture):
    """A video web read makes the receiver push SSHOS and SSAUDSTS; only a real change re-reads it."""

    import asyncio

    dev, _session, telnet, _fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    block = list(HDMI_BLOCK)
    real = dev._webconfig.async_get

    async def get(section, type_id, extra=None):
        if section == "video" and type_id == 3:
            lines = block + ["SSAUDSTS 0212202012100000"]
            asyncio.get_running_loop().call_later(0.01, lambda: [telnet.feed(x) for x in lines])
        return await real(section, type_id, extra)

    dev._webconfig.async_get = get
    unknown = dev._sync.stats["lines_unknown"]
    await dev._sync.refresh_web("video")
    await _settle(telnet)
    assert dev.state.values["hdmi_setup_PAS"] == "OFF"
    assert dev.state.values["audio_menu_status"] == "0212202012100000"
    groups = dict(dev._sync.stats["refresh_groups"])
    for _ in range(3):
        await dev._sync.refresh_web("video")
        await _settle(telnet)
    assert dev._sync.stats["refresh_groups"] == groups
    block[0] = "SSHOSCON OFF"
    telnet.feed("SSHOSCON OFF")
    await _settle(telnet)
    await asyncio.sleep(1.2)
    await _settle(telnet)
    assert dev._sync.stats["refresh_groups"].get("hdmi_setup_web", 0) == groups.get("hdmi_setup_web", 0) + 1
    telnet.feed("SSAUDSTS 0222102012100000")
    await _settle(telnet)
    await asyncio.sleep(1.2)
    await _settle(telnet)
    after = dict(dev._sync.stats["refresh_groups"])
    assert after.get("audio_menu_web", 0) == groups.get("audio_menu_web", 0) + 1
    await asyncio.sleep(1.5)
    await _settle(telnet)
    assert dev._sync.stats["refresh_groups"] == after
    assert dev._sync.stats["lines_unknown"] == unknown
    await dev.async_stop()


async def test_auto_lip_sync_keeps_its_own_control(make_device):
    dev, _session, telnet, _fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    telnet.feed("SSHOSALS ON")
    await _settle(telnet)
    assert "hdmi_setup_ALS" not in dev.state.values
    await dev.async_stop()


async def test_grayed_hdmi_item_shows_its_own_setting(make_device):
    import conftest

    web_setting_attributes = conftest.load_ha_module("helpers").web_setting_attributes

    dev, _session, telnet, _fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    for line in HDMI_BLOCK:
        telnet.feed(line)
    await _settle(telnet)
    video = dev.video_config
    await video.refresh()
    assert video.present("hdmi_pass_through") and not video.available("hdmi_pass_through")
    assert video.value("hdmi_pass_through") == "1"
    assert web_setting_attributes(dev, video, "hdmi_pass_through") == {"settable": False, "own_setting": "off"}
    assert web_setting_attributes(dev, video, "hdmi_control") == {"settable": True, "own_setting": "on"}
    await dev.async_stop()


@pytest.mark.parametrize("fixture", ["avc_x3900h", "avr_x3600h"])
async def test_web_echo_lines_are_known_and_quiet(make_device, fixture):
    dev, _session, telnet, _fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    lines = ["SSAUDSTS 0212102012100000", "SSHOSCON ON", "SSHOSRSS POS", "SSHOSCONPOF ALL", "SSHOSCONSTS LAS",
             "SSHOSPAS OFF", "SSHOSTAS ON", "SSHOSCONARC ON", "SSHOSCONPSV OFF", "SSHOS END"]
    before = dict(dev._sync.stats["refresh_groups"])
    unknown = dev._sync.stats["lines_unknown"]
    for _ in range(3):
        for line in lines:
            telnet.feed(line)
        await _settle(telnet)
    assert dev._sync.stats["lines_unknown"] == unknown
    assert dev._sync.stats["refresh_groups"] == before
    await dev.async_stop()


@pytest.mark.parametrize("fixture", ["avc_x3900h", "avr_x3600h"])
async def test_hdmi_setup_and_audio_menu_are_asked_on_connect(make_device, fixture):
    """The AVR-X3600H only pushes SSHOS on a change, so ask for it on connect."""

    dev, _session, telnet, _fx = make_device(fixture)
    await dev.async_discover()
    queries = dev._sync.resync_queries()
    assert "SSHOS ?" in queries and "SSAUDSTS ?" in queries


@pytest.mark.parametrize(("token", "write"), [("70", "SSVCTZMALIM 60"), ("070", "SSVCTZMALIM 060")])
async def test_numeric_enum_follows_the_receivers_spelling(make_device, token, write):
    dev, _session, telnet, _fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    telnet.feed(f"SSVCTZMALIM {token}")
    await _settle(telnet)
    assert dev.state.values["volume_limit"] == "070"
    telnet.sent.clear()
    await dev.async_set_control("volume_limit", "060")
    await _settle(telnet)
    assert write in telnet.sent
    await dev.async_stop()
