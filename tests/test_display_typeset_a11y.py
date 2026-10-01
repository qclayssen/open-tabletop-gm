"""Wave 2 display polish: narration typesetting, input-panel fit, type and target
floor, accessibility basics. Browser tests load the real index.html through the
Flask app on an ephemeral port and are skipped when playwright or Chromium is
absent. The font-size scan is static and always runs.
"""
import importlib.util
import pathlib
import re
import threading
import unittest

from tests.display_sources import read_display_sources
from tests.display_settle import box_settled, page_ready, present

REPO = pathlib.Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:                                        # pragma: no cover
    HAVE_PLAYWRIGHT = False

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


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from werkzeug.serving import make_server
        spec = importlib.util.spec_from_file_location(
            "gm_display_app_typeset", str(REPO / "display" / "gm-display-app.py"))
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
        except Exception as exc:
            cls.httpd.shutdown()
            raise unittest.SkipTest(f"chromium is not available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def open(self, w, h, **ctx):
        context = self.browser.new_context(viewport={"width": w, "height": h}, **ctx)
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        # `wait_until="load"` already covers the scripts. This covers the two things
        # that are true a beat later and that every measurement below reads wrong
        # until they are: document.fonts has settled (a 100-character probe span
        # measured before Cinzel lands gives the wrong characters-per-line, which is
        # the whole of `test_measure_and_leading`), and display.js's own globals
        # are installed. Measured on a warm page both are already true when load
        # fires, so this normally costs nothing, which is the point: the 500ms it
        # replaces was a fixed cost on every test in the file to cover a
        # condition that is either true immediately or never.
        page_ready(page)
        self.addCleanup(context.close)
        return page

    def narrate(self, page, text=SAMPLE):
        page.evaluate("t => { handleIncomingText(t); instantFlush(); }", text)
        # Not a settle, and the distinction is measured rather than assumed. The
        # block carries a `fadeIn` animation (display.css:172), so it is at opacity
        # 0 for the first 600ms and a test that waited for the fade would be
        # waiting 600ms to read the same numbers. It does not: measured across three
        # page loads, the block's height is final and its element counts are
        # identical at t=0 and t=+700ms, and the font-size scan below walks the
        # same 44 elements at both, because it skips `visibility: hidden` and
        # `display: none` but not `opacity: 0`. So the block is present with its
        # final box the moment instantFlush returns, and the 200ms this replaces
        # was waiting for nothing the assertions in this file read.
        present(page, ".dm-block")

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
        # narrate() already waited for the block to be laid out, so this second
        # sleep was reading the same DOM twice with a 200ms gap and nothing in
        # between. If a payload is ever going to execute it executes when it is
        # parsed, which is before the block exists.
        got = page.evaluate("""() => ({pwn: window.__pwn,
          imgs: document.querySelectorAll('.dm-block img:not(.block-badge)').length,
          tags: document.querySelectorAll('.dm-block script, .dm-block b, .dm-block i').length,
          text: document.querySelector('.dm-block').textContent})""")
        self.assertEqual((got["pwn"], got["imgs"], got["tags"]), (0, 0, 0))
        self.assertIn("<img", got["text"])

    def test_speaker_chip_and_markdown_in_speech(self):
        page = self.open(1200, 784)
        page.evaluate("renderNPCBlock('Hesper', '*She goes still.* **\"No lock,\"** she says.\\nThen more.')")
        # renderNPCBlock builds the element synchronously, and what is read below is
        # text, element counts and two computed style values, none of which a fade
        # is still changing. The 300ms was covering the arrival of something that
        # had already arrived.
        present(page, ".npc-block")
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
                page = self.open(w, h)
                self.narrate(page)
                shut = page.evaluate(self.COLUMN)
                self.assertLessEqual(shut["rail"]["bottom"], shut["colTop"] + 1,
                                     f"the collapsed rail runs over the column: {shut}")
                page.click("#controls-toggle-row")
                # The rail opening, which is a layout change and not a timer.
                # `#audio-controls` has no transition on its box (display.css gives
                # the panel a border-colour transition and the rows a background
                # one), so this normally settles on the first poll, where the 400ms
                # was paying for it on every one of the eight widths.
                box_settled(page, "#audio-controls")
                open_ = page.evaluate(self.COLUMN)
                self.assertLessEqual(open_["rail"]["bottom"], open_["colTop"] + 1,
                                     f"the open rail runs over the column: {open_}")
                self.assertGreater(open_["rail"]["r"] - open_["rail"]["l"], 0, open_)

    # 2. input panel ---------------------------------------------------------

    def open_input_panel(self, page):
        """Expand the Party Input panel and wait for it to be where it is going.

        Four tests below do this, and all four were sleeping a fixed 250 to 300ms
        afterwards. `#input-body` is `display: none` when collapsed (display.css:1588)
        and the panel's only transition is a border colour, so the box is final the
        moment the class flips. Measured: the panel is at top 438 / height 324 on
        the first poll and stays there for 840ms after it.

        Waiting on the panel rather than on `#input-body` is deliberate: the body is
        what the click unhides, but every assertion below reads the PANEL's box
        (does it fit the window, does the roll button sit inside it), so the panel
        is the thing whose size is the subject.
        """
        page.click("#input-panel-header")
        box_settled(page, "#input-panel")
        return page

    def test_roll_button_and_textarea_fit(self):
        for w, h in SIZES:
            with self.subTest(size=(w, h)):
                page = self.open(w, h)
                self.open_input_panel(page)
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
                # The scan below walks every element with a box, so it has to be
                # the panel's final box: a panel still expanding is a panel whose
                # hidden rows have not been measured yet.
                self.open_input_panel(page)
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
                # A target measured while the panel is still expanding is a target
                # measured at the wrong size, and a 32px floor is exactly the kind
                # of number a half-open panel is short of.
                self.open_input_panel(page)
                self.assertEqual(page.evaluate(self.TARGETS.replace("MIN", "32")), [])

    def test_interactive_targets_are_at_least_44px_on_touch(self):
        page = self.open(768, 1024, has_touch=True, is_mobile=True)
        self.assertTrue(page.evaluate("matchMedia('(pointer: coarse)').matches"))
        self.open_input_panel(page)
        bad = page.evaluate(self.TARGETS.replace("MIN", "44"))
        self.assertEqual(bad, [])

    # 4. accessibility -------------------------------------------------------
    def test_landmarks_labels_and_live_log(self):
        page = self.open(1200, 784)
        self.open_input_panel(page)
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
        """Count requestAnimationFrame callbacks over a fixed window.

        THE ONE `wait_for_timeout` IN THIS FILE THAT IS NOT A BUG, and it is left
        as a timer on purpose. This is not waiting for a layout to settle: it is
        measuring a RATE. The display runs an animation loop, and the test asks how
        many frames it asked for in 1200ms, comparing "> 10" (the loop is running)
        against "<= 3" (it has stopped). There is no predicate to wait on, because
        the negative case is the absence of the thing: a loop that never starts has
        no event to wait for and no box that has stopped moving. Any conversion
        would have to be "wait for the loop to stop", which inverts the test.

        So the 1200 is the measurement window and it is a real number rather than a
        guess: the two thresholds it feeds (>10 and <=3) are far enough apart that
        the ~72 frames a 60Hz loop produces in that window is not in doubt, and the
        two stop-cases are pinned at <=3 rather than ==0 precisely so that a slow
        machine that delivers a stray frame or two does not turn this red.
        """
        context = self.browser.new_context(viewport={"width": 1200, "height": 784}, **page_args.get("ctx", {}))
        self.addCleanup(context.close)
        context.add_init_script("""window.__raf = 0; const o = window.requestAnimationFrame;
          window.requestAnimationFrame = function (cb) { window.__raf++; return o.call(window, cb); };""" +
                                page_args.get("init", ""))
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        page.wait_for_timeout(settle)
        return page.evaluate("window.__raf")

    def test_animation_loop_runs_normally_but_stops_for_reduced_motion_and_hidden_tab(self):
        normal = self._raf_count({})
        self.assertGreater(normal, 10, "the counter should see the loop running")
        reduced = self._raf_count({"ctx": {"reduced_motion": "reduce"}})
        self.assertLessEqual(reduced, 3, f"reduced motion still animates: {reduced}")
        hidden = self._raf_count({"init": "Object.defineProperty(Document.prototype, 'hidden', {get: () => true});"})
        self.assertLessEqual(hidden, 3, f"hidden tab still animates: {hidden}")


if __name__ == "__main__":
    unittest.main()
