#!/usr/bin/env python3
"""Replace device-identifying values in captured fixtures (idempotent, structure-preserving).

MAC addresses keep the vendor OUI (first 3 bytes, needed for vendor detection) and get a
fixed device part; serial numbers and UPnP UUIDs are zeroed with the same length/format.
Usage: python3 tools/redact_fixtures.py tests/fixtures/<name>
"""

from __future__ import annotations

import pathlib
import re
import sys

MAC_PLAIN = re.compile(r"\b(000678|0005CD|00067B)([0-9A-Fa-f]{6})\b")
MAC_COLON = re.compile(r"\b(00:06:78|00:05:CD|00:06:7B):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})\b", re.I)
SERIAL = re.compile(r"(<serialNumber>)([A-Za-z0-9]+)(</serialNumber>)")
UUID = re.compile(r"(uuid:)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
IPV4 = re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")


def redact(text: str) -> str:
    text = MAC_PLAIN.sub(lambda m: m.group(1) + "00000" + m.group(2)[-1], text)
    text = MAC_COLON.sub(lambda m: f"{m.group(1)}:00:00:0{m.group(4)[-1]}", text)
    text = SERIAL.sub(lambda m: m.group(1) + m.group(2)[:4] + "0" * (len(m.group(2)) - 4) + m.group(3), text)
    text = UUID.sub(lambda m: m.group(1) + "00000000-0000-0000-0000-000000000000", text)
    return IPV4.sub("192.0.2.10", text)


def main(folder: str) -> int:
    changed = 0
    for path in sorted(pathlib.Path(folder).iterdir()):
        if path.is_file():
            old = path.read_text(encoding="utf-8", errors="surrogateescape")
            new = redact(old)
            if new != old:
                path.write_text(new, encoding="utf-8", errors="surrogateescape")
                changed += 1
    print(f"redacted {changed} file(s) in {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
