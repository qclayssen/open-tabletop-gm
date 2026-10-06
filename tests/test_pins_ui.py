"""The pin layer and the note panel, in `display/static/tactics.js`.

Three kinds of test, and the first is the one that matters:

1. **Structural** -- things that are true of the *shape* of the code rather than
   of its output. A pin label reaches the DOM through `textContent` and never
   through an HTML sink, and the pin layer is a layer of its own. Both are
   source-level properties, and both are the kind a behavioural test misses:
   `textContent` and `innerHTML` receive the identical string, and a `<script>`
   inserted via `innerHTML` does not even execute per the HTML spec, so the spec's
   own proposed test ("a note body with `<script>` is not executed") is satisfied
   by exactly the bug it is meant to catch. `verifier` and `security` both found
   this independently.

2. **Geometry** -- run through node over the block tactics.js marks DOM-free,
   the way `test_display_tactics_ui.py` does. Every helper takes the cell pitch as
   a parameter rather than reading the module-level `C`, because that block is
   extracted and executed with nothing else in scope: a helper reaching for `C`
   raises ReferenceError here rather than in a browser.

3. **Parity** -- every class the pin code sets exists in tactics.css, because
   `ScriptAndStylesheetAgree` in `test_display_tactics_ui.py:314` is the gate
   that would otherwise fail on this change rather than on the next one.
"""
from __future__ import annotations

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

PURE = re.compile(r"/\* Pure helpers:.*?\*/(.*?)/\* end pure helpers \*/", re.S)


def _pure_helpers() -> str:
    m = PURE.search(JS.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError("tactics.js no longer has its marked pure-helper block")
    return m.group(1)


def _run(js: str):
    """Run a snippet with the pure helpers in scope; return its JSON result.

    Over stdin, not argv, for the reason `test_display_tactics_ui.py:56` gives:
    the pure block holds non-ASCII source and a command-line argument is encoded
    with the filesystem encoding.
    """
    if not NODE:
        raise unittest.SkipTest("node is not installed")
    program = ("const out = (() => {" + _pure_helpers() + "\n" + js +
               "})();process.stdout.write(JSON.stringify(out));")
    r = subprocess.run([NODE, "-"], input=program.encode("utf-8"),
                       capture_output=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError("the pure helpers did not run: "
                             + r.stderr.decode("utf-8", "replace").strip())
    return json.loads(r.stdout.decode("utf-8"))


def _pin_block() -> str:
    """The pin drawing code, with its own comments stripped.

    A comment explaining a rule must not read as breaking it, which is the
    idiom `test_display_tactics_ui.py:285` uses for the same reason.
    """
    js = JS.read_text(encoding="utf-8")
    block = js[js.index("function drawPins()"):js.index("function drawOverlay()")]
    return "\n".join(ln for ln in block.splitlines()
                     if not ln.strip().startswith(("//", "*", "/*")))


# ── structural: the properties a behavioural test cannot see ─────────────────

class PinHasNoHtmlSink(unittest.TestCase):
    """A pin's text reaches the DOM as text, and nothing on the pin path parses it.

    This is the whole of the XSS position for a pin. `scripts/pins.py` cleans a
    label server-side and the browser escapes it again on the way out, so a pin is
    already double-handled; this test is what stops the *third* layer from being
    an `innerHTML` someone added later to make a label bold.
    """

    def test_drawPins_never_reaches_for_an_html_sink(self):
        """No HTML sink, with one allowance that is not an allowance.

        `layer.innerHTML = ''` is how every layer in this file clears itself, and
        assigning the empty string parses nothing -- it is not a sink, it is a
        reset. Asserting the substring's absence would have failed on the correct
        code, so this strips the empty-string assignment and forbids everything
        else. What is left, if anything ever changes, is a real assignment of
        data to innerHTML.
        """
        block = re.sub(r"\.innerHTML\s*=\s*(['\"])\1", "", _pin_block())
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                     "document.write", "eval(", "new Function("):
            self.assertNotIn(sink, block, f"the pin layer reached for {sink}")

    def test_the_only_html_assignment_is_the_empty_one(self):
        """Names the exception explicitly rather than relying on the regex above.

        If someone adds `layer.innerHTML = someNote` to render a note inline,
        this is the test that says why not: it is the mutation the previous test
        is guarding, and it is the one that would turn a campaign file into
        markup on every player's screen.
        """
        assignments = re.findall(r"\.innerHTML\s*=\s*([^;\n]+)",
                                 _pin_block())
        for value in assignments:
            self.assertIn(value.strip(), ("''", '""'),
                          f"innerHTML assigned something other than empty: {value!r}")

    def test_the_label_is_a_text_node(self):
        """The specific line, not just the absence of a sink.

        `label.textContent = ...` is what makes a GM's label inert; asserting the
        absence of `innerHTML` alone would also pass if the label were assigned
        via `.innerText`, which parses nothing but is not what we mean either.
        """
        self.assertIn(".textContent =", _pin_block())

    def test_the_note_read_route_cannot_reach_a_sink_through_the_pin_path(self):
        """openPin fetches text and shows it; it does not render it here.

        The panel is filled with `textContent` in showNotePanel. Note rendering
        is the browser's existing `_renderMarkdown`, which escapes first and is
        the display's one markdown path -- but that is a separate module and this
        test is here so the pin path cannot quietly grow its own renderer.
        """
        js = JS.read_text(encoding="utf-8")
        start = js.index("async function openPin(")
        end = js.index("function currentMapSlug()")
        block = "\n".join(ln for ln in js[start:end].splitlines()
                          if not ln.strip().startswith(("//", "*", "/*")))
        for sink in ("innerHTML", "_renderMarkdown"):
            self.assertNotIn(sink, block, f"openPin reached for {sink}")


class PinLayerIsItsOwnLayer(unittest.TestCase):
    """Pins cannot live in a layer the redraw clears.

    `drawOverlay()` starts with `o.innerHTML = ''` and is re-run from
    `hoverSquare()` on every pointermove, so pins drawn into `ui.overlay` or
    `ui.markLayer` would be destroyed about sixty times a second while a mouse
    moved across the map. That is a visible, immediately obvious failure, and it
    is invisible in every test that renders one frame.
    """

    def test_the_pin_layer_is_not_the_overlay_or_the_mark_layer(self):
        js = JS.read_text(encoding="utf-8")
        match = re.search(r"ui\.pinLayer = svg\('g', \{([^}]*)\}, s\);", js)
        self.assertIsNotNone(match, "the pin layer is not created in buildBoard")
        attrs = match.group(1)
        self.assertIn("tx-pin-layer", attrs)

    def test_drawPins_is_called_from_renderBoard(self):
        """Not from drawOverlay, which is the layer that gets cleared."""
        js = JS.read_text(encoding="utf-8")
        render = js[js.index("function renderBoard()"):js.index("function clearLayers()")]
        self.assertIn("drawPins()", render)
        overlay = js[js.index("function drawOverlay()"):js.index("function drawSight()")]
        self.assertNotIn("drawPins()", overlay,
                         "drawPins in drawOverlay would be undone by its own clear")


# ── geometry ────────────────────────────────────────────────────────────────

@unittest.skipUnless(NODE, "node is not installed")
class PinGeometry(unittest.TestCase):
    """The numbers, pinned where a change to them is visible on a table."""

    def test_kind_is_carried_by_shape_not_colour(self):
        """A triangle is a note, a diamond is a map.

        Colour alone would not survive greyscale or colour blindness, and this is
        the difference between a GM clicking a map pin expecting prose and
        getting a fight.
        """
        self.assertEqual(_run('return {n: pinGlyph("note"), m: pinGlyph("map")};'),
                         {"n": "triangle", "m": "diamond"})

    def test_the_two_shapes_are_distinct_paths(self):
        out = _run('return {n: pinPath({x:3,y:4,kind:"note"},32),'
                   '           m: pinPath({x:3,y:4,kind:"map"},32)};')
        self.assertNotEqual(out["n"], out["m"])

    def test_neither_shape_reaches_into_the_square(self):
        """A pin lives in the corner and must not grow into the token.

        The token silhouette occupies roughly +4..+28 of a 32-unit square and the
        HP bar and condition badges sit at +27 and below, so a pin that grew
        toward the cell's centre or its lower edge would be drawn over the thing
        it is pointing at -- or under it, which is worse, because then nobody can
        click it. Asserted as a bound on the path's extent, not on its exact
        numbers, so tightening the pin later is not a test failure.
        """
        out = _run("""
          const extent = d => { const n = d.match(/-?[0-9.]+/g).map(Number);
            const xs = n.filter((_, i) => i % 2 === 0), ys = n.filter((_, i) => i % 2 === 1);
            return {minX: Math.min(...xs), maxX: Math.max(...xs),
                    minY: Math.min(...ys), maxY: Math.max(...ys)}; };
          const cell = 32, at = {x: 3, y: 4};
          const top = at.y * cell, left = at.x * cell;
          const centre = pinCentre(at, cell);
          const n = extent(pinPath({...at, kind: 'note'}, cell));
          const m = extent(pinPath({...at, kind: 'map'}, cell));
          return {top, left, centre, n, m};
        """)
        for name in ("n", "m"):
            box = out[name]
            self.assertGreaterEqual(box["minX"], out["left"], f"{name} runs off the left")
            self.assertLess(box["maxX"], out["left"] + 32, f"{name} runs off the right")
            self.assertLessEqual(box["maxY"] - out["top"], 20,
                                 f"{name} reaches too far down the square to clear a token")

    def test_a_pin_centres_in_the_corner_not_the_middle(self):
        """The centre of a square belongs to a token."""
        out = _run("return {c: pinCentre({x:3,y:4},32)};")
        cx, cy = out["c"]
        self.assertLess(cx, 3 * 32 + 32 / 2)
        self.assertLess(cy, 4 * 32 + 32 / 2)

    def test_a_long_label_is_truncated_and_never_wrapped(self):
        out = _run('return {long: pinLabel({label:"a very long pin label indeed"},32),'
                   '           short: pinLabel({label:"Wreck"},32),'
                   '           empty: pinLabel({},32)};')
        self.assertEqual(out["short"], "Wreck")
        self.assertEqual(out["empty"], "")
        self.assertTrue(out["long"].endswith("…"))
        self.assertLessEqual(len(out["long"]), 18)
        self.assertNotIn("\n", out["long"])

    def test_a_label_is_not_shrunk_by_a_small_board(self):
        """A phone-sized board still gets a readable label, not one character.

        The cap scales with the cell, but it is floored at 4: below that a label
        is an ellipsis, which tells the GM nothing at all.
        """
        out = _run('return {small: pinLabel({label:"Wreck"},10)};')
        self.assertGreaterEqual(len(out["small"]), 4)

    def test_an_off_map_pin_is_dropped_not_clamped(self):
        """Clamping would put a pin from off-map onto a real square.

        A clamped pin reads as being somewhere it is not, which for a pin that
        marks a location is the one failure that matters.
        """
        out = _run("return {ok: pinOnBoard({x:3,y:4},24,18),"
                   "          right: pinOnBoard({x:24,y:4},24,18),"
                   "          low: pinOnBoard({x:3,y:18},24,18),"
                   "          neg: pinOnBoard({x:-1,y:0},24,18),"
                   "          nan: pinOnBoard({x:NaN,y:1},24,18)};")
        self.assertTrue(out["ok"])
        for name in ("right", "low", "neg", "nan"):
            self.assertFalse(out[name], f"{name} was not dropped")


# ── parity with the stylesheet ──────────────────────────────────────────────

class PinClassesAreStyled(unittest.TestCase):
    """Every class the pin code sets exists in the sheet.

    `ScriptAndStylesheetAgree` (`test_display_tactics_ui.py:314`) is the test that
    fails when a class is set and never styled. Doing it here means the pin layer
    cannot land with a silently unstyled class and only be noticed later.
    """

    def test_the_pin_classes_exist_in_the_stylesheet(self):
        js = JS.read_text(encoding="utf-8")
        css = CSS.read_text(encoding="utf-8")
        for cls in ("tx-pin-layer", "tx-pin", "tx-pin-note", "tx-pin-map",
                    "tx-pin-label"):
            # Both halves are checked without dumping either file: assertIn and
            # assertRegex print the whole haystack on failure, and these are
            # 400-line and 80-line files. The named class is the message.
            self.assertTrue(cls in js, f"{cls}: not set by tactics.js")
            self.assertTrue(re.search(rf"\.{re.escape(cls)}\b", css),
                            f"{cls}: set by tactics.js but absent from tactics.css")

    def test_no_pin_class_is_assembled_at_runtime(self):
        """A composed class name is invisible to the parity check.

        `ScriptAndStylesheetAgree` reads class names out of the JS and looks each
        one up in the stylesheet. A name built at runtime -- `'tx-pin-' + kind` --
        is in neither file, so the check passes for the wrong reason and the pin
        loses its colour with nothing failing. This is the case that made
        `tx-pin-note` unstyled for one run of this branch.
        """
        block = _pin_block()
        for prefix in ("tx-pin",):
            self.assertNotIn(f"'{prefix}-' +", block,
                             f"a {prefix} class is composed at runtime")
            self.assertNotIn(f'"{prefix}-" +', block,
                             f"a {prefix} class is composed at runtime")

    def test_a_pin_label_is_styled_with_a_halo_like_the_cell_labels(self):
        """A pin label sits over terrain and artwork, so it needs the halo.

        `.tx-cell-lbl` has been doing this since before pins existed; a pin label
        without it is unreadable over a dark wall.
        """
        css = CSS.read_text(encoding="utf-8")
        rule = re.search(r"\.tx-pin-label \{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("paint-order", rule.group(1))
        self.assertIn("pointer-events: none", rule.group(1),
                      "a label must not steal the click from its own pin")


if __name__ == "__main__":
    unittest.main()