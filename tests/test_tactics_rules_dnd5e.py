"""D&D 5e (2014) rules in systems/dnd5e/tactics_rules.py, hand-checked.

Dice are scripted (tests/tactics_fixtures.py), so each expected number is the
arithmetic in the comment beside it.
"""
from __future__ import annotations

import pytest

from tests.tactics_fixtures import RULES, frog, goblin, kairos, roller
from tactics.roller import PendingRoll
from tactics.rules import AttackContext

MELEE = AttackContext(distance=5, melee=True)
RANGED = AttackContext(distance=30, melee=False)


def scimitar(g):
    return next(a for a in g.attacks if a["name"] == "Scimitar")


# ─── the monster adapter ──────────────────────────────────────────────────────

def test_giant_frog_token_from_srd():
    f = frog()
    assert (f.hp, f.max_hp, f.ac, f.speed, f.swim_speed) == (18, 18, 11, 30, 30)
    bite = f.attacks[0]
    assert bite["name"] == "Bite" and bite["bonus"] == 3 and bite["reach"] == 5
    assert bite["damage"] == [{"dice": "1d6+1", "type": "piercing"}]
    assert "grappled" in bite["rider"]
    assert f.saves["dex"] == 1                       # DEX 13


# ─── attacks ─────────────────────────────────────────────────────────────────

def test_hit_on_exactly_ac_and_damage():
    # Goblin scimitar +4 vs Kairos AC 12: d20 8 -> 12, hits. 1d6+2 with a 3 -> 5.
    k = kairos()
    res = RULES.attack(goblin(), k, scimitar(goblin()), MELEE, roller(8, 3), player=False)
    assert res["hit"] and not res["crit"] and res["total"] == 12
    assert res["damage"]["total"] == 5 and k.hp == 3


def test_natural_20_crits_and_doubles_dice_not_modifier():
    # Nat 20 -> crit. 2d6+2 with 3 and 4 -> 9.
    k = kairos(hp=8)
    res = RULES.attack(goblin(), k, scimitar(goblin()), MELEE, roller(20, 3, 4), player=False)
    assert res["crit"] and res["damage"]["total"] == 9


def test_natural_1_always_misses():
    big = dict(scimitar(goblin()), bonus=30)
    res = RULES.attack(goblin(), kairos(), big, MELEE, roller(1), player=False)
    assert not res["hit"] and res["damage"] is None


def test_melee_hit_on_unconscious_target_is_a_crit():
    k = kairos(hp=0)
    k.add_condition("unconscious")
    # Advantage: faces 5 and 11, keep 11 -> 15, hits; auto-crit within 5 ft.
    res = RULES.attack(goblin(), k, scimitar(goblin()), MELEE, roller(5, 11, 2, 2), player=False)
    assert res["advantage"] == "advantage" and res["crit"]


def test_advantage_and_disadvantage_cancel():
    k, g = kairos(), goblin()
    k.add_condition("prone")                 # adjacent prone target: advantage
    g.add_condition("poisoned")              # poisoned attacker: disadvantage
    mode, reasons = RULES.advantage(g, k, MELEE)
    assert mode == "normal" and len(reasons) == 2


def test_prone_target_is_harder_to_hit_from_range():
    k = kairos()
    k.add_condition("prone")
    assert RULES.advantage(goblin(), k, RANGED)[0] == "disadvantage"


def test_ranged_attack_with_an_enemy_adjacent_has_disadvantage():
    ctx = AttackContext(distance=30, melee=False, hostile_adjacent=True)
    assert RULES.advantage(kairos(), frog(), ctx)[0] == "disadvantage"


def test_cover_adds_to_ac():
    # Fire Bolt +5 vs frog AC 11 + half cover 2 = 13. d20 7 -> 12, miss.
    ctx = AttackContext(distance=30, melee=False, cover=2)
    res = RULES.attack(kairos(), frog(), kairos().attacks[0], ctx, roller(7), player=False)
    assert res["ac"] == 13 and not res["hit"]


def test_hit_chance_matches_the_dice():
    k, f = kairos(), frog()
    bolt = k.attacks[0]
    # +5 vs AC 11: need a natural 6 -> 15/20 = 75%.
    assert RULES.hit_chance(k, f, bolt, RANGED)["percent"] == 75
    # With advantage: 1 - 0.25^2 = 93.75% -> 94.
    f.add_condition("restrained")
    assert RULES.hit_chance(k, f, bolt, RANGED)["percent"] == 94
    # Only a nat 20 hits: 5%, with advantage 1 - 0.95^2 = 9.75% -> 10.
    assert RULES.hit_chance(k, f, dict(bolt, bonus=-50), RANGED)["percent"] == 10


# ─── rolls under roll_mode "players" ──────────────────────────────────────────

def test_player_roll_is_requested_not_invented():
    with pytest.raises(PendingRoll) as e:
        RULES.attack(kairos(), frog(), kairos().attacks[0], RANGED, roller(), player=True)
    assert e.value.notation == "1d20+5"


def test_supplied_player_rolls_are_used_and_tagged():
    # Natural 15 + 5 = 20 hits; Fire Bolt 1d10 supplied as 6.
    r = roller(supplied=[15, 6], source="verbal")
    f = frog()
    res = RULES.attack(kairos(), f, kairos().attacks[0], RANGED, r, player=True)
    assert res["total"] == 20 and res["damage"]["total"] == 6 and f.hp == 12
    assert [x.source for x in r.log] == ["verbal", "verbal"]


# ─── damage ──────────────────────────────────────────────────────────────────

def test_resistance_vulnerability_immunity_and_temp_hp():
    f = frog()
    f.resistances, f.vulnerabilities, f.immunities = ["fire"], ["cold"], ["poison"]
    f.temp_hp = 2
    res = RULES.damage(f, [{"amount": 7, "type": "fire"},      # halved, rounded down: 3
                           {"amount": 4, "type": "cold"},      # doubled: 8
                           {"amount": 9, "type": "poison"}])   # immune: 0
    # 11 total, 2 to temp HP, 9 off 18.
    assert res["total"] == 11 and res["absorbed"] == 2 and f.hp == 9


def test_conditional_resistance_is_not_applied_blindly():
    f = frog()
    f.resistances = ["bludgeoning, piercing, and slashing from nonmagical attacks"]
    res = RULES.damage(f, [{"amount": 6, "type": "piercing"}])
    assert res["total"] == 6 and "GM check" in res["text"]


def test_monster_dies_at_zero():
    f = frog()
    res = RULES.damage(f, [{"amount": 30, "type": "fire"}])
    assert res["dead"] and f.dead and f.hp == 0


# ─── dropping to 0 and death saves (PHB p197) ─────────────────────────────────

def test_pc_drops_unconscious_and_prone():
    k = kairos(hp=3)
    res = RULES.damage(k, [{"amount": 10, "type": "piercing"}])   # overflow 7 < max 8
    assert res["dropped"] and not res["dead"]
    assert k.hp == 0 and k.has("unconscious") and k.has("prone")
    assert not RULES.can_act(k)


def test_massive_damage_kills_outright():
    k = kairos(hp=3)
    res = RULES.damage(k, [{"amount": 11, "type": "piercing"}])   # overflow 8 >= max 8
    assert res["instant_death"] and k.dead


def test_damage_at_zero_is_a_failure_and_a_crit_is_two():
    k = kairos(hp=0)
    RULES.damage(k, [{"amount": 3, "type": "piercing"}])
    assert k.death_saves["failures"] == 1
    RULES.damage(k, [{"amount": 3, "type": "piercing"}], crit=True)
    assert k.death_saves["failures"] == 3 and k.dead


def test_death_save_outcomes():
    k = kairos(hp=0)
    k.add_condition("unconscious")
    assert "success" in RULES.death_save(k, roller(10), player=False)["text"]
    assert "failure" in RULES.death_save(k, roller(9), player=False)["text"]
    RULES.death_save(k, roller(1), player=False)                 # nat 1: two failures
    assert k.death_saves["failures"] == 3 and k.dead


def test_three_successes_stabilise():
    k = kairos(hp=0)
    for face in (12, 15, 19):
        res = RULES.death_save(k, roller(face), player=False)
    assert res["stable"] and k.stable and k.hp == 0


def test_natural_20_death_save_revives_with_1_hp():
    k = kairos(hp=0)
    k.add_condition("unconscious")
    k.death_saves["failures"] = 2
    res = RULES.death_save(k, roller(20), player=False)
    assert res["revived"] and k.hp == 1 and not k.has("unconscious")
    assert k.death_saves == {"successes": 0, "failures": 0}


def test_healing_at_zero_wakes_and_resets_saves():
    k = kairos(hp=0)
    k.add_condition("unconscious")
    k.death_saves["failures"] = 2
    RULES.heal(k, 4)
    assert k.hp == 4 and not k.has("unconscious") and k.death_saves["failures"] == 0


# ─── saves and conditions ─────────────────────────────────────────────────────

def test_paralyzed_fails_dex_saves_automatically():
    k = kairos()
    k.add_condition("paralyzed")
    assert RULES.saving_throw(k, "dex", 10, roller(), player=True)["auto_fail"]


def test_save_uses_the_sheet_bonus():
    # INT save +5, d20 8 -> 13 vs DC 13: success.
    assert RULES.saving_throw(kairos(), "int", 13, roller(8), player=False)["success"]


def test_grappled_means_speed_zero():
    k = kairos()
    k.add_condition("grappled")
    assert RULES.speed(k) == 0


def test_dodge_is_lost_at_speed_zero():
    # PHB: the Dodge benefit ends if you are incapacitated or your speed drops to 0.
    k = kairos()
    k.dodging = True
    assert RULES.advantage(goblin(), k, MELEE)[0] == "disadvantage"
    k.add_condition("grappled")
    assert RULES.advantage(goblin(), k, MELEE)[0] == "normal"


def test_flat_damage_is_never_asked_for():
    r = roller()                                   # nothing supplied, no engine dice
    rec = r.roll("1", "Octopus", "Tentacles damage", player=True)
    assert rec.total == 1 and rec.source == "fixed" and rec.dice == []
