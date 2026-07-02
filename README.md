# Denon AVR (local, dynamic)

A local Home Assistant integration for Denon (HEOS generation) AV receivers such
as the AVR-X3600H. It talks to the receiver directly over the network and adapts
itself to whatever the connected receiver reports — nothing about the device is
hardcoded.

## Design principles

- **Own, dependency free client.** No external PyPI dependency; the receiver
  library lives in the `avr/` subpackage (telnet + HTTP), usable standalone.
- **Everything discovered from the device.** Model name, MAC, zones, input
  sources (and their renamed names + visibility), speaker configuration and
  channels, sound modes and their genre groups, quick select names, feature
  availability, control ranges and option labels are all read from the receiver
  at runtime. The only fixed data is the Denon wire grammar, kept in the external
  `avr/protocol_profile.json` (protocol tokens, not device configuration).
- **Telnet first, with HTTP as a safety net.** Telnet (port 23) provides the
  full state and real time push; HTTP (port 8080) provides discovery
  (`Deviceinfo.xml`) and a periodic reconciliation poll. Two non-disruptive reads
  at discovery fetch what the control channels do not expose: the amp assignment
  over the length-framed TCP channel (port 1256), and the selectable crossover
  set over the HTTPS web config (port 10443). All control goes over telnet.
- **Stability first.** Auto reconnect with backoff, a keepalive probe, a command
  queue with inter command spacing, and clean teardown on unload.

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

| Port | Proto | Purpose | Used by |
|------|-------|---------|---------|
| **23/tcp** | Telnet | Primary control channel and real‑time push: power, volume, mute, source, sound modes and all state updates | **Required** |
| **8080/tcp** | HTTP (goform) | Discovery (`Deviceinfo.xml`) and the reconciliation poll (`…StatusLite.xml`) | **Required** |
| **60006/tcp** | HTTP (UPnP/AIOS) | Device description read once at setup for firmware version and serial number | Optional (degrades gracefully) |
| **1900/udp** | SSDP (multicast) | Automatic discovery of the receiver on the LAN | Optional (auto‑discovery only) |
| **10443/tcp** | HTTPS (`/ajax/*`) | Read once at discovery for the selectable crossover set (the only channel that exposes it); the wider settings write API is not used | Optional (degrades gracefully) |
| **1256/tcp** | Length‑framed JSON | Read once at setup for the amp assignment (a plain, non‑disruptive status read); room‑correction calibration writes are planned | Optional (degrades gracefully) |

Notes:

- Port **1255/tcp** (HEOS) is intentionally **not** used by this integration.
- The receiver accepts **multiple concurrent telnet (23) connections** and
  broadcasts events to all of them, so this integration coexists with the Denon
  app and other controllers.
- Port **10443** is read once at discovery for the selectable crossover set —
  the only channel that exposes it; only that read is used, not the write API.
- Port **1256** is read once at setup for the amp assignment (a plain,
  non-disruptive status query, no calibration session). Room-correction
  calibration writes over the same port are planned — open it ahead of time if
  you want that functionality once it lands.

## What you get

- **Media player** per zone (main + zone 2 where present): power, volume, mute,
  source selection, and (main zone) sound mode.
- **Sound mode** as two coupled selects: a genre group (Movie/Music/Game/Pure)
  and the modes within the active group (context aware, includes Auto).
- **Selects**: Dynamic Compression, Dynamic Volume, Reference Level Offset,
  MultEQ, Restorer, ECO, Front Display dimmer, Video Mode, HDMI Monitor Out,
  HDMI Audio Out (Amp/TV), HDMI Resolution, Aspect Ratio, Input Mode
  (ARC/eARC/…), Quick Select, Subwoofer Mode
  (LFE / LFE+Main), a per speaker group crossover frequency (Hz), and a per
  speaker group size (Large/Small) — each only when the receiver advertises it.
- **Switches**: Tone Control, Dynamic EQ, Loudness Management, Cinema EQ,
  Subwoofer, Speaker Virtualizer, Center Spread, DTS Neural:X, Low Frequency
  Containment (LFC), All Zone Stereo.
- **Numbers**: Bass, Treble, Subwoofer Level, LFE, Dialog Control, Audio Delay,
  Effect Level, Containment Amount, Sleep Timer, and per channel volume trim and
  speaker distance (m) for each configured speaker.
- **Sensors** (diagnostic): sample rate, decoder, audio format, input signal,
  mode info, sound mode, volume (dB), and the current amp assignment.
- **Binary sensor**: telnet connectivity.

Per channel trims and distances, and per group crossovers and sizes, for
speakers the receiver has **not** configured are registered but disabled by
default; enable them from the entity settings if you need them.

Speaker crossover frequencies are a discrete, non-uniform set, so they are
exposed as a select rather than a stepped number. The current crossover per group
is read over telnet (`SSCFR`); the selectable frequencies come from the receiver's
own web config (the only channel that exposes them), with a verified protocol set
as a fallback.

## Notes and limitations

- Some parameters only apply to certain signals or sound modes (for example
  Dynamic Compression needs a Dolby/DTS bitstream, Video Mode needs an active
  video signal). The receiver rejects changes that do not apply to the current
  context; this is expected Denon behaviour, not an integration bug.
- Speaker distances and crossovers are supported over telnet (read/write). Other
  advanced one time setup settings that the receiver exposes only through its web
  UI's `/ajax` configuration API (full speaker layout, HDMI Control/CEC, per
  input assignment, some zone defaults) are not implemented. Room correction
  calibration remains a job for the receiver's own setup tooling.

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

Inspired by the author's earlier C# tooling and by existing Denon control
projects, but written from scratch as an independent, fully dynamic integration.

For the official, first‑party Home Assistant integration for these receivers,
see **Denon AVR Network Receivers**:
<https://www.home-assistant.io/integrations/denonavr/>.

With thanks to **Denon** for publicly documenting the AV Receiver control
protocol, which made this independent, accurate implementation possible. Denon's
official documentation is available at <https://manuals.denon.com/>.

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
