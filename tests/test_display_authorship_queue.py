"""Display server hardening: authorship (N11), queue honesty (N6), DC-safe dice
log, roster errors (N7), stale turn_order (N9) and NPC sheets (N8)."""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "display"))
import queue_claim  # noqa: E402


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_authorship", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PLAYERS = {"players": [{"name": "Kairos"}, {"name": "Mira"}]}
JSON = "application/json"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True

    def setUp(self):
        m = self.mod
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.qf = self.tmp / ".input_queue"
        m.QUEUE_FILE = str(self.qf)
        m.GM_LOG_FILE = str(self.tmp / "gm.jsonl")
        m.CAMP_FILE = str(self.tmp / ".campaign")
        m.STATS_FILE = str(self.tmp / "stats.json")
        m._LOG_FALLBACK = str(self.tmp / "text_log.json")
        m._get_log_file = lambda: str(self.tmp / "text_log.json")
        m._get_tail_file = lambda: None
        m._persist_tail = lambda: None
        self.broadcasts = []
        m._broadcast = self.broadcasts.append
        m._device_ok = lambda device_id, ip: "approved"
        m._sent.clear()
        m._queue_status.clear()
        m._text_log.clear()
        m._rate_buckets.clear()
        m._current_stats = dict(PLAYERS)
        m._expected_author = None
        m._author_violations.clear()
        self.client = m.app.test_client()

    def post(self, path, body, headers=None):
        return self.client.post(path, data=json.dumps(body), content_type=JSON,
                                headers=headers or {"X-DND-Device": "d"})

    def send(self, who, text):
        return self.post("/player-input/send", {"character": who, "text": text})


class Authorship(Base):
    def test_author_is_stamped_in_the_transcript(self):
        self.assertEqual(self.post("/chunk", {"text": "Rain.", "author": "seat-dm"}).status_code, 204)
        self.assertEqual(self.mod._text_log[-1]["author"], "seat-dm")

    def test_unstamped_push_still_works_without_an_expectation(self):
        self.assertEqual(self.post("/chunk", {"text": "Rain."}).status_code, 204)
        self.assertNotIn("author", self.mod._text_log[-1])

    def test_wrong_author_is_refused_and_recorded(self):
        self.post("/author", {"author": "seat-dm"})
        r = self.post("/chunk", {"text": "Slam vs AC 12", "author": "other-dm"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(self.mod._text_log), 0)
        viol = self.client.get("/author").get_json()["violations"]
        self.assertEqual(viol[0]["kind"], "wrong_author")

    def test_expected_author_passes(self):
        self.post("/author", {"author": "seat-dm"})
        self.assertEqual(self.post("/chunk", {"text": "Rain.", "author": "seat-dm"}).status_code, 204)
        self.assertEqual(self.client.get("/author").get_json()["violations"], [])

    def test_unstamped_block_is_shown_but_flagged(self):
        self.post("/author", {"author": "seat-dm"})
        self.assertEqual(self.post("/chunk", {"text": "Rain."}).status_code, 204)
        self.assertEqual(self.mod._text_log[-1]["author_flag"], "unstamped")
        self.assertEqual(self.client.get("/health").get_json()["author_violations"], 1)


class QueueHonesty(Base):
    def test_state_not_recorded_when_the_write_fails(self):
        self.mod.QUEUE_FILE = str(self.tmp / "no-such-dir" / ".input_queue")
        self.assertEqual(self.send("Kairos", "hi").status_code, 500)
        self.assertEqual(self.mod._sent, {})
        self.assertEqual(self.mod._queue_status, [])

    def test_reconcile_drops_actions_that_left_the_file(self):
        self.send("Kairos", "act")
        self.assertEqual(self.mod._queue_status, ["Kairos"])
        self.qf.unlink()
        self.assertTrue(self.mod._reconcile_queue_state())
        self.assertEqual(self.mod._queue_status, [])
        self.assertEqual(self.mod._sent, {})
        self.assertEqual(self.broadcasts[-1]["queue_status"], [])

    def test_reconcile_keeps_actions_still_queued(self):
        self.send("Kairos", "a")
        self.send("Mira", "b")
        self.qf.write_text("[Mira]: b\n", encoding="utf-8")
        self.mod._reconcile_queue_state()
        self.assertEqual(self.mod._queue_status, ["Mira"])

    def test_recall_of_a_vanished_action_is_not_reported_safe(self):
        self.send("Kairos", "act")
        self.qf.unlink()
        r = self.post("/player-input/recall", {"character": "Kairos"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("unconfirmed", r.get_data(as_text=True))
        self.assertNotIn("delivered", r.get_data(as_text=True).lower().replace("unconfirmed", ""))
        self.assertEqual(self.mod._queue_status, [])

    def test_a_queued_action_survives_to_a_consumer(self):
        self.send("Kairos", "opens the door")
        self.send("Mira", "keeps watch")
        text, delivered = queue_claim.claim_and_read(self.qf)
        self.assertTrue(delivered)
        self.assertEqual(text.splitlines(), ["[Kairos]: opens the door", "[Mira]: keeps watch"])

    def test_concurrent_sends_all_reach_the_consumer(self):
        names = [f"P{i}" for i in range(8)]
        self.mod._current_stats = {"players": [{"name": n} for n in names]}
        threads = [threading.Thread(target=self.send, args=(n, "x")) for n in names]
        [t.start() for t in threads]
        [t.join() for t in threads]
        text, _ = queue_claim.claim_and_read(self.qf)
        self.assertEqual({ln.split("]")[0][1:] for ln in text.splitlines()}, set(names))


class DiceLog(Base):
    LEAK = ("Kairos - Investigation (Station 1, The Count) vs DC 16: d20(4) + 6 = 10 -> MISS. "
            "DC falls to 14. Dial: Slipping. No mark - the floor is not reached.")

    def test_bookkeeping_goes_to_the_gm_log_not_the_screen(self):
        self.post("/chunk", {"text": self.LEAK, "dice": True})
        shown = self.mod._text_log[-1]["text"]
        for leak in ("DC falls", "Dial", "No mark", "floor"):
            self.assertNotIn(leak, shown)
        self.assertIn("d20(4) + 6 = 10", shown)
        gm = pathlib.Path(self.mod.GM_LOG_FILE).read_text(encoding="utf-8")
        self.assertIn("DC falls to 14", gm)
        self.assertIn("Dial: Slipping", gm)

    def test_hide_dc_drops_the_target(self):
        self.post("/chunk", {"text": self.LEAK, "dice": True, "hide_dc": True})
        self.assertNotIn("DC", self.mod._text_log[-1]["text"])
        self.assertIn("vs DC 16", pathlib.Path(self.mod.GM_LOG_FILE).read_text(encoding="utf-8"))

    def test_an_open_dc_line_is_untouched_by_default(self):
        self.post("/chunk", {"text": "d20+4 = 18 vs DC 15 - success", "dice": True})
        self.assertIn("vs DC 15", self.mod._text_log[-1]["text"])

    def test_gm_log_field_is_never_broadcast_or_stored_in_the_transcript(self):
        self.post("/chunk", {"text": "Kairos rolls 10.", "dice": True, "gm_log": "DC 16, dial Slipping"})
        self.assertNotIn("gm_log", self.mod._text_log[-1])
        self.assertNotIn("Slipping", json.dumps(self.broadcasts))
        self.assertIn("Slipping", pathlib.Path(self.mod.GM_LOG_FILE).read_text(encoding="utf-8"))

    def test_all_bookkeeping_line_leaves_nothing_on_screen(self):
        self.post("/chunk", {"text": "DC falls to 14. Dial: Slipping.", "dice": True})
        self.assertEqual(len(self.mod._text_log), 0)


class RosterErrors(Base):
    def test_empty_roster_gives_a_clear_error(self):
        self.mod._current_stats = {}
        r = self.send("Kairos", "hi")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "no_roster")
        self.assertEqual(self.client.get("/health").get_json()["roster"], 0)

    def test_unknown_name_is_still_refused_with_a_reason(self):
        r = self.send("Mallory", "hi")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"], "not_in_party")


class StaleTurnOrder(Base):
    def _campaign(self, enc):
        camp = self.tmp / "camps" / "c1"
        (camp / "combat").mkdir(parents=True)
        if enc is not None:
            (camp / "combat" / "encounter.json").write_text(json.dumps(enc), encoding="utf-8")
        self.mod._find_campaign = lambda name: camp
        pathlib.Path(self.mod.CAMP_FILE).write_text("c1", encoding="utf-8")

    def _load(self):
        pathlib.Path(self.mod.STATS_FILE).write_text(
            json.dumps({"players": [{"name": "A"}], "turn_order": {"order": [{"name": "Giant Frog"}]}}),
            encoding="utf-8")
        self.mod._current_stats = {}
        self.mod._load_stats()

    def test_no_encounter_file_clears_turn_order(self):
        self._campaign(None)
        self._load()
        self.assertIsNone(self.mod._current_stats["turn_order"])
        self.assertEqual(self.mod._current_stats["players"], [{"name": "A"}])

    def test_ended_encounter_clears_turn_order(self):
        self._campaign({"status": "ended"})
        self._load()
        self.assertIsNone(self.mod._current_stats["turn_order"])

    def test_active_encounter_keeps_turn_order(self):
        self._campaign({"status": "active"})
        self._load()
        self.assertTrue(self.mod._current_stats["turn_order"])

    def test_unknown_campaign_leaves_it_alone(self):
        self._load()
        self.assertTrue(self.mod._current_stats["turn_order"])


class NpcSheets(Base):
    def setUp(self):
        super().setUp()
        self.camp = self.tmp / "camp"
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "npc-files").mkdir()
        (self.camp / "npc-files" / "the-count.md").write_text("# The Count\n", encoding="utf-8")
        (self.camp / "characters" / "Kairos.md").write_text("# Kairos\n", encoding="utf-8")
        (self.tmp / "secret.md").write_text("SECRET", encoding="utf-8")
        self.mod._find_campaign = lambda name: self.camp
        self.mod._characters_dir = lambda: self.tmp / "nowhere"
        pathlib.Path(self.mod.CAMP_FILE).write_text("c1", encoding="utf-8")

    def get(self, name):
        return self.client.get(f"/character/{name}")

    def test_npc_sheet_resolves_by_slug(self):
        r = self.get("The Count")
        self.assertEqual(r.status_code, 200)
        self.assertIn("The Count", r.get_data(as_text=True))

    def test_pc_sheet_still_works(self):
        self.assertIn("Kairos", self.get("Kairos").get_data(as_text=True))

    def test_path_traversal_is_refused(self):
        for bad in ("..%2Fsecret", "..%2F..%2Fsecret", "%2E%2E/secret"):
            r = self.get(bad)
            self.assertNotIn("SECRET", r.get_data(as_text=True))
        self.assertEqual(self.get("secret").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class SendPyStamp(unittest.TestCase):
    def _send_mod(self):
        spec = importlib.util.spec_from_file_location("send_under_test", str(REPO / "display" / "send.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_stamp_uses_flag_then_env(self):
        import argparse
        mod = self._send_mod()
        ns = argparse.Namespace(author="seat-dm", gm_log="DC 16", hide_dc=True, dice=True)
        self.assertEqual(mod._stamp({"text": "x"}, ns),
                         {"text": "x", "author": "seat-dm", "gm_log": "DC 16", "hide_dc": True})
        os.environ["GM_DISPLAY_AUTHOR"] = "env-dm"
        try:
            ns = argparse.Namespace(author=None, gm_log=None, hide_dc=False, dice=False)
            self.assertEqual(mod._stamp({"text": "x"}, ns)["author"], "env-dm")
        finally:
            del os.environ["GM_DISPLAY_AUTHOR"]
        ns = argparse.Namespace(author=None, gm_log=None, hide_dc=False, dice=False)
        self.assertEqual(mod._stamp({"text": "x"}, ns), {"text": "x"})
