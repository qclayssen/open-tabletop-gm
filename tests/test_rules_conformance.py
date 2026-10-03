"""The `Rules` contract, proved against every system that loads, plus a toy one.

Issue #195, from SPEC-combat-phases-6-7.md 7.1-7.2. The interface and the docs already
exist; what was missing was proof that the engine is system-neutral, and a conformance
suite proves that more cheaply than a second full engine.

THE THREE THINGS THIS ASSERTS
=============================

  1. Callable contract. Every method the docstring in `tactics/rules.py` promises
     exists on every `Rules` implementation that loads, and is callable. Read from the
     base class rather than from a hand-kept list, so a method added to the interface
     is covered the day it is added.

  2. Purity and bounds. `can_act`, `can_react`, `condition_modifiers`, `ac`,
     `condition_notes`, `speed`, `reach`, `crawling`, `turn_budget` and
     `lasting_conditions` do not mutate a token; `condition_modifiers` always returns
     the same keys; `damage` never reports a negative change and clamps at 0.

  3. Preview equals deterministic resolution. The odds a preview promises are the odds
     the roll that follows it actually had, enumerated over every d20 face. This is the
     invariant the `rules.py` docstring promises ("Odds travel with the roll, not beside
     it") and the one a single system can never falsify, because it agrees with itself.
     Two systems can.

WHAT IS NOT HERE
================

Encounter design (`encounter_budget`, `rate_encounter`, `adventuring_day`, `award_xp`).
Those are 2014 tables and `Rules` already answers NotImplementedError, which is the
honest answer rather than a stub's number. A toy ruleset has no day concept, so asking
it for one would test nothing about the interface.

See `tests/fake_rules.py` for the toy system and SYSTEM-PORTING.md for the coupling it
found and the checklist it adds.
"""
from __future__ import annotations

import copy
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT / "scripts", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tactics import engine, rules as rules_mod                # noqa: E402
from tactics.rules import AttackContext, Rules                # noqa: E402
from tactics.state import Encounter, Token                    # noqa: E402
from tests import fake_rules                                  # noqa: E402
from tactics.roller import Roller                             # noqa: E402

#: Every `systems/*/tactics_rules.py` that loads, plus the toy. Parametrised off the
#: directory, so a new system in `systems/` is covered without editing this file.
SYSTEMS = sorted(p.name for p in (ROOT / "systems").iterdir()
                 if (p / "tactics_rules.py").is_file()) + [fake_rules.SYSTEM]

#: Read off the base class, not a hand-kept list: a method added to the interface is
#: covered the day it is added rather than the day somebody remembers this file.
CONTRACT = sorted(n for n, v in vars(Rules).items()
                  if not n.startswith("_") and callable(v))


def rules_for(system: str) -> Rules:
    if system == fake_rules.SYSTEM:
        fake_rules.install()
        return fake_rules.RULES
    return rules_mod.load(system)


def token(tid="t", name="T", side="pc", pos=(2, 2), hp=10, controller="player",
          conditions=()) -> Token:
    return Token(
        id=tid, name=name, side=side, x=pos[0], y=pos[1], hp=hp, max_hp=10, ac=12,
        speed=30, dex_mod=2, controller=controller, conditions=list(conditions),
        attacks=[{"name": "Club", "type": "melee", "source": "weapon", "bonus": 4,
                  "reach": 5, "damage": [{"dice": "1d6", "type": "blunt"}], "flags": []}],
        source={"kind": "fixture", "ref": tid})


def board():
    return {"width": 10, "height": 8, "rows": ["." * 10] * 8, "legend": {".": "floor"},
            "terrain_types": {"floor": {"cost": 5, "blocks_sight": False}},
            "diagonals": "5"}


def encounter(system: str, roll_mode="engine") -> Encounter:
    enc = Encounter(campaign="conformance", grid=board(),
                    tokens={"hero": token("hero", "Hero", "pc", (0, 0)),
                            "foe": token("foe", "Foe", "enemy", (1, 0), 10, "gm")},
                    roll_mode=roll_mode, system=system)
    enc.order, enc.round, enc.turn_index = ["hero", "foe"], 1, 0
    enc.status = "active"
    return enc


def ctx() -> AttackContext:
    return AttackContext(distance=5, melee=True)


class AnyFace:
    """Every roll comes up as `face`, clamped into whatever range was asked for.

    Enumerating a d20 means the damage roll that follows it cannot also be a d20, and
    `ScriptedDice` (rightly) refuses a face outside the range it was asked for. The
    clamping is what lets one number mean "the attack rolled this" without saying
    anything about the dice after it.
    """

    def __init__(self, face):
        self.face = face

    def randint(self, lo, hi):
        return max(lo, min(hi, self.face))


class Cycling:
    """A scripted die that repeats its faces instead of running out of them.

    For the smoke test, where the point is that the fight RUNS and not what any one
    roll was. A roller that raises halfway through would fail on arithmetic rather
    than on coupling.
    """

    def __init__(self, faces):
        self.faces = list(faces)

    def randint(self, lo, hi):
        assert self.faces, "the roller has no faces left"
        return max(lo, min(hi, self.faces.pop(0)))


# ── 1. the callable contract ─────────────────────────────────────────────────

@pytest.mark.parametrize("system", SYSTEMS)
def test_every_method_the_interface_promises_exists_and_is_callable(system):
    R = rules_for(system)
    missing = [n for n in CONTRACT if not callable(getattr(R, n, None))]
    assert not missing, f"{system} does not implement {missing}"


@pytest.mark.parametrize("system", SYSTEMS)
def test_the_contract_is_not_larger_than_the_interface(system):
    """A subclass that answers something the interface never asked for is not a port,
    it is a fork. Extra public methods have to exist on the base class too."""
    R = rules_for(system)
    extra = [n for n in vars(R)
             if not n.startswith("_") and callable(getattr(R, n))
             and not hasattr(Rules, n)]
    assert not extra, f"{system} adds methods the interface does not have: {extra}"


def test_the_contract_is_not_empty():
    """A generator that silently found nothing would make the three tests above pass
    by asserting nothing."""
    assert len(CONTRACT) >= 25, CONTRACT
    for name in ("attack", "saving_throw", "damage", "speed", "can_act",
                 "turn_budget", "condition_modifiers", "heal", "token_from_sheet"):
        assert name in CONTRACT, name


# ── 2. purity, keys and bounds ────────────────────────────────────────────────

@pytest.mark.parametrize("system", SYSTEMS)
def test_condition_modifiers_always_returns_the_same_keys(system):
    """The 5e fix guarantees this (`Rules.condition_modifiers` says so); a second
    system has to as well, because the engine reads every key on every roll and a
    missing one is a KeyError in the middle of a fight."""
    R = rules_for(system)
    want = set(R.condition_modifiers(token()))
    for t in (token(), token(conditions=("prone", "poisoned")), token(hp=0)):
        assert set(R.condition_modifiers(t)) == want, f"{system} changed its keys"


@pytest.mark.parametrize("system", SYSTEMS)
def test_the_read_only_questions_do_not_mutate_the_creature(system):
    R = rules_for(system)
    t = token()
    before = copy.deepcopy(t)
    for call in (lambda: R.can_act(t), lambda: R.can_react(t),
                 lambda: R.condition_modifiers(t), lambda: R.ac(t),
                 lambda: R.condition_notes(t), lambda: R.speed(t),
                 lambda: R.reach(t), lambda: R.crawling(t),
                 lambda: R.turn_budget(t), lambda: R.lasting_conditions(t),
                 lambda: R.passive_perception(t)):
        call()
    assert t == before, f"{system} changed the token while answering a question"


@pytest.mark.parametrize("system", SYSTEMS)
def test_damage_never_reports_a_negative_change(system):
    """A contract invariant, not a 5e one: the engine shows hit points to a player
    without checking the arithmetic first."""
    R = rules_for(system)
    t = token(hp=10)
    out = R.damage(t, [{"amount": 4, "type": "blunt"}])
    assert t.hp >= 0, f"{system} left the token at {t.hp} HP"
    assert out["text"], f"{system} returned no text"
    # The contract fixes `text`, not the damage keys: 5e answers `hp_after`/`total`
    # and the toy answers `hp`/`dealt`. Whichever names a change, it must not be
    # negative.
    for key in ("dealt", "total", "damage"):
        if key in out:
            assert out[key] >= 0, f"{system} reported a change of {out[key]}"


@pytest.mark.parametrize("system", SYSTEMS)
def test_a_damage_beyond_the_remaining_hit_points_clamps(system):
    t = token(hp=3)
    rules_for(system).damage(t, [{"amount": 99, "type": "blunt"}])
    assert t.hp == 0, f"{system} left {t.hp} HP after a 99-point hit on 3 HP"


@pytest.mark.parametrize("system", SYSTEMS)
def test_healing_never_exceeds_the_maximum(system):
    t = token(hp=4)
    rules_for(system).heal(t, 99)
    assert t.hp == 10, f"{system} healed past the maximum"


# ── 3. preview equals deterministic resolution ────────────────────────────────

@pytest.mark.parametrize("system", SYSTEMS)
def test_the_odds_on_the_roll_are_the_odds_the_preview_promised(system):
    """Enumerated over every face, because a ruleset can agree on average and disagree
    on individual rolls, which is the case that matters to the player about to commit."""
    R = rules_for(system)
    attacker, target = token(), token("foe", "Foe", "enemy", (3, 3))
    atk = attacker.attacks[0]
    promised = R.hit_chance(attacker, target, atk, ctx())["percent"]
    hits = 0
    for face in range(1, 21):
        t = token("foe", "Foe", "enemy", (3, 3))
        # One attack roll plus one damage roll, both scripted: the ruleset is allowed
        # to spend dice however it likes, and a fixed count is what makes the twenty
        # iterations comparable.
        # Eight faces: a ruleset may roll the attack, then damage, then a reaction.
        # A fixed count per iteration is what makes the twenty comparable.
        r = Roller(rng=AnyFace(face))
        hits += 1 if R.attack(attacker, t, atk, ctx(), r, player=False)["hit"] else 0
    assert promised == round(hits / 20 * 100), (
        f"{system} promised {promised}% and hit {round(hits / 20 * 100)}% of the same faces")


@pytest.mark.parametrize("system", SYSTEMS)
def test_a_saving_throw_returns_a_text_a_roll_and_a_verdict(system):
    out = rules_for(system).saving_throw(token(), "dex", 12,
                                        Roller(rng=AnyFace(15)), player=False)
    assert out["text"], f"{system} returned no text"
    assert isinstance(out["success"], bool)
    assert out.get("natural") == 15, f"{system} did not record the die: {sorted(out)}"


@pytest.mark.parametrize("system", SYSTEMS)
def test_an_attack_returns_a_text_a_roll_and_the_damage_it_dealt(system):
    R = rules_for(system)
    target = token("foe", "Foe", "enemy", (3, 3))
    out = R.attack(token(), target, token().attacks[0], ctx(),
                   Roller(rng=AnyFace(20)), player=False)
    assert out["text"], f"{system} returned no text"
    assert out.get("natural") == 20, f"{system} did not record the die: {sorted(out)}"
    assert out["damage"], f"{system} reported a hit and no damage: {out}"
    assert out["total"] >= 0, f"{system} reported a total of {out['total']}"


@pytest.mark.parametrize("system", SYSTEMS)
def test_a_hit_carries_the_damage_result_the_engine_hands_on(system):
    """`engine._resolve_attack` passes `res["damage"]` straight to `fx.after_damage`,
    which reads `concentration_dc` off it. So `attack` has to return the dict `damage`
    returned, not the rolled parts it rolled them from.

    This is the suite's first finding, and it was found by the toy system rather than by
    reading: a first FakeRules returned the parts without applying them, which is the
    literal reading of `Rules.damage`'s "parts: already rolled", and the whole fight ran
    and dealt no damage. SYSTEM-PORTING.md said "applies damage on a hit"; the interface
    docstring did not.
    """
    R = rules_for(system)
    out = R.attack(token(), token("foe", "Foe", "enemy", (3, 3)), token().attacks[0], ctx(),
                   Roller(rng=AnyFace(20)), player=False)
    assert isinstance(out["damage"], dict), (
        f"{system} returned {type(out['damage']).__name__} from attack(); "
        f"fx.after_damage needs the dict damage() returned")
    miss = R.attack(token(), token("foe", "Foe", "enemy", (3, 3)), token().attacks[0], ctx(),
                    Roller(rng=AnyFace(1)), player=False)
    assert not miss["damage"], f"{system} rolled damage for a natural 1: {miss['damage']}"


@pytest.mark.parametrize("system", SYSTEMS)
def test_a_creature_at_zero_hit_points_leaves_the_fight(system):
    """The engine ends a fight by asking whether any hostile is still `active`, and
    `Token.active` is `not dead`. Nothing in the interface said who sets `dead`.

    The toy system first wrote `stable = True` here, which is 5e's word for 5e's ritual
    rather than for the board state the engine actually reads, and the fight rolled on
    forever. That is the whole class of leak this suite exists to catch: a second system
    confidently importing a first system's vocabulary.
    """
    R = rules_for(system)
    t = token("foe", "Foe", "enemy", (3, 3), hp=3)
    R.damage(t, [{"amount": 99, "type": "blunt"}])
    assert t.hp == 0 and not t.active, (
        f"{system} left a 0 HP creature on the board (dead={t.dead}, stable={t.stable})")


@pytest.mark.parametrize("system", SYSTEMS)
def test_scripted_dice_make_the_same_call_give_the_same_answer(system):
    """A ruleset whose result depends on anything but its inputs cannot be replayed,
    and a roll receipt is exactly a promise to replay."""
    R = rules_for(system)
    answers = set()
    for _ in range(3):
        t = token("foe", "Foe", "enemy", (3, 3))
        answers.add(R.attack(token(), t, token().attacks[0], ctx(),
                             Roller(rng=AnyFace(13)), player=False)["text"])
    assert len(answers) == 1, f"{system} is not deterministic: {answers}"


@pytest.mark.parametrize("system", SYSTEMS)
def test_a_sheet_round_trips_through_write_back(system, tmp_path):
    R = rules_for(system)
    path = tmp_path / "sheet.md"
    path.write_text("# Hero\n**HP:** 10 / 10\n", encoding="utf-8")
    built = R.token_from_sheet(path, "hero", (0, 0))
    built.hp = 4
    out = R.write_back(path.read_text(encoding="utf-8"), built)
    assert built.name, f"{system} built a nameless token"
    lines = [ln for ln in out.splitlines() if ln.startswith("**HP:**")]
    assert lines, f"{system} wrote nothing back: {out!r}"
    assert "4" in lines[0], f"{system} did not write the hit points back: {lines[0]}"


# ── the smoke test: a whole fight, on a system that is not 5e ────────────────

FACES = (20, 18, 20, 4, 12, 11, 20, 4, 15, 12, 13, 3, 9, 6, 17, 5, 14, 2, 11, 8,
         7, 10, 16, 3, 12, 9, 8, 15, 6, 11, 4, 13, 10, 5, 17, 9, 12, 2, 14, 7, 11, 6, 13,
         4, 16, 8, 10, 5, 15, 3, 12, 9, 7)


def test_a_whole_fight_runs_on_the_toy_system():
    """start, move, attack, end-turn, end: the five things the engine does that a port
    has to work on the first day.

    `roll_mode="engine"` and scripted faces throughout, so every roll the engine makes
    is a real roll rather than a supplied one. A fake that only ever rolled the player's
    dice would not be running a fight.
    """
    system = fake_rules.install()
    enc = encounter(system)
    r = Roller(rng=Cycling(FACES))

    started = engine._start_turn(enc, r)
    assert started["text"].startswith("Hero's turn"), started["text"]

    moved = engine.move(enc, r, "hero", "A2")
    assert moved["text"], "the move reported nothing"
    assert enc.tokens["hero"].pos == (0, 1), enc.tokens["hero"].pos

    # The hero is the current token, so this is the order a fight actually runs in.
    hit = engine.attack(enc, r, "hero", "foe", "Club")
    assert hit["text"], hit
    assert enc.turn.action_used is True, "the attack did not spend the action"
    dropped = enc.tokens["foe"].max_hp - enc.tokens["foe"].hp
    assert dropped > 0, "the hit dealt nothing"
    # 1d6, no crit rule, applied once. A ruleset that applied the damage itself AND
    # returned the parts would drop twelve here, which is the double-count a port is
    # most likely to ship and the one a fight would never show the GM.
    assert dropped <= 6, f"the damage was applied more than once ({dropped} HP gone)"

    ended = engine.end_turn(enc, r)
    assert "Foe's turn" in ended["text"], ended["text"]

    # `end`: the fight stops when nobody hostile is left standing, and only the ruleset
    # knows how a creature stops standing. The kill goes through `damage` rather than by
    # setting a hit point total, so what the engine reads afterwards is what a real
    # ruleset wrote and not something the test asserted into existence.
    fake_rules.RULES.damage(enc.tokens["foe"], [{"amount": 99, "type": "blunt"}])
    assert not enc.tokens["foe"].active, "the ruleset did not take the foe off the board"
    last = engine.end_turn(enc, r)
    assert "All enemies are down." in last["text"], last["text"]


def test_a_fight_on_a_system_with_no_spell_slots_books_no_slots(monkeypatch):
    """`scripts/tactics/slots.py` is 382 lines of 5e spell-slot bookkeeping, and
    `effects.py`, `spells.py`, `actions.py` and `rest.py` all import it. Whether a
    system without slots can run a fight without tripping over it is a question a static
    read cannot answer, and the answer is worth pinning.

    So every slot entry point is poisoned and the fight is run again. Nothing may call
    one. `known_spells` and `spell` are the interface's own answers to "does this system
    have slots", and if the engine reaches past them to `slots.py` this test says so.
    """
    from tactics import slots

    called = []
    for name in ("read", "write", "left_levels", "remaining", "check", "spend",
                 "lowest_with", "restore_all", "cast_class", "cast_level",
                 "long_rest", "short_rest"):
        monkeypatch.setattr(slots, name,
                            (lambda n: lambda *a, **k: called.append(n))(name))

    system = fake_rules.install()
    enc = encounter(system)
    r = Roller(rng=Cycling(FACES))
    engine._start_turn(enc, r)
    engine.move(enc, r, "hero", "A2")
    engine.attack(enc, r, "hero", "foe", "Club")
    engine.end_turn(enc, r)
    assert not called, f"the engine booked spell slots on a system that has none: {called}"


def test_the_engine_never_reaches_around_the_rules_object():
    """The architectural claim in `rules.py`, checked rather than read.

    Read with `ast`, not with `in`: `rules.py` and `spells.py` both NAME a 5e module
    in their docstrings, and `rules.py` builds `systems/<system>/tactics_rules.py` by
    path on purpose. A grep would have reported all three as coupling and taught the
    next reader to ignore this test.

    Both halves of an import matter. `from systems.dnd5e import tactics_rules` has a
    5e module in the *module* field and a 5e file in the *name*, and a mutation that
    wrote exactly that import went green the first time this was run.
    """
    import ast
    leaked = []
    for path in sorted((ROOT / "scripts" / "tactics").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            else:
                continue
            for name in names:
                if any(n in name for n in ("tactics_spells", "tactics_rules",
                                           "tactics_sheet", "spell_slots")):
                    leaked.append(f"{path.name}:{node.lineno} {name}")
    assert not leaked, f"scripts/tactics/ imports a 5e module: {leaked}"


def test_the_toy_system_really_is_not_5e():
    """A guard on the guard. If FakeRules ever grew 5e's rules the coupling it reports
    would be worthless, and this says so where it would be noticed."""
    R = fake_rules.RULES
    assert R.name == fake_rules.SYSTEM and R.name != "dnd5e"
    assert R.condition_notes(token()) == []
    assert R.known_spells(token()) == []


def test_the_toy_system_declines_the_tables_it_does_not_have():
    """NotImplementedError is the honest answer, and `rules.py` documents it as one. A
    toy ruleset that invented an encounter budget would be a second way to have a wrong
    number in the code."""
    R = fake_rules.RULES
    for call in (lambda: R.encounter_budget([1]),
                 lambda: R.rate_encounter([["Foe", 1]], [1]),
                 lambda: R.adventuring_day([1]),
                 lambda: R.award_xp("x", 1)):
        with pytest.raises(NotImplementedError):
            call()


#: The two methods the interface gives a DEFAULT rather than a stub: a ruleset with no
#: damage types has no multiplier and a ruleset with no magic knows no spells. They are
#: the only two, and the pair is asserted so that a third silent default cannot be
#: added without this test saying so.
DEFAULTED = {"damage_multiplier", "known_spells"}


def test_only_two_methods_have_a_default_instead_of_a_stub():
    defaulted = set(CONTRACT) - {n for n in CONTRACT if _raises(getattr(Rules, n))}
    assert defaulted == DEFAULTED, (
        f"the interface now defaults {defaulted}; each one needs a reason here")


def _raises(method) -> bool:
    import inspect
    params = inspect.signature(method).parameters
    try:
        method(Rules(), *([None] * (len(params) - 1)))
    except NotImplementedError:
        return True
    except Exception:
        return False
    return False


@pytest.mark.parametrize("name", sorted(set(CONTRACT) - DEFAULTED))
def test_the_interface_is_abstract_where_it_says_it_is(name):
    """A `Rules` that answers a stub with a number is how a wrong number gets shipped.
    The base class must raise, so a half-implemented port fails loudly on the first
    call rather than at the table.

    The arguments come from the signature rather than a fixed count, so adding a
    parameter to the interface does not turn this into a TypeError that looks like a
    pass. Every method in the contract is covered, not a chosen few: the first
    implementation of a new method is the one that would answer it with a literal.
    """
    import inspect
    method = getattr(Rules, name)
    params = inspect.signature(method).parameters
    args = [None] * (len(params) - 1)          # parameters includes `self`
    with pytest.raises(NotImplementedError):
        method(Rules(), *args)


def test_no_spelling_uses_an_em_dash():
    assert "\u2014" not in pathlib.Path(__file__).read_text(encoding="utf-8")