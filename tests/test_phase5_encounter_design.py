"""Phase 5 — encounter design: what a party can be handed, and what a fight costs.

WHY
===
A GM decides what the party is about to walk into on feel ("four goblins, that
is probably fine"), and when the feel is wrong nothing in the fiction records
why. These are the two questions with numbers behind them, and the number is
printed with its arithmetic so the GM can argue with it instead of trusting it.

WHAT IS ASSERTED, AND WHY IT IS ASSERTED THIS WAY
=================================================
The thresholds are the point. A rating that is off by a tier is not a rounding
difference — it is a GM who believes a deadly fight is a hard one — so the
tables are pinned at their boundaries (one monster, two, three, six, seven, and
past the last band) and the printed output is pinned whole, in the GM's own
language, because a reworded line is a changed tool and these tests are the only
thing that notices.

`end` is the other half: the fight that ran is rated by the tables it was
designed against, awarded to the party still standing, and recorded in the
ledger `xp.py` already keeps. A sheet that does not track XP (milestone
levelling) is reported rather than silently skipped, and a downed PC gets
nothing.

Monster stats come from the real SRD records in tests/fixtures — the generated
dataset is gitignored — through the same seam `combat.py add --srd` uses.
"""
from __future__ import annotations

import difflib
import json
import pathlib
import sys

import pytest

from tests.tactics_fixtures import ROOT, _RAW, _build, roller
from tactics import cli, encounter, engine, rules as rules_mod

RULES_MODULE = sys.modules[type(rules_mod.load("dnd5e")).__module__]
KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")
# The same sheet with its XP field in the form `xp.py` can award into.
KAIROS_XP_MD = KAIROS_MD.replace("**XP:** 0 (milestone levelling; see world.md)",
                                 "**XP:** 0 / 300")


# ─── the campaign these run against ───────────────────────────────────────────

@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
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
    """Near matches, as lookup.suggest gives them, over the fixture monsters."""
    names = [_build._norm_monster(r)["name"] for r in _RAW.values()]
    hit = difflib.get_close_matches(name.lower(), [n.lower() for n in names], n=3, cutoff=0.6)
    return [n for n in names if n.lower() in hit]


def run(capsys, *argv):
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def sheet_of(camp) -> pathlib.Path:
    return camp / "characters" / "Kairos.md"


def ledger_of(camp) -> list:
    path = camp / "xp-ledger.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def rules():
    return rules_mod.load("dnd5e")


# ─── the monster shorthand ────────────────────────────────────────────────────

def test_the_shorthand_reads_x_and_the_multiplication_sign():
    assert encounter.parse_monsters("goblin x4") == [("goblin", 4)]
    assert encounter.parse_monsters("goblin ×4") == [("goblin", 4)]
    assert encounter.parse_monsters("goblin X4") == [("goblin", 4)]
    assert encounter.parse_monsters("goblin*4") == [("goblin", 4)]
    assert encounter.parse_monsters("hobgoblin") == [("hobgoblin", 1)]


def test_the_shorthand_is_comma_separated_and_keeps_multi_word_names():
    assert encounter.parse_monsters("goblin x4, wolf, giant frog x2") == [
        ("goblin", 4), ("wolf", 1), ("giant frog", 2)]
    assert encounter.parse_monsters("  goblin x2 ,  wolf  ") == [("goblin", 2), ("wolf", 1)]


def test_a_name_that_merely_starts_with_x_is_still_a_name():
    # A monster whose own name opens with an x must not be read as a count.
    assert encounter.parse_monsters("xorn x2") == [("xorn", 2)]


def test_a_count_is_never_guessed_and_never_zero():
    with pytest.raises(engine.CombatError, match="1 or more"):
        encounter.parse_monsters("goblin x0")
    for bad in ("", "   ", ",,,"):
        with pytest.raises(engine.CombatError, match="monsters"):
            encounter.parse_monsters(bad)


# ─── the party ────────────────────────────────────────────────────────────────

def test_party_auto_is_every_sheet_in_the_campaign(camp):
    (camp / "characters" / "Vesper.md").write_text(KAIROS_XP_MD.replace("# Kairos", "# Vesper"),
                                                  encoding="utf-8")
    assert encounter.party(camp, rules()) == [("Kairos", 1), ("Vesper", 1)]
    assert encounter.party(camp, rules(), "Vesper") == [("Vesper", 1)]
    with pytest.raises(engine.CombatError, match="No sheet for Nobody"):
        encounter.party(camp, rules(), "Nobody")


def test_party_levels_come_off_the_sheets(camp):
    (camp / "characters" / "Vesper.md").write_text(
        KAIROS_XP_MD.replace("# Kairos", "# Vesper").replace("**Level:** 1", "**Level:** 5"),
        encoding="utf-8")
    assert encounter.party(camp, rules()) == [("Kairos", 1), ("Vesper", 5)]


def test_no_sheets_means_no_budget(camp, capsys):
    sheet_of(camp).unlink()
    code, out = run(capsys, "budget")
    assert code == 1 and "No character sheets" in out


# ─── budget ───────────────────────────────────────────────────────────────────

BUDGET_2014 = """Encounter budget — 2014 rules, party of 1 at average level 1 (Kairos L1).
  tier            Easy     Medium   Hard     Deadly
  per character   25       50       75       100
  party of 1      25       50       75       100

  Monster-count multiplier (on raw monster XP): 1-1 x1, 2-2 x1.5, 3-6 x2, 7-10 x2.5, 11-14 x3, 15+ x4. Groups are worth more than the sum of their parts."""


def test_budget_2014_is_the_dmg_threshold_table_plus_the_multiplier_bands(camp, capsys):
    code, out = run(capsys, "budget")
    assert code == 0
    assert out == BUDGET_2014


BUDGET_2024 = """Encounter budget — 2024 rules, party of 1 at average level 1 (Kairos L1).
  tier            Low        Moderate   High
  per character   25         50         100
  party of 1      25         50         100

  2024 has no monster-count multiplier: the monsters' XP is the encounter's XP, and the party's budget is the per-character figure times the party size."""


def test_budget_2024_has_three_tiers_and_no_multiplier(camp, capsys):
    code, out = run(capsys, "budget", "--ruleset", "2024")
    assert code == 0
    assert out == BUDGET_2024
    assert "Deadly" not in out


def test_budget_follows_the_campaigns_own_ruleset(camp, capsys):
    text = (camp / "state.md").read_text(encoding="utf-8")
    (camp / "state.md").write_text(text.replace("# Campaign: demo",
                                                "**System Version:** 2024\n# Campaign: demo"),
                                   encoding="utf-8")
    code, out = run(capsys, "budget")
    assert code == 0 and "2024 rules" in out
    code, out = run(capsys, "budget", "--ruleset", "2014")
    assert code == 0 and "2014 rules" in out


def test_a_ruleset_with_no_table_is_refused_rather_than_guessed(camp, capsys):
    text = (camp / "state.md").read_text(encoding="utf-8")
    (camp / "state.md").write_text(text.replace("# Campaign: demo",
                                                "**System Version:** 5.2.1\n# Campaign: demo"),
                                   encoding="utf-8")
    code, out = run(capsys, "budget")
    assert code == 1 and "5.2.1" in out and "--ruleset" in out


def test_a_mixed_level_party_is_said_to_be_averaged(camp, capsys):
    (camp / "characters" / "Vesper.md").write_text(
        KAIROS_XP_MD.replace("# Kairos", "# Vesper").replace("**Level:** 1", "**Level:** 5"),
        encoding="utf-8")
    code, out = run(capsys, "budget")
    assert code == 0
    assert "party of 2 at average level 3" in out                    # (1 + 5) / 2
    assert "per character   75       150      225      400" in out     # the level 3 row
    assert "party of 2      275      550      825      1200" in out     # 25+250, 50+500, ...
    assert "Levels differ" in out


# ─── rate ─────────────────────────────────────────────────────────────────────

RATE_2014 = """Rating 5 monster(s) for 2014 rules, party of 1 at average level 1 (Kairos L1).
  4x Goblin   CR 1/4      50 XP each  =    200
  1x Wolf     CR 1/4      50 XP each  =     50
  Raw 250 XP, 5 monsters, multiplier x2 -> 500 adjusted.
  500 XP for a party of 1 = 500 XP each, against Easy 25 / Medium 50 / Hard 75 / Deadly 100.
  DEADLY. Expect someone to drop. Deadly is meant to end a resource, not a character — a party with nothing left to spend loses this one."""


def test_rate_shows_the_arithmetic_and_the_verdict(camp, capsys):
    code, out = run(capsys, "rate", "--monsters", "goblin x4, wolf")
    assert code == 0
    assert out == RATE_2014


def test_rate_2024_has_no_multiplier_and_three_tiers(camp, capsys):
    code, out = run(capsys, "rate", "--monsters", "goblin x4", "--ruleset", "2024")
    assert code == 0
    assert "no multiplier (2024) -> 200 XP for the party." in out
    assert "200 XP for a party of 1 = 200 XP each" in out
    assert "against Low 25 / Moderate 50 / High 100." in out
    assert "HIGH." in out and "DEADLY" not in out


RATE_MEDIUM = """Rating 1 monster(s) for 2014 rules, party of 1 at average level 1 (Kairos L1).
  1x Goblin   CR 1/4      50 XP each  =     50
  Raw 50 XP, 1 monsters, multiplier x1 -> 50 adjusted.
  50 XP for a party of 1 = 50 XP each, against Easy 25 / Medium 50 / Hard 75 / Deadly 100.
  MEDIUM. A fight with weight to it — someone will spend a resource."""


def test_one_goblin_is_medium_and_not_told_the_gm_it_is_easy(camp, capsys):
    code, out = run(capsys, "rate", "--monsters", "goblin")
    assert code == 0
    assert out == RATE_MEDIUM


def test_below_the_first_threshold_is_trivial_not_easy(camp, capsys):
    # "Easy" is the word a GM repeats to themselves when they stop preparing for
    # a fight. Below the table there is no word, and the tool says so.
    _code, out = run(capsys, "rate", "--monsters", "goblin x1", "--ruleset", "2024",
                     "--party", "Vesper")
    assert "no sheet" in out.lower() or "TRIVIAL" in out            # Vesper has no sheet
    (camp / "characters" / "Vesper.md").write_text(
        KAIROS_XP_MD.replace("# Kairos", "# Vesper").replace("**Level:** 1", "**Level:** 20"),
        encoding="utf-8")
    _code, out = run(capsys, "rate", "--monsters", "goblin x1")
    assert "TRIVIAL — under the Easy threshold" in out
    assert "EASY." not in out


def test_the_multiplier_bands_are_applied_at_every_boundary(camp, capsys):
    # One goblin is 50 XP and two are 100 raw but 150 adjusted: the jump is the
    # whole point of the table, so every band edge is checked, not a sample.
    seen = {}
    for n in (1, 2, 3, 6, 7, 10, 11, 14, 15, 20):
        _code, out = run(capsys, "rate", "--monsters", f"goblin x{n}")
        seen[n] = float(out.split("multiplier x")[1].split(" ")[0])
    assert seen == {1: 1.0, 2: 1.5, 3: 2.0, 6: 2.0, 7: 2.5, 10: 2.5,
                    11: 3.0, 14: 3.0, 15: 4.0, 20: 4.0}


def test_a_monster_the_gm_misspelled_comes_back_with_near_matches(camp, capsys):
    code, out = run(capsys, "rate", "--monsters", "gobiln x2")
    assert code == 1
    assert "No SRD monster 'gobiln'" in out
    assert "Did you mean" in out and "Goblin" in out
    assert "Traceback" not in out


def test_an_unknown_monster_with_no_near_match_says_how_to_build_the_data(camp, capsys):
    code, out = run(capsys, "rate", "--monsters", "zzzzqqq x2")
    assert code == 1 and "build_srd.py" in out and "Traceback" not in out


def test_rate_needs_monsters(camp):
    with pytest.raises(SystemExit):
        cli.main(["-c", "demo", "rate"])


# ─── a finished fight feeds the XP ledger ─────────────────────────────────────

def begin(capsys, *monsters):
    argv = ["start", "frog-pond", "--pc", "Kairos@B7", "--seed", "3"]
    for i, m in enumerate(monsters):
        argv += ["--monster", f"{m}@{('J5', 'M11', 'K9')[i % 3]}"]
    return run(capsys, *argv)


def slay(camp, token: str) -> None:
    """Put a PC at 0 HP with three death-save failures, the state that counts
    as dead in this engine (dropping to 0 is not dying)."""
    path = camp / "combat" / "encounter.json"
    enc = json.loads(path.read_text(encoding="utf-8"))
    enc["tokens"][token].update(hp=0, dead=True)
    path.write_text(json.dumps(enc), encoding="utf-8")


def test_end_awards_the_xp_the_fight_was_worth_and_records_it(camp, capsys):
    sheet_of(camp).write_text(KAIROS_XP_MD, encoding="utf-8")
    begin(capsys, "goblin", "goblin")
    code, out = run(capsys, "end")
    assert code == 0
    # Two goblins: 100 raw, x1.5 for two monsters, one character standing.
    assert "XP deadly (2014): 150 XP each for a party of 1" in out
    assert "Kairos: +150 XP -> 150 / 300" in out
    assert "**XP:** 150 / 300" in sheet_of(camp).read_text(encoding="utf-8")
    rows = ledger_of(camp)
    assert len(rows) == 1
    assert rows[0] == {"at": rows[0]["at"], "character": "Kairos", "awarded": 150,
                       "total_after": 150,
                       "note": "deadly combat: 1x Goblin 1, 1x Goblin 2"}


def test_end_can_be_told_not_to_award(camp, capsys):
    sheet_of(camp).write_text(KAIROS_XP_MD, encoding="utf-8")
    begin(capsys, "goblin")
    code, out = run(capsys, "end", "--no-xp")
    assert code == 0 and "XP" not in out
    assert "**XP:** 0 / 300" in sheet_of(camp).read_text(encoding="utf-8")
    assert ledger_of(camp) == []


def test_a_sheet_that_does_not_track_xp_is_reported_not_silently_skipped(camp, capsys):
    # Kairos's own sheet says "**XP:** 0 (milestone levelling)": a campaign
    # advancing by milestone. Writing 150 into it would put a number where the
    # campaign has a rule, and the ledger would claim an award nothing reflects.
    begin(capsys, "goblin")
    code, out = run(capsys, "end")
    assert code == 0
    assert "Kairos: this sheet does not track XP (milestone levelling?)" in out
    assert "milestone levelling" in sheet_of(camp).read_text(encoding="utf-8")
    assert ledger_of(camp) == []


def test_a_character_who_went_down_is_not_awarded(camp, capsys):
    sheet_of(camp).write_text(KAIROS_XP_MD, encoding="utf-8")
    begin(capsys, "goblin")
    slay(camp, "kairos")
    code, out = run(capsys, "end")
    assert code == 0
    assert "XP: the party is all down — nothing awarded." in out
    assert "**XP:** 0 / 300" in sheet_of(camp).read_text(encoding="utf-8")
    assert ledger_of(camp) == []


def test_a_party_that_lost_one_is_scored_on_who_is_standing(camp, capsys):
    sheet_of(camp).write_text(KAIROS_XP_MD, encoding="utf-8")
    (camp / "characters" / "Vesper.md").write_text(
        KAIROS_XP_MD.replace("# Kairos", "# Vesper"), encoding="utf-8")
    run(capsys, "start", "frog-pond", "--pc", "Kairos@B7", "--pc", "Vesper@A6",
        "--monster", "goblin@J5", "--seed", "3")
    slay(camp, "kairos")
    run(capsys, "adjust", "vesper", "hp=4")
    code, out = run(capsys, "end")
    # Vesper finished the fight alone, so it is a party of one: 50 XP, not half
    # of a share sized for two.
    assert "Vesper: +50 XP -> 50 / 300" in out
    assert "**XP:** 0 / 300" in sheet_of(camp).read_text(encoding="utf-8")
    assert [r["character"] for r in ledger_of(camp)] == ["Vesper"]


def test_a_fight_against_something_with_no_cr_is_not_a_failed_award(camp, capsys):
    sheet_of(camp).write_text(KAIROS_XP_MD, encoding="utf-8")
    begin(capsys, "goblin")
    path = camp / "combat" / "encounter.json"
    enc = json.loads(path.read_text(encoding="utf-8"))
    for t in enc["tokens"].values():
        if t["side"] == "enemy":
            t["extra"]["cr"], t["extra"]["xp"] = None, None
    path.write_text(json.dumps(enc), encoding="utf-8")
    code, out = run(capsys, "end")
    assert code == 0 and "**XP:** 0 / 300" in sheet_of(camp).read_text(encoding="utf-8")
    assert ledger_of(camp) == []


def test_ending_a_fight_still_writes_the_sheets_and_the_log_first(camp, capsys):
    sheet_of(camp).write_text(KAIROS_XP_MD, encoding="utf-8")
    begin(capsys, "goblin")
    run(capsys, "adjust", "kairos", "hp=5")
    code, out = run(capsys, "end")
    assert "**HP:** 5 / 8" in sheet_of(camp).read_text(encoding="utf-8")
    assert "### Grid combat: Frog Pond" in (camp / "session-log.md").read_text(encoding="utf-8")
    assert "**XP:** 50 / 300" in sheet_of(camp).read_text(encoding="utf-8")


# ─── the design tools are safe to run with nothing started ────────────────────

def test_budget_and_rate_need_no_encounter_and_write_nothing(camp, capsys):
    assert not (camp / "combat").exists()
    assert run(capsys, "budget")[0] == 0
    assert run(capsys, "rate", "--monsters", "goblin x2")[0] == 0
    assert not (camp / "combat").exists()
    assert not (camp / "tracker.json").exists()
    assert "**HP:** 8 / 8" in sheet_of(camp).read_text(encoding="utf-8")
    assert ledger_of(camp) == []


def test_json_output_carries_the_numbers_not_just_the_text(camp, capsys):
    code = cli.main(["-c", "demo", "rate", "--monsters", "goblin x4", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["result"]["raw"] == 200
    assert payload["result"]["multiplier"] == 2.0
    assert payload["result"]["adjusted"] == 400
    assert payload["result"]["difficulty"] == "deadly"
    assert payload["result"]["rows"][0]["cr_label"] == "1/4"
    assert "combat" not in payload                    # nothing was running to report


def test_rating_a_fight_never_rolls_a_die(camp, capsys):
    # budget and rate are arithmetic, not a fight: with a roller that has no dice
    # left to hand out, both must still produce their numbers.
    exhausted = roller()
    original = cli._roller
    cli._roller = lambda args: exhausted
    try:
        assert run(capsys, "rate", "--monsters", "goblin x4")[0] == 0
        assert run(capsys, "budget")[0] == 0
    finally:
        cli._roller = original
