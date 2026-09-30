"""Fight-time input triage: questions answered by the engine, bad input never kills the REPL.

Evidence: docs/test-reports/TEST-REPORT-pt3-combat-grid (P1, P2, B1, B2). The scenarios
run through the terminal game (scripts/tactics/play.py), so the whole loop is exercised."""
from __future__ import annotations

import random
import re

import pytest

from tests.test_tactics_play import Player, game, play, srd  # noqa: F401  (srd is autouse)
from tactics import cli, engine, fightq

# the four failing pt3 probes plus the ones the design review names
QUESTIONS = [
    ("how far away is the nearest frog", "distance"),
    ("How far is the frog?", "distance"),
    ("can I reach him?", "distance"),
    ("what can I do", "options"),
    ("what can I do?", "options"),
    ("how much movement do I have left", "movement"),
    ("how far can I move?", "movement"),
    ("what's my hp", "hp"),
    ("how hurt am I", "hp"),
    ("who can I attack?", "targets"),
    ("where am I", "position"),
]


@pytest.mark.parametrize("line,topic", QUESTIONS)
def test_classify_finds_the_topic(line, topic):
    q = fightq.classify(line)
    assert q is not None and q.topic == topic


@pytest.mark.parametrize("line", ["move D4", "attack 1", "cast fire bolt 1", "end", "", "   ",
                                  "I swing my sword at the frog", "help", "targets"])
def test_classify_leaves_commands_and_chatter_alone(line):
    assert fightq.classify(line, verbs=("move", "attack", "cast", "end", "help", "targets")) is None


def test_a_command_verb_is_never_a_question():
    assert fightq.classify("status?", verbs=("status",)) is None
    assert fightq.classify("how far is it", verbs=("how",)) is None


def test_classify_extracts_the_subject_and_nearest():
    q = fightq.classify("how far away is the nearest kobold")
    assert q.subject == "kobold" and q.nearest
    assert fightq.classify("how far is kobold 2?").subject == "kobold 2"


def test_classify_is_total_on_junk():
    rng = random.Random(7)
    alphabet = "abc xyz?!0123456789'-’"
    for _ in range(300):
        fightq.classify("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40))))


def test_a_question_is_answered_by_the_engine_and_changes_nothing():
    p = Player(script=["how far away is the nearest kobold", "what can I do", "what's my hp",
                       "how much movement do I have left", "quit"])
    game(["kobolds", "--seed", "2"], p)
    t = p.text
    assert re.search(r"Kobold \d is at K[26], \d+ ft away", t)
    assert "action is ready" in t and "ft of movement left" in t
    assert re.search(r"Kairos: \d+/\d+ HP, AC \d+", t)
    assert "I could not read" not in t and "Unknown command" not in t
    assert "moves" not in t                                  # nothing moved


def test_the_distance_answer_matches_the_engine():
    p = Player(script=["how far is kobold 1", "quit"])
    game(["kobolds", "--seed", "2"], p)
    m = re.search(r"Kobold 1 is at (\w+), (\d+) ft away", p.text)
    assert m
    # B5 -> K2 (kobold 1) or K6: same figure as `preview`'s straight-line grid distance
    from tactics.grid import parse_square
    dx, dy = (abs(a - b) for a, b in zip(parse_square(m.group(1)), parse_square("B5")))
    assert int(m.group(2)) == 5 * max(dx, dy)


def test_a_bad_destination_does_not_kill_the_repl():        # B1
    p = Player(script=["move kairos kobold-1", "move zzz", "move kairos 9", "move !!", "status", "quit"])
    code = game(["kobolds", "--seed", "2"], p)
    assert code == 5                                         # left by `quit`, not a traceback
    assert "Traceback" not in p.text and "ValueError" not in p.text


def test_a_malformed_command_shows_one_friendly_line_not_usage():   # B2
    p = Player(script=["move", "attack", "preview", "area", "cast", "quit"])
    game(["kobolds", "--seed", "2"], p)
    assert "usage:" not in p.text.lower() and "combat.py" not in p.text
    assert p.text.count("needs more") >= 3


def test_cli_prints_one_friendly_line_for_missing_operands(capsys):   # B2 at the source
    code = cli.main(["-c", "nowhere", "move"])
    out = capsys.readouterr()
    assert code == 1 and "usage:" not in (out.out + out.err).lower()
    assert out.out.startswith("That command is not complete") and out.out.count("\n") == 1


def test_cli_help_still_works(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["move", "--help"])
    assert e.value.code == 0 and "usage:" in capsys.readouterr().out.lower()


def test_cast_is_a_player_verb_with_natural_phrasing():     # P2
    p = Player(script=["cast fire bolt at 1", "quit"])
    game(["kobolds", "--seed", "2", "--auto-dice"], p)
    assert re.search(r"Fire Bolt -> Kobold 1", p.text)
    assert "Unknown command" not in p.text


def test_move_toward_a_creature_lets_the_engine_pick_the_square():   # (e) through the REPL
    p = Player(script=["move toward the kobold", "quit"])
    game(["kobolds", "--seed", "2", "--auto-dice"], p)
    assert re.search(r"Kairos moves B5 to \w+", p.text)
    assert "not a square" not in p.text


def _fight(tmp_path, monkeypatch):
    """A live encounter for direct engine calls: the game's own start, no REPL."""
    import os
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    camp, name = play.make_campaign(tmp_path, play.KAIROS, "auto")
    g = play.Game(camp, play.SCENARIOS["kobolds"], name, ask=lambda q: "", out=lambda *_: None)
    assert g.start()
    return g


def test_approach_picks_the_square_and_stays_inside_the_walk(tmp_path, monkeypatch):
    g = _fight(tmp_path, monkeypatch)
    enc = g.enc()
    pc = next(t for t in enc.tokens.values() if t.side == "pc")
    foe = next(t for t in enc.tokens.values() if t.side == "enemy")
    a = engine.approach(enc, pc.id, foe.id)
    walk = engine.reachable(enc, pc.id)["walk"]
    assert a["square"] in walk or a["square"] == pc.square
    assert a["feet"] <= engine.remaining_movement(enc)
    grid = enc.board()
    from tactics.grid import parse_square
    assert grid.distance(parse_square(a["square"]), foe.pos) <= grid.distance(pc.pos, foe.pos)
    # the closest walkable square: no other walkable square is nearer
    best = min(grid.distance(parse_square(sq), foe.pos) for sq in walk)
    assert a["distance"] == best
    with pytest.raises(engine.CombatError):
        engine.approach(enc, pc.id, pc.id)


def test_approach_ends_adjacent_when_it_can_and_moving_to_a_name_uses_it(tmp_path, monkeypatch):
    g = _fight(tmp_path, monkeypatch)
    enc = g.enc()
    pc = next(t for t in enc.tokens.values() if t.side == "pc")
    foe = next(t for t in enc.tokens.values() if t.side == "enemy")
    foe.x, foe.y = pc.x + 3, pc.y                          # three squares off: adjacency is reachable
    a = engine.approach(enc, pc.id, foe.id)
    assert a["in_reach"] and a["distance"] == 5
    plan = engine.preview_move(enc, pc.id, foe.id)            # a creature name as the destination
    assert plan["path"][-1] == a["square"]
    with pytest.raises(engine.CombatError):
        engine.preview_move(enc, pc.id, "not-a-thing")


def test_random_lines_never_escape_the_repl():
    rng = random.Random(3)
    words = ["move", "attack", "cast", "preview", "area", "reactions", "how", "far", "is", "the",
             "kobold", "1", "2", "K2", "ZZ99", "--react", "-x", "?", "toward", "at", "", "kairos",
             "fire", "bolt", "what", "hp", "!!", "é"]
    script = [" ".join(rng.choice(words) for _ in range(rng.randint(1, 5))) for _ in range(60)]
    p = Player(script=script + ["quit"])
    code = game(["kobolds", "--seed", "4", "--auto-dice"], p)
    assert code in (0, 3, 5)
    assert "Traceback" not in p.text and "usage:" not in p.text.lower()
