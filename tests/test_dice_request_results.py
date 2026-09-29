"""A prescribed roll must come back to the GM with its result.

SKILL.md tells the GM to call `send.py --dice-request ... --wait` and resolve
the check from what it prints. The server dropped the request as soon as the
last roll arrived, so the status poll could only say "complete" and --wait
printed nothing the GM could resolve. The status endpoint now keeps each
roll's text for a finished request, and --wait prints it.
"""
import importlib.util
import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_dice", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DiceRequestResults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._persist_log = lambda: None       # never touch the real log files
        cls.mod._persist_tail = lambda: None
        cls.mod._broadcast = lambda payload: None
        cls.client = cls.mod.app.test_client()

    def request(self, chars):
        r = self.client.post("/dice-request", data=json.dumps(
            {"characters": chars, "spec": "1d20", "modifier": 1, "label": "Insight check"}),
            content_type="application/json")
        return r.get_json()["request_id"]

    def roll(self, rid, char):
        r = self.client.post("/player-input/dice", data=json.dumps(
            {"character": char, "spec": "1d20", "modifier": 1, "label": "Insight check",
             "request_id": rid}), content_type="application/json")
        return r.get_json()["text"]

    def status(self, rid):
        return self.client.get(f"/dice-request/{rid}").get_json()

    def test_a_finished_request_reports_every_roll(self):
        rid = self.request(["Kairos", "Mira"])
        first = self.roll(rid, "Kairos")
        st = self.status(rid)
        self.assertFalse(st["complete"])
        self.assertEqual(st["results"], [first])
        second = self.roll(rid, "Mira")
        st = self.status(rid)
        self.assertTrue(st["complete"])
        self.assertEqual(st["results"], [first, second])

    def test_an_unknown_request_is_not_reported_as_complete(self):
        """A request that was never issued must not read as a finished one.

        Both used to answer complete=True, so send.py --wait printed "all rolls
        received" for an id no one ever issued and the GM moved on having rolled
        nothing. It now 404s, and `known` is False for a server that still
        answers 200.
        """
        r = self.client.get("/dice-request/nope")
        self.assertEqual(r.status_code, 404)
        st = r.get_json()
        self.assertFalse(st["complete"])
        self.assertFalse(st["known"])
        self.assertEqual(st.get("results", []), [])

    def test_a_known_request_reports_known_true(self):
        """The finished case is unchanged: known and complete are both true, so
        a caller can still tell a real completion from a miss."""
        rid = self.request(["Kairos"])
        self.roll(rid, "Kairos")
        st = self.status(rid)
        self.assertTrue(st["known"])
        self.assertTrue(st["complete"])

    def test_a_pending_request_is_known_but_not_complete(self):
        rid = self.request(["Kairos", "Mira"])
        st = self.status(rid)
        self.assertTrue(st["known"])
        self.assertFalse(st["complete"])
        self.assertEqual(st["pending"], ["Kairos", "Mira"])

    def test_old_finished_requests_are_forgotten(self):
        """The keep-ring evicts old ids. An evicted id is now a 404 like any other
        unknown one — it is genuinely no longer known — so the results are gone
        and it does not claim to be a completed request either."""
        rids = [self.request(["Kairos"]) for _ in range(self.mod._DICE_DONE_KEEP + 1)]
        for rid in rids:
            self.roll(rid, "Kairos")
        evicted = self.client.get(f"/dice-request/{rids[0]}")
        self.assertEqual(evicted.status_code, 404)
        self.assertEqual(evicted.get_json().get("results", []), [])
        self.assertEqual(len(self.status(rids[-1])["results"]), 1)

    def test_a_cancelled_request_says_so_and_keeps_its_rolls(self):
        rid = self.request(["Kairos", "Mira"])
        first = self.roll(rid, "Kairos")
        self.client.delete(f"/dice-request/{rid}")
        st = self.status(rid)
        self.assertTrue(st["complete"])
        self.assertTrue(st.get("cancelled"))
        self.assertEqual(st["results"], [first])


if __name__ == "__main__":
    unittest.main()
