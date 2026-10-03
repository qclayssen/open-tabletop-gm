"""Shared builders for the tactics tests (not a test module itself).

Dice are scripted: ScriptedDice hands out exact faces, so every expected value
in the tests can be checked by hand. Kairos is built from the numbers on
tests/fixtures/Kairos_Level1.md; the giant frog comes from the real SRD record
in tests/fixtures/srd_monsters_sample.json run through build_srd.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tactics import engine, grid, rules, state          # noqa: E402,F401
from tactics.roller import Roller                      # noqa: E402
from tactics.state import Encounter, Token             # noqa: E402

RULES = rules.load("dnd5e")
_tr = sys.modules[type(RULES).__module__]
token_from_monster = _tr.token_from_monster


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_build = _load("build_srd_for_tactics", ROOT / "systems" / "dnd5e" / "build_srd.py")
_RAW = {r["index"]: r for r in json.loads(
    (ROOT / "tests" / "fixtures" / "srd_monsters_sample.json").read_text(encoding="utf-8"))}


class ScriptedDice:
    """Stands in for random.Random: randint returns the next scripted face."""

    def __init__(self, *faces):
        self.faces = list(faces)

    def randint(self, lo, hi):
        assert self.faces, "test ran out of scripted dice"
        v = self.faces.pop(0)
        assert lo <= v <= hi, f"scripted face {v} not in {lo}..{hi}"
        return v


def roller(*faces, supplied=None, source="verbal") -> Roller:
    return Roller(rng=ScriptedDice(*faces), supplied=list(supplied or []), supplied_source=source)


def frog(tid="frog-1", pos=(1, 0), name=None) -> Token:
    rec = _build._norm_monster(_RAW["giant-frog"])
    return token_from_monster(rec, tid, name or tid.replace("-", " ").title(), pos)


def goblin(tid="goblin-1", pos=(1, 0)) -> Token:
    return token_from_monster(_build._norm_monster(_RAW["goblin"]), tid, "Goblin", pos)


def kairos(pos=(0, 0), hp=8, controller="player") -> Token:
    """Kairos_Level1.md: wizard 1, AC 12, HP 8, DEX +2, INT save +6, WIS save +2."""
    return Token(
        id="kairos", name="Kairos", side="pc", x=pos[0], y=pos[1], hp=hp, max_hp=8, ac=12,
        speed=30, dex_mod=2, controller=controller,
        saves={"str": -1, "dex": 2, "con": 2, "int": 6, "wis": 2, "cha": -1},
        attacks=[
            {"name": "Fire Bolt", "type": "ranged", "source": "spell", "bonus": 6,
             "range": [120, 120], "damage": [{"dice": "1d10", "type": "fire"}], "flags": []},
            {"name": "Dagger", "type": "melee_or_ranged", "source": "weapon", "bonus": 4,
             "reach": 5, "range": [20, 60], "damage": [{"dice": "1d4+2", "type": "piercing"}],
             "flags": []},
        ],
        source={"kind": "sheet", "ref": "Kairos"},
    )


def open_map(w=8, h=8, diagonals="5") -> dict:
    return {"name": "test", "rows": ["." * w] * h, "diagonals": diagonals}


def encounter(tokens, rows=None, roll_mode="players", diagonals="5") -> Encounter:
    g = {"name": "test", "rows": rows, "diagonals": diagonals} if rows else open_map(diagonals=diagonals)
    return Encounter(campaign="test", grid=g, tokens={t.id: t for t in tokens}, roll_mode=roll_mode)


def start(enc, order):
    """Skip initiative: fix the order and start the first turn."""
    enc.order = list(order)
    enc.round, enc.turn_index = 1, 0
    engine._start_turn(enc, roller())
    return enc


# ─── spells (milestone 4) ─────────────────────────────────────────────────────

_SPELLS = {r["index"]: _build._norm_spell(r) for r in json.loads(
    (ROOT / "tests" / "fixtures" / "srd_spells_sample.json").read_text(encoding="utf-8"))}
spells_rules = _tr._spells_module()


def srd_spell(key: str):
    """Stands in for tactics_spells._srd: the real SRD records in the fixture."""
    r = _SPELLS.get(key.replace(" ", "-").replace("/", "-"))
    return dict(r["mechanics"], name=r["name"], level=r["level"]) if r else None


# The production `_srd`, captured at import, BEFORE anything here patches it.
# `tests/conftest.py` restores this for a test that asks for `production_srd`,
# and `tests/test_srd_fixture_isolation.py` asserts against it by identity --
# which is what distinguishes "the conftest put the real one back" from "the
# conftest put back a copy of the patch".
PRODUCTION_SRD = spells_rules._srd

# NOTE: `_srd` used to be replaced here, at import time, by a bare assignment:
#
#     spells_rules._srd = srd_spell
#
# with no teardown. The patch therefore outlived whichever module happened to
# import this file first, and every later test in the process -- including the ones
# that mean to exercise the production SRD lookup -- ran against the fixture
# whether or not it asked for it. Which lookup a test got was decided by
# collection order, which is the order-dependence `agents/dev/verifier.md` names
# as this repo's standing defect class.
#
# The patch now has a test's lifetime, not the process's: the autouse
# `fixture_srd` fixture in tests/conftest.py installs it and restores the
# production function on teardown. A test that wants the real lookup asks for
# `production_srd`, which is the only supported way to get it.
#
# `spells_rules` is still exported: tests need the module object to patch it.

KAIROS_SPELLS = ["Fire Bolt", "Mind Sliver", "Minor Illusion", "Silvery Barbs", "Shield",
                 "Mage Armor", "Magic Missile", "Detect Magic"]


def caster(pos=(0, 0), hp=8, controller="player", spells=None, slots=2) -> Token:
    """Kairos with his spellcasting: DC 14, +6, level 1, two level 1 slots."""
    k = kairos(pos=pos, hp=hp, controller=controller)
    k.extra.update(spells=list(spells or KAIROS_SPELLS), spell_dc=14, spell_attack=6, level=1,
                   slots={"1": {"total": slots, "used": 0}}, passive_perception=12,
                   skills={"stealth": 4, "athletics": -1, "acrobatics": 4},
                   abilities={"str": 8, "dex": 14, "con": 14, "int": 19, "wis": 10, "cha": 8})
    return k


def monster(index, tid, pos, name=None) -> Token:
    return token_from_monster(_build._norm_monster(_RAW[index]), tid, name or tid.replace("-", " ").title(), pos)
