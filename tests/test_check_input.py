"""check_input.py drains the same shared queue as wrapper.py.

The HTTP route and offline fallback both claim `.input_queue` with the shared
queue_claim primitive, so competing consumers cannot double-deliver actions.
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


def _app_queue_file() -> str:
    m = re.search(r'QUEUE_FILE\s*=\s*os\.path\.join\(_DISPLAY_DIR,\s*"([^"]+)"\)', APP_SRC)
    assert m, "QUEUE_FILE not found in gm-display-app.py"
    return m.group(1)


class CheckInputTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_check_input()
        self.tmp = Path(tempfile.mkdtemp())
        # Keep directives out of the output and away from the real display dir.
        self.mod.NARRATION_TARGET = self.tmp / "narration_target"
        self.mod.ROLL_PREFS = self.tmp / "roll_prefs.json"
        self.mod.TOKEN_FILE = self.tmp / ".token"
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

    def test_fallback_uses_the_shared_file_the_app_writes(self):
        self.assertEqual(_load_check_input().READY_FILE.name, _app_queue_file())

    def test_fallback_claims_and_clears_the_shared_queue(self):
        self.mod.DRAIN_URL = "http://127.0.0.1:9/unreachable"
        self.mod.READY_FILE.write_text("[Kairos]: moves to D5\n", encoding="utf-8")
        self.assertIn("[Kairos]: moves to D5", self._run())
        self.assertFalse(self.mod.READY_FILE.exists())


class ReadyPanelQueue(unittest.TestCase):
    """The wrapper and check_input both consume `.input_queue` exactly once."""

    def setUp(self):
        self.mod = _load_check_input()
        self.tmp = Path(tempfile.mkdtemp())
        self.mod.NARRATION_TARGET = self.tmp / "narration_target"
        self.mod.ROLL_PREFS = self.tmp / "roll_prefs.json"
        self.mod.TOKEN_FILE = self.tmp / ".token"
        self.mod.READY_FILE = self.tmp / ".input_queue"
        self.mod.DRAIN_URL = "http://127.0.0.1:9/unreachable"
        self.mod.CONSUMED_URL = "http://127.0.0.1:9/unreachable"

    def test_ready_file_is_the_one_the_app_writes(self):
        self.assertEqual(_load_check_input().READY_FILE.name, _app_queue_file())

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


class NonUtf8Console(unittest.TestCase):
    """On Windows the GM's shell reads stdout through a cp1252 pipe. A roll or
    an action with a character outside cp1252 ("→" in an advantage roll) must
    come out as UTF-8, not crash the script."""

    def _cp1252_stdout(self):
        buf = io.BytesIO()
        return buf, io.TextIOWrapper(buf, encoding="cp1252", write_through=True)

    def test_check_input_prints_any_character(self):
        mod = _load_check_input()
        tmp = Path(tempfile.mkdtemp())
        mod.NARRATION_TARGET, mod.ROLL_PREFS = tmp / "n", tmp / "r"
        mod.TOKEN_FILE = tmp / ".token"
        mod.READY_FILE, mod.DRAIN_URL = tmp / ".input_queue", "http://127.0.0.1:9/unreachable"
        mod.CONSUMED_URL = "http://127.0.0.1:9/unreachable"
        mod.READY_FILE.write_text("[Kairos]: I point → north", encoding="utf-8")
        buf, out = self._cp1252_stdout()
        real = sys.stdout
        sys.stdout = out
        try:
            mod.main()
        finally:
            sys.stdout = real
        self.assertIn("[Kairos]: I point → north", buf.getvalue().decode("utf-8"))

    def test_send_prints_any_character(self):
        spec = importlib.util.spec_from_file_location("send_under_test", str(DISPLAY / "send.py"))
        send = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(send)
        buf, out = self._cp1252_stdout()
        real = sys.stdout
        sys.stdout = out
        try:
            send.utf8_stdout()
            print("Kairos rolls 1d20: [12, 7] → keep 12 (advantage) = 12")
            sys.stdout.flush()
        finally:
            sys.stdout = real
        self.assertIn("→ keep 12", buf.getvalue().decode("utf-8"))


class NoDoubleDelivery(unittest.TestCase):
    """A refused drain must neither strand nor repeat an action. There is one
    queue and the local claim already owns what it took, so the actions print
    once and the next run prints nothing."""

    def test_http_error_delivers_the_claimed_queue_once(self):
        mod = _load_check_input()
        tmp = Path(tempfile.mkdtemp())
        mod.NARRATION_TARGET, mod.ROLL_PREFS = tmp / "n", tmp / "r"
        mod.TOKEN_FILE = tmp / ".token"
        mod.READY_FILE, mod.CONSUMED_URL = tmp / ".input_queue", "http://127.0.0.1:9/unreachable"
        queued = "[Kairos]: moves to D5\n"
        mod.READY_FILE.write_text(queued, encoding="utf-8")

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
        self.assertEqual(out.getvalue(), "[Kairos]: moves to D5\n")
        self.assertIn("500", err.getvalue())
        self.assertFalse(mod.READY_FILE.exists())
        mod.DRAIN_URL = "http://127.0.0.1:9/unreachable"
        again = io.StringIO()
        with contextlib.redirect_stdout(again):
            mod.main()
        self.assertEqual(again.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
