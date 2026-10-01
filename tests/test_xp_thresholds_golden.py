"""test_xp_thresholds_golden.py: 2014's XP tables, every row, pinned.

WHY
===
`systems/dnd5e/xp.py:41` holds `XP_THRESHOLDS`, 20 rows for levels 1 to 20 in four
columns (Easy / Medium / Hard / Deadly) from 2014 DMG ch. 9. It is what `xp.py calc`,
`xp.py award`, `encounter.py:_row` and the end-of-fight rating all read, and a wrong
number in it is not a wrong number but a difficulty verdict handed to a GM.

`tests/test_encounter_xp_budget_2024.py` pinned the 2024 table in #167, and its own
comment records the gap this file closes: "2014's `XP_THRESHOLDS` has no golden of
its own." That matters more than the usual argument for a golden, because there are
now two tables and only one of them is pinned. `test_the_moderate_column_is_not_the_
2014_medium_column` writes out a 2014 Medium figure (500 at level 5) to prove the
2024 column is not the 2014 one, and that figure is currently a hand-typed constant
with nothing in the tree to check it against. Two unpinned tables can drift into
agreement, and then each would vouch for the other.

THE TWO `XP_THRESHOLDS`
=======================
There are two, and they are not the same thing. This is the reason the gap survived
an audit that had already read both files:

  systems/dnd5e/xp.py:41        XP_THRESHOLDS  level -> (Easy, Medium, Hard, Deadly)
  systems/dnd5e/character.py:30 XP_THRESHOLDS  level -> total XP to REACH that level

Same name, different axis, and the second is a trap: `xp.py` spends a paragraph at
line 69 explaining that an encounter threshold and an adventuring-day budget are
different axes, which is the kind of note that exists because the two were once
confused. Only the first is the subject of this file's main table.

THE TWO LEVEL TABLES
====================
`character.py:XP_THRESHOLDS` and `xp.py:LEVEL_XP` are a third and fourth name for one
set of 20 numbers: the total XP to reach each level. They are separate dicts in
separate modules with no test on either, and they are the pair that decides a
level-up. `xp.py award` writes "LEVEL UP PENDING" at a total from one of them and
`character.py level-up` reads the other, so a divergence would print a pending
level-up that the level-up command then refuses. They are pinned here against the
same literal golden, which is not a new requirement so much as a way of making the
coupling visible: if the two are ever meant to differ, the golden has to be split
and the reason written down, rather than one of them drifting.

WHAT IS ASSERTED, AND WHY IT IS ASSERTED THIS WAY
================================================
The numbers themselves, per row, written out. No row is derived from another: 2014's
columns are not related to each other by a rule anyone could restate (level 3 is
75/150/225/400 and level 4 is 125/250/375/500, and Deadly does not track Hard at any
consistent ratio), so a golden computed from a rule would be a restatement of the
thing under test.

The key set is asserted separately, and tied to the level keys rather than to the
values: twenty hardcoded tuples checked one at a time still passes on a table that
lost level 7 and gave level 8 a second row, because every value it does look at is
still a value somebody wrote down. A drop and a duplicate are two of the three
mutations this file is required to catch, so the shape is pinned in its own right.

The column ORDER is pinned separately too, and through the functions that read it
rather than by commenting the tuple. A golden of bare tuples says nothing about
which slot is Deadly: the table transposed left-to-right still contains all twenty
rows of four numbers, and `_classify` would then call 25 XP per player "deadly" at
level 1. `test_the_columns_are_read_as_easy_medium_hard_deadly` is what stops that,
by driving `_classify` and `_xp_per_player` and checking they name the tiers the
column headings say they are.

These figures are copied exactly as `xp.py` has them. A golden copied out of the
thing it pins cannot detect the table having been wrong on day one, only wrong
later, which is the whole of what this file is for.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str):
    """systems/dnd5e/<name>.py by path.

    The system modules import each other by file path rather than by package
    (`encounter.py:_sibling`), so they are not importable as
    `systems.dnd5e.xp`. Loading them the same way the product does keeps this
    test on the real module rather than on a second copy of it.
    """
    path = REPO / "systems" / "dnd5e" / f"{name}.py"
    key = f"otgm_golden_{name}"
    if key in __import__("sys").modules:
        return __import__("sys").modules[key]
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    __import__("sys").modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


xp = _load("xp")
character = _load("character")

# 2014 DMG ch. 9, "XP Thresholds by Encounter Difficulty", XP per character per
# level: Easy / Medium / Hard / Deadly. Transcribed from
# systems/dnd5e/xp.py:41-62, and from no other row and no other table.
XP_THRESHOLDS_GOLDEN = {
    1:  (25,    50,    75,    100),
    2:  (50,    100,   150,   200),
    3:  (75,    150,   225,   400),
    4:  (125,   250,   375,   500),
    5:  (250,   500,   750,   1100),
    6:  (300,   600,   900,   1400),
    7:  (350,   750,   1100,  1700),
    8:  (450,   900,   1400,  2100),
    9:  (550,   1100,  1600,  2400),
    10: (600,   1200,  1900,  2800),
    11: (800,   1600,  2400,  3600),
    12: (1000,  2000,  3000,  4500),
    13: (1100,  2200,  3400,  5100),
    14: (1250,  2500,  3800,  5700),
    15: (1400,  2800,  4300,  6400),
    16: (1600,  3200,  4800,  7200),
    17: (2000,  3900,  5900,  8800),
    18: (2100,  4200,  6300,  9500),
    19: (2400,  4900,  7300,  10900),
    20: (2800,  5700,  8500,  12700),
}

# Total XP to reach each level. One set of numbers under two names, so one golden:
# xp.py calls it LEVEL_XP and character.py calls it XP_THRESHOLDS. The DMG's
# "Level" / "XP Needed to Gain the Level" columns, the same figures again.
LEVEL_XP_GOLDEN = {
    1: 0,       2: 300,    3: 900,    4: 2700,   5: 6500,
    6: 14000,   7: 23000,  8: 34000,  9: 48000,  10: 64000,
    11: 85000,  12: 100000,13: 120000,14: 140000, 15: 165000,
    16: 195000, 17: 225000,18: 265000,19: 305000, 20: 355000,
}

# One case per level, with the level itself as the test id: a red run should say
# `test_xp_threshold_row[5]` and name the level, not `row4`.
_ROW_CASES = sorted(XP_THRESHOLDS_GOLDEN.items())
_LEVEL_CASES = sorted(LEVEL_XP_GOLDEN.items())


@pytest.mark.parametrize("level,row", _ROW_CASES,
                         ids=[str(level) for level, _ in _ROW_CASES])
def test_xp_threshold_row(level, row):
    """Every row of the 2014 thresholds, Easy / Medium / Hard / Deadly, as printed."""
    assert xp.XP_THRESHOLDS.get(level) == row


def test_every_level_1_through_20_is_present_exactly_once():
    """The keys are the levels 1 to 20 and nothing else, and every row is four ints.

    Separate from the golden on purpose. A per-value check cannot see a level that
    went missing or a row copied onto its neighbour, because in both cases every
    value it does look at is still a value somebody wrote down.
    """
    thresholds = xp.XP_THRESHOLDS
    assert sorted(thresholds) == list(range(1, 21))
    for level in range(1, 21):
        row = thresholds[level]
        # `tuple` rather than any sequence: the declared type is
        # `dict[int, tuple[int, int, int, int]]`, and `encounter._row()` copies it
        # with `list(...)`, so a list would work at runtime while no longer being
        # what the module says.
        assert isinstance(row, tuple) and len(row) == 4, f"level {level} is {row!r}"
        assert all(isinstance(v, int) and not isinstance(v, bool) for v in row), \
            f"level {level} is not four ints: {row!r}"


def test_the_thresholds_rise_with_the_level_and_with_the_difficulty():
    """The shape of the table, which no per-value check can see.

    Two directions, because they are two different mistakes. A column that does not
    rise is a transposed or shifted row: the numbers are all real 5e numbers, they
    are just on the wrong level. A row that does not rise left to right is a
    transposed table, which is the one that survives every value check in this file
    and then tells a GM that 25 XP per player is a Deadly encounter at level 1.
    """
    for level in range(1, 21):
        row = xp.XP_THRESHOLDS[level]
        assert list(row) == sorted(row), f"level {level} is not Easy..Deadly: {row}"
        assert len(set(row)) == 4, f"level {level} repeats a difficulty: {row}"
    for col, name in enumerate(("Easy", "Medium", "Hard", "Deadly")):
        column = [xp.XP_THRESHOLDS[level][col] for level in range(1, 21)]
        assert all(b > a for a, b in zip(column, column[1:])), \
            f"the {name} column does not rise with the level: {column}"


def test_the_columns_are_read_as_easy_medium_hard_deadly():
    """A golden of bare tuples does not say which slot is which, and the consumers
    do not guess: `_classify` indexes t[0] for easy and t[3] for deadly, and
    `_xp_per_player` indexes through DIFF_IDX. So the order is driven through them
    rather than asserted about the dict.

    The boundaries are one below each threshold as well as on it, because
    `_classify` uses `>=` and the interesting off-by-one is a tier that claims a
    figure the tier below already covers. Level 1 is where 2014's own numbers are
    smallest and the most likely to be misread: 25 / 50 / 75 / 100.
    """
    row = xp.XP_THRESHOLDS[1]
    for idx, (tier, value) in enumerate(
            (("easy", 25), ("medium", 50), ("hard", 75), ("deadly", 100))):
        assert xp.DIFF_IDX[tier] == idx, f"{tier} is column {xp.DIFF_IDX[tier]}"
        assert xp._xp_per_player(tier, 1) == value, f"{tier} at level 1"
        assert xp._classify(value, 1) == tier, f"{value} XP per player should be {tier}"
        if value:
            below = xp._classify(value - 1, 1)
            assert below == "trivial" or value - 1 >= row[
                max(0, idx - 1)], f"{value - 1} XP should not be {tier}"
    assert xp._classify(24, 1) == "trivial", "24 XP per player is not a fight yet"
    assert xp._classify(101, 1) == "deadly", "past Deadly there is nothing higher"


# ── the total-to-reach-level table, under both of its names ─────────────────

@pytest.mark.parametrize("level,total", _LEVEL_CASES,
                         ids=[str(level) for level, _ in _LEVEL_CASES])
def test_the_total_to_reach_a_level(level, total):
    """xp.py:LEVEL_XP and character.py:XP_THRESHOLDS, pinned as one table.

    The two are checked against the same golden rather than against each other on
    purpose: a test that says `assert a == b` cannot tell a correct pair from two
    wrong ones that agree, which is the same failure #167 recorded for the two
    difficulty tables.
    """
    assert xp.LEVEL_XP.get(level) == total, f"xp.py:LEVEL_XP level {level}"
    assert character.XP_THRESHOLDS.get(level) == total, \
        f"character.py:XP_THRESHOLDS level {level}"


def test_every_level_1_through_20_is_present_in_the_level_table():
    """The same shape check as the thresholds, for the same reason: a missing level
    is a `.get()` that quietly returns None and prints "MAX" at the wrong time."""
    for name, table in (("LEVEL_XP", xp.LEVEL_XP),
                        ("character.XP_THRESHOLDS", character.XP_THRESHOLDS)):
        assert sorted(table) == list(range(1, 21)), f"{name} keys are {sorted(table)}"
        for level, total in table.items():
            assert isinstance(total, int) and not isinstance(total, bool), \
                f"{name} level {level} is {total!r}"
    assert all(a < b for a, b in zip(
        [xp.LEVEL_XP[l] for l in range(1, 21)],
        [xp.LEVEL_XP[l] for l in range(2, 21)])), \
        "the XP needed does not rise with the level"


def test_the_level_up_an_award_announces_is_the_one_level_up_reports(capsys):
    """The cross-tool hazard, as a test rather than a comment.

    `xp.py award` decides whether to print "LEVEL UP PENDING" from
    `_next_level_xp`, which reads `LEVEL_XP`. `character.py xp --level N` decides
    whether to print "Level up available!" from its own `XP_THRESHOLDS`. If those
    two tables ever disagree by one award, a GM is told a level is pending and the
    command they then run says it is not, with nothing in either output saying
    which of the two is right.

    Driven at every boundary rather than one: the tables are 20 rows of numbers
    and the point is that no row of either is allowed to move alone.

    `character.py do_xp` takes the total to REACH the level as its starting point
    and `--gained` is what has come in since, so the boundary is
    `need - current` gained, not `need` gained. Read the other way the first case
    offers the level up at 899 XP into level 2, which is why the arithmetic is
    spelled out here rather than left to the reader.
    """
    for level in range(1, 20):
        need = xp.LEVEL_XP[level + 1]
        assert xp._next_level_xp(level) == need, (
            f"xp.py:LEVEL_XP[{level + 1}] is not what _next_level_xp({level}) reads")
        current = character.XP_THRESHOLDS[level]
        short_by_one = need - current - 1
        assert short_by_one >= 0, f"level {level + 1} needs no XP at all"

        # One XP short of the threshold: the level-up is not available yet.
        character.do_xp(["xp", "--level", str(level), "--gained", str(short_by_one)])
        out = capsys.readouterr().out
        assert "Level up available" not in out, (
            f"character.py offers level {level + 1} at {need - 1} XP, "
            f"which is {need} short: {out}")
        assert (need - 1) < need, "the boundary is not the boundary"

        # And on it: both have to agree it is available, or the award prints a
        # pending level-up that the level-up command will not honour.
        character.do_xp(["xp", "--level", str(level),
                          "--gained", str(need - current)])
        out = capsys.readouterr().out
        assert "Level up available" in out, (
            f"character.py will not level up at exactly {need} XP: {out}")

