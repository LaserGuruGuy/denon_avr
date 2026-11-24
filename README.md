# Denon AVR

[![GitHub Release][releases-shield]][releases]
[![Maintainer][maintainer-shield]][maintainer]
[![HACS Custom][hacs-shield]][hacs-url]

This is a HACS custom integration for Denon (HEOS generation) AV receivers such
as the AVR-X3600H and AVC-X3900H.


## Features

- **Own, dependency free client.** Minimal external dependency.
- **Everything discovered from the device.** Model name, MAC, zones, input
  sources (and their renamed names + visibility), speaker configuration and
  channels, sound modes and their genre groups, quick select names, feature
  availability, control ranges and option labels are all read from the receiver
  at runtime. The only fixed data is the Denon wire grammar.
- **Telnet first.** Telnet (port 23) provides the full state, real time push and
  the bulk of control; HTTP (port 8080) provides discovery and a periodic
  reconciliation poll. Settings that have no telnet command (graphic‑EQ bands,
  HDMI/OSD/format setup, per input settings, amp assign or speaker layout) are
  read and written through the receiver's own setup web interface.
- **Two generations of the setup interface.** HTTPS on port 10443 (seen on the
  AVR‑X3600H) and the newer "Web Control" over plain HTTP on port 11080 (seen on
  the AVC‑X3900H). The interface is found automatically and what it offers is read
  from it; nothing is tied to a model name.
- **Follows your setup live.** Speakers, subwoofers, presets and inputs belong to
  your own setup. Entities appear or become unavailable with it, and changes made
  on the receiver show up right away (settings that only exist in the setup web
  interface: within a minute).
- **Stability** Auto reconnect with backoff, keepalive probe, command queue
  with inter command spacing, teardown on unload.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=LaserGuruGuy&repository=denon_avr&category=integration)

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. HACS → Integrations → three dots → Custom repositories.
3. Add this repository as a [custom integration repository](https://hacs.xyz/docs/faq/custom_repositories), category "Integration".
4. Install integration "Denon AVR".
5. Restart Home Assistant.

### Manual
Copy the `denon_avr` folder into `<config>/custom_components/` and restart Home Assistant.

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
| **23/tcp** | Telnet | Primary control channel and real‑time push: power, volume, mute, source, sound modes, speaker setup and all state updates | **Required** |
| **8080/tcp** | HTTP (goform) | Discovery (`Deviceinfo.xml`) and the reconciliation poll (`…StatusLite.xml`) | **Required** |
| **1900/udp** | SSDP (multicast) | Automatic discovery of the receiver on the LAN (Home Assistant's own SSDP finds Denon and Marantz receivers and offers them; without it, add the receiver by IP address) | Optional (auto‑discovery only) |
| **60006/tcp** | HTTP (UPnP/AIOS) | Device description read once at setup for firmware version and serial number | Optional (degrades gracefully) |
| **1255/tcp** | HEOS CLI | Now‑playing media, album art and transport (play/pause/next) for network sources | Optional (degrades gracefully) |
| **1256/tcp** | Length‑framed JSON | Not currently used (reserved for future use) | Unused |
| **80/tcp, 443/tcp** | HTTP/HTTPS | Only to find the setup web interface below: the receiver redirects to it, and the redirect is followed on the same host | Optional (10443 and 11080 are tried directly otherwise) |
| **10443/tcp** | HTTPS (setup config) | Setup web interface of the older generation (seen on the AVR‑X3600H): graphic‑EQ per‑band values, Video setup menu (HDMI setup/CEC, ARC, OSD, TV/4K format) and Amp Assign: settings with no telnet token, read/write (self‑signed certificate) | Optional (graphic EQ, video, speakers) |
| **11080/tcp** | HTTP (setup config) | The same for the Web Control generation (seen on the AVC‑X3900H), plus per input 4K/8K format and HDCP, speaker layout and the lists of crossover and LFE frequencies | Optional (graphic EQ, video, inputs, speakers) |

Without the setup web interface everything on telnet keeps working; the web based
entities are unavailable or absent.

Notes:

- The receiver accepts **multiple concurrent telnet (23) connections** and
  broadcasts events to all of them, so this integration coexists with the Denon
  app and other controllers.
- The HEOS (1255) and setup web interface channels are optional and do not affect
  control.
- On the Web Control generation the setup interface is **plain HTTP without login**,
  so keep the receiver on a trusted network.

## What you get

Settings are organized into **sub‑devices that mirror the receiver's own setup
menus**, so a large control set stays tidy instead of crowding one page. The main
receiver device keeps the day‑to‑day controls (player, power, source, sound mode,
quick select, input mode, front‑display dimmer); the **Audio**, **Video**,
**Picture**, **Speakers**, **Inputs** and **Graphic EQ** sub‑devices each carry
their own part of the setup. Names are translated (English and Dutch). **Picture** holds the image‑quality tuning (a coherent group,
split off from Video like the Graphic EQ is split off from Audio); **Video** holds
the output/connectivity settings (video mode, HDMI setup, on‑screen display, TV/4K
signal format). **Inputs** holds the settings per input. Which of the entities below
you get depends on what your receiver and your setup report. The lists are grouped by entity type; each entity lands on
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
  Scale, Volume Limit, Muting Level, a per speaker group crossover frequency (Hz)
  plus a Crossover Speaker Selection (All / Individual) that gates them against a
  single All Crossover,
  a per speaker group size (Large/Small, where the receiver reports a size), number
  of subwoofers, Front Speaker (A/B/A+B) and LPF for LFE. Depending on the
  generation: **Amp Assign** with Floor Layout and Height Speakers, or **Speaker
  Layout** (see below). **Speaker Preset** 1/2 where the receiver has it. Options
  such as crossover frequencies come from the receiver where it offers them.
  On **Video**: HDMI Power‑Off Control,
  Pass‑Through Source, RC Select, On‑Screen Volume position, Now‑Playing display,
  4K Signal Format (for all inputs, older generation), TV Format (NTSC/PAL), and the
  format and HDCP of the current input (Web Control generation). On **Inputs**: 4K/8K
  Signal Format and HDCP per input (Web Control generation). On **Picture**: Noise Reduction. Each
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
  curve, and **Default** resets the channel to flat. See the equalizer card
  recipe below.
- **Speaker layout** (Web Control generation): choosing a layout only prepares it;
  **Apply** writes it. Applying also resets other choices on the receiver (surround
  back, height speakers, front A/B); a repair notice then lists what changed and
  **Restore** puts it back as it was.
- **Sensors** (diagnostic): sample rate, decoder, audio format, input signal (named
  where the code is known, else `code_<nn>`),
  mode info, sound mode, volume (dB), amplifier mode, effect speakers, the
  subwoofer directional mode and layout (with two or more subwoofers), video and HDR
  in and out (as the receiver words it; `---` = none; HDR values not tested on
  hardware), the active input channels and speakers (e.g. 5.1.4), and how each
  speaker position is connected (Web Control generation).
- **Binary sensor**: telnet connectivity.

Per channel trims and distances, and per group crossovers and sizes, for
speakers the receiver has **not** configured are registered but disabled by
default; enable them from the entity settings if you need them.

Speaker crossover frequencies are a discrete, non-uniform set, so they are
exposed as a select rather than a stepped number. The current crossover per group
is read over telnet; the selectable frequencies come from the receiver where it
lists them (including Full Range), else from the fixed Denon crossover grid.

### Graphic equalizer card

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
(both are entities above). While the receiver grays its equalizer menu out, these
entities are unavailable. Entity ids follow your device and language: the example is
from an English AVR‑X3600H, so replace them with your own.

## Notes and limitations

- **Breaking change in 1.2.2:** the state of the input signal sensor is now a name
  (`pcm`, `dolby_digital`, ...) instead of the raw code (`02`, `03`, ...). Automations
  can use the attribute `code`, which keeps the raw code.
- The speaker layout restore point is kept in memory: a restart of Home Assistant or a
  reload of the integration drops it.
- Some parameters only apply to certain signals or sound modes (for example
  Dynamic Compression needs a Dolby/DTS bitstream, Video Mode needs an active
  video signal, Equalizer settings can not be altered when MultEQ is not Off).
  The receiver rejects changes that do not apply to the current
  context; this is expected Denon behavior, not an integration bug.
- Speaker distances and crossovers are supported over telnet (read/write). Other
  advanced one time setup settings that the receiver exposes only through its web
  UI's configuration API (custom speaker layouts, input assignment, some zone
  defaults) are not implemented.
- Room correction calibration remains a job for the receiver's own setup tooling.

## Testing

Protocol behavior was verified live against the following.

| Component                    | Version |
|------------------------------|---------|
| Receiver                     | Denon AVR-X3600H, AVC-X3900H |
| Receiver hardware generation | `avr-x-2016` |
| Receiver firmware            | AVR-X3600H 3.139.173, AVC-X3900H 3.139.175 |
| Home Assistant Core          | 2026.11.3 |
| Python                       | 3.14.10 |

Other Denon/Marantz HEOS generation receivers should work as the integration
adapts to whatever the device reports, but only these receivers have been
exercised end to end. Marantz receivers speak the same protocol and are discovered too, but
have not been tested on hardware.

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
  a Xperi company — <https://dts.com>
- **HDMI**, the HDMI logo, and **High‑Definition Multimedia Interface** are
  trademarks of HDMI Licensing Administrator, Inc. — <https://www.hdmi.org>
- **Home Assistant** and the Home Assistant logo are trademarks of the Open Home
  Foundation — <https://www.openhomefoundation.org>
- **HACS** (Home Assistant Community Store) — <https://hacs.xyz>

Any other product or company names mentioned are the trademarks of their
respective owners.

[releases-shield]: https://img.shields.io/github/v/release/LaserGuruGuy/denon_avr.svg?style=for-the-badge
[releases]: https://github.com/LaserGuruGuy/denon_avr/releases/
[maintainer-shield]: https://img.shields.io/badge/maintainer-LaserGuruGuy-blue.svg?style=for-the-badge
[maintainer]: https://github.com/LaserGuruGuy
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[hacs-url]: https://github.com/LaserGuruGuy/denon_avr
