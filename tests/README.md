# Tests

HA-free tests for the `avr` library. Network I/O is replaced by recorded fixtures.

```
pip install -r requirements_test.txt
python -m pytest -p no:anyio            # DENON_UPDATE_GOLDEN=1 to accept intended changes
```

* `fixtures/<receiver>/` holds the raw responses: goform, UPnP, `/ajax` documents and a telnet corpus.
  `fixture.json` describes the set and how the setup web interface is reachable (`webconfig`).
  - `avc_x3900h`: real capture made with `tools/capture_fixtures.py`, read-only and redacted.
  - `avr_x3600h`: real capture, read-only and redacted. Legacy interface: HTTPS 10443, global 4K type 8,
    Amp Assign type 2, 'SSCFR IDV/ALL' crossover mode.
* `golden/<receiver>/` pins the end-to-end behaviour of v1.1.2: discovery, resync queries, telnet replay,
  every setter's exact wire string and the web subsystems. A refactor must keep these byte-identical.
* `golden/ha_registry/` is a reference snapshot of the HA entity surface (unique_ids): live X3900H and the
  removed X3600H. It is used to check the migration in later steps and is not asserted yet.
* `test_known_*` tests pin **known defects** of the current code on purpose, for example the X3900H crossover
  mode, the probe that stops after the first line, and the YES/NO speaker grammar. They say "this is wrong
  today", not "this is right". The fix must change them deliberately.
* `test_layers.py` is the import-rule guard between layers (rules R1-R9, see
  `custom_components/denon_avr/doc/ARCHITECTURE.md`). Its allowlist is empty and may only shrink.
* `test_ports.py` checks that the transports satisfy the service ports.

Fixtures never contain serial numbers, UUIDs, MAC device parts or LAN IPs. Captures are redacted
automatically, and CI checks this.
