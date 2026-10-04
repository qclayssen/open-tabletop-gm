"""The wave-2 re-test findings N-1 to N-6, measured in a browser.

Each test here pins a defect the re-test reproduced against a real page, with
the numbers the report gave. The pattern is the one
test_display_tactics_layout.py established: drive the real index.html through
the Flask app on an ephemeral port, hand the panel a snapshot in the shape
scripts/tactics/sync.py produces, and measure what a browser actually did.
Skipped when playwright or Chromium is absent.

The browser and the display server come from tests/_browser.py (W15).

  N-1  the reading column collapses to a ribbon at narrow widths
  N-2  the map panel covers the story, and the folded panel clips its chips
  N-3  the board opens on the hero with the enemies off-screen, and a re-render
       throws away the player's scroll
  N-4  the reaction prompt names the acting creature, not the deciding one
  N-5  the roll banner omits the advantage the engine is rolling under
  N-6  a refusal is unreadable, contradicts the banner, and covers the log
"""
import itertools
import json
import pathlib
import re
import unittest

from tests._browser import BrowserTestCase
from tests.display_settle import calm, present, settle
from tests.display_sources import read_display_sources

REPO = pathlib.Path(__file__).resolve().parent.parent

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


class Panel(BrowserTestCase):
    """The real page, with the combat panel driven by Tactics.update()."""

    module_name = "gm_display_app_retest"

    # ── helpers ───────────────────────────────────────────────────────────
    # `#text-scroll` transitions padding-top over 0.4s, and --tx-bottom (the
    # measured panel bottom) feeds it. Every measurement in this file is a
    # geometry read, so it wants the settled layout, not a frame of an animation.
    # Taking the transition off is what makes "wait until it stops changing"
    # mean something: with the transition live, "not changing yet" and "finished"
    # are the same observation, and the first is what you get. See calm() and
    # settle() in tests/display_settle.py, which own that reasoning for every
    # display browser test rather than for this file alone.

    def open(self, size=DESKTOP, snap=None, narrate=True, **ctx):
        page = self.open_page(size=size, wait=0, **ctx)
        calm(page)
        if narrate:
            page.evaluate("t => { handleIncomingText(t); instantFlush(); }", NARRATION)
        if snap is not None:
            page.evaluate("s => Tactics.update(s)", json.loads(json.dumps(snap)))
            self.settle(page)
        return page

    def settle(self, page):
        """Block until the inset has caught up with everything overlaying the story.

        The predicate and the three ways it went wrong before it are written out
        in tests/display_settle.py. It is bound as a method because most of the
        calls here are `self.settle(page)`.
        """
        settle(page)

    def present(self, page, predicate, what, arg=None):
        present(page, predicate, what, arg=arg)

    def scroll_board_right(self, page):
        """Scroll the board to its far right, and wait until it has got there.

        A board scroll is clamped to the board's own extent, so "as far right as
        the map goes" is the only position that can be asked for at any map
        width; a fixed pixel count is either past the end or short of it. The
        read that follows has to be of a scrolled board: taken before the scroll
        took effect it is 0, and both tests that use this then measure what the
        panel did to a position the player never had.
        """
        page.evaluate("""() => { const b = document.getElementById('tx-board');
          b.scrollLeft = b.scrollWidth; }""")
        present(page, """() => { const b = document.getElementById('tx-board');
          return b.scrollLeft >= b.scrollWidth - b.clientWidth - 1; }""",
                "the board to scroll to its far right")

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
        self.scroll_board_right(page)
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
        self.scroll_board_right(page)
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
        # The refusal comes back through a fetch and is written to the banner
        # when it lands, so wait for the banner to say it rather than for a
        # length of time: 700ms was enough on an idle machine and a race on a
        # loaded one, where the failure reads as the banner being empty.
        self.present(page, """() => document.getElementById('tx-banner')
                                       .textContent.includes('Kobold 2')""",
                     "the refusal to reach the banner")
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
        still = page.inner_text("#tx-banner")
        self.assertIn("Kobold 2's turn", still,
                      f"the refusal left with the toast: {still!r}")

    def test_a_refusal_outlives_the_toast_timer(self):
        """The toast's 6s expiry is what the banner has to survive: before, the
        only copy of the reason a click did nothing was on a timer.

        Waited on the banner rather than on the toast. The refusal is written to
        one surface (#300 item 0.4 removed act()'s second one), so a wait for the
        toast to come up would be a wait for the copy that no longer exists.

        The toast is read while the refusal has just landed, not after the sleep,
        and the order matters. The toast hides itself at 6s whatever wrote it, so
        a `toast.hidden` read after the sleep is true of the double-showing code
        as well as of the fixed code: it passes on the defect it is meant to
        catch. Read inside the 6s window it can only be true if nothing put the
        refusal there at all.
        """
        page = self.open(DESKTOP, snapshot())
        page.route("**/combat/do", lambda route: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"error": "There is no player's turn open right now. "
                                      "It is Kobold 2's turn. Nothing was sent."})))
        page.click("#tx-actions button:has-text('End turn')")
        self.present(page, """() => document.getElementById('tx-banner')
                                       .textContent.includes('Kobold 2')""",
                     "the refusal to reach the banner")
        self.assertTrue(
            page.evaluate("() => document.getElementById('tx-toast').hidden"),
            "the refusal was raised on the toast as well as on the banner")
        page.wait_for_timeout(6500)        # longer than the toast's own 6s
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
        self.present(page, """() => document.getElementById('tx-banner')
                                       .textContent.toLowerCase().includes("no player's turn")""",
                     "the refusal to reach the banner")
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
        self.present(page, """(was) => document.getElementById('controls-toggle-row')
                                      .getAttribute('aria-expanded') !== was""",
                     "Enter to toggle the settings rail", arg=m["expanded"])
        self.assertEqual(
            page.evaluate("document.getElementById('controls-toggle-row')"
                          ".getAttribute('aria-expanded')"),
            "false" if m["expanded"] == "true" else "true")


class ClientWording(unittest.TestCase):
    """The static half: wording the browser test cannot reach, pinned in source."""

    # The refusal wording is in display/static/display.js after W2 moved the
    # script out of the template; the markup it renders into is still there.
    _src = read_display_sources()
    tactics = (REPO / "display" / "static" / "tactics.js").read_text(encoding="utf-8")
    cli = (REPO / "scripts" / "tactics" / "cli.py").read_text(encoding="utf-8")
    app = (REPO / "display" / "gm-display-app.py").read_text(encoding="utf-8")

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
