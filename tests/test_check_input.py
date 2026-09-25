"""check_input.py must actually reach the queue it is meant to drain.

Two bugs made queued player input disappear in --lan mode:

1. The drain request sent the token as `X-Token`, but gm-display-app.py's
   `_token_ok()` reads `X-DND-Token`. With a LAN token set, every drain was
   rejected with 403.
2. The fallback then read `.input_queue`, a plain-text file owned by
   wrapper.py, and parsed it as JSON. That failed silently, so nothing was
   printed and the real queue (`player_input.json`) was never read.

These tests pin the header name and fallback file to what the app uses,
reading them from the app source so the two cannot drift apart again.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "display"
APP_SRC = (DISPLAY / "gm-display-app.py").read_text(encoding="utf-8")


def _load_check_input():
    spec = importlib.util.spec_from_file_location("check_input_under_test",
                                                  str(DISPLAY / "check_input.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_input_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _app_token_header() -> str:
    body = re.search(r"def _token_ok\(\).*?\n(?=\S)", APP_SRC, re.S)
    assert body, "_token_ok not found in gm-display-app.py"
    m = re.search(r'request\.headers\.get\("([^"]+)"', body.group(0))
    assert m, "_token_ok no longer reads a header"
    return m.group(1)


def _app_input_file() -> str:
    m = re.search(r'INPUT_FILE\s*=\s*os\.path\.join\(_DISPLAY_DIR,\s*"([^"]+)"\)', APP_SRC)
    assert m, "INPUT_FILE not found in gm-display-app.py"
    return m.group(1)


class CheckInputTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_check_input()
        self.tmp = Path(tempfile.mkdtemp())
        # Keep directives out of the output and away from the real display dir.
        self.mod.NARRATION_TARGET = self.tmp / "narration_target"
        self.mod.ROLL_PREFS = self.tmp / "roll_prefs.json"
        self.mod.TOKEN_FILE = self.tmp / ".token"
        self.mod.QUEUE_FILE = self.tmp / "player_input.json"
        self.mod.READY_FILE = self.tmp / ".input_queue"
        self.mod.CONSUMED_URL = "http://127.0.0.1:9/unreachable"

    def _run(self) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.main()
        return out.getvalue()

    def test_drain_sends_the_header_the_app_checks(self):
        header = _app_token_header()
        token = "s3cret"
        self.mod.TOKEN_FILE.write_text(token, encoding="utf-8")
        entries = [{"character": "Kairos", "text": "casts Fire Bolt at the frog"}]

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.headers.get(header) != token:
                    self.send_response(403)
                    self.end_headers()
                    return
                body = json.dumps(entries).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            self.mod.DRAIN_URL = f"http://127.0.0.1:{server.server_address[1]}/player-input/drain"
            self.assertIn("[Kairos]: casts Fire Bolt at the frog", self._run())
        finally:
            server.shutdown()
            server.server_close()

    def test_fallback_file_is_the_one_the_app_persists(self):
        self.assertEqual(_load_check_input().QUEUE_FILE.name, _app_input_file())

    def test_fallback_reads_and_clears_the_persisted_queue(self):
        self.mod.DRAIN_URL = "http://127.0.0.1:9/unreachable"
        self.mod.QUEUE_FILE.write_text(
            json.dumps([{"character": "Kairos", "text": "moves to D5"}]), encoding="utf-8")
        self.assertIn("[Kairos]: moves to D5", self._run())
        self.assertEqual(json.loads(self.mod.QUEUE_FILE.read_text(encoding="utf-8")), [])


class ReadyPanelQueue(unittest.TestCase):
    """Stage + Ready on the display's Party Input panel writes `.input_queue`
    (the file wrapper.py and autorun_wait.py read), not the drain queue. In a
    plain `claude` session neither runs, so check_input.py must read it too or
    the player's typed action never reaches the GM."""

    def setUp(self):
        self.mod = _load_check_input()
        self.tmp = Path(tempfile.mkdtemp())
        self.mod.NARRATION_TARGET = self.tmp / "narration_target"
        self.mod.ROLL_PREFS = self.tmp / "roll_prefs.json"
        self.mod.TOKEN_FILE = self.tmp / ".token"
        self.mod.QUEUE_FILE = self.tmp / "player_input.json"
        self.mod.READY_FILE = self.tmp / ".input_queue"
        self.mod.DRAIN_URL = "http://127.0.0.1:9/unreachable"
        self.mod.CONSUMED_URL = "http://127.0.0.1:9/unreachable"

    def test_ready_file_is_the_one_the_app_writes(self):
        m = re.search(r'QUEUE_FILE\s*=\s*os\.path\.join\(_DISPLAY_DIR,\s*"([^"]+)"\)', APP_SRC)
        self.assertEqual(_load_check_input().READY_FILE.name, m.group(1))

    def test_ready_actions_are_printed_once(self):
        self.mod.READY_FILE.write_text(
            "[Kairos]: I watch the innkeeper's face.\n[Mira]: skips their turn",
            encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.main()
        self.assertIn("[Kairos]: I watch the innkeeper's face.", out.getvalue())
        self.assertIn("[Mira]: skips their turn", out.getvalue())
        self.assertFalse(self.mod.READY_FILE.exists())
        again = io.StringIO()
        with contextlib.redirect_stdout(again):
            self.mod.main()
        self.assertEqual(again.getvalue(), "")


class NoDoubleDelivery(unittest.TestCase):
    """If the display answers with an error it still owns the queue, so the
    file must not be read (the app would deliver the same actions again)."""

    def test_http_error_does_not_fall_back_to_the_file(self):
        mod = _load_check_input()
        tmp = Path(tempfile.mkdtemp())
        mod.NARRATION_TARGET, mod.ROLL_PREFS = tmp / "n", tmp / "r"
        mod.TOKEN_FILE, mod.QUEUE_FILE = tmp / ".token", tmp / "player_input.json"
        mod.READY_FILE, mod.CONSUMED_URL = tmp / ".input_queue", "http://127.0.0.1:9/unreachable"
        queued = json.dumps([{"character": "Kairos", "text": "moves to D5"}])
        mod.QUEUE_FILE.write_text(queued, encoding="utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(500)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            mod.DRAIN_URL = f"http://127.0.0.1:{server.server_address[1]}/player-input/drain"
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                mod.main()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(out.getvalue(), "")
        self.assertIn("500", err.getvalue())
        self.assertEqual(mod.QUEUE_FILE.read_text(encoding="utf-8"), queued)


if __name__ == "__main__":
    unittest.main()
