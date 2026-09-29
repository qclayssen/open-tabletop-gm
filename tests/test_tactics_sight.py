"""Milestone 5: fog of war, the sight overlay, and theatre-of-the-mind parity.

sight.py only reads the engine's own line of sight and cover, so these tests
pin two things: the display is shown exactly what the PCs could see (never an
unseen enemy), and everything the map shows is also available as plain text
to a GM running with the display off.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from tests.tactics_fixtures import ScriptedDice, encounter, goblin, kairos, start
from tests.test_tactics_cli import begin, camp, run  # noqa: F401  (camp is a fixture)
from tactics import engine, grid, maps, roller, rules, sight, state, sync

# A wall down column C with a gap in the bottom row; a table (o) at E4.
ROWS = ["..#...",
        "..#...",
        "..#...",
        "....o.",
        "......"]


def fight(fog=None, gob=(4, 0), me=(0, 0)):
    k, g = kairos(pos=me), goblin("goblin-1", gob)
    enc = start(encounter([k, g], rows=ROWS), ["kairos", "goblin-1"])
    if fog:
        enc.meta["fog"] = fog
    return enc


def ids(snap):
    return [t["id"] for t in snap["tokens"]]


def fog_labels(snap):
    """The visible squares of a snapshot, back in label form from the runs."""
    return {grid.label((x, r[0])) for r in snap["fog"]["runs"] for x in range(r[1], r[2] + 1)}


# ─── visibility ───────────────────────────────────────────────────────────────

def test_visible_from_agrees_with_line_of_sight_on_every_map():
    for name in maps.available():
        rows = maps.load(name)["grid"]
        fast, slow = grid.Grid.from_dict(rows), grid.Grid.from_dict(rows)
        for origin in ((fast.width // 2, fast.height // 2),):
            want = {(x, y) for y in range(slow.height) for x in range(slow.width)
                    if slow.line_of_sight(origin, (x, y))}
            assert fast.visible_from(origin) == want, (name, origin)


def test_the_wall_hides_the_goblin():
    enc = fight()
    assert not enc.board().line_of_sight((0, 0), (4, 0))
    assert (4, 0) not in sight.fog(enc) and (0, 4) in sight.fog(enc)


# ─── fog of war in the players' snapshot ─────────────────────────────────────

def test_hide_fog_leaves_out_an_enemy_no_pc_sees():
    snap = sync.snapshot(fight())
    assert snap["fog"]["mode"] == "hide"
    assert ids(snap) == ["kairos"]
    seen = fog_labels(snap)
    assert "E1" not in seen and "A1" in seen
    assert snap["fog"]["count"] == len(seen)


def test_the_fog_runs_are_far_smaller_than_one_entry_per_square():
    """P8: the display polls this every turn, and a label per square was ~200
    strings of JSON each time. The runs must still cover exactly the same
    squares, and stay inside the grid."""
    enc = fight()
    snap = sync.snapshot(enc)
    runs, count = snap["fog"]["runs"], snap["fog"]["count"]
    assert runs, "a fight under fog reports runs"
    assert len(runs) < count
    board = enc.board()
    assert all(0 <= y < board.height and 0 <= x0 <= x1 < board.width for y, x0, x1 in runs)
    assert sum(x1 - x0 + 1 for _, x0, x1 in runs) == count
    assert fog_labels(snap) == {grid.label(p) for p in sight.fog(enc)}


def test_an_enemy_in_view_is_shown():
    assert ids(sync.snapshot(fight(gob=(4, 4)))) == ["kairos", "goblin-1"]


def test_dim_fog_shows_every_creature_and_off_shows_the_whole_map():
    snap = sync.snapshot(fight("dim"))
    assert ids(snap) == ["kairos", "goblin-1"] and snap["fog"]["mode"] == "dim"
    snap = sync.snapshot(fight("off"))
    assert ids(snap) == ["kairos", "goblin-1"] and snap["fog"] is None


def test_allies_are_always_shown_and_hidden_enemies_never():
    enc = fight("dim")
    enc.tokens["goblin-1"].side = "ally"
    assert sight.shown(enc, enc.tokens["goblin-1"], sight.fog(enc))
    enc.tokens["goblin-1"].side = "enemy"
    enc.tokens["goblin-1"].add_condition("hidden")
    assert ids(sync.snapshot(enc)) == ["kairos"]


def test_no_fog_once_no_pc_is_left_to_see():
    enc = fight()
    enc.tokens["kairos"].dead = True
    assert sight.fog(enc) is None and "goblin-1" in ids(sync.snapshot(enc))


# ─── sight: cover from one creature ──────────────────────────────────────────

def test_sight_lists_cover_and_what_is_out_of_sight():
    enc = fight()
    gm = sight.sight(enc, "kairos")
    assert gm["text"] == "From Kairos (A1): No line of sight: Goblin E1."
    assert "E1" not in gm["visible"] and "A5" in gm["visible"]
    assert "C1" not in gm["cover"]                  # a wall square is never shaded
    players = sight.sight(enc, "kairos", players=True)
    assert players["creatures"] == [] and players["text"] == "Kairos (A1) sees no other creature."


def test_a_table_gives_half_cover_as_in_an_attack():
    enc = fight("off", gob=(5, 3), me=(0, 3))
    data = sight.sight(enc, "kairos", players=True)
    assert data["creatures"][0]["sight"] == "half cover"
    assert data["cover"]["F4"] == "half"
    assert enc.board().cover((0, 3), (5, 3))["cover"] == 2


# ─── the command line: sight, fog, and parity with the display off ──────────

def test_sight_and_fog_commands(camp, capsys):
    begin(capsys)
    path = camp / "combat" / "encounter.json"
    before = path.read_text(encoding="utf-8")
    code, out = run(capsys, "sight", "kairos")
    assert code == 0 and out.startswith("From Kairos (B7):")
    assert path.read_text(encoding="utf-8") == before            # sight is a read
    code, out = run(capsys, "sight", "kairos", "--players", "--json")
    res = json.loads(out)["result"]
    assert res["from"] == "kairos" and "B7" in res["visible"]
    code, out = run(capsys, "fog", "dim")
    assert code == 0 and out.startswith("Fog of war: dim.")
    assert state.load(path).meta["fog"] == "dim"


def test_the_gm_text_does_not_depend_on_the_display(camp, capsys):
    """Theatre of the mind: with the display off, every fact the map shows
    (positions, HP, conditions, cover, line of sight) is in the text, and
    fog of war, a display setting, never changes what the GM reads."""
    begin(capsys)
    run(capsys, "condition", "frog-1", "add", "prone")
    outs = []
    for mode in ("hide", "dim", "off"):
        run(capsys, "fog", mode)
        outs.append((run(capsys, "status")[1], run(capsys, "sight", "kairos")[1]))
    assert outs[0] == outs[1] == outs[2]
    status, seen = outs[0]
    assert "Giant Frog 1 J5 18/18 [prone]" in status and "Kairos B7 8/8" in status
    assert "Giant Frog 1 J5" in seen and "Giant Frog 2 M11" in seen


# ─── no leaks: order, whose turn, the log, the sidebar ──────────────────────

def test_an_unseen_enemy_is_not_named_anywhere_in_the_snapshot():
    enc = fight()
    enc.log.append({"round": 1, "actor": "goblin-1", "kind": "move",
                    "text": "Goblin moves E1 to F1.", "rolls": [{"label": "Goblin"}]})
    enc.turn_index = 1                                       # the goblin's turn
    snap = sync.snapshot(enc)
    assert snap["order"] == ["kairos"]
    assert snap["current"] is None and snap["unseen_turn"] is True
    assert snap["log"][-1] == {"round": 1, "actor": "", "kind": "move",
                               "text": "An unseen creature moves E1 to F1.", "rolls": []}
    assert snap["turn"] == {}
    assert "Goblin" not in json.dumps(snap)
    assert enc.log[-1]["text"] == "Goblin moves E1 to F1."   # the GM's log is untouched


def test_a_seen_enemy_keeps_its_turn_and_log():
    enc = fight(gob=(4, 4))
    enc.log.append({"round": 1, "actor": "goblin-1", "kind": "move", "text": "Goblin moves.", "rolls": []})
    enc.turn_index = 1
    snap = sync.snapshot(enc)
    assert snap["current"] == "goblin-1" and not snap["unseen_turn"]
    assert snap["log"][-1]["text"] == "Goblin moves."


def test_the_sidebar_turn_order_hides_unseen_enemies(monkeypatch):
    sent = []
    monkeypatch.setattr(sync, "display_enabled", lambda: True)
    monkeypatch.setattr(sync, "_post", lambda path, payload: sent.append((path, payload)))
    enc = fight()
    enc.turn_index = 1
    sync.push_display(enc)
    stats = dict(sent)["/stats"]["turn_order"]
    assert stats["order"] == ["Kairos"] and stats["current"] == "Enemy turn"


def test_redaction_matches_whole_names_only():
    enc = fight()
    enc.tokens["goblin-1"].name = "Giant Frog 1"
    enc.tokens["kairos"].name = "Giant Frog 12"                 # in view
    out = sight.redact_log(enc, [{"text": "Giant Frog 12 waits.", "actor": "kairos", "rolls": [1]},
                                 {"text": "Giant Frog 1 hops.", "actor": "goblin-1", "rolls": [1]}],
                           sight.fog(enc))
    assert [e["text"] for e in out] == ["Giant Frog 12 waits.", "An unseen creature hops."]
    assert out[0]["rolls"] == [1] and out[1]["rolls"] == []


def test_a_rolls_odds_do_not_survive_the_redaction_of_an_unseen_creature():
    """A roll carries the chance it was made under (Roll.odds), and that number
    is about the creature: 60% to hit plus a damage die is a way to read back an
    AC. It has to go when the name does, which is why it lives inside the roll
    and not beside it on the log entry."""
    enc = fight(fog="hide", gob=(9, 0))                    # the goblin is out of sight
    assert not sight.shown(enc, enc.tokens["goblin-1"], sight.fog(enc))
    enc.log.append({"round": enc.round, "actor": "kairos", "kind": "attack",
                    "text": "Kairos Longsword -> Goblin: 4 vs AC 15, miss.",
                    "rolls": [{"who": "Kairos", "label": "Longsword vs Goblin",
                               "notation": "1d20+5", "dice": [4], "natural": 4, "total": 9,
                               "source": "engine", "advantage": "normal",
                               "odds": {"percent": 60, "label": "to hit",
                                        "about": "goblin-1", "advantage": "normal"}}]})
    out = sight.redact_log(enc, enc.log, sight.fog(enc))
    assert "Goblin" not in out[0]["text"]
    assert out[0]["rolls"] == [], "the odds rode through the redaction"
    assert "odds" not in out[0] and "percent" not in out[0]


def test_the_odds_of_a_creature_the_players_can_see_are_kept():
    enc = fight(fog="hide", gob=(3, 3))                     # the goblin is in sight
    assert sight.shown(enc, enc.tokens["goblin-1"], sight.fog(enc))
    enc.log.append({"round": enc.round, "actor": "kairos", "kind": "attack",
                    "text": "Kairos Longsword -> Goblin: 14 vs AC 15, hit.",
                    "rolls": [{"who": "Kairos", "label": "Longsword vs Goblin",
                               "notation": "1d20+5", "dice": [9], "natural": 9, "total": 14,
                               "source": "engine", "advantage": "normal",
                               "odds": {"percent": 60, "label": "to hit",
                                        "about": "goblin-1", "advantage": "normal"}}]})
    out = sight.redact_log(enc, enc.log, sight.fog(enc))
    assert out[0]["rolls"][0]["odds"]["percent"] == 60


# ─── the odds, through the real engine rather than a hand-built entry ───────
#
# The two tests above append a log entry by hand, which is right for pinning
# redact_log but leaves the interesting path untested: a roll the ENGINE made,
# for a creature the players genuinely cannot see, arriving in the snapshot the
# display is actually sent. That path is where a chance would leak, and it is
# the one that was open when RI2's display half shipped.
#
# Finding the scenario took some care, which is itself worth recording. A wall
# does not produce it: the engine refuses an attack through a wall ("Kairos has
# no line of sight to Goblin"), so nothing is ever logged to redact. A hidden
# creature does, but ATTACKING reveals it -- the scimitar entry ends "Goblin is
# no longer hidden", so by the time the snapshot is taken the creature is
# visible again and correctly not redacted.
#
# That is the trap for anyone extending this, and it is why the helper below
# stops at "it is the hidden creature's turn" rather than playing the turn out.
# The obvious test -- have the hidden goblin attack -- passes while testing
# nothing, because the attack reveals the goblin and the redaction never
# engages. The window is the turn itself: unseen_turn set, before the creature
# has given itself away.

def _hidden_creature_acting():
    """A goblin with `hidden`, on its own turn, with the PC the only token a
    player can see."""
    k, g = kairos(pos=(0, 0)), goblin("goblin-1", (1, 0))
    enc = start(encounter([k, g], rows=ROWS), ["kairos", "goblin-1"])
    enc.meta["fog"] = "hide"
    g.conditions.append("hidden")
    engine.end_turn(enc, roller.Roller(rng=ScriptedDice()))
    return enc


def _redacted_entry_for_a_real_save():
    """The whole path, for real: the rules fill Roll.odds in, the engine logs
    the roll, and sync.snapshot decides what the display is allowed to see.

    Returns the snapshot entry the display receives. Asserting on the entry (and
    not on redact_log's output) is the point -- redact_log is the unit under
    test elsewhere, and this checks the bytes that actually cross the wire.
    """
    enc = _hidden_creature_acting()
    r = roller.Roller(rng=ScriptedDice(9))
    rules.load("dnd5e").saving_throw(enc.tokens["kairos"], "dex", 15, r, False)
    # The engine DID compute a chance, and a real one. Without this the test
    # would also pass if the odds were simply never computed.
    assert r.log[0].odds == {"percent": 60, "label": "to fail the save",
                             "about": "kairos", "advantage": "normal"}
    enc.log.append({"round": enc.round, "actor": "goblin-1", "kind": "save",
                    "text": "Goblin's burning hands: Kairos DEX save DC 15.",
                    "rolls": [x.to_dict() for x in r.log]})
    return sync.snapshot(enc)["log"][-1]


def test_a_hidden_creature_acting_is_an_unseen_turn_with_nothing_drawn():
    enc = _hidden_creature_acting()
    snap = sync.snapshot(enc)
    assert snap["unseen_turn"] is True
    assert "goblin-1" not in [t["id"] for t in snap["tokens"]]


def test_a_roll_the_engine_made_for_a_hidden_creature_is_redacted_whole():
    """The display must receive neither the faces nor the chance. The chance is
    a function of a DC the entry has just described, so an odds dict surviving
    here would hand that DC over."""
    entry = _redacted_entry_for_a_real_save()
    assert entry["text"].startswith("An unseen creature"), entry["text"]
    assert entry["rolls"] == [], f"the display was sent {entry['rolls']}"
    assert "odds" not in json.dumps(entry)


def test_the_odds_display_shows_nothing_for_a_redacted_entry():
    """The last hop, and the one this file cannot otherwise see: the display's
    own formatter, run over the entry the engine really produced. Empty rolls
    in, no line out, so a redacted entry is silent in the log and the toast
    rather than showing a bare percent.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    entry = _redacted_entry_for_a_real_save()

    js = (pathlib.Path(__file__).resolve().parent.parent
          / "display" / "static" / "tactics.js").read_text(encoding="utf-8")
    pure = re.search(r"/\* Pure helpers:.*?\*/(.*?)/\* end pure helpers \*/", js, re.S)
    assert pure, "tactics.js no longer has its marked pure-helper block"
    # The program goes in over stdin, not argv: the pure-helper block holds
    # non-ASCII source, and an argument is encoded with the filesystem encoding,
    # which is a UnicodeEncodeError under a non-UTF-8 locale.
    program = ("const out=(()=>{" + pure.group(1) +
               "\nreturn {line: entryOdds(" + json.dumps(entry) + ")};})();"
               "process.stdout.write(JSON.stringify(out));")
    res = subprocess.run([node, "-"], input=program.encode("utf-8"),
                         capture_output=True, timeout=30)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    assert json.loads(res.stdout.decode("utf-8"))["line"] == "", \
        "a redacted entry still produced an odds line"
