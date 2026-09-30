"""The SRD's crit notation must survive the whole trip from sheet to roll.

A character sheet writes a crit the way the SRD does -- "1d6 (1d8 crit)" -- and
that is correct as written. It used to be flattened into the dice string by the
same replace(" ", "") that tidies "2d6 + 1", arriving at the roller as
"1d6(1d8crit)". scripts/dice.py does not accept the annotation, so
`average_damage` -- called while the AI weighs which enemy is worth an
opportunity attack -- raised ValueError straight through the REPL and ended the
session mid-fight. Nothing in the campaign was wrong; the notation was right and
the parser was narrow.

Two things have to hold, and the second is the one that is easy to get wrong
while fixing the first:

  1. Nothing crashes. An unparseable damage string is a bad pick for a
     heuristic, not a reason to end the game.
  2. The crit is the SRD's crit. "1d6(1d8crit)" crits for 1d8. Doubling the
     base instead -- which is what an unannotated string does -- would make
     every annotated crit hit harder than the statblock says, and that is an
     error the table has no way to notice.
"""
from __future__ import annotations

import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "systems" / "dnd5e"))

from tactics import roller  # noqa: E402


def test_split_crit_reads_both_spellings():
    assert roller.split_crit("1d6(1d8crit)") == ("1d6", "1d8")
    assert roller.split_crit("1d6 (1d8 crit)") == ("1d6", "1d8")
    assert roller.split_crit("2d6+3(2d12crit)") == ("2d6+3", "2d12")


def test_an_ordinary_string_has_no_crit_of_its_own():
    assert roller.split_crit("2d6+3") == ("2d6+3", None)
    assert roller.split_crit("1") == ("1", None)


def test_average_reads_the_base_not_the_annotation():
    # 3.5, the mean of a d6. Reading "1d8" here would have quietly made the
    # engine prefer a weapon over one that hits harder.
    assert roller.average("1d6(1d8crit)") == 3.5
    assert roller.average("1d6") == 3.5


def test_average_never_raises():
    # Every caller is a heuristic. One unreadable string must not be able to
    # take the session down with it.
    assert roller.average("banana") == 0.0
    assert roller.average("") == 0.0
    assert roller.average(None) == 0.0


def test_a_crit_rolls_the_srd_crit_not_twice_the_base():
    r = roller.Roller(rng=random.Random(1))
    assert r.roll("1d6(1d8crit)", "T", "d").notation == "1d6"
    assert r.roll("1d6(1d8crit)", "T", "d", crit=True).notation == "1d8"


def test_an_unannotated_crit_still_doubles_the_dice():
    r = roller.Roller(rng=random.Random(1))
    assert r.roll("2d6+3", "T", "d", crit=True).notation == "4d6+3"


def test_a_sheet_row_keeps_the_crit_out_of_the_dice_string():
    import tactics_sheet

    spec = tactics_sheet._attack(["Rapier", "+4", "1d6 (1d8 crit)", "piercing", ""])
    assert spec["damage"][0]["dice"] == "1d6"          # not "1d6(1d8crit)"
    assert spec["damage"][0]["crit_dice"] == "1d8"
    # And it has to survive a round trip through the engine's own averaging.
    assert roller.average(spec["damage"][0]["dice"]) == 3.5


def test_a_sheet_row_without_a_crit_is_unchanged():
    import tactics_sheet

    spec = tactics_sheet._attack(["Shortbow", "+4", "1d6 + 2", "piercing", "80/320"])
    assert spec["damage"][0]["dice"] == "1d6+2"
    assert "crit_dice" not in spec["damage"][0]
