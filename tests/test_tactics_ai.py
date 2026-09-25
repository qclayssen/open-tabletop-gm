"""Multiattack, enemy tactics and the rules fixes that came with them.

Tactics borrowed from Baldur's Gate 3, XCOM and Dofus (focus fire, cover,
shoot then fall back) and from 5e monster-tactics writing (morale), always
inside the 2014 rules as written. Dice are scripted: see tactics_fixtures.
"""
from tests.tactics_fixtures import (RULES, _build, _RAW, encounter, engine, frog, goblin,
                                    kairos, roller, start, token_from_monster)

from tactics import ai
from tactics.rules import AttackContext
from tactics.state import Token


def captain(tid="cap", pos=(1, 0)):
    """Bandit Captain: Multiattack is Scimitar x2 + Dagger, or Dagger x2."""
    return token_from_monster(_build._norm_monster(_RAW["bandit-captain"]), tid, "Bandit Captain", pos)


def tough(k, hp=40):
    k.hp = k.max_hp = hp
    return k


def mira(pos, hp=30):
    return Token(id="mira", name="Mira", side="pc", x=pos[0], y=pos[1], hp=hp, max_hp=hp, ac=12)


# ─── Multiattack ─────────────────────────────────────────────────────────────

def test_multiattack_makes_every_attack_as_one_action():
    enc = start(encounter([tough(kairos(pos=(0, 0))), captain()]), ["cap", "kairos"])
    res = engine.multiattack(enc, roller(15, 3, 15, 3, 15, 2), "cap", "kairos")
    assert res["routine"] == 1 and len(res["attacks"]) == 3
    assert enc.tokens["kairos"].hp == 40 - 6 - 6 - 5
    assert enc.turn.action_used
    assert res["text"].startswith("Bandit Captain Multiattack (Scimitar x2, Dagger).")


def test_multiattack_switches_to_a_conscious_target_when_the_first_drops():
    enc = start(encounter([kairos(pos=(0, 0), hp=5), captain(), mira((2, 0))]),
                ["cap", "kairos", "mira"])
    engine.multiattack(enc, roller(15, 3, 15, 3, 15, 2), "cap", "kairos")
    assert enc.tokens["kairos"].hp == 0
    assert enc.tokens["kairos"].death_saves["failures"] == 0     # not hit again while down
    assert enc.tokens["mira"].hp == 30 - 6 - 5


def test_multiattack_never_turns_on_a_downed_pc():
    enc = start(encounter([kairos(pos=(0, 0), hp=5), captain()]), ["cap", "kairos"])
    res = engine.multiattack(enc, roller(15, 3), "cap", "kairos")
    assert res["text"].count("no conscious target in reach, not made") == 2
    assert enc.tokens["kairos"].death_saves["failures"] == 0


def test_parts_that_are_not_attacks_go_to_the_gm():
    dragon = token_from_monster(_build._norm_monster(_RAW["adult-red-dragon"]), "d", "Dragon", (1, 0))
    attacks, other = engine.expand_routine(dragon, engine.multiattack_routines(dragon)[0])
    assert [a["name"] for a in attacks] == ["Bite", "Claw", "Claw"]
    assert other == ["Frightful Presence"]


def test_veteran_multiattack_errata_is_three_attacks():
    rec = {"index": "veteran", "hp": 58, "ac": 17, "speed": "30 ft.", "type": "humanoid",
           "actions": [{"name": "Multiattack", "kind": "multiattack", "flags": [],
                        "multiattack": [[{"action": "Longsword", "count": 2},
                                         {"action": "Shortsword", "count": 2}]]}]}
    t = token_from_monster(rec, "vet", "Veteran", (0, 0))
    assert sum(i["count"] for i in engine.multiattack_routines(t)[0]) == 3


def test_enemy_menu_offers_multiattack_instead_of_single_swings():
    enc = start(encounter([tough(kairos(pos=(0, 0))), captain()]), ["cap", "kairos"])
    opts = ai.options(enc, "cap")
    assert opts[0]["kind"] == "multiattack"
    assert opts[0]["label"].startswith("Multiattack Kairos: Scimitar x2, Dagger from here (70%/70%/70%")
    assert not any(o["kind"] == "attack" and o["attack"] == "Scimitar" for o in opts)
    assert "Multiattack" not in ai.specials(enc, "cap")
    res = ai.choose(enc, roller(15, 3, 15, 3, 15, 2), "cap", 1)
    assert enc.tokens["kairos"].hp == 23 and "Multiattack" in res["text"]


# ─── Targets and morale ──────────────────────────────────────────────────────

def test_when_no_one_fights_back_the_menu_says_so():
    k = kairos(pos=(0, 0))
    k.hp = 0
    k.add_condition("unconscious")
    k.add_condition("prone")
    enc = start(encounter([k, frog("frog-1", (1, 0))]), ["frog-1", "kairos"])
    opts = ai.options(enc, "frog-1")
    assert opts[0]["kind"] == "wait" and "GM decides to finish, capture or leave" in opts[0]["label"]
    downed = next(o for o in opts if o["kind"] == "attack")
    assert "death save failure" in downed["label"] and "can finish them" not in downed["label"]
    assert ai.choose(enc, roller(), "frog-1", 1)["text"] == "Frog 1 waits."
    assert not enc.turn.action_used


def test_with_someone_still_standing_a_downed_pc_ranks_below_dodge():
    k = kairos(pos=(0, 0))
    k.hp = 0
    k.add_condition("unconscious")
    enc = start(encounter([k, frog("frog-1", (1, 0)), mira((7, 7))]), ["frog-1", "kairos", "mira"])
    kinds = [(o["kind"], o.get("target")) for o in ai.options(enc, "frog-1")]
    assert kinds.index(("dodge", None)) < kinds.index(("attack", "kairos"))


def test_morale_breaks_when_half_the_side_is_down():
    g1, g2 = goblin("goblin-1", (1, 0)), goblin("goblin-2", (5, 5))
    g1.hp, g2.dead = 3, True                                   # 3/7: above 25%, below 50%
    enc = start(encounter([kairos(pos=(0, 0)), g1, g2]), ["goblin-1", "kairos"])
    first = ai.options(enc, "goblin-1")[0]
    assert first["kind"] == "retreat" and "morale broken" in first["label"]


def test_undead_fight_to_the_end():
    f = frog("frog-1", (1, 0))
    f.hp, f.extra["type"] = 1, "undead"
    enc = start(encounter([kairos(pos=(0, 0)), f]), ["frog-1", "kairos"])
    assert ai.options(enc, "frog-1")[0]["kind"] == "attack"


def test_archers_shoot_then_fall_back():
    g = goblin("goblin-1", (4, 0))
    g.attacks = [a for a in g.attacks if a["name"] == "Shortbow"]
    k = kairos(pos=(0, 0))
    k.attacks = [a for a in k.attacks if a["name"] == "Fire Bolt"]
    enc = start(encounter([k, g]), ["goblin-1", "kairos"])
    first = ai.options(enc, "goblin-1")[0]
    assert first["then_to"] and ", then fall back to " in first["label"]
    res = ai.choose(enc, roller(15, 3), "goblin-1", 1)
    assert enc.tokens["goblin-1"].square == first["then_to"]
    assert enc.tokens["kairos"].hp == 3 and "moves" in res["text"]


# ─── Rules fixes ─────────────────────────────────────────────────────────────

def test_a_nat_20_death_save_gives_the_turns_movement_back():
    k = kairos(pos=(0, 0))
    k.hp = 0
    k.add_condition("unconscious")
    k.add_condition("prone")
    enc = start(encounter([k, frog("frog-1", (5, 5))], roll_mode="auto"), ["frog-1", "kairos"])
    res = engine.end_turn(enc, roller(20))
    assert "regains 1 HP" in res["text"]
    assert engine.remaining_movement(enc) == 30
    assert "stands up" in engine.stand_up(enc, "kairos")["text"]


def test_an_opportunity_attack_uses_an_attack_that_reaches():
    whip = {"name": "Whip", "type": "melee", "bonus": 4, "reach": 10,
            "damage": [{"dice": "1d4", "type": "slashing"}], "flags": []}
    club = {"name": "Club", "type": "melee", "bonus": 4, "reach": 5,
            "damage": [{"dice": "2d8", "type": "bludgeoning"}], "flags": []}
    h = Token(id="guard", name="Guard", side="enemy", x=0, y=0, hp=11, max_hp=11, ac=12,
              attacks=[club, whip])
    enc = start(encounter([kairos(pos=(2, 0)), h]), ["kairos", "guard"])
    warn = engine.preview_move(enc, "kairos", "E1")["opportunity_attacks"]
    assert [w["attack"] for w in warn] == ["Whip"]


def test_a_ranged_hit_from_within_5_ft_on_an_unconscious_target_is_a_crit():
    k = kairos(pos=(0, 0))
    k.hp = 0
    k.add_condition("unconscious")
    shortbow = next(a for a in goblin().attacks if a["name"] == "Shortbow")
    res = RULES.attack(goblin(), k, shortbow, AttackContext(distance=5, melee=False),
                       roller(15, 10, 3, 3), player=False)
    assert res["hit"] and res["crit"]


def test_attack_without_a_name_uses_the_best_legal_attack():
    g = goblin("goblin-1", (1, 0))
    g.attacks[0]["damage"] = [{"dice": "1d4", "type": "slashing"}]   # scimitar now weaker
    for a in g.attacks:
        if a["name"] == "Shortbow":
            a.update(type="melee_or_ranged", reach=5)                 # usable in melee
    enc = start(encounter([tough(kairos(pos=(0, 0))), g]), ["goblin-1", "kairos"])
    res = engine.attack(enc, roller(15, 3), "goblin-1", "kairos")
    assert "Shortbow" in res["text"]
