"""Architecture guard: import rules between the layers (stdlib ast, no extra tooling).

Every import is resolved to an absolute module name below the integration package
(``pkg`` = custom_components/denon_avr, the library is ``pkg.avr``), so the rules do
not depend on how many dots a relative import uses.

avr/ layers, dependencies only downwards:
  transport/  I/O per wire protocol (a module or a sub-package per protocol)
  protocol/   pure grammar, codecs, command builders, parsers
  services/   subsystems over ports
  device.py   facade + composition root
  models.py, const.py   leaves

Rules:
  R1  nothing under avr/ imports homeassistant
  R2  a transport imports only avr.const and its own unit (module or sub-package);
      transports never import each other or upper layers (transport/__init__ may
      import every transport, it is the transport package surface)
  R3  protocol/ does no network/async I/O and imports nothing from transport/,
      services/ or the facade
  R4  the Home Assistant layer imports the library only through ``avr/__init__.py``
  R5  services/ never import transport/ or aiohttp/socket (they only know the ports)
  R6  only device.py (the composition root) imports transport/
  R7  models.py and const.py are leaves (no imports from the library)
  R8  transport/, protocol/, services/, models and const never import the facade
      (device.py) nor the package surface avr/__init__ that re-exports it
  R9  no absolute self-imports (custom_components.denon_avr...), they bypass these rules

ALLOWED lists known violations. It is a ratchet: a new violation fails, and an entry
that no longer occurs must be removed (so the list only shrinks). Empty since step 2.
"""

from __future__ import annotations

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "denon_avr"
ROOT = "pkg"
LIB = f"{ROOT}.avr"
IO_MODULES = ("aiohttp", "asyncio", "socket")

ALLOWED: set[tuple[str, str, str]] = set()


def _module_name(path: pathlib.Path) -> tuple[str, str]:
    """(module name, package name) of a source file."""

    parts = [ROOT, *path.relative_to(PKG).with_suffix("").parts]
    if parts[-1] == "__init__":
        parts = parts[:-1]
        return ".".join(parts), ".".join(parts)
    return ".".join(parts), ".".join(parts[:-1])


def _is_module(dotted: str) -> bool:
    rel = dotted.split(".")[1:]
    target = PKG.joinpath(*rel) if rel else PKG
    return target.with_suffix(".py").exists() or (target / "__init__.py").exists()


def _imports(path: pathlib.Path, source: str | None = None) -> set[str]:
    """Absolute names imported by a file (``source`` overrides its content, for tests)."""

    _, package = _module_name(path)
    out: set[str] = set()
    text = path.read_text(encoding="utf-8") if source is None else source
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                out.add(node.module or "")
                continue
            base_parts = package.split(".")
            base_parts = base_parts[: len(base_parts) - (node.level - 1)]
            base = ".".join(base_parts + ([node.module] if node.module else []))
            out.add(base)
            # `from .avr import x` imports a module too when x is one (not a constant).
            for alias in node.names:
                if _is_module(f"{base}.{alias.name}"):
                    out.add(f"{base}.{alias.name}")
    return out


def _under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _is_io(name: str) -> bool:
    return any(_under(name, m) for m in IO_MODULES)


def _lib_violations(path: pathlib.Path, source: str | None = None) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    module, _ = _module_name(path)
    rel = str(path.relative_to(PKG))
    parts = module.split(".")
    layer = parts[2] if len(parts) > 2 else "__init__"
    unit = ".".join(parts[:4])  # pkg.avr.transport.<name>
    for imp in _imports(path, source):
        if _under(imp, "homeassistant"):
            found.add(("R1", rel, imp))
        if _under(imp, "custom_components"):
            found.add(("R9", rel, imp))
        if not _under(imp, ROOT):
            if layer == "protocol" and _is_io(imp):
                found.add(("R3", rel, imp))
            if layer == "services" and (_under(imp, "aiohttp") or _under(imp, "socket")):
                found.add(("R5", rel, imp))
            continue
        if layer == "transport":
            surface = module == f"{LIB}.transport"
            allowed = _under(imp, f"{LIB}.const") or _under(imp, f"{LIB}.transport" if surface else unit)
            if not allowed:
                found.add(("R2", rel, imp))
        if layer == "protocol" and any(_under(imp, f"{LIB}.{x}") for x in ("transport", "services", "device")):
            found.add(("R3", rel, imp))
        if layer == "services" and _under(imp, f"{LIB}.transport"):
            found.add(("R5", rel, imp))
        if layer not in ("device", "transport") and _under(imp, f"{LIB}.transport"):
            found.add(("R6", rel, imp))
        if layer in ("models", "const") and _under(imp, LIB):
            found.add(("R7", rel, imp))
        inner = layer in ("transport", "protocol", "services", "models", "const")
        if inner and (_under(imp, f"{LIB}.device") or imp == LIB):
            found.add(("R8", rel, imp))
    return found


def _violations() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted((PKG / "avr").rglob("*.py")):
        found |= _lib_violations(path)
    for path in sorted(PKG.glob("*.py")):
        for imp in _imports(path):
            if _under(imp, LIB) and imp != LIB:
                found.add(("R4", path.name, imp))
            if _under(imp, "custom_components"):
                found.add(("R9", path.name, imp))
    return found


def test_no_new_layer_violations():
    new = _violations() - ALLOWED
    assert not new, "new layer violations (fix them; do not extend ALLOWED): " + ", ".join(map(str, sorted(new)))


def test_allowlist_only_shrinks():
    stale = ALLOWED - _violations()
    assert not stale, "these violations are gone - remove them from ALLOWED: " + ", ".join(map(str, sorted(stale)))


def test_guard_resolves_imports():
    """The guard itself works: it resolves the facade's relative imports."""

    imports = _imports(PKG / "avr" / "device.py")
    assert f"{LIB}.transport" in imports and f"{LIB}.services.sync" in imports
    assert f"{LIB}.const" in _imports(PKG / "avr" / "transport" / "telnet.py")


def test_guard_catches_crafted_escapes():
    """Negative tests: imports that must be caught, however they are spelled."""

    services = PKG / "avr" / "services" / "control.py"
    protocol = PKG / "avr" / "protocol" / "codec.py"
    transport = PKG / "avr" / "transport" / "telnet.py"
    cases = {
        (services, "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .. import DenonAvrDevice\n"): "R8",
        (protocol, "from .. import DenonAvrDevice\n"): "R8",
        (services, "from ..device import DenonAvrDevice\n"): "R8",
        (services, "from ..transport.telnet import TelnetClient\n"): "R5",
        (protocol, "import asyncio\n"): "R3",
        (transport, "from .heos import HeosClient\n"): "R2",
        (transport, "from ..services import sync\n"): "R2",
        (services, "import custom_components.denon_avr.avr.transport\n"): "R9",
    }
    for (path, source), rule in cases.items():
        rules = {v[0] for v in _lib_violations(path, source)}
        assert rule in rules, f"{rule} not raised for {path.name}: {source!r} -> {rules}"
