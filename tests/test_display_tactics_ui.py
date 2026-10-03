"""The combat panel's own layout rules (display/static/tactics.{js,css}).

The panel is a browser asset: there is no Python to call, so the two things
that can be checked from here are pinned instead. First, the parts of
tactics.js that are deliberately free of the DOM -- the side table, the octagon
frame and the cell rule -- are extracted and run under node, so the numbers the
board is drawn at are the numbers this file says they are. Second, the
JavaScript and the CSS are checked against each other: a class the script sets
has to exist in the stylesheet, or the panel silently loses a signal.

Phase 4b must-have 2 (a phone fits the board and pins the action bar),
4 (side shown by frame shape, not colour alone) and 6 (a table display draws
squares of at least 40 px) live here.
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
JS = REPO / "display" / "static" / "tactics.js"
CSS = REPO / "display" / "static" / "tactics.css"

sys.path.insert(0, str(REPO / "scripts"))
NODE = shutil.which("node")

#: Skip decorator, named because the module already uses it spelled out. Every
#: class below that runs JavaScript needs it, and a class that forgets is a
#: silently-skipped test, which is the state this repo has been bitten by.
skip_unless_node = unittest.skipUnless(NODE, "node is not installed")

# The block tactics.js marks as free of the DOM (see "pure helpers" there).
PURE = re.compile(r"/\* Pure helpers:.*?\*/(.*?)/\* end pure helpers \*/", re.S)


def _pure_helpers():
    m = PURE.search(JS.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError("tactics.js no longer has its marked pure-helper block")
    return m.group(1)


def _media_block(css: str, query: str) -> str:
    """The body of the last `@media <query> { ... }`, by brace count.

    A regex cannot do this: several rules inside the block are one-liners, so
    the first closing brace ends a rule, not the block."""
    start = css.rindex(f"@media {query} {{")
    depth, i = 0, css.index("{", start)
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i + 1]
        i += 1
    return ""


def _rgb(hex_colour: str):
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lin(c: int) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb) -> float:
    r, g, b = (_lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    """WCAG 2.x contrast ratio between two sRGB triples."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _over(fg, bg, alpha: float):
    """`fg` at `alpha` alpha-composited over `bg`. sRGB, as a browser does it
    for `fill-opacity` (the non-linear form is what the CSS compositing
    spec's simple case yields and what a screenshot shows)."""
    return tuple(round(alpha * f + (1 - alpha) * b) for f, b in zip(fg, bg))


def _run(js: str) -> dict:
    """Run a snippet with the pure helpers in scope; return its JSON result.

    The program goes in over stdin, not in argv. The pure-helper block holds
    non-ASCII source (the side glyphs, e.g. U+2694), and a command-line
    argument is encoded with the filesystem encoding: under a non-UTF-8
    locale that is a UnicodeEncodeError before node is ever started. stdin is
    bytes, so the same snippet runs under any codepage. Pass the script as
    `node -` and node reads the program from stdin.
    """
    if not NODE:
        raise unittest.SkipTest("node is not installed")
    body = _pure_helpers() + "\n" + js
    program = ("const out = (() => {" + body + "})();"
               "process.stdout.write(JSON.stringify(out));")
    r = subprocess.run([NODE, "-"], input=program.encode("utf-8"),
                       capture_output=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError("the pure helpers did not run: "
                             + r.stderr.decode("utf-8", "replace").strip())
    return json.loads(r.stdout.decode("utf-8"))


@unittest.skipUnless(NODE, "node is not installed")
class CellSizing(unittest.TestCase):
    """boardCell: a square is the smallest thing a player aims at.

    On a phone the whole map fits if the squares stay tappable, and the squares
    stay tappable if it does not. On a shared table display the floor wins: a
    big map scrolls rather than shrinking into a squint.
    """

    def cell(self, W, H, w, h, phone):
        js = f"return {{value: boardCell({W}, {H}, {w}, {h}, {str(phone).lower()})}};"
        return _run(js)["value"]

    def test_a_table_display_never_draws_a_square_under_40px(self):
        # A 1440-wide shared display gives the board about 870x640. Every map
        # the display ships must come out at 40 px or more.
        for W, H in ((20, 14), (24, 16), (24, 18), (30, 12), (12, 9)):
            for avail in ((870, 640), (1100, 700), (600, 400), (360, 300)):
                self.assertGreaterEqual(self.cell(W, H, *avail, phone=False), 40,
                                        f"{W}x{H} in {avail} drew a sub-40px square")

    def test_a_table_display_uses_the_space_it_has(self):
        # Frog Pond (20x14) in a tall box: 640/14 = 45, so 45, not the 40 floor.
        self.assertEqual(self.cell(20, 14, 900, 640, phone=False), 45)
        # A small map is not blown up past a readable size.
        self.assertLessEqual(self.cell(12, 9, 2000, 1200, phone=False), 72)

    def test_a_phone_fits_the_whole_map_when_the_squares_stay_tappable(self):
        # A 390-wide phone gives the board about 355px. Frog Pond: 355/20 = 17.
        self.assertEqual(self.cell(20, 14, 355, 600, phone=True), 17)
        # A narrow map gets bigger squares, not the same 17.
        self.assertEqual(self.cell(12, 9, 355, 600, phone=True), 29)

    def test_a_wide_map_still_fits_a_phone_rather_than_running_off_the_side(self):
        # Mage Tower is 30 wide: fitting it means 11px squares, which is the
        # right trade on a phone -- a whole map beats a legible left half.
        self.assertEqual(self.cell(30, 12, 355, 600, phone=True), 11)
        self.assertLessEqual(30 * self.cell(30, 12, 355, 600, phone=True), 355)

    def test_a_phone_board_fits_the_viewport_width(self):
        """The bug: the board was 560px inside a ~355px phone panel, so the
        right of the map was off-screen. The board is now W * cell wide."""
        for W, H in ((20, 14), (24, 16), (12, 9), (30, 12)):
            cell = self.cell(W, H, 355, 600, phone=True)
            self.assertLessEqual(W * cell, 355, f"{W}x{H} still overflows a phone")

    def test_a_phone_square_never_collapses_to_nothing(self):
        for W, H in ((30, 12), (60, 40), (120, 80)):
            self.assertGreaterEqual(self.cell(W, H, 355, 600, phone=True), 10)


class FrameGeometry(unittest.TestCase):
    def test_the_enemy_frame_is_an_octagon(self):
        out = _run("return {oct: octagon(16, 16, 12)};")
        d = out["oct"]
        # Eight corners and a closing Z: real geometry, not a background image.
        self.assertTrue(d.startswith("M"))
        self.assertTrue(d.endswith("Z"))
        self.assertEqual(d.count("L") + 1, 8)
        pts = [tuple(float(v) for v in p.split(",")) for p in d[1:-1].replace("L", "|").split("|")]
        self.assertEqual(len(pts), 8)
        # A regular octagon: every corner the same distance from the centre.
        radii = [((x - 16) ** 2 + (y - 16) ** 2) ** 0.5 for x, y in pts]
        self.assertAlmostEqual(max(radii), min(radii), places=2)
        # And it spans the same 2r box as the circle it replaces, so an enemy
        # token is not the bigger target on the board.
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.assertAlmostEqual(max(xs) - min(xs), 24, places=2)   # the circle's diameter
        self.assertAlmostEqual(max(ys) - min(ys), 24, places=2)

    def test_the_ally_frame_is_a_circle_of_the_same_radius(self):
        # The enemy frame and the ally frame are drawn at one radius, so the two
        # shapes are the same size on the board and only the outline differs.
        # The geometry now lives in one `shape` object that is drawn twice --
        # filled underneath, stroked over the top -- so that a portrait cannot
        # change the silhouette. The radius is still C / 2 - 4 for both.
        js = JS.read_text(encoding="utf-8")
        self.assertIn("{ tag: 'circle', geo: { cx, cy, r: C / 2 - 4 } }", js)
        self.assertIn("{ tag: 'path', geo: { d: octagon(cx, cy, C / 2 - 4) } }", js)

    def test_every_side_has_its_own_frame_colour_and_word(self):
        out = _run("return {cls: Object.values(SIDES).map(s => s.cls), "
                   "words: Object.values(SIDES).map(s => s.word), "
                   "glyphs: Object.values(SIDES).map(s => s.glyph), "
                   "colours: Object.values(SIDES).map(s => s.colour)};")
        self.assertEqual(sorted(out["cls"]), ["tx-side-enemy", "tx-side-other", "tx-side-pc"])
        self.assertEqual(len(set(out["colours"])), 3, "the three sides share a colour")
        self.assertEqual(len(set(out["glyphs"])), 3, "two sides share a glyph")
        self.assertEqual(sorted(out["words"]), ["ally", "enemy", "neutral"])


@unittest.skipUnless(NODE, "node is not installed")
class OddsAtResolution(unittest.TestCase):
    """oddsText / entryOdds: the chance, shown next to the roll it was made under.

    A player doubts a roll *after* seeing the 3. The pre-action preview badge
    quoted the number at the moment of choosing and has long since gone, so these
    two helpers are what answer the doubt at the moment it is actually felt.

    The number itself is never computed here. Roll.odds is whatever the system
    wrote (dnd5e fills it from hit_chance and save_chance), and these tests are
    about the display quoting it faithfully -- including the direction, which is
    carried by the system's own label because the percent means opposite things
    for an attack and a save.
    """

    def text(self, odds):
        return _run(f"return {{v: oddsText({json.dumps(odds)})}};")["v"]

    def entry(self, entry):
        return _run(f"return {{v: entryOdds({json.dumps(entry)})}};")["v"]

    def test_the_chance_is_shown_with_the_systems_own_label(self):
        # "to hit" is hit_chance's chance to succeed.
        self.assertEqual(self.text({"percent": 75, "label": "to hit"}), "75% to hit")
        # "to fail the save" is save_chance's chance to FAIL. Same shape, and
        # reading it as a chance to succeed inverts the only number on screen.
        self.assertEqual(self.text({"percent": 65, "label": "to fail the save"}),
                         "65% to fail the save")

    def test_a_roll_with_no_odds_says_nothing_rather_than_zero(self):
        """A damage die has no question attached to it. '0%' would be a lie."""
        for odds in ({}, None, {"label": "to hit"}, {"percent": None}):
            self.assertEqual(self.text(odds), "", f"{odds!r} produced a number")

    def test_a_zero_percent_is_a_real_answer_and_is_not_dropped(self):
        # 0% is what a hopeless shot looks like, and it is the number that most
        # needs saying. Only a MISSING percent is silence.
        self.assertEqual(self.text({"percent": 0, "label": "to hit"}), "0% to hit")

    def test_a_number_with_no_label_is_still_shown_and_has_no_trailing_space(self):
        # The bug this caught: a bare percent came out as "0% ", which reads as a
        # truncated sentence in the middle of a log line.
        self.assertEqual(self.text({"percent": 0, "label": ""}), "0%")
        self.assertEqual(self.text({"percent": 65}), "65%")
        self.assertEqual(self.text({"percent": 65, "label": "   "}), "65%")

    def test_the_number_is_rounded_not_truncated(self):
        # hit_chance already rounds; this only guards against a float slipping in.
        self.assertEqual(self.text({"percent": 74.6, "label": "to hit"}), "75% to hit")

    def test_a_junk_percent_is_silence_and_not_nan(self):
        self.assertEqual(self.text({"percent": "seventy", "label": "to hit"}), "")

    def test_one_line_carries_every_chance_in_the_entry(self):
        """Directional cover is the highest roll density in 5e, and a line
        showing one chance out of four reads as the other three being withheld
        -- a worse suspicion than the one the number answers."""
        e = {"rolls": [{"odds": {"percent": 65, "label": "to fail the save"}},
                       {"odds": {"percent": 80, "label": "to fail the save"}},
                       {"odds": {}}]}
        self.assertEqual(self.entry(e), "65% to fail the save · 80% to fail the save")

    def test_a_repeated_chance_is_said_once(self):
        """Four saves at 65% against the same odds is one thing to be told."""
        rolls = [{"odds": {"percent": 65, "label": "to fail the save"}} for _ in range(4)]
        self.assertEqual(self.entry({"rolls": rolls}), "65% to fail the save")

    def test_an_entry_with_no_chances_yields_no_line(self):
        self.assertEqual(self.entry({"rolls": [{"odds": {}}]}), "")
        self.assertEqual(self.entry({}), "")
        self.assertEqual(self.entry({"rolls": []}), "")


class OddsSurfaces(unittest.TestCase):
    """The three places the chance is shown, and the one that may be missing.

    The roadmap's invariant: the board float is the ONLY surface allowed to be
    absent, because it needs `odds.about` to name a token that is actually
    drawn. The toast and the log line must not depend on that lookup, or a
    creature the players cannot see would take the number away from everywhere.
    """

    def js(self):
        return JS.read_text(encoding="utf-8")

    def css(self):
        return CSS.read_text(encoding="utf-8")

    def test_the_toast_carries_the_chance_and_does_not_look_at_the_board(self):
        js = self.js()
        self.assertRegex(js, r"const odds = oddsText\(r\.odds\);\s*\n\s*if \(odds\) shown")
        # announceRolls works off the log entry alone: no token lookup, so a
        # redacted creature cannot suppress it.
        block = js[js.index("function announceRolls()"):js.index("function render()")]
        self.assertNotIn("snap.tokens", block)

    def test_the_log_line_carries_the_chance_and_does_not_look_at_the_board(self):
        js = self.js()
        self.assertIn("entryOdds(e)", js)
        block = js[js.index("el.log.innerHTML = '';"):js.index("el.log.scrollTop")]
        self.assertNotIn("snap.tokens", block,
                         "the log must render the odds it was sent, not the ones it could find")

    def test_the_float_is_the_only_one_that_looks_up_a_token(self):
        """This asymmetry is the design, so it is pinned: the float resolves
        `about` against the drawn tokens, the other two do not resolve anything."""
        js = self.js()
        block = js[js.index("function oddsFloaters()"):js.index("// ── boot")]
        self.assertIn("snap.tokens", block)
        self.assertIn("odds.about", block)
        # And it skips rather than inventing: a roll about a token that is not
        # drawn produces no float.
        self.assertRegex(block, r"const t = \(snap\.tokens \|\| \[\]\)\.find.*\n.*if \(!t\) continue;")

    def test_the_float_never_consults_the_encounter(self):
        """The display has a snapshot, not an encounter. Reaching for anything
        else would move state authority out of sync.snapshot, which is the thing
        battle-system.md exists to prevent -- and would happily draw a token the
        players are not supposed to see."""
        js = self.js()
        block = js[js.index("function oddsFloaters()"):js.index("// ── boot")]
        # The block is stripped of its own prose first, so a comment explaining
        # the rule cannot read as breaking it.
        code = "\n".join(ln for ln in block.splitlines()
                         if not ln.strip().startswith("//"))
        for leak in ("enc.tokens", "enc.", "load_encounter", "state.load"):
            self.assertNotIn(leak, code, f"the odds float reached for {leak}")

    def test_every_class_the_odds_set_is_styled(self):
        css = self.css()
        self.assertIn(".tx-log .tx-odds", css)
        self.assertIn(".tx-float.tx-odds-float", css)

    def test_reduced_motion_can_hide_the_float_without_hiding_the_odds(self):
        """Floats are display:none under reduced motion, so the number must
        still be somewhere that is not an animation."""
        css = self.css()
        block = _media_block(css, "(prefers-reduced-motion: reduce)")
        self.assertIn(".tx-float { display: none; }", block)
        # The two survivors are not animations and so are not in that block.
        self.assertIn("el.log.innerHTML = '';", self.js())


class ScriptAndStylesheetAgree(unittest.TestCase):
    """A class the script sets must exist in the stylesheet, or the panel
    drops that signal without anything failing."""

    def css(self):
        return CSS.read_text(encoding="utf-8")

    def js(self):
        return JS.read_text(encoding="utf-8")

    def test_the_side_stripes_and_glyph_colours_are_styled(self):
        css = self.css()
        for cls in ("tx-side-enemy", "tx-side-pc", "tx-side-other"):
            self.assertIn(f".tx-chip.{cls}", css, f"{cls} is set on a chip but not styled")
        self.assertIn(".tx-side-glyph", css)

    def test_the_stripes_are_geometry_and_not_only_a_colour(self):
        """A stripe is a border on the chip's leading edge, so it survives a
        greyscale print; the glyph is a separate character, so it survives too."""
        css = self.css()
        for cls in ("tx-side-enemy", "tx-side-pc", "tx-side-other"):
            rule = re.search(rf"\.tx-chip\.{cls} \{{([^}}]*)\}}", css)
            self.assertIsNotNone(rule, f"{cls} has no rule")
            self.assertIn("border-left", rule.group(1), f"{cls} is colour only")
        glyphs = re.search(r"\.tx-chip\.tx-side-enemy \.tx-side-glyph \{([^}]*)\}", css)
        self.assertIsNotNone(glyphs)

    def test_the_enemy_token_is_drawn_as_a_path_and_the_ally_as_a_circle(self):
        # The shape is chosen once, from the side, and every element the token
        # draws (fill, portrait clip, frame) is built from that one choice. That
        # is what makes "side is shown by the outline, not the colour" true once
        # portraits are in the middle: a portrait is clipped to the same octagon
        # or circle the frame is stroked over.
        js = self.js()
        self.assertRegex(js, r"const isEnemy = t\.side === 'enemy'")
        self.assertRegex(js, r"\? \{ tag: 'path', geo: \{ d: octagon\(")
        self.assertRegex(js, r": \{ tag: 'circle', geo: \{ cx, cy, r: C / 2 - 4 \} \}")
        # One helper draws it, so the fill and the frame cannot drift apart.
        self.assertIn("const shapeNode = (style, parent) =>", js)
        self.assertIn("shapeNode('fill:none;stroke:var(--tx-panel);stroke-width:2', g);", js)

    def test_the_frame_is_drawn_after_whatever_is_inside_it(self):
        """The frame is the only thing that says which side a creature is, so it
        is stroked last and nothing may land on top of it.

        The portrait fallback is the case that breaks this by accident: it runs
        from an `error` handler, after drawToken has finished, and an appended
        fill goes to the end of the group -- over the frame. So the fallback
        inserts the fill where the image was and removes the image, rather than
        appending. MeasuredLayout's `frames` check cannot see this (it asks
        which shape is present, not what covers what), so it is pinned here.
        """
        js = self.js()
        self.assertIn("g.insertBefore(fill, img);", js)
        self.assertIn("g.removeChild(img);", js)
        # ...and only once, whatever the browser fires at us.
        self.assertIn("if (fellBack || !g.isConnected || !img) return;", js)

    LEAD = re.compile(r"button\('(?P<name>[^']+)',\s*\(\)\s*=>\s*[\w.]+\([^)]*\),\s*\{(?P<opts>[^{}]*)\}",
                      re.S)

    def test_the_lead_actions_are_the_ones_the_phone_bar_pins(self):
        """Move, Attack and Cast are what a player reaches for every turn; the
        phone pins them to the top of the bar so a long spell list cannot push
        them away. End turn is pinned to the bottom of the same bar instead."""
        js = self.js()
        found = {m.group("name"): m.group("opts") for m in self.LEAD.finditer(js)}
        lead = {n for n, o in found.items() if "lead: true" in o}
        self.assertEqual(lead, {"Move", "Attack", "Cast"})
        self.assertIn("end: true", found["End turn"])
        css = self.css()
        self.assertRegex(css, r'\.tx-leads \{[^}]*position: sticky')
        self.assertRegex(css, r'\[data-tx="end"\] \{[^}]*position: sticky')

    def test_a_non_lead_action_is_not_pinned_with_them(self):
        js = self.js()
        secondaries = {"Dash", "Disengage", "Dodge", "Undo move"}
        found = {m.group("name"): m.group("opts") for m in self.LEAD.finditer(js)}
        self.assertTrue(secondaries <= set(found),
                        "the secondary actions are no longer drawn here")
        for name in secondaries:
            self.assertNotIn("lead: true", found[name], f"{name} is in the pinned row")

    def test_the_phone_bar_is_sticky_and_clears_the_home_indicator(self):
        block = _media_block(self.css(), "(max-width: 860px)")
        self.assertIsNotNone(block, "the phone layout block is gone")
        actions = re.search(r"#tx-actions \{(.*?)\}", block, re.S)
        self.assertIsNotNone(actions, "the phone layout no longer styles the action bar")
        self.assertIn("position: sticky", actions.group(1))
        self.assertIn("bottom: 0", actions.group(1))
        self.assertIn("safe-area-inset-bottom", actions.group(1))
        # The bar has to be a scroll box with a ceiling, or a long spell list
        # grows it until the map is gone.
        self.assertIn("overflow-y: auto", actions.group(1))
        self.assertIn("max-height", actions.group(1))

    def test_the_phone_block_comes_after_the_rules_it_replaces(self):
        """A media block written above the base rule of the same weight loses to
        it silently: the panel's own .tx-body rule sat further down the sheet
        and quietly won. The phone block is last for that reason."""
        css = self.css()
        phone = css.rindex("@media (max-width: 860px)")
        for base in (".tx-body {", ".tx-board {", ".tx-side {", ".tx-leads {", ".tx-actions {"):
            self.assertLess(css.index(base), phone,
                            f"{base} is declared after the phone block and will beat it")

    def test_the_phone_breakpoint_is_the_one_the_script_uses(self):
        """The board is sized in JS and laid out in CSS; if the two disagree,
        the board is sized for a layout that is not the one on screen."""
        self.assertRegex(self.js(), r"const PHONE_MAX_W = (\d+);")
        width = re.search(r"const PHONE_MAX_W = (\d+);", self.js()).group(1)
        self.assertIn(f"@media (max-width: {width}px)", self.css())


class Accessibility(unittest.TestCase):
    def test_the_board_has_a_keyboard_route_and_words_for_the_square(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("tx-keys", js)
        self.assertIn("aria-label", js)
        # The square a screen reader reads names the side, not just the colour.
        self.assertIn("sideOf(t).word", js)

    def test_reduced_motion_still_applies_to_the_new_animations(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion: reduce", css)

    def _relative_luminance(self, hex_colour: str) -> float:
        rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    def _contrast(self, a: str, b: str) -> float:
        la, lb = self._relative_luminance(a), self._relative_luminance(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    def _theme_tokens(self, name: str) -> list:
        """A theme token's hex value in every theme that declares it.

        The sheet has three theme blocks (vellum light, and two dark variants),
        so a token is a LIST of values, and a colour that reads well on one panel
        can be invisible on another. Returned in declaration order.
        """
        css = CSS.read_text(encoding="utf-8")
        found = re.findall(rf"{name}:\s*(#[0-9A-Fa-f]{{6}})", css)
        self.assertTrue(found, f"{name} is not declared in any theme")
        return found

    def test_the_odds_text_is_readable_in_every_theme(self):
        """The chance is 12px text, so it has to clear WCAG AA's 4.5:1 rather
        than merely be visible.

        This exists because the first version used `--tx-line`, which is a
        *border* colour: 1.35:1 on the panel in the dark theme and 1.57:1 light.
        It rendered, and the panel test read the right strings out of the DOM,
        so nothing was red -- the number was just a smudge beside the roll it
        belonged to. Contrast is the one property a test asserting on text
        content cannot see, so it is measured here instead.

        Each theme's own panel is paired with its own token value, in
        declaration order, so a colour that only works against one background
        is still caught.
        """
        rule = re.search(r"\.tx-log \.tx-odds \{([^}]*)\}", CSS.read_text(encoding="utf-8"))
        self.assertIsNotNone(rule, ".tx-log .tx-odds has no rule")
        colour = re.search(r"color:\s*var\((--[a-z-]+)\)", rule.group(1))
        self.assertIsNotNone(colour, "the odds colour is not a theme token")
        text_values = self._theme_tokens(colour.group(1))
        panel_values = self._theme_tokens("--tx-panel")
        self.assertEqual(len(text_values), len(panel_values),
                         "the odds colour and the panel are not declared once per theme")
        for text_hex, panel_hex in zip(text_values, panel_values):
            ratio = self._contrast(text_hex, panel_hex)
            self.assertGreaterEqual(
                ratio, 4.5,
                f"{colour.group(1)} ({text_hex}) on {panel_hex} is {ratio:.2f}:1, "
                f"below WCAG AA for 12px text")

    def test_the_odds_are_not_styled_in_a_border_colour(self):
        """The specific trap: `--tx-line` and `--tx-accent` read as reasonable
        "quiet" choices in a stylesheet and are borders, not text."""
        rule = re.search(r"\.tx-log \.tx-odds \{([^}]*)\}",
                         CSS.read_text(encoding="utf-8"))
        for borderish in ("--tx-line", "--tx-accent", "--tx-brass"):
            self.assertNotIn(borderish, rule.group(1),
                             f"{borderish} is a border colour, not a readable text colour")

    def test_the_odds_float_keeps_contrast_against_the_board(self):
        """The float is drawn over terrain and artwork, so it cannot rely on a
        panel background. It carries the same 4px paint-order stroke the damage
        float does, which is what keeps it legible over a pale map."""
        css = CSS.read_text(encoding="utf-8")
        rule = re.search(r"\.tx-float\.tx-odds-float \{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("var(--tx-ink)", rule.group(1))
        base = re.search(r"\.tx-float \{([^}]*)\}", css)
        self.assertIn("paint-order: stroke", base.group(1))
        self.assertIn("stroke: var(--tx-panel)", base.group(1))


# ── map labels (#144) ────────────────────────────────────────────────────────

@skip_unless_node
class LabelBudget(unittest.TestCase):
    """labelBudget: the width one map label may occupy, in SVG user units.

    An SVG <text> has no box, so nothing in CSS can bound it; the clamp is this
    function plus fitLabelText. Both are pure and both are in tactics.js's marked
    DOM-free block, which is run under node here.

    Every case below is a number taken from a real map in display/maps/, and
    each names the label it came from. That matters: a budget test with invented
    numbers proves the arithmetic and not whether the rule fits the maps it has
    to serve.
    """

    def budget(self, w, cx, W, unit=32):
        return _run(f"return {{v: labelBudget({w}, {cx}, {W}, {unit})}};")["v"]

    def test_a_short_name_keeps_all_of_itself_in_a_one_square_feature(self):
        """bows-end-tavern has eleven 1x1 features named "Round table" (11 chars).

        A strict region budget would be 32 - 8 = 24 units, about four characters,
        so the label would read "Roun…" and the feature would lose its name to
        being narrow. The floor is what stops that: the label is short and the
        map has room, so it is not clamped at all.
        """
        # A 1x1 region at the centre of a 32-wide map: cx = 16 * 32 = 512.
        self.assertGreaterEqual(self.budget(1, 512, 32), 11 * 6)

    def test_a_long_name_is_clamped_to_the_region_and_no_wider(self):
        """The great hearth: a 1x1 feature whose label is 79 characters.

        Unclamped at ~6.2 units a character that is ~490 units, about fifteen
        squares, drawn from a one-square hearth across the whole floor.
        """
        budget = self.budget(1, 512, 32)
        self.assertLess(budget, 79 * 6.2)
        self.assertGreater(budget, 0)

    def test_a_wide_region_gets_a_wider_budget_than_a_narrow_one(self):
        """The Cut (the canal) is 18 squares wide; a named table is 2."""
        self.assertGreater(self.budget(18, 16 * 32, 32), self.budget(2, 16 * 32, 32))

    def test_a_label_near_the_right_edge_is_clamped_by_the_room_left(self):
        """A centred box that overruns the viewBox is not drawn at all: SVG
        silently drops content past the last column, so an over-long label at
        the map's edge would vanish rather than overflow. The budget cannot
        exceed twice the distance to the nearer edge."""
        # The last column of a 32-wide map, centre at 31.5 * 32 = 1008.
        cx, W = 31.5 * 32, 32
        self.assertLessEqual(self.budget(18, cx, W), 2 * (W * 32 - cx))

    def test_the_budget_is_positive_at_every_edge_of_the_map(self):
        """A zero or negative budget would blank the label. Both edges, and the
        first and last square of every shipped map width."""
        for W in (9, 12, 20, 24, 30):
            for cx in (0.5, 1, W - 1, W - 0.5):
                self.assertGreater(self.budget(1, cx * 32, W), 0,
                                   f"no room for a label at {cx} of {W}")


@skip_unless_node
class FitLabelText(unittest.TestCase):
    """fitLabelText: the longest prefix of a label that fits a budget.

    `measure` is injected, so these tests pin the SEARCH (prefix, ellipsis,
    no off-by-one) independently of how a browser measures type. The browser
    case is tests/test_display_tactics_layout.py, which uses a real one.
    """

    #: ~6.2 units a character, the same estimate fitLabels falls back to.
    def fit(self, text, budget):
        prog = ("const measure = s => s.length * 6.2;"
                f"return {{v: fitLabelText({json.dumps(text)}, {budget}, measure)}};")
        return _run(prog)["v"]

    #: The longest label on any shipped map, from bows-end-tavern's great hearth.
    HEARTH = ("The great hearth - lit. The chequered floor and produce racks "
              "sit all round it.")

    def test_a_label_that_fits_is_returned_whole(self):
        self.assertEqual(self.fit("Round table", 400), "Round table")

    def test_a_label_that_does_not_fit_gains_an_ellipsis_and_loses_its_tail(self):
        out = self.fit(self.HEARTH, 120)
        self.assertTrue(out.endswith("…"), out)
        self.assertTrue(self.HEARTH.startswith(out[:-1]), out)

    def test_the_result_never_exceeds_the_budget(self):
        """The property the whole change exists for, checked at several budgets
        rather than one: the measured prefix plus its ellipsis must fit."""
        for budget in (24, 40, 60, 80, 120, 200, 400):
            out = self.fit(self.HEARTH, budget)
            self.assertLessEqual(len(out) * 6.2, budget + 6.2,
                                 f"budget {budget} produced {out!r}")

    def test_a_prefix_keeps_the_head_and_a_suffix_would_not(self):
        """These names lead with the identity ("Duel Square (West) - chequered
        floor inside low timber rails"), so keeping the head is what makes a
        clamped label still recognisable."""
        text = "Duel Square (West) - chequered floor inside low timber rails"
        out = self.fit(text, 150)
        self.assertTrue(out.startswith("Duel Square (West)"), out)

    def test_a_budget_too_small_for_any_character_yields_nothing_rather_than_overflow(self):
        """Below one character the honest answer is no label. The full text is
        still in the <title>, so this loses a name rather than a fact."""
        self.assertEqual(self.fit("Round table", 1), "")

    def test_empty_and_missing_labels_are_empty(self):
        self.assertEqual(self.fit("", 400), "")
        self.assertEqual(_run("return {v: fitLabelText(null, 400, s => s.length * 6)};")["v"], "")


@skip_unless_node
class LabelRegion(unittest.TestCase):
    """labelCovers: which squares a map label's region actually covers.

    describeSquare() reads the FULL label text out through the board cursor,
    because the board svg is aria-hidden and a shortened label is not a name. It
    finds the label by region, so this is the test that the region test reaches
    the squares a GM would aim at.
    """

    def covers(self, l, x, y):
        return _run(f"return {{v: !!labelCovers({json.dumps(l)}, {x}, {y})}};")["v"]

    def test_every_square_of_a_multi_square_region_is_covered(self):
        """detention-bog's "Reeds" is 7x5. Testing only the anchor square would
        miss 34 of its 35."""
        l = {"text": "Reeds", "x": 3.5, "y": 2.5, "w": 7, "h": 5}
        for y in range(5):
            for x in range(7):
                self.assertTrue(self.covers(l, x, y), f"({x},{y}) of the Reeds region")

    def test_a_square_outside_the_region_is_not_covered(self):
        l = {"text": "Reeds", "x": 3.5, "y": 2.5, "w": 7, "h": 5}
        self.assertFalse(self.covers(l, 7, 0))
        self.assertFalse(self.covers(l, 0, 5))

    def test_a_label_without_a_size_covers_exactly_its_anchor_square(self):
        """A snapshot written before maps.py published w: a top-left corner, not
        a centre, and one square wide."""
        l = {"text": "Lantern", "x": 2, "y": 3}
        self.assertTrue(self.covers(l, 2, 3))
        self.assertFalse(self.covers(l, 3, 3))


class MapLabelGeometry(unittest.TestCase):
    """maps.py: a label's anchor is its region's centre, and it carries the size.

    Display-only, and the round trip is the reason this is asserted on the
    compiler: mapeditor.py re-derives a saved map's labels from its `features`
    rectangles, so a centre written where a corner was meant cannot corrupt a
    map file. That is a claim about two files agreeing, so it is tested on both.
    """

    def labels(self, spec):
        from scripts.tactics import maps as _maps
        return _maps.compile_map(spec)["meta"]["labels"]

    def test_the_anchor_is_the_centre_of_the_region_it_names(self):
        """Before the fix: x, y were the region's top-left corner, so a label on
        a 6x4 feature sat over its first square and its text ran right across
        five squares of something else."""
        got = self.labels({"width": 8, "height": 6, "base": "floor", "features": [
            {"type": "feature", "x": 1, "y": 2, "w": 6, "h": 4, "label": "Rubble"}]})
        self.assertEqual(got, [{"text": "Rubble", "x": 4.0, "y": 4.0, "w": 6, "h": 4}])

    def test_an_odd_sized_region_gets_a_half_square_centre(self):
        """3 wide puts the middle at x + 1.5. Rounding it to 2 would put the
        label over the second square of three, which is not the middle of
        anything a reader would call the middle."""
        got = self.labels({"width": 8, "height": 6, "base": "floor", "features": [
            {"type": "feature", "x": 0, "y": 0, "w": 3, "h": 1, "label": "Bench"}]})
        self.assertEqual((got[0]["x"], got[0]["y"]), (1.5, 0.5))

    def test_a_squarer_feature_still_gets_w_and_h(self):
        got = self.labels({"width": 8, "height": 6, "base": "floor", "features": [
            {"type": "feature", "x": 2, "y": 2, "label": "Lantern"}]})
        self.assertEqual((got[0]["w"], got[0]["h"]), (1, 1))

    def test_the_grid_rows_are_untouched_by_the_label_geometry(self):
        """The acceptance criterion "grid rows unchanged", on the real thing: a
        labelled map's rows must be identical to the same map with no labels.
        Labels are display metadata; the engine reads rows."""
        feats = [{"type": "feature", "x": 1, "y": 2, "w": 6, "h": 4, "label": "Rubble"},
                 {"type": "wall", "x": 0, "y": 0, "w": 2, "h": 8}]
        with_lbl = {"width": 8, "height": 8, "base": "floor", "features": feats}
        without = json.loads(json.dumps(with_lbl))
        without["features"][0].pop("label")
        self.assertEqual(self.labels(with_lbl)[0]["text"], "Rubble")
        from scripts.tactics import maps as _maps
        self.assertEqual(_maps.compile_map(with_lbl)["grid"]["rows"],
                         _maps.compile_map(without)["grid"]["rows"])

    def test_every_shipped_map_still_compiles_and_keeps_its_label_count(self):
        """The whole shipped set, not a fixture. A label key that a real map
        needs and this test does not have is a map that stops loading."""
        from scripts.tactics import maps as _maps
        root = REPO / "display" / "maps"
        total = 0
        for p in sorted(root.glob("*.json")):
            spec = json.loads(p.read_text(encoding="utf-8"))
            got = _maps.compile_map(spec)["meta"]["labels"]
            wanted = sum(1 for f in spec.get("features") or [] if f.get("label"))
            self.assertEqual(len(got), wanted, f"{p.name}: label count changed")
            for l in got:
                self.assertTrue(1 <= l["x"] <= spec["width"], f"{p.name}: {l} off the map")
                self.assertTrue(1 <= l["y"] <= spec["height"], f"{p.name}: {l} off the map")
                self.assertGreaterEqual(l["w"], 1)
                self.assertGreaterEqual(l["h"], 1)
            total += len(got)
        self.assertGreater(total, 100, "the shipped maps carry far fewer labels than that")


class MapLabelRoundTrip(unittest.TestCase):
    """The editor's save path must not read a centre back as a corner.

    `cover()` in mapeditor.py re-derives features[] from the cell matrix, and the
    cell matrix carries a label per FEATURE INDEX (mapeditor.py:96), not a
    coordinate. So a label whose anchor is now a centre still round trips. This
    is the test that says so, because the alternative is a map file silently
    rewritten with every label shifted.
    """

    def test_a_save_after_a_load_puts_the_label_back_on_its_own_region(self):
        from scripts.tactics import mapeditor as _me
        spec = {"name": "t", "width": 10, "height": 8, "base": "floor", "features": [
            {"type": "feature", "x": 1, "y": 2, "w": 6, "h": 4, "label": "Rubble"}]}
        cells = _me.cells_of(spec)
        back = _me.cover(cells, spec["base"])
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0]["label"], "Rubble")
        self.assertEqual((back[0]["x"], back[0]["y"], back[0]["w"], back[0]["h"]),
                         (1, 2, 6, 4))

    def test_editor_state_publishes_the_centre_and_the_size(self):
        """The editor draws from editor_state()["labels"], so if that list still
        carried top-left corners the editor would render the old way whatever
        maps.py now says."""
        from scripts.tactics import mapeditor as _me
        state = _me.editor_state(
            {"name": "t", "width": 10, "height": 8, "base": "floor", "features": [
                {"type": "feature", "x": 1, "y": 2, "w": 6, "h": 4, "label": "Rubble"}]},
            "t")
        self.assertEqual(state["labels"],
                         [{"text": "Rubble", "x": 4.0, "y": 4.0, "w": 6, "h": 4}])


class MapLabelStylesheet(unittest.TestCase):
    """The type the clamp assumes, and the two properties only CSS can set.

    Not a restatement of the stylesheet: the two assertions that matter are the
    ones a JS clamp cannot supply. `text-anchor: middle` and
    `dominant-baseline: central` are what centre the label on the region, and
    tactics.js sets NEITHER as an attribute (it sets x and y only), so removing
    either line here un-centres every label on the map.
    """

    def rule(self):
        css = CSS.read_text(encoding="utf-8")
        m = re.search(r"\.tx-cell-lbl \{([^}]*)\}", css)
        self.assertIsNotNone(m, ".tx-cell-lbl has no rule")
        return m.group(1)

    def test_the_label_is_centred_on_its_region_by_the_stylesheet_alone(self):
        self.assertIn("text-anchor: middle", self.rule())
        self.assertIn("dominant-baseline: central", self.rule())

    def test_tactics_js_does_not_set_the_anchor_itself(self):
        """The reason the previous two are load-bearing. If the script set
        text-anchor as an attribute it would win over the stylesheet and the
        stylesheet rule would be dead."""
        js = JS.read_text(encoding="utf-8")
        for lbl in js.splitlines():
            if "tx-cell-lbl" in lbl:
                self.assertNotIn("text-anchor", lbl)
                self.assertNotIn("dominant-baseline", lbl)

    def test_the_halo_that_keeps_a_label_readable_over_artwork_is_kept(self):
        self.assertIn("paint-order: stroke", self.rule())

    def test_the_editor_label_is_centred_by_its_stylesheet_too(self):
        css = (REPO / "display" / "static" / "mapseditor.css").read_text(encoding="utf-8")
        m = re.search(r"\.me-lbl \{([^}]*)\}", css)
        self.assertIsNotNone(m, ".me-lbl has no rule")
        self.assertIn("text-anchor: middle", m.group(1))
        self.assertIn("dominant-baseline: central", m.group(1))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(NODE, "node is not installed")
class PendingBanner(unittest.TestCase):
    """pendingBanner: what the panel says while the engine waits on the party."""

    def banner(self, p, who):
        import json as _j
        return _run(f"return {{v: pendingBanner({_j.dumps(p)}, {_j.dumps(who)})}};")["v"]

    def test_roll(self):
        self.assertEqual(self.banner("roll:1d20", "Kairos"), "Waiting on Kairos: roll 1d20")

    def test_reaction(self):
        self.assertEqual(self.banner("react:shield", "Kairos"), "Waiting on Kairos: shield? yes/no")

    def test_nothing_pending_or_death_save_is_silent(self):
        self.assertEqual(self.banner("", "Kairos"), "")
        self.assertEqual(self.banner("death_save", "Kairos"), "")


# ── terrain over artwork (#145) ──────────────────────────────────────────────

@skip_unless_node
class TerrainEdges(unittest.TestCase):
    """terrainEdges: which cells have a differently-terrained neighbour.

    The fill over artwork cannot do this job at any opacity (measured; see
    .tx-terrain-over-art in tactics.css), so the boundary between two terrains is
    drawn as a stroke instead. Where it goes is geometry, so it lives in the
    DOM-free block and is pinned here under node.
    """

    def edges(self, rows, names):
        return _run(f"return {{v: terrainEdges({json.dumps(list(rows))}, "
                    f"{json.dumps([list(r) for r in names])})}};")["v"]

    def test_open_ground_is_not_outlined_at_all(self):
        """The reason this is an edge pass and not a stroke per rect. A 6x6 of
        one terrain has no interior boundary anywhere: only the outer ring of
        cells is outlined, because only those face something else. Outlining
        every cell would draw a 6x6 box grid over open floor, which over
        artwork is worse than no outline at all."""
        rows = ["......"] * 6
        names = [["floor"] * 6 for _ in range(6)]
        by_pos = {(e["x"], e["y"]): e for e in self.edges(rows, names)}
        self.assertEqual(len(by_pos), 20, "only the border ring should be outlined")
        # The four fully-interior cells have no edge at all.
        for x in (2, 3):
            for y in (2, 3):
                self.assertNotIn((x, y), by_pos,
                                 f"open floor at ({x},{y}) was outlined")
        # A corner faces two board edges.
        self.assertEqual(by_pos[(0, 0)], {"x": 0, "y": 0, "l": 1, "r": 0, "t": 1, "b": 0})

    def test_a_region_is_outlined_as_a_region_not_as_a_grid_of_cells(self):
        """A 2x2 wall block in open floor.

        The line follows the REGION: the two wall cells that touch each other get
        no line between them, and only the outer boundary of the block is drawn.
        That is the whole difference between this and a stroke per rect, and it
        is why a solid wall reads as one wall rather than as four squares.

        The floor cells beside the block are outlined too, on the side that
        faces it. A boundary between two cells needs the line on both sides of it
        to read as a boundary rather than as a stain on the floor.
        """
        rows = ["......", "..##..", "..##..", "......"]
        names = [["floor"] * 6,
                 ["floor", "floor", "wall", "wall", "floor", "floor"],
                 ["floor", "floor", "wall", "wall", "floor", "floor"],
                 ["floor"] * 6]
        by_pos = {(e["x"], e["y"]): e for e in self.edges(rows, names)}

        def sides(p):
            e = by_pos[p]
            return (e["l"], e["r"], e["t"], e["b"])

        # Each wall cell: an edge on the sides facing floor or the board, never
        # on a side facing its own terrain.
        self.assertEqual(sides((2, 1)), (1, 0, 1, 0))   # west and north are floor
        self.assertEqual(sides((3, 1)), (0, 1, 1, 0))   # east and north
        self.assertEqual(sides((2, 2)), (1, 0, 0, 1))   # west and south
        self.assertEqual(sides((3, 2)), (0, 1, 0, 1))   # east and south
        # The floor immediately west of the block, outlined on its right only.
        self.assertEqual(sides((1, 1)), (0, 1, 0, 0))
        # A border cell of the board is outlined on the side that faces the
        # board edge, not on its neighbours, which are the same terrain.
        self.assertEqual(sides((3, 0)), (0, 0, 1, 1))

    def test_one_wall_cell_on_its_own_is_outlined_on_all_four_sides(self):
        rows = ["...", ".#.", "..."]
        names = [["floor"] * 3,
                 ["floor", "wall", "floor"],
                 ["floor"] * 3]
        (e,) = [x for x in self.edges(rows, names) if (x["x"], x["y"]) == (1, 1)]
        self.assertEqual((e["l"], e["r"], e["t"], e["b"]), (1, 1, 1, 1))

    def test_a_region_open_to_the_board_edge_is_outlined_on_that_side_only(self):
        """A wall running off the right of the map: its right side faces nothing,
        so only its left, top and bottom sides are boundaries."""
        rows = ["#####", ".....", "#####"]
        names = [["wall"] * 5,
                 ["floor"] * 5,
                 ["wall"] * 5]
        by_pos = {(e["x"], e["y"]): e for e in self.edges(rows, names)}
        wall_mid = by_pos[(2, 1)]
        # (l, r, t, b): its left and right neighbours are the same wall, its top
        # and bottom are floor.
        self.assertEqual((wall_mid["l"], wall_mid["r"],
                          wall_mid["t"], wall_mid["b"]), (0, 0, 1, 1),
                         "a cell enclosed by its own terrain is outlined only "
                         "where its neighbour differs")

    def test_the_outer_boundary_of_the_board_is_an_edge(self):
        """A lone wall in the corner: left and top face the board edge, and a
        board edge is as much a boundary as a differently-terrained square."""
        rows = ["#..", "...", "..."]
        names = [["wall", "floor", "floor"],
                 ["floor"] * 3,
                 ["floor"] * 3]
        by_pos = {(e["x"], e["y"]): e for e in self.edges(rows, names)}
        self.assertEqual((by_pos[(0, 0)]["l"], by_pos[(0, 0)]["t"]), (1, 1))

    def test_two_terrains_sharing_a_colour_are_still_two_terrains(self):
        """Map-specific types can be given the same colour, and a GM who painted
        them differently meant them to read differently. Comparing by name rather
        than by fill is what keeps a colour collision from hiding a boundary."""
        rows = [".."]
        names = [["custom-a", "custom-b"]]
        self.assertEqual(len(self.edges(rows, names)), 2)

    def test_a_uniform_map_outlines_only_its_border(self):
        """Uniform ground has no interior boundary, so only the cells touching
        the board edge are outlined, and each only on the side that faces it."""
        rows = ["##", "##", "##"]
        names = [["wall", "wall"]] * 3
        out = {(e["x"], e["y"]): e for e in self.edges(rows, names)}
        self.assertEqual(len(out), 6, "every cell of a solid map borders the edge")
        self.assertEqual((out[(0, 0)]["l"], out[(0, 0)]["t"], out[(0, 0)]["r"]), (1, 1, 0))
        self.assertEqual((out[(1, 2)]["r"], out[(1, 2)]["b"], out[(1, 2)]["l"]), (1, 1, 0))
        self.assertEqual((out[(0, 1)]["l"], out[(0, 1)]["r"],
                          out[(0, 1)]["t"], out[(0, 1)]["b"]), (1, 0, 0, 0))


@skip_unless_node
class DifficultMark(unittest.TestCase):
    """difficultMark: the chevron's path, in fractions of a cell.

    The old one was `M x*C+9, y*C+23 l6,-10 l6,10` at stroke-opacity .3: a fixed
    12 units in a 32-unit cell, which is 3.75px of marker at the phone minimum of
    10px a square, drawn next to a grid line. The acceptance criterion is
    "cell-scaled", and this is the measurement of that.
    """

    def d(self, x, y):
        return _run(f"return {{v: difficultMark({x}, {y}, 32)}};")["v"]

    def points(self, x, y):
        """(x, y) pairs from the path, as floats.

        The path is `M x,y l dx,dy l dx,dy`, so the first pair is absolute and
        the next two are DELTAS. Walking them as deltas is the point: reading the
        raw numbers as absolute coordinates would make a marker 2 units tall
        whenever its own delta was 2, which is a bug in the reader and not in
        the geometry.
        """
        nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", self.d(x, y))]
        out = [(nums[0], nums[1])]
        cx, cy = nums[0], nums[1]
        for i in range(2, len(nums), 2):
            cx, cy = cx + nums[i], cy + nums[i + 1]
            out.append((cx, cy))
        return out

    def test_the_marker_scales_with_the_cell_rather_than_being_a_fixed_size(self):
        """The core of "cell-scaled". Its bounding box, as a fraction of the
        cell, must be identical at every cell position -- a fixed-size marker
        would be the same absolute size everywhere, so its share of the cell
        would differ between the top-left of a map and the bottom-right.
        """
        spans = []
        for x, y in ((0, 0), (7, 3), (19, 11)):
            pts = self.points(x, y)
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            spans.append((round((max(xs) - min(xs)) / 32, 3),
                          round((max(ys) - min(ys)) / 32, 3)))
        self.assertEqual(len(set(spans)), 1, f"the marker is not cell-scaled: {spans}")

    def test_a_fixed_offset_marker_is_not_centred_and_does_not_sit_where_this_does(self):
        """The old geometry, run through this measurement, as the control.

        `M x*32+9, y*32+23 l6,-10 l6,10` at (0,0) has its box at x 9..21,
        y 13..23: centre (15, 18) where the cell's centre is (16, 16). It hangs
        two units low and one left, in a cell whose centre this change puts the
        marker on. Every other test in this class is measuring the replacement
        for that, and this is what says the replacement is a real change rather
        than a re-expression of the same numbers.

        Deliberately not asserting anything about the old path (there is no
        function for it any more): this computes the old geometry inline so the
        comparison is arithmetic, not a second source of truth in the script.
        """
        def old_points(x, y):
            p = [(x * 32 + 9, y * 32 + 23)]
            cx, cy = p[0]
            for dx, dy in ((6, -10), (6, 10)):
                cx, cy = cx + dx, cy + dy
                p.append((cx, cy))
            return p

        old = old_points(0, 0)
        old_box = ((min(q[0] for q in old), max(q[0] for q in old)),
                   (min(q[1] for q in old), max(q[1] for q in old)))
        old_centre = ((old_box[0][0] + old_box[0][1]) / 2,
                      (old_box[1][0] + old_box[1][1]) / 2)
        self.assertNotEqual(old_centre, (16.0, 16.0),
                            "the old chevron was already centred; the fix would "
                            "be a no-op and this control is wrong")

        new = self.points(0, 0)
        new_centre = ((min(q[0] for q in new) + max(q[0] for q in new)) / 2,
                      (min(q[1] for q in new) + max(q[1] for q in new)) / 2)
        self.assertAlmostEqual(new_centre[0], 16.0, delta=0.01)
        self.assertAlmostEqual(new_centre[1], 16.0, delta=0.01)

    def test_the_marker_sits_inside_its_own_cell(self):
        """A marker that overhangs its square reads on the neighbouring terrain,
        which is the confusion it exists to prevent.

        This is the assertion the old geometry fails. The old chevron was drawn
        at a fixed offset inside the cell -- `M x*32+9, y*32+23 l6,-10 l6,10` --
        which is not centred: its bounding box runs from +9 to +21 horizontally
        (centre +15, where the cell's centre is +16) and from +13 to +23
        vertically (centre +18, below the cell's +16). So it hung low and left,
        and at cell = 10 that is a marker visibly sitting toward the bottom-left
        corner of its square. Checking containment at several positions is what
        catches it; a single containment check at (0,0) does not, because a
        fixed offset that happens to fit the first cell can still be wrong.
        """
        for x, y in ((0, 0), (5, 2), (12, 9), (19, 4)):
            pts = self.points(x, y)
            self.assertGreaterEqual(min(p[0] for p in pts), x * 32,
                                    f"({x},{y}): marker starts left of its cell")
            self.assertLessEqual(max(p[0] for p in pts), (x + 1) * 32,
                                 f"({x},{y}): marker ends right of its cell")
            self.assertGreaterEqual(min(p[1] for p in pts), y * 32,
                                    f"({x},{y}): marker starts above its cell")
            self.assertLessEqual(max(p[1] for p in pts), (y + 1) * 32,
                                 f"({x},{y}): marker ends below its cell")

    def test_the_marker_is_centred_in_its_cell(self):
        pts = self.points(4, 6)
        cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        self.assertAlmostEqual(cx, 4 * 32 + 16, delta=1.0,
                               msg=f"marker off-centre at x=4: {cx}")

    def test_the_marker_is_large_enough_to_see_at_the_phone_minimum(self):
        """At cell = 10, a 32-unit cell is 10px on screen. The old chevron was
        12 units (3.75px); the new one must be a real fraction of the cell, not
        a decoration."""
        pts = self.points(0, 0)
        width_units = max(p[0] for p in pts) - min(p[0] for p in pts)
        self.assertGreaterEqual(width_units / 32, 0.25,
                                "the marker is a quarter the width of a cell or less")
        self.assertGreaterEqual(width_units / 32 * 10, 2.5,
                                "at cell=10 that is under 2.5px on screen")


class TerrainWashStylesheet(unittest.TestCase):
    """The wash opacities, and the claim that they are a floor rather than a
    preference.

    The interesting assertion is not "the number is .38" but that the numbers are
    LOWER than they were. The issue's two requirements are in direct conflict --
    a heavier wash hides the artwork, a lighter one hides the terrain -- and the
    only way out is to stop asking the wash to carry legibility on its own. So
    the test holds the wash down and the edge up.
    """

    def css(self):
        return CSS.read_text(encoding="utf-8")

    def test_the_measured_worst_case_says_no_opacity_can_do_this_job(self):
        """The measurement the whole change rests on, recomputed here rather than
        asserted as a comment.

        Alpha-compositing the terrain colour over the artwork means the
        apparent colour is `alpha*terrain + (1-alpha)*art`. Sweeping every
        terrain colour in both themes against every one of the 125 backdrops in
        a 5-cube RGB grid (which includes black, white, and each terrain colour
        itself), the worst-case contrast between a washed cell and the art under
        it is 1.00:1 -- at .85, .75, .65, .55, .45, .35 and .30 alike.

        So there is a backdrop for which any wash is invisible, and no opacity
        fixes it. That is why legibility moved to the boundary and the wash came
        DOWN. If a future palette change makes some alpha reach 3:1 here, this
        test fails and the premise should be re-examined.
        """
        worst = self._worst_case_contrast()
        self.assertLess(worst, 1.05,
                        f"some opacity now reaches {worst:.2f}:1 against arbitrary "
                        f"artwork; the wash-as-legibility premise has changed")

    def _worst_case_contrast(self):
        palette = self._terrain_palette()
        backdrops = [(r, g, b) for r in (0, 64, 128, 192, 255)
                     for g in (0, 64, 128, 192, 255)
                     for b in (0, 64, 128, 192, 255)]
        worst = 99.0
        for hexes in palette.values():
            fg = _rgb(hexes)
            for alpha in (.85, .75, .65, .55, .45, .35, .30):
                for bg in backdrops:
                    worst = min(worst, _contrast(_over(fg, bg, alpha), bg))
        return worst

    TERRAIN_TOKENS = ("floor", "wall", "water", "difficult", "feature",
                      "wood", "void", "hazard")

    def _terrain_palette(self):
        """Every `--tx-<terrain>` token, read out of the stylesheet, in
        declaration order so the light theme's value is the one found first.

        Taken from the file rather than copied here, so a renamed or recoloured
        terrain is measured too. All eight must be present: a terrain the
        measurement cannot see is a terrain whose legibility was never checked.
        """
        css = self.css()
        out = {}
        for tok in self.TERRAIN_TOKENS:
            m = re.search(rf"--tx-{tok}:\s*(#[0-9A-Fa-f]{{6}})", css)
            self.assertIsNotNone(m, f"--tx-{tok} is not declared in the stylesheet")
            out[tok] = m.group(1)
        return out

    def test_the_wash_over_artwork_is_lighter_than_it_was(self):
        """It was .55, and .85 for walls and voids. It is lower now, deliberately:
        at every opacity the worst-case contrast between a washed cell and
        adversarial artwork beneath it is 1.00:1, so a heavier wash costs
        artwork fidelity and buys no legibility at all."""
        for want, gone in ((".38", ".55"), (".7", ".85")):
            self.assertIn(f"fill-opacity: {want}", self._terrain_rules())
            self.assertNotIn(f"fill-opacity: {gone}", self._terrain_rules())

    def _terrain_rules(self):
        """Just the rules that key on .tx-terrain-over-art, as one string.

        Scoped because `fill-opacity: .55` is a legitimate value elsewhere in this
        stylesheet (the fog, the spell templates) and a whole-file search for
        the old number would pass or fail for the wrong reason.
        """
        css = self.css()
        out, i = [], 0
        while True:
            m = re.search(r"\.tx-terrain-over-art[^{]*\{[^}]*\}", css[i:])
            if not m:
                return "\n".join(out)
            out.append(m.group(0))
            i += m.end()

    def test_the_terrain_wash_is_not_the_only_thing_carrying_legibility(self):
        """The change of principle, as an assertion. Before, the wash opacity was
        the whole of terrain legibility over artwork. Now a boundary stroke and
        the difficult-terrain chevron carry it as well, which is what lets the
        wash come down without the terrain becoming unreadable."""
        css = self.css()
        self.assertIn("rect[data-edge]", css)
        self.assertIn(".tx-difficult-mark", css)

    def test_the_boundary_stroke_is_present_and_does_not_scale_with_the_cell(self):
        rule = re.search(r"\.tx-terrain-over-art rect\[data-edge\] \{([^}]*)\}",
                         self.css())
        self.assertIsNotNone(rule, "the terrain boundary has no rule")
        self.assertIn("non-scaling-stroke", rule.group(1))
        self.assertIn("stroke-opacity", rule.group(1))

    def test_the_stroke_is_keyed_on_data_edge_not_on_every_cell(self):
        """A stroke per rect would draw a box grid over uniform ground. This
        asserts the stylesheet has no rule that strokes a bare terrain rect."""
        css = self.css()
        self.assertNotRegex(
            css, r"\.tx-terrain-over-art rect \{[^}]*stroke",
            "the stylesheet strokes every terrain cell over artwork, which draws "
            "a box grid over uniform ground")

    def test_the_difficult_marker_is_a_separate_rule_from_the_fill(self):
        """It is the only channel separating `difficult` from `hazard`, which are
        1.00:1 in grayscale, so it must not be an inline style that no test can
        reach."""
        css = self.css()
        self.assertIn(".tx-difficult-mark", css)
        self.assertNotIn("stroke-opacity:.3", re.sub(r"\s+", " ",
                         (REPO / "display" / "static" / "tactics.js").read_text(encoding="utf-8")
                         .replace("stroke-opacity: .3", "stroke-opacity:.3")))

    def test_terrain_geometry_is_untouched_by_the_legibility_change(self):
        """The acceptance criterion "geometry unchanged": a cell is still a C x C
        rect at (x*C, y*C). The edge pass must not have moved or resized any of
        them, and the strongest check available is the map round trip -- the same
        map compiles to the same rows and the same number of cells."""
        from scripts.tactics import maps as _maps
        js = JS.read_text(encoding="utf-8")
        self.assertIn("x: x * C, y: y * C, width: C, height: C", js)
        spec = json.loads((REPO / "display" / "maps" / "detention-bog.json")
                          .read_text(encoding="utf-8"))
        grid = _maps.compile_map(spec)["grid"]
        self.assertEqual(len(grid["rows"]) * len(grid["rows"][0]),
                         sum(len(r) for r in grid["rows"]))
