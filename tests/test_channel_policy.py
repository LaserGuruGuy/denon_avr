"""Telnet first: the setup web interface is a secondary channel, used only as a supplement.

Agreed preference, best first: the documented control protocols (telnet; JSON over
TCP port 1256) > the setup web interface (/ajax) > hard-coding. Every web-backed
grammar section must therefore state, in ``channel_policy``, that no telnet/1256
equivalent exists. A new web-backed setting cannot slip in without that statement.
"""

from __future__ import annotations

import conftest  # noqa: F401  # pyflakes: ignore - loads the avr package
from avr import load_profile

WEB_SECTIONS = ("graphic_eq", "video_web", "speakers_web")


def test_every_web_section_declares_it_is_secondary():
    grammar = load_profile().grammar
    web_backed = {name for name, sec in grammar.items() if isinstance(sec, dict) and "config_section" in sec}
    assert web_backed == set(WEB_SECTIONS), f"new web-backed section(s): {web_backed - set(WEB_SECTIONS)}"
    for name in WEB_SECTIONS:
        policy = grammar[name].get("channel_policy", "")
        assert policy.startswith("secondary:"), f"{name}: state why telnet/1256 cannot serve it"


def test_no_web_control_duplicates_a_telnet_control():
    profile = load_profile()
    telnet_ids = set(profile.controls)
    for name in ("video_web", "speakers_web"):
        for control in profile.grammar[name].get("controls", []):
            assert control["id"] not in telnet_ids, f"{control['id']} exists on telnet; use telnet"
