"""The display's grid combat endpoints (gm-display-app.py).

The engine is not run here: _run_tactics is replaced so these tests pin the
app's own job, which is relaying. It must broadcast the engine's snapshot,
let a browser act only for the player whose turn it is, pass rolls through as
the player's own, and queue each result so the GM sees it.
"""
import importlib.util
import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_combat", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SNAP = {"status": "active", "round": 1, "current": "kairos", "order": ["kairos", "frog-1"],
        "tokens": [{"id": "kairos", "name": "Kairos", "controller": "player"},
                   {"id": "frog-1", "name": "Giant Frog 1", "controller": "gm"}]}


class CombatEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._persist_input_queue = lambda: None     # never touch the real queue file
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        self.sent, self.calls = [], []
        self.mod._broadcast = self.sent.append
        self.mod._input_queue.clear()
        self.mod._current_combat = None
        self.mod._rate_buckets.clear()
        self.reply = (0, json.dumps({"text": "Kairos moves B7 to D5 (10 ft, 20 ft left).",
                                     "result": {"feet": 10}}))
        self.mod._run_tactics = lambda args, extra=(): (self.calls.append((args, list(extra)))
                                                        or self.reply)

    def push(self, snap):
        return self.client.post("/combat", data=json.dumps({"combat": snap}),
                                content_type="application/json")

    def do(self, body):
        r = self.client.post("/combat/do", data=json.dumps(body), content_type="application/json",
                             headers={"X-DND-Device": "test-device"})
        return r.status_code, r.get_json()

    def test_a_snapshot_is_stored_and_broadcast(self):
        self.assertEqual(self.push(SNAP).status_code, 204)
        self.assertEqual(self.sent, [{"combat": SNAP}])
        self.assertEqual(self.mod._current_combat, SNAP)

    def test_an_ended_combat_is_broadcast_and_cleared(self):
        self.push(SNAP)
        self.push(dict(SNAP, status="ended"))
        self.assertIsNone(self.mod._current_combat)
        self.assertEqual(self.sent[-1]["combat"]["status"], "ended")

    def test_unknown_commands_are_refused(self):
        self.assertEqual(self.do({"cmd": "end", "args": []})[0], 400)
        self.assertEqual(self.calls, [])

    def test_only_the_player_whose_turn_it_is_can_act(self):
        self.push(dict(SNAP, current="frog-1"))
        code, body = self.do({"cmd": "move", "args": ["frog-1", "D5"]})
        self.assertEqual(code, 409)
        self.push(SNAP)
        code, body = self.do({"cmd": "move", "args": ["frog-1", "D5"]})
        self.assertEqual((code, body["error"]), (409, "Only Kairos can act now."))
        self.assertEqual(self.calls, [])

    def test_a_move_runs_the_engine_and_queues_the_result_for_the_gm(self):
        self.push(SNAP)
        code, body = self.do({"cmd": "move", "args": ["kairos", "D5"]})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.calls, [(["move", "kairos", "D5"], ["--json"])])
        self.assertEqual(self.mod._input_queue[-1]["character"], "Kairos")
        self.assertEqual(self.mod._input_queue[-1]["text"],
                         "(grid) Kairos moves B7 to D5 (10 ft, 20 ft left).")

    def test_rolls_are_passed_as_the_players_own(self):
        self.push(SNAP)
        self.do({"cmd": "attack", "args": ["kairos", "frog-1", "fire", "bolt"],
                 "rolls": [14, "7"], "react": "no"})
        self.assertEqual(self.calls[-1][1], ["--json", "--roll", "14", "--roll", "7",
                                             "--player-roll", "--react", "no"])

    def test_a_pending_roll_changes_nothing(self):
        self.push(SNAP)
        self.reply = (2, "Kairos rolls 1d20+5 for Fire Bolt vs Giant Frog 1.")
        code, body = self.do({"cmd": "attack", "args": ["kairos", "frog-1"]})
        self.assertEqual(body, {"pending": "Kairos rolls 1d20+5 for Fire Bolt vs Giant Frog 1."})
        self.assertEqual(self.mod._input_queue, [])

    def test_reads_do_not_queue_anything(self):
        self.reply = (0, json.dumps({"text": "12 squares walking.", "result": {"walk": {}}}))
        code, body = self.do({"cmd": "reachable", "args": ["kairos"]})
        self.assertTrue(body["ok"])
        self.assertEqual(self.mod._input_queue, [])


    def test_spell_reads_run_for_a_players_token_and_queue_nothing(self):
        self.push(SNAP)
        self.reply = (0, json.dumps({"text": "Fire Bolt", "result": {"spells": []}}))
        for body in ({"cmd": "spells", "args": ["kairos"]},
                     {"cmd": "preview-area", "args": ["kairos", "burning hands", "D7"]}):
            code, res = self.do(body)
            self.assertEqual((code, res["ok"]), (200, True))
        self.assertEqual([c[0] for c in self.calls],
                         [["spells", "kairos"], ["preview-area", "kairos", "burning hands", "D7"]])
        self.assertEqual(self.mod._input_queue, [])

    def test_sight_runs_only_from_a_creature_on_the_players_map(self):
        # The snapshot leaves out hidden and unseen enemies: no sight from them,
        # and the engine is always asked for the players' view.
        self.push(SNAP)
        self.reply = (0, json.dumps({"text": "From Kairos (B7): ...", "result": {"cover": {}}}))
        code, res = self.do({"cmd": "sight", "args": ["frog-1"]})
        self.assertEqual((code, res["ok"]), (200, True))
        self.assertEqual(self.calls, [(["sight", "frog-1", "--players"], ["--json"])])
        code, res = self.do({"cmd": "sight", "args": ["goblin-9"]})
        self.assertEqual(code, 403)
        code, res = self.do({"cmd": "sight", "args": ["kairos", "--players"]})
        self.assertEqual(code, 400)                    # flags from the browser are refused
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.mod._input_queue, [])

    def test_new_actions_are_whitelisted_for_the_current_player(self):
        self.push(SNAP)
        bodies = [["cast", "kairos", "Magic Missile", "frog-1", "frog-1", "frog-1"],
                  ["help", "kairos", "ally-1", "frog-1"], ["hide", "kairos"], ["escape", "kairos"],
                  ["ready", "kairos", "attack", "Dagger", "--target", "frog-1",
                   "--trigger=a frog comes close"],
                  ["reactions", "kairos", "auto"]]
        for cmd, *args in bodies:
            code, body = self.do({"cmd": cmd, "args": args})
            self.assertEqual(code, 200, cmd)
        self.assertEqual([c[0] for c in self.calls], bodies)
        self.assertEqual(len(self.mod._input_queue), len(bodies) - 1)   # reactions is a setting

    def test_new_actions_refuse_another_token(self):
        self.push(SNAP)
        for cmd, args in (("cast", ["frog-1", "fire bolt", "kairos"]), ("help", ["frog-1", "frog-2", "kairos"]),
                          ("hide", ["frog-1"]), ("reactions", ["frog-1", "off"])):
            self.assertEqual(self.do({"cmd": cmd, "args": args})[0], 409, cmd)
        self.assertEqual(self.calls, [])

    def test_react_may_be_a_list_of_answers_capped_at_four(self):
        self.push(SNAP)
        self.do({"cmd": "move", "args": ["kairos", "D5"], "react": ["yes", "no", "maybe", "yes", "no", "yes"]})
        extra = self.calls[-1][1]
        self.assertEqual(extra, ["--json", "--react", "yes", "--react", "no", "--react", "yes"])

    def test_args_are_capped_at_eight(self):
        self.push(SNAP)
        self.do({"cmd": "cast", "args": ["kairos", "magic missile"] + ["frog-1"] * 10})
        self.assertEqual(len(self.calls[-1][0]), 1 + 8)

    def test_flags_in_args_cannot_smuggle_answers(self):
        self.push(SNAP)
        code, body = self.do({"cmd": "attack", "args": ["kairos", "frog-1", "--roll", "20"]})
        self.assertEqual(code, 400)
        code, body = self.do({"cmd": "cast", "args": ["kairos", "fire bolt", "frog-1", "-c", "other"]})
        self.assertEqual(code, 400)
        self.assertEqual(self.calls, [])
        code, body = self.do({"cmd": "cast", "args": ["kairos", "burning hands", "D7", "--level", "1"]})
        self.assertEqual(code, 200)

class Snapshot(unittest.TestCase):
    """sync.snapshot carries what the spell and action UI shows."""

    def test_spell_and_action_fields(self):
        import sys
        sys.path.insert(0, str(REPO / "scripts"))
        from tactics import sync
        from tactics.state import Encounter, Token
        k = Token(id="kairos", name="Kairos", side="pc", x=1, y=6, hp=8, max_hp=8, ac=12,
                  controller="player", concentration="Detect Magic", reactions="auto",
                  conditions=["hidden"], effects=[{"name": "shield", "ac": 5},
                                                  {"name": "web", "conditions": ["restrained"]}],
                  extra={"readied": {"label": "Fire Bolt at frog-1"},
                         "slots": {"1": {"used": 1, "total": 2}}})
        f = Token(id="frog-1", name="Giant Frog 1", side="enemy", x=5, y=6, hp=18, max_hp=18, ac=11)
        enc = Encounter(campaign="t", grid={"rows": ["......."] * 8},
                        tokens={"kairos": k, "frog-1": f}, order=["kairos", "frog-1"], round=1)
        enc.turn.bonus_used = True
        snap = sync.snapshot(enc)
        self.assertEqual((snap["turn"]["bonus_used"], snap["turn"]["reaction"]), (True, True))
        tk = snap["tokens"][0]
        self.assertEqual(tk["concentration"], "Detect Magic")
        self.assertEqual(tk["effects"], ["shield"])
        self.assertEqual((tk["reactions"], tk["readied"], tk["hidden"]), ("auto", "Fire Bolt at frog-1", True))
        self.assertEqual(tk["slots"], {"1": {"used": 1, "total": 2}})
        tf = snap["tokens"][1]
        self.assertEqual((tf["readied"], tf["hidden"], tf["effects"]), (None, False, []))
        for key in ("id", "name", "side", "x", "y", "hp", "max_hp", "ac", "conditions", "dead", "controller"):
            self.assertIn(key, tk)
        k.reaction_used = True
        self.assertFalse(sync.snapshot(enc)["turn"]["reaction"])


if __name__ == "__main__":
    unittest.main()

    # ── second review pass ──
    def test_monster_spells_and_previews_are_not_readable_from_a_browser(self):
        self.push(SNAP)
        code, body = self.do({"cmd": "spells", "args": ["frog-1"]})
        self.assertEqual(code, 403)
        code, body = self.do({"cmd": "preview-area", "args": ["frog-1", "fire bolt", "B7"]})
        self.assertEqual(code, 403)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.do({"cmd": "spells", "args": ["kairos"]})[0], 200)

    def test_a_usage_error_is_an_error_not_a_pending_prompt(self):
        self.push(SNAP)
        self.reply = (2, "usage: combat.py cast ...\ncombat.py cast: error: argument --level: invalid int value: 'x'")
        code, body = self.do({"cmd": "cast", "args": ["kairos", "fire bolt", "--level", "x"]})
        self.assertIn("error", body)
        self.assertNotIn("pending", body)
        self.reply = (2, "Kairos rolls 1d20+5 for Fire Bolt vs Frog 1. Nothing has happened yet.")
        code, body = self.do({"cmd": "cast", "args": ["kairos", "fire bolt", "frog-1"]})
        self.assertIn("pending", body)

    def test_the_reactions_setting_is_not_queued_for_the_gm(self):
        self.push(SNAP)
        self.reply = (0, json.dumps({"text": "Kairos: spell reactions auto.", "result": {}}))
        code, body = self.do({"cmd": "reactions", "args": ["kairos", "auto"]})
        self.assertTrue(body["ok"])
        self.assertEqual(list(self.mod._input_queue), [])

