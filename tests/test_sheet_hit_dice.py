"""Hit Dice on a parsed character sheet: parse, rest, and write-back round trip.

The older rest tests build the hit_dice dict by hand; these go through the real
sheet parser, which is where the `5d6` read as a die (and total 1) bug lived.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "systems" / "dnd5e"))


import tactics_sheet  # noqa: E402
from tactics import rest  # noqa: E402
from tactics.state import Encounter  # noqa: E402

SHEET = """# Kairos

- **Class:** Wizard
- **Level:** 5
- **HP:** 8 / 8
- **AC:** 12
- **Speed:** 30 ft.
- **Hit Dice:** {hd}
"""


def _token(hd):
    return tactics_sheet.read_sheet(SHEET.format(hd=hd), "kairos", (0, 0))


def _enc(token):
    enc = Encounter(campaign="t", grid={"name": "t", "rows": ["." * 6] * 6}, tokens={})
    enc.tokens[token.id] = token
    return enc


def test_the_template_hit_dice_line_parses_die_total_and_remaining():
    t = _token("5d6 (remaining: 2)")
    assert t.extra["hit_dice"] == {"die": "d6", "total": 5, "remaining": 2}


@pytest.mark.parametrize("text,expected", [
    ("5d6", {"die": "d6", "total": 5, "remaining": 5}),
    ("2/5 d6", {"die": "d6", "total": 5, "remaining": 2}),
    ("2/5d8", {"die": "d8", "total": 5, "remaining": 2}),
    ("3d8 + 2d10 (remaining: 4)", {"die": "d8", "total": 5, "remaining": 4}),
    ("5d6 (remaining: 9)", {"die": "d6", "total": 5, "remaining": 5}),
    ("Xd[Y] (remaining: X)", None),
])
def test_hit_dice_variants(text, expected):
    assert tactics_sheet.parse_hit_dice(text) == expected


def test_long_rest_from_a_parsed_sheet_uses_the_real_total():
    t = _token("5d6 (remaining: 2)")
    lines = rest.long_rest(_enc(t), None)
    assert "Kairos: 3 Hit Dice restored (now 5/5)." in lines
    assert t.extra["hit_dice"]["remaining"] == 5


def test_long_rest_restores_half_and_caps_at_total():
    t = _token("5d6 (remaining: 0)")
    rest.long_rest(_enc(t), None)
    assert t.extra["hit_dice"]["remaining"] == 3


@pytest.mark.parametrize("hd,after", [
    ("5d6 (remaining: 2)", "5d6 (remaining: 5)"),
    ("2/5 d6", "5/5 d6"),
    ("5d6", "5d6 (remaining: 5)"),
])
def test_write_back_after_a_long_rest_round_trips(hd, after):
    text = SHEET.format(hd=hd)
    t = tactics_sheet.read_sheet(text, "kairos", (0, 0))
    rest.long_rest(_enc(t), None)
    out = tactics_sheet.write_back(text, t)
    assert f"**Hit Dice:** {after}" in out
    again = tactics_sheet.read_sheet(out, "kairos", (0, 0))
    assert again.extra["hit_dice"] == t.extra["hit_dice"]


def test_write_back_leaves_an_unfilled_template_line_alone():
    text = SHEET.format(hd="Xd[Y] (remaining: X)")
    t = tactics_sheet.read_sheet(text, "kairos", (0, 0))
    assert tactics_sheet.write_back(text, t) == text
