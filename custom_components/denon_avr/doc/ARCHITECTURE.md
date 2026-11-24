# Architecture

`denon_avr` has two parts: a Home Assistant adapter (`custom_components/denon_avr/*.py`) and an
HA-free client library (`custom_components/denon_avr/avr/`). The library could be published on its own.

## Principles

- **Generic.** Receivers differ by protocol and interface version and by the capabilities they
  report, never by model name. User setup (speakers, sizes, crossovers, subwoofers, sources and
  their names, zones, presets, amp assign or speaker layout) is discovered from the device and
  followed at runtime. The code never assumes one household's configuration.
- **Telnet first.** The documented control protocols are the primary channels: telnet (port 23,
  with push) and, where needed, JSON over TCP (port 1256). The setup web interface (`/ajax`) is a
  secondary supplement, used only for settings those protocols cannot serve, after a live check.
  Hard-coding is the last resort. Every web-backed grammar section states this in `channel_policy`,
  and `tests/test_channel_policy.py` enforces it.
- **Data first.** Fixed protocol grammar lives in `protocol/protocol_profile.json`. Value lists
  the device supplies take precedence over the profile, which then only provides labels.
- **Languages.** All user-facing text goes through Home Assistant translations
  (`strings.json`, `translations/`). The library returns stable keys, never sentences.

## Entity catalogue (catalog.py)

`catalog.build(device)` is a pure function (no Home Assistant) that lists every entity: platform, kind,
key, sub-device and default enablement. The platforms only turn entries into entity objects. An entry's
`key` is the entity's unique_id suffix, and `catalog.unique_id()` is the one place the full id is built.
`tests/test_catalog.py` checks that every unique_id in the recorded Home Assistant registries
(`tests/golden/ha_registry`) is still produced, so existing entities keep their id, history and settings.
Capability decides whether an entity exists; user setup decides its default enablement and availability.
Examples: a size select on firmware that reports presence (YES/NO) exists but is unavailable; channels
come from Deviceinfo plus the receiver's telnet level list (e.g. a second subwoofer).

## Library layers (dependencies only point downwards)

```
 HA adapter            custom_components/denon_avr/*.py     imports only `from .avr import ...`
 ─────────────────────────────────────────────────────────────────────────────────────
 device.py             facade + composition root: wires transports into services,
                       lifecycle, the stable public API
 services/             subsystems over ports: discovery, sync, control, graphic_eq,
                       webconfig_doc (one controller per setup web section);
                       ports.py = CommandSink, ConfigDocumentPort; runtime.py = tasks/notify
 protocol/             pure grammar: profile, codec, commands (telnet builders),
                       telnet_parser, deviceinfo, upnp, webdoc, eq_doc
 transport/            one isolated client per wire protocol: telnet 23, goform 8080,
                       upnp 60006, heos 1255, webconfig (/ajax), tcp_client 1256 (not wired)
 models.py, const.py   shared data types and constants (leaves)
```

Enforced by `tests/test_layers.py`. The allowlist is empty and may only shrink.

| Rule | Meaning |
|------|---------|
| R1 | nothing in `avr/` imports `homeassistant` |
| R2 | a transport imports only `..const`; transports never import each other |
| R3 | `protocol/` does no network or async I/O and imports nothing from `transport/` or `services/` |
| R4 | the HA adapter imports the library only through `avr/__init__.py` |
| R5 | `services/` never import `transport/` or `aiohttp`/`socket`; they only know the ports |
| R6 | only `device.py` imports `transport/` (the composition root) |
| R7 | `models.py` and `const.py` are leaves |
| R8 | `transport/`, `protocol/` and `services/` never import the facade |
| R9 | no absolute self-imports |

`tests/test_ports.py` checks that the transports structurally satisfy the ports.

## Locating the setup web interface

Interface generations serve the setup UI on different endpoints (HTTPS 10443 on the legacy app,
plain HTTP 11080 on "Web Control"). Both put an nginx front on port 80/443 that redirects `/` to it.
`transport/webconfig/endpoint.py` follows that redirect chain (at most 3 hops, e.g. http -> https ->
:10443), only on the same host over http or https. It never accepts a front port (80/443) or a port of
another receiver service (telnet, HEOS, JSON, goform, UPnP), and it verifies that the target answers.
Otherwise it falls back to the known endpoints. The probes run concurrently with a 2 s timeout within
a 6 s budget. `WebConfigClient` locates the interface on first use,
serialises the requests, and after a transport failure locates it again, again after 50, 110 and then every 300 s.
Which settings exist is then decided by the receiver's own availability documents, never by the model.
The device link in HA (`configuration_url`) is the located interface.

## Push and refresh

- **Telnet push reaches HA at once.** telnet line -> `TelnetParser` -> `AvrState` -> `StateSync`
  -> 0.1 s debounce -> coordinator listeners. Only a real change notifies: `StateSync` compares a
  fingerprint of state and discovery, so the receiver's periodic signal cluster (about every 5 s on the
  X3900H) and list lines before their END do not rewrite every entity.
- **The coordinator never restarts its interval on a push** (`async_update_listeners`, not
  `async_set_updated_data`), so the 60 s reconcile poll keeps running with a pushing receiver.
- **Refresh groups run when a line changed something, or when the parser does not model the line**
  (then a change cannot be ruled out). The first value of a trigger prefix after start does not run a
  group (the resync asks for everything). An unchanged, understood, repeated
  push does not re-query. The OPSMLALL END handler still re-queries OPSML unconditionally.
- **What the receiver does not re-push** is re-queried by the profile's `refresh` groups (audio info
  and the speaker/channel layout after MS/SI, the sound mode lists after a signal change).
- **Web documents have no push.** A `refresh` group can name web sections (`web`) to re-read when a
  related telnet line arrives: SI -> video (per-input 4K/8K, HDCP); SSSPC/SSCFR/SPPR/PSSP: -> speakers (`PSSP:` with colon, so the Speaker Virtualizer `PSSPV` does not match);
  PSGEQ -> graphic EQ. One read per section at a time; triggers during a read cause exactly one re-run.

## Silence is not absence

A command or item that does not answer is UNKNOWN, not absent: it may be silent only in the current
setup (PSFRONT answers only with a Front B amp assignment, VSSCH only in certain video modes), or a
response may have been cut off. Absence comes only from an explicit statement by the receiver: a
Deviceinfo feature list, an availability `display` of 1, or an explicit absent token (NO / NON).
Consequences:
- profile controls are never removed because their query is silent;
- telnet-first checks that found no answer are recorded as "not found in this setup", and are re-checked
  in other setups and on other receivers;
- parsed items (speaker groups, list entries) are never dropped because they went silent;
- entities that existed stay and turn unavailable; they are not removed (owner decision).

- A speaker group the receiver never mentions counts as not configured (newer firmware omits absent
  groups). Its channel entities are unavailable, never removed. That is the same meaning the default
  enablement always had; it is a deliberate choice, not a removal on silence.

## Telnet setup grammar

- **Introspection completes on a sentinel plus closed lists.** After the introspection queries the
  probe sends the profile's `introspection_sentinel` (`PW?`). It is complete when that has answered
  AND no END-terminated list (`terminated` in the profile) is still open: replies mostly come in order,
  but the sound mode lists stream asynchronously between other replies. SSSPC has no END line at all.
- **SSSPC values are classified per protocol generation** (`speakers.tokens`): presence (YES/NO on
  newer firmware), size (LAR/SMA, and NON = size "none" on older firmware), count (`<n>SP`), other. A
  group is absent only when its presence (or, without a presence line, its size) is an absent token. Per group the last value of
  each kind wins, so a push or a repeated response overwrites instead of accumulating. Newer firmware
  omits absent groups; a group that was reported and later goes silent is kept (see below).
- **Count groups map a count to channels** (`count_groups.*.by_count`): subwoofers 1-4 (SW..SW4),
  surround back 1 (SB) or 2 (SBL/SBR).
- **Numbered lists (OPSML, OPSMLALL):** entry number 1 starts a new response; on END the list is
  replaced only when the numbers run 1..n without a gap, otherwise merged, so nothing is dropped.
- **Crossover "FUL"** (full range) is 0, as in the web value list; the command builder sends 0 as FUL.

## Setup web documents (protocol/webdoc.py)

- **Availability in one place** (`models.Availability`), read the way the receiver's own Web Control
  app reads it: `display` 2, 3 and 4 are shown; 3 and 4 are active (settable); 2 is shown but
  disabled; anything else (1 = not applicable) is not shown. `gray` is not a lock but a reason code
  the UI uses only for a disabled item to pick its explanation. Documents of menus reported as not
  applicable are not read. The availability map is re-read on every poll, so items follow the user's
  setup. Each document is fetched at most once per pass. A failed read keeps the last values.
- **Per-input collections** (`collections` in the profile): one element per input slot, keyed by the
  receiver's own index (stable across renames). Allowed options come per item from the receiver
  (conditional options, or the item's own list). A write is refused unless the receiver reports the
  item as settable and allows that option. Collections can be declared read-only (`writable: false`).
- **Device value lists** (`value_lists`): capability lists such as the crossover grid or LPF values.
  The setting itself stays on telnet; the profile constant is only the fallback.

## Safety net

`tests/` holds characterization goldens of the end-to-end behaviour, recorded from real, redacted
captures of an AVR-X3600H (legacy HTTPS 10443 web interface) and an AVC-X3900H (HTTP 11080). A
refactor must keep every golden byte-identical.

Known defects are pinned as `test_known_*`, so that fixing one is a deliberate change. See
`tests/README.md`.

## Staged speaker layout (avr/services/staged.py)
Prepare, apply, restore. Applying resets other choices on the receiver, which going back
does not restore, so the state before the first apply is kept in memory as a restore point.
Rules: one operation at a time; the preset must be the same (an unanswered question refuses);
no position is written before the mode is confirmed; choices made after the apply are kept;
front A/B is read fresh over telnet and verified; nothing is written after unload.
