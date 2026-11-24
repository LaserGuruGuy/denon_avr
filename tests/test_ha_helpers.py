"""HA-free helpers that turn receiver data into entity options (no Home Assistant needed)."""

from __future__ import annotations

import conftest
from conftest import drain


async def _device(make_device, fixture):
    dev, _session, telnet, fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    await drain(dev)
    return dev, telnet


async def test_crossover_values_follow_the_receiver(make_device):
    helpers = conftest.load_ha_module("helpers")
    new, _ = await _device(make_device, "avc_x3900h")
    old, _ = await _device(make_device, "avr_x3600h")
    assert helpers.crossover_values(new)[:4] == [0, 40, 60, 70]
    # Full Range: the front group, and others while the front is Full Range (manual)
    assert 0 in helpers.crossover_values(new, "FRO") and 0 in helpers.crossover_values(new, "CEN")
    new.state.crossovers["FRO"] = 80
    assert 0 not in helpers.crossover_values(new, "CEN") and 0 in helpers.crossover_values(new, "FRO")
    assert 0 not in helpers.crossover_values(old) and 70 not in helpers.crossover_values(old)
    assert helpers.crossover_label(0, "Hz") == "Full Range" and helpers.crossover_from_label("Full Range") == 0
    assert helpers.crossover_from_label("80 Hz") == 80
    await new.async_stop()
    await old.async_stop()


async def test_subwoofer_count_options(make_device):
    helpers = conftest.load_ha_module("helpers")
    new, _ = await _device(make_device, "avc_x3900h")          # supports 4, uses NO for absent
    old, _ = await _device(make_device, "avr_x3600h")          # supports 2, uses NON
    assert helpers.subwoofer_count_options(new) == {"NO": "None", "1SP": "1", "2SP": "2", "3SP": "3", "4SP": "4"}
    assert helpers.subwoofer_count_options(old) == {"NON": "None", "1SP": "1", "2SP": "2"}
    await new.async_stop()
    await old.async_stop()


async def test_size_select_applies_only_to_size_tokens(make_device):
    helpers = conftest.load_ha_module("helpers")
    new, _ = await _device(make_device, "avc_x3900h")
    old, _ = await _device(make_device, "avr_x3600h")
    assert not helpers.is_size_token(new, new.state.speaker_sizes["FRO"])       # YES: presence, not a size
    assert helpers.is_size_token(old, old.state.speaker_sizes["FRO"])           # LAR
    await new.async_stop()
    await old.async_stop()


async def test_amp_mode_and_preamplifier(make_device):
    helpers = conftest.load_ha_module("helpers")
    new, _ = await _device(make_device, "avc_x3900h")
    old, _ = await _device(make_device, "avr_x3600h")
    assert helpers.amp_mode(new) == ("6", "7_1ch_front_b") and helpers.is_preamplifier(new) is False
    assert helpers.amp_mode(old) == ("6", "7_1ch_front_b") and helpers.is_preamplifier(old) is False
    await new.async_stop()
    await old.async_stop()


async def test_subwoofer_setup_follows_the_subwoofer_count(make_device):
    """Telnet first (SSSPCSWM/SWL); the manual: mode only with 2+ subwoofers,
    layout only in Directional mode."""
    helpers = conftest.load_ha_module("helpers")
    new, telnet = await _device(make_device, "avc_x3900h")
    assert helpers.has_subwoofer_setup(new)
    assert helpers.subwoofer_setup(new, "mode") is None          # one subwoofer
    telnet.feed("SSSPCSWF 2SP")
    assert helpers.subwoofer_setup(new, "mode") == "standard"    # SSSPCSWM STD
    assert helpers.subwoofer_setup(new, "layout") is None         # not Directional
    telnet.feed("SSSPCSWM DIR")
    assert helpers.subwoofer_setup(new, "mode") == "option_DIR"   # unknown token shown, not dropped
    # Measured on the AVC-X3900H (2026-11-24): DRC = Directional, 2FR = Front/Rear.
    telnet.feed("SSSPCSWM DRC")
    assert helpers.subwoofer_setup(new, "mode") == "directional"
    assert helpers.subwoofer_setup(new, "layout") == "left_right"
    telnet.feed("SSSPCSWL 2FR")
    assert helpers.subwoofer_setup(new, "layout") == "front_rear"
    # With 4 subwoofers SSSPCSWL keeps a 2-subwoofer value: not used, the setup
    # document decides (it does not show the layout in this recording).
    telnet.feed("SSSPCSWF 4SP")
    assert helpers.subwoofer_setup(new, "layout") is None
    old, _ = await _device(make_device, "avr_x3600h")
    assert not helpers.has_subwoofer_setup(old)
    await new.async_stop()
    await old.async_stop()


async def test_current_input_follows_si(make_device):
    helpers = conftest.load_ha_module("helpers")
    dev, telnet = await _device(make_device, "avc_x3900h")
    assert dev.state.zone("main").source == "TV"
    assert helpers.current_input_slot(dev, "input_signal_format") == "6"      # TV Audio, as globals 7 says
    telnet.feed("SIBD")                                                         # VIS on the BD slot
    assert helpers.current_input_slot(dev, "input_signal_format") == "3"
    telnet.feed("SIPHONO")                                                      # no per-input video item
    assert helpers.current_input_slot(dev, "input_signal_format") is None
    await dev.async_stop()
