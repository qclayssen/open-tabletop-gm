"""Party Input sends straight to the DM-gated queue — no staging, no Ready step.

WHY
===
Party Input used to be three taps: type, Stage, then Ready. The middle step
existed so a player could revise an action before it reached the DM, and the
Ready button existed so a batch of players could arm together. Both are gone.

What is pinned here is the part that is easy to get quietly wrong when you
delete a step: `.input_queue` is a single shared file that several phones write
to independently. The old code rewrote it wholesale on a single fire; a
one-tap-send version that still rewrites would make the second player of the
round silently erase the first. So the append-and-never-clobber behaviour is
the point of this file, along with recall actually removing the line and
reporting honestly once the DM has taken it.
"""

import importlib.util
import json
import pathlib
import tempfile
import threading
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_player_input", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PLAYERS = {"players": [{"name": "Kairos"}, {"name": "Mira"}]}


class SendEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.qf = self.tmp / ".input_queue"
        self.mod.QUEUE_FILE = str(self.qf)
        self.broadcasts = []
        self.mod._broadcast = self.broadcasts.append
        # Re-patch per test: the device-approval test swaps this out, and a
        # module-level patch would leak "pending" into every later test.
        self.mod._device_ok = lambda device_id, ip: "approved"
        self.mod._sent.clear()
        self.mod._queue_status.clear()
        self.mod._rate_buckets.clear()
        self.mod._current_stats = dict(PLAYERS)
        self.client = self.mod.app.test_client()

    def send(self, character, text):
        return self.client.post(
            "/player-input/send",
            data=json.dumps({"character": character, "text": text}),
            content_type="application/json",
            headers={"X-DND-Device": "test-device"})

    def queue(self):
        return self.qf.read_text(encoding="utf-8") if self.qf.exists() else ""

    # ── every action producer feeds both consumer paths ───────────────

    def test_party_input_send_is_visible_to_both_consumers(self):
        self.assertEqual(self.send("Kairos", "watches the innkeeper").status_code, 204)
        self.assertEqual(self.queue(), "[Kairos]: watches the innkeeper\n")

    def test_legacy_player_input_is_visible_to_both_consumers(self):
        r = self.client.post("/player-input", json={"character": "Kairos", "text": "takes cover"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.queue(), "[Kairos]: takes cover\n")

    def test_grid_action_is_visible_to_both_consumers(self):
        # Scoped to this test: the module is shared by the whole class.
        for name, value in (
                ("_current_combat", {
                    "status": "active", "current": "kairos",
                    "tokens": [{"id": "kairos", "name": "Kairos", "controller": "player"}]}),
                ("_run_tactics", lambda args, extra=(): (
                    0, json.dumps({"text": "Kairos moves to D5", "result": {}})))):
            patcher = mock.patch.object(self.mod, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        response = self.client.post(
            "/combat/do", json={"cmd": "move", "args": ["kairos", "D5"]},
            headers={"X-DND-Device": "test-device"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.queue(), "[Kairos]: (grid) Kairos moves to D5\n")

    def test_the_old_staging_endpoints_are_gone(self):
        # If these ever come back, someone has re-added the two-tap flow.
        for path in ("/player-input/stage", "/player-input/ready", "/player-input/unstage"):
            r = self.client.post(path, data=json.dumps({"character": "Kairos"}),
                                 content_type="application/json")
            self.assertEqual(r.status_code, 404, f"{path} still exists")

    def test_nothing_is_left_waiting_for_a_ready_tap(self):
        self.send("Kairos", "steps back")
        with self.mod._sent_lock:
            self.assertIn("Kairos", self.mod._sent)
        # The badge is driven by queue_status, which must already list them.
        with self.mod._queue_status_lock:
            self.assertIn("Kairos", self.mod._queue_status)

    # ── the regression that matters: two phones, one file ─────────────

    def test_a_second_send_appends_and_never_erases_the_first(self):
        self.send("Kairos", "draws his rapier")
        self.send("Mira", "steps behind the barrel")
        self.assertEqual(
            self.queue(),
            "[Kairos]: draws his rapier\n[Mira]: steps behind the barrel\n")

    def test_concurrent_sends_all_survive(self):
        # Two players tapping Send in the same second hit the same file. The
        # read-modify-write runs under _queue_lock; without it, the loser's
        # stale read rewrites the file and the winner's action vanishes.
        names = [f"P{i}" for i in range(12)]
        self.mod._current_stats = {"players": [{"name": n} for n in names]}
        barrier = threading.Barrier(len(names))

        def tap(name):
            barrier.wait()
            self.send(name, f"acts as {name}")

        threads = [threading.Thread(target=tap, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = [ln for ln in self.queue().splitlines() if ln]
        self.assertEqual(len(lines), len(names), f"lost a send:\n{self.queue()}")
        for n in names:
            self.assertIn(f"[{n}]: acts as {n}", lines)

    def test_a_resend_by_the_same_character_replaces_rather_than_duplicates(self):
        self.send("Kairos", "hesitates")
        self.send("Kairos", "commits")
        self.assertEqual(self.queue(), "[Kairos]: commits\n")

    def test_the_queue_keeps_content_a_consumer_has_not_taken_yet(self):
        # check_input.py may be mid-read; our append must not start from a
        # truncated file and drop whatever was already there.
        self.qf.write_text("[Old]: something already queued\n", encoding="utf-8")
        self.send("Kairos", "adds his voice")
        self.assertIn("[Old]: something already queued", self.queue())
        self.assertIn("[Kairos]: adds his voice", self.queue())

    # ── recall ────────────────────────────────────────────────────────

    def test_recall_removes_the_line_from_the_queue(self):
        self.send("Kairos", "changed his mind")
        r = self.client.post("/player-input/recall", data=json.dumps({"character": "Kairos"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.queue(), "")

    def test_recall_leaves_the_other_players_actions_alone(self):
        self.send("Kairos", "hesitates")
        self.send("Mira", "commits")
        self.client.post("/player-input/recall", data=json.dumps({"character": "Kairos"}),
                         content_type="application/json")
        self.assertEqual(self.queue(), "[Mira]: commits\n")

    def test_recall_reports_honestly_once_the_dm_already_took_it(self):
        # The DM consumed the queue: the action is in Claude's context and
        # cannot be pulled back. Say so rather than pretending it worked.
        self.send("Kairos", "too late")
        self.qf.unlink()
        r = self.client.post("/player-input/recall", data=json.dumps({"character": "Kairos"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 409)

    # ── the DM consuming the queue clears the sent log ────────────────

    def test_consuming_the_queue_clears_the_sent_log_and_pending_banner(self):
        self.send("Kairos", "steps forward")
        self.client.post("/queue/consumed", data="{}", content_type="application/json")
        with self.mod._sent_lock:
            self.assertEqual(self.mod._sent, {})
        with self.mod._queue_status_lock:
            self.assertEqual(self.mod._queue_status, [])
        last = self.broadcasts[-1]
        self.assertEqual(last["sent_log"], {})
        self.assertEqual(last["queue_status"], [])

    # ── guards that must survive the route rewrite ────────────────────

    def test_unknown_characters_are_still_refused(self):
        self.assertEqual(self.send("Giant Frog", "ribbits").status_code, 403)
        self.assertEqual(self.queue(), "")

    def test_empty_actions_are_still_refused(self):
        self.assertEqual(self.send("Kairos", "   ").status_code, 400)
        self.assertEqual(self.queue(), "")

    def test_skip_turn_sends_immediately(self):
        r = self.client.post("/player-input/skip", data=json.dumps({"character": "Mira"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.queue(), "[Mira]: skips their turn\n")

    def test_device_approval_is_still_enforced_on_send(self):
        self.mod._device_ok = lambda device_id, ip: "pending"
        r = self.send("Kairos", "waits for approval")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.queue(), "")


if __name__ == "__main__":
    unittest.main()
