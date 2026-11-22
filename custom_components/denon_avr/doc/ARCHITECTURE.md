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
serialises the requests, and after a transport failure locates it again, at most once per 5 minutes.
Which settings exist is then decided by the receiver's own availability documents, never by the model.
The device link in HA (`configuration_url`) is the located interface.

## Safety net

`tests/` holds characterization goldens of the end-to-end behaviour, recorded from real, redacted
captures of an AVR-X3600H (legacy HTTPS 10443 web interface) and an AVC-X3900H (HTTP 11080). A
refactor must keep every golden byte-identical.

Known defects are pinned as `test_known_*`, so that fixing one is a deliberate change. See
`tests/README.md`.
