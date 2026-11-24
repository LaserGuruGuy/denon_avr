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
    assert dev.discovery.device.hardware_label == "avr-x-2016 (CommApi 0301)"
