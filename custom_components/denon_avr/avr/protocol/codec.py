"""Pure value codecs for the Denon telnet protocol."""

from __future__ import annotations


def decode_half_step(token: str) -> float | None:
    """Decode a Denon half step number such as '50' or '455'."""

    token = token.strip()
    if not token.isdigit():
        return None
    if len(token) >= 3:
        # A three digit value carries a trailing half step: '465' -> 46.5.
        return int(token[:2]) + (0.5 if token[2] == "5" else 0.0)
    return float(int(token))


def encode_half_step(value: float) -> str:
    """Encode a value to the Denon half step representation."""

    value = max(0.0, value)
    whole = int(value)
    has_half = abs(value - whole) >= 0.25
    if has_half:
        return f"{whole:02d}5"
    return f"{whole:02d}"


def decode_centered(token: str, center: int) -> int | None:
    """Decode a fixed-width integer token that is offset around a center point."""

    token = token.strip()
    if not token.isdigit():
        return None
    return int(token) - center


def encode_centered(value: float, center: int, width: int) -> str:
    """Encode a centred value back to its fixed-width wire integer (see above)."""

    return str(int(round(value)) + center).zfill(width)
