"""Architecture guard: import rules between the layers (stdlib ast, no extra tooling).

avr/ layers, dependencies only downwards:
  transport/  I/O per wire protocol          -> ..const only
  protocol/   pure grammar, codecs, parsers  -> protocol siblings, ..models, ..const
  services/   subsystems over ports          -> protocol, models, const, siblings
  device.py   facade + composition root      -> everything in avr/
  models.py, const.py                        -> nothing in avr/ (leaf modules)

Rules:
  R1  nothing under avr/ imports homeassistant
  R2  transport/* imports only ..const from the library
  R3  protocol/* is pure: no aiohttp/asyncio/socket, nothing from transport/ or services/
  R4  the Home Assistant layer imports the library only through the package surface ``.avr``
  R5  services/* never import transport/ or aiohttp/socket (they only know ports)
  R6  only device.py (the composition root) imports transport/
  R7  models.py and const.py are leaves (no imports from the library)
  R8  transport/, protocol/ and services/ never import the facade (device.py)
  R9  no absolute self-imports (custom_components.denon_avr...), they bypass these rules

ALLOWED lists known violations. It is a ratchet: a new violation fails, and an entry
that no longer occurs must be removed (so the list only shrinks). It is empty since the
step-2 refactor.
"""

from __future__ import annotations

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "denon_avr"
LIB = PKG / "avr"
IO_MODULES = ("aiohttp", "asyncio", "socket")

ALLOWED: set[tuple[str, str, str]] = set()


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            out.add(base)
            # `from .avr import graphic_eq` / `from . import transport` import MODULES too;
            # count a name only when it resolves to a module file/package (not a constant).
            if node.level:
                pkg_dir = path.parent
                for _ in range(node.level - 1):
                    pkg_dir = pkg_dir.parent
                target = pkg_dir.joinpath(*(node.module or "").split(".")) if node.module else pkg_dir
                sep = "" if base.endswith(".") else "."
                for alias in node.names:
                    if (target / f"{alias.name}.py").exists() or (target / alias.name / "__init__.py").exists():
                        out.add(base + sep + alias.name)
        elif isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
    return out


def _is_io(imp: str) -> bool:
    return any(imp == m or imp.startswith(m + ".") for m in IO_MODULES)


def _violations() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted(LIB.rglob("*.py")):
        rel = path.relative_to(LIB)
        name = str(path.relative_to(PKG))
        layer = rel.parts[0] if len(rel.parts) > 1 else rel.name
        for imp in _imports(path):
            if imp == "homeassistant" or imp.startswith("homeassistant."):
                found.add(("R1", name, imp))
            if layer == "transport":
                outside = imp.startswith("..") and imp != "..const" and not imp.startswith("..const.")
                sibling = path.name != "__init__.py" and imp.startswith(".") and not imp.startswith("..")
                if outside or sibling:      # transports never import each other or upper layers
                    found.add(("R2", name, imp))
            if layer in ("transport", "protocol", "services") and imp.startswith(("..device", "...device")):
                found.add(("R8", name, imp))
            if imp.startswith("custom_components"):
                found.add(("R9", name, imp))
            if layer == "protocol" and (_is_io(imp) or imp.startswith(("..transport", "..services", "..device"))):
                found.add(("R3", name, imp))
            if layer == "services" and (imp.startswith("..transport") or imp in ("aiohttp", "socket") or imp.startswith("aiohttp.")):
                found.add(("R5", name, imp))
            if layer not in ("device.py", "transport") and imp.startswith((".transport", "..transport")):
                found.add(("R6", name, imp))
            if layer in ("models.py", "const.py") and imp.startswith("."):
                found.add(("R7", name, imp))
    for path in sorted(PKG.glob("*.py")):
        for imp in _imports(path):
            if imp.startswith(".avr."):
                found.add(("R4", path.name, imp))
    return found


def test_no_new_layer_violations():
    new = _violations() - ALLOWED
    assert not new, "new layer violations (fix them; do not extend ALLOWED): " + ", ".join(map(str, sorted(new)))


def test_allowlist_only_shrinks():
    stale = ALLOWED - _violations()
    assert not stale, "these violations are gone - remove them from ALLOWED: " + ", ".join(map(str, sorted(stale)))
