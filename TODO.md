# Roadmap — features that can, could, or may be added

What the receiver exposes that this integration does **not** yet surface, grouped
by menu/area. Each item notes the transport (telnet port 23, goform HTTP 8080,
setup config HTTPS 10443, HEOS 1255, calibration TCP 1256), the command/tokens,
and a confidence tag:

- **can** — mechanism known and verified; a small, well-understood addition.
- **could** — mechanism known but needs a live-verify pass or is context-gated.
- **may** — plausible but unproven; needs reverse-engineering or specific hardware.

The integration only ever creates an entity when the connected receiver
advertises the capability, so every item below is additive and self-gating.

## Audio
- **can — Power-On Volume** (telnet `SSVCTZMAPON`, family read `SSVCTZMA ?`). The
  power-on level, a sibling of the volume scale/limit/mute-level we already have.
  Deferred only because it is a mixed type (Last / Mute / a dB level) needing a
  small composite control (a select for Last/Mute plus a number for the level).
- **could — Second subwoofer level** (`/ajax` audio `type=3`, `SubwooferLevel2`).
  We expose Subwoofer Level (telnet `PSSWL`); a 2nd sub's level is `/ajax`-only and
  only settable when the receiver reports two subwoofers. Add gated on the live
  Subwoofer count (`SSSPCSWF 2SP`), verify against a real 2-sub setup.
- **could — Surround-parameter extras** (telnet `PS…`: `PSPAN` Panorama, `PSDIM`
  Dimension, `PSCEN`/`PSCEI` Center Image/Width, `PSSTW`/`PSSTH` Stage Width/Height,
  `PSDEH` Dialogue Enhancer, `PSBSC` Bass Sync, high/low-pass filters). All are DSP
  **mode-gated** — the receiver only accepts/reports them in the matching sound
  mode, so each must be live-verified while that mode is active.
- **may — IMAX / Auro-3D / DTS parameters** (`PSIMAX*`, `PSAURO*`). Present in the
  protocol superset but not licensed/active on every model; verify per device.

## Video / Picture
- **could — Picture controls write path** (telnet `PVCN`/`PVBR`/`PVST`/`PVDNR`/
  `PVENH`, already implemented). Reads work; **writes are signal-gated** — confirm
  set→restore with an active HDMI video source, then they are done.
- **could — Output Settings** (`/ajax` video `type=4`): video conversion, i/p
  scaler, resolution, sharpness, progressive mode. `/ajax`-only, and each is gated
  on Video Conversion being on + an active signal — build behind those gates.
- **may — 4K/8K signal format per input, HDMI scaler, screen saver** (`/ajax`
  video `type=13/14`, `FourKEightK…`). Absent on the reference unit (X3600H);
  add when a device advertises them (availability map `type=1`).

## Speakers / Calibration
- **could — Speaker "for" assignment** (`/ajax` speakers `type=2`, `SpeakerFor`).
  Read-only on the reference unit (no `<List>`); becomes a settable select on
  configs where the receiver offers a list — reuse the amp-assign controller.
- **may — Full Audyssey MultEQ calibration** (TCP **port 1256**, JSON frames;
  transport `avr/transport/tcp_client.py` is already built and reserved). Reading
  amp/speaker/distance is non-disruptive (`GET_AVRINF`/`GET_AVRSTS`); the measure/
  write path (`ENTER_AUDY`, `SET_COEFDT`, filter coefficients) is a large, stateful
  sub-protocol that interrupts playback — a separate opt-in module, never wired
  into the live coordinator.
- **may — Bass sync, LFE distribution, 2-channel playback, XLR/pre-out assign**
  (`/ajax` speakers `type=9/12/16/18`). `/ajax`-only advanced setup; lower daily
  value, mostly set-once.

## Zones
- **could — Full Zone 2/3 feature parity** (telnet `Z2…`/`Z3…`): per-zone tone,
  channel level, HPF, sleep, quick-select. We expose per-zone power/volume/mute/
  source; the rest are documented tokens to add per zone the receiver reports.

## Inputs / General / System
- **could — Auto-standby** (telnet `STBY 15M/30M/60M/OFF`, `Z2STBY`/`Z3STBY`). A
  simple select; not signal-gated.
- **could — Trigger outputs** (telnet `TR1`/`TR2 ON/OFF`) — simple switches.
- **could — Input assignment / rename / hide** (rename & visibility are already
  *read* via `SSFUN`/`SSSOD`; making them settable, plus per-input decode-mode and
  level-trim, is `/ajax` inputs-menu territory).
- **may — Display/General options** (language, timezone, per-zone power-on
  defaults, setup lock). `/ajax` general/globals menus; mostly set-once, some
  setup-lock gated (HTTP 423).

## Network / HEOS (port 1255)
- **could — Media browsing & richer transport.** We expose now-playing (title/
  artist/album/art) and play/pause/next for network sources; browsing favourites/
  playlists and seek/shuffle/repeat are further HEOS CLI additions.

## Cross-cutting
- **Multi-language (i18n).** Entity **names** are localised (v1.1.0): every
  fixed-identity entity has a `_attr_translation_key` with English
  (`strings.json`/`translations/en.json`) and Dutch (`translations/nl.json`);
  device-derived names (per channel/group, sound modes, sources) stay dynamic.
  *Remaining:* **select option (state) localisation** — the fixed-enum options
  (Large/Small, Individual/All, None/1/2, NTSC/PAL, All/Video/Off, HDMI signal
  format, etc.) are still shown in English; adding a `state` block per select and
  switching option handling to stable keys would localise those too. Device-
  supplied options (sound modes, sources, amp-assign labels, pass-through source)
  legitimately stay dynamic.
- **could — Newer receiver models.** Amp-assign options are device-driven off the
  queried `AmpType`; the option table covers the current lineup (2ch … 15-channel,
  Atmos height types). A 2024+ model adding a new `AmpType`/`AssignMode` id would
  extend the table once that device's setup data is available.
- **may — goform AppCommand.** The `/goform/AppCommand.xml` batch API returns empty
  on this firmware generation; on models where it works it could replace some
  polling. Not needed today (telnet push + StatusLite cover it).

## Test safety net: follow-ups from the step-1 architecture review
- HA layer tests (pytest-homeassistant-custom-component): coordinator, entity availability,
  unique_ids against tests/golden/ha_registry, diagnostics redaction.
- More layer rules: pure modules import only pure modules/const; services never import
  concrete transports; the library never imports `..` outside avr/.
- Telnet transport tests (framing, CR split, queue, reconnect) through a fake open_connection.
- Faster tests that do not depend on timing: a fake clock instead of the real 0.8 s / 0.1 s sleeps.
- Wider redaction: 10/8, 172.16/12, IPv6, hostnames, SSID, HEOS pid.
- (done) the synthetic X3600H set is replaced by a real capture.

## Generic app: user setup is discovered and followed live (architect review 2026-11-22)
Principle: protocol constants live in the profile; device capabilities are discovered;
user setup (speakers, sizes, crossovers, subwoofers, sources, names, zones, presets, amp
assign / speaker layout) is discovered AND followed at runtime. Never one household's setup.
- must: introspection probe completes on each list's END, not after the first line
  (pinned: test_known_probe_stops_early).
- must: SSSPC rebuilt per response cycle; presence tokens per protocol version (YES/NO
  next to NON/LAR/SMA) (pinned: test_known_x3900h_yes_no_speaker_grammar).
- must: device-supplied value lists first (crossover grid incl. FUL/70, LPF 70, limits
  "70" vs "070"), the profile only as label/token dictionary; unknown value = raw option.
- must: speaker groups FRW/SHE/TPS/CHE, subwoofers SW3/SW4 from the device.
- must: setup refresh group (SSSPC, SSCFR, SSFUN, SSSOD, SSQSN, amp assign) + entities
  that follow setup live (capability -> entity exists; user setup -> available), dynamic
  add; replace one-off disabled_by=INTEGRATION by availability.
- should: zone names/sources per zone (globals type 7), Z2 sub-commands (CV/PS/SLP/QUICK)
  not parsed as source, zone-2 HTTP fallback, distance unit (SSSDESTP), core controls only
  when the setup has them (Front B, SSSWM).
- tests: synthetic variations derived from the real corpora under tests/fixtures/synthetic/
  (S1 speaker change at runtime, S2 YES/NO, S3 subwoofer count, S4 crossover FUL/70,
  S5 source rename/hide/duplicate, S6 zones 1/3, S7 web 500/out-of-table values, S8 misc),
  asserting invariants rather than goldens.
- API coverage: 11080 web interface, SpeakerLayout/Crossovers/Distances/SpeakerPreset docs,
  per-input 4K/8K, zone features, source management writes, quick select per zone, HEOS
  favourites/groups, port 1256 (tcp_client.py is unused).

## Refactor progress (plan v4)
- [x] step 1: test safety net (fixtures X3900H + X3600H, goldens, layer guard, CI)
- [x] step 2: 1:1 refactor into transport / protocol / services / facade; HA imports only `.avr`
- [ ] step 3: webconfig endpoint probe (443 Location -> 10443 / 11080), lock, re-probe policy
- [ ] step 4: schema engine for /ajax documents (variants, per-input collections, device value lists)
- [ ] step 5: HA adapter (entity catalogue, Inputs device, live user-setup following, i18n)
- [ ] step 6: telnet / 1256 parity; then live verification, docs, 1.2.0
- later (from the step-2 review): HA-layer pyflakes warnings (select.py:225, const.py:12, sensor.py:19);
  volume step default 0.5 from the profile; a public test hook instead of `_tasks.pending`.
