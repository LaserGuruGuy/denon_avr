#!/usr/bin/env python3
"""Export the denon_avr part of a Home Assistant entity + device registry as a reference golden.

Reads the registry JSON files directly (offline, read-only) and keeps only what defines the
integration's public surface: unique_id, platform domain, entity_id, original name,
translation key, entity category, disabled_by/hidden_by and the owning (sub)device. MAC
addresses are redacted like tools/redact_fixtures.py.
entity_ids are anonymised to "<domain>.<model-slug>_<rest>": anything a user prefixed
(e.g. a room name) is dropped, because these files are published.
Usage: python3 tools/export_ha_registry.py <core.entity_registry> <core.device_registry> <out.json>
       [--model X] [--deleted]   (--deleted: export deleted_entities, e.g. a replaced receiver)
"""

from __future__ import annotations

import json
import re
import sys

MAC = re.compile(r"([0-9a-f]{2}:){5}[0-9a-f]{2}|[0-9A-F]{12}", re.I)


def redact(text: str) -> str:
    return MAC.sub(lambda m: (m.group(0)[:8] + ":00:00:0X") if ":" in m.group(0) else (m.group(0)[:6] + "00000X"), text)


def anonymise_entity_id(entity_id: str, model: str | None) -> str:
    domain, object_id = entity_id.split(".", 1)
    slug = (model or "").lower().replace("-", "_")
    if slug and slug in object_id:
        object_id = object_id[object_id.index(slug):]
    return f"{domain}.{object_id}"


def main(ent_path: str, dev_path: str, out_path: str, model: str | None = None, deleted: bool = False) -> int:
    data = json.load(open(ent_path))["data"]
    ents = data["deleted_entities"] if deleted else data["entities"]
    devs = {d["id"]: d for d in json.load(open(dev_path))["data"]["devices"]}
    rows = []
    for e in ents:
        if e.get("platform") != "denon_avr":
            continue
        dev = devs.get(e.get("device_id") or "", {})
        if model and not deleted and model not in json.dumps(dev):
            continue
        if model and deleted and model.lower().replace("-", "_") not in e["entity_id"]:
            continue
        ident = next(iter(dev.get("identifiers") or []), ["", ""])[1] if dev else ""
        rows.append({
            "unique_id": redact(e["unique_id"]),
            "domain": e["entity_id"].split(".", 1)[0],
            "entity_id": anonymise_entity_id(e["entity_id"], model),
            "original_name": e.get("original_name"),
            "translation_key": e.get("translation_key"),
            "entity_category": e.get("entity_category"),
            "disabled_by": e.get("disabled_by"),
            "hidden_by": e.get("hidden_by"),
            "device": redact(str(ident)),
            "device_model": dev.get("model"),
        })
    rows.sort(key=lambda r: r["unique_id"])
    json.dump({"source": "deleted_entities" if deleted else "entities", "count": len(rows), "entities": rows}, open(out_path, "w"), indent=1, ensure_ascii=False)
    print(out_path, len(rows), "entities")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    model = a[a.index("--model") + 1] if "--model" in a else None
    sys.exit(main(a[0], a[1], a[2], model, "--deleted" in a))
