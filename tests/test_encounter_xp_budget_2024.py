"""test_encounter_xp_budget_2024.py: the 2024 XP budget table, every row, pinned.

WHY
===
`systems/dnd5e/encounter.py:66` holds `XP_BUDGET_2024`, 20 rows for levels 1 to 20 in
three columns (Low / Moderate / High) taken from 2024 DMG ch. 12. It is the one place in
this fork where a wrong number is not a wrong number but a difficulty verdict handed to
a GM with a confident-looking table beside it: `budget`, `rate` and the end-of-fight
rating all read straight off it, and nothing else in the tree is a second copy to
disagree with it.

The 2024 ruleset is a deliberate, bounded exception in a project that is otherwise 2014
only, and `systems/dnd5e/system.md:13-15` is explicit about how narrow it is: the
campaign `ruleset` selects the **XP budget table** for those three commands "because it
is a table lookup, not a rules engine. It does not select combat rules." Pinning the
table does not widen that exception. It is the cost of having made it.

WHAT IS ASSERTED, AND WHY IT IS ASSERTED THIS WAY
================================================
The numbers themselves, per row. "2024 differs from 2014" would prove almost nothing
here, because the two tables are not related by a rule anyone could restate: 2024 has
three tiers where 2014 has four (Easy / Medium / Hard / Deadly), and its Moderate
column is not the 2014 Medium column at 13 of the 20 levels. So each row is written
out and compared.

The existing 2024 golden, `tests/test_phase5_encounter_design.py:176-180`, does not
cover this and could not: it pins **level 1**, and at level 1 both editions say 50 for
their middle column. The rows a 2014-shaped golden would miss are the ones the editions
diverge hardest on, L5 (400 against 500), L13 (2400 against 2200) and L20 (6400 against
5700), and those are named and pinned explicitly at the end of this file.

The key set is asserted separately, and deliberately tied to the level keys rather than
to the values: hardcoding 20 tuples and checking them one at a time still passes on a
table that lost level 7 and gave level 8 a second row, because every value present is
a value somebody wrote down. A drop and a duplicate are two of the three mutations this
file is required to catch, so the shape is pinned in its own right.

These figures are copied exactly as `encounter.py` has them. The roadmap records that
all 20 rows were spot-checked against 2024 DMG ch. 12 and are correct, so this is a pin
against silent drift, not a licence to "correct" the table. A golden copied out of the
thing it pins cannot detect the table having been wrong on day one, only wrong later.
"""
from __future__ import annotations

import pytest

import systems.dnd5e.encounter as encounter_design

# 2024 DMG ch. 12, encounter XP budget per character per level: Low / Moderate / High.
# Transcribed from systems/dnd5e/encounter.py:66-86, not from the 2014 tables, and not
# derived from any other row: every number below stands on its own.
XP_BUDGET_2024_GOLDEN = {
    1:  (25,   50,   100),
    2:  (50,   75,   150),
    3:  (75,   150,  225),
    4:  (125,  250,  375),
    5:  (250,  400,  750),
    6:  (300,  500,  1100),
    7:  (350,  750,  1400),
    8:  (450,  1000, 1900),
    9:  (550,  1100, 2400),
    10: (600,  1400, 2800),
    11: (800,  1600, 3600),
    12: (1000, 2000, 4500),
    13: (1100, 2400, 5400),
    14: (1250, 2800, 6300),
    15: (1400, 3200, 7100),
    16: (1600, 3900, 8400),
    17: (2000, 4500, 10000),
    18: (2100, 5000, 11400),
    19: (2400, 5700, 12800),
    20: (2800, 6400, 14400),
}

# 2024 Moderate against 2014 Medium, at the levels where the editions part company
# hardest. Both figures are written out rather than read from `systems/dnd5e/xp.py`,
# because comparing the table under test against a second table that nothing else pins
# would let one wrong number vouch for the other. The 2014 column has a golden of its
# own now, in tests/test_xp_thresholds_2014.py, which checks all twenty rows and the
# column order; it is still written out here rather than imported, so that this file
# states the comparison it exists to make rather than depending on the other file
# being run for the point to land.
DIVERGENT_MODERATE_VS_MEDIUM = [
    (5,  400, 500),
    (13, 2400, 2200),
    (20, 6400, 5700),
]

# One case per level, with the level itself as the test id: a red run should say
# `test_xp_budget_2024_row[5]` and name the level, not `row4`.
_GOLDEN_CASES = sorted(XP_BUDGET_2024_GOLDEN.items())


@pytest.mark.parametrize("level,row", _GOLDEN_CASES,
                         ids=[str(level) for level, _ in _GOLDEN_CASES])
def test_xp_budget_2024_row(level, row):
    """Every row of the 2024 budget, Low / Moderate / High, exactly as printed."""
    assert encounter_design.XP_BUDGET_2024.get(level) == row


def test_every_level_1_through_20_is_present_exactly_once():
    """The keys are the levels 1 to 20 and nothing else, and every row is Low/Mod/High.

    Separate from the golden on purpose. A per-value check cannot see a level that
    went missing or a row that got copied onto its neighbour, because in both cases
    every value it does look at is still a value somebody wrote down.
    """
    budget = encounter_design.XP_BUDGET_2024
    assert sorted(budget) == list(range(1, 21))
    for level in range(1, 21):
        row = budget[level]
        # `tuple` rather than any sequence: the declared type is
        # `dict[int, tuple[int, int, int]]`, and `_row()` copies it with `list(...)`,
        # so a list would work at runtime while no longer being what the module says.
        assert isinstance(row, tuple) and len(row) == 3, f"level {level} is {row!r}"
        assert all(isinstance(v, int) and not isinstance(v, bool) for v in row), \
            f"level {level} is not three ints: {row!r}"


@pytest.mark.parametrize(
    "level,moderate_2024,medium_2014", DIVERGENT_MODERATE_VS_MEDIUM,
    ids=[f"L{level}-2024-{m24}-not-2014-{m14}" for level, m24, m14 in DIVERGENT_MODERATE_VS_MEDIUM])
def test_the_moderate_column_is_not_the_2014_medium_column(level, moderate_2024, medium_2014):
    """The rows a 2014-shaped golden would mistake for the 2014 table.

    This is the whole argument for pinning 2024 separately: at these three levels the
    2024 Moderate figure is not the 2014 Medium figure, and swapping one for the other
    is the mistake the table's own module docstring warns about. Level 1, where the
    existing golden sits, agrees across editions at 50 and would not notice.
    """
    row = encounter_design.XP_BUDGET_2024[level]
    assert row[1] == moderate_2024
    assert row[1] != medium_2014


# ── no monster-count multiplier, at the counts where 2014 has one ────────────

# Every band edge in `xp.MONSTER_MULTIPLIERS` except 1. One monster is x1 in 2014,
# so a count of 1 would pass whether or not the 2024 branch existed. These are the
# counts at which 2014 multiplies the group's XP, which is exactly where a 2024
# rating that quietly multiplied would be wrong by a factor the GM cannot see.
# encounter.py:144-153 is where the two rulesets part company.
MULTIPLIED_COUNTS = (2, 3, 6, 7, 10, 11, 14, 15, 20)


@pytest.mark.parametrize("count", MULTIPLIED_COUNTS)
def test_2024_has_no_multiplier_where_2014_would_multiply(count):
    """`multiplier_for` answers None for 2024 at every count 2014 multiplies.

    Before this there was no test of the 2024 branch of that function anywhere in
    the suite, so deleting it -- making 2024 fall through to the 2014 table --
    left every test green while `rate` handed a GM a 2024 encounter difficulty
    computed from a multiplier the 2024 rules do not have.
    """
    assert encounter_design.multiplier_for(count, "2024") is None
    # ...and 2014 really does multiply at these counts, so the line above is
    # asserting a difference rather than a coincidence.
    assert encounter_design.multiplier_for(count, "2014") > 1.0


# The caller's guard, and why the assertions below are about the arithmetic.
#
# `rate` is `mult = multiplier_for(count, ruleset); adjusted = int(raw * mult) if
# mult else raw`. Rewriting that guard as `if mult is not None` is an EQUIVALENT
# mutation and no test can kill it: no multiplier `_monster_multiplier` can return
# is 0, so the two guards take the same branch for every input. It is recorded
# here rather than papered over with a test pretending to tell them apart, and
# the tests assert the thing that is actually the contract: the ARITHMETIC a 2024
# rating produces and the multiplier field the GM is shown. Those kill every
# mutation that changes behaviour. The one that does not is not a defect.
GOBLIN = {"goblin": {"cr": "1/4", "xp": 50, "name": "Goblin"}}


@pytest.mark.parametrize("count", MULTIPLIED_COUNTS)
def test_a_2024_rating_multiplies_nothing(count):
    """The shipped caller, at a count 2014 would have multiplied.

    `known` supplies the CR and XP so the rating never touches the SRD dataset,
    which is gitignored and absent in a clean worktree.
    """
    rated = encounter_design.rate([("goblin", count)], [1], "2024", known=GOBLIN)
    assert rated["count"] == count
    assert rated["raw"] == 50 * count
    assert rated["multiplier"] is None
    assert rated["adjusted"] == rated["raw"], f"2024 multiplied {count} goblins: {rated}"


def test_the_same_group_is_multiplied_in_2014_and_not_in_2024():
    """The two rulesets, side by side, on one group.

    The contrast is the point. Asserted separately, "2024 does not multiply" and
    "2014 does" are two facts a later edit could quietly stop both being true of;
    asserted together, a multiplier that turns up in the 2024 path is a failure
    that says which of the two changed.
    """
    group = [("goblin", 6)]
    fourteen = encounter_design.rate(group, [1], "2014", known=GOBLIN)
    twentyfour = encounter_design.rate(group, [1], "2024", known=GOBLIN)
    assert (fourteen["raw"], fourteen["multiplier"], fourteen["adjusted"]) == (300, 2.0, 600)
    assert (twentyfour["raw"], twentyfour["multiplier"], twentyfour["adjusted"]) == (300, None, 300)


def test_a_2024_rating_divides_among_the_party_and_not_by_a_multiplier():
    """The per-character figure, which is what the difficulty is read against.

    300 XP for six goblins is 150 each to a party of two, and the 2024 High
    budget for level 1 is 100, so the verdict is High. Read against a 600 XP
    figure the same fight would be six times the top of a table with no tier
    above it to say so. The multiplier is not a display detail: it is the
    difference between a verdict and a shrug.
    """
    rated = encounter_design.rate([("goblin", 6)], [1, 1], "2024", known=GOBLIN)
    assert rated["party_size"] == 2
    assert rated["per_character"] == 150
    assert rated["difficulty"] == "high"
    assert rated["thresholds"] == list(encounter_design.XP_BUDGET_2024[1])


def test_the_multiplier_bands_are_printed_for_2014_and_not_for_2024():
    """What `budget` hands the GM to print.

    A separate surface from `multiplier_for`: `budget` carries the band list so
    the GM can see the x2 that 2014 applies, and a 2024 budget that printed the
    bands would be claiming 2024 has them.
    """
    assert encounter_design.budget([1], "2024")["multiplier"] == []
    bands = encounter_design.budget([1], "2014")["multiplier"]
    assert bands and bands[0] == [1, 1.0], bands
