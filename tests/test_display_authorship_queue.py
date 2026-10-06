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


class StaleCampaignState(Base):
    """stats.json restored under the wrong campaign.

    The roster is not cosmetic state. `display/wrapper.py` `_known_chars()` reads
    the `players` names out of this same file and uses them as the allowlist that
    decides which character names may post a turn (wrapper.py:175), so a stale
    roster rejects the real party's turns and admits the other campaign's
    characters.

    The no-stamp case is asserted here as a first-class expectation rather than
    left incidental. Trusting it is a deliberate choice, not an oversight: an
    EMPTY roster makes wrapper.py skip its name check entirely ("Empty set =
    bypass name check", wrapper.py:134), so dropping the roster would turn a name
    allowlist into no allowlist. Trust is also the reversible branch -- dropping
    can be added later as one more line.
    """

    CAMPAIGN_SCOPED = ("players", "world_time", "factions", "quests")

    def _campaign(self, name="c1"):
        camp = self.tmp / "camps" / (name or "c1")
        (camp / "combat").mkdir(parents=True, exist_ok=True)
        self.mod._find_campaign = lambda wanted: camp
        pathlib.Path(self.mod.CAMP_FILE).write_text(name or "", encoding="utf-8")

    def _load(self, payload):
        pathlib.Path(self.mod.STATS_FILE).write_text(json.dumps(payload), encoding="utf-8")
        self.mod._current_stats = {}
        self.mod._load_stats()

    def _everything(self, stamp):
        payload = {"players": [{"name": "A"}], "world_time": {"day": 3},
                   "factions": [{"name": "Grove", "standing": "Friendly"}],
                   "quests": [{"id": "q1"}]}
        if stamp is not None:
            payload["_campaign"] = stamp
        return payload

    def test_another_campaigns_roster_is_dropped(self):
        self._campaign("c1")
        self._load(self._everything("other-campaign"))
        self.assertNotIn("players", self.mod._current_stats)

    def test_every_campaign_scoped_key_is_dropped_not_just_the_roster(self):
        # A cross-campaign file restores another campaign's clock, factions and
        # quest log too, and none of them is re-derived on load. Dropping only
        # `players` would close one instance of the class and leave the rest.
        self._campaign("c1")
        self._load(self._everything("other-campaign"))
        for key in self.CAMPAIGN_SCOPED:
            self.assertNotIn(key, self.mod._current_stats,
                             f"{key} survived a cross-campaign restore")

    def test_a_matching_campaign_keeps_everything(self):
        # Kills the over-eager-drop mutant: a guard that cleared on any stamp
        # would pass the test above and fail this one.
        self._campaign("c1")
        self._load(self._everything("c1"))
        for key in self.CAMPAIGN_SCOPED:
            self.assertIn(key, self.mod._current_stats)

    def test_an_unstamped_file_is_trusted(self):
        # Kills a drop-on-no-stamp implementation, which is the one that fails
        # open on wrapper.py's allowlist.
        self._campaign("c1")
        self._load(self._everything(None))
        for key in self.CAMPAIGN_SCOPED:
            self.assertIn(key, self.mod._current_stats)

    def test_an_unresolvable_campaign_leaves_the_file_alone(self):
        # No .campaign at all: the honest state is unknown, so the saved table is
        # left exactly as _drop_stale_turn_order leaves it when the encounter
        # cannot be resolved.
        self._campaign("")
        self._load(self._everything("other-campaign"))
        for key in self.CAMPAIGN_SCOPED:
            self.assertIn(key, self.mod._current_stats)

    def test_the_drop_persists_so_the_next_restart_reads_the_right_file(self):
        # Clearing the key without persisting leaves the wrong file on disk and
        # the bug returns on the next restart.
        self._campaign("c1")
        self._load(self._everything("other-campaign"))
        on_disk = json.loads(pathlib.Path(self.mod.STATS_FILE).read_text(encoding="utf-8"))
        for key in self.CAMPAIGN_SCOPED:
            self.assertNotIn(key, on_disk, f"{key} still in stats.json after the drop")

    def test_turn_order_is_not_stamp_guarded(self):
        # turn_order keeps its own oracle -- the campaign's encounter.json, which
        # is per-campaign and status-bearing -- so this guard must not touch it.
        # The encounter file has to exist and be active, otherwise
        # _drop_stale_turn_order clears it for its own reason and this would
        # assert the wrong cause.
        camp = self.tmp / "camps" / "c1"
        (camp / "combat").mkdir(parents=True, exist_ok=True)
        (camp / "combat" / "encounter.json").write_text(
            json.dumps({"status": "active"}), encoding="utf-8")
        self.mod._find_campaign = lambda name: camp
        pathlib.Path(self.mod.CAMP_FILE).write_text("c1", encoding="utf-8")
        self._load({"_campaign": "other-campaign", "players": [{"name": "A"}],
                    "turn_order": {"order": [{"name": "Giant Frog"}]}})
        self.assertNotIn("players", self.mod._current_stats)
        self.assertIsNotNone(self.mod._current_stats.get("turn_order"),
                             "the stamp guard cleared turn_order, which has its own oracle")


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
