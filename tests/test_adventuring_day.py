"""`combat.py day` — what a whole adventuring day costs a party, not one fight.

WHY
===
`budget` answers "what does one encounter cost" and `rate` answers "what did
that fight cost". Neither answers the question a GM asks when planning a session
rather than rating a fight: *is this a day, or is this three days?* The 2014 DMG
tabulates a whole day separately from an encounter, and nothing read it.

THE PART THAT MATTERS, AND WHY THE TEST IS MOSTLY ABOUT IT
==========================================================
The day budget on its own is close to useless, and the reason is worth pinning
because it is the whole design. The table is *calibrated* so that three to five
encounters ARE a day, so dividing the day budget by any difficulty threshold
returns three or four encounters at every level from 1 to 20. `test_the_budget_alone_cannot_be_over_or_under`
asserts exactly that, at both ends of the level range: if that ever stops being
true the table and the thresholds have drifted apart and every other assertion
here is measuring the wrong thing.

Which is why `--plan` exists. Only a designed day can come out over or under the
budget, so the headroom bands are asserted against real plans at each boundary,
and the refusal paths are asserted as refusals rather than as numbers — a
confident wrong answer about how much a day holds is the failure this whole
subsystem is built to avoid.
"""
from __future__ import annotations

import sys

import pytest

# Import order is load-bearing, not stylistic: `tests.tactics_fixtures` puts the
# repo root on sys.path, which is what makes `tactics` importable at all. Ruff
# would sort these the other way round and the module then fails to collect, so
# the I001 is left standing here exactly as it is in the sibling
# test_phase5_encounter_design.py.
from tests.tactics_fixtures import _RAW, ROOT, _build
from tactics import cli, rules as rules_mod

RULES_MODULE = sys.modules[type(rules_mod.load("dnd5e")).__module__]
KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")


@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    # Written up front so `day_writes_nothing_to_the_campaign` can assert it is
    # untouched: the file has to exist for "unchanged" to be checkable, and
    # creating it inside the test would be the test doing the writing.
    (d / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    monkeypatch.setattr(RULES_MODULE, "_lookup_monster", _srd)
    monkeypatch.setattr(RULES_MODULE, "_srd_suggest", _suggest)
    return d


def _srd(name: str) -> dict:
    key = name.lower().replace(" ", "-")
    if key not in _RAW:
        raise ValueError(f"no SRD monster {name!r}")
    return _build._norm_monster(_RAW[key])


def _suggest(name: str) -> list:
    return [name] if name.lower().replace(" ", "-") in _RAW else []


def run(capsys, *argv):
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def day(levels, plan=None, **kw):
    """The system-level call, for the arithmetic no CLI output can show."""
    return rules_mod.load("dnd5e").adventuring_day(levels, plan=plan, **kw)


# ─── the table itself ─────────────────────────────────────────────────────────

def test_the_day_table_covers_every_level_and_is_the_2014_one():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dnd5e_xp_day", ROOT / "systems" / "dnd5e" / "xp.py")
    xp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xp)
    assert sorted(xp.ADVENTURING_DAY_XP) == list(range(1, 21))
    # A spot-check at three levels, so a transposed digit is caught rather than
    # merely "the right number of rows".
    assert xp.ADVENTURING_DAY_XP[1] == 300
    assert xp.ADVENTURING_DAY_XP[5] == 3500
    assert xp.ADVENTURING_DAY_XP[20] == 40000
    assert xp.ENCOUNTERS_PER_DAY == (3, 5)


def test_the_day_budget_is_a_different_axis_from_one_encounter():
    """300 XP is the whole day at level 1. One Hard fight is 75 of it.

    If these two ever read as the same number, someone has copied the wrong
    table and every day figure becomes off by the encounter count.
    """
    d = day([1])
    assert d["party_total"] == 300
    assert d["thresholds"] == [25, 50, 75, 100]
    assert d["thresholds"][2] * 4 == 300


# ─── why --plan exists ────────────────────────────────────────────────────────

def test_the_budget_alone_cannot_be_over_or_under():
    """The reason `day` takes a plan: the table cannot come out over on its own.

    Asserted at both ends of the level range. Every level between them divides
    to the same three-or-four, because the two tables were built from the same
    encounter counts — so a bare `day` is a true number that tells a GM planning
    a level 20 day nothing they had not already assumed.
    """
    for level in (1, 5, 10, 20):
        d = day([level])
        # Keyed by the printed tier name, not lowercase: the dict is what the
        # output iterates, and a test that lowercased it here would stop noticing
        # a rename of the tier it prints.
        hard = d["encounters"]["Hard"]
        assert 3 <= hard <= 5, f"level {level} affords {hard} Hard fights"
        assert d["headroom"] == "no fights planned yet"


def test_a_plan_is_rated_by_the_same_code_that_rates_one_fight():
    """A day's worth of fights must not be costed by a second implementation.

    `rate` is the reference: the same monsters, the same party, must produce the
    same adjusted XP and the same tier whether asked one fight at a time or as
    part of a day.
    """
    groups = [("goblin", 4)]
    one = rules_mod.load("dnd5e").rate_encounter(groups, [1], "2014")
    planned = day([1], plan=[groups])["planned"][0]
    assert planned["adjusted"] == one["adjusted"]
    assert planned["per_character"] == one["per_character"]
    assert planned["difficulty"] == one["difficulty"]
    assert planned["multiplier"] == one["multiplier"]


def test_the_multiplier_is_applied_per_fight_not_to_the_day():
    """Two fights of two are not one fight of four.

    The 2014 multiplier is a property of a single encounter's monster count. Sum
    the day and apply it once and a party of four gets rated against a horde.
    """
    separate = day([1], plan=[[("goblin", 2)], [("goblin", 2)]])
    together = day([1], plan=[[("goblin", 4)]])
    # 50 XP each: two goblins is x1.5 on 100, four is x2 on 200. Asserted as the
    # arithmetic rather than as a literal so the test says which multiplier it is
    # checking, and so a change to the fixture's XP does not silently pass it.
    assert separate["planned"][0]["multiplier"] == 1.5
    assert together["planned"][0]["multiplier"] == 2
    assert separate["planned_xp"] == 2 * 150          # x1.5 applied twice
    assert together["planned_xp"] == 400              # x2 applied once
    assert separate["planned_xp"] != together["planned_xp"]


# ─── the headroom bands ───────────────────────────────────────────────────────

def test_a_small_plan_leaves_room(camp, capsys):
    code, out = run(capsys, "day", "--plan", "goblin")
    assert code == 0
    assert "Room for more" in out


def test_a_plan_at_the_budget_is_a_full_day_and_not_over(camp, capsys):
    code, out = run(capsys, "day", "--plan", "goblin x2 | giant-frog")
    assert code == 0
    assert "not an over budget one" in out


def test_a_plan_over_the_budget_is_named_as_two_days(camp, capsys):
    code, out = run(capsys, "day", "--plan", "goblin x4")
    assert code == 0
    assert "Over: two days" in out


def test_a_plan_far_over_the_budget_says_so_and_names_exhaustion(camp, capsys):
    """The far band is the one a GM can act on, so it is pinned whole.

    It is also the band that must not be reached by a party being *under*
    budget, which is why the assertion is on the whole sentence rather than on
    a percentage.
    """
    code, out = run(capsys, "day", "--plan",
                    "adult-red-dragon | adult-red-dragon | adult-red-dragon")
    assert code == 0
    assert "Far over" in out
    assert "exhaustion 5" in out


def test_the_fight_count_is_the_planned_count_not_an_invented_equivalent(camp, capsys):
    """Four planned fights are reported as four.

    An earlier version converted the XP back into a "fights' worth" figure and
    cheerfully reported four planned fights as "roughly 1 fights' worth". A GM
    who planned four fights learns nothing from that, and it is the exact
    confident-wrong-number failure this subsystem exists to prevent.
    """
    code, out = run(capsys, "day", "--plan", "goblin x2 | giant-frog | goblin x2 | giant-frog")
    assert code == 0
    assert "4 planned fights" in out
    assert "roughly" not in out


def test_one_planned_fight_is_singular(camp, capsys):
    code, out = run(capsys, "day", "--plan", "goblin x4")
    assert code == 0
    assert "1 planned fight," in out
    assert "1 planned fights" not in out


# ─── refusals ─────────────────────────────────────────────────────────────────

def test_2024_is_refused_rather_than_derived(camp, capsys):
    """2024's tiers are a day's share, not an encounter cost, so there is
    nothing to divide. Dividing the High tier by three would produce a number
    with the right shape and no meaning, so the GM is told instead."""
    code, out = run(capsys, "day", "--ruleset", "2024")
    assert code == 1
    assert "2014 DMG table" in out
    assert "rate" in out
    assert "XP" not in out.replace("2014 DMG table", "")


def test_a_campaign_on_2024_is_refused_the_same_way(camp, capsys):
    """The refusal is not only for an explicit flag.

    A campaign stamped `**System Version:** 2024` follows the campaign's own
    ruleset, and a budget read under the wrong table is a wrong answer with a
    confident shape.
    """
    path = camp / "state.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "# Campaign: demo", "**System Version:** 2024\n# Campaign: demo"), encoding="utf-8")
    code, out = run(capsys, "day")
    assert code == 1 and "2014 DMG table" in out


def test_an_unknown_ruleset_is_refused_by_argparse(camp, capsys):
    """`--ruleset` is a choice, so argparse refuses it before any of this runs.

    Worth pinning because it is a different refusal from the 2024 one: this is
    "that is not a ruleset", the other is "that ruleset has no day table". Both
    have to be refusals, and a test that only covered the second would let a
    loosened `choices` through.
    """
    with pytest.raises(SystemExit) as exc:
        cli.main(["-c", "demo", "day", "--ruleset", "5e"])
    assert exc.value.code == 2


def test_a_typo_in_a_plan_gets_near_misses_not_a_traceback(camp, capsys):
    code, out = run(capsys, "day", "--plan", "gobiln x4")
    assert code == 1
    assert "No SRD monster" in out


def test_a_zero_count_is_refused(camp, capsys):
    code, out = run(capsys, "day", "--plan", "goblin x0")
    assert code == 1 and "1 or more" in out


def test_an_empty_plan_is_refused_with_the_syntax_rather_than_a_bare_budget(camp, capsys):
    """`--plan " | "` is a GM who meant to type something.

    Falling through to the bare budget would show a true number and imply the
    plan had been costed, which is the failure mode of answering an unasked
    question with a plausible one.
    """
    code, out = run(capsys, "day", "--plan", " | ")
    assert code == 1
    assert "--plan" in out and "|" in out


# ─── the output ───────────────────────────────────────────────────────────────

def test_the_budget_line_reports_the_party_share(camp, capsys):
    code, out = run(capsys, "day")
    assert code == 0
    assert "A day is 300 XP for the party, 300 XP each." in out
    assert "3 to 5 encounters" in out


def test_no_plan_says_how_to_give_one(camp, capsys):
    """The hint names the real syntax.

    An empty `day` that printed only a budget would read as a complete answer,
    and the GM would not know they were missing the useful half.
    """
    code, out = run(capsys, "day")
    assert code == 0
    assert "--plan" in out and "|" in out


def test_the_multiplier_is_not_written_after_the_xp_figure(camp, capsys):
    """`400 XP x2` reads as a unit, not as arithmetic.

    The multiplier is the number a GM is least likely to have in their head, so
    it is the one that must be unambiguous. This failed on the first run and the
    fix is why the assertion exists.
    """
    code, out = run(capsys, "day", "--plan", "goblin x4")
    assert code == 0
    assert "x2 for 4 monsters" in out
    assert "XP x2," not in out


def test_a_single_monster_fight_shows_no_multiplier(camp, capsys):
    """x1 is the absence of a multiplier, not a multiplier of one."""
    code, out = run(capsys, "day", "--plan", "goblin")
    assert code == 0
    assert "multipliers" not in out
    assert "x1" not in out


def test_json_carries_the_arithmetic(camp, capsys):
    """A rating the GM cannot check is a rating they have to take on faith.

    Same reason `rate` returns its rows: the headroom sentence is a summary of
    arithmetic, and the arithmetic has to be reachable.
    """
    import json
    code = cli.main(["-c", "demo", "day", "--json", "--plan", "goblin x4 | giant-frog"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    result = payload["result"]
    assert result["party_total"] == 300
    assert result["planned_xp"] == 450          # 400 + 50
    assert len(result["planned"]) == 2
    # A list, not a tuple: this comes back through --json, and a test asserting a
    # tuple would fail on the JSON shape rather than on the content.
    assert result["planned"][0]["groups"][0][:2] == ["Goblin", 4]
    # 450 of 300 is 150%, so the band is "two days", not "far over". Asserted as
    # the band this plan actually lands in rather than the loudest one.
    assert "Over: two days" in result["headroom"]


def test_day_writes_nothing_to_the_campaign(camp, capsys):
    """`day` is a planning question, not an encounter.

    It is read-only, so it must not create an encounter, consume a pending roll,
    or touch the session log. A planning tool that mutates the fight state is a
    tool that can be called at the wrong moment and cost a real roll.
    """
    code, _ = run(capsys, "day", "--plan", "goblin x4")
    assert code == 0
    assert not (camp / "combat" / "encounter.json").exists()
    assert not (camp / "combat" / "pending.json").exists()
    assert (camp / "session-log.md").read_text(encoding="utf-8") == "# Session Log\n"
