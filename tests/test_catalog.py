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
