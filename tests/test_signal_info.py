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
