"""Engine turn flow and the encounter file, hand-checked.

Board: an open 8x8 map unless a test draws its own. Kairos (AC 12, 8 HP,
speed 30) against SRD giant frogs (AC 11, 18 HP, Bite +3, 1d6+1, reach 5).
Engine dice are scripted; player dice are supplied, as the CLI will do.
"""
from __future__ import annotations

import json

import pytest

from tests.tactics_fixtures import (encounter, engine, frog, kairos, roller, start, state)
from tactics.roller import PendingRoll

CombatError, DecisionNeeded = engine.CombatError, engine.DecisionNeeded


# ─── initiative and turns ─────────────────────────────────────────────────────

def test_initiative_order_and_tie_break():
    enc = encounter([kairos(), frog("frog-1", (5, 5)), frog("frog-2", (6, 6))])
    # Rolled in token order: Kairos 10+2=12, Frog 1 11+1=12, Frog 2 13+1=14.
    # Tie at 12 goes to the higher Dex modifier: Kairos (+2) before Frog 1 (+1).
    res = engine.begin(enc, roller(10, 11, 13))
    assert enc.order == ["frog-2", "kairos", "frog-1"]
    assert enc.round == 1 and enc.current.id == "frog-2"
    assert res["text"].startswith("Initiative: Frog 2 14, Kairos 12, Frog 1 12.")
    assert [r["source"] for r in enc.log[0]["rolls"]] == ["engine"] * 3


def test_end_turn_skips_the_dead_and_wraps_the_round():
    f1, f2 = frog("frog-1", (5, 5)), frog("frog-2", (6, 6))
    enc = start(encounter([kairos(), f1, f2]), ["kairos", "frog-1", "frog-2"])
    f1.dead = True
    assert engine.end_turn(enc, roller())["actor"] == "frog-2"
    res = engine.end_turn(enc, roller())
    assert res["actor"] == "kairos" and res["round"] == 2


def test_acting_out_of_turn_is_refused():
    enc = start(encounter([kairos(), frog("frog-1", (5, 5))]), ["frog-1", "kairos"])
    with pytest.raises(CombatError, match="Frog 1's turn"):
        engine.move(enc, roller(), "kairos", "B2")


def test_all_enemies_down_is_announced():
    f = frog("frog-1", (5, 5))
    enc = start(encounter([kairos(), f]), ["kairos", "frog-1"])
    f.dead = True
    assert "All enemies are down" in engine.end_turn(enc, roller())["text"]


# ─── movement ─────────────────────────────────────────────────────────────────

def test_move_spends_feet_and_reports_what_is_left():
    enc = start(encounter([kairos(), frog("frog-1", (7, 7))]), ["kairos", "frog-1"])
    res = engine.move(enc, roller(), "kairos", "D4")         # 3 diagonals = 15 ft
    assert res["feet"] == 15 and enc.tokens["kairos"].square == "D4"
    assert engine.remaining_movement(enc) == 15
    engine.move(enc, roller(), "kairos", "G7")                 # 3 more = 15 ft
    with pytest.raises(CombatError, match="has 0 ft left"):
        engine.move(enc, roller(), "kairos", "G6")


def test_preview_says_when_dash_would_get_there():
    enc = start(encounter([kairos(), frog("frog-1", (7, 0))]), ["kairos", "frog-1"])
    p = engine.preview_move(enc, "kairos", "H8")                # 7 squares = 35 ft
    assert not p["legal"] and "Reachable with Dash" in p["text"]
    engine.dash(enc, "kairos")
    assert engine.move(enc, roller(), "kairos", "H8")["feet"] == 35


def test_reachable_splits_walk_and_dash_range():
    enc = start(encounter([kairos(), frog("frog-1", (7, 7))]), ["kairos", "frog-1"])
    r = engine.reachable(enc, "kairos")
    assert r["walk"]["F6"] == 25 and r["walk"]["G1"] == 30
    assert "H1" not in r["walk"] and r["dash"]["H1"] == 35


def test_undo_restores_position_until_an_action_locks_it():
    enc = start(encounter([kairos(), frog("frog-1", (7, 7))]), ["kairos", "frog-1"])
    engine.move(enc, roller(), "kairos", "C1")
    engine.undo_move(enc)
    assert enc.tokens["kairos"].square == "A1" and engine.remaining_movement(enc) == 30
    engine.move(enc, roller(), "kairos", "C1")
    engine.dodge(enc, "kairos")
    with pytest.raises(CombatError, match="locked in"):
        engine.undo_move(enc)


# ─── opportunity attacks ─────────────────────────────────────────────────────

def test_leaving_reach_provokes_an_opportunity_attack():
    enc = start(encounter([kairos(), frog("frog-1", (1, 0))]), ["kairos", "frog-1"])
    # Every 3-step path A1 -> D1 leaves the frog's reach on its last step.
    # Bite: d20 15 + 3 = 18 vs AC 12, hit; 1d6+1 with a 4 = 5. Kairos 8 -> 3.
    res = engine.move(enc, roller(15, 4), "kairos", "D1")
    k = enc.tokens["kairos"]
    assert res["opportunity_attacks"] == 1 and k.hp == 3 and k.square == "D1"
    assert enc.tokens["frog-1"].reaction_used
    assert not engine.can_undo(enc)
    assert "Opportunity attack" in enc.log[-1]["text"]


def test_preview_warns_before_provoking():
    enc = start(encounter([kairos(), frog("frog-1", (1, 0))]), ["kairos", "frog-1"])
    p = engine.preview_move(enc, "kairos", "D1")
    # Bite +3 vs AC 12 needs a natural 9: 12/20 = 60%.
    assert p["opportunity_attacks"] == [{"id": "frog-1", "name": "Frog 1",
                                         "attack": "Bite", "hit_percent": 60}]


def test_moving_within_reach_does_not_provoke():
    enc = start(encounter([kairos(), frog("frog-1", (1, 0))]), ["kairos", "frog-1"])
    res = engine.move(enc, roller(), "kairos", "A2")          # still adjacent to B1
    assert res["opportunity_attacks"] == 0


def test_disengage_prevents_opportunity_attacks():
    enc = start(encounter([kairos(), frog("frog-1", (1, 0))]), ["kairos", "frog-1"])
    engine.disengage(enc, "kairos")
    assert engine.move(enc, roller(), "kairos", "D1")["opportunity_attacks"] == 0


def test_an_opportunity_attack_that_drops_you_stops_the_move():
    enc = start(encounter([kairos(hp=2), frog("frog-1", (1, 0))]), ["kairos", "frog-1"])
    res = engine.move(enc, roller(15, 4), "kairos", "D1")     # 5 damage on 2 HP
    k = enc.tokens["kairos"]
    assert res["stopped"] and k.hp == 0 and k.has("unconscious")
    assert k.x == 2                                            # still in reach, where it was hit


def test_a_players_reaction_is_their_choice():
    k, f = kairos(pos=(0, 0)), frog("frog-1", (1, 0))
    enc = start(encounter([k, f]), ["frog-1", "kairos"])
    with pytest.raises(DecisionNeeded) as e:
        engine.move(enc, roller(), "frog-1", "E1")
    assert e.value.who == "kairos" and e.value.kind == "opportunity_attack"
    res = engine.move(enc, roller(), "frog-1", "E1", reactions={"kairos": False})
    assert "lets Frog 1 go" in res["text"] and not k.reaction_used


def test_a_players_opportunity_attack_asks_for_their_roll():
    k, f = kairos(pos=(0, 0)), frog("frog-1", (1, 0))
    enc = start(encounter([k, f]), ["frog-1", "kairos"])
    with pytest.raises(PendingRoll):
        engine.move(enc, roller(), "frog-1", "E1", reactions={"kairos": True})
    # Dagger +4: natural 12 -> 16 vs AC 11, hit; 1d4+2 with a 3 -> 5. Frog 18 -> 13.
    enc = start(encounter([kairos(pos=(0, 0)), frog("frog-1", (1, 0))]), ["frog-1", "kairos"])
    r = roller(supplied=[12, 3], source="player")
    engine.move(enc, r, "frog-1", "E1", reactions={"kairos": True})
    assert enc.tokens["frog-1"].hp == 13
    assert {x["source"] for x in enc.log[-1]["rolls"]} == {"player"}


# ─── attacks ─────────────────────────────────────────────────────────────────

def test_player_attack_waits_for_the_roll_then_logs_its_source():
    enc = start(encounter([kairos(), frog("frog-1", (5, 0))]), ["kairos", "frog-1"])
    with pytest.raises(PendingRoll) as e:
        engine.attack(enc, roller(), "kairos", "frog-1", "fire bolt")
    assert e.value.notation == "1d20+5"
    enc = start(encounter([kairos(), frog("frog-1", (5, 0))]), ["kairos", "frog-1"])
    res = engine.attack(enc, roller(supplied=[14, 7]), "kairos", "frog-1", "fire bolt")
    assert res["hit"] and enc.tokens["frog-1"].hp == 11
    assert [r["source"] for r in enc.log[-1]["rolls"]] == ["verbal", "verbal"]


def test_roll_for_me_lets_the_engine_roll_a_players_dice():
    enc = start(encounter([kairos(), frog("frog-1", (5, 0))]), ["kairos", "frog-1"])
    r = roller(14, 7)
    r.for_me = True
    engine.attack(enc, r, "kairos", "frog-1", "fire bolt")
    assert [x["source"] for x in enc.log[-1]["rolls"]] == ["engine", "engine"]


def test_one_action_per_turn():
    enc = start(encounter([kairos(), frog("frog-1", (5, 0))], roll_mode="auto"),
                ["kairos", "frog-1"])
    engine.attack(enc, roller(2), "kairos", "frog-1", "fire bolt")   # 2 + 5 = 7, miss
    with pytest.raises(CombatError, match="already used their action"):
        engine.attack(enc, roller(15, 5), "kairos", "frog-1", "fire bolt")


def test_no_line_of_sight_through_a_wall():
    rows = ["..#..",
            "..#..",
            "..#.."]
    enc = start(encounter([kairos(pos=(0, 1)), frog("frog-1", (4, 1))], rows=rows,
                          roll_mode="auto"), ["kairos", "frog-1"])
    with pytest.raises(CombatError, match="no line of sight"):
        engine.attack(enc, roller(), "kairos", "frog-1", "fire bolt")


def test_melee_attack_out_of_reach_is_refused():
    enc = start(encounter([kairos(), frog("frog-1", (5, 5))]), ["frog-1", "kairos"])
    with pytest.raises(CombatError, match="reaches 5 ft"):
        engine.attack(enc, roller(), "frog-1", "kairos", "bite")


def test_thrown_dagger_beyond_normal_range_has_disadvantage():
    enc = start(encounter([kairos(), frog("frog-1", (5, 0))], roll_mode="auto"),
                ["kairos", "frog-1"])
    # 25 ft: past the dagger's 20 ft normal range, inside 60. Faces 18 and 4, keep 4.
    res = engine.attack(enc, roller(18, 4), "kairos", "frog-1", "dagger")
    assert res["advantage"] == "disadvantage" and "long range" in res["reasons"]
    assert not res["hit"]


def test_attack_options_rank_targets_with_hit_chance():
    enc = start(encounter([kairos(), frog("frog-1", (5, 0)), frog("frog-2", (7, 7))]),
                ["kairos", "frog-1", "frog-2"])
    opts = engine.attack_options(enc, "kairos")
    bolt = [o for o in opts if o["attack"] == "Fire Bolt"]
    assert {o["target"] for o in bolt} == {"frog-1", "frog-2"}
    assert all(o["hit_percent"] == 75 for o in bolt)
    assert opts[0]["legal"] and opts[0]["attack"] == "Fire Bolt"     # 75% x 5.5 beats the dagger


# ─── dying ───────────────────────────────────────────────────────────────────

def test_death_save_at_the_start_of_a_dying_pcs_turn():
    k = kairos(hp=0)
    k.add_condition("unconscious")
    f = frog("frog-1", (5, 5))
    enc = start(encounter([k, f]), ["frog-1", "kairos"])
    res = engine.end_turn(enc, roller())                     # players mode: waits for the roll
    assert "must roll a death save" in res["text"] and enc.turn.pending == "death_save"
    with pytest.raises(CombatError, match="death save"):
        engine.end_turn(enc, roller())
    out = engine.death_save(enc, roller(supplied=[13]))
    assert "success" in out["text"] and k.death_saves["successes"] == 1
    assert enc.turn.pending == ""


def test_death_save_is_automatic_in_auto_mode():
    k = kairos(hp=0)
    k.add_condition("unconscious")
    enc = start(encounter([k, frog("frog-1", (5, 5))], roll_mode="auto"), ["frog-1", "kairos"])
    res = engine.end_turn(enc, roller(4))
    assert "failure" in res["text"] and k.death_saves["failures"] == 1


# ─── the encounter file ──────────────────────────────────────────────────────

def test_save_and_load_round_trip(tmp_path):
    enc = start(encounter([kairos(), frog("frog-1", (1, 0))]), ["kairos", "frog-1"])
    engine.move(enc, roller(15, 4), "kairos", "D1")
    path = state.encounter_path(tmp_path)
    state.save(enc, path)
    back = state.load(path)
    assert back.to_dict() == enc.to_dict()
    assert back.tokens["kairos"].hp == 3 and back.current.id == "kairos"


def test_save_keeps_a_backup_and_leaves_no_temp_file(tmp_path):
    enc = start(encounter([kairos(), frog("frog-1", (7, 7))]), ["kairos", "frog-1"])
    path = state.encounter_path(tmp_path)
    state.save(enc, path)
    engine.move(enc, roller(), "kairos", "B1")
    state.save(enc, path)
    bak = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
    assert bak["tokens"]["kairos"]["x"] == 0                 # the previous version
    assert state.load(path).tokens["kairos"].x == 1
    assert not path.with_suffix(".json.tmp").exists()


def test_invalid_state_is_refused_on_save_and_load(tmp_path):
    enc = encounter([kairos(), frog("frog-1", (0, 0))])        # two tokens on A1
    with pytest.raises(ValueError, match="share A1"):
        state.save(enc, tmp_path / "e.json")
    good = encounter([kairos(), frog("frog-1", (3, 3))])
    state.save(good, tmp_path / "e.json")
    data = json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))
    data["tokens"]["kairos"]["hp"] = 99
    (tmp_path / "e.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="hp 99"):
        state.load(tmp_path / "e.json")
