"""Manual grid alignment in the map editor (#143).

The acceptance criteria are "pitch/offset controls with overlay/keyboard nudge",
"metadata-only save preserves terrain/spawns", "existing backup/confirmation" and
"invalid input untouched". All four are about `mapeditor.apply_alignment` and the
editor page, and **none of them needs a Flask route** -- the rule is a pure
function on a spec dict, so it is tested directly. That is deliberate: the route
is not wired (see the note at the end and `docs/routes/map_grid_route.py.txt`),
and a test that needed it would have forced the route to exist.

The one thing that cannot be tested from Python is the pair of JavaScript
drawings: `mapseditor.js`'s `artAttrs` and `tactics.js`'s, which both decide
where a picture lands and are both mirrors of the server's `art_geometry`. A
mirror is exactly the thing that drifts, so there is a test below that runs the
editor's arithmetic under node against `art_geometry`'s numbers on the same
inputs.

`before fix:` for every case is that `mapeditor` has no `apply_alignment`,
`alignment` or `alignment_fits`, `editor_state` carries no `alignment` key, and
the editor page has no alignment box.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tactics import mapeditor as _me   # noqa: E402
from tactics import maps as _maps      # noqa: E402
from tactics.maps import art_geometry  # noqa: E402

NODE = shutil.which("node")
MAPEDITOR_JS = ROOT / "display" / "static" / "mapseditor.js"
TEMPLATE = ROOT / "display" / "templates" / "mapseditor.html"

# A played map. Walls, water, a spawn and a label: a preservation test against a
# fixture with no terrain would pass a function that dropped it.
PLAYED = {
    "name": "The Crypt", "width": 4, "height": 3, "diagonals": "5",
    "base": "floor",
    "features": [{"type": "wall", "x": 0, "y": 0, "w": 1, "h": 3},
                 {"type": "water", "x": 2, "y": 1, "w": 2, "h": 2, "label": "Pool"}],
    "spawns": [{"type": "monster", "id": "goblin-1", "x": 3, "y": 0}],
    "zones": [{"name": "crypt-floor", "points": [[0, 0], [4, 0], [4, 3]]}],
    "credit": "Ekrahir",
    "image": "images/crypt.jpg", "image_px": [400, 300],
    "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0},
}


def _played():
    return json.loads(json.dumps(PLAYED))


# ── the editor state carries the alignment ────────────────────────────────────

def test_editor_state_carries_the_alignment_so_the_box_is_filled():
    """Criterion 1 needs the inputs to start at what is saved. A page that starts
    blank teaches the GM that the file has no alignment, which is a different
    claim and a wrong one."""
    state = _me.editor_state(_played(), "crypt")
    assert state["alignment"] == {"cell_px": 100, "offset_x": 0, "offset_y": 0,
                                  "image_px": [400, 300], "has_image": True}


def test_alignment_reads_zero_for_a_map_with_no_recorded_pitch():
    """A map with no `grid` block still needs three inputs with something in them.
    A blank field that silently becomes 0 on save is worse than a 0 that always
    was going to be 0."""
    bare = {"name": "Blank", "width": 4, "height": 3, "base": "floor", "features": []}
    assert _me.alignment(bare) == {"cell_px": 0, "offset_x": 0, "offset_y": 0,
                                   "image_px": [], "has_image": False}


def test_alignment_refuses_to_let_a_bool_through_as_a_number():
    """`True` is an int in Python and `cell_px: true` is what a browser form can
    actually send. Reading it as 1 would write a 1px cell and the GM would find
    out at the table."""
    out = _me.alignment({"grid": {"cell_px": True, "offset_x": False}})
    assert out["cell_px"] == 0 and out["offset_x"] == 0


def test_alignment_ignores_a_malformed_image_px():
    """A hand-edited map with `image_px: 400` rather than a pair. Returning it
    would put a one-element list into the page and the draw would be `NaN` wide."""
    assert _me.alignment({"grid": {"cell_px": 100}, "image_px": [400]})["image_px"] == []


# ── criterion 2: a metadata-only save preserves everything else ───────────────

def test_apply_alignment_changes_the_grid_and_nothing_else():
    before = _played()
    after = _me.apply_alignment(before, {"cell_px": 50, "offset_x": 10, "offset_y": 20})
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert changed == ["grid"]
    assert after["grid"] == {"cell_px": 50.0, "offset_x": 10.0, "offset_y": 20.0}


def test_apply_alignment_cannot_reach_the_features_key_at_all():
    """Written after a mutant that added `out["features"] = cover(...)` to
    `apply_alignment` **survived** the preservation tests.

    It survived because `cover(cells_of(spec))` is idempotent: the fixture's
    rectangles are already a minimal cover, so re-deriving them returns the same
    list and every "features unchanged" assertion passes. That is luck, not
    safety -- a map whose rectangles happen not to be minimal would have had them
    rewritten, and `apply_strokes` is the only thing in this repo allowed to do
    that.

    So this asserts the property rather than the outcome: the alignment save must
    not contain a `features` assignment at all. A source check, because the bug
    class is "a line someone adds", not "a value someone got wrong".
    """
    src = (ROOT / "scripts" / "tactics" / "mapeditor.py").read_text(encoding="utf-8")
    body = src[src.index("def apply_alignment("):src.index("def alignment_fits(")]
    assert 'out["features"]' not in body
    assert 'out["spawns"]' not in body
    assert 'out["zones"]' not in body
    assert 'out["width"]' not in body and 'out["height"]' not in body
    # And it does not call the re-derivation helpers either.
    for forbidden in ("cover(", "cells_of(", "apply_strokes("):
        assert forbidden not in body, forbidden


def test_a_non_minimal_feature_list_would_be_caught_if_rewritten():
    """The fixture above is already minimal, which is what let the mutant through.
    This pins *why*: a map whose rectangles are not minimal is a real map (a GM
    hand-wrote them, or an older editor produced them), and re-deriving its cover
    changes the file. Kept as a test of `cover` so the next person building a
    fixture knows not to use a clean one."""
    spec = {"name": "C", "width": 4, "height": 3, "base": "floor",
            # Two rectangles describing one L, where the minimal cover is
            # different -- not a cover `cover()` would return unchanged.
            "features": [{"type": "wall", "x": 0, "y": 0, "w": 2, "h": 1},
                         {"type": "wall", "x": 0, "y": 1, "w": 1, "h": 2}]}
    rederived = _me.cover(_me.cells_of(spec), "floor")
    assert rederived != spec["features"]


def test_apply_alignment_preserves_terrain_features_and_spawns_exactly():
    """Compared whole, not field by field: a rectangle moved by one square still
    has a `features` key, and a spawn moved by one still has a `spawns` key."""
    before = _played()
    after = _me.apply_alignment(before, {"cell_px": 50, "offset_x": 0, "offset_y": 0})
    for key in ("features", "spawns", "zones", "base", "name", "width", "height",
                "credit", "image", "image_px", "diagonals"):
        assert after[key] == before[key], key


def test_apply_alignment_does_not_mutate_the_spec_it_was_given():
    """A caller that keeps a reference and finds it changed underneath has lost
    the "before" it needed for a rollback."""
    before = _played()
    _me.apply_alignment(before, {"cell_px": 25, "offset_x": 0, "offset_y": 0})
    assert before["grid"] == {"cell_px": 100, "offset_x": 0, "offset_y": 0}


def test_the_rows_are_unchanged_after_an_alignment_save():
    """The engine's invariant, and the reason this operation is safe at all: every
    rule is computed from `grid.rows`."""
    before = _played()
    after = _me.apply_alignment(before, {"cell_px": 33, "offset_x": 7, "offset_y": 9})
    assert _maps.compile_map(after)["grid"]["rows"] == _maps.compile_map(before)["grid"]["rows"]
    # And the board size, which this operation must never move.
    assert after["width"] == before["width"] and after["height"] == before["height"]


def test_the_result_is_a_map_the_engine_accepts():
    """`compile_map` runs inside `apply_alignment`, so a grid this page produced
    is one the engine has already agreed to load -- the same gate `/features`
    uses."""
    out = _me.apply_alignment(_played(), {"cell_px": 100, "offset_x": 0, "offset_y": 0})
    compiled = _maps.compile_map(out)
    assert compiled["meta"]["grid_align"]["cell_px"] == 100.0


def test_the_saved_alignment_always_records_all_three_numbers():
    """A map whose offsets are absent from the file is a map whose renderer has to
    guess; the editor is the one place the GM is already looking. All three are
    therefore **required** rather than defaulted, so a request that omits one is a
    bad request from a client that disagrees with this file, not a request to
    write zero.
    """
    out = _me.apply_alignment({"name": "B", "width": 4, "height": 3, "base": "floor",
                               "features": [], "grid": {"cell_px": 100}},
                              {"cell_px": 100, "offset_x": 0, "offset_y": 0})
    assert out["grid"] == {"cell_px": 100.0, "offset_x": 0.0, "offset_y": 0.0}
    assert sorted(out["grid"]) == ["cell_px", "offset_x", "offset_y"]


def test_image_px_is_dropped_for_a_map_with_no_artwork():
    """A size with no picture is what `maps.compile_map` refuses to interpret, and
    a GM aligning a map before importing its art is doing something reasonable."""
    bare = {"name": "B", "width": 4, "height": 3, "base": "floor", "features": [],
            "image_px": [400, 300]}
    out = _me.apply_alignment(bare, {"cell_px": 100, "offset_x": 0, "offset_y": 0})
    assert "image_px" not in out


# ── criterion 4: invalid input untouched ─────────────────────────────────────

@pytest.mark.parametrize("body, message", [
    ({}, "cell_px is required"),
    ({"cell_px": 100}, "offset_x is required"),
    ({"cell_px": 100, "offset_x": 0}, "offset_y is required"),
    ({"cell_px": 0, "offset_x": 0, "offset_y": 0}, "greater than 0"),
    ({"cell_px": -100, "offset_x": 0, "offset_y": 0}, "greater than 0"),
    ({"cell_px": "100", "offset_x": 0, "offset_y": 0}, "cell_px must be a number"),
    ({"cell_px": True, "offset_x": 0, "offset_y": 0}, "cell_px must be a number"),
    ({"cell_px": 100, "offset_x": None, "offset_y": 0}, "offset_x must be a number"),
    ({"cell_px": 100, "offset_x": 0, "offset_y": [0]}, "offset_y must be a number"),
    ("not an object", "must be an object"),
    (None, "must be an object"),
])
def test_invalid_input_is_refused_and_the_spec_is_untouched(body, message):
    """Criterion 4. Asserted on the *spec as well as the exception*, because a
    function that validates after mutating raises just as loudly and still loses
    the map."""
    before = _played()
    with pytest.raises(ValueError, match=message):
        _me.apply_alignment(before, body)
    assert before == PLAYED


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_number_is_refused(value):
    """JSON cannot carry NaN, so this arrives as a bare token from a hand-written
    body or a `1e999`. `float("nan") != float("nan")`, which is the test used
    here -- an equality check would let NaN through as a "finite" number."""
    before = _played()
    with pytest.raises(ValueError, match="finite"):
        _me.apply_alignment(before, {"cell_px": value, "offset_x": 0, "offset_y": 0})
    assert before == PLAYED


def test_an_integer_pitch_is_accepted():
    """The common case. A form field with `step="1"` sends `100`, not `100.0`, and
    refusing an int would make the box useless."""
    out = _me.apply_alignment(_played(), {"cell_px": 100, "offset_x": 0, "offset_y": 0})
    assert out["grid"]["cell_px"] == 100.0


# ── criterion 1's other half: the live readout ───────────────────────────────

def test_alignment_fits_reports_an_exact_fit():
    got = _me.alignment_fits(PLAYED, {"cell_px": 100, "offset_x": 0, "offset_y": 0})
    assert got == {"cells": [4, 3], "leftover": [0.0, 0.0], "exact": True}


def test_alignment_fits_reports_what_would_be_cropped():
    got = _me.alignment_fits(PLAYED, {"cell_px": 101, "offset_x": 0, "offset_y": 0})
    assert got["exact"] is False
    assert got["leftover"] != [0.0, 0.0]


def test_alignment_fits_accounts_for_the_offset():
    got = _me.alignment_fits(PLAYED, {"cell_px": 100, "offset_x": 100, "offset_y": 0})
    assert got["cells"] == [3, 3] and got["exact"] is True


@pytest.mark.parametrize("body", [
    {"cell_px": 0}, {"cell_px": -1}, {"cell_px": "x"}, {}, None, "nope",
    {"cell_px": 100, "offset_x": "y"},
])
def test_alignment_fits_never_raises(body):
    """This runs on every keystroke in a browser. A refusal here would be an
    unhandled error in the one page whose job is to let a GM try numbers, so the
    bad value comes back as `exact: False` and the refusal happens on save."""
    got = _me.alignment_fits(PLAYED, body)
    assert got["exact"] is False
    assert got["cells"] == []


def test_alignment_fits_says_nothing_without_a_recorded_picture_size():
    """A map with no `image_px` has nothing to line up, and the honest answer is
    that rather than a count of zero squares."""
    bare = {"name": "B", "width": 4, "height": 3}
    assert _me.alignment_fits(bare, {"cell_px": 100})["exact"] is False


def test_alignment_fits_matches_the_engine_it_will_be_saved_by():
    """The readout and the save must not disagree. Same inputs, same numbers, on
    every axis -- checked rather than assumed, because they are two call sites of
    what is conceptually one rule."""
    for cell in (50, 100, 33, 128):
        for ox, oy in ((0, 0), (10, 20), (37, 0)):
            body = {"cell_px": cell, "offset_x": ox, "offset_y": oy}
            readout = _me.alignment_fits(PLAYED, body)
            engine = art_geometry({"image_px": PLAYED["image_px"],
                                   "grid": {"cell_px": cell, "offset_x": ox,
                                            "offset_y": oy}})
            assert readout["cells"] == engine["cells"], (cell, ox, oy)
            assert readout["leftover"] == engine["leftover"], (cell, ox, oy)
            assert readout["exact"] is (not any(engine["leftover"]))


# ── the editor page: controls, overlay, nudge ────────────────────────────────

def test_the_page_has_the_three_inputs_a_save_button_and_a_readout():
    html = TEMPLATE.read_text(encoding="utf-8")
    for el_id in ("me-cell-px", "me-offset-x", "me-offset-y",
                  "me-align-save", "me-align-read"):
        assert f'id="{el_id}"' in html, el_id
    assert "Save alignment" in html


def test_the_pitch_input_will_not_accept_zero_from_the_browser():
    """`min="1"` is belt to the script's braces: it stops a GM typing 0 before the
    page has to explain, and the page still refuses it if the number arrives some
    other way."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r'id="me-cell-px"[^>]*min="1"', html)


def test_the_readout_is_a_live_region_so_a_keyboard_nudge_is_announced():
    """The nudge is keyboard-driven and silent otherwise, so a screen-reader user
    would have no way to know a press did anything."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r'id="me-align-read"[^>]*aria-live="polite"', html)


def test_a_nudge_does_not_save():
    """An arrow key nudges the preview and stops. It must not POST.

    Found by mutation: adding `saveAlignment()` to the keydown handler **survived**
    every other test here, because writing the file and previewing the file look
    identical from the outside -- the page ends up showing the right numbers either
    way. What it costs is a GM nudging a pitch by eye, hitting an arrow key
    seventeen times, and having written the file seventeen times without knowing
    it, with the first original now sitting in a .bak.

    So the assertion is that the nudge handler calls nothing but `nudge`, and that
    `nudge` itself touches only the input and the redraw.
    """
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    handler = js[js.index("const NUDGE = {"):js.index("el.alignSave.addEventListener")]
    assert "saveAlignment" not in handler
    assert "fetch(" not in handler
    body = js[js.index("function nudge("):js.index("async function saveAlignment()")]
    for forbidden in ("saveAlignment", "fetch("):
        assert forbidden not in body, forbidden


def test_typing_previews_without_saving():
    """Criterion 1's "live overlay". The `input` listener sets `trial`, and
    `trial` is only ever read by `draw()`; the save is a separate click on a
    separate button. That separation is asserted in the source because it is the
    whole difference between a preview and a silent write."""
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    assert "trial = readAlignment();" in js
    save = js[js.index("async function saveAlignment()"):
              js.index("function fillAlignment()")]
    before_fetch = save.split("await fetch")[0]
    assert "trial" not in before_fetch, "the save must not read the trial value"
    assert "artAttrs(state, W, H, C, trial)" in js


def test_the_four_arrow_keys_nudge_and_shift_nudges_by_ten():
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    for key, field, delta in (("ArrowLeft", "offsetX", -1), ("ArrowRight", "offsetX", 1),
                              ("ArrowUp", "offsetY", -1), ("ArrowDown", "offsetY", 1)):
        assert f"{key}: ['{field}', {delta}]" in js, key
    assert "e.shiftKey ? delta * 10 : delta" in js
    assert "e.preventDefault()" in js, "an arrow key would also move the caret"


def test_the_nudge_is_bound_to_the_inputs_and_not_the_document():
    """On the document it would fire while a GM is painting terrain, and Esc has to
    keep meaning "cancel the stroke"."""
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    nudge = js[js.index("const NUDGE = {"):
               js.index("el.alignSave.addEventListener")]
    assert "el[field].addEventListener('keydown'" in nudge
    assert "document.addEventListener" not in nudge


def test_the_saved_alignment_is_adopted_from_the_server_not_locally():
    """Same rule as the terrain save: the file is the server's answer, so the page
    cannot end up showing numbers the file does not have."""
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    assert "state.alignment = out.alignment;" in js
    assert "trial = null;" in js


def test_the_alignment_save_posts_to_the_grid_route():
    """The route is not wired; this pins the URL the editor expects, so whoever
    pastes `docs/routes/map_grid_route.py.txt` in gets a match rather than a
    404."""
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    assert "`/maps/${encodeURIComponent(state.slug)}/grid`" in js
    assert "'X-DND-Token'" in js, "the token gate is not sent"


def test_the_editor_draws_the_artwork_at_the_trial_pitch():
    """The bug that made this box necessary: the editor used to always stretch the
    picture to the board, so a GM aligning here was lining up against a different
    picture than the players would see."""
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    assert "preserveAspectRatio: 'none'" in js          # the legacy branch survives
    fn = js[js.index("function artAttrs("):js.index("// ── the board ──")]
    assert "cell / a.cell_px" in fn
    assert "offset_x || 0) * k" in fn
    assert "size[0] * k" in fn and "size[1] * k" in fn


def test_the_aligned_artwork_is_clipped_by_the_viewbox():
    """A picture drawn past the board's edge is not drawn at all in an SVG, so the
    leftover pixels simply do not appear -- the crop the spec asks for, without a
    clipPath here. Stated in a test because the reason it needs no clip is not
    obvious."""
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    fn = js[js.index("function artAttrs("):js.index("// ── the board ──")]
    assert "clipPath" not in fn
    assert "viewBox" in js


# ── the JavaScript arithmetic agrees with the engine ──────────────────────────

def _node(program: str):
    assert NODE, "node is not installed"
    r = subprocess.run([NODE, "-"], input=program.encode("utf-8"),
                       capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return json.loads(r.stdout.decode("utf-8"))


@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.parametrize("size, align", [
    ([400, 300], {"cell_px": 100, "offset_x": 0, "offset_y": 0}),
    ([400, 300], {"cell_px": 50, "offset_x": 10, "offset_y": 20}),
    ([3072, 4096], {"cell_px": 64, "offset_x": 0, "offset_y": 0}),
    ([1000, 750], {"cell_px": 128, "offset_x": 37, "offset_y": 0}),
])
def test_the_editors_art_attrs_is_the_engines_arithmetic(size, align):
    """A mirror is exactly the thing that drifts, and `mapseditor.js` and
    `tactics.js` both mirror `maps.art_geometry` because neither can import it.

    So the JS runs under node on real inputs and its numbers are checked against
    the engine's: same `k = cell / cell_px` on both axes, same `-offset * k`. A
    64px pitch on a 3072x4096 picture is in here specifically because a mistake
    that only shows up on a non-round cell would pass every round one.
    """
    cell = 32
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    fn = js[js.index("function artAttrs("):js.index("// ── the board ──")]
    st = {"image": "images/c.jpg",
          "alignment": {**align, "image_px": size}}
    k = cell / align["cell_px"]
    # The board size comes from the engine, not from `size // cell_px` written
    # here: with a non-zero offset those differ, and writing it out by hand is how
    # a test ends up pinning the wrong board.
    cells = art_geometry({"image_px": size,
                          "grid": {"cell_px": align["cell_px"],
                                   "offset_x": align["offset_x"],
                                   "offset_y": align["offset_y"]}})["cells"]
    got = _node(
        f"const st = {json.dumps(st)};\n" + fn + "\n"
        f"const a = artAttrs(st, {cells[0]}, {cells[1]}, {cell});\n"
        "process.stdout.write(JSON.stringify([a.x, a.y, a.width, a.height]));")
    assert got[0] == pytest.approx(-align["offset_x"] * k)
    assert got[1] == pytest.approx(-align["offset_y"] * k)
    assert got[2] == pytest.approx(size[0] * k)
    assert got[3] == pytest.approx(size[1] * k)


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_editor_falls_back_to_the_stretch_without_a_recorded_size():
    """Every map shipped before #142, plus any map with art and no `image_px`.
    The editor must not draw those at `width: NaN`."""
    js = MAPEDITOR_JS.read_text(encoding="utf-8")
    fn = js[js.index("function artAttrs("):js.index("// ── the board ──")]
    cases = [{"image": "a.jpg", "alignment": {"cell_px": 0, "image_px": [400, 300]}},
             {"image": "a.jpg", "alignment": {"cell_px": 100, "image_px": []}},
             {"image": "", "alignment": {"cell_px": 100, "image_px": [400, 300]}}]
    got = _node(f"const cases = {json.dumps(cases)};\n" + fn + "\n"
                "process.stdout.write(JSON.stringify(cases.map(s => artAttrs(s, 4, 3, 32))));")
    for attrs in got:
        assert attrs["width"] == 4 * 32 and attrs["height"] == 3 * 32
        assert attrs["preserveAspectRatio"] == "none"


# ── criterion 3: the existing backup policy is the one that is reused ─────────

def test_the_alignment_write_uses_the_existing_backup_policy(tmp_path):
    """`mapeditor.write` is already the repo's one map-write policy -- atomic, and
    the first original kept as `.bak` and never overwritten. The route calls it,
    so this asserts the function the route depends on still behaves, rather than
    asserting the route exists."""
    target = tmp_path / "crypt.json"
    original = json.dumps(PLAYED, indent=1) + "\n"
    target.write_text(original, encoding="utf-8")
    changed = _me.apply_alignment(json.loads(original),
                                  {"cell_px": 50, "offset_x": 0, "offset_y": 0})
    bak = _me.write(target, changed)
    assert bak.name == "crypt.json.bak"
    assert bak.read_text(encoding="utf-8") == original
    # A second save does not replace the first original with a saved version.
    _me.write(target, changed)
    assert bak.read_text(encoding="utf-8") == original


def test_the_route_block_is_documented_and_deliberately_unwired():
    """Criterion "existing backup/confirmation" is met by *reusing* the policy
    rather than by adding a second one. This test exists so that a reader who
    finds the unwired route knows it is on purpose: it asserts the block is
    present, complete, and says why it is not in the display app."""
    route = ROOT / "docs" / "routes" / "map_grid_route.py.txt"
    assert route.is_file(), "the route block is missing; a reader cannot wire it"
    text = route.read_text(encoding="utf-8")
    assert "NOT in" in text and "CAT-8" in text
    for needed in ("_token_ok()", "_mapeditor.apply_alignment", "_mapeditor.write",
                   "_mapeditor.alignment", "editor_state"):
        assert needed in text, needed
