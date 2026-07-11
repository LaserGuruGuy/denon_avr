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
