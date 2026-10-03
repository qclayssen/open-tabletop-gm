"""#136: mechanics that were being discarded must be preserved, or named.

WHY
===
Three SRD mechanics reached `build_srd.py` and left it as data the engine could
never see:

1. **Multi-component spell damage.** Upstream gives Flame Strike, Ice Storm and
   Meteor Swarm a *list* of damage components, one per type. The parser read
   `damage[0] if len(damage) == 1 else None`, so for exactly those three it
   threw the whole table away and wrote `damage_unparsed`. Flame Strike's record
   said it did no damage while its own description said "4d6 fire damage and 4d6
   radiant damage". A record whose prose and mechanics disagree is the defect:
   whoever reads the mechanics gets a spell that does nothing.

2. **`rider_damage`** (6 SRD actions). Parsed and kept on the record, then
   dropped by `tactics_rules.token_from_monster`. The token is what the engine
   reads, so an Aboleth's 1d12 acid and an Assassin's 7d6 poison were in
   `dnd5e_srd.json` and on no attack spec.

3. **`damage_choice`** (16 SRD actions). Same path, same loss. A Druid's
   Quarterstaff carried three damage options and reached the token with none.

WHAT WAS DECIDED, AND WHY
=========================
**Preserve the data; do not resolve it.** Both halves matter and the issue allows
either, so the choice is stated rather than implied:

- *Preserved* is right for all three. The numbers are correct, they came from the
  SRD, and nothing upstream is wrong. Deleting them is the defect; there is
  nothing to recover.
- *Not resolved* is right for all three, and this is the part that could have
  gone wrong. Making Flame Strike, Ice Storm and Meteor Swarm mechanical would
  move the RI5 headline (58 of 319 spells) and change how the engine resolves
  area effects. That is a rules decision needing its own validation against the
  2014 books, not a parsing one, and it belongs to #135's coverage work. So
  `resolve()` behaves exactly as before: these three still narrate, and the
  surviving numbers are now in the record instead of nowhere.

A fourth thing was verified rather than fixed, and the test says so: the roadmap
claims "a Giant Spider's poison numbers exist in the dataset and are not on the
token". **That is false.** Its 2d8 poison parses into `rider_effects`, and
`rider_effects` *was* already carried onto the token, and the engine applies it
on a failed CON 11 save. `test_the_giant_spider_poison_is_already_applied` pins
that, so the false claim cannot come back.

WHAT WOULD MAKE THESE FAIL
==========================
  - reverting `_spell_mechanics` to `damage[0] if len == 1 else None`
  - dropping `rider_damage`/`damage_choice` from `token_from_monster`
  - making `diagnostics()` return the prose paragraph instead of the short reason
  - deleting a registry row, which `test_every_flag_the_build_writes_is_registered`
    catches -- the flag and its explanation are one unit.

Fixture: `tests/fixtures/srd_discarded_mechanics.json`, real upstream 5e-bits
records (SRD 5.1, OGL v1.0) covering the poison, choice and multi-type shapes.
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

build_srd = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location("build_srd_136", DND5E / "build_srd.py"))
build_srd.__spec__.loader.exec_module(build_srd)
sys.path.insert(0, str(DND5E))
import tactics.rules as rules_mod

_RULES_MODULE = sys.modules[type(rules_mod.load("dnd5e")).__module__]
_RULES = rules_mod.load("dnd5e")

RAW = json.loads((FIXTURES / "srd_discarded_mechanics.json").read_text(encoding="utf-8"))
SPELLS = {r["index"]: r for r in RAW["spells"]}
MONSTERS = {r["index"]: r for r in RAW["monsters"]}


def _spell(index):
    return build_srd._norm_spell(SPELLS[index])["mechanics"]


def _monster(index):
    return build_srd._norm_monster(MONSTERS[index])


def _action(index, name):
    return next(a for a in _monster(index)["actions"] if a["name"] == name)


def _token_spec(index, name):
    token = _RULES_MODULE.token_from_monster(_monster(index), "t1", index, (0, 0))
    return next(a for a in token.attacks if a["name"] == name)


class MultiTypeSpellDamage(unittest.TestCase):
    """The whole damage table, not one component of it."""

    def test_flame_strike_keeps_both_damage_types(self):
        m = _spell("flame-strike")
        self.assertEqual([c["type"] for c in m["damage"]["components"]],
                         ["fire", "radiant"])
        self.assertEqual(m["damage"]["combine"], "all")
        # SRD 5.1 text: "A creature takes 4d6 fire damage and 4d6 radiant damage
        # on a failed save". Both, not one and not a choice.
        self.assertEqual(m["damage"]["components"][0]["slot"]["5"], "4d6")
        self.assertEqual(m["damage"]["components"][1]["slot"]["5"], "4d6")

    def test_the_upcast_alternative_is_kept_readable_and_unresolved(self):
        """\"the fire damage OR the radiant damage (your choice)\" stays a choice.

        2014 PHB, Flame Strike: at 6th level or higher the fire damage or the
        radiant damage (your choice) increases by 1d6. Picking either number here
        would be the parser deciding the caster's decision.
        """
        m = _spell("flame-strike")
        fire = m["damage"]["components"][0]["slot"]
        self.assertEqual(fire["5"], "4d6")
        self.assertEqual(fire["6"], "4d6 OR 5d6")      # verbatim, not "4d6OR5d6"
        self.assertIn("damage_choice_upcast", m["flags"])
        self.assertNotIn("damage_unparsed", m["flags"],
                         "an OR is understood and declined, not a parse failure")

    def test_components_that_scale_independently_are_kept_independently(self):
        """Ice Storm's bludgeoning scales; its cold does not. Both survive."""
        m = _spell("ice-storm")
        by_type = {c["type"]: c["slot"] for c in m["damage"]["components"]}
        self.assertEqual(by_type["bludgeoning"]["4"], "2d8")
        self.assertEqual(by_type["bludgeoning"]["9"], "7d8")
        self.assertEqual(by_type["cold"]["4"], "4d6")
        self.assertEqual(by_type["cold"]["9"], "4d6")   # never scales
        self.assertEqual(m["flags"], ["damage_multi"])

    def test_meteor_swarm_keeps_both_types(self):
        m = _spell("meteor-swarm")
        self.assertEqual([c["type"] for c in m["damage"]["components"]],
                         ["fire", "bludgeoning"])
        self.assertTrue(all(c["slot"] == {"9": "20d6"} for c in m["damage"]["components"]))

    def test_a_single_component_list_is_not_mislabelled_as_multi(self):
        """A one-element list is the ordinary case and keeps the ordinary shape.

        A test-local record, because no SRD spell has a one-element list: the
        shape `damage[0] if len == 1 else None` was written FOR is the shape that
        must not change.
        """
        m = build_srd._spell_mechanics({
            "name": "One Type", "casting_time": "1 action", "range": "60 feet",
            "level": 1, "damage": [{"damage_type": {"index": "fire"},
                                    "damage_at_slot_level": {"1": "3d6"}}],
        })
        self.assertEqual(m["damage"], {"type": "fire", "slot": {"1": "3d6"}})
        self.assertEqual(m["flags"], [])

    def test_an_empty_damage_list_is_still_unparsed(self):
        m = build_srd._spell_mechanics({"name": "Nothing", "casting_time": "1 action",
                                        "range": "60 feet", "level": 1, "damage": []})
        self.assertNotIn("damage", m)
        self.assertIn("damage_unparsed", m["flags"])

    def test_spiritual_weapons_caster_modifier_is_still_flagged_not_invented(self):
        """\"1d8 + your spellcasting ability modifier\" stays a flag.

        The modifier is the caster's and is only known at cast time, so the
        number cannot be completed here. It is reported, not guessed.
        """
        m = _spell("spiritual-weapon")
        self.assertEqual(m["damage"]["type"], "force")
        self.assertEqual(m["damage"]["slot"]["2"], "1d8+MOD")
        self.assertIn("damage_unparsed", m["flags"])

    def test_ordinary_spells_are_untouched(self):
        self.assertEqual(_spell("fire-bolt"),
                         {"casting": "action", "concentration": False, "range": 120,
                          "origin": "point", "attack": "ranged",
                          "damage": {"type": "fire",
                                     "character": {"1": "1d10", "5": "2d10",
                                                   "11": "3d10", "17": "4d10"}},
                          "flags": []})

    def test_the_three_multi_type_spells_still_narrate(self):
        """Behaviour is unchanged on purpose. See "WHAT WAS DECIDED".

        Making these three mechanical is a rules decision about area effects and
        would move #135's headline number. Until that is validated, `resolve`
        must keep demoting them, and this test fails if it stops doing so.

        Driven from the parsed fixture mechanics through `spellbook`, because the
        checked-in spell fixture holds six other spells and none of these three.
        That is also the realistic path: `mechanics()` writes exactly this dict.
        """
        from tests import tactics_fixtures as fx

        for name, index in (("flame strike", "flame-strike"),
                            ("ice storm", "ice-storm"),
                            ("meteor swarm", "meteor-swarm")):
            m = _spell(index)
            caster = fx.caster(spells=[name])
            caster.extra["spellbook"] = {name: dict(m, name=m and name.title())}
            spec = _RULES_MODULE._spells_module().resolve(caster, name)
            self.assertEqual(spec["mode"], "narrate", index)
            self.assertIn("damage_multi", spec["flags"], index)


class PoisonRider(unittest.TestCase):
    """`rider_damage`: damage a rider deals, which is not hit damage."""

    def test_the_assassins_poison_parses_and_reaches_the_token(self):
        a = _action("assassin", "Shortsword")
        self.assertEqual(a["damage"], [{"dice": "1d6+3", "type": "piercing"}])
        self.assertEqual(a["rider_damage"], [{"dice": "7d6", "type": "poison"}])
        self.assertEqual(_token_spec("assassin", "Shortsword")["rider_damage"],
                         [{"dice": "7d6", "type": "poison"}],
                         "the poison was in the record and off the token")

    def test_the_aboleths_periodic_acid_is_kept_and_left_to_the_gm(self):
        """1d12 acid every 10 minutes while diseased. Never hit damage.

        Putting it in `damage` would apply 1d12 acid on every tentacle hit, which
        is not what the SRD says, so it stays in `rider_damage` and the GM runs
        the ten-minute clock.
        """
        a = _action("aboleth", "Tentacle")
        self.assertEqual(a["damage"], [{"dice": "2d6+5", "type": "bludgeoning"}])
        self.assertEqual(a["rider_damage"], [{"dice": "1d12", "type": "acid"}])
        self.assertIsNone(a.get("rider_effects"), "no saving throw: this one is periodic")
        self.assertEqual(_token_spec("aboleth", "Tentacle")["rider_damage"],
                         [{"dice": "1d12", "type": "acid"}])

    def test_the_giant_spider_poison_is_already_applied(self):
        """The roadmap's claim here is FALSE and this test says so.

        ROADMAP-ideas.md (RI5) says "a Giant Spider's poison numbers exist in the
        dataset and are not on the token". They are on the token: the bite's
        rider parses into `rider_effects`, `rider_effects` is carried, and
        `engine._apply_riders` applies the 2d8 on a failed CON 11 save. Pinned so
        the claim cannot be used to justify a second, redundant fix.
        """
        a = _action("giant-spider", "Bite")
        self.assertEqual(a["rider_effects"],
                         [{"kind": "save", "ability": "con", "dc": 11,
                           "damage": [{"dice": "2d8", "type": "poison"}],
                           "on_success": "half"}])
        spec = _token_spec("giant-spider", "Bite")
        self.assertEqual(spec["rider_effects"], a["rider_effects"])
        self.assertIsNone(spec.get("rider_damage"),
                          "the spider's poison parsed, so it is a rider EFFECT")

    def test_the_spider_poison_is_actually_rolled_by_the_engine(self):
        """Not just carried: applied on a failed save, halved on a success.

        Runs the real engine path, so the false-claim pin above is about behaviour
        and not about a dict shape.
        """
        from scripts.tactics import engine as eng
        from tests import tactics_fixtures as fx

        spider = fx.monster("giant-spider", "spider-1", (3, 0))
        kairos = fx.caster()
        fx.start(fx.encounter([kairos, spider]), ["kairos", "spider-1"])
        # 20 to hit (nat 20), then 2d8 poison: 5 + 4 = 9. Kairos CON save +2.
        _RULES.attack(spider, kairos, spider.attacks[0],
                      eng.AttackContext(distance=5, melee=True), fx.roller(20, 5, 8),
                      player=False)
        self.assertLess(kairos.hp, kairos.max_hp, "the bite did no damage at all")


class DamageChoice(unittest.TestCase):
    """\"Melee Weapon Attack: ... one of the following options\" -- 16 SRD actions."""

    def test_the_djinnis_lightning_or_thunder_reaches_the_token(self):
        a = _action("djinni", "Scimitar")
        self.assertEqual(a["damage_choice"],
                         {"choose": 1, "options": [{"dice": "1d6", "type": "lightning"},
                                                   {"dice": "1d6", "type": "thunder"}]})
        self.assertEqual(a["damage"], [{"dice": "2d6+5", "type": "slashing"}],
                         "the options are EXTRA, not a replacement for the hit damage")
        self.assertEqual(_token_spec("djinni", "Scimitar")["damage_choice"],
                         a["damage_choice"])

    def test_the_druids_three_options_all_survive(self):
        a = _action("druid", "Quarterstaff")
        options = a["damage_choice"]["options"]
        self.assertEqual([o["dice"] for o in options], ["1d6", "1d8", "1d8+2"])
        spec = _token_spec("druid", "Quarterstaff")
        self.assertEqual([o["dice"] for o in spec["damage_choice"]["options"]],
                         ["1d6", "1d8", "1d8+2"])

    def test_an_action_whose_only_damage_is_a_choice_resolves_to_none(self):
        """A Salamander's spear: the options are all of its piercing damage.

        `damage` is therefore empty and `damage_unparsed` is flagged. This is
        correct: emitting the FIRST option would make the GM's choice the
        parser's, and every spear hit would be the smaller number.
        """
        a = _action("salamander", "Spear")
        self.assertNotIn("damage", a)
        self.assertEqual([o["dice"] for o in a["damage_choice"]["options"]],
                         ["2d6+4", "2d8+4"])
        self.assertIn("damage_unparsed", a["flags"])
        self.assertEqual(_token_spec("salamander", "Spear")["damage"],
                         [], "no option was silently picked")

    def test_the_choice_is_not_resolved_into_damage(self):
        """The token keeps the options and stays undecided.

        Mutant: `token_from_monster` picking `options[0]` into `damage`. That is
        the failure this whole issue is about, arriving from the other direction.
        """
        spec = _token_spec("druid", "Quarterstaff")
        self.assertEqual(spec["damage"], [])
        self.assertIn("damage_choice", spec["flags"])


class ExplicitDiagnostics(unittest.TestCase):
    """Every flag the builders write says where its data went."""

    def test_every_flag_the_build_writes_is_registered(self):
        """A flag with no registry row is an unexplained shrug.

        The flag is what a reader sees on a record; the registry row is what tells
        them whether the numbers survived. Deleting a row therefore breaks this,
        which is the point: the flag and its explanation are one unit.
        """
        for source, field in ((build_srd._spell_mechanics, "spell mechanics"),
                              (build_srd._norm_monster_action, "monster action")):
            if field == "spell mechanics":
                written = set(_spell("flame-strike")["flags"])
                written |= set(_spell("ice-storm")["flags"])
                written |= set(_spell("meteor-swarm")["flags"])
                written |= set(_spell("spiritual-weapon")["flags"])
            else:
                written = set(_action("djinni", "Scimitar")["flags"])
                written |= set(_action("aboleth", "Tentacle")["flags"])
                written |= set(_action("salamander", "Spear")["flags"])
            self.assertTrue(written, field)
            for flag in written:
                self.assertIn(flag, build_srd.PRESERVED_NOT_APPLIED, (field, flag))

    def test_every_registered_flag_has_a_state_a_reason_and_a_why(self):
        for flag, row in build_srd.PRESERVED_NOT_APPLIED.items():
            self.assertIn(row.get("state"), ("preserved", "recorded", "dropped"), flag)
            for key in ("reason", "where", "why"):
                self.assertTrue(row.get(key), f"{flag} has no {key}")

    def test_no_registry_row_is_for_a_flag_the_build_never_writes(self):
        """The reverse check, and the one that caught a real mistake.

        `rider_damage` is a KEY on an action, not a flag: the flag covering it is
        `rider`. The registry was originally keyed by flag and had a `rider_damage`
        row that `diagnostics()` could never reach, so the aboleth's periodic acid
        had no explanation while appearing to have one. A row nobody can look up is
        worse than no row, because it reads as coverage.

        Sweeps every action of every fixture monster plus every fixture spell, so
        a key mistaken for a flag cannot get a row of its own again.
        """
        written = set()
        for raw in MONSTERS.values():
            for action in build_srd._norm_monster(raw)["actions"]:
                written |= set(action.get("flags", []))
        for raw in SPELLS.values():
            written |= set(build_srd._spell_mechanics(raw)["flags"])
        for flag in build_srd.PRESERVED_NOT_APPLIED:
            self.assertIn(flag, written,
                          f"PRESERVED_NOT_APPLIED has a row for {flag!r}, which no "
                          "record writes as a flag. It is probably a KEY (rider, "
                          "rider_effects, rider_damage) documented under its own "
                          "name; put it in the row for the flag that covers it.")

    def test_the_two_dropped_flags_are_reachable_from_the_fixture(self):
        """The census is only complete if the `dropped` rows have records too.

        `targeting`, `success_conflict` and `unparsed` are the only flags whose
        numbers are genuinely gone, and they are the ones a reader most needs to
        be able to look up. So the fixture carries a record for each: an Adult Red
        Dragon (success_type contradicting its own text, and Frightful Presence
        with no parseable area) and a Giant Frog (Swallow, prose only).

        If these ever stop producing their flags the registry would be describing
        records no fixture holds, and this fails rather than leaving the claim
        resting on nothing.
        """
        for index, name, flag, state in (
                ("adult-red-dragon", "Fire Breath", "success_conflict", "recorded"),
                ("adult-red-dragon", "Frightful Presence", "targeting", "dropped"),
                ("giant-frog", "Swallow", "unparsed", "dropped")):
            action = _action(index, name)
            self.assertIn(flag, action["flags"], (index, name))
            self.assertIn("raw", action, "an unresolved flag must keep the printed text")
            self.assertTrue(any(l.startswith(f"{flag}: {state}")
                                for l in build_srd.diagnostics(action)),
                            (index, name, action["flags"]))

    def test_only_two_flags_are_dropped_and_that_is_deliberate(self):
        """Pins the census at exactly the two states that mean data is gone.

        `targeting` (49 actions) and `unparsed` (82) are the only flags whose
        numbers do not survive in any structured form; everything else is
        `recorded` or `preserved`. If a third flag ever becomes `dropped`, this
        is the test that says so and forces the decision to be made on purpose
        rather than by accident.
        """
        dropped = {f for f, row in build_srd.PRESERVED_NOT_APPLIED.items()
                   if row["state"] == "dropped"}
        self.assertEqual(dropped, {"targeting", "unparsed"})

    def test_width_assumed_is_registered_and_says_it_is_an_assumption(self):
        """The one flag with no consumer anywhere in the tree, and the reason
        #135 counts it.

        `width_assumed` is written by build_srd and read by nobody: five line
        spells print no width, so 5 ft is this parser's guess. It is harmless
        today only because every one of them also carries `area_placement` and
        the engine never places a wall. The registry row is what stops it
        looking harmless for a reason nobody wrote down.
        """
        wall = _spell("wall-of-fire")
        self.assertIn("width_assumed", wall["flags"])
        self.assertEqual(wall["area"], {"shape": "line", "size": 60, "width": 5})
        self.assertTrue(any(l.startswith("width_assumed: recorded")
                            for l in build_srd.diagnostics(wall)))
        self.assertIn("assumption", build_srd.PRESERVED_NOT_APPLIED["width_assumed"]["reason"])

    def test_conditional_bonus_is_registered(self):
        """Found by running the new parser over all 334 real monsters, not the
        fixture: two actions write `conditional_bonus` and no fixture record in
        this file did. The Druid's Quarterstaff is the example and it is here."""
        staff = _action("druid", "Quarterstaff")
        self.assertIn("conditional_bonus", staff["flags"])
        self.assertEqual(build_srd.PRESERVED_NOT_APPLIED["conditional_bonus"]["state"],
                         "recorded")
        self.assertTrue(any(l.startswith("conditional_bonus: recorded")
                            for l in build_srd.diagnostics(staff)))

    def test_diagnostics_name_the_state_and_the_reason(self):
        """Three states, and the reader can tell them apart without reading code.

        `dropped` means the numbers are gone and only the printed text survives;
        `recorded` means they are in the record and the engine declines to apply
        them. Conflating those is what made this invisible.
        """
        lines = build_srd.diagnostics(_spell("flame-strike"))
        self.assertTrue(any(l.startswith("damage_multi: recorded") for l in lines), lines)
        self.assertTrue(any(l.startswith("damage_choice_upcast: recorded") for l in lines), lines)

    def test_a_dropped_flag_says_dropped(self):
        action = {"name": "Frightful Presence",
                  "desc": "Each creature of the dragon's choice within 60 feet "
                          "must succeed on a DC 17 Wisdom saving throw.",
                  "dc": {"dc_type": {"index": "wis"}, "dc_value": 17,
                         "success_type": "none"}}
        parsed = build_srd._norm_monster_action(action)
        self.assertIn("targeting", parsed["flags"])
        self.assertTrue(any(l.startswith("targeting: dropped")
                            for l in build_srd.diagnostics(parsed)), parsed["flags"])

    def test_a_flagless_record_has_no_diagnostics(self):
        self.assertEqual(build_srd.diagnostics({"flags": []}), [])
        self.assertEqual(build_srd.diagnostics(_spell("fire-bolt")), [])

    def test_diagnostics_ignore_a_flag_nobody_registered(self):
        self.assertEqual(build_srd.diagnostics({"flags": ["not_a_real_flag"]}), [])


if __name__ == "__main__":
    unittest.main()