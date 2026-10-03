"""#179: a rest between fights, off the campaign's own character sheets.

`rest short|long` reached `cli.run`'s encounter load and stopped there, so the
one rest a campaign actually spends its evenings on was the one that could not
be asked for: with no combat/encounter.json the command answered "No grid
combat is running. Rest between fights, or start one first." -- telling the GM
to do the very thing it had just refused. `end` leaves the encounter file on
disk with status "ended", so even the after-a-fight case said the same thing.

What lands here, and the rules each is checked against (2014 PHB, and the
campaign's own systems/dnd5e/system.md "Rests" section):

  * the same `short_rest`/`long_rest` the fight path runs, over an Encounter
    built in memory from characters/*.md, committed to four stores as one unit;
  * a short rest is refused for a creature at 0 HP (PHB p197: unconscious, and
    the unconscious condition says it cannot move or speak);
  * a long rest is eight hours and the in-world clock rolls over midnight, a
    day and a month on the way (systems/dnd5e/system.md: "Long rest (8 hours)");
  * a refusal mutates nothing at all, and a failed write leaves no store
    half-updated;
  * faction clocks do not move: they tick on whole days (scripts/calendar.py
    `_tick_world`), and no rest is a whole day.

Every fixture is a `tmp_path` campaign built from the committed sheet in
tests/fixtures. No real campaign is read, and nothing DM-sealed is touched.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

from tests.tactics_fixtures import ROOT, caster, encounter, roller

sys.path.insert(0, str(ROOT / "scripts"))

import safeio                                    # noqa: E402
from tactics import cli, rest                    # noqa: E402

KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")

# Kairos' own sheet, wounded and poisoned: the state a party is actually in when
# somebody types "rest". One Hit Die left (Wizard 1, 1d6), so a long rest has
# half a die to restore and a short rest has exactly one to spend. The Conditions
# line is added because the shipped fixture has none, and write_back() inserts
# one the first time a lasting condition exists.
WOUNDED = KAIROS_MD.replace("- **HP:** 8 / 8 | **Temp HP:** 0",
                            "- **HP:** 2 / 8 | **Temp HP:** 0")
WOUNDED = WOUNDED.replace("- **Death Saves:** Successes: 0 | Failures: 0",
                          "- **Death Saves:** Successes: 0 | Failures: 0\n"
                          "- **Conditions:** poisoned")


@pytest.fixture
def camp(tmp_path, monkeypatch):
    """A temporary campaign with one wounded sheet, no fight and a clock.

    No encounter.json on purpose: that is the state #179 is about. The calendar
    is written straight into the campaign's own calendar.json (the shape
    `calendar.py init` produces) rather than by shelling out to it, so the test
    does not depend on a subprocess to set up its own precondition.
    """
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(WOUNDED, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "calendar.json").write_text(json.dumps({
        "day": 15, "month": 8, "year": 1247, "hour": 20,
        "months": ["Frostfall", "Deepwinter", "Thawmonth", "Seedtime", "Bloomtide",
                   "Highsun", "Harvestmoon", "Duskfall"],
        "month_length": 30, "day_names": [], "events": []}, indent=2), encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    return d


def run(capsys, *argv) -> tuple:
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def sheet(camp) -> str:
    return (camp / "characters" / "Kairos.md").read_text(encoding="utf-8")


def clock(camp) -> dict:
    return json.loads((camp / "calendar.json").read_text(encoding="utf-8"))


def tracker(camp) -> dict:
    return json.loads((camp / "tracker.json").read_text(encoding="utf-8"))


# ─── the headline: a rest that no longer needs a fight ─────────────────────────

def test_a_long_rest_out_of_combat_heals_the_sheet_and_advances_the_clock(camp, capsys):
    """Before #179 this exited 1 with "No grid combat is running"."""
    assert not (camp / "combat" / "encounter.json").exists(), "precondition: no fight"
    code, out = run(capsys, "rest", "long")
    assert code == 0, out
    assert "Kairos: healed 6 HP (now 8/8)." in out
    assert "Kairos: 1 Hit Dice restored (now 1/1)." in out
    assert "Kairos: cleared poisoned." in out
    text = sheet(camp)
    assert "**HP:** 8 / 8" in text
    assert "**Hit Dice:** 1d6 (remaining: 1)" in text
    assert "- **Conditions:** none" in text
    assert clock(camp)["day"] == 16 and clock(camp)["hour"] == 4, clock(camp)


def test_a_long_rest_out_of_combat_drops_one_level_of_exhaustion(camp, capsys):
    """The SRD's own sentence (systems/dnd5e/data/dnd5e_srd.json, conditions /
    exhaustion): "Finishing a long rest reduces a creature's exhaustion level by
    1". The condition lives on the sheet, so the rest has to clear it there."""
    (camp / "characters" / "Kairos.md").write_text(
        WOUNDED.replace("- **Conditions:** poisoned", "- **Conditions:** exhaustion"),
        encoding="utf-8")
    code, out = run(capsys, "rest", "long")
    assert code == 0 and "Kairos: cleared exhaustion." in out, out
    assert "- **Conditions:** none" in sheet(camp)


def test_the_out_of_combat_rest_also_mirrors_the_tracker(camp, capsys):
    run(capsys, "rest", "long")
    ent = tracker(camp)["kairos"]
    assert ent["conditions"] == [] and ent["death_saves"] == {"successes": 0,
                                                             "failures": 0, "stable": False}


def test_a_short_rest_out_of_combat_spends_a_hit_dice_and_moves_one_hour(camp, capsys):
    """The Hit Die face is the engine's own, so the exact healing is pinned one
    layer down (see test_the_spent_hit_die_is_the_die_plus_con_below); through
    the CLI what has to hold is that a die was spent and the sheet says so."""
    code, out = run(capsys, "rest", "short", "--for-me", "--seed", "5")
    assert code == 0, out
    assert re.search(r"Kairos: spent 1 Hit Die \(d6 \+2\), healed \d+ HP \(now 8/8\)\.", out)
    assert "**HP:** 8 / 8" in sheet(camp)
    assert "**Hit Dice:** 1d6 (remaining: 0)" in sheet(camp)
    assert clock(camp)["hour"] == 21 and clock(camp)["day"] == 15


def test_the_spent_hit_die_is_the_die_plus_con(camp):
    """One layer down, with a scripted die: a Hit Die heals its own face plus
    the Constitution modifier (systems/dnd5e/system.md, Rests)."""
    kairos = caster()                          # CON 14, so +2
    kairos.hp = 2
    kairos.extra["hit_dice"] = {"die": "d6", "total": 1, "remaining": 1}
    enc = encounter([kairos])
    dice = roller(3)
    dice.for_me = True
    lines = rest.short_rest(enc, None, dice)
    assert lines == ["Kairos: spent 1 Hit Die (d6 +2), healed 5 HP (now 7/8)."], lines
    assert kairos.extra["hit_dice"]["remaining"] == 0


def test_a_short_rest_does_not_spend_a_players_hit_dice_without_for_me(camp, capsys):
    code, out = run(capsys, "rest", "short")
    assert code == 0 and "1/1 Hit Dice (d6 +2 each) available" in out
    assert "**HP:** 2 / 8" in sheet(camp), "nothing was healed"
    assert "**Hit Dice:** 1d6 (remaining: 1)" in sheet(camp)


def test_a_long_rest_restores_every_spell_slot_on_the_sheet(camp, capsys):
    spent = WOUNDED.replace("| 1st | 2 | 0 |", "| 1st | 2 | 2 |")
    (camp / "characters" / "Kairos.md").write_text(spent, encoding="utf-8")
    code, out = run(capsys, "rest", "long")
    assert code == 0 and "Kairos: all spell slots restored." in out
    assert "| 1st | 2 | 0 |" in sheet(camp)


# ─── zero HP: the refusal ────────────────────────────────────────────────────

def test_a_short_rest_is_refused_for_a_creature_at_zero_hit_points(camp, capsys):
    """PHB p197. Before the refusal, `_spend_hit_dice` rolled healing onto a
    dying character: 0 HP is not "needs healing", it is unconscious."""
    down = WOUNDED.replace("- **HP:** 2 / 8 | **Temp HP:** 0",
                           "- **HP:** 0 / 8 | **Temp HP:** 0")
    (camp / "characters" / "Kairos.md").write_text(down, encoding="utf-8")
    code, out = run(capsys, "rest", "short", "--for-me", "--seed", "5")
    assert code == 0, out
    assert "Kairos is at 0 HP: unconscious" in out
    assert "Hit Die" in out and "short rest has no" in out
    assert "**HP:** 0 / 8" in sheet(camp), "a short rest healed a dying character"
    assert "**Hit Dice:** 1d6 (remaining: 1)" in sheet(camp), "and spent a Hit Die on it"


def test_a_long_rest_is_not_refused_at_zero_hit_points(camp, capsys):
    """The same creature, eight hours later. `rest long` is the answer to being
    at 0 HP, so the zero-HP refusal must not swallow it."""
    down = WOUNDED.replace("- **HP:** 2 / 8 | **Temp HP:** 0",
                           "- **HP:** 0 / 8 | **Temp HP:** 0")
    (camp / "characters" / "Kairos.md").write_text(down, encoding="utf-8")
    code, out = run(capsys, "rest", "long")
    assert code == 0 and "Kairos: healed 8 HP (now 8/8)." in out


# ─── eight hours, and the rollovers that come with them ───────────────────────

@pytest.mark.parametrize("start,expect", [
    ({"day": 15, "month": 8, "hour": 20}, {"day": 16, "month": 8, "hour": 4}),
    ({"day": 15, "month": 8, "hour": 23}, {"day": 16, "month": 8, "hour": 7}),
    ({"day": 30, "month": 8, "hour": 20}, {"day": 1, "month": 1, "year": 1248, "hour": 4}),
])
def test_eight_hours_roll_over_night_day_and_year(camp, capsys, start, expect):
    data = clock(camp)
    data.update(start)
    (camp / "calendar.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    code, out = run(capsys, "rest", "long")
    assert code == 0, out
    got = clock(camp)
    for key, want in expect.items():
        assert got[key] == want, (key, got)


def test_a_short_rest_is_one_hour_and_does_not_roll_the_day(camp, capsys):
    data = clock(camp)
    data.update({"day": 30, "month": 8, "hour": 23})
    (camp / "calendar.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert run(capsys, "rest", "short")[0] == 0
    got = clock(camp)
    assert (got["day"], got["month"], got["year"], got["hour"]) == (1, 1, 1248, 0)


# ─── rejection mutates nothing ───────────────────────────────────────────────

def test_a_rest_for_someone_who_is_not_in_the_party_changes_nothing(camp, capsys):
    before = {p.name: p.read_text(encoding="utf-8")
              for p in sorted((camp / "characters").glob("*"))}
    cal_before = (camp / "calendar.json").read_text(encoding="utf-8")
    code, out = run(capsys, "rest", "long", "--token", "Nobody")
    assert code == 1 and "no token 'Nobody'" in out, out
    assert {p.name: p.read_text(encoding="utf-8")
            for p in sorted((camp / "characters").glob("*"))} == before
    assert (camp / "calendar.json").read_text(encoding="utf-8") == cal_before
    assert not (camp / "tracker.json").exists(), "a refused rest wrote the tracker"


def test_a_rest_with_an_unreadable_sheet_changes_nothing(camp, capsys):
    """One bad sheet must not heal the good ones. Before the transaction, the
    good sheet was written first and the failure came after."""
    (camp / "characters" / "Broken.md").write_text("# Broken\n\nNo HP line here.\n",
                                                  encoding="utf-8")
    good_before = (camp / "characters" / "Kairos.md").read_text(encoding="utf-8")
    cal_before = (camp / "calendar.json").read_text(encoding="utf-8")
    code, out = run(capsys, "rest", "long")
    assert code == 1 and "Kairos" not in out.split("\n")[0], out
    assert (camp / "characters" / "Kairos.md").read_text(encoding="utf-8") == good_before
    assert (camp / "calendar.json").read_text(encoding="utf-8") == cal_before


def test_a_rest_with_nobody_to_rest_is_refused(camp, capsys):
    for p in (camp / "characters").glob("*.md"):
        p.unlink()
    code, out = run(capsys, "rest", "long")
    assert code == 1 and "No character sheets" in out


# ─── all or none ─────────────────────────────────────────────────────────────

def test_a_failed_write_rolls_every_store_back(camp, capsys, monkeypatch):
    """The sheets go first, the tracker second, the clock third. Kill the third
    write and the first two must be back where they were: a campaign that is
    half a night older is worse than one that did not rest at all."""
    sheet_before = sheet(camp)
    cal_before = (camp / "calendar.json").read_text(encoding="utf-8")
    real = safeio.atomic_write_text
    seen = []

    def boom(path, text, *a, **k):
        seen.append(pathlib.Path(path).name)
        if pathlib.Path(path).name == "calendar.json":
            raise OSError("disk full")
        return real(path, text, *a, **k)

    monkeypatch.setattr(safeio, "atomic_write_text", boom)
    with pytest.raises(OSError, match="disk full"):
        rest.cmd_rest_campaign(cli._parse(["-c", "demo", "rest", "long"]), camp, "demo", None)
    assert "Kairos.md" in seen and "calendar.json" in seen, seen
    assert sheet(camp) == sheet_before, "the sheet survived a rolled-back rest"
    assert (camp / "calendar.json").read_text(encoding="utf-8") == cal_before
    assert not (camp / "tracker.json").exists(), "the tracker survived a rolled-back rest"


def test_a_successful_rest_writes_the_clock_last(camp, capsys, monkeypatch):
    """The clock is the commit, not the first thing to happen: if it is written
    before the sheets, a crash between them heals the party and leaves the world
    where it was."""
    order = []
    real = safeio.atomic_write_text

    def note(path, text, *a, **k):
        order.append(pathlib.Path(path).name)
        return real(path, text, *a, **k)

    monkeypatch.setattr(safeio, "atomic_write_text", note)
    assert run(capsys, "rest", "long")[0] == 0
    assert order.index("Kairos.md") < order.index("calendar.json"), order
    assert "tracker.json" in order, order


def test_the_fight_path_advances_the_clock_through_advance_calendar(camp, capsys):
    """`cli.run` still calls advance_calendar() after an in-fight rest, and that
    function was the subprocess. It has to keep being the one that moves the
    clock, or the combat path loses its date."""
    assert cli.main(["-c", "demo", "start", "frog-pond", "--pc", "Kairos@B7",
                     "--monster", "giant frog@J5", "--seed", "3"]) == 0
    capsys.readouterr()
    code, out = run(capsys, "rest", "long")
    assert code == 0 and "Long rest (+8 hours)" in out, out
    assert (clock(camp)["day"], clock(camp)["hour"]) == (16, 4)


# ─── the faction store is inside the transaction, and never inside its effect ─

def test_a_rest_leaves_the_faction_clocks_byte_for_byte(camp, capsys):
    """scripts/calendar.py `_tick_world` ticks factions on whole days only
    ("Rest hours and single hours change nothing a faction can act on"), so a
    rest must not move one. The files are still inside the transaction, so a
    rollback would reach them; here they must simply not change."""
    (camp / "factions.json").write_text(json.dumps({"factions": {"Red Hand": {"current": 2}}}),
                                        encoding="utf-8")
    (camp / "faction_log.md").write_text("# faction log\n", encoding="utf-8")
    factions_before = (camp / "factions.json").read_text(encoding="utf-8")
    log_before = (camp / "faction_log.md").read_text(encoding="utf-8")
    state_before = (camp / "state.md").read_text(encoding="utf-8")
    assert run(capsys, "rest", "long")[0] == 0
    assert (camp / "factions.json").read_text(encoding="utf-8") == factions_before
    assert (camp / "faction_log.md").read_text(encoding="utf-8") == log_before
    assert (camp / "state.md").read_text(encoding="utf-8") == state_before


def test_a_rest_needs_no_calendar(camp, capsys, monkeypatch):
    monkeypatch.setattr(rest, "_calendar_module", lambda: _NoCalendar())
    code, out = run(capsys, "rest", "long")
    assert code == 0, out
    assert "Kairos: healed 6 HP" in out and "Long rest (+8 hours)" not in out


class _NoCalendar:
    """A campaign whose clock cannot be resolved at all."""

    def _load(self, campaign):
        raise SystemExit(2)

    _send_date = staticmethod(lambda cal: None)


# ─── the fight path is untouched ──────────────────────────────────────────────

def test_a_rest_during_a_fight_still_goes_through_the_encounter(camp, capsys):
    """Preserving the combat rest is the acceptance criterion, so it is asserted
    even though it passes both before and after this change: the fight's own HP
    is what changes, and the sheet is not written until `end`."""
    assert cli.main(["-c", "demo", "start", "frog-pond", "--pc", "Kairos@B7",
                     "--monster", "giant frog@J5", "--seed", "3"]) == 0
    capsys.readouterr()
    path = camp / "combat" / "encounter.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["tokens"]["kairos"]["hp"] = 1
    saved["order"], saved["turn_index"], saved["turn"]["actor"] = ["kairos"], 0, "kairos"
    path.write_text(json.dumps(saved), encoding="utf-8")
    sheet_before = sheet(camp)

    code, out = run(capsys, "rest", "long")
    assert code == 0 and "Kairos: healed 7 HP (now 8/8)." in out, out
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "active"
    assert sheet(camp) == sheet_before, "the fight path writes sheets at `end`, not at `rest`"


def test_a_rest_after_the_fight_ended_uses_the_campaign_path(camp, capsys):
    """`end` leaves encounter.json on disk with status 'ended'. Before #179 that
    file made `rest` take the combat branch and refuse."""
    assert cli.main(["-c", "demo", "start", "frog-pond", "--pc", "Kairos@B7",
                     "--monster", "giant frog@J5", "--seed", "3"]) == 0
    capsys.readouterr()
    assert cli.main(["-c", "demo", "end", "--no-xp"]) == 0
    capsys.readouterr()
    assert json.loads((camp / "combat" / "encounter.json").read_text(
        encoding="utf-8"))["status"] == "ended"
    (camp / "characters" / "Kairos.md").write_text(WOUNDED, encoding="utf-8")
    code, out = run(capsys, "rest", "long")
    assert code == 0 and "Kairos: healed 6 HP (now 8/8)." in out, out
    assert "**HP:** 8 / 8" in sheet(camp)


# ─── the json form names the stores it moved ──────────────────────────────────

def test_json_reports_the_sheets_the_slots_and_the_clock(camp, capsys):
    code = cli.main(["-c", "demo", "rest", "long", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["result"]["sheets"] == ["Kairos"]
    assert payload["result"]["targets"] == ["kairos"]
    assert payload["result"]["calendar"] is True
    assert payload["result"]["slots"]["kairos"]["1"]["used"] == 0
