"""Monster records carry structured actions for the tactical combat engine.

WHY
===
The combat engine picks and resolves enemy actions itself, so it needs attack
bonus, reach, range, damage dice and save DCs as data, not prose. build_srd.py
now adds an `actions` list to each monster record.

Two promises are pinned here:

1. Nothing the GM already sees changes. The golden file was generated from the
   normaliser BEFORE `actions` existed; every other field and the formatted
   lookup text must still match it byte for byte.
2. Nothing is guessed. An action that does not fit a known shape keeps its raw
   text and says why in `flags`. An action with no flags has no `raw`.

Fixtures are real upstream 5e-bits records (SRD 5.1, OGL), so the parser is
tested against the shapes it will actually meet.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
DND5E = ROOT / "systems" / "dnd5e"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_srd = _load("build_srd", DND5E / "build_srd.py")
sys.path.insert(0, str(DND5E))
lookup = _load("lookup_under_test", DND5E / "lookup.py")

RAW = json.loads((FIXTURES / "srd_monsters_sample.json").read_text(encoding="utf-8"))
GOLDEN = json.loads((FIXTURES / "srd_monsters_golden.json").read_text(encoding="utf-8"))
BY_INDEX = {r["index"]: r for r in RAW}


def _actions(index: str) -> dict:
    return {a["name"]: a for a in build_srd._norm_monster(BY_INDEX[index])["actions"]}


class ExistingOutputUnchanged(unittest.TestCase):
    def test_golden_covers_every_sample(self):
        self.assertEqual(set(GOLDEN), set(BY_INDEX))

    def test_every_other_field_is_unchanged(self):
        for index, raw in BY_INDEX.items():
            rec = build_srd._norm_monster(raw)
            for key in ("actions", "saves", "skills", "passive_perception"):   # engine-only fields
                rec.pop(key, None)
            self.assertEqual(rec, GOLDEN[index]["record"], index)

    def test_formatted_lookup_text_is_unchanged(self):
        for index, raw in BY_INDEX.items():
            self.assertEqual(lookup._fmt_monster(build_srd._norm_monster(raw)),
                             GOLDEN[index]["text"], index)


class StructuredActions(unittest.TestCase):
    def test_plain_melee_attack(self):
        scim = _actions("goblin")["Scimitar"]
        self.assertEqual(scim["kind"], "attack")
        self.assertEqual(scim["attack"], {"type": "melee", "source": "weapon",
                                          "bonus": 4, "reach": 5})
        self.assertEqual(scim["damage"], [{"dice": "1d6+2", "type": "slashing"}])
        self.assertEqual(scim["flags"], [])
        self.assertNotIn("raw", scim)

    def test_ranged_attack_has_normal_and_long_range(self):
        bow = _actions("goblin")["Shortbow"]
        self.assertEqual(bow["attack"]["range"], [80, 320])
        self.assertNotIn("reach", bow["attack"])

    def test_rider_is_kept_raw_and_flagged(self):
        bite = _actions("giant-frog")["Bite"]
        self.assertEqual(bite["attack"]["bonus"], 3)
        self.assertEqual(bite["damage"], [{"dice": "1d6+1", "type": "piercing"}])
        self.assertIn("rider", bite["flags"])
        self.assertIn("grappled (escape DC 11)", bite["rider"])
        self.assertIn("raw", bite)

    def test_prose_only_action_is_unparsed(self):
        swallow = _actions("giant-frog")["Swallow"]
        self.assertEqual(swallow["kind"], "other")
        self.assertEqual(swallow["flags"], ["unparsed"])
        self.assertTrue(swallow["raw"].startswith("The frog makes one bite attack"))

    def test_multiattack_lists_its_parts(self):
        multi = _actions("adult-red-dragon")["Multiattack"]
        self.assertEqual(multi["kind"], "multiattack")
        self.assertEqual(multi["multiattack"], [[
            {"action": "Frightful Presence", "count": 1, "type": "ability"},
            {"action": "Bite", "count": 1, "type": "melee"},
            {"action": "Claw", "count": 2, "type": "melee"},
        ]])

    def test_multiattack_with_alternatives_keeps_each_option(self):
        multi = _actions("bandit-captain")["Multiattack"]
        self.assertEqual(len(multi["multiattack"]), 2)
        self.assertEqual(multi["multiattack"][1],
                         [{"action": "Dagger", "count": 2, "type": "ranged"}])

    def test_breath_weapon_has_dc_and_area(self):
        breath = build_srd._norm_monster_action({
            "name": "Fire Breath",
            "desc": ("The dragon exhales fire in a 60-foot line that is 5 feet wide. Each "
                     "creature in that line must make a DC 18 Dexterity saving throw, taking "
                     "45 (13d6) fire damage on a failed save, or half as much damage on a "
                     "successful one."),
            "usage": {"type": "recharge on roll", "dice": "1d6", "min_value": 5},
            "dc": {"dc_type": {"index": "dex"}, "dc_value": 18, "success_type": "half"},
            "damage": [{"damage_type": {"index": "fire"}, "damage_dice": "13d6"}],
        })
        self.assertEqual(breath["kind"], "save")
        self.assertEqual(breath["dc"], {"ability": "dex", "value": 18, "on_success": "half"})
        self.assertEqual(breath["area"], {"shape": "line", "size": 60, "width": 5})
        self.assertEqual(breath["damage"], [{"dice": "13d6", "type": "fire"}])
        self.assertEqual(breath["usage"]["min_value"], 5)
        self.assertEqual(breath["flags"], [])

    def test_upstream_success_type_contradicting_its_text_is_flagged(self):
        breath = _actions("adult-red-dragon")["Fire Breath"]    # field "none", text "half"
        self.assertIsNone(breath["dc"]["on_success"])
        self.assertIn("success_conflict", breath["flags"])
        self.assertEqual(breath["area"], {"shape": "cone", "size": 60})

    def test_save_without_a_known_area_is_flagged(self):
        fear = _actions("adult-red-dragon")["Frightful Presence"]
        self.assertEqual(fear["kind"], "save")
        self.assertIn("targeting", fear["flags"])

    def test_damage_choice_is_structured_not_picked(self):
        scim = _actions("djinni")["Scimitar"]
        self.assertEqual(scim["damage"], [{"dice": "2d6+5", "type": "slashing"}])
        self.assertEqual([o["type"] for o in scim["damage_choice"]["options"]],
                         ["lightning", "thunder"])
        self.assertIn("damage_choice", scim["flags"])

    def test_conditional_attack_bonus_is_flagged(self):
        staff = _actions("druid")["Quarterstaff"]
        self.assertIn("conditional_bonus", staff["flags"])
        self.assertEqual(staff["attack"]["bonus"], 2)

    def test_flat_damage_survives(self):
        tentacles = _actions("octopus")["Tentacles"]
        self.assertEqual(tentacles["damage"], [{"dice": "1", "type": "bludgeoning"}])

    def test_non_attack_with_stray_attack_bonus_is_unparsed(self):
        ink = _actions("octopus")["Ink Cloud"]
        self.assertEqual(ink["kind"], "other")
        self.assertIn("unparsed", ink["flags"])

    def test_breath_options_are_unparsed(self):
        breath = _actions("adult-brass-dragon")["Breath Weapons"]
        self.assertIn("unparsed", breath["flags"])

    def test_raw_text_present_exactly_when_flagged(self):
        for raw in RAW:
            for a in build_srd._norm_monster(raw)["actions"]:
                self.assertEqual("raw" in a, bool(a["flags"]), (raw["index"], a["name"]))


class RiderDamage(unittest.TestCase):
    """Upstream lists a rider's damage alongside the hit damage. Applying it on
    every hit would be wrong, so it moves to `rider_damage`."""

    def test_rider_damage_is_separated(self):
        bite = build_srd._norm_monster_action({
            "name": "Bite",
            "desc": ("Melee Weapon Attack: +5 to hit, reach 5 ft., one creature. Hit: 7 "
                     "(1d8 + 3) piercing damage, and the target must make a DC 11 "
                     "Constitution saving throw, taking 9 (2d8) poison damage on a "
                     "failed save, or half as much damage on a successful one."),
            "attack_bonus": 5,
            "dc": {"dc_type": {"index": "con"}, "dc_value": 11, "success_type": "half"},
            "damage": [
                {"damage_type": {"index": "piercing"}, "damage_dice": "1d8+3"},
                {"damage_type": {"index": "poison"}, "damage_dice": "2d8"},
            ],
        })
        self.assertEqual(bite["damage"], [{"dice": "1d8+3", "type": "piercing"}])
        self.assertEqual(bite["rider_damage"], [{"dice": "2d8", "type": "poison"}])
        self.assertIn("rider", bite["flags"])

    def test_plus_damage_stays_on_hit(self):
        bite = _actions("adult-red-dragon")["Bite"]
        self.assertEqual(bite["damage"], [{"dice": "2d10+8", "type": "piercing"},
                                          {"dice": "2d6", "type": "fire"}])
        self.assertEqual(bite["flags"], [])

    def test_unparseable_count_is_flagged_not_crashed(self):
        multi = build_srd._norm_monster_action({
            "name": "Multiattack", "multiattack_type": "actions",
            "desc": "The hydra makes as many bite attacks as it has heads.",
            "actions": [{"action_name": "Bite", "count": "Number of Heads", "type": "melee"}],
        })
        self.assertEqual(multi["flags"], ["unparsed"])
        self.assertNotIn("multiattack", multi)


if __name__ == "__main__":
    unittest.main()
