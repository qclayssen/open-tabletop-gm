"""The dice-pending badge and the story's top inset, measured in a browser.

THE DEFECT THIS PINS
====================
`display.css` answered "the story starts below the waiting badge" with a fixed
`padding-top: 124px`, and it was wrong in three separate ways at once.

1. It was 3px short. One outstanding request draws a 71px box under a 56px
   offset, so the badge's bottom edge is 127px and the prose began three pixels
   inside it. Nothing overlapped at that size, which is why every geometry
   assertion in the suite was green.

2. A restated constant beat the combat panel's own inset. `:has()` contributes
   the specificity of its most specific argument, so
   `body:has(#dice-pending-badge.visible) #text-scroll` computes to two ids
   against `body.tx-on #text-scroll`'s one, and won outright whatever the load
   order. With the panel up and a roll outstanding, `--tx-bottom` measured 698px
   and the story still started at 124, so the board covered the prose for as
   long as anything was pending. The badge is up at exactly that moment: it is
   what says the fight is waiting on a roll.

3. It was smaller than the page's own resting inset. Below the 1100px
   breakpoint the base inset is 172px, so a one-request badge on a 390px page
   computed 155px against that page's 172 and the story jumped UP 17px when a
   roll was requested. A badge may only ever push the story down.

The fix measures the badge the way tactics.js already measures the panel
(`--dpb-bottom`, published by display.js) and takes the largest of the three
inputs: the panel's edge, the badge's edge and the resting inset. The tests
below assert the three failures above, in that order, at the widths where each
one shows.

WHAT IS NOT ASSERTED
====================
No pixel count. The gap the stylesheet leaves under each overlay is its own
business, and pinning it here would be pinning a number that the layout is free
to change. Every assertion is a relationship: the story clears what is over it,
and the badge never raises it above where it already was. The one place a
number appears is the 17px regression, and it is written as a relationship too
("a badge may not move the story up") rather than as a computed figure.
"""
import pathlib
import re
import unittest

from tests._browser import BrowserTestCase
from tests.display_settle import calm, present, settle
from tests.display_sources import read_display_sources
from tests.test_display_retest_findings import snapshot

TACTICS_CSS = (pathlib.Path(__file__).resolve().parent.parent
               / "display" / "static" / "tactics.css")

# The viewports that separate the three cases. A wide desktop is where the panel
# is 698px tall and the fixed 124 was most obviously wrong; 390 is a phone, and
# is the only width where the badge's own geometry is larger than the page's
# resting inset, which is where it used to pull the story up.
DESKTOP = (1200, 784)
PHONE = (390, 844)

NARRATION = ("## Kobold Camp\n\nThe `first_sound` is **loud**, *very* loud, and it\n"
             "is coming from the dark of the tree line. " + "Filler to make the " * 8)

# The shipped entry point, not the class: the SSE handler calls
# _updateDicePendingBadge with the server's snapshot and nothing else touches
# the badge. One entry is a request for one player, which is the shape the
# original 124px was sized for.
SHOW = """(n) => _updateDicePendingBadge(Array.from({length: n}, () => (
  {pending: ['Kairos'], label: 'Fire Bolt'})))"""

HIDE = "() => _updateDicePendingBadge([])"

# One round trip: the insets the stylesheet is using, and the boxes they are
# supposed to be clearing.
GEOMETRY = """() => {
  const r = e => e ? Math.round(e.getBoundingClientRect().bottom) : null;
  const badge = document.getElementById('dice-pending-badge');
  const panel = document.getElementById('tx-panel');
  const prose = document.querySelector('#text-content .dm-block');
  const ts = getComputedStyle(document.getElementById('text-scroll'));
  const body = getComputedStyle(document.body);
  return {
    pad: Math.round(parseFloat(ts.paddingTop)),
    base: Math.round(parseFloat(ts.getPropertyValue('--text-top-inset')) || 0),
    dpb: parseFloat(body.getPropertyValue('--dpb-bottom')) || 0,
    txb: parseFloat(body.getPropertyValue('--tx-bottom')) || 0,
    badgeVisible: badge.classList.contains('visible'),
    badgeBottom: badge.classList.contains('visible') ? r(badge) : null,
    panelBottom: panel && !panel.hidden ? r(panel) : null,
    proseTop: prose ? Math.round(prose.getBoundingClientRect().top) : null,
  };
}"""


class BadgeInset(BrowserTestCase):
    module_name = "gm_display_app_badge_inset"

    def open(self, size, snap=None):
        page = self.open_page(size=size, wait=0)
        calm(page)
        page.evaluate("t => { handleIncomingText(t); instantFlush(); }", NARRATION)
        if snap is not None:
            page.evaluate("s => Tactics.update(s)", snap)
            settle(page)
        return page

    def show(self, page, n=1):
        page.evaluate(SHOW, n)
        present(page, "() => !!document.querySelector('#dice-pending-badge.visible')",
                f"the {n}-request badge to show")
        settle(page)
        return page.evaluate(GEOMETRY)

    # ── it is measured, and the number is removed again ────────────────────
    def test_the_badge_publishes_its_own_bottom_edge(self):
        """The stylesheet reads a measurement, not a constant.

        Both halves matter. Publishing nothing leaves the inset on its fallback
        for the whole time the badge is up, and publishing without removing it
        leaves the story pushed down after the roll is answered, which is the
        same defect in the other direction and just as invisible to a geometry
        check that only looks for overlap.
        """
        page = self.open(DESKTOP)
        page.evaluate(SHOW, 1)
        present(page, "() => !!getComputedStyle(document.body)"
                      ".getPropertyValue('--dpb-bottom').trim()",
                "--dpb-bottom to be published")
        settle(page)
        got = page.evaluate(GEOMETRY)
        self.assertGreaterEqual(got["dpb"], got["badgeBottom"],
                                f"the published edge is above the badge: {got}")
        self.assertLessEqual(got["dpb"] - got["badgeBottom"], 1, got)

        page.evaluate(HIDE)
        present(page, "() => !getComputedStyle(document.body)"
                      ".getPropertyValue('--dpb-bottom').trim()",
                "--dpb-bottom to be withdrawn when the badge goes")
        settle(page)
        self.assertEqual(page.evaluate(GEOMETRY)["dpb"], 0,
                         "a withdrawn request still pushes the story down")

    def test_the_published_edge_follows_the_badge_rather_than_a_count(self):
        """More waiting players, a taller badge, a deeper inset.

        This is the case a constant cannot serve at all: the badge grows by a
        line and a rule per extra request, and the same one request ends 19px
        lower on a phone than on a desktop because the hint wraps. Three is not
        a special number, it is simply more than one.
        """
        page = self.open(DESKTOP)
        for n in (1, 2, 3, 1):
            with self.subTest(requests=n):
                got = self.show(page, n)
                self.assertEqual(got["badgeVisible"], True, got)
                self.assertGreaterEqual(got["pad"], got["badgeBottom"],
                                        f"the prose starts inside the badge: {got}")

    # ── the three failures ─────────────────────────────────────────────────
    def test_the_story_clears_the_badge_at_every_width(self):
        """The 3px one, and the whole of it, measured as a relationship.

        One request at a time, because that is the size the old constant was
        written for: the badge's box is 71px under a 56px offset, so it ends at
        127px, and 124px put the first line of prose three pixels inside it.
        """
        for size in (DESKTOP, PHONE):
            with self.subTest(size=size):
                page = self.open(size)
                got = self.show(page, 1)
                self.assertGreaterEqual(got["pad"], got["badgeBottom"],
                                        f"the story starts under the badge: {got}")
                self.assertGreaterEqual(got["proseTop"], got["badgeBottom"],
                                        f"the prose is inside the badge: {got}")

    def test_a_badge_only_ever_pushes_the_story_down(self):
        """The 17px one. The baseline is read from the same page, every width.

        Not written as a figure, because the resting inset is not one number:
        72px above the 1100px breakpoint and 172px below it, and the badge's own
        height is bigger than the smaller of them. A GM asking for a roll on a
        phone used to see the opening of the scene move up under them.
        """
        for size in (DESKTOP, PHONE):
            with self.subTest(size=size):
                page = self.open(size)
                rest = page.evaluate(GEOMETRY)
                for n in (1, 3):
                    with self.subTest(requests=n):
                        got = self.show(page, n)
                        self.assertGreaterEqual(
                            got["pad"], rest["pad"],
                            f"asking for {n} roll(s) moved the story up from "
                            f"{rest['pad']}px to {got['pad']}px: {got}")
                page.evaluate(HIDE)
                settle(page)
                back = page.evaluate(GEOMETRY)
                self.assertEqual(back["pad"], rest["pad"],
                                 f"the story did not come back to {rest['pad']}px: {back}")

    def test_the_story_clears_the_panel_and_the_badge_together(self):
        """The one that mattered, and the reason for taking a max().

        A pending roll from inside a fight is the ordinary case, not a corner:
        the panel is up, the badge is up, and the two are up because the same
        event produced them. Before, the badge's 124px won the specificity
        contest outright and 698px of board sat on the prose for as long as
        anything was pending.
        """
        for size in (DESKTOP, PHONE):
            with self.subTest(size=size):
                page = self.open(size, snapshot())
                panel_only = page.evaluate(GEOMETRY)
                self.assertTrue(panel_only["txb"], f"--tx-bottom was never set: {panel_only}")
                got = self.show(page, 2)
                self.assertGreaterEqual(
                    got["pad"], got["txb"],
                    f"the badge overruled the panel's own inset: {got}")
                self.assertGreaterEqual(got["pad"], got["panelBottom"],
                                        f"the panel covers the story: {got}")
                self.assertGreaterEqual(got["proseTop"], got["panelBottom"],
                                        f"the prose is inside the panel: {got}")
                # ...and the badge, which is the one the panel's inset could
                # otherwise be hiding from this test.
                self.assertGreaterEqual(got["pad"], got["badgeBottom"],
                                        f"the badge covers the story: {got}")

    def test_the_panel_keeps_its_own_inset_with_the_badge_up(self):
        """The badge may not shrink the inset the panel earned.

        The same test read the other way round, and the reason the two are not
        merged: a max() taken in one rule instead of two is the arrangement that
        let a restated constant win. With the panel up, the panel's edge is
        still the answer, whatever the badge says.
        """
        page = self.open(DESKTOP, snapshot())
        before = page.evaluate(GEOMETRY)
        got = self.show(page, 3)
        self.assertGreaterEqual(got["pad"], before["pad"],
                                f"a badge took the story up under the panel: {got}")


class BadgeInsetStylesheet(unittest.TestCase):
    """The static half: the shape of the rule, which no viewport can check.

    Every assertion here is about something that only shows up as a wrong
    NUMBER at some width, or as a number at all. A browser test that measured
    the inset at four widths and found it correct would not notice a literal
    reappearing in the rule; it would just keep passing, because the literal
    happened to be right for the widths it tried.
    """

    _src = read_display_sources()
    # Comments are stripped before anything is matched, and that is not tidiness.
    # These rules sit under a paragraph of prose that names the selectors and the
    # variables they are about, so a naive scan finds "the selector" in the
    # comment that explains it and passes on a rule that has neither. The first
    # version of the `:not(.tx-on)` assertion below did exactly that, against a
    # stylesheet that had the constant back in it.
    _css = re.sub(r"/\*.*?\*/", "", _src.css, flags=re.S)
    _tactics = re.sub(r"/\*.*?\*/", "", TACTICS_CSS.read_text(encoding="utf-8"), flags=re.S)

    def _badge_rule(self):
        found = [m for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", self._css)
                 if "dice-pending-badge.visible" in m.group(1)
                 and "text-scroll" in m.group(1)]
        self.assertEqual(len(found), 1,
                         f"expected one rule moving the story for the badge, found "
                         f"{len(found)}: {[m.group(1).strip() for m in found]}")
        return found[0]

    def test_the_badge_rule_computes_the_inset_and_states_no_number(self):
        """No bare length in the value. A restated px is the defect, in one word."""
        rule = self._badge_rule()
        self.assertIn("var(--dpb-bottom", rule.group(2),
                      f"the badge rule does not read the measurement: {rule.group(2)!r}")
        self.assertIn("var(--text-top-inset", rule.group(2),
                      f"the badge rule ignores the page's resting inset: {rule.group(2)!r}")
        self.assertNotRegex(
            rule.group(2), r":\s*\d+px",
            f"a literal inset is back in the badge rule: {rule.group(2)!r}")

    def test_the_badge_rule_steps_aside_when_the_panel_is_writing(self):
        """`:not(.tx-on)`, not a second max().

        Two rules writing padding-top leave the winner to load order, and the
        whole of this defect was a specificity contest. Scoping the badge rule
        out of the panel's state is what makes the two states disjoint: with the
        panel up, tactics.css's `body.tx-on` rule is the only one writing the
        inset and it takes the badge into account there.
        """
        rule = self._badge_rule()
        self.assertIn(":not(.tx-on)", rule.group(1),
                      f"the badge rule is not scoped out of the panel's state: "
                      f"{rule.group(1).strip()!r}")

    def test_both_panels_of_the_combat_inset_take_the_badge_into_account(self):
        """The two `body.tx-on` rules, the desktop one and the phone one.

        tactics.css has a second one below its own 860px breakpoint, and it
        rewrites the same property. A max() added to the first and not the
        second is a rule that holds on a table and not on a phone, which is the
        same split this repository keeps having to find.
        """
        rules = [m for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", self._tactics)
                 if "text-scroll" in m.group(1) and "padding-top" in m.group(2)]
        self.assertEqual(len(rules), 2,
                         f"expected the desktop and phone combat insets, found "
                         f"{len(rules)}")
        for rule in rules:
            self.assertIn("var(--tx-bottom", rule.group(2), rule.group(1).strip())
            self.assertIn("var(--dpb-bottom", rule.group(2),
                          f"the combat inset ignores the badge: {rule.group(2)!r}")
            self.assertIn("max(", rule.group(2),
                          f"the combat inset does not take a maximum: {rule.group(2)!r}")

    def test_the_resting_inset_is_published_as_a_variable(self):
        """72px, and 172px below the breakpoint, both reachable by the rules
        that outrank the one that declares them.

        The badge rule and both `body.tx-on` rules outrank `#text-scroll`, so a
        number written into its `padding` is invisible to all three. The
        variable is the only part of the inset they can see, which is why it is
        published rather than written.
        """
        self.assertRegex(self._css, r"#text-scroll\s*\{[^}]*--text-top-inset:\s*72px")
        self.assertRegex(self._css, r"@media \(max-width: 1100px\)\s*\{[^@]*?"
                                    r"#text-scroll\s*\{[^}]*--text-top-inset:\s*172px",
                         "the narrow resting inset is not published as 172px")
        # And the base `padding` reads it rather than repeating it, so the two
        # cannot drift apart.
        self.assertRegex(self._css, r"#text-scroll\s*\{[^}]*padding:\s*var\(--text-top-inset\)")


if __name__ == "__main__":
    unittest.main()
