"""`send.py --dice-request --offer ... --wait` must let the GM resolve the check.

The GM's loop ends with what `--wait` printed. If the spend is only visible in the
pad, the GM is reading the phone over the player's shoulder to learn that a
per-rest resource was consumed — which is the failure this whole feature exists
to fix.

So the spend has to arrive on stdout. It does, through the roll text:
`… = 26 — Stealth (Kenku Recall)`. This test proves it end to end by driving the
real `send.py` against the real Flask app, rather than asserting that send.py
prints `results` (which it did before any of this work and would have passed with
the spend absent).

The refusal path matters as much: a GM whose player pressed a spent feature gets
an HTTP 400 with a sentence, and send.py must print that sentence rather than
`dice-request failed: HTTP Error 400`.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from wsgiref.simple_server import WSGIRequestHandler, make_server

REPO = pathlib.Path(__file__).resolve().parent.parent
SEND = REPO / "display" / "send.py"


def _quiet(*_a):
    """A request handler that does not narrate every poll to stderr."""
    pass


def _import_app():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_wait_spend", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._token_ok = lambda: True
    mod._persist_log = lambda: None
    mod._persist_tail = lambda: None
    return mod


class WaitPrintsTheSpend(unittest.TestCase):
    """The real send.py, the real app, over a real socket."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        # A threading server: the display holds /stream open, and a
        # single-threaded one answers exactly one asset and then stalls.
        class Handler(WSGIRequestHandler):
            def log_message(self, *a):
                pass

        # "localhost", not "127.0.0.1": send.py builds its URL as
        # f"{scheme}://localhost:{port}" (send.py:88) and offers no host
        # override, so a server bound only to the IP literal is unreachable.
        cls.httpd = make_server("localhost", 0, cls.mod.app,
                                handler_class=Handler)
        cls.httpd.timeout = 0.5
        cls.port = cls.httpd.server_port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.client = self.mod.app.test_client()
        self.client.post("/stats", data=json.dumps({
            "players": [{"name": "Kairos", "hp": {"current": 20, "max": 20}}]}),
            content_type="application/json")
        self.client.post("/stats", data=json.dumps({"players": [
            {"name": "Kairos",
             "_resource_set": {"Kenku Recall": {"used": 0, "max": 2}}}]}),
            content_type="application/json")
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def tearDown(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def _send_and_roll(self, offer, roll=True):
        """Run send.py --dice-request --offer ... --wait, answering the roll from
        a background thread the way a player's phone would."""
        proc_holder = {}

        def player():
            # Wait for the request to be registered, then answer it the way a
            # player's phone would — spending the feature only when the test says
            # an offer was declared.
            body = {"character": "Kairos", "spec": "1d20", "modifier": 0,
                    "label": "Stealth"}
            if offer:
                body["spend"] = "kenku_recall"
            for _ in range(100):
                with self.mod._dice_pending_lock:
                    pending = dict(self.mod._dice_pending)
                if pending:
                    rid = next(iter(pending))
                    self.client.post("/player-input/dice", data=json.dumps(
                        dict(body, request_id=rid)),
                        content_type="application/json")
                    return
                time.sleep(0.05)

        # send.py hardcodes `localhost` and takes the port from the environment
        # (BASE_URL at send.py:88), so the display must listen on the loopback
        # name rather than 127.0.0.1 to be reachable. Bind both by using
        # localhost in the override below.
        env = os.environ.copy()
        env["GM_DISPLAY_PORT"] = str(self.port)
        args = [sys.executable, str(SEND), "--dice-request",
                "--character", "Kairos", "--spec", "1d20", "--label", "Stealth"]
        if offer:
            args += ["--offer", offer]
        args += ["--wait", "--wait-timeout", "8"]

        if roll:
            threading.Thread(target=player, daemon=True).start()
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", timeout=30, env=env)
        proc_holder["proc"] = proc
        return proc

    def test_the_spend_is_on_stdout_for_the_gm_to_resolve_from(self):
        proc = self._send_and_roll("Kenku Recall:advantage")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Kenku Recall", proc.stdout,
                      "the GM must be able to resolve the check from what "
                      "--wait printed, without reading the player's phone")

    def test_the_spend_appears_with_the_check_it_paid_for(self):
        proc = self._send_and_roll("Kenku Recall:advantage")
        self.assertRegex(proc.stdout, r"— Stealth \(Kenku Recall\)")

    def test_a_roll_with_no_spend_prints_no_feature_name(self):
        """The regression guard: the common case must not gain a name."""
        proc = self._send_and_roll(None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Kenku Recall", proc.stdout)

    def test_a_malformed_offer_prints_the_reason_not_a_status_code(self):
        proc = self._send_and_roll("Kenku Recall:granted", roll=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("granted", proc.stderr)
        self.assertNotIn("HTTP Error", proc.stderr,
                         "the GM needs the sentence, not urllib's status line")


if __name__ == "__main__":
    unittest.main()