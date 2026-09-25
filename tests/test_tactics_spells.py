"""Milestone 4: spells, templates, saves, concentration, reactions, riders,
Help, Hide, Escape and Ready. Hand-checked with scripted dice.

Kairos (tests.tactics_fixtures.caster): AC 12, 8 HP, spell save DC 13, spell
attack +5, CON save +2, two level 1 slots, controlled by the player, so his
dice are supplied (roller(supplied=[...])). Engine dice are the scripted faces.
Giant frog: AC 11, 18 HP, DEX save +1, INT save -4, Bite +3 (1d6+1, grapples).
"""
from __future__ import annotations

import pytest

from tests.tactics_fixtures import (RULES, caster, encounter, engine, frog, monster, roller,
                                    start, KAIROS_SPELLS)
from tactics import actions, ai, effects as fx, spells
from tactics.roller import PendingRoll

CombatError, DecisionNeeded = engine.CombatError, engine.DecisionNeeded
NO_REACT = {"kairos:silvery barbs": False, "kairos:shield": False}


def fight(*tokens, order=None):
    enc = encounter(list(tokens))
    return start(enc, order or [t.id for t in tokens])


# ─── spell attacks and saves ──────────────────────────────────────────────────

def test_fire_bolt_is_a_spell_attack_with_the_players_dice():
    k, f = caster(), frog("frog-1", (4, 0))
    enc = fight(k, f)
    # d20 15 + 5 = 20 vs AC 11: hit; 1d10 = 7 fire. Frog 18 -> 11.
    res = spells.cast(enc, roller(supplied=[15, 7]), "kairos", "fire bolt", ["frog-1"])
    assert f.hp == 11 and enc.turn.action_used
    assert res["text"].startswith("Kairos casts Fire Bolt. Kairos Fire Bolt -> Frog 1: 20 vs AC 11, hit.")


def test_a_cantrip_needs_the_players_roll_first():
    enc = fight(caster(), frog("frog-1", (4, 0)))
    with pytest.raises(PendingRoll):
        spells.cast(enc, roller(), "kairos", "fire bolt", ["frog-1"])


def test_mind_sliver_damage_then_the_penalty_on_the_next_save():
    k, f = caster(), frog("frog-1", (4, 0))
    enc = fight(k, f)
    # Damage is rolled first (player: 4), then the frog's INT save: 8 - 4 = 4, fails.
    res = spells.cast(enc, roller(8, supplied=[4]), "kairos", "mind sliver", ["frog-1"], reactions=NO_REACT)
    assert f.hp == 14 and "subtracts 1d4 from their next save" in res["text"]
    # Next save of any kind: DEX 12 + 1 = 13, minus the 1d4 (3) = 10: still a DC 10 success.
    out = RULES.saving_throw(f, "dex", 10, roller(12, 3), False)
    assert out["total"] == 10 and out["success"] and "-3 mind sliver" in out["text"]
    assert not f.effects                                  # spent by that save


def test_mind_sliver_penalty_ends_at_the_end_of_the_casters_next_turn():
    k, f = caster(), frog("frog-1", (4, 0))
    enc = fight(k, f)
    spells.cast(enc, roller(8, supplied=[4]), "kairos", "mind sliver", ["frog-1"], reactions=NO_REACT)
    engine.end_turn(enc, roller())                        # Kairos's own turn ends: still there
    assert f.effects
    engine.end_turn(enc, roller())                        # frog ends; Kairos's next turn starts
    assert f.effects and f.effects[0]["armed"]
    engine.end_turn(enc, roller())                        # the end of Kairos's next turn
    assert not f.effects


def test_silvery_barbs_is_offered_when_an_enemy_saves():
    enc = fight(caster(), frog("frog-1", (4, 0)))
    # INT save 20 - 4 = 16 >= 13: success, and Kairos can answer with Silvery Barbs.
    with pytest.raises(DecisionNeeded) as e:
        spells.cast(enc, roller(20, supplied=[4]), "kairos", "mind sliver", ["frog-1"])
    assert e.value.key == "kairos:silvery barbs"
    # Yes: reroll 2 -> keep 2, 2 - 4 = -2: now a failure; one slot spent.
    k, f = caster(), frog("frog-1", (4, 0))
    enc = fight(k, f)
    res = spells.cast(enc, roller(20, 2, supplied=[4]), "kairos", "mind sliver", ["frog-1"],
                      reactions={"kairos:silvery barbs": True})
    assert "Now a failure." in res["text"] and f.hp == 14
    assert k.extra["slots"]["1"]["used"] == 1 and k.reaction_used
    assert fx.find(k, name="silvery barbs")               # the advantage goes to the caster


def test_magic_missile_darts_split_and_spend_a_slot():
    k, f1, f2 = caster(), frog("frog-1", (4, 0)), frog("frog-2", (4, 2))
    enc = fight(k, f1, f2)
    with pytest.raises(CombatError, match="3 darts"):
        spells.cast(enc, roller(), "kairos", "magic missile", ["frog-1", "frog-2"])
    assert k.extra["slots"]["1"]["used"] == 0             # a refused cast costs nothing
    # One roll for every dart: 1d4 = 3, +1 = 4 force each. Frog 1 takes 8, Frog 2 takes 4.
    res = spells.cast(enc, roller(supplied=[3]), "kairos", "magic missile",
                      ["frog-1", "frog-2", "frog-1"])
    assert (f1.hp, f2.hp) == (10, 14) and k.extra["slots"]["1"]["used"] == 1
    assert "2 darts hit Frog 1 for 4 force each" in res["text"]


def test_upcasting_adds_darts_and_needs_that_slot():
    k = caster()
    k.extra["slots"]["2"] = {"total": 1, "used": 0}
    f = frog("frog-1", (4, 0))
    enc = fight(k, f)
    spells.cast(enc, roller(supplied=[2]), "kairos", "magic missile", ["frog-1"], level=2)
    assert f.hp == 18 - 4 * 3 and k.extra["slots"]["2"]["used"] == 1


def test_burning_hands_one_roll_a_save_each_half_on_success():
    k = caster(spells=KAIROS_SPELLS + ["Burning Hands"])
    f1, f2, f3 = frog("frog-1", (1, 0)), frog("frog-2", (3, 1)), frog("frog-3", (2, 1))
    enc = fight(k, f1, f2, f3)
    # 15 ft cone east from A1: B1, C1, D1, D2 (the 1-1-3 shape). Frog 3 on C2 is outside.
    # Damage 3d6 = 10 (player). Frog 1 DEX 5 + 1 = 6 fails: 10. Frog 2 15 + 1 = 16 saves: 5.
    res = spells.cast(enc, roller(5, 15, supplied=[10]), "kairos", "burning hands", ["D1"],
                      reactions=NO_REACT)
    assert (f1.hp, f2.hp, f3.hp) == (8, 13, 18)
    assert "(15 ft cone: Frog 1, Frog 2)" in res["text"]
    assert sorted(res["squares"]) == ["B1", "C1", "D1", "D2"]


def test_the_area_preview_shows_fail_chance_expected_damage_and_allies():
    k = caster(spells=KAIROS_SPELLS + ["Burning Hands"])
    ally = caster(pos=(2, 0))
    ally.id, ally.name, ally.controller = "mira", "Mira", "gm"
    enc = fight(k, frog("frog-1", (1, 0)), ally)
    pv = spells.preview(enc, "kairos", "burning hands", "D1")
    frog_row = next(r for r in pv["affected"] if r["id"] == "frog-1")
    # DEX +1 vs DC 13: fails on 1-11, 55%. 10.5 x (0.55 + 0.45 / 2) = 8.1.
    assert frog_row["fail_percent"] == 55 and frog_row["expected"] == 8.1
    mira = next(r for r in pv["affected"] if r["id"] == "mira")
    assert mira["ally"] and "(ALLY)" in pv["text"]


def test_range_and_sight_are_checked_before_anything_is_spent():
    k = caster(spells=KAIROS_SPELLS + ["Burning Hands"])
    rows = ["..#....."] * 3
    enc = start(encounter([k, frog("frog-1", (6, 1))], rows=rows), ["kairos", "frog-1"])
    with pytest.raises(CombatError, match="reaches 120 ft|line of sight"):
        spells.cast(enc, roller(), "kairos", "fire bolt", ["frog-1"])
    with pytest.raises(CombatError, match="neither a creature nor a square"):
        spells.cast(enc, roller(), "kairos", "burning hands", ["nowhere"])
    assert not enc.turn.action_used


def test_the_bonus_action_spell_rule():
    k = caster(spells=KAIROS_SPELLS + ["Healing Word"])
    enc = fight(k, frog("frog-1", (4, 0)))
    k.hp = 3
    spells.cast(enc, roller(supplied=[15, 7]), "kairos", "fire bolt", ["frog-1"])    # action cantrip
    # Healing Word: 1d4 + MOD (DC 13 - 8 - proficiency 2 = +3): 2 + 3 = 5 HP.
    spells.cast(enc, roller(supplied=[2]), "kairos", "healing word", ["kairos"])
    assert k.hp == 8
    enc2 = fight(caster(spells=KAIROS_SPELLS + ["Healing Word"]), frog("frog-1", (4, 0)))
    spells.cast(enc2, roller(supplied=[2]), "kairos", "healing word", ["kairos"])
    with pytest.raises(CombatError, match="only a 1-action cantrip"):
        spells.cast(enc2, roller(supplied=[1]), "kairos", "magic missile", ["frog-1"])


def test_mage_armor_sets_ac_and_narrated_spells_still_cost():
    k = caster(spells=KAIROS_SPELLS + ["Bless"])
    enc = fight(k, frog("frog-1", (4, 0)))
    spells.cast(enc, roller(), "kairos", "mage armor")
    assert k.ac == 15 and k.extra["slots"]["1"]["used"] == 1
    enc.turn.action_used = False
    res = spells.cast(enc, roller(), "kairos", "bless", ["kairos"])    # no engine effect: narrated
    assert "GM decides the effect" in res["text"] and k.concentration == "Bless"


def test_reactions_are_not_cast_directly():
    enc = fight(caster(), frog("frog-1", (4, 0)))
    with pytest.raises(CombatError, match="reaction"):
        spells.cast(enc, roller(), "kairos", "shield")


# ─── concentration ────────────────────────────────────────────────────────────

def test_entangle_restrains_until_concentration_ends():
    k = caster(spells=KAIROS_SPELLS + ["Entangle"])
    f1, f2 = frog("frog-1", (4, 4)), frog("frog-2", (5, 5))
    enc = fight(k, f1, f2)
    # 20 ft cube around F6 catches both. STR saves: 3 + 1 = 4 and 5 + 1 = 6, both fail.
    spells.cast(enc, roller(3, 5), "kairos", "entangle", ["F6"], reactions=NO_REACT)
    assert f1.has("restrained") and f2.has("restrained") and k.concentration == "Entangle"
    fx.end_concentration(enc, k)
    assert not f1.has("restrained") and not f2.has("restrained")


def test_damage_forces_a_concentration_save():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    k.concentration, k.reactions = "Bless", "off"
    enc = fight(f, k)
    # Bite 15 + 3 = 18 hits; 1d6+1 with a 4 = 5: DC 10 CON. Kairos rolls 3 + 2 = 5: lost.
    res = engine.attack(enc, roller(15, 4, supplied=[3]), "frog-1", "kairos")
    assert k.concentration is None and "loses concentration on Bless" in res["text"]


def test_dropping_to_zero_ends_concentration():
    k, f = caster(pos=(0, 0), hp=2), frog("frog-1", (1, 0))
    k.concentration, k.reactions = "Bless", "off"
    enc = fight(f, k)
    engine.attack(enc, roller(15, 4), "frog-1", "kairos")
    assert k.hp == 0 and k.concentration is None


# ─── Shield and Silvery Barbs against attacks ─────────────────────────────────

def test_shield_turns_a_hit_into_a_miss_and_lasts_until_his_turn():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    enc = fight(f, k)
    # Bite 12 + 3 = 15 vs AC 12 hits. Silvery Barbs is asked first, then Shield.
    with pytest.raises(DecisionNeeded) as e:
        engine.attack(enc, roller(12), "frog-1", "kairos")
    assert e.value.key == "kairos:silvery barbs"
    res = engine.attack(enc, roller(12), "frog-1", "kairos", None,
                        {"kairos:silvery barbs": False, "kairos:shield": True})
    assert not res["hit"] and res["ac"] == 17 and k.hp == 8
    assert RULES.ac(k) == 17 and k.extra["slots"]["1"]["used"] == 1
    engine.end_turn(enc, roller())                         # Kairos's turn starts: Shield fades
    assert RULES.ac(k) == 12


def test_shield_is_not_offered_when_it_would_not_help():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    enc = fight(f, k)
    # 15 + 3 = 18 >= 17: Shield cannot make it miss, so only Silvery Barbs is asked (declined).
    res = engine.attack(enc, roller(15, 4), "frog-1", "kairos", None, {"kairos:silvery barbs": False})
    assert res["hit"] and k.extra["slots"]["1"]["used"] == 0


def test_silvery_barbs_rerolls_an_enemy_hit():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    enc = fight(f, k)
    # 12 + 3 = 15 hits; Barbs reroll 3: keep 3, 6 vs AC 12, miss.
    res = engine.attack(enc, roller(12, 3), "frog-1", "kairos", None, {"kairos:silvery barbs": True})
    assert not res["hit"] and "keeps 3" in res["text"] and k.hp == 8
    assert fx.find(k, name="silvery barbs")                # Kairos was the target: he gets it


def test_reactions_off_never_asks():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    k.reactions = "off"
    enc = fight(f, k)
    res = engine.attack(enc, roller(12, 3), "frog-1", "kairos")
    assert res["hit"] and k.extra["slots"]["1"]["used"] == 0


# ─── riders ───────────────────────────────────────────────────────────────────

def test_wolf_bite_knocks_prone_on_a_failed_save():
    k, w = caster(pos=(0, 0)), monster("wolf", "wolf-1", (1, 0))
    k.reactions = "off"
    enc = fight(w, k)
    # Bite 14 + 4 = 18 hits; 2d4+2 with 3 and 2 = 7; STR save (player) 5 - 1 = 4 < 11: prone.
    res = engine.attack(enc, roller(14, 3, 2, supplied=[5]), "wolf-1", "kairos")
    assert k.has("prone") and "Kairos is knocked prone." in res["text"]


def test_spider_poison_is_halved_on_a_success():
    k, s = caster(pos=(0, 0), hp=8), monster("giant-spider", "spider-1", (1, 0))
    k.max_hp, k.hp, k.reactions = 30, 30, "off"
    enc = fight(s, k)
    # Bite 15 + 5 = 20 hits; 1d8+3 with 4 = 7 piercing. CON save 12 + 2 = 14 >= 11:
    # poison 2d8 (5, 5) = 10, halved to 5. 30 - 7 - 5 = 18.
    engine.attack(enc, roller(15, 4, 5, 5, supplied=[12]), "spider-1", "kairos")
    assert k.hp == 18


def test_a_grapple_ends_when_the_grappler_drops_or_moves_away():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    k.reactions = "off"
    enc = fight(f, k)
    engine.attack(enc, roller(15, 4), "frog-1", "kairos")
    assert k.has("grappled") and k.has("restrained")
    engine.move(enc, roller(), "frog-1", "D1", {"kairos": False})    # no opportunity attack
    assert not k.has("grappled") and not k.has("restrained")


def test_escape_uses_the_better_of_athletics_and_acrobatics():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    k.reactions = "off"
    enc = fight(f, k)
    engine.attack(enc, roller(15, 4), "frog-1", "kairos")
    engine.end_turn(enc, roller())
    # Acrobatics +2 beats Athletics -1: 9 + 2 = 11 vs DC 11, free.
    res = actions.escape(enc, roller(supplied=[9]), "kairos")
    assert res["escaped"] and not k.has("grappled") and "Acrobatics 11 vs DC 11" in res["text"]


def test_breaking_free_mid_turn_gives_the_speed_back():
    # Regression: the turn's movement was fixed when the turn started, while
    # Kairos was grappled (speed 0), so escaping left him 0 ft for the turn.
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    k.reactions = "off"
    enc = fight(f, k)
    engine.attack(enc, roller(15, 4), "frog-1", "kairos")
    engine.end_turn(enc, roller())
    assert engine.remaining_movement(enc) == 0
    actions.escape(enc, roller(supplied=[9]), "kairos")
    assert engine.remaining_movement(enc) == 30
    engine.move(enc, roller(), "kairos", "A2")               # still in the frog's reach
    assert engine.remaining_movement(enc) == 25


# ─── Help, Hide, Ready ────────────────────────────────────────────────────────

def test_help_gives_the_ally_advantage_on_the_next_attack():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    mira = caster(pos=(2, 1))
    mira.id, mira.name, mira.controller = "mira", "Mira", "gm"
    enc = fight(k, mira, f)
    actions.help_action(enc, "kairos", "mira", "frog-1")
    engine.end_turn(enc, roller())
    # Mira's dagger with advantage: faces 3 and 17, keep 17 + 4 = 21 hits; 1d4+2 = 2 + 2.
    res = engine.attack(enc, roller(3, 17, 2), "mira", "frog-1", "dagger")
    assert res["advantage"] == "advantage" and res["hit"] and not mira.effects


def test_help_needs_the_target_within_5_ft():
    k, f = caster(pos=(0, 0)), frog("frog-1", (5, 5))
    mira = caster(pos=(4, 4))
    mira.id, mira.name = "mira", "Mira"
    enc = fight(k, mira, f)
    with pytest.raises(CombatError, match="within 5 ft"):
        actions.help_action(enc, "kairos", "mira", "frog-1")


def test_hide_needs_total_cover_then_beats_passive_perception():
    rows = ["...#....", "...#....", "...#....", "........"]
    k, f = caster(pos=(0, 0)), frog("frog-1", (6, 0))
    enc = start(encounter([k, f], rows=rows), ["kairos", "frog-1"])
    # Behind the wall: Stealth 8 + 4 = 12 ties the frog's passive Perception 12: hidden.
    res = actions.hide(enc, roller(supplied=[8]), "kairos")
    assert res["hidden"] and k.has("hidden")
    # In the open, hiding is refused and nothing is spent.
    k2, f2 = caster(pos=(0, 3)), frog("frog-1", (6, 3))
    enc2 = start(encounter([k2, f2], rows=rows), ["kairos", "frog-1"])
    with pytest.raises(CombatError, match="plain sight"):
        actions.hide(enc2, roller(), "kairos")
    assert not enc2.turn.action_used


def test_attacking_from_hiding_has_advantage_then_reveals():
    k, f = caster(pos=(0, 0)), frog("frog-1", (4, 0))
    k.add_condition("hidden")
    enc = fight(k, f)
    res = engine.attack(enc, roller(supplied=[15, 6]), "kairos", "frog-1", "fire bolt")
    assert res["advantage"] == "advantage" and not k.has("hidden")


def test_enemies_cannot_target_what_they_cannot_find():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    k.add_condition("hidden")
    enc = fight(f, k)
    assert all(o["kind"] != "attack" for o in ai.options(enc, "frog-1"))


def test_ready_a_spell_and_trigger_it_on_the_enemys_turn():
    k, f = caster(pos=(0, 0)), frog("frog-1", (6, 0))
    enc = fight(k, f)
    res = actions.ready(enc, roller(), "kairos", "cast", "fire bolt", "frog-1", "a frog surfaces")
    assert k.concentration == "Fire Bolt (readied)" and "trigger kairos" in res["text"]
    engine.end_turn(enc, roller())
    out = actions.trigger(enc, roller(supplied=[15, 7]), "kairos")
    assert f.hp == 11 and k.reaction_used and k.concentration is None and "readied" not in k.extra


def test_a_readied_action_is_lost_at_the_start_of_the_next_turn():
    k, f = caster(pos=(0, 0)), frog("frog-1", (6, 0))
    enc = fight(k, f)
    actions.ready(enc, roller(), "kairos", "attack", "dagger", "frog-1", "it comes close")
    engine.end_turn(enc, roller())
    res = engine.end_turn(enc, roller())
    assert "readied" not in k.extra and "is lost" in res["text"]


# ─── breath weapons ───────────────────────────────────────────────────────────

def test_breath_weapon_option_is_offered_used_and_recharges():
    k, m = caster(pos=(0, 0)), monster("ice-mephit", "mephit-1", (3, 0))
    k.reactions = "off"
    enc = fight(m, k)
    opts = ai.options(enc, "mephit-1")
    breath = next(o for o in opts if o["kind"] == "area")
    assert "Frost Breath: 15 ft cone" in breath["label"] and "recharge 6" in breath["label"]
    assert "Kairos 35% to fail" in breath["label"]         # DEX +2 vs DC 10: fails on 1-7
    # Aimed at A1 from D1: 2d4 = 3 + 2 = 5 cold; DEX save (player) 4 + 2 = 6 fails.
    res = spells.use_action(enc, roller(3, 2, supplied=[4]), "mephit-1", "frost breath", "A1")
    assert k.hp == 3 and "Frost Breath (15 ft cone: Kairos)" in res["text"]
    assert m.extra["usage"]["Frost Breath"]["charged"] is False
    assert not any(o["kind"] == "area" for o in ai.options(enc, "mephit-1"))
    engine.end_turn(enc, roller())                          # Kairos
    k.death_saves = {"successes": 0, "failures": 0}
    res = engine.end_turn(enc, roller(6))                   # mephit's turn: recharge on a 6
    assert m.extra["usage"]["Frost Breath"]["charged"] and "Frost Breath recharges" in res["text"]


def test_blinding_breath_blinds_until_a_repeat_save():
    k, m = caster(pos=(0, 0)), monster("dust-mephit", "mephit-1", (2, 0))
    k.reactions = "off"
    enc = fight(m, k)
    spells.use_action(enc, roller(supplied=[3]), "mephit-1", "blinding breath", "A1")
    assert k.has("blinded")
    engine.end_turn(enc, roller())                          # Kairos's turn begins
    # End of Kairos's turn: DEX save again, 12 + 2 = 14 >= 10, the blindness ends.
    engine.end_turn(enc, roller(1, supplied=[12]))            # (1: the breath does not recharge)
    assert not k.has("blinded")


def test_a_grappling_frog_keeps_biting_its_target_and_stays_put():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    mira = caster(pos=(2, 1))
    mira.id, mira.name, mira.controller, mira.reactions = "mira", "Mira", "gm", "off"
    k.reactions = "off"
    enc = fight(f, k, mira)
    engine.attack(enc, roller(15, 4), "frog-1", "kairos")
    engine.end_turn(enc, roller()), engine.end_turn(enc, roller(10)), engine.end_turn(enc, roller())
    opts = ai.options(enc, "frog-1")
    bites = [o for o in opts if o["kind"] == "attack"]
    assert bites and all(o["target"] == "kairos" for o in bites)
    assert "keeps grapple" in bites[0]["label"]
    assert not any(o["kind"] in ("retreat", "dash") for o in opts)


# ─── review fixes (each pinned by a regression test) ──────────────────────────

def test_a_paralyzed_grappler_lets_go():
    k, f = caster(pos=(0, 0), spells=KAIROS_SPELLS + ["Hold Person"]), frog("frog-1", (1, 0))
    k.extra["slots"]["2"] = {"total": 1, "used": 0}
    k.reactions = "off"
    enc = fight(f, k)
    engine.attack(enc, roller(15, 4), "frog-1", "kairos")
    engine.end_turn(enc, roller())
    k.remove_condition("restrained")                     # a test shortcut: let him cast
    k.remove_condition("grappled")
    k.effects[0]["conditions"] = []
    k.add_condition("grappled")
    k.effects[0]["conditions"] = ["grappled"]
    spells.cast(enc, roller(2), "kairos", "hold person", ["frog-1"])    # WIS 2 + 0: fails
    assert f.has("paralyzed") and not k.has("grappled")


def test_a_hidden_caster_keeps_advantage_on_a_spell_attack():
    k, f = caster(), frog("frog-1", (4, 0))
    k.add_condition("hidden")
    enc = fight(k, f)
    # Advantage: the player's kept d20 is supplied as one value (the engine keeps the higher).
    res = spells.cast(enc, roller(supplied=[18, 7]), "kairos", "fire bolt", ["frog-1"])
    assert "advantage" in res["text"] and not k.has("hidden")


def test_ready_respects_the_bonus_action_spell_rule():
    k = caster(spells=KAIROS_SPELLS + ["Healing Word"])
    enc = fight(k, frog("frog-1", (4, 0)))
    spells.cast(enc, roller(supplied=[2]), "kairos", "healing word", ["kairos"])
    with pytest.raises(CombatError, match="only a 1-action cantrip"):
        actions.ready(enc, roller(), "kairos", "cast", "magic missile", "frog-1")
    assert k.extra["slots"]["1"]["used"] == 1


def test_undo_cannot_bring_back_a_released_grapple():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    k.reactions = "off"
    enc = fight(f, k)
    engine.attack(enc, roller(15, 4), "frog-1", "kairos")
    enc.turn.action_used, enc.turn.undo_locked = True, False
    k.reaction_used = True                               # no opportunity attack
    engine.move(enc, roller(), "frog-1", "D1")
    assert not k.has("grappled") and not engine.can_undo(enc)


def test_ending_an_effect_keeps_prone_and_other_conditions():
    goblin = monster("goblin", "goblin-1", (3, 0))
    goblin.add_condition("poisoned")                     # from something else
    enc = fight(caster(), goblin)
    e = {"name": "hideous laughter", "source": "kairos", "conditions": ["prone", "incapacitated", "poisoned"]}
    fx.add(goblin, e)
    fx.remove(goblin, goblin.effects[0])
    assert goblin.has("prone") and goblin.has("poisoned") and not goblin.has("incapacitated")


def test_nothing_clings_to_a_corpse():
    k, f = caster(pos=(0, 0)), frog("frog-1", (1, 0))
    k.reactions = "off"
    enc = fight(f, k)
    engine.attack(enc, roller(15, 4), "frog-1", "kairos")
    f.hp = 1
    enc.turn_index, enc.turn.actor = 1, "kairos"
    enc.turn.action_used = False
    k.effects[0]["conditions"], k.conditions = [], []    # let him act for the test
    spells.cast(enc, roller(supplied=[15, 7]), "kairos", "fire bolt", ["frog-1"])
    assert f.dead and not f.effects and not fx.grappling(enc, f)


def test_mass_heals_and_temporary_hp_spells_do_not_crash_or_heal_wrongly():
    k = caster(spells=KAIROS_SPELLS + ["False Life", "Mass Cure Wounds"])
    k.extra["slots"]["5"] = {"total": 1, "used": 0}
    heal = {"casting": "action", "range": 0, "origin": "self", "concentration": False, "flags": []}
    k.extra["spellbook"] = {
        "false life": dict(heal, name="False Life", level=1, heal={"slot": {"1": "1d4+4"}}),
        "mass cure wounds": dict(heal, name="Mass Cure Wounds", level=5, range=60, origin="point",
                                 area={"shape": "sphere", "size": 30},
                                 heal={"slot": {"5": "3d8+MOD"}})}
    assert RULES.spell(k, "false life")["mode"] == "narrate"        # temp HP: the GM's
    ally, f = caster(pos=(2, 0)), frog("frog-1", (3, 0))
    ally.id, ally.name, ally.hp = "mira", "Mira", 1
    k.hp = 2
    enc = fight(k, ally, f)
    f.hp = 5
    # 3d8+3 = 10 + 3 for each of Kairos and Mira; the frog in the area is not healed.
    spells.cast(enc, roller(supplied=[10, 10]), "kairos", "mass cure wounds", ["B1"])
    assert (k.hp, ally.hp, f.hp) == (8, 8, 5)


def test_a_condition_shared_by_two_effects_ends_with_the_last_one():
    f = frog("frog-1", (3, 0))
    a = {"name": "web", "source": "kairos", "conditions": ["restrained"]}
    b = {"name": "grapple", "source": "frog-2", "conditions": ["grappled", "restrained"]}
    fx.add(f, a)
    fx.add(f, b)
    fx.remove(f, f.effects[0])
    assert f.has("restrained")                           # the grapple still holds it
    fx.remove(f, f.effects[0])
    assert not f.has("restrained") and not f.has("grappled") and not f.effects


def test_a_prone_rider_leaves_no_empty_effect():
    f = frog("frog-1", (3, 0))
    fx.add(f, {"name": "trip", "source": "wolf-1", "conditions": ["prone"],
               "save": {"ability": "str", "dc": 11}, "repeat": "end"})
    assert f.has("prone") and f.effects == []


def test_a_hidden_enemy_is_not_in_the_players_snapshot():
    from tactics import sync
    k, g = caster(), monster("goblin", "goblin-1", (3, 0))
    g.add_condition("hidden")
    enc = fight(k, g)
    ids = [t["id"] for t in sync.snapshot(enc)["tokens"]]
    assert ids == ["kairos"]

