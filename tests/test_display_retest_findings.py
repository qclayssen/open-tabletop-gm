"""The wave-2 re-test findings N-2 and N-3, measured in a browser.

Each test here pins a defect the re-test reproduced against a real page, with
the numbers the report gave. The pattern is the one
test_display_tactics_layout.py established: drive the real index.html through
the Flask app on an ephemeral port, hand the panel a snapshot in the shape
scripts/tactics/sync.py produces, and measure what a browser actually did.
Skipped when playwright or Chromium is absent.

  N-2  the map panel covers the story, and the folded panel clips its chips
  N-3  the board opens on the hero with the enemies off-screen, and a re-render
       throws away the player's scroll
"""
import importlib.util
import itertools
import json
import pathlib
import threading
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright
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
    def open(self, size=DESKTOP, snap=None, narrate=True, **ctx):
        context = self.browser.new_context(viewport={"width": size[0], "height": size[1]}, **ctx)
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        page.wait_for_timeout(400)
        if narrate:
            page.evaluate("t => { handleIncomingText(t); instantFlush(); }", NARRATION)
        if snap is not None:
            page.evaluate("s => Tactics.update(s)", json.loads(json.dumps(snap)))
            page.wait_for_timeout(400)
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
        page.wait_for_timeout(400)
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
        page.wait_for_timeout(300)

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
        page.wait_for_timeout(400)
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
        page.wait_for_timeout(300)
        page.evaluate("document.getElementById('tx-board').scrollLeft = 1200")
        page.wait_for_timeout(150)
        self.assertGreater(page.evaluate("document.getElementById('tx-board').scrollLeft"), 0,
                           "could not scroll away from the creatures")
        page.evaluate("s => Tactics.update(s)", snapshot(w=40, h=20))
        page.wait_for_timeout(400)
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
        cases = ((DESKTOP, 12, 9, True),      # 606px inner: 346 beside a column, so stack
                 ((1440, 900), 12, 9, False),  # 846px inner: 586 beside it, so keep it
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


if __name__ == "__main__":
    unittest.main()
