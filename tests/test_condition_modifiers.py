"""The condition modifier engine, condition by condition (Phase A Task A1).

Every one of the fourteen 5e conditions plus exhaustion is checked three times
over: the table says what it changes, the roll it reaches is the right one, and
the printed line says why — because a modifier nobody can see in the output is a
modifier the table will be quietly "corrected" out of the next time it is wrong.

Dice are scripted (tests/tactics_fixtures.py), so each expected number is the
arithmetic in the comment beside it.
"""
from __future__ import annotations

import json

import pytest

from tests.tactics_fixtures import (RULES, encounter, frog, goblin, kairos, roller,
                                    start, state)
from tests.test_tactics_cli import begin, camp, run       # noqa: F401  (the CLI harness)
from tactics import effects as fx
from tactics import engine
from tactics.rules import AttackContext
import systems.dnd5e.tactics_rules as tr

MELEE = AttackContext(distance=5, melee=True)
FAR_MELEE = AttackContext(distance=10, melee=True)      # a reach-10 attacker
RANGED = AttackContext(distance=30, melee=False)
OUT_OF_SIGHT = AttackContext(distance=5, melee=True, source_in_sight=False)

CONDITIONS = ("blinded", "charmed", "deafened", "frightened", "grappled", "incapacitated",
              "invisible", "paralyzed", "petrified", "poisoned", "prone", "restrained",
              "stunned", "unconscious")


def with_(cond: str, **kw):
    """A token carrying one condition, the way the GM applied it.

    `side` and `ac` are for the times a condition has to sit on the *target* of an
    attack rather than on the one making it.
    """
    t = kairos(hp=kw.pop("hp", 8), pos=kw.pop("pos", (0, 0)))
    for key, value in kw.items():
        setattr(t, key, value)
    RULES.set_condition(t, cond)
    return t


def mods(cond: str) -> dict:
    return tr.get_condition_modifiers(with_(cond))


# ─── A1.1  the shape of the answer ────────────────────────────────────────────

def test_every_condition_is_in_the_table():
    assert set(tr.CONDITION_EFFECTS) == set(CONDITIONS) | {"exhaustion"}


def test_the_six_keys_are_always_there():
    """A caller must never have to tell "this condition grants nothing" from
    "nobody looked": a missing key is an exception twenty commands into a fight."""
    for token in (kairos(), with_("poisoned"), with_("stunned"), with_("exhaustion 3")):
        assert set(tr.MODIFIER_KEYS) <= set(tr.get_condition_modifiers(token))


def test_a_clean_creature_gets_neutral_values():
    m = tr.get_condition_modifiers(kairos())
    assert (m["attack_roll"], m["ability_check"], m["attack_against"],
            m["movement"], m["action_economy"]) == (None, None, None, None, None)
    assert m["save"] == {} and m["sources"] == []


def test_advantage_and_disadvantage_from_two_conditions_cancel():
    t = with_("poisoned")
    assert tr.get_condition_modifiers(t)["attack_roll"] == "dis"
    RULES.set_condition(t, "invisible")
    assert tr.get_condition_modifiers(t)["attack_roll"] is None   # +adv and -dis cancel
    RULES.set_condition(t, "blinded")                 # a second dis, which is still a dis
    assert tr.get_condition_modifiers(t)["attack_roll"] == "dis"


def test_a_gated_effect_that_does_not_apply_contributes_nothing():
    """An unmet gate must be silent, not cancelling: an invisible creature's melee
    swing keeps its advantage even while prone, whose own penalty is a ranged one."""
    t = with_("invisible")
    RULES.set_condition(t, "prone")
    m = tr.get_condition_modifiers(t)
    assert m["attack_roll"] == {"*": "adv", "ranged": "dis"}
    assert tr._mod_for(m, "attack_roll", t, MELEE) == "adv"      # prone's gate is silent
    assert tr._mod_for(m, "attack_roll", t, RANGED) is None       # adv and the gated dis
    assert RULES.advantage(t, frog(), MELEE)[0] == "advantage"


# ─── A1.2  poisoned ───────────────────────────────────────────────────────────

def test_poisoned_gives_disadvantage_on_attacks_and_checks():
    m = mods("poisoned")
    assert m["attack_roll"] == "dis" and m["ability_check"] == "dis"
    assert RULES.advantage(with_("poisoned"), frog(), MELEE)[0] == "disadvantage"


# ─── A1.3  blinded ────────────────────────────────────────────────────────────

def test_blinded():
    m = mods("blinded")
    assert m["attack_roll"] == "dis" and m["ability_check"] == "dis"
    assert m["attack_against"] == "adv"
    # Dagger +4 vs AC 11: need a 7, 14/20 = 70%; 1 - 0.3^2 = 91%.
    hc = RULES.hit_chance(kairos(), with_("blinded", side="enemy", ac=11),
                          {"name": "Dagger", "bonus": 4, "damage": []}, MELEE)
    assert hc["percent"] == 91


# ─── A1.4  stunned ────────────────────────────────────────────────────────────

def test_stunned():
    m = mods("stunned")
    assert m["attack_roll"] == "dis" and m["ability_check"] == "dis"
    assert m["save"] == {"str": "auto_fail", "dex": "auto_fail"}
    assert m["attack_against"] == "adv"
    assert m["action_economy"] == "none" and m["movement"] == 0
    assert not RULES.can_act(with_("stunned")) and RULES.speed(with_("stunned")) == 0


# ─── A1.5  prone ──────────────────────────────────────────────────────────────

def test_prone_melee_within_5_ft_is_advantage_and_ranged_is_disadvantage():
    m = mods("prone")
    assert m["attack_roll"] == {"ranged": "dis"}
    assert m["attack_against"] == {"melee_within_5": "adv", "ranged": "dis"}
    prone = with_("prone")
    assert RULES.advantage(kairos(), prone, MELEE)[0] == "advantage"
    assert RULES.advantage(kairos(), prone, RANGED)[0] == "disadvantage"
    # A prone creature's own ranged attack is the hard one (PHB p292).
    assert RULES.advantage(prone, frog(), RANGED)[0] == "disadvantage"
    assert RULES.advantage(prone, frog(), MELEE)[0] == "normal"


def test_prone_melee_beyond_5_ft_is_an_ordinary_roll():
    """PHB p292: advantage to attackers *within 5 ft*, disadvantage to ranged
    attacks. A 10 ft reach is neither, and the engine used to call it a
    disadvantage — the preview said 36% for a roll with nothing wrong with it."""
    assert RULES.advantage(kairos(), with_("prone"), FAR_MELEE)[0] == "normal"


# ─── A1.6  restrained ─────────────────────────────────────────────────────────

def test_restrained():
    m = mods("restrained")
    assert m["attack_roll"] == "dis" and m["attack_against"] == "adv"
    assert m["save"] == {"dex": "dis"} and m["movement"] == 0
    # DEX +2 on Kairos: d20 8 -> 10, the DC 12 is a failure either way, but the
    # roll has to be the disadvantaged one: faces 3 and 9, keep 3.
    r = roller(3, 9)
    res = RULES.saving_throw(with_("restrained"), "dex", 12, r, player=False)
    assert res["advantage"] == "disadvantage" and res["total"] == 5
    # A STR save is untouched: a rigger's Strength is their own.
    assert RULES.saving_throw(with_("restrained"), "str", 12, roller(8), player=False)[
        "advantage"] == "normal"


# ─── A1.7  paralyzed and unconscious ──────────────────────────────────────────

@pytest.mark.parametrize("cond", ["paralyzed", "unconscious"])
def test_paralyzed_and_unconscious(cond):
    m = mods(cond)
    assert m["attack_roll"] == "dis" and m["ability_check"] == "dis"
    assert m["save"] == {"str": "auto_fail", "dex": "auto_fail"}
    assert m["attack_against"] == "adv" and m["action_economy"] == "none"
    assert m["auto_crit_within_5"] is True
    # STR and DEX auto-fail without a die; the other four are rolled as usual.
    res = RULES.saving_throw(with_(cond), "str", 10, roller(), player=False)
    assert res["auto_fail"] and res["total"] is None and cond in res["text"]
    assert RULES.saving_throw(with_(cond), "int", 10, roller(8), player=False)["auto_fail"] is False


def test_petrified_is_stunned_and_immune_to_poison_and_disease():
    m = mods("petrified")
    assert m["save"] == {"str": "auto_fail", "dex": "auto_fail"}
    assert m["auto_crit_within_5"] is True
    assert set(m["immunities"]) == {"poison", "disease"}
    t = with_("petrified", hp=10)
    dmg = RULES.damage(t, [{"amount": 5, "type": "poison"}, {"amount": 4, "type": "slashing"}])
    # Poison is immunity, so that 5 is 0. Slashing is still damage, and petrified
    # resists all of it, so 4 becomes 2. The old total of 4 was the missing resistance.
    assert dmg["total"] == 2 and "immune to poison" in dmg["text"]
    assert "resists all damage" in dmg["text"]


def test_a_hit_within_5_ft_on_a_paralyzed_target_is_a_crit():
    k = with_("paralyzed", hp=8, side="enemy")
    # Advantage: faces 5 and 11, keep 11 -> 15; auto-crit within 5 ft: 2d6+2 -> 9.
    res = RULES.attack(goblin(), k, goblin().attacks[0], MELEE, roller(5, 11, 3, 4), player=False)
    assert res["crit"] and res["damage"]["total"] == 9


# ─── A1.8  exhaustion 1-6 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("level,key,value", [
    (1, "ability_check", "dis"),
    (2, "movement", "half"),
    (3, "attack_roll", "dis"),
    (4, "hp_max", "half"),
    (5, "movement", 0),
    (6, "death", True),
])
def test_each_exhaustion_level(level, key, value):
    assert tr.get_condition_modifiers(with_(f"exhaustion {level}"))[key] == value


def test_exhaustion_three_hits_attacks_and_every_save():
    t = with_("exhaustion 3")
    assert RULES.advantage(t, frog(), MELEE)[0] == "disadvantage"
    for ability in ("str", "dex", "con", "int", "wis", "cha"):
        assert RULES.saving_throw(t, ability, 15, roller(4, 17), player=False)["advantage"] \
            == "disadvantage"


def test_exhaustion_two_halves_the_speed_and_five_stops_it():
    assert RULES.speed(with_("exhaustion 2")) == 15
    assert RULES.speed(with_("exhaustion 5")) == 0


def test_exhaustion_four_halves_the_hit_points_once_and_six_kills():
    t = with_("exhaustion 4", hp=8)
    assert t.max_hp == 4 and t.hp == 4
    RULES.set_condition(t, "exhaustion 5")            # deeper still, not halved again
    assert t.max_hp == 4
    RULES.clear_condition(t, "exhaustion 2")          # a long rest took one level off
    assert t.max_hp == 8
    dead = with_("exhaustion 6")
    assert dead.dead and not RULES.can_act(dead)


def test_clearing_one_condition_leaves_the_others_alone():
    """Removing a poison must not quietly take the exhaustion with it."""
    t = with_("exhaustion 3")
    RULES.set_condition(t, "poisoned")
    RULES.clear_condition(t, "poisoned")
    assert t.has("exhaustion") and t.extra["exhaustion_level"] == 3
    # "exhaustion" with no number is one level off, which is what a long rest
    # does; with a number it is the GM ruling that level outright.
    RULES.clear_condition(t, "exhaustion")
    assert tr.exhaustion_level(t) == 2
    RULES.clear_condition(t, "exhaustion 5")
    assert tr.exhaustion_level(t) == 5 and RULES.speed(t) == 0
    RULES.clear_condition(t, "exhaustion")
    assert tr.exhaustion_level(t) == 4 and RULES.speed(t) == 30


def test_exhaustion_level_lives_in_extra_and_survives_a_save():
    t = with_("exhaustion 3")
    assert t.conditions == ["exhaustion"] and t.extra["exhaustion_level"] == 3
    again = state.Token.from_dict(t.to_dict())
    assert tr.get_condition_modifiers(again)["attack_roll"] == "dis"


def test_the_word_exhaustion_alone_is_level_one():
    t = with_("exhaustion")
    assert t.extra["exhaustion_level"] == 1
    assert tr.get_condition_modifiers(t)["ability_check"] == "dis"


# ─── A1.9  frightened ─────────────────────────────────────────────────────────

def test_frightened_is_disadvantage_only_while_the_source_is_in_sight():
    m = mods("frightened")
    assert m["attack_roll"] == {"source_in_sight": "dis"}
    assert m["ability_check"] == {"source_in_sight": "dis"}
    t = with_("frightened")
    assert RULES.advantage(t, frog(), MELEE)[0] == "disadvantage"
    assert RULES.advantage(t, frog(), OUT_OF_SIGHT)[0] == "normal"


def test_a_restrained_creature_gets_no_credit_from_a_dodge():
    """Dodge is worth nothing against the DEX penalty of being restrained, and
    the mode has to be the worse of the two or a creature gets to pick."""
    t = with_("restrained")
    t.dodging = True
    assert RULES._save_mode(t, "dex")[0] == "disadvantage"
    t.dodging = False
    assert RULES._save_mode(t, "dex")[0] == "disadvantage"
    # An unconditioned creature does get the dodge, so the above is the condition
    # winning and not the method never granting it.
    free = kairos()
    free.dodging = True
    assert RULES._save_mode(free, "dex")[0] == "advantage"


# ─── A1.10  charmed ───────────────────────────────────────────────────────────

def test_charmed_gates_on_the_charmer():
    m = mods("charmed")
    assert m["attack_roll"] == {"charmer": "dis"}
    assert m["ability_check"] == {"charmer_social": "adv"}
    assert m["harmful_to"] == ["charmer"]


def test_the_charm_makes_the_creature_weak_against_its_charmer():
    """A charmed creature will not fight the friend: its attack rolls against the
    charmer are disadvantage, and against anyone else they are its own."""
    charmed = _charmed_by(goblin())
    assert RULES.advantage(charmed, goblin(), MELEE)[0] == "disadvantage"
    assert RULES.advantage(charmed, goblin("goblin-2"), MELEE)[0] == "normal"
    # Nobody charmed it by name, so there is no charmer to single out.
    assert RULES.advantage(with_("charmed"), goblin(), MELEE)[0] == "normal"


def _charmed_by(charmer):
    """A charmed creature with its charmer recorded, the way a spell records it
    (effects.py writes `source` on everything it grants)."""
    t = with_("charmed")
    fx.add(t, {"name": "Charm", "source": charmer.id, "conditions": ["charmed"]})
    return t


# ─── A1.11  invisible ─────────────────────────────────────────────────────────

def test_invisible():
    m = mods("invisible")
    assert m["attack_roll"] == "adv" and m["attack_against"] == "dis"
    assert RULES.advantage(with_("invisible"), frog(), MELEE)[0] == "advantage"
    assert RULES.advantage(kairos(), with_("invisible", side="enemy"), MELEE)[0] == "disadvantage"


def test_the_conditions_that_have_no_attack_modifier():
    """Deafened, grappled and incapacitated change one thing each, and the table
    should say so rather than leaving them out and hoping."""
    assert mods("deafened")["ability_check"] == {"hearing": "auto_fail"}
    assert mods("grappled")["movement"] == 0
    assert mods("incapacitated")["action_economy"] == "none"
    assert not RULES.can_act(with_("incapacitated"))


# ─── A1.12  attack() uses the modifiers, and the GM's ruling wins ─────────────

def test_a_poisoned_attack_rolls_twice_and_says_so():
    # Fire Bolt +6 vs frog AC 11: disadvantage, faces 3 and 14, keep 3 -> 9: a miss.
    t = with_("poisoned")
    res = RULES.attack(t, frog(), t.attacks[0], RANGED, roller(3, 14), player=False)
    assert res["advantage"] == "disadvantage" and res["total"] == 9 and not res["hit"]
    assert "disadvantage" in res["text"] and "poisoned" in res["text"]


def test_an_explicit_ruling_beats_the_condition_in_both_directions():
    t = with_("poisoned")
    assert RULES.advantage(t, frog(), MELEE, "advantage")[0] == "advantage"
    assert RULES.advantage(t, frog(), MELEE, "disadvantage")[0] == "disadvantage"
    # ...including when the ruling agrees with what the condition would have said,
    # which is the case that has to keep its reason in the line.
    res = RULES.attack(t, frog(), t.attacks[0], RANGED, roller(17, 3, 9), player=False,
                       explicit="advantage")
    assert res["advantage"] == "advantage" and "GM's ruling" in res["text"]
    assert res["total"] == 23 and res["hit"]


def test_a_ruling_beats_the_targets_condition_too():
    prone = with_("prone", side="enemy", ac=11)
    assert RULES.advantage(kairos(), prone, MELEE, "disadvantage")[0] == "disadvantage"


def test_hit_chance_and_the_roll_agree_under_a_ruling():
    """The preview a player commits against has to be the roll they get: a
    --dis on a prone target is 56%, not the 94% the condition alone would give."""
    prone = with_("prone", side="enemy", ac=11)
    bolt = {"name": "Fire Bolt", "bonus": 5, "damage": [{"dice": "1d10"}]}
    # Need a 6 to hit (11 - 5): 75%; advantage against a prone target: 1-0.25^2 = 94%.
    assert RULES.hit_chance(kairos(), prone, bolt, MELEE)["percent"] == 94
    # The same roll with the GM's ruling against: 0.75^2 = 56%.
    assert RULES.hit_chance(kairos(), prone, bolt, MELEE, "disadvantage")["percent"] == 56


# ─── A1.13  saving_throw() uses the modifiers ─────────────────────────────────

def test_an_auto_failing_save_rolls_nothing_and_says_which_condition():
    t = with_("stunned")
    r = roller()                                  # scripted dice: a roll here is a bug
    res = RULES.saving_throw(t, "dex", 15, r, player=False)
    assert res["auto_fail"] and not r.log and res["advantage"] == "auto fail"
    assert "stunned" in res["text"] and res["text"].endswith("(stunned).")
    assert RULES.save_chance(t, "dex", 15)["percent_fail"] == 100


def test_a_conditional_save_penalty_and_an_explicit_ruling():
    t = with_("restrained")
    r = roller(3, 9)                              # disadvantage: keep 3, +2 DEX = 5
    assert RULES.saving_throw(t, "dex", 12, r, player=False)["total"] == 5
    # A stunned creature is normally handed a save to make, and the GM may call it
    # either way: an explicit ruling outranks the condition that would forbid it.
    t2 = with_("stunned")
    res = RULES.saving_throw(t2, "int", 10, roller(3, 12), player=False, explicit="advantage")
    assert res["advantage"] == "advantage" and res["total"] == 18   # keep 12, +6 INT
    assert res["text"] == ("Kairos INT save: 18 vs DC 10, advantage, success "
                           "(advantage: the GM's ruling).")


# ─── A1.14  the line says why ─────────────────────────────────────────────────

def test_the_attack_line_names_the_condition_that_did_it():
    t = with_("poisoned")
    res = RULES.attack(t, frog(), t.attacks[0], RANGED, roller(3, 14), player=False)
    assert res["text"].startswith("Kairos Fire Bolt -> Frog 1: 9 vs AC 11, disadvantage, miss")
    assert "disadvantage: Kairos is poisoned" in res["text"]


def test_a_normal_roll_says_nothing_about_conditions():
    """Fire Bolt +6 vs frog AC 11: d20 12 -> 18, hit; 1d10 with a 9 -> 9 damage."""
    res = RULES.attack(kairos(), frog(), kairos().attacks[0], RANGED, roller(12, 9), player=False)
    assert res["text"] == ("Kairos Fire Bolt -> Frog 1: 18 vs AC 11, hit. "
                           "9 fire damage; Frog 1 9/18 HP.")


def test_condition_notes_say_what_the_condition_is_doing():
    assert RULES.condition_notes(with_("exhaustion 3")) == [
        "exhaustion 3: disadvantage on attack rolls and saving throws"]
    assert RULES.condition_notes(with_("poisoned")) == [
        "poisoned: disadvantage on attack rolls and ability checks"]
    # Every condition says something, and nothing on an unconditioned creature,
    # so the line never ends in a bare condition name.
    for cond in CONDITIONS:
        notes = RULES.condition_notes(with_(cond))
        assert len(notes) == 1 and notes[0].startswith(f"{cond}: ") and len(notes[0]) > 12
    assert RULES.condition_notes(kairos()) == []


def test_the_only_line_a_roll_needs_is_the_reason_it_rolled_twice():
    """Two die faces for a normal attack would mean the engine is spending dice
    on a roll nobody asked for."""
    r = roller(12, 9)                              # one face for the attack, one for damage
    res = RULES.attack(kairos(), frog(), kairos().attacks[0], RANGED, r, player=False)
    assert len(r.log) == 2 and r.log[0].dice == [12]


# ─── the engine and the command line ──────────────────────────────────────────

def test_the_engine_measures_whether_the_source_is_in_sight():
    """A charm or a fear lasts exactly as long as the creature causing it, so the
    engine can measure that gate instead of assuming it holds."""
    def fight(rows=None):
        k = kairos(pos=(0, 1))
        source, prey = frog("frog-1", (0, 5)), frog("frog-2", (3, 1))
        enc = start(encounter([k, source, prey], rows), ["kairos", "frog-1", "frog-2"])
        RULES.set_condition(k, "frightened")
        fx.add(k, {"name": "Fear", "source": "frog-1", "conditions": ["frightened"]})
        ctx = engine._attack_context(enc, k, prey, k.attacks[0])[0]
        return RULES.advantage(k, prey, ctx)[0], ctx

    mode, ctx = fight()
    assert ctx.source_in_sight is True and mode == "disadvantage"
    # A wall between the frightened creature and the thing frightening it. The
    # fear does not end, it stops counting (PHB p291).
    walled = ["." * 8, "." * 8, "########", "." * 8, "." * 8, "." * 8]
    mode, ctx = fight(walled)
    assert ctx.source_in_sight is False and mode == "normal"


def test_a_check_is_rolled_out_of_turn_with_the_conditions_on_it():
    k, f = kairos(pos=(0, 0)), frog("frog-1", (2, 0))
    enc = start(encounter([k, f]), ["kairos", "frog-1"])
    RULES.set_condition(k, "poisoned")
    res = engine.check(enc, roller(supplied=[3, 14]), "kairos", "perception", 10,
                       advantage="normal", sense="sight")
    assert res["advantage"] == "disadvantage" and "poisoned" in res["text"]
    assert res["total"] == 5 and not res["success"]
    assert enc.turn.action_used is False          # a check costs no action


def test_a_deafened_creature_fails_only_the_checks_that_need_hearing():
    k, f = kairos(pos=(0, 0)), frog("frog-1", (2, 0))
    enc = start(encounter([k, f]), ["kairos", "frog-1"])
    RULES.set_condition(k, "deafened")
    hearing = engine.check(enc, roller(supplied=[]), "kairos", "perception", 10, sense="hearing")
    assert hearing["auto_fail"] and hearing["text"].endswith("check (deaf).")
    # The same creature hears nothing about what it can see, and still rolls.
    seen = engine.check(enc, roller(supplied=[12]), "kairos", "perception", 10, sense="sight")
    assert seen["auto_fail"] is False and seen["advantage"] == "normal"


def test_a_frightened_check_only_counts_while_its_source_is_in_sight():
    walled = ["." * 8, "." * 8, "########", "." * 8, "." * 8, "." * 8]
    k = kairos(pos=(0, 1))
    f = frog("frog-1", (0, 5))
    enc = start(encounter([k, f], walled), ["kairos", "frog-1"])
    RULES.set_condition(k, "frightened")
    fx.add(k, {"name": "Fear", "source": "frog-1", "conditions": ["frightened"]})
    # The frog is behind a wall, so the fear is not doing anything to the check.
    assert engine.check(enc, roller(supplied=[8]), "kairos", "perception", 10,
                        source_ref="frog-1")["advantage"] == "normal"
    f.x, f.y = 0, 1                                # the frog comes round the wall
    assert engine.check(enc, roller(supplied=[8]), "kairos", "perception", 10,
                        source_ref="frog-1")["advantage"] == "disadvantage"


def _kairos_on_turn(camp):
    """Kairos first, so the test is about the command and not about initiative."""
    path = camp / "combat" / "encounter.json"
    enc = json.loads(path.read_text(encoding="utf-8"))
    enc["order"] = ["kairos", "frog-1", "frog-2"]
    enc["turn_index"] = 0
    enc["turn"]["actor"] = "kairos"
    enc["turn"]["action_used"] = False
    enc["turn"]["movement_used"] = 0
    enc["turn"]["movement_budget"] = 30
    path.write_text(json.dumps(enc), encoding="utf-8")


def test_the_explicit_flags_survive_the_command_line(camp, capsys):
    code, out = begin(capsys, "--roll-mode", "auto")
    assert code == 0
    run(capsys, "condition", "kairos", "add", "poisoned")
    _kairos_on_turn(camp)
    code, out = run(capsys, "attack", "kairos", "frog-1", "fire bolt", "--seed", "7")
    assert code == 0, out
    assert "disadvantage" in out and "poisoned" in out
    _kairos_on_turn(camp)
    code, out = run(capsys, "attack", "kairos", "frog-1", "fire bolt", "--adv", "--seed", "7")
    assert code == 0, out
    assert "advantage" in out and "the GM's ruling" in out


def test_the_condition_command_reports_what_it_did(camp, capsys):
    begin(capsys, "--roll-mode", "auto")
    code, out = run(capsys, "condition", "kairos", "add", "poisoned")
    assert code == 0 and out == (
        "Kairos: poisoned added. "
        "poisoned: disadvantage on attack rolls and ability checks")
    code, out = run(capsys, "condition", "kairos", "add", "exhaustion 3")
    assert "exhaustion 3 added" in out
    assert "disadvantage on attack rolls and saving throws" in out
    code, out = run(capsys, "status")
    assert "exhaustion 3" in out
    code, out = run(capsys, "check", "kairos", "perception", "--dc", "10", "--seed", "4")
    assert code == 0 and "Kairos Perception check" in out and "disadvantage" in out
    # ...and the same check with the GM's ruling on it.
    code, out = run(capsys, "check", "kairos", "perception", "--dc", "10", "--adv",
                    "--seed", "4")
    assert code == 0, out
    assert "the GM's ruling" in out and "poisoned" not in out


def test_the_exhaustion_level_can_be_set_with_a_flag(camp, capsys):
    begin(capsys, "--roll-mode", "auto")
    code, out = run(capsys, "condition", "kairos", "add", "exhaustion", "--level", "2")
    assert "exhaustion 2 added" in out and "speed halved" in out
    saved = json.loads((camp / "combat" / "encounter.json").read_text(encoding="utf-8"))
    assert saved["tokens"]["kairos"]["extra"]["exhaustion_level"] == 2
    assert saved["tokens"]["kairos"]["conditions"] == ["exhaustion"]


# ─── the breakdown behind a chance ────────────────────────────────────────────

def test_hit_chance_carries_the_numbers_it_was_made_from():
    """A display says why a percentage is what it is from these fields alone."""
    prone = with_("prone", side="enemy", ac=11)
    bolt = {"name": "Fire Bolt", "bonus": 5, "damage": [{"dice": "1d10"}]}
    covered = AttackContext(distance=5, melee=True, cover=2)
    hc = RULES.hit_chance(kairos(), prone, bolt, covered)
    # AC 11 + 2 cover - 5 bonus: an 8 hits. 13/20 = 65%; advantage vs prone: 88%.
    assert (hc["bonus"], hc["ac"], hc["cover"], hc["need"]) == (5, 11, 2, 8)
    assert hc["percent"] == 88 and hc["advantage"] == "advantage" and hc["reasons"]


def test_save_chance_carries_the_dc_bonus_cover_and_reasons():
    t = with_("restrained")
    sc = RULES.save_chance(t, "dex", 14, cover=2)
    assert sc["dc"] == 14 and sc["cover"] == 2 and sc["bonus"] == 2 + 2
    assert sc["advantage"] == "disadvantage" and sc["reasons"]
    assert RULES.save_chance(t, "str", 14, cover=2)["cover"] == 0     # cover only helps Dex saves
    auto = RULES.save_chance(with_("stunned"), "dex", 15)
    assert auto["percent_fail"] == 100 and auto["reasons"] and auto["need"] is None
