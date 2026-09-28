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
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
JS = REPO / "display" / "static" / "tactics.js"
CSS = REPO / "display" / "static" / "tactics.css"
NODE = shutil.which("node")

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


def _run(js: str) -> dict:
    """Run a snippet with the pure helpers in scope; return its JSON result."""
    if not NODE:
        raise unittest.SkipTest("node is not installed")
    body = _pure_helpers() + "\n" + js
    r = subprocess.run([NODE, "-e", "const out = (() => {" + body + "})();"
                        "process.stdout.write(JSON.stringify(out));"],
                       capture_output=True, encoding="utf-8", timeout=30)
    if r.returncode != 0:
        raise AssertionError(f"the pure helpers did not run: {r.stderr.strip()}")
    return json.loads(r.stdout)


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
        js = JS.read_text(encoding="utf-8")
        self.assertIn("svg('circle', { cx, cy, r: C / 2 - 4", js)
        self.assertIn("svg('path', { d: octagon(cx, cy, C / 2 - 4)", js)

    def test_every_side_has_its_own_frame_colour_and_word(self):
        out = _run("return {cls: Object.values(SIDES).map(s => s.cls), "
                   "words: Object.values(SIDES).map(s => s.word), "
                   "glyphs: Object.values(SIDES).map(s => s.glyph), "
                   "colours: Object.values(SIDES).map(s => s.colour)};")
        self.assertEqual(sorted(out["cls"]), ["tx-side-enemy", "tx-side-other", "tx-side-pc"])
        self.assertEqual(len(set(out["colours"])), 3, "the three sides share a colour")
        self.assertEqual(len(set(out["glyphs"])), 3, "two sides share a glyph")
        self.assertEqual(sorted(out["words"]), ["ally", "enemy", "neutral"])


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
        js = self.js()
        self.assertRegex(js, r"t\.side === 'enemy'\)\s*\n?\s*svg\('path', \{ d: octagon\(")
        self.assertRegex(js, r"else\s*\n?\s*svg\('circle', \{ cx, cy, r: C / 2 - 4")

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


if __name__ == "__main__":
    unittest.main()
