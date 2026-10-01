"""Display hardening: XSS escaping, security headers, SSE sequencing and replay.

Server tests use the Flask test client and module internals. The XSS test loads
the real index.html in Chromium and is skipped when playwright or Chromium is
absent; a static check covers the same sinks without a browser.
"""
import importlib.util
import json
import pathlib
import queue
import re
import threading
import unittest

from tests.display_sources import read_display_sources

REPO = pathlib.Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:                                        # pragma: no cover
    HAVE_PLAYWRIGHT = False


def _load_app(name):
    spec = importlib.util.spec_from_file_location(
        name, str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StaticSinks(unittest.TestCase):
    # The sinks and the dispatcher are in display/static/display.js; the pill they
    # drive is still markup in the template (W2). Hence two readers, not one.
    _src = read_display_sources()

    def test_known_sinks_go_through_escape_helper(self):
        for needle in ("${_escHtml(name)}", "${_escHtml(label)}",
                       "_escHtml(line1.join", "${_escHtml(subLine)}",
                       "${_escHtml(val)}", "${_escHtml(a.notes||'')}"):
            self.assertIn(needle, self._src.js)

    def test_dispatcher_isolates_branches_and_shows_status(self):
        self.assertIn("const _try = (name, fn)", self._src.js)
        self.assertIn('id="conn-status" role="status" aria-live="polite"',
                      self._src.template)
        self.assertIn("'since=' + _lastSeq", self._src.js)


class Headers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_app("gm_display_hardening_headers")
        cls.client = cls.mod.app.test_client()

    def test_security_headers_on_index(self):
        r = self.client.get("/")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["Referrer-Policy"], "same-origin")
        self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", r.headers["Content-Security-Policy-Report-Only"])
        self.assertNotIn("Content-Security-Policy", r.headers)

    def test_no_cors_grant(self):
        r = self.client.get("/", headers={"Origin": "http://evil.example"})
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        r = self.client.options("/stream", headers={
            "Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    def test_foreign_origin_write_refused_same_origin_and_no_origin_pass(self):
        r = self.client.post("/player-input/skip", json={},
                             headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/player-input/skip", json={})
        self.assertNotIn(b"cross-origin", r.data)
        r = self.client.post("/player-input/skip", json={},
                             headers={"Origin": "http://localhost"})
        self.assertNotIn(b"cross-origin", r.data)

    def test_foreign_origin_write_allowed_with_lan_token(self):
        mod = _load_app("gm_display_hardening_lan")
        mod._lan_token = "tok"
        c = mod.app.test_client()
        bad = c.post("/player-input/skip", json={}, headers={"Origin": "http://x.example"})
        self.assertEqual(bad.status_code, 403)
        ok = c.post("/player-input/skip", json={},
                    headers={"Origin": "http://x.example", "X-DND-Token": "tok"})
        self.assertNotIn(b"cross-origin", ok.data)


class SseSequencing(unittest.TestCase):
    def setUp(self):
        self.mod = _load_app("gm_display_hardening_sse")

    def _client(self, size=256):
        q = queue.Queue(maxsize=size)
        with self.mod._clients_lock:
            self.mod._clients.append(q)
        return q

    def test_seq_increases_and_original_payload_untouched(self):
        q = self._client()
        src = {"stats": {"a": 1}}
        self.mod._broadcast(src)
        self.mod._broadcast({"text": "x"})
        a, b = q.get_nowait(), q.get_nowait()
        self.assertLess(a["seq"], b["seq"])
        self.assertNotIn("seq", src)

    def test_replay_since_returns_only_missed_narration(self):
        m = self.mod
        m._broadcast({"text": "one"})
        m._broadcast({"stats": {}})
        mark = m._seq
        m._broadcast({"text": "two"})
        m._broadcast({"text": "three"})
        with m._clients_lock:
            got = m._replay_since(mark)
            same = m._replay_since(m._seq)
            future = m._replay_since(m._seq + 5)
        self.assertEqual([p["text"] for p in got], ["two", "three"])
        self.assertEqual(same, [])
        self.assertIsNone(future)

    def test_replay_gap_when_buffer_rolled(self):
        m = self.mod
        first = m._seq
        m._broadcast({"text": "old"})
        for i in range(m._SEQ_BUFFER_MAX + 2):
            m._broadcast({"text": f"n{i}"})
        with m._clients_lock:
            self.assertIsNone(m._replay_since(first))
            self.assertIsNotNone(m._replay_since(m._seq - 3))
            self.assertEqual(len(m._seq_buffer), m._SEQ_BUFFER_MAX)

    def test_full_queue_client_is_closed_not_silently_dropped(self):
        m = self.mod
        slow = self._client(size=2)
        fast = self._client()
        for i in range(3):
            m._broadcast({"text": str(i)})
        with m._clients_lock:
            self.assertNotIn(slow, m._clients)
            self.assertIn(fast, m._clients)
        self.assertIs(slow.get_nowait(), m._CLOSE)
        self.assertEqual(fast.qsize(), 3)

    def test_stream_resume_sends_hello_and_skips_full_replay(self):
        m = self.mod
        m._broadcast({"text": "seen"})
        mark = m._seq
        m._broadcast({"text": "missed"})
        r = m.app.test_client().get(f"/stream?since={mark}&epoch={m._EPOCH}",
                                    buffered=False)
        chunks = []
        for chunk in r.response:
            chunks.append(chunk.decode("utf-8"))
            if "missed" in chunks[-1]:
                break
        r.close()
        body = "".join(chunks)
        payloads = [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ")]
        self.assertTrue(payloads[0]["hello"]["resumed"])
        self.assertEqual(payloads[0]["hello"]["epoch"], m._EPOCH)
        self.assertNotIn("replay_batch", [k for p in payloads for k in p])
        self.assertIn("missed", body)
        self.assertNotIn('"seen"', body)
        self.assertRegex(body, r"id: \d+\n")


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class XssInBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from werkzeug.serving import make_server
        mod = _load_app("gm_display_hardening_xss")
        try:
            cls.httpd = make_server("127.0.0.1", 0, mod.app)
        except OSError as exc:
            raise unittest.SkipTest(f"cannot bind: {exc}") from exc
        cls.port = cls.httpd.server_port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:
            cls.httpd.shutdown()
            raise unittest.SkipTest(f"chromium is not available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_hostile_names_do_not_execute(self):
        evil = '<img src=x onerror="window.__pwned=(window.__pwned||0)+1">'
        stats = {
            "players": [{"name": evil, "race": evil, "class": evil + "/" + evil,
                         "background": evil, "level": 1,
                         "hp": {"current": 5, "max": 9}}],
            "turn_order": {"order": [evil, "Goblin"], "current": "Goblin", "round": 1},
        }
        page = self.browser.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        page.wait_for_timeout(500)
        page.evaluate("s => updateStats(s)", stats)
        page.wait_for_timeout(500)
        self.assertIsNone(page.evaluate("window.__pwned"))
        turn_html = page.evaluate("document.getElementById('sb-turn-list').innerHTML")
        self.assertIn("&lt;img", turn_html)
        ident = page.evaluate("document.querySelector('.sb-identity').innerHTML")
        self.assertIn("&lt;img", ident)
        self.assertEqual(page.evaluate("document.querySelectorAll('.sb-identity img').length"), 0)
        page.close()

    def test_connection_pill_reflects_reconnect(self):
        page = self.browser.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        page.wait_for_timeout(500)
        self.assertEqual(page.get_attribute("#conn-status", "data-state"), "connected")
        page.evaluate("_setConnStatus('reconnecting', 2)")
        self.assertIn("Reconnecting (attempt 2)", page.inner_text("#conn-status"))
        self.assertEqual(page.get_attribute("#conn-status", "aria-live"), "polite")
        page.close()


if __name__ == "__main__":
    unittest.main()
