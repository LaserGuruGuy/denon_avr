"""The entity catalogue against the real Home Assistant registries: no unique_id may change."""

from __future__ import annotations

import json
import pathlib

import pytest

import conftest
from conftest import drain

REGISTRY = pathlib.Path(__file__).parent / "golden" / "ha_registry"
# Keys that an OLDER version created and a later version deliberately replaced
# (git history), so they are not expected from the current catalogue.
RETIRED = {
    "sensor_amp_assign",   # b39a193 sensor -> 4b2b2ee replaced by the settable select_amp_assign
}


async def _catalogue(make_device, fixture):
    catalog = conftest.load_ha_module("catalog")
    dev, _session, telnet, fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    await drain(dev)
    entries = catalog.build(dev)
    await dev.async_stop()
    return catalog, dev, entries


def _keys(path: pathlib.Path) -> set[str]:
    rows = json.loads(path.read_text())["entities"]
    return {r["unique_id"].split("_", 1)[1] for r in rows}


@pytest.mark.parametrize(("fixture", "registry"), [
    ("avc_x3900h", "avc_x3900h_live.json"),
    ("avr_x3600h", "avr_x3600h_deleted.json"),
])
async def test_every_registered_entity_is_still_in_the_catalogue(make_device, fixture, registry):
    """Every unique_id Home Assistant has for this receiver is produced again (superset):
    existing entities keep their id, history and settings."""
    _catalog, _dev, entries = await _catalogue(make_device, fixture)
    keys = [e.key for e in entries]
    assert len(keys) == len(set(keys)), "duplicate keys"
    missing = _keys(REGISTRY / registry) - set(keys) - RETIRED
    assert not missing, sorted(missing)


async def test_unique_id_is_built_in_one_place(make_device):
    catalog, dev, entries = await _catalogue(make_device, "avc_x3900h")
    device = dev.discovery.device
    ident = catalog.device_identifier(device.mac_address, device.serial_number, dev.host)
    assert ident == dev.identifier
    assert catalog.unique_id(ident, entries[0].key) == f"{dev.identifier}_{entries[0].key}"


async def test_catalogue_snapshot(make_device):
    for fixture in conftest.FIXTURE_SETS:
        _catalog, _dev, entries = await _catalogue(make_device, fixture)
        conftest.assert_golden(fixture, "entity_catalogue", [
            {"platform": e.platform, "kind": e.kind, "key": e.key, "sub_device": e.sub_device, "enabled": e.enabled_default}
            for e in entries
        ])


async def test_known_entities_are_kept_when_the_receiver_is_silent(make_device, monkeypatch):
    """Silence is not absence: a web control hidden in the current setup, or a speaker
    group that did not answer, keeps its entity when Home Assistant already has it."""
    catalog, dev, entries = await _catalogue(make_device, "avc_x3900h")
    keys = {e.key for e in entries}
    assert "select_tv_format" not in keys                      # TVFormat display=1 on the X3900H
    known = {"select_tv_format", "select_crossover_FRH", "select_speaker_size_SUD", "select_crossover_NOPE"}
    restored = {e.key: e for e in catalog.build(dev, known)}
    assert {"select_tv_format", "select_crossover_FRH", "select_speaker_size_SUD"} <= set(restored)
    assert "select_crossover_NOPE" not in restored            # not a speaker group: ignored
    assert restored["select_crossover_FRH"].params["channels"] == ["FHL", "FHR"]


async def test_integration_disabled_entities_are_re_enabled_when_used(make_device):
    catalog, dev, entries = await _catalogue(make_device, "avc_x3900h")
    # SW2 is disabled by the integration (one subwoofer); C is configured
    disabled = {"number_channel_trim_SW2", "number_channel_trim_C"}
    configured = dev.discovery.configured_channels
    assert catalog.keys_to_enable(entries, disabled, configured) == {"number_channel_trim_C"}
    assert catalog.keys_to_enable(entries, disabled, set()) == set()          # setup unknown: decide nothing
    assert catalog.keys_to_enable(entries, {"sensor_amp_mode"}, configured) == set()  # only channel entities
    dev.discovery.configured_channels = set(dev.discovery.configured_channels) | {"SW2"}   # a 2nd sub
    entries = catalog.build(dev)
    assert catalog.keys_to_enable(entries, disabled, dev.discovery.configured_channels) == {
        "number_channel_trim_SW2", "number_channel_trim_C"}


async def test_channel_entities_carry_their_required_channels(make_device):
    _catalog, _dev, entries = await _catalogue(make_device, "avr_x3600h")
    by_key = {e.key: e for e in entries}
    assert by_key["number_channel_trim_SW2"].params["channels"] == ["SW2"]
    assert by_key["select_crossover_FRO"].params["channels"] == ["FL", "FR"]


async def test_inputs_sub_device(make_device):
    _catalog, _dev, entries = await _catalogue(make_device, "avc_x3900h")
    inputs = [e for e in entries if e.sub_device == "inputs"]
    keys = {e.key for e in inputs}
    assert {"select_input_1_input_signal_format", "select_input_1_input_hdcp", "select_input_6_input_hdcp"} <= keys
    assert all(e.enabled_default for e in inputs)                               # display 2/3: shown
    current = {e.key for e in entries if e.kind == "current_input_setting"}
    assert current == {"select_current_input_signal_format", "select_current_input_hdcp"}
    assert not any(e.key.startswith("select_input_") and "speaker_layout" in e.key for e in entries)  # staged: no direct per-item select
    _c2, _d2, legacy = await _catalogue(make_device, "avr_x3600h")
    assert not [e for e in legacy if e.sub_device == "inputs"]                  # legacy: no per-input documents



async def test_upgrade_of_the_live_x3900h_registry(make_device):
    """Upgrading the live installation: nothing appears disabled-to-enabled by mistake,
    and the enabled channel entities that become unavailable are exactly the surround
    back ones (the receiver reports SSSPCSBK NO)."""
    catalog, dev, entries = await _catalogue(make_device, "avc_x3900h")
    rows = json.loads((REGISTRY / "avc_x3900h_live.json").read_text())["entities"]
    enabled = {r["unique_id"].split("_", 1)[1] for r in rows if r["disabled_by"] is None}
    integration_disabled = {r["unique_id"].split("_", 1)[1] for r in rows if r["disabled_by"] == "integration"}
    configured = dev.discovery.configured_channels
    assert catalog.keys_to_enable(entries, integration_disabled, configured) == set()
    unavailable = {
        e.key for e in entries
        if e.key in enabled and e.params.get("channels") and not any(c in configured for c in e.params["channels"])
    }
    assert unavailable == {
        "number_channel_trim_SBL", "number_channel_trim_SBR",
        "number_channel_distance_SBL", "number_channel_distance_SBR", "select_crossover_SBK",
    }


async def test_staged_speaker_layout_entities(make_device):
    _catalog, _dev, entries = await _catalogue(make_device, "avc_x3900h")
    keys = {e.key for e in entries}
    assert {"select_speaker_layout_assign", "button_speaker_layout_apply", "button_speaker_layout_discard"} <= keys
    _c, _d, legacy = await _catalogue(make_device, "avr_x3600h")
    assert not {e.key for e in legacy} & {"select_speaker_layout_assign", "button_speaker_layout_apply"}


async def test_speaker_preset_only_where_the_receiver_has_it(make_device):
    _c, _d, new = await _catalogue(make_device, "avc_x3900h")
    _c2, _d2, old = await _catalogue(make_device, "avr_x3600h")
    assert "select_speaker_preset" in {e.key for e in new}
    assert "select_speaker_preset" not in {e.key for e in old}
