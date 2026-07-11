"""Pure value codecs for the Denon telnet protocol.

These helpers convert between the on the wire textual representation and Python
values. They are pure functions with no device or Home Assistant dependency and
are shared by the parser (decoding incoming lines) and the device (encoding
outgoing commands).

The receiver encodes levels and the master volume on a "half step" scale: a two
digit number is a whole step, and a three digit number carries a trailing half
step. For example "50" means 50.0 and "455" means 45.5. The reference point
(the value that represents 0 dB) differs per control and is supplied by the
caller.
"""

from __future__ import annotations


def decode_half_step(token: str) -> float | None:
    """Decode a Denon half step number such as '50' or '455'.

    Returns None when the token is not numeric (for example 'OFF' or 'NON').
    """

    token = token.strip()
    if not token.isdigit():
        return None
    if len(token) >= 3:
        # A three digit value carries a trailing half step: '465' -> 46.5.
        return int(token[:2]) + (0.5 if token[2] == "5" else 0.0)
    return float(int(token))


def encode_half_step(value: float) -> str:
    """Encode a value to the Denon half step representation.

    Whole numbers become two digits ('50'), half steps become three digits
    ('455'). The integer part is always zero padded to two digits. Values are
    clamped to zero because the wire scale has no negative representation (every
    caller works with the raw 0-based scale, not dB offsets).
    """

    value = max(0.0, value)
    whole = int(value)
    has_half = abs(value - whole) >= 0.25
    if has_half:
        return f"{whole:02d}5"
    return f"{whole:02d}"


def decode_centered(token: str, center: int) -> int | None:
    """Decode a fixed-width integer token that is offset around a center point.

    The picture controls encode their value as a plain, zero-padded integer that
    is centred on a fixed point (for example contrast '050' with centre 50 means
    0, '000' means -50, '100' means +50). Unlike the half step scale, all three
    digits are data. Returns None when the token is not numeric.
    """

    token = token.strip()
    if not token.isdigit():
        return None
    return int(token) - center


def encode_centered(value: float, center: int, width: int) -> str:
    """Encode a centred value back to its fixed-width wire integer (see above)."""

    return str(int(round(value)) + center).zfill(width)
