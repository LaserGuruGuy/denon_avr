#!/usr/bin/env python3
"""Capture read-only fixtures from a live Denon/Marantz receiver (stdlib only).

Writes raw device responses into tests/fixtures/<name>/ for the characterization
tests. Strictly READ-ONLY:

* telnet: only commands that are queries (contain '?'), taken from
  protocol_profile.json; anything else is refused;
* HTTP: only GET of status/description documents and /ajax ``get_config``;
  ``set_config`` is never called.

Usage: python3 tools/capture_fixtures.py <host> <fixture-name> [--web-base URL]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

from redact_fixtures import redact

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = ROOT / "custom_components" / "denon_avr" / "avr" / "protocol" / "protocol_profile.json"

GOFORM = (
    "/goform/Deviceinfo.xml",
    "/goform/formMainZone_MainZoneXmlStatusLite.xml",
    "/goform/formZone2_Zone2XmlStatusLite.xml",
)
UPNP = ("/upnp/desc/aios_device/aios_device.xml",)
# /ajax documents: (section, type). Covers every document the integration reads
# today plus the ones the newer setup interface adds.
AJAX = [("video", t) for t in (1, 3, 4, 7, 8, 9, 11, 12, 13, 14)]
AJAX += [("audio", t) for t in (1, 3, 10)]
AJAX += [("speakers", t) for t in (1, 2, 3, 4, 6, 15, 17)]
AJAX += [("globals", t) for t in (1, 7)]


def profile_queries() -> list[str]:
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("query", "list_query", "read_query") and isinstance(value, str):
                    if value not in found:
                        found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(PROFILE.read_text()))
    return found


def http_get(url: str, insecure: bool = False) -> tuple[int, bytes]:
    ctx = ssl._create_unverified_context() if insecure else None  # noqa: S323 - LAN device, self-signed
    try:
        with urllib.request.urlopen(url, timeout=8, context=ctx) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, OSError):
        return 0, b""


def capture_telnet(host: str, queries: list[str], settle: float = 0.35) -> list[dict]:
    """Send each query and record every line received until the next query."""
    for q in queries:
        if "?" not in q:
            raise SystemExit(f"refusing non-query telnet command {q!r}")
    out: list[dict] = []
    with socket.create_connection((host, 23), timeout=5) as sock:
        sock.settimeout(0.2)
        buf = b""
        t0 = time.monotonic()
        for q in queries:
            sock.sendall(q.encode("ascii") + b"\r")
            end = time.monotonic() + settle
            while time.monotonic() < end:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    text = line.decode("ascii", "replace").strip()
                    if text:
                        out.append({"t": round(time.monotonic() - t0, 3), "after": q, "line": text})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("name")
    ap.add_argument("--web-base", default=None, help="setup-config base, e.g. http://host:11080 or https://host:10443")
    args = ap.parse_args()
    dest = ROOT / "tests" / "fixtures" / args.name
    dest.mkdir(parents=True, exist_ok=True)
    meta: dict = {"host_redacted": True, "captured": time.strftime("%Y-%m-%dT%H:%M:%S"), "documents": {}}

    for path in GOFORM:
        code, body = http_get(f"http://{args.host}:8080{path}")
        meta["documents"][f"goform{path}"] = code
        if code == 200:
            (dest / ("goform_" + path.rsplit("/", 1)[-1])).write_bytes(body)
    for path in UPNP:
        code, body = http_get(f"http://{args.host}:60006{path}")
        meta["documents"][f"upnp{path}"] = code
        if code == 200:
            (dest / "upnp_aios_device.xml").write_bytes(body)
    if args.web_base:
        insecure = args.web_base.startswith("https")
        for section, type_id in AJAX:
            code, body = http_get(f"{args.web_base}/ajax/{section}/get_config?type={type_id}", insecure)
            meta["documents"][f"ajax/{section}/{type_id}"] = code
            if code == 200 and body:
                (dest / f"ajax_{section}_{type_id}.xml").write_bytes(body)

    lines = capture_telnet(args.host, profile_queries())
    (dest / "telnet_corpus.json").write_text(json.dumps(lines, indent=1, ensure_ascii=False) + "\n")
    meta["telnet_lines"] = len(lines)
    (dest / "capture_meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    for path in dest.iterdir():  # never leave identifying data (serial, UUID, MAC, IP) on disk
        if path.is_file():
            path.write_text(redact(path.read_text(encoding="utf-8", errors="surrogateescape")),
                            encoding="utf-8", errors="surrogateescape")
    print(json.dumps({k: v for k, v in meta.items() if k != "documents"}, indent=1))
    print("documents:", sum(1 for v in meta["documents"].values() if v == 200), "ok,",
          [k for k, v in meta["documents"].items() if v != 200], "not 200")
    return 0


if __name__ == "__main__":
    sys.exit(main())
