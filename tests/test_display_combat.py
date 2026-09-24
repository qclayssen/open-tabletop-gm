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


if __name__ == "__main__":
    unittest.main()
