# Denon / Marantz AVR control protocol — superset reference

A synthesised, model-annotated reference of the Denon/Marantz telnet (and a few
HTTP/HEOS) control commands, used as the ground truth when extending this
integration's protocol profile (`custom_components/denon_avr/avr/protocol_profile.json`).

It lists the **superset** of commands across recent AV receiver generations. The
integration only ever *populates* an entity when the specific connected receiver
advertises the matching capability (`FuncName` in `/goform/Deviceinfo.xml`), so a
command listed here that a given model lacks simply produces no entity on that
model. See the profile/parser for how a control is added: usually a single
profile entry, gated by its `FuncName`.

## Sources (ground truth, in priority order)

1. **The manufacturer's published control-protocol document (2017 generation)**
   — application models AVR-X6400H / X4400H / X3400H / X2400H / X1400H / S930H /
   S730H. The most complete first-party grammar available (832 distinct command
   tokens).
2. **The receiver itself** — live telnet / HTTP / HEOS probing of the target
   device (this project's reference unit is an **AVR-X3600H**, 2019). Ground
   truth for that model's exact tokens, values and quirks. Note: an empty telnet
   reply to a `?` query does **not** prove a command is unsupported (a no-op set
   or an inapplicable state also yields silence) — cross-check before concluding.
3. **A widely-used community control library** — real-world telnet command
   templates validated across many newer models (2020–2023). Source of commands
   newer than the 2017 document (e.g. `BTTX`, `PSDIRAC`, `PSDACFIL`, `PSIMAX*`,
   `SSHOSALS`).
4. **Another community integration** — cross-check only; its command set is a
   list of symbolic one-shot "simple commands" mapped onto the same tokens.

Newer models (X3800H / X3900H / X6800H) publish only owner's manuals, not a
public control-protocol document, so their additions are captured here via
sources 2–4 and annotated by generation rather than by a per-model document.

Command tokens are functional facts (the wire protocol), documented here in the
project's own words.

## Coverage legend

- `[x]` implemented as a stateful entity in this integration
- `[~]` partially covered / read-only / derived
- `[ ]` known token, not implemented yet (candidate)
- `[action]` one-shot command (fits a future button/remote surface, not a stateful entity)
- `gate:` the `FuncName` that must be advertised for the entity to appear

Conventions in the tables: `<CR>` line terminator omitted; `?` = status query;
`UP`/`DOWN` = relative step; numeric values are the raw wire form (see the
profile for scaling, e.g. master volume `MV` uses 2-digit + optional half-step).

---

## 1. Power

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `PW` | System (all-zone) power | `ON`, `STANDBY`, `?` | `[x]` system_power |
| `ZM` | Main zone power | `ON`, `OFF`, `?` | `[x]` main_power |
| `Z2` / `Z3` (power) | Zone 2/3 power | `ON`, `OFF`, `?` | `[x]` per-zone media player |

## 2. Master volume & mute

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `MV` | Master volume | `00`–`99` (+`5` half-step), `UP`, `DOWN`, `?` | `[x]` main_volume |
| `MVMAX` | Volume ceiling (reported) | `nn` | `[x]` used for scaling |
| `MU` | Main mute | `ON`, `OFF`, `?` | `[x]` main_mute |
| `SSVCTZMADIS` | Volume display scale | `REL`, `ABS` | `[x]` volume_scale |
| `SSVCTZMALIM` | Volume limit | `OFF`,`060`,`070`,`080` | `[x]` volume_limit |
| `SSVCTZMAMLV` | Muting level | `MUT`,`040` | `[x]` muting_level |
| `SSVCTZMAPON` | Power-on level | `LAST`,`MUT`,`nnn` | `[ ]` deferred (mixed type) |

## 3. Source & input

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `SI` | Main source select | `TV`,`BD`,`CD`,`DVD`,`GAME`,`SAT/CBL`,`MPLAY`,`NET`,`BT`,`TUNER`,`PHONO`,`AUX1..7`,`IPD`,`USB/IPOD`,`SERVER`,`FAVORITES`,... | `[x]` main_source |
| `SV` | Video source assign | same set + `ON`/`OFF` | `[x]` video_select |
| `SD` | Input mode (digital/analog/HDMI/ARC) | `AUTO`,`HDMI`,`DIGITAL`,`ANALOG`,`ARC`,`NO`,`7.1IN` | `[x]` input_mode |
| `SSFUN` | Source rename (reported) | `<code> <name>` | `[~]` names discovered |
| `SSSOD` | Source delete/visibility | `<code> USE`/`DEL` | `[~]` visibility discovered |

## 4. Sound mode (`MS`)

Selectable modes are **discovered** from the receiver (`OPSML`/`OPSMLALL`), because
the wire token differs from the display name per mode; the profile only holds the
few wire overrides. The superset of documented `MS` tokens:

`MSMOVIE`, `MSMUSIC`, `MSGAME`, `MSDIRECT`, `MSPURE DIRECT`, `MSSTEREO`,
`MSAUTO`, `MSDOLBY <...>` (Atmos, D EX, D+, Surround, ...), `MSDTS <...>`
(Neural:X, ES ..., Virtual:X), `MSMCH STEREO`, `MSMULTI CH IN[ 7.1]`,
`MSNEURAL:X`, `MSVIRTUAL`, `MSMONO MOVIE`, `MSROCK ARENA`, `MSJAZZ CLUB`,
`MSMATRIX`, `MSVIDEO GAME`, `MSCLASSIC CONCERT`, `MSSUPER STADIUM`,
`MSWIDE SCREEN`, `MSAURO3D`, `MSAURO2DSURR`, `MSLEFT`, `MSRIGHT`, `MS7.1IN`,
legacy `MSPL2*`/`MSPL2X*`/`MSNEO:6*`/`MSDSD*`/`MSAUDYSSEY DSX`.

| Token | Function | Coverage |
|-------|----------|----------|
| `MS` | Sound mode select / `?` | `[x]` sound_mode (+ media player), list discovered |
| `MSALL ZONE STEREO` / `MNZST` | All-zone stereo | `[x]` all_zone_stereo (state derived from `MS`) |
| `MSQUICK0..5`, `MSQUICK ?`, `MSQUICKn MEMORY` | Quick select recall/store | `[~]` quick_select (recall); store `[action]` |
| `MSSMG` | Sound-mode genre group (reported MOV/MUS/GAM/PUR) | `[~]` sound_mode_genre |

## 5. Surround parameters (`PS…`)

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `PSNEURAL` | DTS Neural:X | `ON`/`OFF` | `[x]` dts_neural_x · gate: DTSNeuralX |
| `PSCES` | Center spread | `ON`/`OFF` | `[x]` center_spread · gate: CenterSpread |
| `PSSPV` | Speaker virtualizer | `ON`/`OFF` | `[x]` speaker_virtualizer · gate: SpeakerVirtualizer |
| `PSEFF` | Effect level | `nn`,`UP`,`DOWN` | `[x]` effect_level · gate: EffectLevel |
| `PSDIC` | Dialog control | `nn` | `[x]` dialog_control · gate: DialogControl |
| `PSDIL` | Dialog/center level adjust | `nn`,`UP`,`DOWN` | `[ ]` candidate · gate: DialogLevel |
| `PSDEH` | Dialog enhancer | `OFF`/`LOW`/`MED`/`HIGH` | `[ ]` candidate |
| `PSBSC` | Bass sync | `nn` | `[ ]` candidate |
| `PSSTW` / `PSSTH` | Stage width / height (DSX) | `nn` | `[ ]` |
| `PSDSX` | Audyssey DSX | `OFF`/`ONH`/`ONW`/... | `[ ]` (DSX; not on Atmos-era units) |
| `PSAUROPR` / `PSAUROST` | Auro-3D preset / strength | `SML/MED/LAR` / `nn` | `[ ]` gate: (Auro-3D models) |
| `PSSP:` | Effect speaker selection | `FL`,`HF`,... | `[ ]` |

## 6. Audyssey (`PS…`)

The receiver's `Audyssey` block nests exactly these; all implemented:

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `PSMULTEQ:` | MultEQ curve | `AUDYSSEY`,`BYP.LR`,`FLAT`,`OFF` | `[x]` multeq · gate: MultEq |
| `PSDYNEQ` | Dynamic EQ | `ON`/`OFF` | `[x]` dynamic_eq · gate: DynamicEq |
| `PSDYNVOL` | Dynamic Volume | `LIT`/`MED`/`HEV`/`OFF` | `[x]` dynamic_volume · gate: DynamicVolume |
| `PSREFLEV` | Reference level offset | `0`/`5`/`10`/`15` | `[x]` reference_level_offset · gate: RefLevOffset |
| `PSLFC` | Low-frequency containment | `ON`/`OFF` | `[x]` low_frequency_containment · gate: AudysseyLfc |
| `PSCNTAMT` | Containment amount | `01`–`07` | `[x]` containment_amount · gate: ContainmentAmount |
| `PSRSZ` | Audyssey DSX room size | `S`/`MS`/`M`/`ML`/`L` | `[ ]` candidate · gate: RoomSize |

## 7. Tone (`PS…`)

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `PSTONE CTRL` | Tone control enable | `ON`/`OFF` | `[x]` tone_control · gate: ToneControl |
| `PSBAS` | Bass | `nn`,`UP`,`DOWN` | `[x]` bass · gate: Bass |
| `PSTRE` | Treble | `nn`,`UP`,`DOWN` | `[x]` treble · gate: Treble |
| `PSLOM` | Loudness management | `ON`/`OFF` | `[x]` loudness_management · gate: Loudness |

## 8. Audio (`PS…`)

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `PSDRC` | Dynamic range compression | `OFF`/`LOW`/`MID`/`HI`/`AUTO` | `[x]` dynamic_compression · gate: DynamicCompression |
| `PSLFE` | LFE level | `nn` (0..-10) | `[x]` lfe · gate: LFE |
| `PSRSTR` | Audio Restorer | `OFF`/`LOW`/`MED`/`HI` | `[x]` restorer · gate: Restorer |
| `PSDELAY` | Audio delay (ms) | `nnn`,`UP`,`DOWN` | `[x]` audio_delay · gate: AudioDelay |
| `PSDEL` | Delay time (alt) | `nnn` | `[~]` overlaps PSDELAY |
| `PSCINEMA EQ.` | Cinema EQ | `ON`/`OFF` | `[x]` cinema_eq · gate: CinemaEq |
| `PSLFL` | LFE low-pass filter | `00`..`10` (→ Hz) | `[ ]` candidate · gate: LowPassFilter |
| `PSGEQ` | Graphic EQ enable | `ON`/`OFF` | `[x]` graphic_eq · gate: GraphicEQ |
| `PSHEQ` | Headphone EQ | `ON`/`OFF` | `[ ]` candidate |
| `PSDACFIL` | DAC filter | `1`/`2` | `[ ]` (models with a DAC filter) |
| `PSDIRAC` | Dirac Live filter slot | `1`/`2`/`3`/`OFF` | `[ ]` gate: (Dirac models; **not** on X3600H) |
| `PSIMAXAUD` / `PSIMAX` / `PSIMAXHPF` / `PSIMAXLPF` / `PSIMAXSWM` / `PSIMAXSWO` | IMAX audio settings | various | `[ ]` gate: IMAXAudioSettings |

## 9. Speaker & channel levels

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `CV<ch>` | Per-channel trim | `FL`,`FR`,`C`,`SW`,`SW2`,`SL`,`SR`,`SBL`,`SBR`,`SB`,`FHL/R`,`FWL/R`,`TFL/R`,`TML/R`,`TRL/R`,`RHL/R`,`SHL/R`,`TS` · `nn`,`UP`,`DOWN` | `[x]` channel_level (trims) · gate: ChannelLevel |
| `CVZRL` | Reset all channel trims | (action) | `[action]` |
| `PSSWL` / `PSSWL2` | Subwoofer level 1/2 | `nn`,`UP`,`DOWN` | `[x]` subwoofer_level (1) |
| `PSSWR` | Subwoofer on/off | `ON`/`OFF` | `[x]` subwoofer_switch |
| `SSSWM` | Subwoofer mode | `LFE`/`L+M` | `[x]` subwoofer_mode |
| `PSFRONT` | Front speaker A/B | `SPA`/`SPB`/`A+B` | `[ ]` gate: SpeakerAB/SpeakerSelect |
| `SSLEV` / `SSSPC` / `SSSDE` / `SSCFR` | Calibration level / config / distance / crossover (SS setup family) | discovered | `[x]` per-group distance/size/crossover/trim (derived) |

> The `SS…` speaker-setup family (crossover `SSCFR`, config `SSSPC`, distance
> `SSSDE`, level `SSLEV`) is newer than the V01 document; this integration
> derives it live from the device. Values are grammar-fixed in the profile.

## 10. Video & picture (`VS…`, `PV…`)

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `VSMONI` | HDMI monitor output | `1`/`2`/`AUTO` | `[x]` hdmi_video_output · gate: HdmiVideoOut |
| `VSASP` | Aspect ratio | `NRM`/`FUL` | `[x]` aspect_ratio · gate: AspectRatio |
| `VSSC` | Resolution (scaler) | `48P/10I/10P/72P/10P24/4K/4KF/AUTO` | `[~]` see VSSCH |
| `VSSCH` | Resolution (HDMI) | as VSSC | `[x]` resolution_hdmi · gate: ResolutionHdmi |
| `VSVPM` | Video processing mode | `AUTO`/`GAME`/`MOVI`/`BYP` | `[x]` video_mode · gate: VideoMode |
| `VSAUDIO` | HDMI audio decode out | `AMP`/`TV` | `[x]` hdmi_audio_out · gate: HdmiAudioOut |
| `VSVST` | Vertical stretch | `ON`/`OFF` | `[ ]` |
| `PV` (mode) | Picture mode | `OFF/STD/MOV/VVD/STM/CTM/DAY/NGT` | `[x]` picture_mode (exact-enum) · gate: PictureMode |
| `PVCN`/`PVBR`/`PVST`/`PVDNR`/`PVENH` | Contrast/Brightness/Saturation/Noise-reduction/Enhancer | `nnn`,`UP`,`DOWN` | `[ ]` candidates (share `PV` prefix — need exact/anchored matching) |

## 11. Zones 2 & 3 (`Z2…`, `Z3…`)

Zone power/volume/mute/source use the bare `Z2`/`Z3` prefix (overloaded, the
parser disambiguates by content). Additional per-zone settings:

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `Z2`/`Z3` | Power / volume / source | `ON`/`OFF` · `00`–`98`/`UP`/`DOWN` · `<source>` | `[x]` per-zone media player |
| `Z2MU`/`Z3MU` | Zone mute | `ON`/`OFF` | `[x]` |
| `Z2PSBAS`/`Z2PSTRE` (+Z3) | Zone tone bass/treble | `nn` | `[ ]` candidate |
| `Z2CVFL`/`Z2CVFR` (+Z3) | Zone channel level L/R | `nn` | `[ ]` candidate |
| `Z2HPF`/`Z3HPF` | Zone high-pass filter | `ON`/`OFF` | `[ ]` candidate |
| `Z2CS`/`Z3CS` | Zone channel setting | `MONO`/`ST` | `[ ]` candidate |
| `Z2HDA`/`Z3HDA` | Zone HD audio | `PCM`/`THR` | `[ ]` |
| `Z2QUICKn`/`Z3QUICKn` | Zone quick select | recall / `MEMORY` | `[ ]` |
| `Z2SLP`/`Z3SLP` | Zone sleep | `nnn`/`OFF` | `[ ]` |
| `Z2STBY`/`Z3STBY` | Zone auto-standby | `2H`/`4H`/`8H`/`OFF` | `[ ]` |

## 12. Tuner (`TF…`, `TM…`, `TP…`)

FM/HD tuner. `TFAN`/`TFHD` frequency, `TMAN`/`TMHD` band+mode, `TPAN`/`TPHD`
presets. Values: `TFANUP/DOWN`, `TFAN<freq>`, `TMANFM/AM/AUTO/MANUAL`,
`TPANUP/DOWN`, `TPAN01..`, `TPANMEM`. All `[ ]` (fit a tuner/media surface).

## 13. Network & now-playing

| Token | Function | Coverage |
|-------|----------|----------|
| `NSE`/`NSE0..8` | Net on-screen display lines (title/artist/album text) | `[~]` (HEOS used instead) |
| `NSA`/`NSFRN` | Net status / friendly name | `[~]` |
| `NS9x`, `NSRND`, `NSRPT`, `NSFV` | Net transport / shuffle / repeat / favourite | `[action]`/`[ ]` |
| HEOS CLI (port 1255) | Now-playing media + **album art URL** + transport | `[x]` media player (title/artist/album, entity_picture, play/pause/stop/next/prev) |

> This integration reads now-playing and album art from the **HEOS CLI**
> (`heos://player/get_now_playing_media`), because the legacy goform now-playing
> endpoints answer HTTP 403 on HEOS-era firmware.

## 14. System, timers, illumination

| Token | Function | Values | Coverage |
|-------|----------|--------|----------|
| `DIM` | Front-display dimmer | `BRI`/`DIM`/`DAR`/`OFF`/`SEL`(toggle) | `[x]` dimmer · gate: FrontDisplay |
| `ECO` | ECO mode | `ON`/`AUTO`/`OFF` | `[x]` eco_mode · gate: ECO |
| `SLP` | Sleep timer (main) | `nnn`/`OFF` | `[x]` sleep_timer · gate: SleepTimer |
| `STBY` | Auto-standby | `15M`/`30M`/`60M`/`OFF` | `[ ]` candidate |
| `ILB` | Illumination brightness | `nn` | `[ ]` (models with front illumination) |
| `TR` / `TR1` / `TR2` | 12 V trigger out | `ON`/`OFF` | `[ ]` candidate (switch per trigger) |
| `SYPANEL` / `SYREMOTE` | Panel / remote lock | `LOCK ON`/`LOCK OFF`/`+V LOCK ON` | `[ ]` candidate |
| `SYRST` | System restart | (action) | `[action]` |
| `BTTX` | **Bluetooth transmitter** | `ON`/`OFF` + output mode (device reports `OFF`/`SP`; modes "Bluetooth Only"/"Bluetooth + Speakers") | `[ ]` candidate (multi-field family — probe before adding) · gate: BTTX/Transmitter |
| `SSHOSALS` | Auto lip sync | `ON`/`OFF` | `[ ]` candidate · gate: AutoLipSync |

## 15. Menu / cursor navigation (`MN…`) — remote surface

`MNCUP`/`MNCDN`/`MNCLT`/`MNCRT` (cursor), `MNENT` (enter), `MNRTN` (back),
`MNMEN ON/OFF` (menu), `MNINF` (info), `MNOPT` (option), `MNCHL`
(channel-level menu). All `[action]` — belong to a future `remote`/button entity,
not stateful entities.

## 16. Raw remote codes (`RC…`) — model-specific

Some control libraries use opaque remote codes for a few toggles that lack a
clean setter, e.g. HDMI-CEC on/off (`RCKSK0410826`/`...827`), Dolby Atmos toggle,
input-mode select. These are model-specific and brittle; prefer a real setter
when one exists. `[action]` only, and only if no cleaner token is available.

## 17. Setup-interface config API (graphic EQ, audio setup)

Some audio settings — most importantly the **manual graphic equaliser** per-band
values — are not on the telnet channel at all (telnet only toggles the EQ
on/off). They are read and written through the receiver's setup interface on
**HTTPS port 10443**, which is non-disruptive (no calibration session):

- **read:** `GET /ajax/<section>/get_config?type=<id>` → an XML document
- **write:** `POST /ajax/<section>/set_config`, body `type=<id>&data=<url-encoded XML>`

`<section>` is `audio` or `globals`. The certificate is self-signed. Audio
config `type` ids: 2 center level, 3 subwoofer level, 4 surround parameter
(incl. High/Low-pass filter value lists), 5 restorer, 6 audio delay + auto lip
sync, 9 Audyssey (MultEQ / DynamicEQ / RefLevOffset / DynamicVolume / LFC /
containment), 10 graphic EQ, 11 bass sync, 12 dialog level, 13 DAC filter,
14 Dirac. Empty `<rx>`/document means the model does not have that group.

**Graphic EQ (`type=10`)** document shape:

```
<GraphicEQ>
  <Enable>1</Enable>                         on/off
  <SpeakerSelection>2</SpeakerSelection>     1 = Left/Right, 2 = Each, 3 = All
  <SelectableSpeaker><Each>3311…</Each></SelectableSpeaker>
  <AdjustEQ>
    <Channel>0</Channel>                     selected channel index
    <Eq63Hz>-110</Eq63Hz> … <Eq16kHz>-40</Eq16kHz>   9 bands, gain = value / 10 dB
  </AdjustEQ>
  <CurveCopy/>                               write 1 to copy the Audyssey/flat curve
</GraphicEQ>
```

Nine fixed bands (63 Hz … 16 kHz), range −20.0 … +6.0 dB step 0.5 (wire = dB×10).
Writes send `<GraphicEQ>…</GraphicEQ>` with only the tags being changed (e.g.
`<AdjustEQ><Channel>N</Channel><Eq500Hz>V</Eq500Hz></AdjustEQ>`), plus `<Enable>`,
`<SpeakerSelection>`, `<CurveCopy>1</CurveCopy>`, `<SetDefaults>`. Editing requires
MultEQ **off** and the graphic EQ **enabled**. Coverage: `[ ]` sub-device (build
in progress) — a distinct interface, so it fits a discovery-gated EQ sub-device.

---

## Adding a command to this integration

1. Confirm the receiver advertises it: `FuncName` present in `/goform/Deviceinfo.xml`.
2. Confirm the telnet token/values against **this doc** and, if unsure, the
   **device** (set a *different* valid value and watch for the echo — a no-op set
   does not echo).
3. Add one entry to `protocol_profile.json` with `group`, `feature` (the gate),
   `kind`, `prefix`, `query`, and `values`/`labels`. The generic matcher and the
   entity platforms pick it up automatically — no parser or platform code for the
   common kinds (`onoff`/`enum`/`level`/`integer`). Use `exact_enum: true` when
   the prefix is short and shared with siblings (e.g. `PV`).
4. Action-only commands (cursor, triggers, presets, tuner nav) are **not**
   stateful entities; they belong to a future button/remote surface.
