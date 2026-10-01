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
# would let one wrong number vouch for the other; 2014's `XP_THRESHOLDS` has no golden
# of its own.
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