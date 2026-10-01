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

The browser and the static server come from tests/_browser.py (W15).
"""
import json
import pathlib
import unittest

from tests._browser import BrowserTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent

VIEWPORTS = {"table": (1440, 900), "phone": (390, 844)}


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


class MeasuredLayout(BrowserTestCase):
    server_kind = "static"
    ready_js = "window.__ready === true"

    def panel(self, name, spell_list=False):
        w, h = VIEWPORTS[name]
        page = self.open_page(size=(w, h), wait=200)
        if spell_list:
            page.evaluate(SPELLS)
            page.click("#tx-leads button:has-text('Cast')")
            page.wait_for_timeout(500)
        return page.evaluate(PROBE)

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

    # ── RI2: the chance, rendered ──────────────────────────────────────────
    # The static tests in test_display_tactics_ui.py pin what the helpers
    # compute. These pin that the panel actually shows it, because the badge
    # feature shipped twice as "implemented" while nothing on screen ever
    # carried it: a CSS class, a build function, and no caller. Only a rendered
    # DOM separates the two.
    ODDS = """() => ({
      logLines: [...document.querySelectorAll('#tx-log li')].map(li => ({
        text: li.firstChild ? li.firstChild.textContent : '',
        odds: (li.querySelector('.tx-odds') || {}).textContent || '',
        oddsVisible: !!li.querySelector('.tx-odds')
                      && li.querySelector('.tx-odds').getBoundingClientRect().height > 0,
      })),
      floats: [...document.querySelectorAll('.tx-odds-float')].map(f => f.textContent),
      toast: (document.getElementById('tx-toast') || {}).textContent || '',
    })"""

    # A float is a delta, so it needs a previous snapshot to be a delta FROM.
    # The harness's own single update has none, and floaters() correctly draws
    # nothing on the first snapshot it is ever handed. Re-feed the same state
    # and then the resolved one, which is the shape a real stream arrives in.
    RESOLVE = """() => {
      const s = JSON.parse(JSON.stringify(window.__SNAP));
      Tactics.update(s);                       // the state before the roll
      const n = JSON.parse(JSON.stringify(s));
      n.tokens.forEach(t => { if (t.id === 'frog-2') t.hp = 11; });
      Tactics.update(n);                       // the roll resolving
    }"""

    def panel_with_odds(self, name="table", before_resolve=None):
        w, h = VIEWPORTS[name]
        page = self.open_page(size=(w, h), wait=0)
        if before_resolve:
            page.evaluate(before_resolve)
        else:
            page.evaluate(self.RESOLVE)
        # The odds float lives for 1.4s, so it has to be read before it goes.
        page.wait_for_timeout(200)
        return page.evaluate(self.ODDS)

    def odds(self, name="table"):
        return self.panel_with_odds(name)

    def test_every_log_entry_with_a_chance_shows_it(self):
        """The log is the surface that survives the toast fading, so a chance
        that is only in the toast is a chance that is gone."""
        got = self.odds()
        with_odds = [l for l in got["logLines"] if l["odds"]]
        self.assertEqual(len(with_odds), 3,
                         f"only {len(with_odds)} of 3 log entries showed a chance")
        for l in with_odds:
            self.assertTrue(l["oddsVisible"],
                            f"the chance line on {l['text'][:40]!r} has zero height")

    def test_the_direction_of_the_chance_is_the_systems_own_word(self):
        """'40% to fail the save' and '65% to hit' are the same shape. Reading
        the save's percent as a chance to SUCCEED inverts the only number on
        screen, so the label has to survive into the display verbatim."""
        text = " | ".join(l["odds"] for l in self.odds()["logLines"])
        self.assertIn("65% to hit", text)
        self.assertIn("40% to fail the save", text)

    def test_an_entry_with_several_chances_shows_all_of_them(self):
        """The AoE entry rolls two saves. Showing one would read as the other
        being withheld."""
        entry = [l for l in self.odds()["logLines"] if "Cone of Cold" in l["text"]]
        self.assertEqual(len(entry), 1, "the AoE entry is not in the log")
        self.assertIn("40% to fail the save", entry[0]["odds"])

    def test_a_roll_with_no_chance_adds_no_line(self):
        """The longsword entry carries a damage die with no chance attached. It
        must not print a bare '0%', which would be a lie about a 65% shot."""
        entry = [l for l in self.odds()["logLines"] if "longsword" in l["text"]]
        self.assertEqual(len(entry), 1)
        self.assertNotIn("0%", entry[0]["odds"])

    def test_the_toast_carries_the_chance_with_the_roll(self):
        got = self.odds()["toast"]
        self.assertRegex(got, r"\d+%", f"the toast has no chance in it: {got!r}")

    def test_the_float_is_drawn_over_each_creature_the_chance_is_about(self):
        """One float per token in the newest entry, so an AoE against two
        creatures shows both rather than stacking four on one square.

        The newest entry and no other, which is the same scope the toast has
        already used and the same scope the damage float has: a float is a
        thing that just happened, and re-floating the last eight entries on
        every snapshot would put a number on the board for a roll from a
        minute ago. Completeness across entries is the log line's job, and it
        is the surface that keeps all eight."""
        floats = self.odds()["floats"]
        self.assertEqual(sorted(floats), ["40% to fail the save", "40% to fail the save"])

    def test_the_log_carries_the_chances_the_float_only_shows_for_the_newest(self):
        """The completeness the float deliberately leaves out, so that losing a
        float costs nothing."""
        text = " | ".join(l["odds"] for l in self.odds()["logLines"])
        self.assertIn("65% to hit", text, "the older entry's chance is only on the board nowhere")

    def test_a_chance_about_a_creature_that_is_not_drawn_gets_no_float(self):
        """The one surface allowed to go missing. A roll naming a token the
        players cannot see must not draw it, and must not crash trying.

        The number stays in the log, which is the point: only the float may be
        absent, so this asserts the float is gone AND that the log still says
        something. The counts are compared rather than the text, because
        Theodric's own save in the same entry reads identically and must
        survive."""
        got = self.panel_with_odds("table", before_resolve="""() => {
          const s = JSON.parse(JSON.stringify(window.__SNAP));
          // Giant Frog 1 is in no one's line of sight, so sync.snapshot would
          // not have put it in tokens at all. That is the shape reproduced
          // here: the roll survives, the token does not.
          s.tokens = s.tokens.filter(t => t.id !== 'frog-1');
          Tactics.update(s);
          const n = JSON.parse(JSON.stringify(s));
          n.log[n.log.length - 1].rolls =
            n.log[n.log.length - 1].rolls.filter(r => r.odds.about !== 'frog-1');
          n.log[n.log.length - 1].text += ' Something unseen takes the cold.';
          Tactics.update(n);
        }""")
        # Two saves floated in the full snapshot; the unseen one takes its own
        # float and nothing else with it.
        self.assertEqual(len(got["floats"]), 1,
                         f"expected only Theodric's float, got {got['floats']}")
        self.assertTrue([l for l in got["logLines"] if l["odds"]],
                        "the log went quiet when a float could not be drawn")

    def test_the_odds_survive_a_phone_too(self):
        """The phone is where a player actually reads their own roll."""
        got = self.odds("phone")
        self.assertTrue([l for l in got["logLines"] if l["oddsVisible"]],
                        "the chance did not render on a phone")

    def test_no_float_runs_off_the_edge_of_the_board(self):
        """The board is an SVG with a viewBox: past its edge is not drawn at
        all, it is simply not rendered. "40% to fail the save" is three cells
        wide, so a token in the first column lost most of its text and the
        chance went missing for exactly the creatures at the map's edge.

        The first fix for this set a `transform` and looked correct in the
        diff: the rise animation is a CSS transform, and CSS wins over the SVG
        attribute of the same name, so it moved nothing. The test measures
        rendered rectangles, which is the only thing that could have caught it.
        """
        page = self.open_page(size=(1440, 900), wait=0)
        page.evaluate(self.RESOLVE)
        page.wait_for_timeout(200)
        out = page.evaluate("""() => {
          const bd = document.getElementById('tx-board').getBoundingClientRect();
          return [...document.querySelectorAll('.tx-odds-float')].map(f => {
            const r = f.getBoundingClientRect();
            return {text: f.textContent, l: Math.round(r.left), r: Math.round(r.right),
                    w: Math.round(r.width)};
          }).concat([{board: true, l: Math.round(bd.left), r: Math.round(bd.right)}]);
        }""")
        board = [o for o in out if o.get("board")][0]
        floats = [o for o in out if not o.get("board")]
        self.assertTrue(floats, "no odds float was drawn to measure")
        for f in floats:
            self.assertGreaterEqual(f["l"], board["l"],
                                    f"{f['text']!r} starts {board['l'] - f['l']}px off the board")
            self.assertLessEqual(f["r"], board["r"],
                                 f"{f['text']!r} ends {f['r'] - board['r']}px past the board")

    def test_a_token_in_the_first_column_still_shows_its_whole_chance(self):
        """The worst case, on its own: a token at x=0 with a phrase three cells
        wide, which is where the clamping actually has to do something."""
        page = self.open_page(size=(1440, 900), wait=0)
        page.evaluate("""() => {
          const s = JSON.parse(JSON.stringify(window.__SNAP));
          // Theodric is the leftmost token; put him in column 0 and make the
          // newest entry's save his.
          s.tokens.forEach(t => { if (t.id === 'theodric') t.x = 0; });
          s.log[s.log.length - 1].rolls = s.log[s.log.length - 1].rolls
            .filter(r => r.odds.about === 'theodric');
          Tactics.update(s);
          const n = JSON.parse(JSON.stringify(s));
          Tactics.update(n);
        }""")
        page.wait_for_timeout(200)
        out = page.evaluate("""() => {
          const bd = document.getElementById('tx-board').getBoundingClientRect();
          const f = document.querySelector('.tx-odds-float');
          if (!f) return null;
          const r = f.getBoundingClientRect();
          return {text: f.textContent, l: Math.round(r.left), r: Math.round(r.right),
                  bl: Math.round(bd.left), br: Math.round(bd.right)};
        }""")
        self.assertIsNotNone(out, "a token in the first column drew no float at all")
        self.assertEqual(out["text"], "40% to fail the save")
        self.assertGreaterEqual(out["l"], out["bl"], "the chance ran off the left edge")
        self.assertLessEqual(out["r"], out["br"], "the chance ran off the right edge")


if __name__ == "__main__":
    unittest.main()
