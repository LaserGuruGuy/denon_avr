"""Fixtures and goldens are published: they must not identify a real receiver or LAN."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from redact_fixtures import redact  # noqa: E402

FILES = sorted(p for d in ("fixtures", "golden") for p in (ROOT / "tests" / d).rglob("*") if p.is_file())


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT / "tests")))
def test_file_is_redacted(path):
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    assert redact(text) == text, "unredacted device data; run tools/redact_fixtures.py on this folder"


REGISTRY = sorted((ROOT / "tests" / "golden" / "ha_registry").glob("*.json"))


@pytest.mark.parametrize("path", REGISTRY, ids=lambda p: p.name)
def test_registry_entity_ids_are_anonymised(path):
    """entity_ids start at the model slug: no user-chosen prefix (room, person) is published."""
    import json
    import re

    rows = json.loads(path.read_text())["entities"]
    bad = [r["entity_id"] for r in rows if not re.match(r"^[a-z_]+\.[a-z]{2,4}_[a-z0-9]+(_|$)", r["entity_id"])]
    assert rows and not bad, f"re-export with tools/export_ha_registry.py: {bad[:5]}"
