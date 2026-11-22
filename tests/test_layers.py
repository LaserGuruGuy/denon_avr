"""Architecture guard: import rules between layers (stdlib ast, no extra tooling).

Rules (see doc/ARCHITECTURE.md once written):
  R1  nothing under avr/ imports homeassistant
  R2  avr/transport/* imports only ..const from the library (transports never import
      each other, the profile, the parser or models)
  R3  pure protocol modules (codec, parser, profile, models) never import aiohttp,
      asyncio or the transport package
  R4  the Home Assistant layer imports the library only through the package surface
      (``.avr``), never its internal modules

ALLOWED lists today's known violations. It is a ratchet: a new violation fails, and an
entry that no longer occurs must be removed from ALLOWED (so the list only shrinks).
"""

from __future__ import annotations

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "denon_avr"
LIB = PKG / "avr"
PURE = {"codec.py", "parser.py", "profile.py", "models.py", "const.py"}

# (rule, module, imported) - known in v1.1.2; shrink during the refactor.
ALLOWED = {
    ("R4", "__init__.py", ".avr.profile"),
    ("R4", "config_flow.py", ".avr.profile"),
    ("R4", "helpers.py", ".avr.models"),
    ("R4", "helpers.py", ".avr.profile"),
    ("R4", "media_player.py", ".avr.models"),
    ("R4", "number.py", ".avr.graphic_eq"),
    ("R4", "number.py", ".avr.profile"),
    ("R4", "select.py", ".avr.graphic_eq"),
    ("R4", "select.py", ".avr.profile"),
    ("R4", "sensor.py", ".avr.models"),
    ("R4", "switch.py", ".avr.profile"),
}


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


def _violations() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted(LIB.rglob("*.py")):
        rel = str(path.relative_to(PKG))
        imps = _imports(path)
        for imp in imps:
            if imp == "homeassistant" or imp.startswith("homeassistant."):
                found.add(("R1", rel, imp))
        if path.parent.name == "transport" and path.name != "__init__.py":
            for imp in imps:
                if imp.startswith(".") and imp not in ("..const",):
                    found.add(("R2", rel, imp))
        if path.parent == LIB and path.name in PURE:
            for imp in imps:
                if imp in ("aiohttp", "asyncio") or imp.startswith((".transport", "aiohttp.")):
                    found.add(("R3", rel, imp))
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
