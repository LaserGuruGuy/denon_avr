"""Push handling: only real changes notify; telnet events re-read the related web documents."""

from __future__ import annotations

import asyncio

import conftest  # noqa: F401  # pyflakes: ignore - loads the avr package
from conftest import drain

SIGNAL_CLUSTER = ["SSINFAISSIG 02", "SYSDA PCM", "OPINFINS 22111111111111000000", "OPINFASP 22121100000000000000000000000000"]


async def _started(make_device, fixture="avc_x3900h"):
    dev, session, telnet, fx = make_device(fixture)
    fired: list[int] = []
    dev.register_update_callback(lambda: fired.append(1))
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    await drain(dev)
    await asyncio.sleep(0.15)
    fired.clear()
    return dev, session, telnet, fired


async def test_repeated_signal_cluster_does_not_notify(make_device):
    dev, _session, telnet, fired = await _started(make_device)
    for _ in range(3):                       # the X3900H pushes this every ~5 s
        for line in SIGNAL_CLUSTER:
            telnet.feed(line)
        await asyncio.sleep(0.15)
    assert fired == []
    await dev.async_stop()


async def test_a_real_change_notifies(make_device):
    dev, _session, telnet, fired = await _started(make_device)
    telnet.feed("MV31")
    await asyncio.sleep(0.15)
    assert fired == [1]
    await dev.async_stop()


async def test_source_change_rereads_the_video_web_section(make_device):
    dev, session, telnet, _fired = await _started(make_device)
    before = len(session.requests)
    telnet.feed("SIGAME")
    await asyncio.sleep(1.2)                 # > the group's debounce (1.0 s)
    await drain(dev)
    urls = [r["url"] for r in session.requests[before:]]
    assert any("/ajax/video/get_config?type=14" in u for u in urls)
    assert any("/ajax/video/get_config?type=11" in u for u in urls)
    await dev.async_stop()


async def test_web_refresh_is_single_flight(make_device):
    dev, _session, _telnet, _fired = await _started(make_device)
    calls: list[int] = []
    gate = asyncio.Event()

    async def slow_refresh():
        calls.append(1)
        await gate.wait()

    dev._sync._web_refreshers["video"] = slow_refresh
    first = asyncio.ensure_future(dev._sync.refresh_web("video"))
    await asyncio.sleep(0)
    for _ in range(5):                        # five triggers while the first read runs
        await dev._sync.refresh_web("video")
    gate.set()
    await first
    assert len(calls) == 2                    # the running read + exactly one re-run
    await dev.async_stop()


async def test_unchanged_signal_cluster_requeries_nothing(make_device):
    dev, _session, telnet, _fired = await _started(make_device)
    telnet.sent.clear()
    for line in SIGNAL_CLUSTER:
        telnet.feed(line)
    await asyncio.sleep(1.2)
    await drain(dev)
    assert telnet.sent == []
    await dev.async_stop()


async def test_speaker_virtualizer_does_not_trigger_speakers_web(make_device):
    dev, session, telnet, _fired = await _started(make_device)
    before = len(session.requests)
    telnet.feed("PSSPV ON")
    telnet.feed("PSSPV OFF")
    await asyncio.sleep(1.7)
    await drain(dev)
    assert not [r for r in session.requests[before:] if "/ajax/speakers/" in r["url"]]
    await dev.async_stop()


async def test_unmodelled_setup_push_triggers_speakers_web(make_device):
    dev, session, telnet, _fired = await _started(make_device)
    before = len(session.requests)
    telnet.feed("SPPR 2")                      # speaker preset: not modelled by the parser yet
    await asyncio.sleep(1.7)
    await drain(dev)
    assert [r for r in session.requests[before:] if "/ajax/speakers/" in r["url"]]
    await dev.async_stop()


async def test_poll_and_trigger_share_the_single_flight(make_device):
    dev, _session, _telnet, _fired = await _started(make_device)
    calls: list[str] = []
    for name in ("video", "speakers", "graphic_eq"):
        async def _r(name=name):
            calls.append(name)
        dev._sync._web_refreshers[name] = _r
    await dev.async_poll()
    assert sorted(calls) == ["graphic_eq", "speakers", "video"]
    await dev.async_stop()
