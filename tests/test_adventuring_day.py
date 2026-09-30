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
because it is the whole design. The DMG puts an adventuring day at about six to
eight *medium or hard* encounters, and the day table is calibrated to that blend:
divided by the Medium column it gives 5 to 8, by the Hard column 3 to 5, at every
level from 1 to 20. So "a day holds about seven Medium fights" is true at level 1
and at level 20 and tells a GM planning a level 20 day nothing they had not
already assumed. Only a designed day can come out over or under, which is why
`--plan` is the feature and the budget is the frame.

`test_the_day_budget_divides_to_the_dmgs_own_range_per_tier` asserts those ranges
per tier at all twenty levels, because the earlier version of this file asserted
`3 <= hard <= 5` at four levels and was decoration: replacing the whole day table
with `Hard x 4` left all 23 tests green.

A note on the number itself, since it was wrong once. The first version of this
work claimed the DMG says three to five encounters make up a day. It does not;
that is the Hard column alone. DMG p. 84 says six to eight *medium or hard*, and
the distinction is the whole difference between a GM planning a full day and a GM
planning half of one. A confident wrong number is the failure this subsystem
exists to avoid, and it is easiest to commit while writing the prose that
justifies the design.
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
    """The system-level call, for the arithmetic no CLI output can show.

    Takes `camp` for its side effect, not its value: the fixture is what
    monkeypatches `RULES_MODULE._lookup_monster`, and `adventuring_day` reaches the
    bestiary through that seam rather than through a `lookup=` argument. Without the
    fixture these tests hit the real lookup, which needs the generated
    `data/dnd5e_srd.json` — gitignored, so absent in a clean worktree. They passed
    in the developer's primary checkout only because a dataset happened to be lying
    there, which is a false green of exactly the kind this repo already has one of
    (see `test_faculty_sheets.py`) and this file is supposed to be cleaner than.
    """
    return rules_mod.load("dnd5e").adventuring_day(levels, plan=plan, **kw)


# ─── the table itself ─────────────────────────────────────────────────────────

def test_the_day_table_is_the_2014_one_in_full():
    """All 20 values, not a spot-check.

    An earlier version of this test asserted the key set plus three levels, on the
    reasoning that "the right number of rows" was coverage. It was not: mutating
    level 17 from 25,000 to 99,999 — the largest single-value error available in
    the table — left all 23 tests green. The whole feature is one table lookup, so
    a spot-check of it is a spot-check of everything.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dnd5e_xp_day", ROOT / "systems" / "dnd5e" / "xp.py")
    xp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xp)
    assert xp.ADVENTURING_DAY_XP == {
        1: 300, 2: 600, 3: 1200, 4: 1700, 5: 3500,
        6: 4000, 7: 5000, 8: 6000, 9: 7500, 10: 9000,
        11: 10500, 12: 11500, 13: 13500, 14: 15000, 15: 18000,
        16: 20000, 17: 25000, 18: 27000, 19: 30000, 20: 40000,
    }


def test_the_encounters_per_day_figure_is_the_dmgs(camp):
    """Six to eight, from the DMG's own sentence.

    This was 3 to 5, which is the *Hard* column alone, and it was printed to the GM
    on every invocation as though the DMG had said it. A GM who believed it would
    plan half the encounters that actually fit in a day. The DMG p. 84 sentence is
    "about six to eight medium or hard encounters in a day", and the "medium or
    hard" is load-bearing: the day table divides to the Medium column's 5 to 8 and
    the Hard column's 3 to 5, so quoting either column alone misquotes the table.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dnd5e_xp_day2", ROOT / "systems" / "dnd5e" / "xp.py")
    xp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xp)
    assert xp.ENCOUNTERS_PER_DAY == (6, 8)


def test_the_day_budget_divides_to_the_dmgs_own_range_per_tier(camp):
    """The calibration, checked per tier, because it only holds for two of them.

    The previous version asserted `3 <= hard <= 5` at four levels and was sold as
    the test that pins the design finding. It cannot: replacing the whole day table
    with `Hard threshold x 4` leaves it green, because `(Hard*4) // Hard == 4` at
    every level. The assertion is a tautology over the levels it samples.

    What is actually true, and what the docstring now says, is the real spread.
    Asserting the exact per-tier range at all 20 levels is a test that can fail for
    the reason it claims to, and it fails loudly if either DMG table is edited.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dnd5e_xp_day3", ROOT / "systems" / "dnd5e" / "xp.py")
    xp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xp)
    rows = {tier: set() for tier in ("Easy", "Medium", "Hard", "Deadly")}
    for level in range(1, 21):
        got = day([level])["encounters"]
        for tier in rows:
            rows[tier].add(got[tier])
    # Exact ranges, measured from the two tables. Easy and Deadly are further out
    # than the DMG's "six to eight medium or hard" because the DMG's own sentence
    # says easy days hold more and deadly days fewer.
    assert rows["Easy"] == set(range(11, 17))
    assert rows["Medium"] == set(range(5, 9))
    assert rows["Hard"] == {3, 4, 5}
    assert rows["Deadly"] == {2, 3}
    # And the claim the docstring makes: the budget is calibrated, so no level's
    # own day is wildly out of line with the DMG's Medium-or-hard guidance.
    low, high = day([1])["per_day"]
    for level in range(1, 21):
        medium = day([level])["encounters"]["Medium"]
        assert low - 2 <= medium <= high, f"level {level}: {medium} Medium fights"


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

    The human-readable statement of the design finding. The exact per-tier ranges
    are asserted by `test_the_day_budget_divides_to_the_dmgs_own_range_per_tier`,
    which is the test that can actually fail: the `3 <= hard <= 5` assertion that
    used to live here survived replacing the entire day table with
    `Hard threshold x 4`, because `(Hard*4) // Hard == 4` at every level.
    """
    for level in (1, 5, 10, 20):
        d = day([level])
        # Keyed by the printed tier name, not lowercase: the dict is what the
        # output iterates, and a test that lowercased it here would stop noticing
        # a rename of the tier it prints.
        hard = d["encounters"]["Hard"]
        assert 3 <= hard <= 5, f"level {level} affords {hard} Hard fights"
        assert d["headroom"] == "no fights planned yet"


def test_a_plan_is_rated_by_the_same_code_that_rates_one_fight(camp):
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


def test_the_multiplier_is_applied_per_fight_not_to_the_day(camp):
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
    """Pinned whole, because this is the sentence with the most authority.

    An earlier version asserted only the substring "Over: two days", and
    replacing the entire recommendation with a fabricated house rule ("or grant two
    extra Hit Dice mid-fight (GMs: this is a house rule, not the DMG)") left all 23
    tests green. That is the single most dangerous mutation available in this file:
    a made-up mechanic, in the game's own voice, recommended to a GM at the table.
    """
    code, out = run(capsys, "day", "--plan", "goblin x4")
    assert code == 0
    assert ("1 planned fight, about 133% of the day. Over: two days, or a long rest "
            "in the middle of it. Which of those is a GM's call, not the table's"
            in out)


def test_a_plan_far_over_the_budget_says_so_without_predicting_a_condition(camp, capsys):
    """The far band, pinned whole, and it must not name an exhaustion level.

    The first version ended "exhaustion 5 (speed 0) is likely by the end of it".
    The exhaustion number is correct 2014 5e (PHB p. 291) and was still wrong here,
    because nothing in this command can produce it: `day` is read-only, loads no
    encounter, sees no tokens, and the XP budget measures expected XP earned
    rather than resource depletion. Exhaustion lives in the tracker, where only a
    long rest reduces it. So the sentence asserted a specific mechanical game state
    the tool did not compute, and a GM would put it on a character sheet.

    The editorial half of the sentence survives deliberately: "the party will be
    spent" is a GM's judgement offered as advice, and a GM is entitled to it. The
    numbered condition is not.
    """
    code, out = run(capsys, "day", "--plan",
                    "adult-red-dragon | adult-red-dragon | adult-red-dragon")
    assert code == 0
    assert "Far over" in out
    assert "multi-day march" in out
    assert "the DMG's exhaustion table is the reference" in out
    # The prohibition itself, so a reintroduction is caught by name.
    assert "exhaustion" not in out.lower() or "exhaustion table" in out.lower()
    for level in range(1, 7):
        assert f"exhaustion {level}" not in out.lower()


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
    """2024 removed the adventuring day, so there is no table to read.

    The first version's reason was "2024's three tiers are a whole day's share, not
    an encounter cost, so there is nothing to divide". That is a true observation
    standing in for a false reason: 2024 dropped the concept rather than redefining
    its tiers, and a refusal should say the thing that is actually true. The
    conclusion was right either way; only the reasoning was soft, and a GM reading
    it would have learned something false about 2024.
    """
    code, out = run(capsys, "day", "--ruleset", "2024")
    assert code == 1
    assert "2014 DMG table" in out
    assert "no adventuring day" in out
    assert "rate" in out
    # A refusal, not a derived fiction: no figure of any kind. Matched as a number
    # attached to a quantity rather than "any digit", because the ruleset names
    # themselves are 2014 and 2024 and would satisfy the blunter check.
    import re
    assert not re.search(r"\d[\d,.]*\s*(xp|%|encounter|fight)", out, re.I), out


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


def test_an_unknown_ruleset_is_refused_before_anything_runs(camp, capsys):
    """`--ruleset` is a choice, so a bad one is refused at the door.

    Worth pinning because it is a different refusal from the 2024 one: this is
    "that is not a ruleset", the other is "that ruleset has no day table". Both
    have to be refusals, and a test that only covered the second would let a
    loosened `choices` through.

    Asserted as exit 1 and the message rather than as `SystemExit(2)`, because
    `cli.main` now catches argparse's own error and re-reports it as a
    user-facing line. Pinning the raw `SystemExit` would have made this test fail
    on a change that made the tool *better*, which is how a test starts
    pressuring the code back towards worse.
    """
    code, out = run(capsys, "day", "--ruleset", "5e")
    assert code == 1
    assert "invalid choice" in out
    assert "day --help" in out
    assert "XP" not in out


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

    Asserted against the *empty-plan* line specifically, not against `--plan` and
    `|`: both substrings also appear in the success path's hint line, so the
    original assertion was satisfied by the wrong output and only the exit code was
    doing any work.
    """
    code, out = run(capsys, "day", "--plan", " | ")
    assert code == 1
    assert "No fights in --plan" in out
    # And the hint must NOT be here: a refusal that also shows the success hint is
    # the confusing output, not the helpful one.
    assert "Pass --plan to cost a day" not in out


# ─── the output ───────────────────────────────────────────────────────────────

def test_the_budget_line_reports_the_party_share(camp, capsys):
    code, out = run(capsys, "day")
    assert code == 0
    assert "A day is 300 XP for the party, 300 XP each." in out
    assert "6 to 8 medium or hard encounters" in out


def test_a_mixed_party_reports_each_characters_own_day(camp, capsys):
    """No "XP each" figure for a mixed party, because none of them has one.

    The first version printed `party_total // party_size` and labelled it "XP each".
    For a L1 and a L20 that is 20,150, a number belonging to no character on the
    table, printed directly beneath a threshold row built from the *average level's*
    data — two different averages presented as one table, both called "the average".
    A GM quoting 20,150 to the level-1 character has quoted a figure that character
    cannot spend twice inside their own 300-XP day.

    So each character's own day is reported by name, and the mean is not reported
    at all: it is arithmetic, not a budget.
    """
    # A genuinely mixed party: the day budget is level-keyed, so two level-1
    # sheets are not a mixed party and would take the homogeneous branch.
    (camp / "characters" / "Vesper.md").write_text(
        KAIROS_MD.replace("# Kairos", "# Vesper")
        .replace("Wizard 1 (Chronurgy at 2)", "Wizard 20 (Chronurgy at 2)")
        .replace("**Level:** 1 |", "**Level:** 20 |"),
        encoding="utf-8")
    code, out = run(capsys, "day")
    assert code == 0
    assert "Kairos 300, Vesper 40000" in out
    assert "40300 XP for the party" in out
    # Scoped to the budget line, not the whole output: "at 25 XP each" in the
    # threshold table above is the per-character difficulty threshold and is
    # correct, and appears identically in `budget`. What must not appear is the
    # *day* mean, which belongs to no character.
    budget_line = next(ln for ln in out.splitlines() if ln.strip().startswith("A day is"))
    assert "XP each" not in budget_line
    assert "20150" not in budget_line


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
    """x1 is the absence of a multiplier, not a multiplier of one.

    The first version also asserted `"multipliers" not in out`, which is
    unfalsifiable: the string appears in no `day`, `rate` or `budget` output
    anywhere, only in a module docstring in `xp.py`. Removed rather than kept as
    decoration that happens to pass.
    """
    code, out = run(capsys, "day", "--plan", "goblin")
    assert code == 0
    assert "for 1 monsters" not in out
    assert "x1" not in out
    # A two-monster fight DOES show one, so the assertion above is about the
    # absence rather than the substring never appearing at all.
    code, out = run(capsys, "day", "--plan", "goblin x2")
    assert code == 0 and "x1.5 for 2 monsters" in out


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
