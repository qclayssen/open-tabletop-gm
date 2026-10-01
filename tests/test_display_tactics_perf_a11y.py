"""Two things about the combat panel a browser can show and a grep cannot.

W12, the board cache: tactics.js used to rebuild the whole SVG on every
snapshot, W*H terrain rects and all, to show a token that had moved. The
static layers (terrain, artwork, grid, labels, fog) are now built once and
kept, keyed on the map. What that has to get right is the failure the audit
names first: a stale layer on a map change. So the tests here push two
snapshots and assert the DOM objects are literally the same, then push a
snapshot that changes one of the things in the key and assert the board was
torn down and rebuilt with nothing of the old one left on it.

W13, the keyboard: rebuild-and-refocus, Escape from anywhere, a visible focus
ring. Lives below in KeyboardAndFocus.

The board-key rule itself (fogKey / boardKey) is pure in tactics.js, so it is
run under node and pinned without a browser, in the same way
test_display_tactics_ui.py runs boardCell and octagon. The DOM behaviour needs
a real browser and is skipped when playwright or its Chromium is missing.

The browser and the static server come from tests/_browser.py (W15).
"""
import pathlib
import unittest

from tests._browser import BrowserTestCase
from tests.test_display_tactics_ui import CSS, JS, NODE, _run

REPO = pathlib.Path(__file__).resolve().parent.parent


# A grid to test the key against: 6x3, two walls.
ROWS = ["......",
        ".##...",
        "......"]


def _snap(name="Frog Pond", rows=None, fog=None, **meta):
    """A snapshot shaped like sync.snapshot's, small enough to read."""
    s = {"status": "active", "round": 1, "current": "kairos", "unseen_turn": False,
         "meta": dict({"name": name}, **meta),
         "grid": {"name": name, "rows": list(rows or ROWS)},
         "order": ["kairos"], "turn": {"movement_left": 30, "action_used": False},
         "tokens": [{"id": "kairos", "name": "Kairos", "side": "pc", "x": 0, "y": 0,
                     "hp": 10, "max_hp": 10, "ac": 15, "dead": False, "hidden": False,
                     "controller": "player", "conditions": [], "effects": []}],
         "log": []}
    if fog is not None:
        s["fog"] = fog
    return s


def _key(snap):
    rows = snap["grid"]["rows"]
    H = len(rows)
    W = len(rows[0]) if H else 0
    js = (f"return {{k: boardKey({_js(snap)}, {W}, {H})}};" if H else
          f"return {{k: boardKey({_js(snap)}, 0, 0)}};")
    return _run(js)["k"]


def _js(value):
    import json
    return json.dumps(value)


@unittest.skipUnless(NODE, "node is not installed")
class BoardCacheKey(unittest.TestCase):
    """What has to change before the terrain on screen can be kept.

    A match means the cached layers are what this snapshot asks for. Anything
    missed here is a map showing another map's ground, which is worse than the
    rebuild the cache was meant to avoid, so every input the static drawing
    reads is in the key.
    """

    def test_the_same_snapshot_always_keys_the_same(self):
        self.assertEqual(_key(_snap()), _key(_snap()))
        self.assertEqual(_key(_snap(fog={"runs": [[0, 0, 5]]})),
                         _key(_snap(fog={"runs": [[0, 0, 5]]})))

    def test_a_different_map_is_a_different_key(self):
        self.assertNotEqual(_key(_snap()), _key(_snap(name="Mage Tower")))

    def test_the_shape_is_in_the_key(self):
        six = _snap(rows=["......", "......", "......"])
        three = _snap(rows=["...", "...", "..."])
        six_wide = _snap(rows=["......", "...", "..."])
        self.assertNotEqual(_key(six), _key(three), "a shorter map reused a longer one's terrain")
        self.assertNotEqual(_key(six), _key(six_wide),
                            "a narrower map of the same rows reused the wider terrain")

    def test_a_terrain_edit_under_the_same_name_is_a_different_key(self):
        """The rows are in the key, not just the map's name and shape.

        A map edited in place keeps its name, its width and its height, so a key
        built from those three would keep drawing the terrain as it was before
        the edit.
        """
        before = _snap(rows=ROWS)
        after = _snap(rows=["......", "..#..", "......"])
        self.assertEqual(before["meta"]["name"], after["meta"]["name"])
        self.assertEqual(before["grid"]["rows"][0], after["grid"]["rows"][0])
        self.assertNotEqual(_key(before), _key(after),
                            "an edited map kept the old terrain on screen")

    def test_the_fog_is_in_the_key(self):
        """Fog decides which squares are lit, so it is terrain, not an overlay."""
        clear = _snap()
        fogged = _snap(fog={"runs": [[0, 0, 2]]})
        self.assertNotEqual(_key(clear), _key(fogged))

    def test_the_fog_runs_order_is_not_the_fog(self):
        """Same squares seen, sent in another order: still the same key.

        Sorting can only cost a rebuild here, never buy a wrong reuse, so the
        ordering the engine happens to send is not allowed to be load-bearing.
        """
        a = _snap(fog={"runs": [[0, 0, 2], [1, 0, 5], [2, 1, 3]]})
        b = _snap(fog={"runs": [[2, 1, 3], [0, 0, 2], [1, 0, 5]]})
        self.assertEqual(_key(a), _key(b))

    def test_no_fog_and_fog_over_every_square_are_different(self):
        """The case a lazy key collapses, and it is the one that lies.

        A snapshot with no `fog` key draws no fog at all; one with
        `fog: {runs: []}` shades every square. Same runs string, so a key that
        only joined the runs would keep the first board up for the second and
        show the party the whole map in the dark.
        """
        no_fog = _snap()
        everywhere = _snap(fog={"runs": []})
        self.assertNotIn("fog", no_fog)
        self.assertEqual(everywhere["fog"]["runs"], [])
        self.assertNotEqual(_key(no_fog), _key(everywhere))

    def test_one_more_square_seen_is_a_different_key(self):
        a = _snap(fog={"runs": [[0, 0, 2], [1, 0, 5]]})
        b = _snap(fog={"runs": [[0, 0, 2], [1, 0, 4]]})
        self.assertNotEqual(_key(a), _key(b))

    def test_the_rest_of_the_map_is_in_the_key(self):
        """Artwork, zones, labels and colours are all drawn from the map file.

        Each of them is a static layer too, so a snapshot that changes one has
        to rebuild rather than keep the layer drawn for the previous value.
        """
        plain = _snap()
        for change in ({"image": "frog-pond.png"}, {"zones": [3]},
                       {"labels": [{"x": 1, "y": 1, "text": "Shallows"}]},
                       {"colors": {"water": "#2b4a6f"}}):
            with self.subTest(change=sorted(change)):
                self.assertNotEqual(_key(plain), _key(_snap(**change)),
                                    f"{sorted(change)} is not in the key")


class TheBoardIsOnlyEmptiedWhenItIsRebuilt(unittest.TestCase):
    """The cache, pinned at the source level.

    A browser test proves it works today. These three pin the things that would
    silently turn it off again: emptying the board outside the rebuild (the
    cache would never hit), re-attaching a listener to a reused svg (a click
    would send two actions), and dropping the check that the cached svg is still
    the one on screen (a stale board would be drawn into nothing).
    """

    def setUp(self):
        self.js = JS.read_text(encoding="utf-8")

    def block(self, name):
        """The code of one function, by brace count, with its comments stripped.

        A regex cannot do this and a fixed line range cannot either: the two
        functions are adjacent, so slicing between their signatures returns
        nothing at all. Comments go first so that a comment explaining the rule
        cannot read as breaking it.
        """
        start = self.js.index(f"function {name}(")
        depth, i = 0, self.js.index("{", start)
        while i < len(self.js):
            if self.js[i] == "{":
                depth += 1
            elif self.js[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        code = self.js[start:i + 1]
        return "\n".join(ln for ln in code.splitlines()
                         if not ln.strip().startswith("//"))

    def test_the_board_is_emptied_exactly_once_and_only_in_the_rebuild(self):
        self.assertEqual(self.js.count("el.board.innerHTML = '';"), 1,
                         "the board is emptied more than once, so a reused svg is discarded")
        self.assertIn("el.board.innerHTML = '';", self.block("buildBoard"),
                      "the board is emptied outside buildBoard")
        self.assertNotIn("innerHTML", self.block("renderBoard"),
                         "renderBoard empties the board, so the cache never hits")

    def test_the_board_listeners_are_attached_in_the_rebuild_not_the_redraw(self):
        render = self.block("renderBoard")
        for listener in ("pointermove", "onBoardClick"):
            self.assertNotIn(listener, render,
                             f"{listener} is re-attached on every render and would stack up")
        build = self.block("buildBoard")
        self.assertIn("s.addEventListener('pointermove', onHover);", build)
        self.assertIn("s.addEventListener('click', onBoardClick);", build)

    def test_reuse_is_guarded_on_the_svg_still_being_the_one_shown(self):
        self.assertIn("const reuse = key === ui.boardKey && ui.svg && ui.svg.parentNode === el.board;",
                      self.block("renderBoard"),
                      "a cache hit no longer checks that the cached svg is the one on screen")

    def test_the_layers_that_only_grow_are_emptied_on_every_redraw(self):
        """drawToken only appends, so something has to empty what it fills."""
        cleared = self.block("clearLayers")
        for layer in ("ui.defsLayer", "ui.tokenLayer", "ui.floatLayer"):
            self.assertIn(layer, cleared, f"{layer} is filled but never emptied")


class StaticBoardLayers(BrowserTestCase):
    """W12, measured: the same DOM objects, and a full teardown when they differ.

    Identity is the assertion, not a count. A board that happens to draw the
    same number of rects for the wrong reason would pass a count, so the svg
    and the terrain group are tagged and compared by reference.
    """

    server_kind = "static"
    ready_js = "window.__ready === true"

    def setUp(self):
        self.page = self.open_page(size=(1440, 900), wait=0)

    # ── helpers ─────────────────────────────────────────────────────────────

    def snap(self, **changes):
        """The harness's own snapshot, as a dict this test can change."""
        snap = self.page.evaluate("() => JSON.parse(JSON.stringify(window.__SNAP))")
        for key, value in changes.items():
            if key == "rows":
                snap["grid"]["rows"] = value
            elif key == "token":
                snap["tokens"][0].update(value)
            elif key == "portraits":
                # The art is gitignored, so a portrait that will not load is the
                # normal state on a clone; the clip it needs is still made.
                for i, t in enumerate(snap["tokens"]):
                    t["portrait"] = "portraits/tok-%d.png" % i
            else:
                snap[key] = value
        return snap

    def push(self, snap):
        self.page.evaluate("(s) => { Tactics.update(s); }", snap)

    def tag(self):
        """Mark the svg and the terrain group, the way an expando property would."""
        return self.page.evaluate("""() => {
          const b = document.getElementById('tx-board');
          const svg = b.querySelector('svg');
          const terrain = b.querySelector('.tx-terrain');
          window.__tag = {svg, terrain,
                          rects: document.querySelectorAll('#tx-board rect').length};
          return {rects: window.__tag.rects, hasTerrain: !!terrain,
                  terrainRects: terrain ? terrain.querySelectorAll('rect').length : 0};
        }""")

    def compare(self):
        return self.page.evaluate("""() => {
          const b = document.getElementById('tx-board');
          const svg = b.querySelector('svg');
          const terrain = b.querySelector('.tx-terrain');
          return {
            sameSvg: svg === window.__tag.svg,
            sameTerrain: terrain === window.__tag.terrain,
            rects: document.querySelectorAll('#tx-board rect').length,
            rectsBefore: window.__tag.rects,
            terrainRects: terrain ? terrain.querySelectorAll('rect').length : 0,
            svgs: b.querySelectorAll('svg').length,
            tokens: b.querySelectorAll('.tx-tok').length,
            fog: b.querySelectorAll('.tx-fog').length,
          };
        }""")

    # ── the cache ───────────────────────────────────────────────────────────

    def test_two_snapshots_of_the_same_map_keep_the_same_svg_and_terrain(self):
        """The headline: the same DOM objects, not an equal-looking rebuild.

        The token moves and one HP goes down, so the dynamic layers really are
        redrawn; the ground under them is not rebuilt to do it.
        """
        before = self.tag()
        self.assertTrue(before["hasTerrain"], "the terrain group is not findable")
        self.assertEqual(before["terrainRects"], 20 * 14)
        self.push(self.snap(token={"x": 3, "y": 5}))
        self.push(self.snap(token={"x": 4, "y": 5, "hp": 6}))
        after = self.compare()
        self.assertTrue(after["sameSvg"], "the svg was rebuilt for a map that did not change")
        self.assertTrue(after["sameTerrain"], "the terrain was redrawn for a map that did not change")
        self.assertEqual(after["svgs"], 1, "a second svg was left on the board")
        self.assertEqual(after["tokens"], 5, "the tokens were not redrawn")
        # And the terrain is not just the same node: the same 280 cells, with
        # the tokens' own bars on top, so the rect count holds still.
        self.assertEqual(after["rects"], after["rectsBefore"])

    def test_the_board_does_not_grow_over_a_run_of_pushes(self):
        first = self.tag()["rects"]
        for i in range(1, 8):
            self.push(self.snap(token={"x": 2 + (i % 5), "y": 6, "hp": 9 - (i % 4)}))
            now = self.page.evaluate(
                "() => document.querySelectorAll('#tx-board rect').length")
            self.assertEqual(now, first, f"push {i} changed the rect count")

    def test_a_reused_board_sends_one_action_per_click(self):
        """A listener re-attached to a reused svg would double every click."""
        self.page.evaluate("""() => {
          window.__posts = [];
          window.fetch = (url, opts) => {
            if (url === '/combat/do') window.__posts.push(JSON.parse(opts.body));
            return Promise.resolve({ok: true, json: () => Promise.resolve({})});
          };
        }""")
        self.push(self.snap(token={"x": 3}))
        self.push(self.snap(token={"x": 4}))
        self.page.evaluate(
            "() => document.querySelector('#tx-board .tx-tok').dispatchEvent("
            "new MouseEvent('click', {bubbles: true}))")
        self.page.wait_for_timeout(120)
        posts = self.page.evaluate("() => window.__posts")
        self.assertEqual(len(posts), 1,
                         f"one click produced {len(posts)} actions on a reused board")

    def test_a_resize_refits_the_board_without_rebuilding_it(self):
        """The svg is sized to the box, not to the map, so it is set every time."""
        self.tag()
        width = self.page.evaluate(
            "() => document.querySelector('#tx-board svg').getAttribute('width')")
        self.page.set_viewport_size({"width": 800, "height": 600})
        self.page.wait_for_timeout(500)            # the resize handler debounces 150ms
        after = self.compare()
        self.assertTrue(after["sameSvg"], "a resize rebuilt the terrain")
        now = self.page.evaluate(
            "() => document.querySelector('#tx-board svg').getAttribute('width')")
        self.assertNotEqual(str(now), str(width), "the board did not refit for a narrower panel")

    # ── the failure mode: a stale layer on a map change ─────────────────────

    def test_a_different_map_rebuilds_everything(self):
        self.tag()
        self.push(self.snap(name="Mage Tower",
                            rows=["#####", ".....", "#####"],
                            meta={"image": None}))
        after = self.compare()
        self.assertFalse(after["sameSvg"], "a different map reused the first map's svg")
        self.assertFalse(after["sameTerrain"], "a different map reused the first map's terrain")
        self.assertEqual(after["svgs"], 1, "the old svg was left on the board")
        self.assertEqual(after["terrainRects"], 5 * 3,
                         "the new map's terrain was not drawn from its own rows")

    def test_a_map_with_artwork_does_not_leave_the_old_maps_artwork_behind(self):
        self.tag()
        self.push(self.snap(meta={"image": "frog-pond.png"}))
        self.assertEqual(self.page.evaluate(
            "() => document.querySelectorAll('#tx-board .tx-art').length"), 1)
        # ...and back again, which is the direction that leaves the ghost.
        self.push(self.snap(meta={"image": None}))
        left = self.page.evaluate("""() => ({
          art: document.querySelectorAll('#tx-board .tx-art').length,
          overArt: document.querySelectorAll('#tx-board .tx-terrain-over-art').length,
          clipped: document.querySelectorAll('#tx-board rect[clip-path]').length,
        })""")
        self.assertEqual(left["art"], 0, "the previous map's artwork is still on the board")
        self.assertEqual(left["overArt"], 0, "the previous map's translucent terrain is still there")
        self.assertEqual(left["clipped"], 0, "a clipped cell survived a map without art")

    def test_a_terrain_edit_under_the_same_name_is_redrawn(self):
        """Name, width and height all unchanged: only the rows say it is a new map."""
        self.tag()
        rows = [list(r) for r in self.snap()["grid"]["rows"]]
        rows[7] = list("#" * 20)
        self.push(self.snap(rows=rows))
        after = self.compare()
        self.assertFalse(after["sameSvg"],
                         "an edited map kept the terrain drawn for the old rows")
        self.assertFalse(after["sameTerrain"], "an edited map kept the old terrain group")
        self.assertEqual(after["terrainRects"], 20 * 14,
                         "the edited rows were not drawn")
        drawn = self.page.evaluate(
            "() => document.querySelectorAll('#tx-board .tx-terrain rect[data-t=\"wall\"]').length")
        self.assertEqual(drawn, 20, "the new wall row was not drawn")

    # ── fog: part of the terrain, not an overlay ────────────────────────────

    def test_a_fog_change_rebuilds_the_board(self):
        """One row of the map seen: the other thirteen are shadowed."""
        self.tag()
        self.push(self.snap(fog={"runs": [[0, 0, 19]]}))
        after = self.compare()
        self.assertFalse(after["sameSvg"],
                         "a fog change reused the board, which kept the old fog with it")
        self.assertEqual(after["fog"], 13 * 20,
                         "the fog is not drawn from the snapshot's runs")

    def test_a_steady_fog_is_cached_rather_than_rebuilt_every_push(self):
        """The other half: fog in the key must not mean fog defeats the cache."""
        self.push(self.snap(fog={"runs": [[0, 0, 19]]}))
        self.tag()
        self.push(self.snap(fog={"runs": [[0, 0, 19]]}, token={"x": 2}))
        after = self.compare()
        self.assertTrue(after["sameSvg"], "an unchanged fog still rebuilt the board")
        self.assertEqual(after["fog"], 13 * 20, "the fog was lost on the cached board")

    def test_no_fog_at_all_and_fog_over_everything_are_not_the_same_board(self):
        """The lie: a clear board kept up for a map the party can see nothing of."""
        self.tag()
        self.push(self.snap(fog={"runs": []}))
        after = self.compare()
        self.assertFalse(after["sameSvg"],
                         "fog over every square reused the unfogged board")
        self.assertEqual(after["fog"], 20 * 14, "every square should be fogged")
        # And the other direction: the fog has to go again when it does.
        self.push(self.snap(fog={"runs": []}))
        self.push(self.snap())
        self.assertEqual(self.page.evaluate(
            "() => document.querySelectorAll('#tx-board .tx-fog').length"), 0,
            "the fog survived a snapshot with no fog of its own")

    # ── the layers that are emptied every time ──────────────────────────────

    def test_tokens_clips_and_floats_do_not_stack_up(self):
        """clearLayers exists for the three layers whose draw calls only add.

        drawToken appends a token and a portrait clip and never removes either,
        and a floating number leaves on its own timer. On a cached board nothing
        else would ever empty them, so the count has to be the same after one
        push as after five whatever the pushes changed.
        """
        self.push(self.snap(portraits=True, token={"hp": 6}))
        once = self.counts()
        for i in range(1, 5):
            self.push(self.snap(portraits=True, token={"x": i, "hp": 10 - i}))
        five = self.counts()
        self.assertEqual(five["tokens"], 5, "tokens accumulated across pushes")
        self.assertEqual(five["clips"], 5,
                         "portrait clips accumulated, so a dead token's clip outlived it")
        self.assertEqual(five["floats"], once["floats"], "floating numbers accumulated")
        self.assertGreater(five["floats"], 0, "the harness snapshot should float something")

    def counts(self):
        return self.page.evaluate("""() => ({
          tokens: document.querySelectorAll('#tx-board .tx-tok').length,
          clips: document.querySelectorAll('#tx-board clipPath').length,
          floats: document.querySelectorAll('#tx-board .tx-float').length,
        })""")


# A fake engine, so a mode can actually be armed without a server. It answers
# the two calls a turn needs (reach, targets) and remembers what it was sent.
ENGINE = """() => {
  window.__sent = [];
  window.fetch = (url, opts) => {
    const body = JSON.parse(opts.body);
    window.__sent.push(body.cmd);
    let result = {};
    if (body.cmd === 'targets') result = {targets: [
      {target: 'frog-1', attack: 'Longsword', legal: true, hit_percent: 65},
      {target: 'frog-2', attack: 'Longsword', legal: true, hit_percent: 60}]};
    if (body.cmd === 'reachable') result = {walk: {B2: 0, C2: 0}, dash: {E2: 0}};
    return Promise.resolve({ok: true, json: () => Promise.resolve({result})});
  };
}"""

# A question for the engine to ask, which opens the prompt ask() builds. The
# dice go on the FIRST line: ask() reads the first line to decide which of the
# three prompts to build, and a "rolls 1d20+3" on a second line is text it never
# looks at.
PENDING = """() => {
  window.fetch = (url, opts) => Promise.resolve({ok: true, json: () =>
    Promise.resolve({pending: 'Kairos attacks Giant Frog 1, rolls 1d20+3'})});
}"""

# The focus ring on whatever currently has the keyboard, read as the browser
# resolved it rather than as a string in the stylesheet.
RING = """() => {
  const a = document.activeElement, s = getComputedStyle(a);
  return {tag: a.tagName, id: a.id || null, key: a.dataset ? (a.dataset.key || null) : null,
          width: s.outlineWidth, offset: s.outlineOffset,
          colour: s.outlineColor, style: s.outlineStyle};
}"""

# Where the keyboard is, and what the panel says about it.
WHERE = """() => {
  const a = document.activeElement;
  const q = s => document.querySelector(s);
  return {
    tag: a ? a.tagName : null,
    id: a ? a.id : null,
    key: a && a.dataset ? (a.dataset.key || null) : null,
    text: a ? (a.textContent || '').trim().slice(0, 30) : null,
    inPanel: !!(a && document.getElementById('tx-panel').contains(a)),
    attack: !!q('#tx-leads button[data-key="Attack"]')
              && q('#tx-leads button[data-key="Attack"]').getAttribute('aria-pressed') === 'true',
    move: !!q('#tx-leads button[data-key="Move"]')
              && q('#tx-leads button[data-key="Move"]').getAttribute('aria-pressed') === 'true',
    attackChoices: document.querySelectorAll('#tx-actions [data-key^="attack:"]').length,
    reachCells: document.querySelectorAll('#tx-actions .tx-reach').length,
    cursor: document.querySelectorAll('#tx-board .tx-cursor').length,
    say: (document.getElementById('tx-say').textContent || '').trim().slice(0, 40),
  };
}"""


class KeyboardAndFocus(BrowserTestCase):
    """W13: the panel is playable from the keyboard, and keeps the keyboard.

    Three separate things. The keyboard survives a rebuild, found by data-key
    rather than by text, because text is not an identity here: an attack button
    reads "Longsword (up to 65%)" and the number moves with the roll. Escape
    answers whatever is on top, from wherever the keyboard happens to be. And
    every control in the panel draws a ring when the keyboard is on it.
    """

    server_kind = "static"
    ready_js = "window.__ready === true"

    def setUp(self):
        self.page = self.open_page(size=(1440, 900), wait=0)
        self.page.evaluate(ENGINE)

    def snap(self, **changes):
        snap = self.page.evaluate("() => JSON.parse(JSON.stringify(window.__SNAP))")
        for key, value in changes.items():
            if key == "token":
                snap["tokens"][0].update(value)
            else:
                snap[key] = value
        return snap

    def push(self, snap):
        self.page.evaluate("(s) => { Tactics.update(s); }", snap)

    def where(self):
        return self.page.evaluate(WHERE)

    def focus(self, selector):
        """Put the keyboard on a control, the way a Tab would."""
        self.page.evaluate("(s) => document.querySelector(s).focus()", selector)

    def tab_to(self, name):
        """Tab until the keyboard is on the control with this data-key, or id.

        Real Tab presses, because :focus-visible only answers to keyboard
        navigation and a programmatic focus() is not one. The bound covers the
        panel plus the harness's own Send button many times over.
        """
        at = "(n) => { const a = document.activeElement; return (a.dataset.key || a.id) === n; }"
        for _ in range(60):
            if self.page.evaluate(at, name):
                return True
            self.page.keyboard.press("Tab")
        return False

    def attack(self, key="Attack"):
        self.focus('#tx-leads button[data-key="%s"]' % key)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(200)

    def ask(self):
        """Make the engine put up a question, and return when it is up.

        Dash rather than an attack, because an attack button only chooses the
        weapon: it sends nothing until a target is clicked on the board. Dash
        goes straight to act(), which is the loop that calls ask().
        """
        self.page.evaluate(PENDING)
        self.focus('#tx-actions button[data-key="Dash"]')
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(250)
        self.assertFalse(self.page.evaluate("() => document.getElementById('tx-prompt').hidden"),
                         "the engine's question did not open")

    # ── the keyboard-only flow the audit asks for ───────────────────────────

    def test_tab_to_attack_enter_arrows_escape_with_no_mouse_at_all(self):
        """The whole flow, on the keyboard, start to finish.

        Tab to Attack, Enter to arm, arrows to walk the square cursor to the
        target, Escape to call it off. The part that used to break is the
        middle: arming a mode from the keyboard left the keyboard on the Attack
        button, so the arrow keys went to a button and nothing moved.
        """
        self.page.focus("#tx-board")
        self.page.keyboard.press("Tab")          # the board's next tab stop is the action bar
        # Read by text here, not by data-key: this test is about the flow, and
        # naming the controls is another test's job.
        self.assertEqual(self.where()["text"], "Move",
                         "the first Tab off the board did not reach the action bar")
        self.page.keyboard.press("Tab")
        self.assertEqual(self.where()["text"], "Attack")
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(200)
        armed = self.where()
        self.assertTrue(armed["attack"], "Enter did not arm the attack mode")
        self.assertEqual(armed["attackChoices"], 1, "the attack list did not open")
        self.assertEqual(armed["id"], "tx-board",
                         "the keyboard stayed on the button, so the arrow keys would do nothing")
        self.assertEqual(armed["cursor"], 1, "no square cursor was drawn on the focused board")

        # Arrow keys walk the cursor. The board listens for them itself, so this
        # is the flow working, not the cursor being drawn by something else.
        self.page.keyboard.press("Home")        # the creature whose turn it is: B7
        self.assertTrue(self.where()["say"].startswith("B7"), self.where()["say"])
        for _ in range(8):
            self.page.keyboard.press("ArrowRight")
        after = self.where()
        self.assertTrue(after["say"].startswith("J7"), after["say"])
        # ...and it survives a snapshot, which is the thing that used to throw
        # the keyboard to the top of the page mid-flow.
        self.push(self.snap(token={"x": 2, "hp": 7}))
        self.assertEqual(self.where()["id"], "tx-board",
                         "a snapshot took the keyboard off the board mid-flow")
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(150)
        gone = self.where()
        self.assertFalse(gone["attack"], "Escape did not cancel the armed mode")
        self.assertEqual(gone["attackChoices"], 0, "the attack list outlived the mode")
        self.assertEqual(gone["id"], "tx-board", "Escape threw the keyboard out of the panel")

    # ── focus survives the rebuild ──────────────────────────────────────────

    def test_the_keyboard_stays_on_the_same_button_across_a_push(self):
        """A snapshot does not take the keyboard away.

        The bar is emptied and refilled on every push, so without this focus
        lands on the body and the next Tab starts from the top of the page,
        which is a different place on every action.
        """
        self.focus('#tx-leads button[data-key="Attack"]')
        for i in range(3):
            self.push(self.snap(token={"x": 2 + i, "hp": 9 - i}))
            self.assertEqual(self.where()["key"], "Attack", f"push {i} moved the keyboard")

    def test_the_keyboard_is_found_by_key_and_not_by_its_text(self):
        """An attack button's text is a number that moves; its key is a name.

        Text matching cannot find this button again once the percentage changes,
        and the percentage changes on exactly the push that most needs the
        keyboard to stay put: the attack mode re-reads its options.
        """
        self.attack()
        text_of = ("() => document.querySelector"
                   "('#tx-actions [data-key=\"attack:Longsword\"]').textContent")
        before = self.page.evaluate(text_of)
        self.assertIn("65%", before)
        self.page.evaluate("""() => {
          window.fetch = (url, opts) => Promise.resolve({ok: true, json: () =>
            Promise.resolve({result: {targets: [
              {target: 'frog-1', attack: 'Longsword', legal: true, hit_percent: 71}]}})});
        }""")
        self.page.keyboard.press("Escape")            # cancel
        self.page.wait_for_timeout(100)
        self.attack()                                 # arm again, 71%
        after = self.page.evaluate(text_of)
        self.assertIn("71%", after, "the new percentage was not drawn")
        self.assertNotEqual(before, after, "the text did not move, so this proves nothing")
        self.focus('#tx-actions [data-key="attack:Longsword"]')
        self.push(self.snap(token={"x": 5}))
        self.assertEqual(self.where()["key"], "attack:Longsword",
                         "the keyboard was lost because the text moved")

    def test_every_rebuilt_control_is_named_and_no_two_share_a_name(self):
        """The mechanism, pinned: a control with no key cannot be found again.

        The action bar is the rebuilt region. The header buttons are written
        once into the panel's markup and never emptied, so a key on them would
        name something that cannot be lost.
        """
        self.focus('#tx-leads button[data-key="Cast"]')
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(250)
        keys = self.page.evaluate("""() => {
          const all = [...document.querySelectorAll('#tx-actions button')];
          return {total: all.length,
                  keyed: all.filter(b => b.dataset.key).length,
                  dupes: all.map(b => b.dataset.key)
                              .filter((k, i, a) => k && a.indexOf(k) !== i)};
        }""")
        self.assertGreater(keys["total"], 10, "the bar did not render")
        self.assertEqual(keys["keyed"], keys["total"],
                         "a button in the bar has no data-key, so focus cannot return to it")
        self.assertEqual(keys["dupes"], [], "two controls share a key, so focus picks one")

    def test_a_button_that_greys_out_leaves_the_keyboard_in_the_panel(self):
        """Spending the action greys the whole bar out.

        A disabled control cannot hold focus at all, so there is nothing to put
        the keyboard back on and the old text match simply gave up. It goes to
        the board instead, which is where a turn is played.
        """
        self.focus('#tx-actions button[data-key="Ready"]')
        self.assertEqual(self.where()["key"], "Ready")
        self.push(self.snap(turn={"movement_left": 30, "action_used": True}))
        self.assertTrue(self.page.evaluate(
            "() => document.querySelector('#tx-actions button[data-key=\"Ready\"]').disabled"),
            "Ready should be disabled once the action is spent")
        self.assertTrue(self.where()["inPanel"],
                        "the keyboard left the panel when the button greyed out")

    # ── Escape ──────────────────────────────────────────────────────────────

    def test_escape_cancels_from_the_side_list(self):
        self.attack()
        self.focus('#tx-leads button[data-key="Attack"]')
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(150)
        after = self.where()
        self.assertFalse(after["attack"], "Escape from the side list did not cancel the mode")
        self.assertEqual(after["attackChoices"], 0, "the attack list outlived the mode")
        self.assertEqual(after["key"], "Attack", "the keyboard was thrown out of the panel")

    def test_escape_cancels_from_the_board(self):
        self.attack("Move")
        self.page.focus("#tx-board")
        self.assertTrue(self.where()["move"])
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(150)
        after = self.where()
        self.assertFalse(after["move"], "Escape on the board did not cancel the mode")
        self.assertEqual(after["reachCells"], 0, "the reachable overlay outlived the mode")

    def test_escape_cancels_the_measure_before_the_mode(self):
        """Two things armed, one Escape each, nearest first."""
        self.attack("Move")
        self.focus('[data-ruler="line"]')
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate(
            "() => document.querySelector('[data-ruler=\"line\"]').getAttribute('aria-pressed')"),
            "true")
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate(
            "() => document.querySelector('[data-ruler=\"line\"]').getAttribute('aria-pressed')"),
            "false", "the first Escape did not take the measure")
        self.assertTrue(self.where()["move"], "the first Escape also cancelled the mode")
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(100)
        self.assertFalse(self.where()["move"], "the second Escape did not cancel the mode")

    def test_escape_closes_the_engines_question(self):
        """The prompt holds the keyboard and is on top, so it is what Escape means.

        Otherwise the one thing a keyboard cannot leave is the thing the engine
        put up: Escape reached past it to whatever the board was doing and the
        question stayed on screen with the keyboard still inside it.
        """
        self.ask()
        self.assertTrue(self.page.evaluate(
            "() => document.getElementById('tx-prompt').contains(document.activeElement)"),
            "the prompt did not take the keyboard")
        sent = self.page.evaluate("() => window.__sent.length")
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(250)
        self.assertTrue(self.page.evaluate("() => document.getElementById('tx-prompt').hidden"),
                        "Escape did not close the engine's question")
        self.assertEqual(self.page.evaluate(
            "() => document.getElementById('tx-prompt').innerHTML"), "",
            "Escape left the question's controls on screen")
        # Escape answered it, exactly as the prompt's own Cancel button does:
        # the action is given up rather than sent again with no answer.
        self.assertEqual(self.page.evaluate("() => window.__sent.length"), sent,
                         "Escape re-sent the action instead of cancelling it")

    # ── the ring ────────────────────────────────────────────────────────────

    def test_every_control_in_the_panel_shows_the_panels_ring(self):
        """A control the keyboard can reach has to say where the keyboard is.

        Read as a computed style rather than as a string in the stylesheet,
        because the buttons already had a rule and the one that matters is on
        the control nobody remembered to list: the number input the engine's
        own question puts up. Both are read on a real keyboard focus, because
        :focus-visible answers to one and a programmatic focus() is not it.
        """
        self.ask()
        number = self.page.evaluate(RING)
        self.assertEqual(number["tag"], "INPUT",
                         f"the ring was read from the wrong control: {number}")
        self.assertEqual(number["style"], "solid",
                         f"the engine's own input has no drawn ring: {number}")
        self.page.keyboard.press("Escape")           # answer the question again
        self.page.wait_for_timeout(200)
        self.assertTrue(self.tab_to("Move"), "Tab never reached a lead action")
        lead = self.page.evaluate(RING)
        self.assertEqual(lead["key"], "Move", lead)
        self.assertEqual(lead["width"], number["width"], "the two rings differ in width")
        self.assertEqual(lead["offset"], number["offset"], "the two rings differ in offset")
        self.assertEqual(lead["colour"], number["colour"],
                         "the engine's own input does not get the panel's brass ring")

    def test_the_board_keeps_its_own_closer_ring(self):
        """The panel default must not flatten the board's offset.

        The board sits flush against its own frame and wants 1px of offset, so
        the rule that rings every other control in the panel leaves it out.
        """
        self.assertTrue(self.tab_to("tx-board"), "Tab never reached the board")
        board = self.page.evaluate(RING)
        self.assertEqual(board["id"], "tx-board", board)
        self.assertEqual(board["style"], "solid", board)
        self.assertTrue(self.tab_to("Move"), "Tab never reached a lead action")
        lead = self.page.evaluate(RING)
        self.assertEqual(lead["key"], "Move", lead)
        self.assertEqual(board["width"], lead["width"], board)
        self.assertEqual(board["colour"], lead["colour"], board)
        self.assertEqual(board["offset"], "1px",
                         f"the board lost the 1px offset it draws flush: {board}")
        self.assertEqual(lead["offset"], "2px", lead)


class TheKeyboardMechanism(unittest.TestCase):
    """The focus mechanism, pinned at the source level.

    The behaviour is measured in a browser above. These pin the three things
    that would silently undo it: restoring focus by text content again, a
    render that rebuilds the bar without putting the keyboard back, and a
    button factory that stops naming its buttons.
    """

    def setUp(self):
        self.js = JS.read_text(encoding="utf-8")

    def block(self, name):
        start = self.js.index(f"function {name}(")
        depth, i = 0, self.js.index("{", start)
        while i < len(self.js):
            if self.js[i] == "{":
                depth += 1
            elif self.js[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        code = self.js[start:i + 1]
        return "\n".join(ln for ln in code.splitlines()
                         if not ln.strip().startswith("//"))

    def test_focus_is_restored_by_key_and_not_by_text(self):
        for name in ("restoreFocus", "focusedKey"):
            self.assertNotIn("textContent", self.block(name),
                             f"{name} identifies a control by its text again")
        self.assertIn("dataset.key", self.block("restoreFocus"))

    def test_every_render_that_rebuilds_the_bar_puts_the_keyboard_back(self):
        for name in ("renderSide", "renderInfo"):
            self.assertIn("focusedKey()", self.block(name), f"{name} drops the keyboard")
            self.assertIn("restoreFocus(", self.block(name), f"{name} never puts it back")

    def test_every_button_is_named(self):
        self.assertIn("b.dataset.key = o.key || text;", self.block("button"))

    def test_escape_is_answered_on_the_document_and_the_prompt_first(self):
        self.assertRegex(self.js, r"document\.addEventListener\('keydown', e => \{\s*\n"
                                  r"\s*if \(e\.key !== 'Escape'\) return;")
        handler = self.block("build")
        self.assertIn("el.prompt.onEscape()", handler,
                      "Escape does not answer the engine's question first")
        self.assertIn("if (ui.mode) { clearMode(); render(); }", handler)
        self.assertIn("box.onEscape = () => finish(null);", self.block("ask"),
                      "the prompt does not offer Escape a way out")


if __name__ == "__main__":
    unittest.main()
