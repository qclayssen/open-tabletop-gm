"""Milestone 4: spell mechanics, monster save proficiencies and structured riders.

The engine casts spells and applies common riders itself, so build_srd.py now
keeps upstream's numbers as data: casting time, range, attack or save, damage
by slot or character level, area of effect. Riders the engine can apply
(grapple with an escape DC, save or be knocked prone, save for poison damage)
are structured; everything else stays text for the GM.

Fixtures are real upstream 5e-bits records (SRD 5.1, OGL).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_srd = _load("build_srd_mechanics", ROOT / "systems" / "dnd5e" / "build_srd.py")
SPELLS = {r["index"]: build_srd._norm_spell(r)["mechanics"] for r in json.loads(
    (FIXTURES / "srd_spells_sample.json").read_text(encoding="utf-8"))}
MONSTERS = {r["index"]: build_srd._norm_monster(r) for r in json.loads(
    (FIXTURES / "srd_monsters_sample.json").read_text(encoding="utf-8"))}


def _action(monster, name):
    return next(a for a in MONSTERS[monster]["actions"] if a["name"] == name)


# ─── spells ───────────────────────────────────────────────────────────────────

def test_attack_cantrip_scales_with_character_level():
    bolt = SPELLS["fire-bolt"]
    assert bolt["casting"] == "action" and bolt["attack"] == "ranged"
    assert (bolt["range"], bolt["origin"]) == (120, "point")
    assert bolt["damage"] == {"type": "fire",
                              "character": {"1": "1d10", "5": "2d10", "11": "3d10", "17": "4d10"}}
    assert bolt["flags"] == []


def test_area_save_spell_from_self():
    hands = SPELLS["burning-hands"]
    assert hands["origin"] == "self" and hands["area"] == {"shape": "cone", "size": 15}
    assert hands["save"] == {"ability": "dex", "on_success": "half"}
    assert hands["damage"]["slot"]["1"] == "3d6" and hands["damage"]["slot"]["3"] == "5d6"


def test_line_width_comes_from_the_text():
    assert SPELLS["lightning-bolt"]["area"] == {"shape": "line", "size": 100, "width": 5}
    assert SPELLS["sunbeam"]["area"]["width"] == 5          # "5-foot-wide" phrasing
    assert "area_placement" in SPELLS["wall-of-fire"]["flags"]   # a wall, not a blast


def test_reactions_and_pure_effects_are_flagged():
    assert SPELLS["shield"]["casting"] == "reaction"
    assert "effect" in SPELLS["shield"]["flags"] and "effect" in SPELLS["mage-armor"]["flags"]
    assert SPELLS["hold-person"]["concentration"] is True
    assert SPELLS["hold-person"]["save"] == {"ability": "wis", "on_success": "none"}


def test_healing_and_touch_range():
    cure = SPELLS["cure-wounds"]
    assert (cure["range"], cure["origin"]) == (5, "touch")
    assert cure["heal"]["slot"]["1"] == "1d8+MOD"
    assert SPELLS["healing-word"]["casting"] == "bonus"


# ─── monsters ─────────────────────────────────────────────────────────────────

def test_monster_save_proficiencies_and_passive_perception():
    wyrm = MONSTERS["red-dragon-wyrmling"]
    assert wyrm["saves"] == {"dex": 2, "con": 5, "wis": 2, "cha": 4}
    assert wyrm["skills"]["perception"] == 4 and wyrm["passive_perception"] == 14
    assert MONSTERS["goblin"]["saves"] == {} and MONSTERS["goblin"]["skills"] == {"stealth": 6}


def test_grapple_rider_is_structured_and_the_rest_stays_text():
    bite = _action("giant-frog", "Bite")
    assert bite["rider_effects"] == [{"kind": "grapple", "escape_dc": 11, "restrained": True}]
    assert bite["rider_rest"] == "The frog can't bite another target."
    assert "rider" in bite["flags"]                     # the GM still sees there is a rider


def test_save_or_prone_and_save_for_poison():
    assert _action("wolf", "Bite")["rider_effects"] == [
        {"kind": "save", "ability": "str", "dc": 11, "condition": "prone"}]
    spider = _action("giant-spider", "Bite")["rider_effects"]
    assert spider == [{"kind": "save", "ability": "con", "dc": 11,
                       "damage": [{"dice": "2d8", "type": "poison"}], "on_success": "half"}]
    assert "stable but poisoned" in _action("giant-spider", "Bite")["rider_rest"]
    centipede = _action("giant-centipede", "Bite")["rider_effects"][0]
    assert centipede["on_success"] == "none" and centipede["damage"][0]["dice"] == "3d6"


def test_qualified_riders_are_never_structured():
    claws = _action("ghoul", "Claws")                   # "other than an elf or undead"
    assert "rider_effects" not in claws and "rider" in claws["flags"]


def test_breath_weapons_carry_area_usage_and_conditions():
    breath = _action("red-dragon-wyrmling", "Fire Breath")
    assert breath["kind"] == "save" and breath["flags"] == []
    assert breath["area"] == {"shape": "cone", "size": 15}
    assert breath["usage"] == {"type": "recharge on roll", "dice": "1d6", "min_value": 5}
    dust = _action("dust-mephit", "Blinding Breath")
    assert dust["rider_effects"] == [{"kind": "save", "ability": "dex", "dc": 10,
                                      "condition": "blinded", "duration": "1 minute",
                                      "repeat": "end"}]
