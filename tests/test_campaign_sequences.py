"""#188: the whole campaign, in sequences, against invariants.

The shipped tests check one thing at a time and one fixture at a time.
`test_sheet_hit_dice.py` round-trips Hit Dice through one inline sheet and
through `Kairos_Level1.md`. `test_state_integrity.py` kills the process at every
truncation point of one file at a time. `test_receipts.py` proves an *attack*
leaves signed evidence.

What none of them do is run a session from end to end and then ask whether the
campaign is still coherent: a sheet that reads back what was written, a receipt
for every roll the sequence made, a tracker that matches the fight, a ledger that
matches the sheet, and numbers still inside the bounds the rules set. That is
this file, and it extends those three rather than repeating them:

  * `test_sheet_hit_dice.py` keeps the per-field Hit Dice round trip. Here the
    same round trip is run over **every** sheet fixture the tree ships, found by
    globbing rather than named, so a fixture added later is covered without
    editing this file. Nothing about Hit Dice parsing is asserted twice.
  * `test_state_integrity.py` keeps the crash and corruption cases. Here the
    campaign is not killed at all: it is played, and the point is that a played
    campaign is not a corrupted one.
  * `test_receipts.py` keeps the attack receipts and the chain-verification
    tests. Here the chain has to survive *four* kinds of roll across a whole
    session, and the assertion is that it still verifies.

Fixtures: `tmp_path` campaigns built from the committed, player-facing
`tests/fixtures/Kairos_Level1.md`, with monster statistics from the committed
`tests/fixtures/srd_monsters_sample.json` through the same `build_srd` path
`tests/tactics_fixtures.py` already uses. No real campaign is read and nothing
DM-sealed is touched.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

from tests.tactics_fixtures import (ROOT, RULES, _build, _RAW,
                                    rules as _dn5e_rules)

from tactics import cli, receipts, state, sync   # noqa: E402
from tactics.state import Token            # noqa: E402

# tactics_sheet, loaded the way tactics_rules loads its own siblings, so there is
# one copy of the parser in the process and this test cannot drift from the one
# the engine uses.
_DN5E = sys.modules[type(RULES).__module__]
SHEET_MOD = _DN5E._sheet_module()

KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")
BLANK_TEMPLATE = ROOT / "templates" / "character-sheet.md"

# The shipped Kairos sheet records XP the way his campaign advances ("0
# (milestone levelling)"), which `xp.py` deliberately refuses to write a total
# into. Filling the field in is the only way the XP and level steps are testable,
# and it is a field the template already has.
assert "**XP:** 0 (milestone levelling" in KAIROS_MD, "the fixture's XP shape moved"
XP_SHEET = KAIROS_MD.replace("**XP:** 0 (milestone levelling; see world.md)",
                             "**XP:** 0 / 300")


def _sheet_fixtures() -> list:
    """Every character-sheet-shaped markdown file the tree ships.

    Discovered, not listed: the failure mode this guards against is a fourth
    sheet fixture landing and nothing checking that it round-trips.
    """
    return sorted((ROOT / "tests" / "fixtures").glob("*.md")) + [BLANK_TEMPLATE]


def _parseable(path: pathlib.Path) -> bool:
    try:
        SHEET_MOD.read_sheet(path.read_text(encoding="utf-8"), path.stem.lower(), (0, 0))
    except ValueError:
        return False
    return True


# The blank template has no HP, so it has nothing to round-trip. It is still
# checked, by test_a_sheet_fixture_either_parses_or_refuses_by_name, and it is
# left out of the round-trip parametrisations rather than skipped there: a skip
# in a suite is green for a reason nobody reads, and this one is a fact.
ROUND_TRIPPED = [p for p in _sheet_fixtures() if _parseable(p)]


# ─── every fixture sheet round-trips ─────────────────────────────────────────

def test_the_fixture_discovery_is_not_vacuous():
    found = _sheet_fixtures()
    assert KAIROS_MD_PATH() in found and BLANK_TEMPLATE in found, found
    assert ROUND_TRIPPED == [KAIROS_MD_PATH()], (
        "a new sheet fixture landed and is not round-tripped: %s" % (found,))


def KAIROS_MD_PATH() -> pathlib.Path:
    return ROOT / "tests" / "fixtures" / "Kairos_Level1.md"


@pytest.mark.parametrize("path", _sheet_fixtures(), ids=lambda p: p.name)
def test_a_sheet_fixture_either_parses_or_refuses_by_name(path):
    """The blank template is a template, so it has no numbers to read. What it
    must not do is produce a token: a sheet read is the entry to every write-back
    the campaign ever makes."""
    text = path.read_text(encoding="utf-8")
    try:
        token = SHEET_MOD.read_sheet(text, path.stem.lower(), (0, 0))
    except ValueError as e:
        assert path == BLANK_TEMPLATE, f"{path.name} is not a sheet the parser can read: {e}"
        assert "**HP:**" in str(e), f"{path.name} refused for the wrong reason: {e}"
        return
    assert token.name and token.max_hp > 0 and token.name != "<Name>"


@pytest.mark.parametrize("path", ROUND_TRIPPED, ids=lambda p: p.name)
def test_a_sheet_fixture_round_trips_unchanged(path):
    """`write_back` of a token nobody has touched must be the file it came from.

    This is the property every write-back depends on and the one that makes a
    campaign's own file safe to hand to `combat.py end`: the engine's write is a
    diff, so a write that changes nothing must change nothing.
    """
    text = path.read_text(encoding="utf-8")
    token = SHEET_MOD.read_sheet(text, path.stem.lower(), (0, 0))
    out = SHEET_MOD.write_back(text, token)
    assert out == text, [l for l in out.splitlines() if l not in text.splitlines()]


@pytest.mark.parametrize("path", ROUND_TRIPPED, ids=lambda p: p.name)
def test_a_sheet_fixture_round_trips_every_combat_field(path):
    """The sequence version of `test_sheet_hit_dice.py`'s round trip: each field
    the engine writes is set, written, and read back equal, for every fixture.

    Hit Dice parsing itself is asserted in `test_sheet_hit_dice.py`; what is new
    here is that it holds for a fixture nobody named, and that HP, temp HP, death
    saves, slots and conditions all survive the same trip.
    """
    text = path.read_text(encoding="utf-8")
    token = SHEET_MOD.read_sheet(text, path.stem.lower(), (0, 0))
    token.hp = max(1, token.max_hp - 1)
    token.temp_hp = 3
    token.death_saves.update(successes=1, failures=2)
    token.extra["slots"] = {"1": {"total": 2, "used": 2}}
    if token.extra.get("hit_dice"):
        token.extra["hit_dice"] = dict(token.extra["hit_dice"], remaining=1)
    token.conditions = ["exhaustion"]
    once = SHEET_MOD.write_back(text, token)
    twice = SHEET_MOD.write_back(once, SHEET_MOD.read_sheet(once, "kairos", (0, 0)))
    assert twice == once, "write_back is not idempotent"

    again = SHEET_MOD.read_sheet(once, "kairos", (0, 0))
    assert (again.hp, again.max_hp) == (token.hp, token.max_hp), once
    assert again.temp_hp == 3, once
    assert again.death_saves == {"successes": 1, "failures": 2}, once
    assert again.extra["slots"]["1"] == {"total": 2, "used": 2}, once
    assert "exhaustion" in again.conditions, once
    if token.extra.get("hit_dice"):
        assert again.extra["hit_dice"]["remaining"] == 1, once
        assert again.extra["hit_dice"]["total"] == token.extra["hit_dice"]["total"], once
    # And the untouched parts of the sheet are untouched: the prose, the attacks,
    # the features. A rest must not eat a character's backstory.
    for line in ("## Character Pillar", "| Fire Bolt |", "**Player:** Quentin"):
        if line in text:
            assert line in once, f"{path.name}: write_back dropped {line!r}"


# ─── a session, end to end ───────────────────────────────────────────────────

@pytest.fixture
def camp(tmp_path, monkeypatch):
    """A temporary campaign with one XP-tracking sheet, a clock and no fight."""
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(XP_SHEET, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: auto\n", encoding="utf-8")
    (d / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    (d / "calendar.json").write_text(json.dumps({
        "day": 15, "month": 8, "year": 1247, "hour": 20,
        "months": ["Frostfall", "Deepwinter", "Thawmonth", "Seedtime", "Bloomtide",
                   "Highsun", "Harvestmoon", "Duskfall"],
        "month_length": 30, "day_names": [], "events": []}, indent=2), encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    monkeypatch.setattr(_DN5E, "_lookup_monster",
                        lambda name: _build._norm_monster(
                            _RAW[name.lower().replace(" ", "-")]))
    return d


def run(capsys, *argv) -> tuple:
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def sheet(camp) -> str:
    return (camp / "characters" / "Kairos.md").read_text(encoding="utf-8")


def bounds(camp, label: str) -> Token:
    """Re-read the sheet from disk and assert every bounded number is in bounds."""
    token = SHEET_MOD.read_sheet(sheet(camp), "kairos", (0, 0))
    hd = token.extra.get("hit_dice")
    assert 0 <= token.hp <= token.max_hp, f"{label}: HP {token.hp}/{token.max_hp}"
    assert token.temp_hp >= 0, f"{label}: temp HP {token.temp_hp}"
    for save, value in token.death_saves.items():
        assert 0 <= value <= 3, f"{label}: death {save} {value}"
    if hd:
        assert 0 <= hd["remaining"] <= hd["total"], f"{label}: hit dice {hd}"
    for level, slot in (token.extra.get("slots") or {}).items():
        assert 0 <= slot["used"] <= slot["total"], f"{label}: slot {level} {slot}"
    xp = re.search(r"\*\*XP:\*\*\s*(\d+)\s*/\s*(\d+)", sheet(camp))
    assert xp and int(xp.group(1)) >= 0, f"{label}: XP {xp and xp.group(0)}"
    return token


def _session(capsys, camp) -> list:
    """One whole session, through the real command line, in order.

    start -> damage both ways -> cast -> short rest -> long rest -> snapshot ->
    end (XP). Every step is the GM-facing command a table types, and each one
    either rolls or writes, which is what the receipts and the invariants are
    then read against. The turn is walked around rather than skipped so the
    sequence is real: a second action needs a second turn, and a Hit Die needs
    Kairos to be hurt.
    """
    steps = []
    assert run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--monster", "giant frog@M11",
               "--seed", "3")[0] == 0
    steps.append(("start", "Grid combat on Frog Pond"))
    # Spell reactions off: a frog's bite asks Kairos about Silvery Barbs and then
    # about Shield, and a sequence test should not be about answering them.
    assert run(capsys, "reactions", "kairos", "off")[0] == 0
    capsys.readouterr()
    # Kairos first and both frogs on the bank beside him, so the fight is close
    # enough for a bite and a Magic Missile.
    _give_turn(camp, actor="kairos")
    code, out = run(capsys, "attack", "kairos", "frog-1", "fire", "bolt", "--seed", "11")
    assert code == 0, out
    steps.append(("attack", out))
    # A frog's turn: it bites, so Kairos is hurt and the short rest has a Hit Die
    # to spend, which is the roll that has to be receipted.
    _give_turn(camp, actor="frog-1")
    code, out = run(capsys, "attack", "frog-1", "kairos", "--seed", "7")
    assert code == 0, out
    steps.append(("damage", out))
    assert kairos_hp(camp)[0] < 8, "the frog's bite has to land for the rest to roll"
    _give_turn(camp, actor="kairos")
    code, out = run(capsys, "cast", "kairos", "magic", "missile", "frog-2", "--seed", "13")
    assert code == 0, out
    steps.append(("cast", out))
    code, out = run(capsys, "rest", "short", "--for-me", "--seed", "17")
    assert code == 0, out
    steps.append(("rest short", out))
    code, out = run(capsys, "rest", "long")
    assert code == 0, out
    steps.append(("rest long", out))
    code, out = run(capsys, "status")
    assert code == 0, out
    steps.append(("status", out))
    code, out = run(capsys, "end")
    assert code == 0, out
    steps.append(("end", out))
    return steps


def kairos_hp(camp) -> tuple:
    """(hp, max_hp) straight out of the running encounter."""
    enc = state.load(state.encounter_path(camp))
    token = enc.tokens["kairos"]
    return token.hp, token.max_hp


def _give_turn(camp, actor: str) -> None:
    """Make it `actor`'s turn, with every frog beside Kairos on the bank.

    Adjacent, not merely in range: a square is 5 ft, and a giant frog's Bite
    reaches 5, so the bite that wounds Kairos (and gives the short rest a Hit Die
    to spend) needs the frog on the square next to him.
    """
    path = camp / "combat" / "encounter.json"
    enc = json.loads(path.read_text(encoding="utf-8"))
    spots = [(2, 6), (2, 7)]
    foes = [tid for tid, t in enc["tokens"].items() if t["side"] == "enemy"]
    for tid, (x, y) in zip(foes, spots):
        enc["tokens"][tid]["x"], enc["tokens"][tid]["y"] = x, y
    enc["order"] = [actor] + [t for t in enc["order"] if t != actor]
    enc["turn_index"] = 0
    enc["turn"] = {"actor": actor, "movement_budget": 30}
    path.write_text(json.dumps(enc), encoding="utf-8")


def test_a_whole_session_leaves_a_receipt_for_every_roll_it_made(camp, capsys):
    """The receipts test proves an attack leaves evidence. This proves a session
    does: damage, a cast and a spent Hit Die are three different kinds of roll in
    three different modules, and all of them have to land in the same signed
    chain without a gap."""
    _session(capsys, camp)
    recs = receipts._read(receipts.log_path(camp))
    assert recs, "a session that rolled left no receipts"
    assert [r["seq"] for r in recs] == list(range(1, len(recs) + 1)), "a gap in the chain"
    kinds = {r["kind"] for r in recs}
    assert {"attack", "cast", "rest"} <= kinds, kinds
    actors = {r["actor"] for r in recs}
    assert "kairos" in actors, actors
    assert receipts.verify(camp)["ok"] is True, receipts.verify(camp)["lines"]
    notations = {r["roll"]["notation"] for r in recs}
    assert "1d20+6" in notations, notations              # the attack roll
    assert any(n.startswith("1d4") for n in notations), notations   # Magic Missile darts


def test_a_whole_session_leaves_the_campaign_coherent(camp, capsys):
    """Every store the sequence touched says the same thing afterwards."""
    _session(capsys, camp)
    # the sheet: healed, slots back, hit dice back, exhaustion gone
    token = bounds(camp, "after the session")
    assert (token.hp, token.max_hp) == (8, 8), token.hp
    assert token.extra["slots"]["1"] == {"total": 2, "used": 0}, token.extra["slots"]
    assert token.extra["hit_dice"]["remaining"] == 1, token.extra["hit_dice"]
    # the tracker: one PC, no monster, death saves zeroed
    tracked = json.loads((camp / "tracker.json").read_text(encoding="utf-8"))
    assert set(tracked) == {"kairos"}, tracked
    assert tracked["kairos"]["death_saves"] == {"successes": 0, "failures": 0,
                                                "stable": False}, tracked
    # the session log: one combat summary, and not under the template
    log = (camp / "session-log.md").read_text(encoding="utf-8")
    assert log.count("### Grid combat: Frog Pond") == 1, log
    assert "- Kairos: 8/8 HP." in log, log
    # the ledger: the XP the sheet now carries
    ledger = [json.loads(line) for line
              in (camp / "xp-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(ledger) == 1 and ledger[0]["character"] == "Kairos", ledger
    assert ledger[0]["total_after"] == token_xp(camp), ledger
    # and the fight is over
    saved = json.loads((camp / "combat" / "encounter.json").read_text(encoding="utf-8"))
    assert saved["status"] == "ended", saved["status"]


def token_xp(camp) -> int:
    m = re.search(r"\*\*XP:\*\*\s*(\d+)\s*/\s*(\d+)", sheet(camp))
    return int(m.group(1))


def test_the_fights_own_award_lands_on_the_sheet_and_the_ledger_agrees(camp, capsys):
    """The XP the fight was worth, written by `combat.py end` itself. Distinct
    from the level-up test below, which drives `xp.py award` directly: this one
    is about the engine's own award path and its two records agreeing."""
    _session(capsys, camp)
    after = token_xp(camp)
    assert after > 0, sheet(camp)
    ledger = [json.loads(line) for line
              in (camp / "xp-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert ledger[0]["total_after"] == after, (ledger, after)


def test_a_level_up_is_written_as_pending_and_never_as_a_silent_gain(camp, capsys):
    """`xp.py` does not level a character: it writes the total, and past the
    threshold it writes `⚠ LEVEL UP PENDING (Level N)` and stops. Levelling is a
    GM act with dice behind it, so the engine owes the flag and the ledger row,
    not the level.

    Three level-1-deadly awards (100 each, `xp.py`'s own table) cross Kairos's
    300. The threshold is checked here rather than assumed, so a table change
    fails this test rather than quietly making it vacuous.
    """
    import importlib.util
    _spec = importlib.util.spec_from_file_location("xp_seq", ROOT / "systems" / "dnd5e" / "xp.py")
    xp = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(xp)
    assert xp._next_level_xp(1) == 300, xp._next_level_xp(1)
    per = xp._xp_per_player("deadly", 1)
    assert per == 100, per

    for _ in range(3):
        # xp.py is a script with its own argparse, so it reads sys.argv. Saved and
        # restored rather than left pointing at an award: every other test in this
        # process inherits that argv, and a suite that ends with a stranger's
        # command line in it is a suite whose next failure is somebody else's.
        argv = sys.argv
        sys.argv = ["xp.py", "award", "--campaign", "demo", "--characters", "Kairos",
                    "--difficulty", "deadly"]
        try:
            xp.main()
        except SystemExit as e:
            assert not e.code, e.code
        finally:
            sys.argv = argv
        capsys.readouterr()
    assert "LEVEL UP PENDING (Level 2)" in sheet(camp), sheet(camp)
    assert token_xp(camp) == 300, sheet(camp)
    rows = [json.loads(line) for line
            in (camp / "xp-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert [r["total_after"] for r in rows] == [100, 200, 300], rows


def test_a_second_fight_and_award_keeps_the_ledger_and_the_sheet_in_step(camp, capsys):
    """One award is a single write. Two is the sequence: the ledger must not
    duplicate a row, and the sheet's total must be the sum of the rows."""
    _session(capsys, camp)
    first = token_xp(camp)
    ledger = (camp / "xp-ledger.jsonl").read_text(encoding="utf-8")
    assert ledger.count('"character": "Kairos"') == 1, ledger
    assert run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--seed", "3")[0] == 0
    capsys.readouterr()
    assert run(capsys, "end")[0] == 0
    capsys.readouterr()
    rows = [json.loads(line) for line
            in (camp / "xp-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 2, rows
    assert token_xp(camp) == first + rows[1]["awarded"], (rows, token_xp(camp))


def test_the_bounds_hold_at_every_step_not_only_at_the_end(camp, capsys):
    """The point of a sequence test: a number can be inside its bounds at the end
    and have been outside it in the middle, which is where the player sees it.

    Checked against the *encounter* while the fight is running, because that is
    where the numbers live until `end` writes them out, and against the sheet
    afterwards.
    """
    assert run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--monster", "giant frog@M11",
               "--seed", "3")[0] == 0
    capsys.readouterr()
    assert run(capsys, "reactions", "kairos", "off")[0] == 0
    capsys.readouterr()

    def live(label: str) -> Token:
        enc = state.load(state.encounter_path(camp))
        for token in enc.tokens.values():
            assert 0 <= token.hp <= token.max_hp, f"{label}: {token.name} HP {token.hp}"
            assert token.temp_hp >= 0, f"{label}: {token.name} temp {token.temp_hp}"
            for save, value in token.death_saves.items():
                assert 0 <= value <= 3, f"{label}: {token.name} death {save} {value}"
            hd = token.extra.get("hit_dice")
            if hd:
                assert 0 <= hd["remaining"] <= hd["total"], f"{label}: {token.name} {hd}"
            for level, slot in (token.extra.get("slots") or {}).items():
                assert 0 <= slot["used"] <= slot["total"], f"{label}: {token.name} {slot}"
        return enc.tokens["kairos"]

    kairos = live("started")
    assert kairos.hp == kairos.max_hp == 8, kairos.hp
    assert kairos.extra["slots"]["1"]["used"] == 0

    _give_turn(camp, actor="kairos")
    assert run(capsys, "attack", "kairos", "frog-1", "fire", "bolt", "--seed", "11")[0] == 0
    capsys.readouterr()
    live("after the attack")

    _give_turn(camp, actor="frog-1")
    assert run(capsys, "attack", "frog-1", "kairos", "--seed", "7")[0] == 0
    capsys.readouterr()
    kairos = live("after the bite")
    assert kairos.hp < 8, "the bite has to land for the rest to have work to do"

    _give_turn(camp, actor="kairos")
    assert run(capsys, "cast", "kairos", "magic", "missile", "frog-2", "--seed", "13")[0] == 0
    capsys.readouterr()
    kairos = live("after the cast")
    assert kairos.extra["slots"]["1"]["used"] == 1, kairos.extra["slots"]
    assert kairos.hp <= kairos.max_hp

    assert run(capsys, "rest", "short", "--for-me", "--seed", "17")[0] == 0
    capsys.readouterr()
    kairos = live("after the short rest")
    assert kairos.extra["hit_dice"]["remaining"] == 0, kairos.extra["hit_dice"]
    assert kairos.hp <= kairos.max_hp, "a Hit Die cannot take a character past its maximum"

    assert run(capsys, "rest", "long")[0] == 0
    capsys.readouterr()
    kairos = live("after the long rest")
    assert kairos.hp == kairos.max_hp, kairos.hp
    assert kairos.extra["hit_dice"]["remaining"] == kairos.extra["hit_dice"]["total"] == 1, \
        kairos.extra["hit_dice"]

    # A second long rest with nothing spent: the restoration is capped at the
    # character's own total, not added to it. Wizard 1 has one Hit Die, and after
    # two rests he still has one, not two.
    assert run(capsys, "rest", "long")[0] == 0
    capsys.readouterr()
    kairos = live("after the second long rest")
    assert kairos.extra["hit_dice"]["remaining"] == 1, kairos.extra["hit_dice"]

    assert run(capsys, "end")[0] == 0
    capsys.readouterr()
    bounds(camp, "after end")


def test_the_snapshot_after_each_step_is_serialisable_and_says_who_is_where(camp, capsys):
    """`sync.snapshot` is what the display polls. A campaign that cannot be
    rendered cannot be played, and a snapshot is only exercised in this repo by
    the display's own tests, against a hand-built encounter."""
    assert run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--seed", "3")[0] == 0
    capsys.readouterr()
    for label, enc in (("fresh", state.load(state.encounter_path(camp))),):
        snap = sync.snapshot(enc, enc.meta)
        json.dumps(snap, default=str)                      # must not raise
        assert snap["status"] == "active" and snap["round"] == 1, snap
        names = {t["name"]: t for t in snap["tokens"]}
        assert names["Kairos"]["hp"] == names["Kairos"]["max_hp"] == 8, names["Kairos"]
        assert names["Giant Frog"]["slots"] == {}, "a monster has no spell slots"
        assert names["Kairos"]["slots"] == {"1": {"total": 2, "used": 0}}, names["Kairos"]
    _give_turn(camp, actor="kairos")
    assert run(capsys, "attack", "kairos", "frog-1", "fire", "bolt", "--seed", "11")[0] == 0
    capsys.readouterr()
    snap = sync.snapshot(state.load(state.encounter_path(camp)), {})
    json.dumps(snap, default=str)
    frogs = [t for t in snap["tokens"] if t["name"].startswith("Giant Frog")]
    assert any(t["hp"] < t["max_hp"] for t in frogs), frogs
    assert all(0 <= t["hp"] <= t["max_hp"] for t in snap["tokens"]), snap["tokens"]
    assert run(capsys, "end")[0] == 0
    capsys.readouterr()
    snap = sync.snapshot(state.load(state.encounter_path(camp)), {})
    json.dumps(snap, default=str)
    assert snap["status"] == "ended", snap["status"]


def test_a_rest_written_by_the_sequence_is_readable_by_the_next_session(camp, capsys):
    """The hand-off: a second `start` must read the sheet the first session left,
    not the sheet the campaign started with. This is the round trip the whole
    system exists for, across a process boundary."""
    _session(capsys, camp)
    after = sheet(camp)
    assert "**HP:** 8 / 8" in after
    assert run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--seed", "3")[0] == 0
    capsys.readouterr()
    enc = state.load(state.encounter_path(camp))
    kairos = enc.tokens["kairos"]
    assert kairos.hp == 8 and kairos.max_hp == 8, kairos.hp
    assert kairos.extra["slots"]["1"] == {"total": 2, "used": 0}, kairos.extra["slots"]
    assert kairos.extra["hit_dice"] == {"die": "d6", "total": 1, "remaining": 1}, kairos.extra


# ─── feature-specific tests, deferred on purpose ─────────────────────────────

def test_the_features_this_issue_defers_are_still_absent():
    """#188's fourth criterion: "feature-specific tests await implementations".

    Written as an assertion of absence rather than a to-do, so it is a fact that
    can be checked and it says what to do the day the fact stops holding. When
    one of these lands, this test fails and names the deferred test that is now
    due:

      * inventory and other bounded resources (CAT-6) — `fightq.py` has an
        "inventory" topic that computes nothing, and `Rules` has no inventory
        method, so there is no bounded resource to hold inside its bound.

    `FakeRules` (#195) was on this list and is no longer. It landed, in PR #224
    ("test(rules): prove the Rules contract against a second system"), together
    with the conformance suite that was waiting for it. That is the list working
    as designed: the tripwire fired, and the work it named got done. So the
    entry is not deleted silently -- it is turned around. The assertion below now
    says the thing that is TRUE, and keeps its teeth: `FakeRules` exists AND
    something checks the engine against it. Before, the suite could have deleted
    `tests/test_rules_conformance.py` and stayed green, because the deferral
    only ever asserted that the stub was missing.
    """
    from tactics import rules as rules_mod

    # ── #195's FakeRules landed (#224); what it was deferred FOR must still exist ──
    conformance = ROOT / "tests" / "test_rules_conformance.py"
    assert conformance.exists(), (
        "#195's FakeRules landed in #224 with its conformance suite. If that suite "
        "is being removed, the Rules interface is no longer provable against a "
        "second system and this file needs to say so on purpose, not by deletion.")
    body = conformance.read_text(encoding="utf-8")
    assert "FakeRules" in body, (
        "test_rules_conformance.py no longer mentions FakeRules, so the engine's "
        "interface is not being checked against the toy system any more. #224 is "
        "what made this possible; losing it silently is the failure this guards.")
    assert not hasattr(rules_mod.Rules, "inventory"), \
        "Rules.inventory has landed: the bounded-resource invariant is now due here"
