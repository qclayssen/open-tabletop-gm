"""The expanded Party Input panel, measured in a browser (follow-up to #127).

The panel sits in the right-hand rail under the settings rows. Expanded, it must
not overlap those rows or the reading column. Loads the real index.html through
the Flask app on an ephemeral port. Skipped when playwright or Chromium is absent.

Also pins, as static checks, the client wording for the recall and refusal paths.

The browser and the display server come from tests/_browser.py (W15).
"""
import re
import unittest

from tests._browser import (
    LAYOUT_PROBE,
    VIEWPORT_MATRIX,
    BrowserTestCase,
    assert_viewport_matrix,
)
from tests.display_sources import read_display_sources

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
    # The handlers are in display/static/display.js; the error region's id is
    # still markup in the template (W2).
    _src = read_display_sources()

    def test_recall_409_never_says_delivered(self):
        m = re.search(r"async function _recallAction.*?\n}\n", self._src.js, re.S)
        self.assertTrue(m)
        self.assertNotIn("'Delivered'", m.group(0))
        self.assertIn("No longer queued", m.group(0))

    def test_send_and_skip_surface_the_server_message(self):
        self.assertIn("_showInputError(", self._src.js)
        self.assertIn('id="input-error"', self._src.template)
        skip = re.search(r"async function _skipTurn.*?\n}\n", self._src.js, re.S).group(0)
        self.assertIn("_refusalMessage", skip)


class ExpandedPanelLayout(BrowserTestCase):
    module_name = "gm_display_app_pi_layout"

    def measure(self, w, h):
        page = self.open_page(size=(w, h), wait=600)
        page.click("#input-panel-header")
        page.wait_for_timeout(250)
        return page.evaluate(PROBE)

    def check(self, w, h):
        m = self.measure(w, h)
        self.assertGreaterEqual(m["top"], m["rowsBottom"] + 8, f"overlaps settings rows: {m}")
        self.assertLessEqual(m["bottom"], m["vh"], f"runs off the viewport: {m}")
        self.assertGreaterEqual(m["left"], m["readRight"], f"overlaps the reading column: {m}")

    def check_centred(self, w, h, classes):
        page = self.open_page(size=(w, h), wait=600)
        page.click("#input-panel-header")
        # The padding animates (transition: padding 0.4s), so wait it out.
        page.evaluate("cls => document.getElementById('text-scroll').classList.add(...cls)", classes)
        page.wait_for_timeout(700)
        m = page.evaluate(PROBE)
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

    def test_the_matrix_widths_too(self):
        """The panel check above runs at three desktop widths. The audit's matrix
        names three more, and 768 is the one the re-test measured: it is where
        the story column used to collapse to 84px.

        Two of check()'s three assertions carry over. The third, that the panel
        starts to the right of the reading column, is a desktop-only fact and is
        deliberately not repeated here: below the 1100px breakpoint the rails
        stop being side columns, so the panel is a fixed overlay pinned to the
        bottom of the window and shares horizontal space with the prose by
        design. Asserting it at 375 would be a new claim about the layout, not
        this panel's existing one, and W9 is the item that owns it.
        """
        for name, w, h in VIEWPORT_MATRIX:
            for expanded in (False, True):
                with self.subTest(viewport=name, input_panel="expanded" if expanded else "collapsed"):
                    self.clear_display_state()
                    page = self.open_page(size=(w, h), wait=600)
                    page.click("#input-panel-header")
                    page.wait_for_timeout(250)
                    assert_viewport_matrix(self, page.evaluate(LAYOUT_PROBE), where=name)
                    p = page.evaluate(PROBE)
                    self.assertLessEqual(p["bottom"], p["vh"],
                                         f"runs off the viewport ({name}): {p}")
                    self.assertGreaterEqual(p["top"], p["rowsBottom"] + 8,
                                            f"overlaps settings rows ({name}): {p}")


if __name__ == "__main__":
    unittest.main()
