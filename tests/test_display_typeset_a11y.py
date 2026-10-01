"""Wave 2 display polish: narration typesetting, input-panel fit, type and target
floor, accessibility basics. Browser tests load the real index.html through the
Flask app on an ephemeral port and are skipped when playwright or Chromium is
absent. The font-size scan is static and always runs.

The browser and the display server come from tests/_browser.py (W15).
"""
import re
import unittest

from tests._browser import (
    LAYOUT_PROBE,
    MIN_READING_COLUMN,
    VIEWPORT_MATRIX,
    BrowserTestCase,
    assert_viewport_matrix,
)
from tests.display_settle import present
from tests.display_sources import read_display_sources

SIZES = ((1440, 900), (1024, 768), (768, 1024), (1200, 784))

# N-1: the widths where the two 340px side rails used to squeeze the prose.
# 1100 is the breakpoint: above it the desktop inset still leaves a readable
# column, below it the rails stop being side columns.
NARROW = ((1100, 768), (1024, 768), (900, 800), (768, 1024), (700, 900),
          (640, 900), (480, 800), (390, 844))

# font-size declarations below 12px that are allowed, with the reason. Empty on
# purpose: anything new that is this small is a bug, not a style.
SMALL_FONT_ALLOW = {}

SAMPLE = ("## Station 2 - The Note\n### Scene 0\n"
          "The `first_sound` is **loud**, *very* loud.\nSecond source line.\n\n"
          "1. one\n2. two\n- a\n- b\n")


class TypeFloor(unittest.TestCase):
    def test_no_font_size_declaration_under_12px(self):
        # Every font-size declaration is CSS, and the CSS is display.css since W2
        # split it out of the template. The script sets two inline sizes, so it is
        # scanned too rather than trusted.
        _src = read_display_sources()
        src = _src.css + "\n" + _src.js
        src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
        bad = []
        for m in re.finditer(r"font-size:\s*(\d+(?:\.\d+)?)px", src):
            if float(m.group(1)) < 12:
                line = src.count("\n", 0, m.start()) + 1
                ctx = src[max(0, m.start() - 40):m.end()].strip().replace("\n", " ")
                if ctx not in SMALL_FONT_ALLOW:
                    bad.append(f"line {line}: {m.group(0)}")
        self.assertEqual(bad, [], "font-size under 12px: " + "; ".join(bad[:10]))

    def test_no_em_dash_in_new_markup(self):
        # <main> is markup, so it stayed in the template (W2).
        self.assertNotIn("—", re.search(r"<main id=\"main-content\">.*?</main>",
                                         read_display_sources().template, re.S).group(0))


class Browser(BrowserTestCase):
    module_name = "gm_display_app_typeset"

    # Every measurement in this file is of a DEFAULT layout, and a default layout
    # needs a display with no roster on it. `display/stats.json` is gitignored
    # runtime state that gm-display-app.py loads at import, so whichever display
    # test posted a roster last leaves one behind and the character sidebar takes
    # its 210px column: at 390 the reading column then measures 138px of 390, and
    # the scan below fails on a tree that has not changed. Reproduced on clean
    # origin/main by running test_display_xss.py first. The viewport matrix at the
    # bottom of this file already called clear_display_state() for exactly this
    # reason; the two column scans had not caught up.

    def open(self, w, h, **ctx):
        return self.open_page(size=(w, h), wait=500, **ctx)

    def narrate(self, page, text=SAMPLE):
        page.evaluate("t => { handleIncomingText(t); instantFlush(); }", text)
        present(page, "() => !!document.querySelector('#text-content .dm-block')",
                "the narration to render")

    def open_input(self, page):
        """Open the party input panel, and wait until it is open.

        Five of the tests below measure a layout that only exists with the panel
        expanded, and each of them used to sleep for it. The header toggles a
        class and that class is what hides the body, so the honest condition is
        a box rather than a length of time: `#input-body` having a height.
        Reading `display` instead would be satisfied one style recalc before the
        geometry the test is about has settled.
        """
        page.click("#input-panel-header")
        present(page, """() => {
          const r = document.getElementById('input-body').getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        }""", "the party input panel to open")
        return page

    # 1. typesetting ---------------------------------------------------------
    def test_markdown_renders_as_elements_not_literals(self):
        page = self.open(1200, 784)
        self.narrate(page)
        got = page.evaluate("""() => { const b = document.querySelector('.dm-block');
          const q = s => b.querySelectorAll(s).length;
          return {h2: q('h2'), h3: q('h3'), strong: q('strong'), em: q('em'),
                  code: q('code'), ol: q('ol > li'), ul: q('ul > li'), p: q('p:not(:empty)'),
                  text: b.textContent, para: b.querySelector('p').textContent}; }""")
        self.assertEqual((got["h2"], got["h3"], got["strong"], got["em"], got["code"]), (1, 1, 1, 1, 1))
        self.assertEqual((got["ol"], got["ul"]), (2, 2))
        for raw in ("##", "**", "`", "1. one"):
            self.assertNotIn(raw, got["text"])
        # single newline joins into ONE paragraph; blank line/block breaks split
        self.assertEqual(got["p"], 1)
        self.assertIn("loud. Second source line.", got["para"])

    def test_llm_text_is_never_parsed_as_html(self):
        page = self.open(1200, 784)
        page.evaluate("window.__pwn = 0")
        self.narrate(page, '<img src=x onerror="window.__pwn=1"> **b** <script>window.__pwn=2</script>\n'
                           '## <b>h</b>\n- <i onclick="1">x</i>\n')
        got = page.evaluate("""() => ({pwn: window.__pwn,
          imgs: document.querySelectorAll('.dm-block img:not(.block-badge)').length,
          tags: document.querySelectorAll('.dm-block script, .dm-block b, .dm-block i').length,
          text: document.querySelector('.dm-block').textContent})""")
        self.assertEqual((got["pwn"], got["imgs"], got["tags"]), (0, 0, 0))
        self.assertIn("<img", got["text"])

    def test_speaker_chip_and_markdown_in_speech(self):
        page = self.open(1200, 784)
        page.evaluate("renderNPCBlock('Hesper', '*She goes still.* **\"No lock,\"** she says.\\nThen more.')")
        present(page, "() => !!document.querySelector('.npc-block .npc-name')",
                "the NPC block to render")
        got = page.evaluate("""() => { const n = document.querySelector('.npc-block .npc-name');
          const cs = getComputedStyle(n);
          return {name: n.textContent, radius: parseFloat(cs.borderTopLeftRadius),
                  size: parseFloat(cs.fontSize), em: document.querySelectorAll('.npc-block em').length,
                  strong: document.querySelectorAll('.npc-block strong').length,
                  text: document.querySelector('.npc-block p').textContent}; }""")
        self.assertEqual(got["name"], "Hesper")
        self.assertGreaterEqual(got["radius"], 8)
        self.assertGreaterEqual(got["size"], 12)
        self.assertEqual((got["em"], got["strong"]), (1, 1))
        self.assertNotIn("*", got["text"])

    def test_measure_and_leading(self):
        long = "word " * 200
        for w, h in SIZES:
            with self.subTest(size=(w, h)):
                page = self.open(w, h)
                self.narrate(page, long)
                m = page.evaluate("""() => { const p = document.querySelector('.dm-block p');
                  const cs = getComputedStyle(p); const fs = parseFloat(cs.fontSize);
                  const c = document.createElement('span'); c.textContent = '0'.repeat(100);
                  c.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap';
                  p.appendChild(c); const ch = c.getBoundingClientRect().width / 100; c.remove();
                  return {chars: p.getBoundingClientRect().width / ch,
                          lh: parseFloat(cs.lineHeight) / fs, width: p.getBoundingClientRect().width}; }""")
                self.assertLessEqual(m["chars"], 76, m)
                self.assertGreaterEqual(m["lh"], 1.55, m)
                self.assertLessEqual(m["lh"], 1.65, m)
                self.assertGreaterEqual(m["width"], 300, f"prose squeezed: {m}")

    # 1b. the reading column at narrow widths (N-1) --------------------------
    COLUMN = """() => {
      const tc = document.getElementById('text-content');
      const p = document.querySelector('#text-content p') || tc;
      const c = document.createElement('span'); c.textContent = '0'.repeat(100);
      c.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap';
      p.appendChild(c); const ch = c.getBoundingClientRect().width / 100; c.remove();
      const rail = document.getElementById('audio-controls').getBoundingClientRect();
      const col = tc.getBoundingClientRect();
      return {vw: innerWidth, w: col.width, chars: col.width / ch,
              padL: parseFloat(getComputedStyle(document.getElementById('text-scroll')).paddingLeft),
              padR: parseFloat(getComputedStyle(document.getElementById('text-scroll')).paddingRight),
              rail: {l: rail.left, r: rail.right, top: rail.top, bottom: rail.bottom},
              colTop: col.top,
              overflowX: document.documentElement.scrollWidth - innerWidth};
    }"""

    def test_the_reading_column_is_not_squeezed_at_narrow_widths(self):
        """The bug: #text-scroll carried the desktop 340px inset on both sides at
        every width, so a 768px window left #text-content 84px wide and the prose
        ran one word per line. The column has to take the width once the rails
        stop being side columns."""
        for w, h in NARROW:
            with self.subTest(width=w):
                self.clear_display_state()
                page = self.open(w, h)
                self.narrate(page)
                m = page.evaluate(self.COLUMN)
                # An 84px ribbon is 11% of a 768px window; a real column is not.
                self.assertGreaterEqual(m["w"], 0.5 * m["vw"],
                                        f"the column is {m['w']:.0f}px of {m['vw']}px: {m}")
                self.assertGreaterEqual(m["chars"], 34, m)
                self.assertLessEqual(m["overflowX"], 0, m)

    def test_the_settings_rail_never_sits_on_the_prose(self):
        """Collapsed or open: the rail is a right-hand column on a desktop and a
        row across the reserved top band below the breakpoint, so the reading
        column is clear of it in both states."""
        for w, h in NARROW:
            with self.subTest(width=w):
                self.clear_display_state()
                page = self.open(w, h)
                self.narrate(page)
                shut = page.evaluate(self.COLUMN)
                self.assertLessEqual(shut["rail"]["bottom"], shut["colTop"] + 1,
                                     f"the collapsed rail runs over the column: {shut}")
                # The wait is on the toggle having taken effect, not on a
                # direction. Below the 1100px breakpoint the rail STARTS
                # collapsed (display.js _setControlsVisible), so the click opens
                # it there and closes it above, and `shut`/`open_` are the two
                # states in whichever order this page happened to begin in.
                was = page.get_attribute("#controls-toggle-row", "aria-expanded")
                page.click("#controls-toggle-row")
                present(page, """(was) => document.getElementById('controls-toggle-row')
                                        .getAttribute('aria-expanded') !== was""",
                        "the settings rail to toggle", arg=was)
                open_ = page.evaluate(self.COLUMN)
                self.assertLessEqual(open_["rail"]["bottom"], open_["colTop"] + 1,
                                     f"the open rail runs over the column: {open_}")
                self.assertGreater(open_["rail"]["r"] - open_["rail"]["l"], 0, open_)

    # 2. input panel ---------------------------------------------------------

    def test_roll_button_and_textarea_fit(self):
        for w, h in SIZES:
            with self.subTest(size=(w, h)):
                page = self.open(w, h)
                self.open_input(page)
                m = page.evaluate("""() => { const r = id => document.getElementById(id).getBoundingClientRect();
                  const p = r('input-panel'), roll = r('dp-roll'), ta = r('player-input-text');
                  const ts = document.getElementById('text-scroll').getBoundingClientRect();
                  return {pTop: p.top, pBottom: p.bottom, rollTop: roll.top, rollBottom: roll.bottom,
                          taH: ta.height, vh: innerHeight}; }""")
                self.assertGreaterEqual(m["pTop"], 0, m)
                self.assertLessEqual(m["pBottom"], m["vh"], m)
                self.assertLessEqual(m["rollBottom"], m["pBottom"] + 1, m)
                self.assertGreaterEqual(m["rollTop"], m["pTop"], m)
                self.assertGreaterEqual(m["taH"], 44, f"textarea clipped: {m}")
                self.assertTrue(page.is_visible("#dp-roll"))

    def test_textarea_autosizes_and_dice_options_collapse(self):
        page = self.open(1200, 784)
        page.click("#input-panel-header")
        self.assertFalse(page.is_visible("#dp-options"))
        page.click("#dp-toggle")
        self.assertTrue(page.is_visible("#dp-options"))
        self.assertEqual(page.get_attribute("#dp-toggle", "aria-expanded"), "true")
        page.click("#dp-toggle")
        before = page.evaluate("document.getElementById('player-input-text').offsetHeight")
        page.fill("#player-input-text", "line\n" * 6)
        page.dispatch_event("#player-input-text", "input")
        after = page.evaluate("document.getElementById('player-input-text').offsetHeight")
        self.assertGreater(after, before)

    def test_empty_send_focuses_and_explains(self):
        page = self.open(1200, 784)
        page.click("#input-panel-header")
        page.click("#send-btn")
        self.assertEqual(page.evaluate("document.activeElement.id"), "player-input-text")
        self.assertTrue(page.is_visible("#input-error"))
        self.assertTrue(page.inner_text("#input-error").strip())

    def test_send_failure_is_mirrored_into_the_error_region(self):
        page = self.open(1200, 784)
        page.route("**/player-input/send", lambda route: route.fulfill(status=500, body="boom"))
        page.click("#input-panel-header")
        page.fill("#player-input-text", "I open the door")
        page.click("#send-btn")
        page.wait_for_function("document.getElementById('input-error').textContent.includes('500')", timeout=8000)
        self.assertEqual(page.input_value("#player-input-text"), "I open the door")
        # typing again retires the stale failure label and message
        page.fill("#player-input-text", "I open the door now")
        page.dispatch_event("#player-input-text", "input")
        self.assertEqual(page.inner_text("#send-btn").strip().lower(), "send")
        self.assertFalse(page.is_visible("#input-error"))

    # 3. type and target floor -----------------------------------------------
    def test_rendered_text_is_at_least_12px(self):
        for w, h in SIZES:
            with self.subTest(size=(w, h)):
                page = self.open(w, h)
                self.narrate(page)
                page.evaluate("renderNPCBlock('Hesper', 'hi')")
                self.open_input(page)
                small = page.evaluate("""() => { const out = [];
                  document.querySelectorAll('body *').forEach(e => {
                    const r = e.getBoundingClientRect(); if (!r.width || !r.height) return;
                    if (!([...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))) return;
                    const cs = getComputedStyle(e); if (cs.visibility === 'hidden' || cs.display === 'none') return;
                    const f = parseFloat(cs.fontSize);
                    if (f < 12) out.push((e.id ? '#' + e.id : e.tagName + '.' + e.className) + ' ' + f); });
                  return out; }""")
                self.assertEqual(small, [])

    TARGETS = """() => { const out = [];
      document.querySelectorAll('button,a[href],input:not([type=hidden]),select,textarea,[role=button]').forEach(e => {
        const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
        if (!r.width || !r.height || cs.visibility === 'hidden' || e.closest('[hidden]')) return;
        if (e.classList.contains('skip-link')) return;
        if (r.height < MIN - 0.5 || r.width < MIN - 0.5)
          out.push((e.id ? '#' + e.id : e.tagName + '.' + e.className) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height)); });
      return out; }"""

    def test_interactive_targets_are_at_least_32px(self):
        for w, h in SIZES:
            with self.subTest(size=(w, h)):
                page = self.open(w, h)
                self.open_input(page)
                self.assertEqual(page.evaluate(self.TARGETS.replace("MIN", "32")), [])

    def test_interactive_targets_are_at_least_44px_on_touch(self):
        page = self.open(768, 1024, has_touch=True, is_mobile=True)
        self.assertTrue(page.evaluate("matchMedia('(pointer: coarse)').matches"))
        self.open_input(page)
        bad = page.evaluate(self.TARGETS.replace("MIN", "44"))
        self.assertEqual(bad, [])

    # 4. accessibility -------------------------------------------------------
    def test_landmarks_labels_and_live_log(self):
        page = self.open(1200, 784)
        page.click("#input-panel-header")
        got = page.evaluate("""() => { const log = document.getElementById('text-scroll');
          const unnamed = [];
          document.querySelectorAll('button,[role=button],textarea,input:not([type=hidden])').forEach(e => {
            const r = e.getBoundingClientRect(); if (!r.width || !r.height) return;
            const name = (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.textContent ||
                          e.title || e.placeholder || '').trim();
            if (!name) unnamed.push(e.id || e.className || e.tagName); });
          return {main: document.querySelectorAll('main').length, h1: document.querySelectorAll('h1').length,
                  inMain: !!document.querySelector('main #text-scroll'), role: log.getAttribute('role'),
                  live: log.getAttribute('aria-live'), label: document.getElementById('player-input-text').getAttribute('aria-label'),
                  unnamed, skip: !!document.querySelector('a.skip-link[href^="#"]')}; }""")
        self.assertEqual((got["main"], got["h1"], got["inMain"]), (1, 1, True))
        self.assertEqual((got["role"], got["live"]), ("log", "polite"))
        self.assertTrue(got["label"])
        self.assertEqual(got["unnamed"], [])
        self.assertTrue(got["skip"])

    def test_skip_link_is_first_tab_stop_and_focus_ring_is_visible(self):
        page = self.open(1200, 784)
        page.keyboard.press("Tab")
        self.assertEqual(page.evaluate("document.activeElement.className"), "skip-link")
        self.assertGreaterEqual(page.evaluate("document.activeElement.getBoundingClientRect().top"), 0)
        page.click("#input-panel-header")
        page.focus("#player-input-text")
        page.keyboard.press("Tab")          # keyboard focus => :focus-visible
        ring = page.evaluate("""() => { const cs = getComputedStyle(document.activeElement);
          return [cs.outlineStyle, parseFloat(cs.outlineWidth)]; }""")
        self.assertNotEqual(ring[0], "none")
        self.assertGreaterEqual(ring[1], 2)

    def _raf_count(self, page_args, settle=1200):
        """Frames drawn in `settle` ms, counted by an init script.

        The one sleep in this file that stays a sleep, and the reason the rest
        are not: this measures a RATE, so the answer genuinely is "how much time
        were you given". The two negative cases are the absence of the loop, and
        no predicate can wait for a thing that should not be there.
        """
        context = self.browser.new_context(viewport={"width": 1200, "height": 784}, **page_args.get("ctx", {}))
        self.addCleanup(context.close)
        context.add_init_script("""window.__raf = 0; const o = window.requestAnimationFrame;
          window.requestAnimationFrame = function (cb) { window.__raf++; return o.call(window, cb); };""" +
                                page_args.get("init", ""))
        page = context.new_page()
        page.goto(self.url(), wait_until="load")
        page.wait_for_timeout(settle)
        return page.evaluate("window.__raf")

    def test_animation_loop_runs_normally_but_stops_for_reduced_motion_and_hidden_tab(self):
        normal = self._raf_count({})
        self.assertGreater(normal, 10, "the counter should see the loop running")
        reduced = self._raf_count({"ctx": {"reduced_motion": "reduce"}})
        self.assertLessEqual(reduced, 3, f"reduced motion still animates: {reduced}")
        hidden = self._raf_count({"init": "Object.defineProperty(Document.prototype, 'hidden', {get: () => true});"})
        self.assertLessEqual(hidden, 3, f"hidden tab still animates: {hidden}")

    # 5. the viewport matrix (W15) --------------------------------------------
    #
    # The audit's three widths, measured as one thing rather than three: no
    # horizontal overflow, a reading column of at least MIN_READING_COLUMN, and
    # no rail painted over the party input panel or the new-content pill. 768 is
    # the one that matters most: it is where the story column used to collapse to
    # an 84px ribbon, and it is the width the re-test report measured. The
    # matrix is here rather than in the audit's own words because W9 extends it,
    # and a second copy of these three assertions is how they stop being run.
    def test_the_viewport_matrix_holds_at_every_width_that_matters(self):
        for name, w, h in VIEWPORT_MATRIX:
            for expanded in (False, True):
                with self.subTest(viewport=name, input_panel="expanded" if expanded else "collapsed"):
                    self.clear_display_state()
                    page = self.open(w, h)
                    self.narrate(page)
                    if expanded:
                        self.open_input(page)
                    assert_viewport_matrix(self, page.evaluate(LAYOUT_PROBE),
                                           where=f"{name} {w}x{h} input "
                                                 f"{'expanded' if expanded else 'collapsed'}")

    def test_the_viewport_matrix_agrees_with_the_narrow_width_scan(self):
        """The matrix floor is the same fact the NARROW scan above asserts, at
        the three widths the audit names. It is stated a second time because the
        two are measured at different widths and a reader who finds one failing
        should not have to know which file owns it."""
        for name, w, h in VIEWPORT_MATRIX:
            with self.subTest(viewport=name):
                self.clear_display_state()
                page = self.open(w, h)
                self.narrate(page)
                m = page.evaluate(self.COLUMN)
                self.assertGreaterEqual(m["w"], MIN_READING_COLUMN, f"{name}: {m}")
                self.assertGreaterEqual(m["w"], 0.5 * m["vw"], f"{name}: {m}")
                self.assertLessEqual(m["overflowX"], 0, f"{name}: {m}")


if __name__ == "__main__":
    unittest.main()
