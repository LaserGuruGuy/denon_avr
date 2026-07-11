# Denon AVR

A local Home Assistant integration for Denon (HEOS generation) AV receivers such
as the AVR-X3600H. It talks to the receiver directly over the network and adapts
itself to what the connected receiver advertises — receiver capabilities are not
hardcoded.

## Design principles

- **Own, dependency free client.** Minimal external dependency.
- **Everything discovered from the device.** Model name, MAC, zones, input
  sources (and their renamed names + visibility), speaker configuration and
  channels, sound modes and their genre groups, quick select names, feature
  availability, control ranges and option labels are all read from the receiver
  at runtime. The only fixed data is the Denon wire grammar.
- **Telnet, HTTP** Telnet (port 23) provides the full state, real time push and
  the bulk of control; HTTP (port 8080) provides discovery and a periodic
  reconciliation poll. A few settings that have no telnet command (graphic‑EQ
  bands, HDMI/OSD/format setup, amp assign) are read and written over the setup
  config API (HTTPS 10443); everything else is telnet.
- **Stability** Auto reconnect with backoff, keepalive probe, command queue
  with inter command spacing, teardown on unload.

## Installation

### HACS (custom repository)
1. HACS → Integrations → three dots → Custom repositories.
2. Add this repository, category "Integration".
3. Install "Denon AVR" and restart Home Assistant.

### Manual
Copy the `denon_avr` folder into `<config>/custom_components/` and restart.

## Configuration

Settings → Devices & Services → Add Integration → **Denon AVR**, then enter the
receiver's **IP address**. The name and all capabilities are read from the device
automatically; no other input is required.

## Network ports and protocols

All traffic is on the local network and is initiated by Home Assistant (client)
towards the receiver (server). To let the two communicate through a router or
VLAN firewall, allow the Home Assistant host to reach the receiver on these
ports. Nothing needs to be opened towards the internet.

| Port | Protocol | Purpose | Ordinality |
|------|----------|---------|------------|
| **23/tcp** | Telnet | Primary control channel and real‑time push: power, volume, mute, source, sound modes and all state updates | **Required** |
| **8080/tcp** | HTTP (goform) | Discovery (`Deviceinfo.xml`) and the reconciliation poll (`…StatusLite.xml`) | **Required** |
| **60006/tcp** | HTTP (UPnP/AIOS) | Device description read once at setup for firmware version and serial number | Optional (degrades gracefully) |
| **1900/udp** | SSDP (multicast) | Automatic discovery of the receiver on the LAN | Optional (auto‑discovery only) |
| **1256/tcp** | Length‑framed JSON | Not currently used (reserved for a future Audyssey/calibration read path) | Unused |
| **1255/tcp** | HEOS CLI | Now‑playing media, album art and transport (play/pause/next) for network sources | Optional (degrades gracefully) |
| **10443/tcp** | HTTPS (setup config) | Graphic‑EQ per‑band values, the Video setup menu (HDMI setup/CEC, ARC, OSD, TV/4K format) and Amp Assign — settings with no telnet token, read/write (non‑disruptive; self‑signed cert) | Optional (graphic EQ + video + amp assign) |

Notes:

- The receiver accepts **multiple concurrent telnet (23) connections** and
  broadcasts events to all of them, so this integration coexists with the Denon
  app and other controllers.
- The HEOS (1255) and setup‑config (10443) channels are only opened when the
  receiver advertises the matching capability; both are non‑disruptive.

## What you get

Settings are organised into **sub‑devices that mirror the receiver's own setup
menus**, so a large control set stays tidy instead of crowding one page. The main
receiver device keeps the day‑to‑day controls (player, power, source, sound mode,
quick select, input mode, front‑display dimmer); the **Audio**, **Video**,
**Picture**, **Speakers** and **Graphic EQ** sub‑devices each carry their own
part of the setup. **Picture** holds the image‑quality tuning (a coherent group,
split off from Video like the Graphic EQ is split off from Audio); **Video** holds
the output/connectivity settings (video mode, HDMI setup, on‑screen display, TV/4K
signal format). The lists below are grouped by entity type; each entity lands on
the sub‑device its setting belongs to.

- **Media player** per zone (main + zone 2 where present): power, volume, mute,
  source selection, and (main zone) sound mode. On the main zone, network sources
  show **album art, track/artist/album and play/pause/next transport** (via HEOS);
  otherwise the player carries a source‑aware **input icon** (TV, disc, tuner,
  Bluetooth, USB, cast, …).
- **Sound mode** as two coupled selects: a genre group (Movie/Music/Game/Pure)
  and the modes within the active group (context aware, includes Auto).
- **Selects**: Dynamic Compression, Dynamic Volume, Reference Level Offset,
  MultEQ, Restorer, ECO, Front Display dimmer, Video Mode, Picture Mode, HDMI
  Monitor Out, HDMI Audio Out (Amp/TV), HDMI Resolution, Aspect Ratio, Input Mode
  (ARC/eARC/…), Quick Select, Subwoofer Mode (LFE / LFE+Main), Room Size, Volume
  Scale, Volume Limit, Muting Level, a per speaker group crossover frequency (Hz),
  a per speaker group size (Large/Small), Front Speaker (A/B/A+B), and Amp Assign
  (the amp‑assignment mode — options are read from the receiver per its amp type,
  e.g. 7.1ch + Front B / 9.1ch / Bi‑Amp / Zone2). On **Video**: HDMI Power‑Off Control,
  Pass‑Through Source, RC Select, On‑Screen Volume position, Now‑Playing display,
  4K Signal Format, TV Format (NTSC/PAL). On **Picture**: Noise Reduction. Each
  only when the receiver advertises it.
- **Switches**: Main Power, Main Mute, Tone Control, Dynamic EQ, Loudness
  Management, Cinema EQ, Subwoofer, Speaker Virtualizer, Center Spread,
  DTS Neural:X, Low Frequency Containment (LFC), Graphic EQ, Auto Lip Sync,
  All Zone Stereo, and — on **Video** — HDMI Control (CEC), ARC/eARC, TV Audio
  Switching, HDMI Power Saving, Smart Menu, HDMI Pass‑Through, On‑Screen Info.
- **Numbers**: Bass, Treble, Subwoofer Level, LFE, Dialog Control, Center Level
  Adjust, Audio Delay, Effect Level, Containment Amount, Sleep Timer, per channel
  volume trim and speaker distance (m) for each configured speaker, and — on
  **Picture** — Contrast, Brightness, Saturation, Enhancer.
- **Graphic EQ** (a sub‑device, when the receiver has one): the on/off, a
  speaker‑selection mode (L/R · Each · All), the channel being adjusted, and one
  slider per band (63 Hz … 16 kHz). Selecting a channel loads that channel's own
  curve; the sliders **stage** it and **Apply** writes the whole band block to
  the receiver at once. **Copy Curve** seeds the manual EQ from the reference
  curve, and **Default** resets the channel to flat. See the equaliser card
  recipe below.
- **Sensors** (diagnostic): sample rate, decoder, audio format, input signal,
  mode info, sound mode, and volume (dB).
- **Binary sensor**: telnet connectivity.

Per channel trims and distances, and per group crossovers and sizes, for
speakers the receiver has **not** configured are registered but disabled by
default; enable them from the entity settings if you need them.

Speaker crossover frequencies are a discrete, non-uniform set, so they are
exposed as a select rather than a stepped number. The current crossover per group
is read over telnet; the selectable frequencies are the fixed Denon
crossover grid (a protocol constant, verified live), the same as every other
enum's values.

### Graphic equaliser card

The graphic EQ is exposed as plain entities (one number per band, plus the
speaker‑selection and channel selects), so any card can drive it. For a proper
fader look, this integration does **not** ship a card (an integration must not);
instead install a mixer/fader card from HACS — for example **`wrodie/mixer-card`**
— and point it at the band numbers. A minimal Lovelace example:

```yaml
type: vertical-stack
cards:
  - type: entities
    entities:
      - switch.avr_x3600h_graphic_eq
      - select.avr_x3600h_speaker_selection
      - select.avr_x3600h_eq_channel
      - button.avr_x3600h_apply
      - button.avr_x3600h_copy_curve
      - button.avr_x3600h_default
  - type: custom:mixer-card
    faders:
      - entity_id: number.avr_x3600h_eq_63_hz
      - entity_id: number.avr_x3600h_eq_125_hz
      - entity_id: number.avr_x3600h_eq_250_hz
      - entity_id: number.avr_x3600h_eq_500_hz
      - entity_id: number.avr_x3600h_eq_1_khz
      - entity_id: number.avr_x3600h_eq_2_khz
      - entity_id: number.avr_x3600h_eq_4_khz
      - entity_id: number.avr_x3600h_eq_8_khz
      - entity_id: number.avr_x3600h_eq_16_khz
```

Pick a channel, adjust the nine faders, then press **Apply** to write that
channel's curve. Editing requires MultEQ **off** and the graphic EQ **enabled**
(both are entities above). Entity ids follow your device name — adjust the prefix
to match yours.

## Notes and limitations

- Some parameters only apply to certain signals or sound modes (for example
  Dynamic Compression needs a Dolby/DTS bitstream, Video Mode needs an active
  video signal). The receiver rejects changes that do not apply to the current
  context; this is expected Denon behaviour, not an integration bug.
- Speaker distances and crossovers are supported over telnet (read/write). Other
  advanced one time setup settings that the receiver exposes only through its web
  UI's configuration API (full speaker layout, HDMI Control/CEC, per input
  assignment, some zone defaults) are not implemented.
- Room correction calibration remains a job for the receiver's own setup tooling.

## Tested with

Protocol behaviour was verified live against the following. Other Denon/Marantz
HEOS generation receivers should work as the integration adapts to whatever the
device reports, but only this combination has been exercised end to end.

| Component | Version |
|-----------|---------|
| Receiver | Denon AVR-X3600H (hardware generation `avr-x-2016`) |
| Receiver firmware | 3.88.614 |
| Home Assistant Core | 2026.7.0 |
| Python | 3.13 |

## Credits

With thanks to **Denon** for publicly documenting the AV Receiver control
protocol, which made this independent implementation possible. Denon's
official documentation is available at <https://manuals.denon.com/>.

For the official, first‑party Home Assistant integration for these receivers,
see **Denon AVR Network Receivers**:
<https://www.home-assistant.io/integrations/denonavr/>.

## Trademarks and acknowledgements

This is an independent, unofficial project. It is **not affiliated with, endorsed
by, sponsored by, or supported by** any of the companies below. All product and
company names, logos, and brands are the property of their respective owners and
are used here for identification purposes only (nominative use); their use does
not imply any affiliation or endorsement.

- **Denon**, **Marantz**, and **HEOS** are trademarks of D&M Holdings Inc. —
  <https://www.denon.com>
- **Audyssey**, **Audyssey MultEQ**, **MultEQ XT32**, **Dynamic EQ**,
  **Dynamic Volume**, and **LFC (Low Frequency Containment)** are trademarks of
  Audyssey Laboratories, Inc. — <https://audyssey.com>
- **Dolby**, **Dolby Audio**, **Dolby Surround**, and **Dolby Atmos** are
  trademarks of Dolby Laboratories, Inc. — <https://www.dolby.com>
- **DTS**, **DTS Neural:X**, and **DTS Virtual:X** are trademarks of DTS, Inc.,
  an Xperi company — <https://dts.com>
- **HDMI**, the HDMI logo, and **High‑Definition Multimedia Interface** are
  trademarks of HDMI Licensing Administrator, Inc. — <https://www.hdmi.org>
- **Home Assistant** and the Home Assistant logo are trademarks of the Open Home
  Foundation — <https://www.openhomefoundation.org>
- **HACS** (Home Assistant Community Store) — <https://hacs.xyz>

Any other product or company names mentioned are the trademarks of their
respective owners.
