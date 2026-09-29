"""The combat panel's real layout, measured in a browser (Phase 4b).

The unit-level numbers in test_display_tactics_ui.py pin what the script and
the stylesheet say; this pins what a browser actually did with them, at the
two viewports the panel is built for. It drives display/evidence-panel.html,
which feeds tactics.js the same shape sync.snapshot returns, and asserts the
three must-haves of Phase 4b:

  2  a phone fits the board and keeps End turn on a sticky bar
  4  side is shown by the shape of the frame, not by colour alone
  6  a shared table display draws squares of at least 40px

Skipped when playwright or its Chromium is not installed; nothing here is
needed to run the rest of the suite.
"""
import http.server
import json
import pathlib
import socketserver
import threading
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
HARNESS = REPO / "display" / "evidence-panel.html"
PORT = 8749

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:                                        # pragma: no cover
    HAVE_PLAYWRIGHT = False

VIEWPORTS = {"table": (1440, 900), "phone": (390, 844)}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _serve():
    handler = lambda *a, **k: _Quiet(*a, directory=str(REPO / "display"), **k)
    # allow_reuse_address before binding: a hardcoded PORT left bound by a previous
    # run (or a crashed one) would otherwise fail this bind outright and take all
    # 8 tests in the class with it at setUpClass.
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# The numbers the panel has to hit, as a browser must report them.
PROBE = """() => {
  const bd = document.getElementById('tx-board'), svg = bd.querySelector('svg'),
        bar = document.getElementById('tx-actions'), leads = document.getElementById('tx-leads'),
        end = bar.querySelector('[data-tx=end]'), panel = document.getElementById('tx-panel');
  const W = window.__SNAP.grid.rows[0].length;
  const r = e => e.getBoundingClientRect();
  const inBar = e => { const a = r(e), b = r(bar);
    return a.height > 0 && a.top >= b.top - 1 && a.bottom <= b.bottom + 1; };
  const frames = {};
  for (const g of bd.querySelectorAll('.tx-tok')) {
    const side = (window.__SNAP.tokens.find(t => t.id === g.getAttribute('data-id')) || {}).side;
    frames[side] = (g.querySelector('path') ? 'octagon' : 'circle');
  }
  return {
    cell: Math.round(r(svg).width / W * 10) / 10,
    boardW: Math.round(bd.clientWidth), svgW: Math.round(r(svg).width),
    boardScrollsX: bd.scrollWidth > bd.clientWidth + 1,
    barSticky: getComputedStyle(bar).position,
    barAtPanelBottom: Math.round(r(bar).bottom) >= Math.round(r(panel).bottom) - 12,
    leadsPinned: inBar(leads), endTurnInBar: inBar(end),
    endTurnOnScreen: r(end).bottom <= innerHeight && r(end).top >= 0,
    buttonH: Math.round(r(leads.querySelector('button')).height),
    chipStripes: [...new Set([...document.querySelectorAll('.tx-chip')]
      .map(c => getComputedStyle(c).borderLeftWidth))],
    frames,
  };
}"""

SPELLS = ("() => { window.__spells = Array.from({length: 14}, (_, i) => ({name: 'Spell ' + i,"
          " level: i % 4, mode: 'save', area: {shape: 'sphere', size: 20},"
          " targeting: 'single', range: 60, ok: true}));"
          " window.fetch = u => u === '/combat/do'"
          " ? Promise.resolve({json: () => Promise.resolve({text: '', result: {spells: window.__spells}})})"
          " : Promise.reject(new Error('offline')); }")


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class MeasuredLayout(unittest.TestCase):
    server = None
    playwright = None
    browser = None

    @classmethod
    def setUpClass(cls):
        if not HARNESS.exists():
            raise unittest.SkipTest("display/evidence-panel.html is missing")
        # A port we cannot bind is an environment problem, not a layout failure.
        # Raising here would take all 8 tests in the class with it and read as a
        # broken panel; skipping says only that the measurement could not run.
        try:
            cls.server = _serve()
        except OSError as exc:
            raise unittest.SkipTest(f"cannot bind {PORT}: {exc}") from exc
        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:                          # no browser downloaded
            cls.tearDownClass()
            raise unittest.SkipTest(f"chromium is not available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        if cls.browser:
            cls.browser.close()
        if cls.playwright:
            cls.playwright.stop()
        if cls.server:
            cls.server.shutdown()
            # shutdown() stops the serve loop; it does NOT close the listening
            # socket. Without server_close() the port stays bound after the
            # process exits and the next run's bind fails with Errno 48.
            cls.server.server_close()
            cls.server = None

    def panel(self, name, spell_list=False):
        w, h = VIEWPORTS[name]
        page = self.browser.new_page(viewport={"width": w, "height": h})
        page.goto(f"http://127.0.0.1:{PORT}/evidence-panel.html", wait_until="load")
        page.wait_for_function("window.__ready === true", timeout=15000)
        page.wait_for_timeout(200)
        if spell_list:
            page.evaluate(SPELLS)
            page.click("#tx-leads button:has-text('Cast')")
            page.wait_for_timeout(500)
        out = page.evaluate(PROBE)
        page.close()
        return out

    # ── must-have 6: a table display reads from across the table ───────────
    def test_a_table_display_draws_squares_of_at_least_40px(self):
        got = self.panel("table")
        self.assertGreaterEqual(got["cell"], 40)

    def test_a_table_display_keeps_the_map_bigger_than_it_was(self):
        """The old rule drew 28px squares in a fixed 560px box."""
        got = self.panel("table")
        self.assertGreaterEqual(got["svgW"], 20 * 40)

    # ── must-have 2: the phone fits, and the bar stays put ─────────────────
    def test_a_phone_fits_the_whole_board_across(self):
        got = self.panel("phone")
        self.assertFalse(got["boardScrollsX"], "the board still runs off the side")
        self.assertLessEqual(got["svgW"], got["boardW"])

    def test_a_phone_pins_the_bar_to_the_bottom_of_the_panel(self):
        got = self.panel("phone")
        self.assertEqual(got["barSticky"], "sticky")
        self.assertTrue(got["barAtPanelBottom"])
        self.assertTrue(got["endTurnOnScreen"])

    def test_a_phone_keeps_end_turn_on_screen_with_a_long_spell_list(self):
        """The bug: the spell list wrapped the bar to three rows and End turn
        fell under the fold."""
        got = self.panel("phone", spell_list=True)
        self.assertTrue(got["leadsPinned"], "the lead row scrolled out of the bar")
        self.assertTrue(got["endTurnInBar"], "End turn scrolled out of the bar")
        self.assertTrue(got["endTurnOnScreen"])

    def test_a_phone_uses_bigger_touch_targets_than_a_table_display(self):
        self.assertGreaterEqual(self.panel("phone")["buttonH"], 40)
        self.assertEqual(self.panel("table")["buttonH"], 36)   # a mouse, not a thumb

    # ── must-have 4: side is a shape, not a colour ─────────────────────────
    def test_enemy_tokens_are_notched_and_others_are_round(self):
        frames = self.panel("table")["frames"]
        self.assertEqual(frames.get("enemy"), "octagon")
        for side in ("pc", "other"):
            self.assertEqual(frames.get(side), "circle", f"{side} should be round")

    def test_the_initiative_strip_marks_side_with_a_stripe(self):
        self.assertEqual(self.panel("table")["chipStripes"], ["4px"])


if __name__ == "__main__":
    unittest.main()
