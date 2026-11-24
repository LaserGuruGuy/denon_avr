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
        ("input_channels", "11111111111111000000", None, ()),
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
    client = type(dev._telnet).instances[-1] if hasattr(type(dev._telnet), "instances") else dev._telnet
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
    assert client is not None
    await dev.async_stop()
