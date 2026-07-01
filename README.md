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
  (`Deviceinfo.xml`) and a periodic reconciliation poll.
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

## What you get

- **Media player** per zone (main + zone 2 where present): power, volume, mute,
  source selection, and (main zone) sound mode.
- **Sound mode** as two coupled selects: a genre group (Movie/Music/Game/Pure)
  and the modes within the active group (context aware, includes Auto).
- **Selects**: Dynamic Compression, Dynamic Volume, Reference Level Offset,
  MultEQ, Restorer, ECO, Front Display dimmer, Video Mode, HDMI Monitor Out,
  Aspect Ratio, Input Mode (ARC/eARC/…), Quick Select — each only when the
  receiver advertises it.
- **Switches**: Tone Control, Dynamic EQ, Loudness Management, Cinema EQ,
  Subwoofer, Speaker Virtualizer, Center Spread, DTS Neural:X, Calibration LFC.
- **Numbers**: Bass, Treble, Subwoofer Level, LFE, Dialog Control, Audio Delay,
  Effect Level, Containment Amount, Sleep Timer, and a per channel volume trim
  for each configured speaker.
- **Sensors** (diagnostic): sample rate, decoder, audio format, input signal,
  mode info, sound mode, volume (dB).
- **Binary sensor**: telnet connectivity.

Per channel trims for speakers the receiver has **not** configured are registered
but disabled by default; enable them from the entity settings if you need them.

## Notes and limitations

- Some parameters only apply to certain signals or sound modes (for example
  Dynamic Compression needs a Dolby/DTS bitstream, Video Mode needs an active
  video signal). The receiver rejects changes that do not apply to the current
  context; this is expected Denon behaviour, not an integration bug.
- Advanced one time setup settings that the receiver exposes only through its web
  UI's `/ajax` configuration API (speaker layout/crossovers/distances, HDMI
  Control/CEC, per input assignment, some zone defaults) are not implemented.

## Credits

Protocol behaviour was verified live against an AVR-X3600H. Inspired by the
author's earlier C# tooling and by existing Denon control projects, but written
from scratch as an independent, fully dynamic integration.
