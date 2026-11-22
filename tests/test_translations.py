"""Every stable key the library/profile produces has a translation (en + nl); strings.json == en."""

from __future__ import annotations

import json
import pathlib

import pytest

import conftest  # noqa: F401  # pyflakes: ignore - loads the avr package
from avr import load_profile

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "denon_avr"
LANGS = {p.stem: json.loads(p.read_text()) for p in (PKG / "translations").glob("*.json")}
SPEAKERS = load_profile().grammar["speakers_web"]


def test_strings_json_equals_english():
    assert json.loads((PKG / "strings.json").read_text()) == LANGS["en"]


@pytest.mark.parametrize("lang", sorted(LANGS))
def test_profile_keys_are_translated(lang):
    sensor = LANGS[lang]["entity"]["sensor"]
    assert set(SPEAKERS["amp_modes"]["keys"].values()) <= set(sensor["amp_mode"]["state"])
    assert set(SPEAKERS["subwoofer_setup"]["mode_keys"].values()) <= set(sensor["subwoofer_mode"]["state"])
    assert set(SPEAKERS["subwoofer_setup"]["layout_keys"].values()) <= set(sensor["subwoofer_layout"]["state"])
