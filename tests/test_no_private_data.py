"""No identifying data of a real receiver or network in the repository."""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
BARE_MAC = re.compile(r"\b(?:0005CD|000678)[0-9A-Fa-f]{6}\b", re.I)   # Denon / Marantz OUIs
LAN_IP = re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")


def _tracked() -> list[pathlib.Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / f for f in out.splitlines() if f.endswith((".py", ".md", ".json", ".xml", ".yaml", ".yml"))]


def _placeholder(mac: str) -> bool:
    """Placeholders keep the vendor part and zero the device part (last byte free)."""

    return re.sub(r"[:-]", "", mac)[6:10] == "0000"


def test_no_real_mac_or_lan_address():
    found = []
    for path in _tracked():
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for mac in MAC.findall(text) + BARE_MAC.findall(text):
            if not _placeholder(mac):
                found.append(f"{path.relative_to(ROOT)}: MAC")
        if LAN_IP.search(text):
            found.append(f"{path.relative_to(ROOT)}: LAN address")
    assert not found, found
