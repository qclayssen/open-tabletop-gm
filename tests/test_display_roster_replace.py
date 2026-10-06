"""A roster replace must decide WHO is at the table, not erase each player's state.

`scripts/localdm/play.py:1816` pushes the roster on every GM start:

    display.push_party(context.party_stats(camp_dir))

`context.party_stats()` (`scripts/localdm/context.py:343`) parses `characters/*.md`
and emits exactly eight keys -- name, race, class, level, hp{current,max,temp},
ac, initiative, speed -- and nothing else. Everything else a player carries
(xp, spell_slots, hit_dice, effects, concentration, conditions, inventory,
resources) arrives only from an explicit push: `display/push_stats.py --xp` /
`--spell-slots`, and `tactics/sync.py:222-224` for conditions and live HP
mid-fight.

The `/stats` handler made those two facts collide:

    if data.get("replace_players"):
        _current_stats["players"] = []          # cleared here...
    existing_players = _current_stats.setdefault("players", [])   # ...bound here

so the by-name match one line later could never hit, and every incoming player
was re-appended through the new-player branch carrying only those eight keys.
A GM restart therefore reset the whole party's spell slots, effects and hit dice
to nothing -- mid-fight, with the roster push as the cause.

The property asserted here is the user-visible one, deliberately not the ordering
fix: **a party mid-fight is still mid-fight after the display restarts.** The
wipe ordering is an implementation detail that could be rewritten three ways; the
property is what a player at the table would actually notice.
"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app(name="gm_display_app_roster_replace"):
    spec = importlib.util.spec_from_file_location(
        name, str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RosterReplaceKeepsEachPlayersState(unittest.TestCase):
    """replace_players decides membership. It does not decide a player's fields."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._persist_log = lambda: None
        cls.mod._persist_tail = lambda: None
        cls.mod._broadcast = lambda payload: None
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        # Per-test stats.json, so a real restart can be simulated rather than
        # asserted about. gm-display-app.py resolves STATS_FILE at module level,
        # so pointing the global is enough to redirect persistence.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved_stats = self.mod.STATS_FILE
        self._saved_camp = self.mod.CAMP_FILE
        self.mod.STATS_FILE = str(pathlib.Path(self._tmp.name) / "stats.json")
        # An unresolvable campaign: the honest "unknown" answer, which makes the
        # load path leave saved campaign state alone (#281) so it cannot be
        # mistaken for the cause of a loss asserted here.
        self.mod.CAMP_FILE = str(pathlib.Path(self._tmp.name) / "absent")
        self.addCleanup(self._restore)
        self.mod._current_stats = {}

    def _restore(self):
        self.mod.STATS_FILE = self._saved_stats
        self.mod.CAMP_FILE = self._saved_camp

    # ── helpers ──────────────────────────────────────────────────────────────

    def push(self, payload):
        return self.client.post("/stats", data=json.dumps(payload),
                                content_type="application/json")

    def gm_start(self, *names):
        """Exactly what play.py:1816 sends: the roster parsed from the sheets,
        with replace_players so a character who left stops showing up."""
        return self.push({
            "players": [{"name": n, "race": "Kenku", "class": "Bard",
                         "level": 2, "hp": {"current": 20, "max": 20, "temp": 0},
                         "ac": 14, "initiative": "+2", "speed": 30}
                        for n in names],
            "replace_players": True})

    def mid_fight_push(self, name="Kairos"):
        """What the GM's own commands put on the player between starts."""
        self.push({"players": [{
            "name": name,
            "xp": 950,
            "spell_slots": {"2": {"used": 2, "max": 3}},
            "hit_dice": {"d8": {"used": 1, "max": 4}},
            "effects": [{"name": "Bless", "duration_type": "rounds",
                         "concentration": True}],
            "conditions": ["Blessed"],
            "concentration": "Bless",
        }]})

    def restart_display(self):
        """A display restart: in-memory stats reloaded from stats.json."""
        self.mod._current_stats = {}
        self.mod._load_stats()

    def player(self, name="Kairos"):
        return next(p for p in self.mod._current_stats["players"]
                    if p.get("name") == name)

    # ── the property ─────────────────────────────────────────────────────────

    def test_a_mid_fight_party_keeps_slots_effects_and_hit_dice_across_a_restart(self):
        self.gm_start("Kairos")
        self.mid_fight_push()

        self.restart_display()
        self.gm_start("Kairos")          # the GM starts again, mid-fight

        p = self.player()
        self.assertEqual(p["spell_slots"], {"2": {"used": 2, "max": 3}},
                         "a GM restart spent the party's spell slots")
        self.assertEqual(p["hit_dice"], {"d8": {"used": 1, "max": 4}},
                         "a GM restart rolled the party's hit dice back")
        self.assertEqual(p["effects"], [{"name": "Bless", "duration_type": "rounds",
                                         "concentration": True}],
                         "a GM restart ended the party's active effects")
        self.assertEqual(p["conditions"], ["Blessed"],
                         "a GM restart cleared the party's conditions")
        self.assertEqual(p["concentration"], "Bless",
                         "a GM restart dropped who was concentrating")
        self.assertEqual(p["xp"], 950, "a GM restart zeroed the party's XP")

    def test_the_push_still_overlays_the_keys_it_does_carry(self):
        """Partial refresh, not no refresh: what the sheet DOES say wins."""
        self.gm_start("Kairos")
        self.mid_fight_push()
        self.restart_display()

        self.push({"players": [{"name": "Kairos", "level": 5,
                               "hp": {"current": 34, "max": 38, "temp": 0}}],
                   "replace_players": True})

        p = self.player()
        self.assertEqual(p["level"], 5, "the sheet's level did not overwrite the stale one")
        self.assertEqual(p["hp"], {"current": 34, "max": 38, "temp": 0})
        self.assertEqual(p["spell_slots"], {"2": {"used": 2, "max": 3}},
                         "overlaying hp discarded the slots")

    # ── the purpose replace_players exists for must survive ──────────────────

    def test_a_character_who_left_the_party_is_still_dropped(self):
        """This is the reason the flag exists (see its docstring): a character
        who is no longer on the roster must not linger on the sidebar. A fix
        that preserved everything would have broken this silently."""
        self.gm_start("Kairos", "Sela")
        self.restart_display()
        self.gm_start("Kairos")

        names = [p.get("name") for p in self.mod._current_stats["players"]]
        self.assertEqual(names, ["Kairos"],
                         "replace_players no longer removes a departed character")

    def test_a_character_who_stays_keeps_their_identity_across_the_filter(self):
        """The filter rebuilds the list, so the surviving entries must be the
        matched ones -- carrying their state -- not fresh ones."""
        self.gm_start("Kairos", "Sela")
        self.push({"players": [{"name": "Sela", "xp": 300}]})
        self.restart_display()

        self.gm_start("Kairos", "Sela")

        self.assertEqual(self.player("Sela")["xp"], 300)

    # ── AC-4: the paths that share this code keep working ─────────────────────

    def test_a_partial_update_without_replace_still_works(self):
        self.gm_start("Kairos")
        self.mid_fight_push()
        self.push({"players": [{"name": "Kairos",
                                "hp": {"current": 11, "max": 20, "temp": 0}}]})

        p = self.player()
        self.assertEqual(p["hp"]["current"], 11)
        self.assertEqual(p["spell_slots"], {"2": {"used": 2, "max": 3}})

    def test_a_partial_update_for_one_player_leaves_the_others_alone(self):
        self.gm_start("Kairos", "Sela")
        self.push({"players": [{"name": "Sela", "xp": 300}]})
        self.push({"players": [{"name": "Kairos", "hp": {"current": 3, "max": 20, "temp": 0}}]})

        self.assertEqual(self.player("Kairos")["hp"]["current"], 3)
        self.assertEqual(self.player("Sela")["xp"], 300)

    def test_a_slot_mutation_still_spends_a_slot(self):
        self.gm_start("Kairos")
        self.mid_fight_push()
        self.push({"players": [{"name": "Kairos", "_slot_use": "2"}]})

        self.assertEqual(self.player()["spell_slots"]["2"], {"used": 3, "max": 3},
                         "spending the last slot did not clamp at max")

    def test_an_inventory_mutation_still_applies(self):
        self.gm_start("Kairos")
        self.push({"players": [{"name": "Kairos", "_inventory_add": "Thieves' Tools"}]})
        inv = self.player().get("sheet", {}).get("inventory", [])
        self.assertIn("Thieves' Tools", inv)

    def test_a_roster_push_without_replace_still_appends_a_new_character(self):
        self.gm_start("Kairos")
        self.push({"players": [{"name": "Sela", "level": 3}]})

        names = sorted(p.get("name") for p in self.mod._current_stats["players"])
        self.assertEqual(names, ["Kairos", "Sela"])


if __name__ == "__main__":
    unittest.main()