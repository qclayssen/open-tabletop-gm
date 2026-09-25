"""Milestone 5: fog of war, the sight overlay, and theatre-of-the-mind parity.

sight.py only reads the engine's own line of sight and cover, so these tests
pin two things: the display is shown exactly what the PCs could see (never an
unseen enemy), and everything the map shows is also available as plain text
to a GM running with the display off.
"""
from __future__ import annotations

import json

from tests.tactics_fixtures import encounter, goblin, kairos, start
from tests.test_tactics_cli import begin, camp, run  # noqa: F401  (camp is a fixture)
from tactics import grid, maps, sight, state, sync

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
    assert "E1" not in snap["fog"]["visible"] and "A1" in snap["fog"]["visible"]


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
