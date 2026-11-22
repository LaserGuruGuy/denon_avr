"""Protocol layer: wire grammar, codecs, command builders and parsers. No network I/O.

Everything here is deterministic over its inputs; the only state is the telnet
parser's per-response accumulators, and load_profile reads the bundled JSON once.

* profile      - typed view over protocol_profile.json (the fixed grammar)
* codec        - value encodings shared by the channels
* commands     - telnet command builders
* telnet_parser- telnet events -> Discovery/AvrState
* deviceinfo   - goform Deviceinfo document
* upnp         - UPnP/AIOS description
* webdoc       - setup web interface documents
* eq_doc       - graphic EQ documents and payloads
"""
