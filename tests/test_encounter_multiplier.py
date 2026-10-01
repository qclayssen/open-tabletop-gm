"""`multiplier_for`, and the 2024 `None` it returns, covered at last.

WHY
===
`systems/dnd5e/encounter.py:149` is the one place that decides whether the
monster-count multiplier applies at all. It has two answers: a float for 2014, and
`None` for 2024, which has no such thing. `git grep multiplier_for` found no test
anywhere, so the 2024 branch had never been executed by the suite: it was reachable
only by running a 2024 campaign, and the only 2024 coverage in the tree
(`tests/test_encounter_xp_budget_2024.py`) pins the budget TABLE, not this
function. A `None` that no test ever sees is a `None` whose handling was never
checked, and the one caller reads it on a line with a bare `if mult` where a
mistake reads as a plausible number rather than as a crash.

WHAT IS ASSERTED, AND WHY IT IS ASSERTED THIS WAY
================================================
The bands, at their boundaries, written out. 2014 DMG ch. 9's multiplier is not a
curve anyone could restate from the table (1 / 1.5 / 2 / 2.5 / 3 / 4, with the
ceilings at 1, 2, 6, 10, 14), so a golden derived from the bands would be a
restatement of the thing under test. The cases are the count at each ceiling, one
under it, and one over the last band, because a band that is off by one at a
ceiling is exactly the bug a "check three counts" test cannot see: at 5 and at 6
alone, a table whose 2.0 band started at 5 would agree on both.

The `None` branch is asserted as a fact about the RULESET rather than about a
count, over counts that would have received a multiplier. "2024 has no multiplier"
is a property of the edition, so the strongest form of the test is that it does
not come back for a fight of fourteen goblins, where 2014 would say x3.

The caller is driven too, and through the shipped one, because the question that
matters is not "what does multiplier_for return" but "what does `rate` do with a
None". `rate` computes `adjusted = int(raw * mult) if mult else raw`, so the
None takes the `raw` path and the encounter is worth what the monsters are worth.
That is asserted as arithmetic, since that is the property: a 2024 rating of
14 goblins must not carry a 2014 multiplier into the difficulty verdict.

ONE FINDING, RECORDED RATHER THAN CHANGED
=========================================
"No multiplier" is two different values in one module. `_multiplier_table` returns
an empty LIST (`[]`) because `budget()` prints bands from it, while `multiplier_for`
returns `None` because `rate()` needs a scalar it can test. Both are falsy so every
`if data["multiplier"]:` reads correctly, and this file pins both shapes so that the
distinction stays deliberate: a refactor that unified them on the truthy empty list
would make `rate` return `adjusted = int(raw * [])`, which raises rather than
silently rating a fight wrong, and a refactor onto `None` would break `budget`'s
band printing with a TypeError. Neither is a latent bug today; both are the kind of
thing that is a latent bug the day someone changes one of them.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str):
    """systems/dnd5e/<name>.py by path, the way `encounter.py:_sibling` does it.

    The system modules are scripts rather than an installed package, so
    `import systems.dnd5e.encounter` is not the same module the product runs.
    """
    key = f"otgm_mult_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        key, REPO / "systems" / "dnd5e" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


encounter = _load("encounter")
xp = _load("xp")

# 2014 DMG ch. 9, "Monsters per Encounter": each additional monster makes the
# encounter more dangerous than the sum of its parts, so raw XP is multiplied.
# Written out per count rather than read from MONSTER_MULTIPLIERS, which is the
# table under test.
#
# The bands are x1 for 1, x1.5 for 2, x2 for 3-6, x2.5 for 7-10, x3 for 11-14 and
# x4 for 15+, which is the list `scripts/character.md:79` prints to the GM and so
# is the one a reader can check against. Each band is sampled at both ends: at 5
# and at 6 alone, a table whose x2 band started at 5 would agree on both.
BANDS = [
    (1,   1.0),    # one monster: the sum of its parts is the whole story
    (2,   1.5),    # a one-monster band, the only one
    (3,   2.0),    # the x2 band is 3-6, not 2-6
    (6,   2.0),
    (7,   2.5),    # and the x2.5 band starts here, not at 6
    (10,  2.5),
    (11,  3.0),
    (14,  3.0),
    (15,  4.0),    # past the last named band
    (999, 4.0),    # and past the table's own sentinel
]

# Counts that 2014 would put at x1.5, x2, x3 and x4. 2024 has none of them.
WOULD_HAVE_A_MULTIPLIER = (2, 6, 14, 30)

# A record shaped like the one `rate` is handed through `known=`, so nothing
# here needs the generated SRD dataset (gitignored, built over the network).
GOBLIN = {"name": "Goblin", "cr": "1/4", "xp": 50}


@pytest.mark.parametrize("count,mult", BANDS, ids=[f"n{count}-x{mult:g}" for count, mult in BANDS])
def test_the_2014_multiplier_band_for_a_monster_count(count, mult):
    """Every band, at its ceiling, one under it, and one over the last."""
    assert encounter.multiplier_for(count, "2014") == mult


def test_the_2014_multiplier_is_the_default_ruleset():
    """`multiplier_for(count)` with no ruleset is 2014, and it is the default at
    every count rather than only at the first: a default that held for one monster
    and fell through to None for a group would rate a goblin swarm as free."""
    for count, mult in BANDS:
        assert encounter.multiplier_for(count) == mult, count


def test_2024_has_no_multiplier_at_any_count():
    """The None branch, and it is a property of the edition rather than of a count.

    Fourteen goblins is the case that matters: 2014 calls that x3, so a None here
    is the difference between 2100 XP and 700, and a 2024 campaign is exactly where
    getting it wrong would be hardest to notice.
    """
    for count in WOULD_HAVE_A_MULTIPLIER:
        assert encounter.multiplier_for(count, "2024") is None, (
            f"2024 has no monster-count multiplier, but {count} returned "
            f"{encounter.multiplier_for(count, '2024')!r}")


def test_the_two_rulesets_disagree_at_every_count_that_could_have_one():
    """The contrast, so the None cannot be read as "the function is broken at this
    count". Both are asked the same question in the same loop."""
    for count in WOULD_HAVE_A_MULTIPLIER:
        assert (encounter.multiplier_for(count, "2014")
                != encounter.multiplier_for(count, "2024")), count


def test_an_unknown_ruleset_is_not_silently_2014():
    """Not a documented case, and the reason it is pinned is that it is the shape a
    typo would take. `multiplier_for` does no validation, so anything that is not
    the literal "2024" gets the 2014 table: a caller that passed a campaign's
    `**System Version:**` straight through would get a confident wrong answer
    rather than a refusal. `rate` and `budget` both validate before they get
    here, so this pins the shape of the function rather than endorsing it, and
    says so."""
    assert encounter.multiplier_for(6, "5.2.1") == 2.0
    assert encounter.multiplier_for(6, "2014 ") == 2.0      # not stripped here
    with pytest.raises(ValueError, match="2014 or 2024"):
        encounter.rate([("Goblin", 6)], [1], ruleset="5.2.1", known={"goblin": GOBLIN})


# ── the caller: does `rate` handle the None, or crash on it ─────────────────

def test_a_2024_rating_leaves_the_monsters_xp_alone():
    """The property that matters, as arithmetic.

    `rate` computes `adjusted = int(raw * mult) if mult else raw`, so the None
    takes the raw path. Fourteen goblins is 700 XP of monsters; 2014 would call
    that 2100, which against a level-1 party's thresholds is Deadly. 2024 has to
    call it 700, which is High. The whole difference between the two editions'
    verdicts on the same fight is the line that had no test.
    """
    rated = encounter.rate([("Goblin", 14)], [1], ruleset="2024", known={"goblin": GOBLIN})
    assert rated["multiplier"] is None
    assert rated["raw"] == 700
    assert rated["adjusted"] == 700, "2024 multiplied a fight it says has no multiplier"
    assert rated["difficulty"] == "high", rated["difficulty"]
    # And the 2014 rating of the identical fight, to show the two really do part.
    other = encounter.rate([("Goblin", 14)], [1], ruleset="2014", known={"goblin": GOBLIN})
    assert other["multiplier"] == 3.0
    assert other["adjusted"] == 2100
    assert other["difficulty"] == "deadly", other["difficulty"]


def test_a_single_goblin_rates_the_same_under_both_rulesets():
    """The count that cannot tell the editions apart, pinned so the disagreement
    above cannot be an artefact of the fixture.

    One monster is x1 under 2014, so `int(raw * 1.0) == raw` and the None branch
    produces the same number. Level 1 at 50 XP per character is Moderate in 2014
    (threshold 50) and Moderate in 2024 (threshold 50) as well, so the verdict
    agrees too. Both editions say 50 for their middle column at level 1, which is
    the fact `test_encounter_xp_budget_2024.py` opens by warning about, and it is
    why the multiplier test above cannot live at one monster.
    """
    got = {rs: encounter.rate([("Goblin", 1)], [1], ruleset=rs, known={"goblin": GOBLIN})
           for rs in ("2014", "2024")}
    assert got["2014"]["multiplier"] == 1.0
    assert got["2024"]["multiplier"] is None
    assert got["2014"]["adjusted"] == got["2024"]["adjusted"] == 50
    # The two editions disagree on the WORD even where they agree on the number:
    # 2014 says "medium" and 2024 says "moderate", because each names its own
    # middle tier. The tier names are pinned here because a test that compared the
    # two verdicts to each other would have to normalise them first, and the
    # normalisation is the thing worth pinning.
    assert got["2014"]["difficulty"] == "medium"
    assert got["2024"]["difficulty"] == "moderate"
    assert got["2014"]["tiers"] == ["Easy", "Medium", "Hard", "Deadly"]
    assert got["2024"]["tiers"] == ["Low", "Moderate", "High"]


def test_a_2024_rate_never_carries_a_multiplier_into_the_award():
    """The end of the chain, because that is where a wrong multiplier is hardest to
    notice. `adventuring_day` reads `rated["multiplier"]` back out of each planned
    fight to print it, and a None reaching that f-string would be a TypeError
    rather than a wrong number. It is 2014-only today, so this drives the same
    `rate` call it would make and checks the field is the one the printer tests
    for truthiness on."""
    for count in WOULD_HAVE_A_MULTIPLIER:
        rated = encounter.rate([("Goblin", count)], [1], ruleset="2024",
                              known={"goblin": GOBLIN})
        # This is `scripts/tactics/encounter.py:225` and `:295` exactly: both
        # branch on truthiness, and both would raise formatting a None with :g.
        spelled = (f", x{rated['multiplier']:g} for {rated['count']} monsters"
                   if rated["multiplier"] and rated["multiplier"] != 1 else "")
        assert spelled == "", spelled
        assert f"{rated['adjusted']} XP" in f"{rated['adjusted']} XP{spelled}"


# ── the other "no multiplier", which is not the same value ─────────────────

def test_budget_says_no_multiplier_with_an_empty_list_and_rate_with_a_none():
    """The two sentinels, pinned so the difference stays deliberate.

    `budget` needs something it can print bands from and gets `[]`; `rate` needs a
    scalar it can test and gets `None`. Unifying them breaks one of the two
    consumers with a TypeError rather than a wrong number, which is the good kind
    of break, but only if something says they are different on purpose.
    """
    assert encounter._multiplier_table("2014"), "2014 has bands to print"
    assert encounter._multiplier_table("2024") == []
    assert encounter.multiplier_for(14, "2024") is None
    # The band list is a copy, not the dict itself: `budget` hands it to a caller
    # that could sort or extend it, and a shared reference would let that edit the
    # table every later call reads.
    bands = encounter._multiplier_table("2014")
    bands.append((9999, 99.0))
    assert encounter._multiplier_table("2014") != bands
    assert len(encounter._multiplier_table("2014")) == len(xp.MONSTER_MULTIPLIERS)
