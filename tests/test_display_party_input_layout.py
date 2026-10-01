"""The expanded Party Input panel, measured in a browser (follow-up to #127).

The panel sits in the right-hand rail under the settings rows. Expanded, it must
not overlap those rows or the reading column. Loads the real index.html through
the Flask app on an ephemeral port. Skipped when playwright or Chromium is absent.

Also pins, as static checks, the client wording for the recall and refusal paths.
"""
import importlib.util
import pathlib
import re
import threading
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
INDEX = REPO / "display" / "templates" / "index.html"

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:                                        # pragma: no cover
    HAVE_PLAYWRIGHT = False

PROBE = """() => {
  const r = e => e.getBoundingClientRect();
  const panel = r(document.getElementById('input-panel'));
  const rows = [...document.querySelectorAll('#audio-controls .audio-row')]
    .filter(e => e.offsetHeight).map(e => r(e).bottom);
  const ts = document.getElementById('text-scroll');
  const cs = getComputedStyle(ts);
  const readLeft = parseFloat(cs.paddingLeft);
  const readRight = innerWidth - parseFloat(cs.paddingRight);
  return {top: panel.top, bottom: panel.bottom, left: panel.left,
          rowsBottom: Math.max(...rows), readLeft, readRight,
          readCentre: (readLeft + readRight) / 2, vw: innerWidth, vh: innerHeight};
}"""


class ClientWording(unittest.TestCase):
    src = INDEX.read_text(encoding="utf-8")

    def test_recall_409_never_says_delivered(self):
        m = re.search(r"async function _recallAction.*?\n}\n", self.src, re.S)
        self.assertTrue(m)
        self.assertNotIn("'Delivered'", m.group(0))
        self.assertIn("No longer queued", m.group(0))

    def test_send_and_skip_surface_the_server_message(self):
        self.assertIn("_showInputError(", self.src)
        self.assertIn('id="input-error"', self.src)
        skip = re.search(r"async function _skipTurn.*?\n}\n", self.src, re.S).group(0)
        self.assertIn("_refusalMessage", skip)


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class ExpandedPanelLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from werkzeug.serving import make_server
        spec = importlib.util.spec_from_file_location(
            "gm_display_app_pi_layout", str(REPO / "display" / "gm-display-app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
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

    def measure(self, w, h):
        page = self.browser.new_page(viewport={"width": w, "height": h})
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        page.wait_for_timeout(600)
        page.click("#input-panel-header")
        page.wait_for_timeout(250)
        out = page.evaluate(PROBE)
        page.close()
        return out

    def check(self, w, h):
        m = self.measure(w, h)
        self.assertGreaterEqual(m["top"], m["rowsBottom"] + 8, f"overlaps settings rows: {m}")
        self.assertLessEqual(m["bottom"], m["vh"], f"runs off the viewport: {m}")
        self.assertGreaterEqual(m["left"], m["readRight"], f"overlaps the reading column: {m}")

    def check_centred(self, w, h, classes):
        page = self.browser.new_page(viewport={"width": w, "height": h})
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        page.wait_for_timeout(600)
        page.click("#input-panel-header")
        # The padding animates (transition: padding 0.4s), so wait it out.
        page.evaluate("cls => document.getElementById('text-scroll').classList.add(...cls)", classes)
        page.wait_for_timeout(700)
        m = page.evaluate(PROBE)
        page.close()
        self.assertAlmostEqual(m["readCentre"], m["vw"] / 2, delta=2,
                               msg=f"reading column is not centred ({classes}): {m}")
        self.assertGreaterEqual(m["left"], m["readRight"],
                                f"party input overlaps the prose ({classes}): {m}")

    def test_reading_column_is_centred_in_every_rail_state(self):
        for w, h in ((1440, 900), (1280, 720), (1920, 1080)):
            for classes in ([], ["sidebar-hidden"], ["controls-hidden"],
                            ["sidebar-hidden", "controls-hidden"]):
                with self.subTest(size=(w, h), classes=classes):
                    self.check_centred(w, h, classes)

    def test_1440x900(self):
        self.check(1440, 900)

    def test_1280x720(self):
        self.check(1280, 720)


if __name__ == "__main__":
    unittest.main()
