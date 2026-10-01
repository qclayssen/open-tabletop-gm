"""The wave-2 re-test findings N-1 to N-6, measured in a browser.

Each test here pins a defect the re-test reproduced against a real page, with
the numbers the report gave. The pattern is the one
test_display_tactics_layout.py established: drive the real index.html through
the Flask app on an ephemeral port, hand the panel a snapshot in the shape
scripts/tactics/sync.py produces, and measure what a browser actually did.
Skipped when playwright or Chromium is absent.

  N-1  the reading column collapses to a ribbon at narrow widths
  N-2  the map panel covers the story, and the folded panel clips its chips
  N-3  the board opens on the hero with the enemies off-screen, and a re-render
       throws away the player's scroll
  N-4  the reaction prompt names the acting creature, not the deciding one
  N-5  the roll banner omits the advantage the engine is rolling under
  N-6  a refusal is unreadable, contradicts the banner, and covers the log
  N-7  a dice-pending badge pins the story's inset to 124px, overriding the
       measured panel bottom and not clearing the badge either
"""
import importlib.util
import itertools
import json
import pathlib
import re
import threading
import unittest

from tests.display_sources import read_display_sources
from tests.display_settle import NO_PADDING_TRANSITION

REPO = pathlib.Path(__file__).resolve().parent.parent
HARNESS = REPO / "display" / "evidence-panel.html"

try:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    HAVE_PLAYWRIGHT = True
except ImportError:                                        # pragma: no cover
    HAVE_PLAYWRIGHT = False

# The viewports the re-test used: 1200x784 (its window), 768x780 and 1024x768
# (its same-origin iframes) and 390x844 (a phone).
DESKTOP = (1200, 784)
IFRAME = (768, 780)
PHONE = (390, 844)
# Wide enough that the 40px table floor puts more map than the board has: 40
# squares is 1600px of svg against a ~1316px board at 1920, so there is a real
# scroll to lose and a real off-screen creature to bring back.
WIDE = (1920, 1080)

NARRATION = ("## Kobold Camp\n\nThe `first_sound` is **loud**, *very* loud, and it\n"
             "is coming from the dark of the tree line. " + "Filler to make the " * 8)

# Mutable so the PendingRoll stub in the marker test can be re-pointed per case.
ADV = ["normal"]


def _encounter():
    """The smallest thing cli.state.load can hand back for this test: an active
    encounter, because _push_pending returns early for anything else."""
    import sys
    sys.path.insert(0, str(REPO))
    try:
        from scripts.tactics.state import Encounter
    finally:
        sys.path.pop(0)
    return Encounter(campaign="t", grid={"rows": ["..."], "legend": {".": "floor"}})


def _tok(i, name, side, x, y, hp, **extra):
    return dict({"id": i, "name": name, "side": side, "x": x, "y": y, "hp": hp,
                 "max_hp": hp, "ac": 12, "dead": False, "hidden": False,
                 "controller": "gm" if side == "enemy" else "player",
                 "conditions": [], "effects": [], "concentration": None,
                 "readied": None, "threat": 10 if side == "enemy" else 0}, **extra)


def snapshot(w=12, h=9, **turn):
    """A Kobold Camp encounter: Kairos on one side, kobolds spread on the other.

    Spread matters. The N-3 defect was only visible with the enemies far enough
    from the hero to fall outside the board's box, and a snapshot that clusters
    them would hide it.
    """
    rows = ["." * w for _ in range(h)]
    base = {"status": "active", "round": 2, "current": "kairos", "unseen_turn": False,
            "meta": {"name": "Kobold Camp"},
            "grid": {"name": "Kobold Camp", "rows": rows},
            "order": ["kairos", "kob-1", "kob-2", "kob-3"],
            "turn": {"movement_left": 30, "action_used": False, "bonus_used": False,
                     "reaction": True},
            "tokens": [_tok("kairos", "Kairos", "pc", 1, h // 2, 8),
                       _tok("kob-1", "Kobold 1", "enemy", w - 3, 1, 5),
                       _tok("kob-2", "Kobold 2", "enemy", w - 2, h - 2, 5),
                       _tok("kob-3", "Kobold 3", "enemy", w - 4, h // 2, 5)],
            "log": [{"text": "Kairos is hit for 5 (8/8 left).", "round": 2, "rolls": []}]}
    if turn:
        base["turn"].update(turn)
    return base


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class Panel(unittest.TestCase):
    """The real page, with the combat panel driven by Tactics.update()."""

    port = None

    @classmethod
    def setUpClass(cls):
        from werkzeug.serving import make_server
        spec = importlib.util.spec_from_file_location(
            "gm_display_app_retest", str(REPO / "display" / "gm-display-app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        try:
            cls.httpd = make_server("127.0.0.1", 0, mod.app, threaded=True)
        except OSError as exc:
            raise unittest.SkipTest(f"cannot bind: {exc}") from exc
        cls.port = cls.httpd.server_port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:                          # no browser downloaded
            cls.httpd.shutdown()
            raise unittest.SkipTest(f"chromium is not available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # ── helpers ───────────────────────────────────────────────────────────
    # `#text-scroll` transitions padding-top over 0.4s, and --tx-bottom (the
    # measured panel bottom) feeds it. Every measurement in this file is a
    # geometry read, so it wants the settled layout, not a frame of an animation.
    # Taking the transition off is what makes "wait until it stops changing"
    # mean something: with the transition live, "not changing yet" and "finished"
    # are the same observation, and the first is what you get. See settle().
    #
    # The string is shared with the other two display browser files (which need it
    # for the same reason) and lives in tests/display_settle.py with the rest of
    # the waiting vocabulary. It is bound here as a class attribute so the tests
    # that use it read the same as before.
    NO_PADDING_TRANSITION = NO_PADDING_TRANSITION

    def open(self, size=DESKTOP, snap=None, narrate=True, **ctx):
        context = self.browser.new_context(viewport={"width": size[0], "height": size[1]}, **ctx)
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        page.add_style_tag(content=self.NO_PADDING_TRANSITION)
        page.wait_for_timeout(400)
        if narrate:
            page.evaluate("t => { handleIncomingText(t); instantFlush(); }", NARRATION)
        if snap is not None:
            page.evaluate("s => Tactics.update(s)", json.loads(json.dumps(snap)))
            self.settle(page)
        return page

    # Poll padding-top, not --tx-bottom: the variable is published synchronously by
    # the ResizeObserver, so it is already final (`698px`) on the first read while
    # padding-top is still short of it. Polling the variable returns immediately.
    #
    # --dpb-bottom is the dice-pending badge's own measured bottom, published the
    # same way by display.js. It is here for the same reason: on a page where the
    # badge is what the story has to clear, it is the only number padding-top can
    # be compared against. Both are read, and `pad >= max()` is the real
    # lower bound for "arrived" -- which is the whole point, because a badge with
    # a fixed 124px under it could never satisfy it.
    SETTLE = """() => {
      const bs = getComputedStyle(document.body);
      const pad = parseFloat(getComputedStyle(
        document.getElementById('text-scroll')).paddingTop);
      const txb = parseFloat(bs.getPropertyValue('--tx-bottom')) || 0;
      const dpb = parseFloat(bs.getPropertyValue('--dpb-bottom')) || 0;
      // Both have to be true, and the second is the one that was missing:
      // --tx-bottom is what padding-top is computed FROM, so until it is
      // published there is nothing to be stable relative to, and a padding
      // reading of 72 or 172 is the untouched starting value rather than a
      // settled one. `open()` takes the transition off, so once this holds the
      // value is final rather than momentarily unmoved.
      //
      // "At least one", not "--tx-bottom specifically": a page with no combat
      // panel publishes no --tx-bottom at all, and there the badge is the only
      // extent there is to settle against. With neither published, padding-top
      // is sitting on the base 72px inset, which is a constant, so it would read
      // as perfectly stable and the measurement would be of an unbuilt layout.
      if (!txb && !dpb) return false;
      if (pad < Math.max(txb, dpb)) return false;
      const w = window;
      if (w.__mqLastPad === undefined || pad !== w.__mqLastPad) {
        w.__mqLastPad = pad; w.__mqStable = 0; return false;
      }
      return ++w.__mqStable >= 3;
    }"""

    def settle(self, page):
        """Block until padding-top has caught up with the published panel bottom.

        Three things this has to get right, each of which I got wrong first:

        - Poll padding-top, NOT --tx-bottom. The variable is published
          synchronously by the ResizeObserver, so it is already at its final
          value (`698px`) on the first read while padding-top is still animating
          toward it. Polling the variable returns immediately and measures 187px
          mid-flight -- that version failed five tests that had been passing.
        - Each poll must be a separate task. A synchronous `for` loop inside the
          predicate never yields, style recalc never runs, padding sits at its
          72px starting value for every iteration, and "stable" is reached on
          the third one -- measuring before the animation began.
        - Three identical samples, not one: a single match can be two samples
          inside the same easing step.

        The bug that made this file red on `main`, and the reason the first two
        are not enough on their own: "three samples in a row and none of them
        moved" is ALSO what an animation looks like in its first 50ms, before it
        has travelled anywhere. So the predicate could return true while padding
        was still at its starting value, and the geometry read that followed was
        of a half-built layout. It passed in isolation because a lightly loaded
        machine got its first poll after the transition had begun, and it failed
        at the fourth viewport when run after the rest of the file because by
        then there were three stale pages open and everything was slower. That is
        the worst shape for a timing bug: green when you run the test, red when
        you run the suite.

        The fix is to stop inferring "finished" from motion. `--tx-bottom` is
        published synchronously and padding-top is `calc(var(--tx-bottom) + 28px)`,
        so `pad >= txb` is a real lower bound for "arrived" rather than a guess
        about easing curves, and `open()` removes the transition so that arriving
        is the only thing left to observe.

        This does not weaken what the file checks. On the broken tree the new
        predicate fails `test_the_story_starts_below_the_panel` at (1440, 900) and
        (390, 844) exactly as the old one did, and it still fails when the inset
        is merely 12px short, which is the off-by-N bug this whole mechanism
        exists to catch and which a pure stability check cannot see.
        """
        page.wait_for_function(self.SETTLE, polling="raf", timeout=8000)
        page.wait_for_timeout(50)

    def try_settle(self, page, timeout=4000):
        """settle() as a question rather than a wait, for a page that may not be
        able to satisfy it.

        The one case where it cannot: an overlay whose rule pins padding-top to a
        fixed number while a deeper one is measured, so `pad >= max(txb, dpb)` is
        unsatisfiable rather than merely slow. That is the badge defect below,
        and settle() against it is four seconds per case ending in a timeout,
        which is the least informative failure a layout test can produce: it says
        the page did not settle and nothing about what is wrong with it. So the
        wait is bounded, the result is discarded, and the geometry read that
        follows is what names the defect.

        Nothing is given up by not blocking. `open()` has already taken the
        padding transition off, so on a page where the inset is already at its
        final (wrong) value there is nothing left to wait for at all; and the
        tests below assert `pad >= txb` in their own right rather than trusting
        this to have blocked, so a wait that gave up early cannot hide anything.
        """
        try:
            page.wait_for_function(self.SETTLE, polling="raf", timeout=timeout)
            page.wait_for_timeout(50)
        except PlaywrightTimeout:
            pass
        return page

    # ── N-2: the panel must not be an opaque lid on the story ────────────
    GEOMETRY = """() => {
      const r = e => { const b = e.getBoundingClientRect();
        return {t: Math.round(b.top), b: Math.round(b.bottom), l: Math.round(b.left),
                rr: Math.round(b.right), h: Math.round(b.height)}; };
      const p = document.getElementById('tx-panel');
      const ts = document.getElementById('text-scroll');
      const first = document.querySelector('#text-content .dm-block');
      const chips = [...document.querySelectorAll('.tx-chip')].map(c => r(c));
      return {panel: r(p), panelOverflows: p.scrollHeight > p.clientHeight + 1,
              panelScrollH: p.scrollHeight, panelClientH: p.clientHeight,
              prose: first ? r(first) : null,
              inset: parseFloat(getComputedStyle(ts).paddingTop),
              published: getComputedStyle(document.body).getPropertyValue('--tx-bottom').trim(),
              chips, folded: document.body.classList.contains('tx-min')};
    }"""

    def test_the_story_starts_below_the_panel(self):
        """The panel is a fixed overlay and the reading column has to clear it.

        The report measured the story's first block at y=609 with the panel's
        bottom at y=600 at 1200x784, and worse at 1024: the inset was a
        constant min(62vh, 640px) + 90px and the panel is as tall as its content.
        """
        for size in (DESKTOP, (1024, 768), IFRAME, (1440, 900), PHONE):
            with self.subTest(size=size):
                page = self.open(size, snapshot())
                m = page.evaluate(self.GEOMETRY)
                self.assertTrue(m["published"], f"--tx-bottom was never set: {m}")
                self.assertGreaterEqual(m["prose"]["t"], m["panel"]["b"],
                                        f"the panel covers the story: {m}")

    def test_the_folded_panel_clips_nothing(self):
        """'Hide map' folds to the header and the initiative strip. A fixed 132px
        was not enough of a height: the report saw the chips for Kairos and the
        kobolds cut off and the '3/8 HP' bar clipped, because the banner had
        re-wrapped the header onto a second row."""
        page = self.open(DESKTOP, snapshot())
        page.click("#tx-min")
        self.settle(page)
        m = page.evaluate(self.GEOMETRY)
        self.assertTrue(m["folded"], m)
        self.assertFalse(m["panelOverflows"],
                         f"the folded panel is clipping: {m}")
        for c in m["chips"]:
            self.assertLessEqual(c["b"], m["panel"]["b"] + 1, f"a chip is cut: {m}")
        # The HP bar is the thing that was clipped, so it has to be in view too.
        bars = page.evaluate("""() => [...document.querySelectorAll('.tx-hpbar')]
          .map(b => Math.round(b.getBoundingClientRect().bottom))""")
        self.assertTrue(bars, "no HP bars to measure")
        for bottom in bars:
            self.assertLessEqual(bottom, m["panel"]["b"] + 1, f"an HP bar is cut: {m}")

    def test_every_action_button_reaches_the_panel_on_a_phone(self):
        """Not just the first. The action bar is a scroll box on a phone, and the
        bar itself may overflow the panel it sits in, which is a different defect
        from the one above and would leave End turn (the last button) off-screen
        however well the first row is placed."""
        page = self.open(IFRAME, snapshot())
        m = page.evaluate("""() => {
          const panel = document.getElementById('tx-panel').getBoundingClientRect();
          return {panelBottom: Math.round(panel.bottom),
                  buttons: [...document.querySelectorAll('#tx-actions button')].map(b => {
                    const r = b.getBoundingClientRect();
                    return {text: b.textContent.trim(), top: Math.round(r.top),
                            bottom: Math.round(r.bottom)}; })}; }""")
        self.assertTrue(m["buttons"], "no action buttons to measure")
        for b in m["buttons"]:
            self.assertLessEqual(b["bottom"], m["panelBottom"] + 1,
                                 f"{b['text']!r} runs past the panel: {m}")

    def test_the_action_bar_is_inside_the_panel_on_a_phone(self):
        """At 768 the panel was 692px tall holding 907px of content, so Move and
        Attack began at y=705: below the board, below the panel, with nothing to
        say the panel scrolled."""
        page = self.open(IFRAME, snapshot())
        m = page.evaluate("""() => {
          const p = document.getElementById('tx-panel');
          const a = document.getElementById('tx-actions');
          const b = a.getBoundingClientRect();
          const first = a.querySelector('button').getBoundingClientRect();
          return {panel: {t: p.getBoundingClientRect().top, b: p.getBoundingClientRect().bottom},
                  bar: {t: Math.round(b.top), b: Math.round(b.bottom)},
                  first: {text: a.querySelector('button').textContent,
                          t: Math.round(first.top), b: Math.round(first.bottom)},
                  overflows: p.scrollHeight > p.clientHeight + 1,
                  endTurn: (() => { const e = a.querySelector('[data-tx=end]');
                    if (!e) return null; const r = e.getBoundingClientRect();
                    return {t: Math.round(r.top), b: Math.round(r.bottom)}; })()};
        }""")
        self.assertLessEqual(m["first"]["b"], m["panel"]["b"] + 1,
                             f"the action bar is below the panel: {m}")
        self.assertIsNotNone(m["endTurn"], "no End turn button to find")
        self.assertLessEqual(m["endTurn"]["b"], m["panel"]["b"] + 1,
                             f"End turn is below the panel: {m}")

    # ── N-3: the whole fight, and the player's scroll, survive ────────────
    BOARD = """() => {
      const bd = document.getElementById('tx-board');
      const svg = bd.querySelector('svg');
      const inView = [...document.querySelectorAll('.tx-tok')].map(g => {
        const b = g.getBoundingClientRect(), box = bd.getBoundingClientRect();
        return {id: g.getAttribute('data-id'),
                l: Math.round(b.left), r: Math.round(b.right),
                t: Math.round(b.top), bo: Math.round(b.bottom),
                boxL: Math.round(box.left), boxR: Math.round(box.right),
                boxT: Math.round(box.top), boxB: Math.round(box.bottom)};
      });
      return {clientW: bd.clientWidth, scrollW: bd.scrollWidth,
              svgW: Math.round(svg.getBoundingClientRect().width),
              scrollLeft: bd.scrollLeft, scrollTop: bd.scrollTop,
              inView, stacked: document.getElementById('tx-panel').classList.contains('tx-stacked')};
    }"""

    def test_everyone_is_on_screen_the_first_time_the_map_opens(self):
        """The report: at 1200 a 12x9 grid showed about 7.5 columns, so the
        kobolds at K2 and K6 were off-screen and the first thing a player saw was
        Kairos alone.

        The case that separates this from the old behaviour needs a map wider
        than the board, with the fight clustered in one part of it: framing on
        the acting token then shows the hero and whatever is beside it, while
        framing on the encounter shows the whole group. A map that fits outright
        cannot tell the two apart, which is why this is not measured at 1200 on a
        12x9 map.
        """
        # 60 squares at the 40px floor is 2400px of svg in a ~1316px board.
        # The group sits in the right-hand half and stays inside the board's
        # height, since a group that is also taller than the box has no framing
        # that shows all of it and the panel correctly falls back to the hero.
        snap = snapshot(w=60, h=20)
        for i, t in enumerate(snap["tokens"]):
            t["x"] = 40 + i * 4
            t["y"] = 9 + (i % 2)
        page = self.open(WIDE, snap)
        m = page.evaluate(self.BOARD)
        self.assertGreater(m["svgW"], m["clientW"],
                           f"the map fits, so framing cannot differ: {m}")
        for t in m["inView"]:
            self.assertGreaterEqual(t["l"], t["boxL"] - 1,
                                    f"{t['id']} starts off the board: {m}")
            self.assertLessEqual(t["r"], t["boxR"] + 1,
                                 f"{t['id']} ends off the board: {m}")

    def test_the_whole_map_is_shown_when_it_fits(self):
        """The other shape of the same defect, and the one the report measured:
        a 12x9 map that the board can hold whole, opened with the kobolds past
        the right edge. Nothing is scrolling here, so every token has to be
        inside the board at 1200, 1440 and in the 768 iframe."""
        for size in (DESKTOP, (1440, 900), IFRAME):
            with self.subTest(size=size):
                page = self.open(size, snapshot())
                m = page.evaluate(self.BOARD)
                self.assertLessEqual(m["svgW"], m["clientW"] + 1,
                                     f"the map does not fit at {size}: {m}")
                for t in m["inView"]:
                    self.assertGreaterEqual(t["l"], t["boxL"] - 1,
                                            f"{t['id']} starts off the board: {m}")
                    self.assertLessEqual(t["r"], t["boxR"] + 1,
                                         f"{t['id']} ends off the board: {m}")

    def test_a_wider_screen_does_not_show_less_of_the_fight(self):
        """The layout was worse on the wider screen, which is backwards: the
        board had 342px at 1200 and 724px at 768, because the two-column split
        spent 260px of a 630px panel on the side column and left a 12x9 grid
        showing 7.5 columns.

        Measured in squares on screen rather than board pixels, since stacking
        legitimately changes the board's width: what must never happen is a
        wider window showing fewer squares of the map. Compared across the
        desktop widths, where the panel is inset by the same two rails and only
        the width differs.
        """
        cols = []
        for size in (DESKTOP, (1440, 900), (1920, 1080)):
            with self.subTest(size=size):
                page = self.open(size, snapshot())
                m = page.evaluate(self.BOARD)
                self.assertLessEqual(m["svgW"], m["clientW"] + 1,
                                     f"the map is clipped: {m}")
                # How many of the map's 12 columns the board can actually show.
                # Capped at 12: the whole map is the ceiling, and a board wide
                # enough for all of them is not penalised for the last fraction.
                cols.append(min(12.0, round(m["clientW"] * 12 / m["svgW"], 1)))
        for before, after in itertools.pairwise(cols):
            self.assertGreaterEqual(after, before,
                                    f"a wider window showed less of the map: {cols}")

    def _centre_hero(self, page, snap, x=20):
        """Put the acting token in the middle of a wide map.

        The point of the two tests below is the rule, not the map: the scroll
        survives a push while the acting token is still in view, and is given up
        when the token is not. A hero in the middle is the only placement where
        both halves are reachable, because a 40-square map is 1600px of svg in a
        1316px box and there is only 284px of scroll to give.
        """
        snap["tokens"][0]["x"] = x
        page.evaluate("s => Tactics.update(s)", dict(snap))
        self.settle(page)

    def test_a_push_does_not_throw_away_the_scroll(self):
        """The report: 'Re-render resets scrollLeft (after each push it snaps
        back to 0)'. renderBoard kept the scroll and then re-framed on the
        acting token, which undid it whenever the hero sat near the left edge.
        Here the token is still on screen, so there is nothing to bring back and
        the player's view has to survive untouched."""
        snap = snapshot(w=40, h=20)
        page = self.open(WIDE, snap)
        self._centre_hero(page, snap)
        self.assertGreater(page.evaluate(self.BOARD)["scrollW"],
                           page.evaluate("document.getElementById('tx-board').clientWidth"),
                           "the map fits, so there is no scroll to lose")
        page.evaluate("document.getElementById('tx-board').scrollLeft = 284")
        page.wait_for_timeout(150)
        before = page.evaluate("document.getElementById('tx-board').scrollLeft")
        self.assertGreater(before, 0, "could not scroll the board to test with")
        page.evaluate("s => Tactics.update(s)", dict(snap))
        self.settle(page)
        after = page.evaluate("document.getElementById('tx-board').scrollLeft")
        self.assertEqual(after, before, f"the push reset the scroll: {before} -> {after}")

    def test_a_push_brings_back_a_token_that_left_the_board(self):
        """The other half of the same rule: the scroll is the player's, but a
        creature that has walked off the board is the panel's problem. Framed on
        the encounter when the fight is too wide to show, and on the acting token
        when it is not, so nothing that matters is ever silently outside."""
        page = self.open(WIDE, snapshot(w=40, h=20))
        # Everybody in the left-hand column, then scrolled well right of them.
        page.evaluate("s => { s.tokens.forEach(t => { t.x = 0; t.y = 0; });"
                      " Tactics.update(s); }", snapshot(w=40, h=20))
        self.settle(page)
        page.evaluate("document.getElementById('tx-board').scrollLeft = 1200")
        page.wait_for_timeout(150)
        self.assertGreater(page.evaluate("document.getElementById('tx-board').scrollLeft"), 0,
                           "could not scroll away from the creatures")
        page.evaluate("s => Tactics.update(s)", snapshot(w=40, h=20))
        self.settle(page)
        m = page.evaluate(self.BOARD)
        kairos = next(t for t in m["inView"] if t["id"] == "kairos")
        self.assertGreaterEqual(kairos["l"], kairos["boxL"] - 1,
                                f"the acting token is off the board after a push: {m}")
        self.assertLessEqual(kairos["r"], kairos["boxR"] + 1, m)

    def test_the_split_is_dropped_before_the_map_is_clipped(self):
        """The split is worth having only when the board can still hold the map
        at a readable square (the 40px table-display floor), so a panel too
        narrow for both stacks instead of clipping. The old viewport breakpoint
        kept the two columns at 1200, where the board got 342px of a 630px panel
        and a 12x9 map lost two columns.

        A map too wide for any panel still scrolls: that is the table-display
        rule and stacking would not make it fit, so only the no-clip case is
        asserted.
        """
        # 12 squares at the 40px floor needs 480px of board. At 1200 the panel's
        # inner width is 606, so the split would leave 346 and the map is
        # clipped; at 1440 it is 846 and the split leaves 586, which fits.
        cases = ((DESKTOP, 12, 9, True),
                 ((1440, 900), 12, 9, False),
                 ((1920, 1080), 12, 9, False))
        for size, w, h, stacked in cases:
            with self.subTest(size=size, map=(w, h)):
                page = self.open(size, snapshot(w=w, h=h))
                m = page.evaluate(self.BOARD)
                self.assertEqual(m["stacked"], stacked, m)
                self.assertLessEqual(m["svgW"], m["clientW"] + 1, f"clipped: {m}")

    def test_a_map_wider_than_any_panel_still_scrolls(self):
        """Not a defect to fix: the table display draws at least 40px squares and
        scrolls a big map rather than shrinking it into a squint. Pinned so the
        stacking rule is not later "fixed" by dropping the floor."""
        page = self.open(WIDE, snapshot(w=40, h=20))
        m = page.evaluate(self.BOARD)
        self.assertGreater(m["svgW"], m["clientW"],
                           f"a 40-wide map was squeezed to fit instead of scrolling: {m}")
        self.assertGreaterEqual(round(m["svgW"] / 40), 40, m)

    # ── the badge has to be cleared too, and by a measurement ─────────────
    # The "Waiting on..." badge is the third fixed overlay over the story, and
    # it is the newest: display.css answered with a fixed 124px while the panel
    # beside it published a measured --tx-bottom. Because `:has()` contributes its
    # argument, the badge rule computed to (2 ids) against the panel rule's (1
    # id) and beat it outright, so a page with a dice request outstanding had its
    # story inset pinned to 124px with the panel's bottom measured at 698px. The
    # panel covered the story for as long as anything was pending, and
    # `pad >= txb` could not hold, which is the predicate the file settles on.

    BADGE = """(n) => {
      // The real event handler, fed the shape gm-display-app.py's
      // _dice_pending_snapshot() produces, rather than the `visible` class set
      // by hand. The extent is published by the same call that shows the badge,
      // so a test that added the class itself would be measuring a badge that
      // nothing had measured, and the number under test would not exist.
      const snap = [];
      for (let i = 0; i < n; i++) {
        snap.push({request_id: 'req-' + i, pending: ['Piper'],
                   label: 'Fire Bolt, attack'});
      }
      _updateDicePendingBadge(snap);
    }"""

    BADGE_GEOMETRY = """() => {
      const r = e => e.getBoundingClientRect();
      const bs = getComputedStyle(document.body);
      const b = document.getElementById('dice-pending-badge');
      const p = document.getElementById('tx-panel');
      const first = document.querySelector('#text-content .dm-block');
      return {pad: parseFloat(getComputedStyle(
                document.getElementById('text-scroll')).paddingTop),
              txb: parseFloat(bs.getPropertyValue('--tx-bottom')) || 0,
              dpb: parseFloat(bs.getPropertyValue('--dpb-bottom')) || 0,
              visible: b.classList.contains('visible'),
              badge: {h: Math.round(r(b).height), t: Math.round(r(b).top),
                      b: Math.round(r(b).bottom)},
              panelB: p.hidden ? 0 : Math.round(r(p).bottom),
              prose: first ? Math.round(r(first).top) : null};
    }"""

    def show_badge(self, page, n=1):
        """Show the badge through the real handler, then read the geometry.

        try_settle() rather than settle(), deliberately: on the pre-fix CSS this
        page cannot settle at all, and saying so in a message that carries the
        numbers is the point of these tests.
        """
        page.evaluate(self.BADGE, n)
        self.try_settle(page)
        return page.evaluate(self.BADGE_GEOMETRY)

    def assert_cleared(self, m):
        """The invariant, in the order that makes the pre-fix failure readable.

        The first two are geometry read off the elements themselves, so they hold
        in any world: the inset has to be at least as deep as the badge that is
        on screen, and the story's first line has to start below the deepest
        overlay. The pre-fix CSS fails the first with "124 not >= 127" for a
        one-request badge and "124 not >= 698" the moment the panel is up, which
        is the defect stated in a sentence.

        The last is the mechanism rather than the effect: the number the
        stylesheet used IS the badge's measured bottom, so a larger constant
        (300px would clear every badge in this file) cannot pass. Without it the
        first two would only pin "big enough", which is how 124 got there.
        """
        self.assertGreaterEqual(m["pad"], m["badge"]["b"],
                                f"the inset does not clear the badge: {m}")
        self.assertGreaterEqual(m["prose"], m["badge"]["b"],
                                f"the badge covers the story: {m}")
        if m["txb"]:
            self.assertGreaterEqual(m["pad"], m["txb"],
                                    f"the inset stopped following the panel: {m}")
            self.assertGreaterEqual(m["prose"], m["panelB"],
                                    f"the panel covers the story: {m}")
        if m["visible"]:
            self.assertEqual(m["dpb"], m["badge"]["b"],
                             f"the inset is not following the measured badge: {m}")

    def test_a_visible_badge_does_not_unmeasure_the_panel_inset(self):
        """The defect, in the shape it shipped: badge visible, panel up, story
        under the board.

        Every case here is a page where something is waiting on a roll, which is
        exactly when a player is most likely to be reading the story. The
        `pad >= txb` half is the predicate the rest of this file settles on, and
        it is asserted rather than waited on, because a product that cannot
        satisfy it is a defect and not a slow machine.
        """
        for size in (DESKTOP, (1024, 768), IFRAME, PHONE):
            for n in (1, 3):
                with self.subTest(size=size, pending=n):
                    page = self.open(size, snapshot())
                    m = self.show_badge(page, n)
                    self.assertTrue(m["visible"], f"the badge never showed: {m}")
                    self.assertTrue(m["txb"], f"--tx-bottom was never set: {m}")
                    self.assert_cleared(m)

    def test_the_badge_is_cleared_whatever_it_has_in_it(self):
        """The other half, and the reason the value is measured rather than a
        bigger constant: the badge is as tall as the requests in it, and it does
        not stop at one. 124px is 56px (the badge's top) plus a one-request
        badge, so it was already 3px short there, 49px short of two and 100px of
        three, with the story's first line unmoved at y=157 throughout."""
        for n in (1, 2, 3):
            with self.subTest(pending=n):
                page = self.open(DESKTOP, snapshot())
                m = self.show_badge(page, n)
                self.assert_cleared(m)
                # A one-request badge is the case the 124 was sized for, and the
                # only one where the old number was nearly right, so it is the
                # one that would not have been noticed by eye.
                if n == 1:
                    self.assertGreaterEqual(m["pad"], 124, m)

    def test_a_badge_on_a_page_with_no_panel_still_pushes_the_story_down(self):
        """The case the 124 existed for, and the one a naive "only while the
        panel is driving the inset" scoping would have thrown away.

        There is no --tx-bottom here at all: `hide()` removes it along with the
        panel (tactics.js:632,965), so the base 72px inset is what the story
        would otherwise start under and the badge would sit on it. That is why
        the badge rule is scoped to `:not(.tx-on)` rather than deleted, and why
        settle() accepts --dpb-bottom as an extent to settle against.
        """
        page = self.open(DESKTOP, None, narrate=True)
        self.assertFalse(page.evaluate("document.body.classList.contains('tx-on')"))
        for n in (1, 3):
            with self.subTest(pending=n):
                m = self.show_badge(page, n)
                self.assertEqual(m["txb"], 0, f"--tx-bottom should be gone: {m}")
                self.assert_cleared(m)
                if n == 1:
                    self.assertGreaterEqual(m["pad"], 124, m)

    def test_a_badge_never_shrinks_the_inset_the_page_already_had(self):
        """The one way the measured badge can still be wrong while every other
        assertion here passes.

        The badge rule wins on specificity against BOTH the 72px base and the
        172px narrow-width inset, and `max(72px, --dpb-bottom) + 28px` only knows
        about the first. Below 1100px the page is inset to 172px to clear the
        settings row across the top band, and a one-request badge measures 127px,
        so the badge rule computes 155px: 17px SHALLOWER than the page it is
        added to. Nothing overlaps, so assert_cleared() is silent about it, and
        the story jumps up 17px when a dice request arrives, which is a visible
        jump for the reader at the moment a roll is pending.

        The invariant is about the direction of the change, not about a number:
        showing a badge may only ever push the story DOWN, never up. The baseline
        is read from the same page with no badge, so this cannot be satisfied by
        picking a bigger constant in the badge rule.
        """
        for size in (PHONE, IFRAME):
            with self.subTest(size=size):
                page = self.open(size, None, narrate=True)
                base = page.evaluate(self.BADGE_GEOMETRY)["pad"]
                for n in (1, 3):
                    m = self.show_badge(page, n)
                    self.assert_cleared(m)
                    self.assertGreaterEqual(
                        m["pad"], base,
                        f"a {n}-request badge made the inset shallower than the "
                        f"page's own {base}px: {m}")
                page.evaluate("() => _updateDicePendingBadge([])")
                self.try_settle(page)
                page.close()

    def test_a_badge_that_goes_away_takes_its_inset_with_it(self):
        """A stale --dpb-bottom would hold the story down for a badge that is no
        longer on screen, which is the failure mode a publish-and-forget has. The
        empty snapshot is what the server sends once the last request resolves."""
        page = self.open(DESKTOP, snapshot())
        self.show_badge(page, 2)
        page.evaluate("() => _updateDicePendingBadge([])")
        self.try_settle(page)
        m = page.evaluate(self.BADGE_GEOMETRY)
        self.assertFalse(m["visible"], m)
        self.assertEqual(m["dpb"], 0, f"--dpb-bottom was left behind: {m}")
        self.assert_cleared(m)

    # ── N-4 and N-5: the banner names the right creature and the right roll ──
    BANNER = """(pending) => ({
      banner: document.getElementById('tx-banner').textContent,
      info: document.getElementById('tx-info').textContent,
    })"""

    def banner_for(self, pending, size=DESKTOP, snap=None):
        page = self.open(size, snap or snapshot())
        page.evaluate("p => { Tactics.state().turn.pending = p; Tactics.update(Tactics.state()); }",
                      pending)
        self.settle(page)
        out = page.evaluate("""() => ({
          banner: document.getElementById('tx-banner').textContent,
          info: document.getElementById('tx-info').textContent,
        })""")
        page.close()
        return out

    def test_a_reaction_names_the_creature_that_has_to_decide(self):
        """Kobold 2 was acting and the engine was waiting on Kairos to spend a
        reaction. The banner named Kobold 2, so it read as the kobold answering
        for a spell reaction it was the victim of."""
        snap = snapshot()
        snap["turn"]["pending"] = "react:kairos:silvery barbs"
        snap["current"] = "kob-2"
        got = self.banner_for("react:kairos:silvery barbs", snap=snap)
        self.assertIn("Kairos", got["info"], got)
        self.assertNotIn("Waiting on Kobold 2", got["info"], got)
        self.assertIn("silvery barbs", got["info"], got)

    def test_a_roll_banner_says_how_the_roll_is_made(self):
        """The terminal said 'rolls 1d20+4 with disadvantage' and the display
        said 'roll 1d20+4', which reads as one die. The advantage has to be in
        the wait: it is what the player is deciding how to respond to."""
        for adv in ("advantage", "disadvantage"):
            with self.subTest(adv=adv):
                got = self.banner_for(f"roll:1d20+4|{adv}")
                self.assertIn(f"with {adv}", got["info"], got)
        plain = self.banner_for("roll:1d20+4")
        self.assertIn("roll 1d20+4", plain["info"], plain)
        self.assertNotIn("with normal", plain["info"], plain)

    # ── N-6: a refusal has to be readable, agree with the banner, and not hide
    #         the log line that says the same thing ─────────────────────────
    # Class attribute rather than a list literal in the method, so a reader can
    # see the four shapes the server can return next to the test that uses them.
    REFUSALS = (
        (404, "No active campaign.", "Start one in the terminal"),
        (403, "This device is not approved to act yet.", "not approved"),
        (409, "There is no player's turn open right now. It is Kobold 2's turn.",
         "Kobold 2's turn"),
        (409, "Only Kairos can act now. Nothing was sent.", "Kairos"),
    )

    def test_each_refusal_says_what_to_do_about_it(self):
        """A player cannot act on 'It is not a player's turn.' The four refusals
        the map's buttons can return each need a different sentence, and each
        needs to name the thing that has to change."""
        page = self.open(DESKTOP, snapshot())
        for status, body, expect in self.REFUSALS:
            with self.subTest(status=status, body=body):
                got = page.evaluate("a => Tactics.refusal(a[0], {error: a[1]})",
                                    [status, body])
                self.assertIn(expect, got, f"refusal({status}, {body!r}) = {got!r}")
                self.assertGreater(len(got), 30, f"nothing actionable in {got!r}")

    def test_a_refusal_for_a_status_the_server_did_not_name_is_still_shown(self):
        """The engine's own sentence is shown when there is no case for it: a
        refusal the display cannot improve on is still the truth, and hiding it
        because it is unrecognised would leave a click with no answer."""
        page = self.open(DESKTOP, snapshot())
        got = page.evaluate("""a => Tactics.refusal(a[0], {error: a[1]})""",
                            [422, "That spell needs a line of effect."])
        self.assertIn("line of effect", got)
        got2 = page.evaluate("a => Tactics.refusal(a[0], null)", [500])
        self.assertIn("500", got2)

    def test_a_refusal_shows_in_the_banner_and_survives_the_toast(self):
        page = self.open(DESKTOP, snapshot())
        page.route("**/combat/do", lambda route: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"error": "There is no player's turn open right now. "
                                      "It is Kobold 2's turn. Nothing was sent."})))
        page.click("#tx-actions button:has-text('End turn')")
        page.wait_for_timeout(700)
        m = page.evaluate("""() => {
          const b = document.getElementById('tx-banner');
          return {text: b.textContent, role: b.getAttribute('role'),
                  cls: b.className};
        }""")
        self.assertIn("Kobold 2's turn", m["text"], m)
        self.assertEqual(m["role"], "alert", m)
        # The banner is the answer and it stays; the toast is the transient copy
        # of it, so the toast going is not the message going. The toast is
        # dismissed here rather than waited out, so the assertion is about the
        # banner's lifetime and not about a timer.
        page.evaluate("document.getElementById('tx-toast').hidden = true")
        page.wait_for_timeout(300)
        still = page.inner_text("#tx-banner")
        self.assertIn("Kobold 2's turn", still,
                      f"the refusal left with the toast: {still!r}")

    def test_a_refusal_outlives_the_toast_timer(self):
        """The toast's 6s expiry is what the banner has to survive: before, the
        only copy of the reason a click did nothing was on a timer."""
        page = self.open(DESKTOP, snapshot())
        page.route("**/combat/do", lambda route: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"error": "There is no player's turn open right now. "
                                      "It is Kobold 2's turn. Nothing was sent."})))
        page.click("#tx-actions button:has-text('End turn')")
        page.wait_for_timeout(700)
        self.assertFalse(page.evaluate("document.getElementById('tx-toast').hidden"),
                         "the toast should be showing at first")
        page.wait_for_function("document.getElementById('tx-toast').hidden", timeout=12000)
        still = page.inner_text("#tx-banner")
        self.assertIn("Kobold 2's turn", still,
                      f"the refusal went when the toast did: {still!r}")

    def test_the_toast_does_not_cover_the_log(self):
        """The toast sat at the bottom of the panel, over the combat log and, on
        a phone, over the action bar: the two things a player looks at when an
        action does not happen."""
        for size in (DESKTOP, IFRAME, PHONE):
            with self.subTest(size=size):
                page = self.open(size, snapshot())
                page.evaluate("""() => { const t = document.getElementById('tx-toast');
                  t.textContent = 'x'; t.hidden = false; }""")
                m = page.evaluate("""() => {
                  const tb = document.getElementById('tx-toast').getBoundingClientRect();
                  const log = document.getElementById('tx-log').getBoundingClientRect();
                  const bar = document.getElementById('tx-actions').getBoundingClientRect();
                  const over = (a, b) => a.width > 0 && a.height > 0 &&
                    a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
                  return {overLog: over(tb, log), overBar: over(tb, bar)};
                }""")
                self.assertFalse(m["overLog"], f"the toast covers the log: {m}")
                self.assertFalse(m["overBar"], f"the toast covers the action bar: {m}")

    def test_a_new_turn_clears_a_standing_refusal(self):
        page = self.open(DESKTOP, snapshot())
        page.route("**/combat/do", lambda route: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"error": "There is no player's turn open right now."})))
        page.click("#tx-actions button:has-text('End turn')")
        page.wait_for_timeout(600)
        self.assertIn("no player's turn",
                      page.inner_text("#tx-banner").lower())
        snap = snapshot()
        snap["current"] = "kob-1"
        page.unroute("**/combat/do")
        page.evaluate("s => Tactics.update(s)", snap)
        self.settle(page)
        got = page.inner_text("#tx-banner")
        self.assertIn("Kobold 1", got)
        self.assertNotIn("no player's turn", got.lower(), got)

    def test_the_fold_button_is_big_enough_to_hit(self):
        """P3-2, still open from the first pass and cheap to close: the map's
        Hide/Show control was a target and a legibility problem in one."""
        page = self.open(DESKTOP, snapshot())
        m = page.evaluate("""() => { const b = document.getElementById('tx-min');
          const cs = getComputedStyle(b), r = b.getBoundingClientRect();
          return {w: r.width, h: r.height, size: parseFloat(cs.fontSize),
                  label: b.textContent.trim(),
                  name: b.getAttribute('aria-label'), title: b.title}; }""")
        self.assertGreaterEqual(m["h"], 32, f"the fold control is {m['h']}px tall: {m}")
        self.assertGreaterEqual(m["size"], 12, f"the fold control is {m['size']}px type: {m}")
        self.assertTrue(m["name"] or m["label"], m)

    def test_the_rail_hide_control_is_a_legible_reachable_button(self):
        """P3-2 again, on the other control the report meant: the "Hide >" in the
        settings rail was 7.5px of text at 0.5 alpha, on a div with a click
        handler, so it was neither readable nor reachable by keyboard."""
        page = self.open(DESKTOP, snapshot())
        m = page.evaluate("""() => {
          const row = document.getElementById('controls-toggle-row');
          const label = row.querySelector('.audio-label');
          const r = row.getBoundingClientRect(), lcs = getComputedStyle(label);
          // The alpha is read here, not from the string: computed color is
          // "rgba(r, g, b, a)" when translucent and "rgb(r, g, b)" when not, and
          // 1 is the default for the second form.
          const parts = lcs.color.split(',');
          return {tag: row.tagName, h: r.height, size: parseFloat(lcs.fontSize),
                  alpha: parts.length > 3 ? parseFloat(parts[3]) : 1,
                  text: label.textContent.trim(),
                  name: row.getAttribute('aria-label'),
                  expanded: row.getAttribute('aria-expanded')}; }""")
        self.assertEqual(m["tag"], "BUTTON", f"the rail hide control is a {m['tag']}: {m}")
        self.assertGreaterEqual(m["h"], 32, f"the rail hide row is {m['h']}px tall: {m}")
        self.assertGreaterEqual(m["size"], 12, f"the rail hide label is {m['size']}px: {m}")
        self.assertGreaterEqual(m["alpha"], 0.75,
                                f"the rail hide label is nearly invisible: {m}")
        self.assertTrue(m["name"], f"the rail hide row has no accessible name: {m}")
        self.assertEqual(m["expanded"], "true", m)
        # Keyboard: Tab reaches it and Enter toggles, as any button does.
        page.evaluate("document.getElementById('controls-toggle-row').focus()")
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        self.assertEqual(
            page.evaluate("document.getElementById('controls-toggle-row')"
                          ".getAttribute('aria-expanded')"), "false")


class ClientWording(unittest.TestCase):
    """The static half: wording the browser test cannot reach, pinned in source."""

    # The refusal wording is in display/static/display.js after W2 moved the
    # script out of the template; the markup it renders into is still there.
    _src = read_display_sources()
    tactics = (REPO / "display" / "static" / "tactics.js").read_text(encoding="utf-8")
    cli = (REPO / "scripts" / "tactics" / "cli.py").read_text(encoding="utf-8")
    app = (REPO / "display" / "gm-display-app.py").read_text(encoding="utf-8")

    def test_the_badge_inset_is_measured_and_never_a_fixed_padding(self):
        """The static half of the badge defect, which is the half that runs when
        playwright does not. Every browser test in the file skips to green on a
        machine with no Chromium, and a stylesheet regression is exactly the kind
        that would go unnoticed there.

        Two things are pinned, and the second is the one that matters. The rule
        that gives the story room for a visible badge must read --dpb-bottom
        (display.js measures the badge and publishes its bottom) rather than a
        number. And that rule must be scoped to `:not(.tx-on)`, so it cannot win
        against the panel rule on specificity: `:has()` contributes its argument,
        which is how a fixed 124px came to override a measured --tx-bottom of
        698px and cover the story for as long as a dice request was outstanding.
        """
        rules = re.findall(r"((?:body:has\(#dice-pending-badge\.visible\)[^{]*)?"
                           r"#text-scroll\s*\{)([^}]*)\}", self._src.css, re.S)
        rules = [(s, b) for s, b in rules if "dice-pending-badge" in s]
        self.assertEqual(len(rules), 1,
                         f"expected exactly one badge inset rule, found {rules}")
        selector, rule = rules[0]
        self.assertIn(":not(.tx-on)", selector,
                      f"the badge rule can still outrank the panel rule: {selector}")
        self.assertIn("--dpb-bottom", rule,
                      f"the badge inset is not measured: {rule}")
        # Not a bare constant. `padding-top: 124px` is the defect, and so would
        # be 300px, which clears every badge in this file: what has to be pinned
        # is that the clearance is a function of the measurement. The 72px base
        # inset and the 28px gap inside the calc() are deliberate and named (the
        # 28 is the same gap the panel rule uses, and the 72 is the base inset
        # from the #text-scroll rule above), so only a wholly literal value is
        # refused here.
        pad = re.search(r"padding-top:\s*([^;]+);", rule)
        self.assertIsNotNone(pad, rule)
        self.assertIsNone(re.fullmatch(r"\s*\d+px\s*", pad.group(1)),
                          f"the badge inset is a fixed padding: {pad.group(1)!r}")
        self.assertTrue(pad.group(1).count("(") >= 2 and "var(" in pad.group(1),
                        f"the badge inset does not compute from the measurement: "
                        f"{pad.group(1)!r}")

    def test_the_badge_publishes_its_own_extent(self):
        """Both halves of the publish, because either one alone leaves the other
        broken: without the removeProperty a finished request holds the story
        down for a badge that has gone, and without the ResizeObserver the first
        measurement is all there ever is (a web font landing, or a window
        narrowing and re-wrapping the label, changes the badge's height without
        _updateDicePendingBadge running)."""
        for name, src in (("display.js", self._src.js), ("tactics.js", self.tactics)):
            self.assertIn("--dpb-bottom" if name == "display.js" else "--tx-bottom", src,
                          f"{name} never publishes its panel extent")
        self.assertIn("publishBadgeExtent", self._src.js)
        self.assertIn("removeProperty('--dpb-bottom')", self._src.js)
        self.assertIn("watchBadgeExtent", self._src.js)

    def test_no_em_dash_in_the_new_client_text(self):
        for name, text in (("display.js", self._src.js), ("tactics.js", self.tactics)):
            # "—" was already in tactics.js (the CRIT/fumble suffix) before this
            # work, so only the strings added here are checked.
            for line in text.splitlines():
                if "tx-refusal" in line or "refusal(" in line or "not your turn" in line.lower():
                    self.assertNotIn("—", line, f"{name}: {line}")

    def test_the_no_campaign_refusal_says_what_to_do(self):
        m = re.search(r"NO_CAMPAIGN = \((.*?)\)\n", self.app, re.DOTALL)
        self.assertIsNotNone(m, "NO_CAMPAIGN is not defined in gm-display-app.py")
        msg = " ".join(m.group(1).split())
        self.assertIn("combat start", msg,
                      f"the no-campaign message does not say how to fix it: {msg!r}")
        self.assertIn("No fight is open", msg, msg)

    def test_the_advantage_reaches_the_marker(self):
        """The marker is what carries the advantage, so the plumbing is pinned
        against the real engine rather than only in the display: a PendingRoll at
        disadvantage has to reach turn.pending with it attached, or the banner
        cannot say it however well it is written. Driven through cli.main with
        the campaign resolved and the engine stubbed, so this exercises the
        shipped except-branch and not a copy of it."""
        import sys
        sys.path.insert(0, str(REPO))
        try:
            from scripts.tactics import cli, roller
        finally:
            sys.path.pop(0)

        pushed = []
        real = {n: getattr(cli, n) for n in
                ("_push_pending", "_clear_pending", "_save_pending", "_camp_dir", "run")}
        cli._push_pending = lambda camp, marker: pushed.append(marker)
        cli._clear_pending = lambda camp: None
        cli._save_pending = lambda *a: None
        # A campaign path that exists, so main() gets as far as the pause.
        cli._camp_dir = lambda args: pathlib.Path(REPO)
        real_state_load = cli.state.load
        cli.state.load = lambda path: _encounter()

        def _raise(args):
            raise roller.PendingRoll("Kairos", "attack: Fire Bolt", "1d20+4", ADV[0])

        cli.run = _raise
        try:
            for adv, want in (("normal", "roll:1d20+4"),
                              ("disadvantage", "roll:1d20+4|disadvantage"),
                              ("advantage", "roll:1d20+4|advantage")):
                with self.subTest(adv=adv):
                    ADV[0] = adv
                    pushed.clear()
                    # main() takes the argument list without the program name,
                    # which is how it is called from __main__ and from the app.
                    code = cli.main(["-c", "t", "attack", "kairos",
                                     "kob-1", "fire", "bolt"])
                    self.assertEqual(code, 2, f"PendingRoll should exit 2, got {code}")
                    self.assertEqual(pushed, [want])
        finally:
            ADV[0] = "normal"
            for n, fn in real.items():
                setattr(cli, n, fn)
            cli.state.load = real_state_load

    def test_the_reaction_marker_is_resolved_to_a_token(self):
        self.assertIn("byId", self.tactics,
                      "pendingBanner cannot resolve the deciding creature's id")


if __name__ == "__main__":
    unittest.main()
