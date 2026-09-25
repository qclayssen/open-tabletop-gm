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
    """Kairos_Level1.md: wizard 1, AC 12, HP 8, DEX +2, INT save +5."""
    return Token(
        id="kairos", name="Kairos", side="pc", x=pos[0], y=pos[1], hp=hp, max_hp=8, ac=12,
        speed=30, dex_mod=2, controller=controller,
        saves={"str": -1, "dex": 2, "con": 2, "int": 5, "wis": 3, "cha": -1},
        attacks=[
            {"name": "Fire Bolt", "type": "ranged", "source": "spell", "bonus": 5,
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


spells_rules._srd = srd_spell

KAIROS_SPELLS = ["Fire Bolt", "Mind Sliver", "Minor Illusion", "Silvery Barbs", "Shield",
                 "Mage Armor", "Magic Missile", "Detect Magic"]


def caster(pos=(0, 0), hp=8, controller="player", spells=None, slots=2) -> Token:
    """Kairos with his spellcasting: DC 13, +5, level 1, two level 1 slots."""
    k = kairos(pos=pos, hp=hp, controller=controller)
    k.extra.update(spells=list(spells or KAIROS_SPELLS), spell_dc=13, spell_attack=5, level=1,
                   slots={"1": {"total": slots, "used": 0}}, passive_perception=11,
                   skills={"stealth": 4, "athletics": -1, "acrobatics": 2},
                   abilities={"str": 8, "dex": 15, "con": 14, "int": 17, "wis": 12, "cha": 8})
    return k


def monster(index, tid, pos, name=None) -> Token:
    return token_from_monster(_build._norm_monster(_RAW[index]), tid, name or tid.replace("-", " ").title(), pos)
