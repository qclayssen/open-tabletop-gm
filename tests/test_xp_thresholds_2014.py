"""test_xp_thresholds_2014.py: the 2014 encounter difficulty table, every row, pinned.

WHY
===
`systems/dnd5e/xp.py:41` holds `XP_THRESHOLDS`, 20 rows for levels 1 to 20 in
four columns (Easy / Medium / Hard / Deadly) from 2014 DMG ch. 9. It is the
table every 2014 encounter is rated against, and it had no golden of its own.
`tests/test_encounter_xp_budget_2024.py` pinned the 2024 replacement row for
row (#167), and it hand-writes three 2014 Medium figures (500, 2200, 5700) to
show the editions part company at levels 5, 13 and 20. Those three numbers had
nothing to check themselves against: a wrong 2014 Medium figure would have made
that test's point about the editions differing for a reason that was itself
wrong, and nothing in the suite would have said so.

The same shape of gap is the reason the column ORDER is pinned here and not just
the twenty rows. A transposed table still has 20 rows of 4 numbers, every one of
them a value somebody wrote down, and a GM reading one is told that 25 XP per
player is a Deadly encounter.

WHAT IS ASSERTED, AND WHY IT IS ASSERTED THIS WAY
===============================================
The numbers per row, then the order, then the boundaries.

The order is asserted through the two functions that consume it rather than by
hand: `xp._xp_per_player("deadly", 1)` is what `xp.py calc --difficulty deadly
--level 1` prints to a GM, and `xp._classify(25, 1)` is what `xp.py award` uses
to label what it just awarded. A test that asserted the order only against a
literal would keep passing if the mapping from name to column were the thing
that broke, which is the more likely edit.

The boundaries are asserted at every level and at every edge: one XP below a
threshold is the tier below it, and a threshold exactly is its own tier. A row
that is internally out of order (a Hard below its Medium) cannot produce that,
so the transposed table and the mis-ordered row are both caught here and not
only by reading the numbers.

These figures are copied exactly as `xp.py` has them. The roadmap records the
2014 table as spot-checked against DMG ch. 9, so this is a pin against silent
drift, not a licence to "correct" it. A golden copied out of the thing it pins
cannot detect the table having been wrong on day one, only wrong later.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

import systems.dnd5e.encounter as encounter_design

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str, path: str):
    """Load a system script by path, the way the sibling loader in encounter.py does.

    `systems/dnd5e/` is a directory of scripts rather than an installed package
    (there is no `__init__.py`), so `import systems.dnd5e.xp` is not a thing one
    can do. The module name is unique per load so two tests holding different
    copies of `xp.py` cannot share one through `sys.modules`.
    """
    spec = importlib.util.spec_from_file_location(f"_xpgolden_{name}", ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


xp = _load("xp", "systems/dnd5e/xp.py")
character = _load("character", "systems/dnd5e/character.py")

# 2014 DMG ch. 9, encounter difficulty thresholds per character per level:
# Easy / Medium / Hard / Deadly.
# Transcribed from systems/dnd5e/xp.py:41-62, not from the 2024 table, and not
# derived from any other row: every number below stands on its own.
XP_THRESHOLDS_2014_GOLDEN = {
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

#: The four column names, in the order the table is written and in the order
#: `encounter.TIERS["2014"]` names them. Every order assertion below is this
#: list applied to something.
TIERS_2014 = ("easy", "medium", "hard", "deadly")

#: Total XP required to REACH each level. This is a different table from the one
#: above, and the two have been confused before: a threshold is what ONE encounter
#: costs, this is what a whole career costs. It is duplicated in this repository
#: under two names -- `xp.LEVEL_XP` and `character.XP_THRESHOLDS` -- and nothing
#: checked that the two copies agreed. `xp.py award` prints a pending level-up
#: against one and `character.py do_xp` decides whether there is one against the
#: other, so a GM who awards 14000 XP can be told "level up pending" by one
#: command and "15000 more to level 5" by the other, with both correct.
#:
#: Note the name collision with the table at the top of this file: the encounter
#: difficulty table is `xp.XP_THRESHOLDS` and this one is `character.XP_THRESHOLDS`.
#: Two tables, two names, one being a misnomer for the other. Both are pinned
#: against this single golden rather than being reconciled, because reconciling
#: them is a change to how a campaign's sheet is read and is not this test's
#: decision to make.
TOTAL_XP_GOLDEN = {
    1: 0,       2: 300,    3: 900,    4: 2700,   5: 6500,
    6: 14000,   7: 23000,  8: 34000,  9: 48000,  10: 64000,
    11: 85000,  12: 100000, 13: 120000, 14: 140000, 15: 165000,
    16: 195000, 17: 225000, 18: 265000, 19: 305000, 20: 355000,
}

_ROW_CASES = sorted(XP_THRESHOLDS_2014_GOLDEN.items())


# ── the encounter difficulty table ────────────────────────────────────────────

@pytest.mark.parametrize("level,row", _ROW_CASES, ids=[str(l) for l, _ in _ROW_CASES])
def test_xp_threshold_2014_row(level, row):
    """Every row of the 2014 thresholds, Easy / Medium / Hard / Deadly."""
    assert xp.XP_THRESHOLDS.get(level) == row


def test_every_level_1_through_20_is_present_exactly_once():
    """The keys are the levels 1 to 20 and nothing else, four ints each.

    Separate from the golden on purpose: a per-value check cannot see a level
    that went missing or a row copied onto its neighbour, because in both cases
    every value it does look at is still a value somebody wrote down.
    """
    table = xp.XP_THRESHOLDS
    assert sorted(table) == list(range(1, 21))
    for level in range(1, 21):
        row = table[level]
        assert isinstance(row, tuple) and len(row) == 4, f"level {level} is {row!r}"
        assert all(isinstance(v, int) and not isinstance(v, bool) for v in row), \
            f"level {level} is not four ints: {row!r}"


def test_the_columns_are_easy_medium_hard_deadly_in_that_order():
    """The order, through the function a GM's `--difficulty` argument reaches.

    Twenty rows of four numbers cannot catch a transposition: every value is
    still a value somebody wrote down. What catches it is the mapping from the
    name a GM types to the column it reads, which is `DIFF_IDX` and nothing
    else. A table with its columns reversed prints "Easy 100" and awards it.
    """
    for level, row in XP_THRESHOLDS_2014_GOLDEN.items():
        named = [xp._xp_per_player(tier, level) for tier in TIERS_2014]
        assert named == list(row), f"level {level}: {dict(zip(TIERS_2014, named))}"


def test_the_tier_boundaries_are_where_the_table_says_they_are():
    """One XP under a threshold is the tier below it; the threshold is its own.

    Driven through `_classify`, which is what `xp.py award` labels an award
    with. This is the assertion that a transposed table cannot survive, and it
    is stated at every level and every edge rather than sampled: a row whose own
    four numbers are out of order still classifies a figure into the wrong tier
    while every literal in the golden above remains satisfied.
    """
    for level, row in XP_THRESHOLDS_2014_GOLDEN.items():
        below = [(row[i] - 1, TIERS_2014[i - 1] if i else "trivial")
                 for i in range(4)]
        for figure, want in below:
            assert xp._classify(figure, level) == want, \
                f"level {level}: {figure} XP should be {want}, not {xp._classify(figure, level)}"
        for i, tier in enumerate(TIERS_2014):
            assert xp._classify(row[i], level) == tier, \
                f"level {level}: {row[i]} XP is not {tier}"
        # Over the top: the highest threshold is the top of the table, and there
        # is no tier above it.
        assert xp._classify(row[3] + 1, level) == "deadly", f"level {level}"


def test_the_easy_and_2024_low_tiers_are_not_being_confused():
    """The three 2014 Medium figures that test_encounter_xp_budget_2024.py writes out.

    That file's whole argument is that the editions part company hardest at
    levels 5, 13 and 20, and it quotes 2014 to do it. Those quotes were
    unchecked; they are checked here, in the file that owns the 2014 table, so
    that a correction here is the correction both files then agree on.
    """
    for level, medium_2014 in ((5, 500), (13, 2200), (20, 5700)):
        assert xp.XP_THRESHOLDS[level][1] == medium_2014, f"level {level}"
        assert xp._xp_per_player("medium", level) == medium_2014, f"level {level}"


# ── the encounter path reads the same row ─────────────────────────────────────

@pytest.mark.parametrize("level", list(range(1, 21)))
def test_the_encounter_tools_read_the_2014_row(level):
    """`budget()` and `rate()` go through `encounter._row`, not through xp.py's
    own lookups, so a row that is right in xp.py and read wrong here would rate
    a fight from a table nobody can see."""
    assert encounter_design._row("2014", level) == list(XP_THRESHOLDS_2014_GOLDEN[level])
    budget = encounter_design.budget([level], "2014")
    assert budget["per_character"] == list(XP_THRESHOLDS_2014_GOLDEN[level])
    assert budget["tiers"] == ["Easy", "Medium", "Hard", "Deadly"]


def test_a_figure_is_classified_the_same_way_by_both_rules_modules():
    """`_classify` in xp.py and `classify` in encounter.py, at every edge.

    Two copies of one rule. `xp.py award` uses the first and `combat.py rate`
    uses the second, so a GM can award a "Medium" and then be told by the rating
    tool that the same encounter was Easy. They are checked against each other
    at the same figures rather than against a third golden, because the two
    answers are what has to agree.
    """
    for level, row in XP_THRESHOLDS_2014_GOLDEN.items():
        for figure in (row[0] - 1, *row, row[3] + 1):
            assert encounter_design.classify(figure, row, "2014") == xp._classify(figure, level), \
                f"level {level}, {figure} XP: xp.py says {xp._classify(figure, level)}, " \
                f"encounter.py says {encounter_design.classify(figure, row, '2014')}"


# ── total XP to reach a level: one golden, both of its names ─────────────────

@pytest.mark.parametrize("level,total", sorted(TOTAL_XP_GOLDEN.items()),
                         ids=[str(l) for l in sorted(TOTAL_XP_GOLDEN)])
def test_total_xp_to_reach_a_level_xp_award(level, total):
    """`xp.LEVEL_XP`, which is what an award compares against."""
    assert xp.LEVEL_XP.get(level) == total


@pytest.mark.parametrize("level,total", sorted(TOTAL_XP_GOLDEN.items()),
                         ids=[str(l) for l in sorted(TOTAL_XP_GOLDEN)])
def test_total_xp_to_reach_a_level_character_sheet(level, total):
    """`character.XP_THRESHOLDS`, which is what `character.py xp` compares against.

    Same table, same golden, different file and a different name. See the note
    on TOTAL_XP_GOLDEN: the two are pinned rather than merged.
    """
    assert character.XP_THRESHOLDS.get(level) == total


def test_the_two_copies_of_the_total_table_agree():
    """The check that was missing, and the whole reason there is one golden.

    A drop, an edit or a transposed pair in either copy leaves the other file's
    tests green, because each file only ever looked at its own dict. This one
    compares them, so a divergence is a failure here rather than a GM being
    told two different things by two commands in the same session.
    """
    assert dict(xp.LEVEL_XP) == dict(character.XP_THRESHOLDS)
    assert dict(xp.LEVEL_XP) == dict(TOTAL_XP_GOLDEN)


def test_the_award_and_the_sheet_agree_on_where_the_next_level_is(capsys):
    """Driven through both commands, because that is where the two meet a player.

    `xp._next_level_xp` is what an award prints as the denominator on a sheet
    and as the "N XP to Level M" note; `character.do_xp` is what prints "Level
    up available" or "N XP remaining". Same award, same level, and the two must
    not disagree about the figure.
    """
    for level in (1, 4, 9, 19):
        want = TOTAL_XP_GOLDEN[level + 1]
        assert xp._next_level_xp(level) == want, f"xp.py at level {level}"
        character.do_xp(["--level", str(level), "--gained", "10"])
        out = capsys.readouterr().out
        assert str(want) in out, \
            f"character.py at level {level} did not name {want}: {out}"
        assert "Level up available" not in out, \
            f"10 XP took a level-{level} character to level {level + 1}: {out}"


def test_the_level_up_figure_is_the_same_one_both_sides_act_on(capsys):
    """The figure is not only printed, it is acted on, at every level.

    `do_xp` announces a level-up at or above the threshold and reports the
    remainder below it, and it reads the same dict `xp._write_xp` compares
    against before it writes "LEVEL UP PENDING" onto a sheet. A table that
    disagreed at exactly the level-up figure would award XP, print "N XP
    remaining" and print "Level up available" in the same breath, which is the
    shape of bug a table golden cannot see on its own.

    Driven through `do_xp` at every level rather than at a sample: the two dicts
    are compared directly in the test above, and this says the dict they share
    is the one whose edges decide the level.

    `do_xp --gained` is the amount added to the CURRENT level's total, not a
    running total from zero, so the two edges are the gap between consecutive
    levels and one less than it.
    """
    for level in range(1, 20):
        current, want = TOTAL_XP_GOLDEN[level], TOTAL_XP_GOLDEN[level + 1]
        gap = want - current
        character.do_xp(["--level", str(level), "--gained", str(gap)])
        at = capsys.readouterr().out
        assert "Level up available" in at, \
            f"{gap} XP at level {level} is exactly the level {level + 1} " \
            f"threshold and was not announced as one: {at}"
        character.do_xp(["--level", str(level), "--gained", str(gap - 1)])
        just_under = capsys.readouterr().out
        assert "Level up available" not in just_under, \
            f"{gap - 1} XP is one short of level {level + 1} and announced a level up: {just_under}"
        assert "1 XP remaining" in just_under, just_under
