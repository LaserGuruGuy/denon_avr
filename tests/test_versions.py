"""The versions the receiver reports are kept; no behaviour depends on them."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("fixture", "module"), [("avc_x3900h", "Aios 6.5V"), ("avr_x3600h", "Aios 4.025")]
)
async def test_reported_versions(make_device, fixture, module):
    dev, _session, _telnet, _fx = make_device(fixture)
    await dev.async_discover()
    assert dev.discovery.device.versions == {
        "DeviceInfoVers": "0001", "CommApiVers": "0301", "Gen": "0002", "BrandCode": "0",
        "capability_version": "4", "moduleType": module,
    }
    assert dev.discovery.device.hardware_type == "avr-x-2016"
    assert dev.discovery.device.hardware_label == f"{module} (CommApi 0301)"


@pytest.mark.parametrize(
    ("fixture", "label", "build"),
    [("avc_x3900h", "3.139.174 · DTS 4.30.10.15", "5300-1084-C071-9631"), ("avr_x3600h", "3.139.173 · DTS 3.90.50.82", "2400-9173-2171-9031")],
)
async def test_firmware_with_dts_version(make_device, fixture, label, build):
    dev, _session, _telnet, _fx = make_device(fixture)
    await dev.async_discover()
    assert dev.discovery.device.firmware_label == label
    assert dev.discovery.device.firmware_build == build


async def test_versions_are_read_again_after_a_reconnect(make_device):
    """A firmware update restarts the receiver; the reconnect reads the versions again."""

    dev, session, telnet, fx = make_device("avr_x3600h")
    await dev.async_discover()
    await dev.async_start()
    fx_text = fx.file("ajax_general_12.xml")
    original = fx.file
    fx.file = lambda name: fx_text.replace("3.90.50.82", "3.91.0.1") if name == "ajax_general_12.xml" else original(name)
    assert await dev._discovery_service.refresh_versions()
    assert dev.discovery.device.dts_version == "3.91.0.1"
    assert not await dev._discovery_service.refresh_versions()
    await dev.async_stop()
