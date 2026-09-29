"""send.py --dice-request --wait must not report rolls that were never made.

`--wait` blocks the GM until every prescribed character has rolled, then prints
"all rolls received" and exits 0. It decides that by polling
`GET /dice-request/<id>` and reading `complete`.

That route used to answer `complete=True` for an id that was never issued, so
`--wait` printed a success and exited 0 having rolled nothing. The GM then moved
on believing the table had answered. A GM agent hit this in the wild and
reported it against a live display:

    send.py --dice-request --wait reported "all rolls received" while returning
    {"complete":true,"pending":[],"results":[]}

These tests drive the real CLI against a stub display so the claim is pinned
where it was made -- in the exit code and the message -- not just in the JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEND = REPO / "display" / "send.py"

# What the display answers for a status poll, keyed by request id. A test sets
# STATUS to the body it wants and the id it is asked about.
STATUS: dict = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):        # keep the test output clean
        pass

    def _send(self, code: int, body: dict):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._send(200, {"request_id": "req-1", "pending": ["Kairos"], "complete": False})

    def do_GET(self):
        code, body = STATUS.get("response", (200, {}))
        self._send(code, body)


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class WaitForUnknownRequest(unittest.TestCase):
    """--wait against a display that does not know the request."""

    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()      # release the socket: a leak here breaks reruns

    def _run(self, response):
        STATUS.clear()
        STATUS["response"] = response
        with tempfile.TemporaryDirectory() as tmp:
            # Inherit the real environment and override only what the stub needs.
            # A hand-built minimal env looks tidier and is wrong: the child
            # interpreter needs the platform variables to start at all (on
            # Windows a stripped env fails with "_Py_HashRandomization_Init:
            # failed to get random numbers" before it reaches main()).
            env = os.environ.copy()
            # send.py builds its URL from GM_DISPLAY_PORT and always says
            # "localhost", so the stub has to listen there.
            env["GM_DISPLAY_PORT"] = str(self.port)
            proc = subprocess.run(
                [sys.executable, str(SEND), "--dice-request", "--character", "Kairos",
                 "--wait", "--wait-timeout", "5"],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
                env=env, cwd=tmp,
            )
        return proc

    def test_an_unknown_request_exits_nonzero_and_says_so(self):
        """The regression: this used to print "all rolls received" and exit 0."""
        proc = self._run((404, {"known": False, "complete": False,
                                "pending": [], "results": []}))
        combined = proc.stdout + proc.stderr
        self.assertNotIn("all rolls received", combined)
        self.assertNotEqual(proc.returncode, 0, "a request that never existed must not be a success")
        self.assertIn("never issued", combined)

    def test_a_server_that_answers_200_but_says_known_false_also_fails(self):
        """Belt and braces: an older display, or a proxy, that still returns 200
        for an unknown id must not be read as success either."""
        proc = self._run((200, {"known": False, "complete": True,
                                "pending": [], "results": []}))
        combined = proc.stdout + proc.stderr
        self.assertNotIn("all rolls received", combined)
        self.assertNotEqual(proc.returncode, 0)

    def test_a_known_finished_request_still_succeeds(self):
        """The fix must not break the path that works: a real request whose
        players have all rolled exits 0 and prints the results."""
        proc = self._run((200, {"known": True, "complete": True, "pending": [],
                                "results": ["Kairos rolls 1d20+6: 17 (hit)"]}))
        combined = proc.stdout + proc.stderr
        self.assertIn("all rolls received", combined)
        self.assertIn("17 (hit)", proc.stdout)
        self.assertEqual(proc.returncode, 0, combined)

    def test_a_known_pending_request_keeps_waiting_then_times_out(self):
        """Still-waiting is not success either: it times out and exits 2."""
        proc = self._run((200, {"known": True, "complete": False,
                                "pending": ["Kairos"], "results": []}))
        combined = proc.stdout + proc.stderr
        self.assertNotIn("all rolls received", combined)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("timeout", combined)


class DiagnosticsSurviveACp1252Console(unittest.TestCase):
    """Every diagnostic --wait prints goes to stderr, so stderr needs the UTF-8
    reconfigure as much as stdout does.

    On a Windows console (or any cp1252 parent) a message carrying an em dash
    raised UnicodeEncodeError, the call died before it could report anything,
    and the GM was left with a traceback instead of the line explaining that
    the request was unknown. Caught by the Windows CI jobs.
    """

    def test_send_reconfigures_both_streams(self):
        src = SEND.read_text(encoding="utf-8")
        self.assertRegex(src, r"(?s)def utf8_stdout\(\).*?\(sys\.stdout, sys\.stderr\)")

    def test_a_non_ascii_diagnostic_reaches_a_cp1252_parent_intact(self):
        """The behaviour, not the source text: run send.py under a cp1252
        PYTHONIOENCODING and confirm the em dash in the unknown-request
        message arrives as UTF-8 rather than killing the process."""
        port = _free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            STATUS.clear()
            STATUS["response"] = (404, {"known": False, "complete": False,
                                        "pending": [], "results": []})
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["GM_DISPLAY_PORT"] = str(port)
                env["PYTHONIOENCODING"] = "cp1252"
                proc = subprocess.run(
                    [sys.executable, str(SEND), "--dice-request", "--character", "Kairos",
                     "--wait", "--wait-timeout", "5"],
                    capture_output=True, text=True, encoding="utf-8", timeout=60,
                    env=env, cwd=tmp,
                )
        finally:
            server.shutdown()
            server.server_close()
        self.assertNotIn("UnicodeEncodeError", proc.stderr, proc.stderr)
        self.assertIn("never issued", proc.stderr, proc.stderr)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


class SendImportsUrlerror(unittest.TestCase):
    def test_urllib_error_is_imported(self):
        """The 404 branch catches urllib.error.HTTPError, which is a NameError
        unless the submodule is imported. Cheap guard against that regression."""
        src = SEND.read_text(encoding="utf-8")
        # assertRegex's third argument is the failure message, not a flag — the
        # multiline flag has to be inline or `^`/`$` anchor to the whole file.
        self.assertRegex(src, r"(?m)^import urllib\.error$")


if __name__ == "__main__":
    unittest.main()
