"""Characterization (golden) tests: pin the CURRENT end-to-end behaviour of the avr library.

These are the safety net for the refactor in step 2: after moving code, every golden
must be byte-identical. They intentionally test through the public facade
(DenonAvrDevice) with only the network replaced (see conftest).
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import FIXTURE_SETS, assert_golden, drain

pytestmark = pytest.mark.asyncio


def _sample_values(spec):
    """Representative values per control kind (deterministic)."""
    kind = spec.kind
    if kind == "onoff":
        return [True, False]
    if kind == "enum":
        values = spec.get("values") or []
        return values[:1] + values[-1:] if values else ["X"]
    if kind == "level":
        return [0.0, 1.5, -2.0]
    if kind == "centered":
        return [0, 5, -5]
    if kind in ("signed_int",):
        return [-1, 3]
    if kind == "integer":
        return [0, 10]
    if kind == "minutes":
        return [0, 30]
    return []


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_discovery(make_device, fixture):
    dev, session, _telnet, _fx = make_device(fixture)
    discovery = await dev.async_discover()
    assert_golden(fixture, "discovery", discovery)
    assert_golden(fixture, "http_requests_discovery", session.requests)
    assert_golden(fixture, "probe_queries", dev.test_probe.queries)


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_resync_queries_and_start(make_device, fixture):
    dev, _session, telnet, _fx = make_device(fixture)
    await dev.async_discover()
    assert_golden(fixture, "resync_queries", dev._resync_queries())
    await dev.async_start()
    await drain(dev)
    assert_golden(fixture, "telnet_sent_on_start", telnet.sent)
    await dev.async_stop()


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_telnet_replay_state(make_device, fixture):
    dev, _session, telnet, fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    await drain(dev)
    telnet.sent.clear()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    await drain(dev)
    assert_golden(fixture, "state_after_replay", dev.state)
    assert_golden(fixture, "discovery_after_replay", dev.discovery)
    assert_golden(fixture, "telnet_sent_during_replay", telnet.sent)
    await dev.async_stop()


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_setter_commands(make_device, fixture):
    """Every public setter's exact wire string (byte-for-byte golden)."""
    dev, _session, telnet, fx = make_device(fixture)
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    await drain(dev)
    out: dict[str, object] = {}

    async def record(label, coro):
        telnet.sent.clear()
        try:
            await coro
            await drain(dev)
            out[label] = list(telnet.sent)
        except Exception as err:  # noqa: BLE001 - the golden records failures too
            out[label] = f"raised {type(err).__name__}"

    profile = dev.profile
    for control_id in sorted(profile.controls):
        spec = profile.control(control_id)
        for value in _sample_values(spec):
            await record(f"control:{control_id}={value!r}", dev.async_set_control(control_id, value))
    zones = [z.id for z in dev.discovery.zones] if dev.discovery.zones else ["main"]
    for zone in zones:
        await record(f"power:{zone}=on", dev.async_set_power(zone, True))
        await record(f"power:{zone}=off", dev.async_set_power(zone, False))
        await record(f"mute:{zone}=on", dev.async_set_mute(zone, True))
        await record(f"volume_level:{zone}=0.5", dev.async_set_volume_level(zone, 0.5))
        await record(f"volume_step:{zone}=up", dev.async_volume_step(zone, True))
        sources = [s.name for s in dev.discovery.sources][:2]
        for name in sources:
            await record(f"source:{zone}={name}", dev.async_select_source(zone, name))
    await record("system_power=off", dev.async_set_system_power(False))
    modes = list(dev.discovery.all_sound_modes or [])[:3]
    for mode in modes:
        await record(f"sound_mode={mode}", dev.async_select_sound_mode(mode))
    await record("quick_select=1", dev.async_select_quick_select(1))
    channels = [c.code for c in dev.discovery.channels][:2]
    for code in channels:
        await record(f"channel_trim:{code}=+1.0", dev.async_set_channel_trim(code, 1.0))
        await record(f"channel_distance:{code}=3.2", dev.async_set_channel_distance(code, 3.2))
    for group in sorted(dev.state.crossovers)[:2]:
        await record(f"crossover:{group}=120", dev.async_set_crossover(group, 120))
    await record("crossover:ALL=80", dev.async_set_crossover("ALL", 80))
    for mode in ("IDV", "ALL"):
        await record(f"crossover_mode={mode}", dev.async_set_crossover_mode(mode))
    for group in sorted(dev.state.speaker_sizes)[:2]:
        await record(f"speaker_size:{group}=SMA", dev.async_set_speaker_size(group, "SMA"))
    for zone in zones:
        await record(f"volume_raw:{zone}=45.5", dev.async_set_volume_raw(zone, 45.5))
    for code in sorted(dev.profile.sound_mode_genre_commands)[:4]:
        await record(f"sound_mode_group={code}", dev.async_select_sound_mode_group(code))
    assert_golden(fixture, "setter_commands", out)
    heos = dev.test_heos
    for name in ("async_media_play", "async_media_pause", "async_media_stop", "async_media_next", "async_media_previous"):
        await getattr(dev, name)()
    assert_golden(fixture, "heos_calls", heos.calls)
    await dev.async_stop()


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_web_subsystems(make_device, fixture):
    """Graphic EQ / video / speaker web-config view after discovery (pins the legacy path)."""
    dev, session, _telnet, _fx = make_device(fixture)
    await dev.async_discover()
    view = {}
    for name in ("video_config", "speaker_config"):
        ctl = getattr(dev, name)
        ids = sorted(c["id"] for c in ctl.controls())
        view[name] = {
            "supported": ctl.supported,
            "controls": {
                c: {"present": ctl.present(c), "available": ctl.available(c), "value": ctl.value(c), "options": ctl.options(c)}
                for c in ids
            },
            "feature_flags": ctl.feature_flags() if hasattr(ctl, "feature_flags") else None,
            "availability": ctl.availability_map(),
            "collections": {spec["id"]: ctl.collection(spec["id"]) for spec in ctl.collection_specs()},
            "value_lists": {spec["id"]: ctl.value_list(spec["id"]) for spec in ctl.value_list_specs()},
        }
    eq = dev.graphic_eq
    view["graphic_eq"] = {"supported": eq.supported}
    assert_golden(fixture, "web_subsystems", view)
    assert_golden(fixture, "http_requests_web", session.requests)


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_web_set_paths(make_device, fixture):
    """Exact set_config URLs (payload included) of the web subsystems; never sent anywhere."""
    dev, session, _telnet, _fx = make_device(fixture)
    await dev.async_discover()
    out: dict[str, object] = {}

    async def record(label, coro):
        before = len(session.requests)
        await coro
        out[label] = [r["url"] for r in session.requests[before:]]

    for name in ("video_config", "speaker_config"):
        ctl = getattr(dev, name)
        for control in ctl.controls():
            cid = control["id"]
            if ctl.present(cid):
                options = list(ctl.options(cid)) or [ctl.value(cid) or "1"]
                await record(f"{name}:{cid}={options[-1]}", ctl.async_set(cid, options[-1]))
    eq = dev.graphic_eq
    await record("geq:refresh", eq.refresh())
    await record("geq:set_channel=1", eq.set_channel(1))
    tags = sorted(eq.state.bands) or ["Eq1kHz"]
    await eq.set_band(tags[0], -1.5)
    await record("geq:apply", eq.apply())
    await record("geq:set_speaker_selection=2", eq.set_speaker_selection("2"))
    await record("geq:curve_copy", eq.curve_copy())
    await record("geq:set_defaults", eq.set_defaults())
    out["geq_state"] = eq.state
    assert_golden(fixture, "web_set_paths", out)


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_availability_poll_and_fallback(make_device, fixture):
    """Update callback debounce, availability transitions and the goform fallback poll."""
    dev, session, telnet, _fx = make_device(fixture)
    fired: list[bool] = []
    dev.register_update_callback(lambda: fired.append(dev.available))
    await dev.async_discover()
    await dev.async_start()
    await drain(dev)
    await asyncio.sleep(0.15)                       # > debounce (0.1 s)
    trace = {"after_start": {"available": dev.available, "callbacks": list(fired)}}
    fired.clear()
    for _ in range(5):                              # a burst collapses into one notification
        telnet.feed("MV50")
    await asyncio.sleep(0.15)
    trace["burst_callbacks"] = list(fired)
    fired.clear()
    await dev.async_poll()                          # telnet up: only confirms reachability
    trace["poll_connected_zone"] = dev.state.zones.get("main")
    telnet.feed("MV30")                             # diverge from the HTTP status (43) ...
    telnet.drop()                                   # ... so the fallback below is observable
    await asyncio.sleep(0.15)
    trace["after_drop"] = {"available": dev.available, "callbacks": list(fired)}
    before = len(session.requests)
    await dev.async_poll()                          # telnet down: recover core zone state over HTTP
    await asyncio.sleep(0.15)
    trace["poll_fallback_zone"] = dev.state.zones.get("main")
    trace["poll_fallback_requests"] = session.requests[before:]
    trace["poll_fallback_callbacks"] = list(fired)
    assert_golden(fixture, "availability_poll", trace)
    trigger = next(iter(next(iter(dev.profile.refresh_groups.values()))["on"]))
    telnet.feed(trigger)                            # schedules a debounced re-query task
    assert dev._tasks.pending
    await dev.async_stop()
    assert not dev._tasks.pending, "async_stop must cancel every background task"


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_fallback_sources_without_introspection(make_device, fixture):
    """Without SSFUN/SSSOD answers the Deviceinfo source list is used (await_ready)."""
    dev, _session, _telnet, _fx = make_device(fixture)
    dev.test_probe.exclude = ("SSFUN", "SSSOD")
    await dev.async_discover()
    assert dev.discovery.sources == []
    await dev.async_await_ready(timeout=0.2)
    assert_golden(fixture, "fallback_sources", dev.discovery.sources)


async def test_known_x3900h_crossover_gap(make_device):
    """Known v1.1.2 defect, pinned on purpose: the X3900H reports no crossover speaker-
    selection mode, so 'All crossover' is permanently unavailable. Step 4 fixes this from
    /ajax/speakers type 6; this test must then be changed deliberately."""
    dev, _session, telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    await drain(dev)
    assert "crossover_speaker_selection" not in dev.state.values
    assert "crossover_all" not in dev.state.values
    assert "FRO" not in dev.state.crossovers, "'FUL' (full range) is dropped silently"
    assert fx.file("ajax_speakers_6.xml") and 'mode="normal"' in fx.file("ajax_speakers_6.xml")
    await dev.async_stop()


@pytest.mark.parametrize("fixture", FIXTURE_SETS)
async def test_known_probe_stops_early(make_device, fixture):
    """Known v1.1.2 defect, pinned on purpose: the introspection probe is 'complete' after
    the FIRST SSSPC/SSFUN line, so discovery holds only part of the user's speaker setup
    (FL/FR) until the persistent resync fills it in. To be fixed by completing on each
    list's END; this test must then be changed deliberately."""
    dev, _session, _telnet, _fx = make_device(fixture)
    discovery = await dev.async_discover()
    assert sorted(discovery.configured_channels) == ["FL", "FR"]
    assert list(discovery.speaker_config) == ["FRO"]


async def test_known_x3900h_yes_no_speaker_grammar(make_device):
    """Known v1.1.2 defect, pinned on purpose: the X3900H reports speaker presence as
    YES/NO ('SSSPCSBK NO') while the profile only knows the absent token NON, so the
    surround back channels count as configured although the user has none."""
    dev, _session, telnet, fx = make_device("avc_x3900h")
    await dev.async_discover()
    await dev.async_start()
    for rec in fx.telnet_corpus:
        telnet.feed(rec["line"])
    await drain(dev)
    assert any(r["line"] == "SSSPCSBK NO" for r in fx.telnet_corpus)
    assert sorted(dev.discovery.configured_channels) == ["C", "FL", "FR", "SBL", "SBR", "SL", "SR", "SW"]
    await dev.async_stop()
