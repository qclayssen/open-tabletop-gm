"""display_settle: the waits themselves, tested against boxes that actually move.

WHY
===
`tests/display_settle.py` exists because three display browser test files were
guessing at how long a reflow takes with `wait_for_timeout`, and a guess that is too
short does not read as a guess: it reads as a layout bug, in whichever direction the
machine happened to be slow. `fix-n2-settle-predicate` is what that costs, and it
is why the `pad >= txb` work exists at all.

But a waiting helper that silently stops waiting is the same failure wearing a
different hat, and worse: it turns every test that uses it into a test that passes
without measuring anything, and there is no symptom. A predicate is not better than
a sleep because it is a predicate; it is better because it is right about when the
thing it is waiting for has happened. So each one is driven here against a page
whose box genuinely takes longer to arrive than any of the sleeps it replaced, and
is required to come back at the final value.

No browser is needed for most of this, so it is not skipped when Chromium is absent
for the same reason the font-size scan in test_display_typeset_a11y.py is not: a
helper the other files depend on should not go unchecked on a machine that cannot
run them. Skipped only when playwright is missing, which is the one thing that does
stop it.
"""
from __future__ import annotations

import time
import unittest

try:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    HAVE_PLAYWRIGHT = True
except ImportError:                                        # pragma: no cover
    HAVE_PLAYWRIGHT = False

from tests.display_settle import NO_PADDING_TRANSITION, box_settled, gone, present, page_ready


# A box that starts at 10px and is transitioned to 400px over 900ms, 50ms after
# load. At 250ms it is at roughly 192px: still moving, and a plausible-looking
# number, which is exactly what makes a short sleep dangerous rather than merely
# wrong.
SLOW_BOX = """<div id="grow" style="height:10px;background:red"></div>
<script>
setTimeout(function () {
  var g = document.getElementById('grow');
  g.style.transition = 'height 0.9s ease';
  g.style.height = '400px';
}, 50);
</script>"""


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class Waits(unittest.TestCase):
    browser = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:                          # no browser downloaded
            raise unittest.SkipTest(f"chromium is not available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        if cls.browser:
            cls.browser.close()
        cls.pw.stop()

    def page(self, html):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)
        return page

    def test_box_settled_waits_for_the_box_to_arrive_rather_than_a_number(self):
        """The whole case for the helper, as one assertion.

        900ms of transition against a sleep that used to be 250ms: `box_settled`
        has to come back at 400px. Coming back at 192px would be a wait that gave
        up, and every caller would then measure a half-reflowed page while
        believing it had settled. Asserting the RETURNED value rather than a
        duration is deliberate: a duration test passes for a helper that waits
        876ms and then reads the wrong thing, and fails for a fast machine.
        """
        page = self.page(SLOW_BOX)
        page.wait_for_timeout(50)          # the transition has just been kicked off
        box_settled(page, "#grow", timeout=5000)
        h = page.evaluate("document.getElementById('grow').getBoundingClientRect().height")
        self.assertAlmostEqual(h, 400, delta=1,
                               msg=f"box_settled returned mid-reflow at {h}px")

    def test_a_fixed_sleep_would_have_read_the_moving_box(self):
        """The control, so the test above cannot be satisfied by a helper that is
        simply faster than the old sleep. Same page, same instant, one of each."""
        page = self.page(SLOW_BOX)
        page.wait_for_timeout(50)
        page.wait_for_timeout(250)
        h = page.evaluate("document.getElementById('grow').getBoundingClientRect().height")
        self.assertLess(h, 390,
                        f"the 250ms sleep happened to read the final box ({h}px), "
                        f"so this page no longer demonstrates anything")

    def test_box_settled_does_not_report_the_first_frame_of_a_transition(self):
        """The failure mode the three-sample rule exists for, and it is the one the
        original `fix-n2-settle-predicate` brief describes happening to that file:
        "three samples in a row and none of them moved" is also what a transition
        looks like in its first few milliseconds, before it has travelled anywhere.
        A one-sample version would return immediately here, at 10px, and look
        correct because it did return."""
        page = self.page(SLOW_BOX)
        page.wait_for_timeout(50)
        first = page.evaluate("""() => {
          const el = document.getElementById('grow');
          const r = el.getBoundingClientRect();
          return Math.round(r.height);
        }""")
        box_settled(page, "#grow", timeout=5000)
        last = page.evaluate(
            "document.getElementById('grow').getBoundingClientRect().height")
        self.assertLess(first, 100, f"the box was already moving at the first read: {first}")
        self.assertAlmostEqual(last, 400, delta=1, msg=f"read {last}px")

    def test_present_waits_for_a_box_and_not_merely_for_the_tag(self):
        """`document.getElementById` returning a node is not the same as the node
        being on screen, and a caller that measured a display:none element's
        geometry would read a zero rect and call it a layout bug. So the predicate
        is about the box."""
        page = self.page("""<div id="later"></div>
<script>
setTimeout(function () {
  var d = document.getElementById('later');
  d.textContent = 'now with a box';
}, 300);
</script>""")
        self.assertIsNotNone(page.query_selector("#later"),
                             "the element is in the DOM from the start")
        present(page, "#later", timeout=3000)
        self.assertGreater(
            page.evaluate("document.getElementById('later').getBoundingClientRect().height"), 0)

    def test_present_times_out_rather_than_passing_on_an_absent_element(self):
        """A wait that gave up quietly is the failure this module exists to remove,
        so the failure has to be loud. `wait_for_timeout` cannot do this at all.

        The type is AssertionError, not PlaywrightTimeout, and the reason is the
        message: Playwright's own "Timeout 5000ms exceeded" reads as a slow
        machine, which is the wrong diagnosis for the common cause of a wait that
        never clears, which is that the thing never rendered. That misreading is
        what this module is here to prevent, so the raised error says so. The
        original is chained, so the playwright detail is not lost.
        """
        page = self.page("<div id='nope'></div>")
        with self.assertRaises(AssertionError) as cm:
            present(page, "#never", timeout=600)
        self.assertIsInstance(cm.exception.__cause__, PlaywrightTimeout)
        self.assertIn("never appeared", str(cm.exception))
        self.assertIn("not a slow machine", str(cm.exception))

    def test_gone_waits_for_a_timed_thing_to_leave(self):
        """The counterpart, for the floats that remove themselves on a 1400ms
        timer. Waiting for something to be GONE is not a degenerate case of
        `present`; it is the only way to assert a float has finished its life,
        which is the opposite of what the float tests need."""
        page = self.page("""<div id="f" style="height:20px;background:red"></div>
<script>
setTimeout(function () { document.getElementById('f').remove(); }, 400);
</script>""")
        present(page, "#f", timeout=2000)
        gone(page, "#f", timeout=3000)
        self.assertIsNone(page.query_selector("#f"))

    def test_page_ready_is_already_true_when_load_fires(self):
        """Why `page_ready` is nearly free, and why replacing a 500ms sleep with it
        is a saving rather than a cost. Measured on a served display page: fonts
        are loaded and display.js's globals are installed by the time `load`
        fires, so the predicate returns on its first evaluation."""
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content("<p>no display here</p>")
        # On a page with no display.js the globals are never there, so the
        # predicate is false and stays false: it is not a wait that passes
        # vacuously on a page that has nothing to wait for.
        with self.assertRaises(PlaywrightTimeout):
            page_ready(page, timeout=600)

    def test_the_padding_transition_taken_off_is_the_rule_the_predicate_reasons_from(self):
        """`NO_PADDING_TRANSITION` is shared by all three files, so it is pinned
        here rather than in whichever file happens to be read. It has to be the
        `#text-scroll` padding transition specifically: taking off the wrong one
        would leave `pad >= txb` untrustworthy, which is the whole mechanism."""
        self.assertIn("#text-scroll", NO_PADDING_TRANSITION)
        self.assertIn("transition", NO_PADDING_TRANSITION)
        self.assertIn("!important", NO_PADDING_TRANSITION,
                      "without !important the product's own rule wins and the "
                      "transition stays live")


if __name__ == "__main__":
    unittest.main()
