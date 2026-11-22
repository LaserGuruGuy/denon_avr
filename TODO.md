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
- [x] step 3: webconfig endpoint probe (80/443 redirect -> 10443 / 11080, candidates), serialised requests, re-probe policy, configuration_url
- [~] step 4 (mostly): /ajax document engine: Availability as the receiver's UI reads it (display
  2/3/4 shown, 3/4 active, gray = reason of a disabled item), per-input collections (4K/8K format,
  HDCP; speaker layout read-only), device value lists (crossover, LPF), one fetch per document per
  pass. Telnet checked first: SpeakerPreset is SPPR (telnet), PSSP and SSLFL are telnet; for per-input
  4K/HDCP no telnet command was FOUND in the current setup (silence is not absence: re-check). Moved to step 5: graphic EQ onto the availability map (audio listSetupMenu),
  Amp Assign / Speaker Layout as explicit document variants.
- step 5 (revised 2026-11-22 after the push analysis):
  - [x] 5a push/refresh: no poll-timer reset on push, notify only on real change, duplicate OPSML query
    removed, web sections re-read on related telnet events (single flight)
  - [x] 5b telnet setup grammar: probe completes on a sentinel (SSSPC has no END line), presence
    YES/NO/NON, count groups 1-4, groups SHE/TPS/CHE, SSSPC per kind + per cycle, interrupted lists
    not duplicated. Still open (5c/6): crossover select option 0 = Full range, LPF 70/070, group FRW (no evidence
    of its code yet), size selects only where the receiver reports a size token (X3900H: YES only).
  - [x] 5c pure entity catalogue (catalog.py) + one unique_id function + test against both registries
    (all 150 live X3900H ids and all X3600H ids except the retired sensor_amp_assign); platforms build
    from it. Device-driven: crossover options incl. 0 = Full Range / 70 Hz, subwoofer count 1..
    SubwooferNum with the receiver's own absent token, size selects unavailable where presence is
    reported, quick select from Deviceinfo, SW2/FWL/FWR from the telnet level list; read-only sensors
    amp mode (incl. pre-amplifier) and subwoofer mode/layout; en/nl translations for them.
    Was:
    from the 5b review: quick select entity from Deviceinfo QuickSelect when SSQSNZMA is silent
    (X3900H), subwoofer count labels from by_count (1-4), size selects only where a size token is
    reported, crossover options from the device value list (incl. 0 = Full), LPF 70/070
  - [~] 5d CODE DONE, ARCHITECT REVIEW PENDING (commit marked so): availability per channel (telnet vs last good web read), dynamic entities, registry restore of
    temporarily hidden entities, retry of the first web discovery; replaces _apply_channel_defaults
  - [ ] 5e Inputs sub-device, current input (immediate via SI), i18n (state keys, {input}, device
    translation_key, issues, exceptions); graphic EQ onto the audio availability map
  - [ ] 5f speaker layout staged + Apply (code and tests only, no live write)
  - [ ] 5g diagnostics counters (triggers, notifies, polls, last web read per document)
- [ ] step 6: telnet parity (SPPR select, PSSP:, VSSCH OFF, Z2 sub-commands); 7 live verification; 8 docs, 1.2.0; 9 push
- later (from the step-2 review): HA-layer pyflakes warnings (select.py:225, const.py:12, sensor.py:19);
  volume step default 0.5 from the profile; a public test hook instead of `_tasks.pending`.

## Availability display=1 ("hidden"): DECIDED 2026-11-22 (owner agreed with the architect advice)
Measured on the X3900H: FourKSignalFormat=1 -> video type 8 answers 500, AmpAssign=1 -> speakers type
2 answers 500, TVFormat=1 -> video type 9 answers 200 with a meaningless value. So display=1 means
"not applicable now". Architect advice: do not read such documents, but follow the availability map
live (re-read on every poll, done in step 3). 1->2/3: read the document and add the entity dynamically;
3->2: grayed (unavailable); >=2->1: unavailable, not removed; never seen >=2: no entity.
Availability maps go into diagnostics (done in step 4). display=4 is active, like 3 (Web Control bundle).

## Before the push (owner's condition: tested on BOTH receivers, with permission)
- live read test of every web subsystem on both receivers;
- one harmless, reversible write over Web Control (plain HTTP, GET), e.g. OSD Volume, on the X3900H
  and the same on the X3600H, only with the owner's explicit yes;
- retry of the first web discovery when the receiver's web stack is not ready at HA start (step 5,
  together with dynamic entities).

## From step 4 for steps 5/6
- SpeakerPreset on telnet: `SPPR ?` -> `SPPR 1` (X3900H only) -> telnet control (step 6).
- `PSSP:` (speaker layout height) answers on both receivers -> telnet control (step 6).
- VSSCH reports `OFF` on the X3900H (new token next to AUTO/48P/...) -> profile (step 6).
- Crossover 'FUL' on telnet == '0' in the web list: parsed and sent since 5b. Still open (5c): the
  crossover select has no 0 / Full range option. LPF web values '70' vs telnet width '070' -> normalise.
- Speaker layout labels (mode ids 1..19, per-position config ids) from the Web Control bundle, and a
  staged, confirmed apply (CustomSettings path) -> step 5; never without the owner's yes.

## To observe live with the owner (read-only, owner present)
- Does the receiver push SS*, SPPR, PSSP or MNMEN when a setting is changed by hand (OSD/app)?
- How many telnet sessions does it allow, and do replies go to all of them? The X3900H corpus likely
  contains the running HA session's traffic -> recapture with the integration disabled.
- Debug log: "Finished fetching" keeps appearing every 60 s while telnet is busy (after 5a).

## Silence is not absence: re-check items that did not answer (other setups, both receivers)
- per-input 4K/8K format and HDCP on telnet (candidate queries silent on both receivers, 2026-11-22);
- SPPR on the AVR-X3600H (silent; SpeakerPreset may not exist there, or may need another setup);
- what the X3900H reports for a speaker group that is switched to "None" (NO, or omitted?), observed
  read-only while the owner changes it.
- Probe sentinel PW? is also the keepalive and other sessions may receive PW replies: consider a
  dedicated sentinel (it is combined with "no open list" and the 0.4 s quiet gap already).
- SSSWM ? is silent on the X3900H while SSSPCSWM carries the subwoofer mode -> step 6.
- Setup load: the probe does not ask SI?/MS?, so the resync triggers the refresh groups once more.

## From the owner's manuals (AVR-X3600H EU, AVC-X3900H EU online manuals, read 2026-11-22)
Sources: https://manuals.denon.com/AVRX3600H/EU/EN/DRDZSYasydxfyy.php (Amp Assign),
GFNFSYcrpfrlsu.php (Speaker Config.), GFNFSYaxhqtbzr.php (Bass), GFNFSYitmloggd.php (Crossovers);
https://manuals.denon.com/AVCX3900H/EU/EN/GMISSYldtrlyhd.php (Amp Assign), DRDZSYyfgvveow.php
(Front/Center/Surround/Surround Back), GMISSYqdkjlbbz.php (Subwoofers), GMISSYtggmpgvn.php (Subwoofer
Mode), GMISSYbjvpcnxf.php (Subwoofer Layout), GFNFSYaxhqtbzr.php (Subwoofer Output), DRDZSYdbdgoqfu.php
(Speaker Preset). The X3900H page bodies came through a summariser: option names likely right, verify.
- Two speaker models, by generation (capability, not model): legacy SIZE per group (Large/Small/None);
  Web Control PRESENCE per group (Yes/No) + CONNECTION ("Spkr + Pre-out" / "Pre-out Only") per group
  (= SpeakerLayout item <Conn>). Catalogue (5c): size selects only for size-token groups; presence +
  connection (read-only first) for the new generation.
- Subwoofers: legacy 1-2 ("None", "1 spkr", "2 spkrs"); Web Control 1-4 ("No".."4 spkrs").
  Subwoofer Mode Standard/Directional only with 2+ subs (SpeakerLayout item 16, SSSPCSWM STD);
  Subwoofer Layout only in Directional (item 17, SSSPCSWL 2LR). Legacy "Subwoofer Mode" LFE/LFE+Main
  (SSSWM) is called "Subwoofer Output" on Web Control (SSSWM ? silent on the X3900H -> live check).
- Crossover: Web Control "Full Range" (telnet FUL, web 0) + 40..250; Front forced to Full Range
  without subwoofer; others Full Range only if Front is. Legacy: "Full Band" shown (not settable) for
  Large speakers in LFE mode -> map in 5c as a read-only state, never as a write.
- LPF: legacy LPF for LFE 80..250; Web Control adds "Bass Extraction LPF" (40..250 incl. 70, only for
  Full Range channels) next to LPF for LFE -> two settings; value lists from the device.
- Pre-amp: Web Control "Preamplifier" (SpeakerLayout mode 17): every Connection is forced to
  "Pre-out Only". Legacy: the EU manual does not list Pre Amplifier; the device-reported options decide
  (silence is not absence). Owner: speaker outputs and pre-outs are separate, so pre-amp is not a
  safety issue; the amp-assign select stays writable.
- Speaker Preset 1/2 (Web Control, SPPR): stores the WHOLE speaker setup (layout, distances, levels,
  crossovers, subwoofer output, LPF, distribution, front speaker, Audyssey/Dirac/Graphic EQ).
  -> 5c/6: a preset switch must re-read everything speaker-related (telnet speaker_channel queries +
  speakers web + graphic EQ + distances/levels); SPPR select via telnet.
- Adding/removing a speaker after Audyssey disables MultEQ XT32 / Dynamic EQ / Dynamic Volume
  (X3600H manual) -> every speaker-setup write in HA carries that warning (5f).
- Web Control lists a Setup Menu; Audyssey measurement only on the on-screen menu.
- From the 5c verification: catalogue_entities corrects only the unique_id; also check sub_device /
  enabled_default of the created entity against the entry (5d). The SSSPCSWM/SWL token maps (2LR,
  Directional token) must be verified live before they are fixed in the profile.
