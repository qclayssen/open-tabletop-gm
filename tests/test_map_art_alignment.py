"""Artwork drawn at its recorded grid pitch (#142, SPEC-grid-and-map 4.1).

The defect this closes: `compile_map` copied `grid` into `meta.grid_align`
verbatim and **nothing read it**. A map could declare `{"cell_px": 100,
"offset_x": 0, "offset_y": 0}`, the browser stretched the picture to the board
with `preserveAspectRatio=none`, and the recorded offsets were discarded on the
floor. The failure is invisible -- the map looks right and every distance read
off it is subtly wrong -- which is why the tests here are mostly about refusal
and about `rows` staying byte-identical.

Three layers, because they are three languages:

* `maps.art_geometry` / `grid_alignment` / `image_px`, called directly;
* `compile_map`, for what reaches the display and for what must not change;
* `artAttrs` in `display/static/tactics.js`, extracted and run under node the
  way `tests/test_display_tactics_ui.py` already does for the same block.

`before fix:` for the validation cases is that `maps` had no such functions and
`grid` was copied through unvalidated; for the renderer it is that `artAttrs`
did not exist and `buildBoard` drew `width: W*C, height: H*C,
preserveAspectRatio: none` whatever the map said.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tactics import maps  # noqa: E402
from tactics.grid import Grid  # noqa: E402

JS = ROOT / "display" / "static" / "tactics.js"
NODE = subprocess.run(["which", "node"], capture_output=True).stdout.decode().strip()

# The block tactics.js marks as free of the DOM.
_PURE_START = "/* Pure helpers:"
_PURE_END = "/* end pure helpers */"


def _pure_helpers() -> str:
    text = JS.read_text(encoding="utf-8")
    start = text.index(_PURE_START)
    end = text.index(_PURE_END, start)
    return text[text.index("*/", start) + 2:end]


def _run(js: str):
    """Run a snippet with the pure helpers in scope; return its JSON result.

    Over stdin, not argv: the block holds non-ASCII source and a command-line
    argument is encoded with the filesystem encoding, so under a non-UTF-8
    locale this would fail before node starts (the same reason and the same
    workaround as tests/test_display_tactics_ui.py).
    """
    if not NODE:
        pytest.skip("node is not installed")
    body = _pure_helpers() + "\n" + js
    program = ("const out = (() => {" + body + "})();"
               "process.stdout.write(JSON.stringify(out));")
    r = subprocess.run([NODE, "-"], input=program.encode("utf-8"),
                       capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return json.loads(r.stdout.decode("utf-8"))


@pytest.fixture
def spec():
    """A minimal valid map: 8x6 floor, no artwork, no grid block."""
    return {"name": "Crypt", "width": 8, "height": 6, "base": "floor", "features": []}


# ── the alignment is validated, not passed through ────────────────────────────

@pytest.mark.parametrize("grid, message", [
    ({"cell_px": 0}, "greater than 0"),
    ({"cell_px": -100}, "greater than 0"),
    ({"cell_px": "100"}, "must be a number"),
    ({"cell_px": True}, "must be a number"),          # a bool is an int in Python
    ({"cell_px": float("nan")}, "finite"),
    ({"cell_px": float("inf")}, "finite"),
    ({"cell_px": 100, "offset_x": "0"}, "must be a number"),
    ({"cell_px": 100, "offset_y": None}, "must be a number"),
    ("100", "must be an object"),
    ([100], "must be an object"),
])
def test_an_invalid_grid_block_is_refused(spec, grid, message):
    """Every one of these used to load. A `cell_px` of "100" or 0 reached the
    display and was read by nothing, so the map looked correct and measured
    wrong; that is the whole reason these are refused."""
    with pytest.raises(ValueError, match=message):
        maps.compile_map({**spec, "image": "images/crypt.jpg", "grid": grid})


def test_an_invalid_grid_block_is_refused_even_with_no_artwork(spec):
    """A `grid` block on a map with no image is still a claim about how the grid
    sits on that map, and it is validated whether or not anything draws it.
    Otherwise "artless map" becomes a way to smuggle a nonsense alignment in."""
    with pytest.raises(ValueError, match="greater than 0"):
        maps.compile_map({**spec, "grid": {"cell_px": -1}})


@pytest.mark.parametrize("size, message", [
    ([3200], r"\[width, height\] pair"),
    ([3200, 4500, 100], r"\[width, height\] pair"),
    (3200, r"\[width, height\] pair"),
    ([0, 4500], "greater than 0"),
    ([3200, -1], "greater than 0"),
    (["3200", "4500"], "must be a number"),
    ([3200, float("inf")], "finite"),
])
def test_an_invalid_image_px_is_refused(spec, size, message):
    with pytest.raises(ValueError, match=message):
        maps.compile_map({**spec, "image": "images/crypt.jpg",
                          "image_px": size, "grid": {"cell_px": 100}})


def test_image_px_without_a_pitch_is_refused(spec):
    """A picture's size is the scale of nothing until the pitch says how many
    pixels a square spans. Recording one alone would be the same defect this
    issue closes: a number in a file that nothing can use."""
    with pytest.raises(ValueError, match="needs grid.cell_px"):
        maps.compile_map({**spec, "image": "images/crypt.jpg", "image_px": [3200, 4500]})


def test_a_picture_smaller_than_one_square_is_refused(spec):
    with pytest.raises(ValueError, match="no 100.0px square fits"):
        maps.compile_map({**spec, "image": "images/crypt.jpg",
                          "image_px": [80, 4500], "grid": {"cell_px": 100}})


def test_offsets_past_the_picture_are_refused(spec):
    """An offset that eats the whole picture leaves no square at all, which would
    be a map that draws nothing rather than a refusal."""
    with pytest.raises(ValueError, match="no 100.0px square fits"):
        maps.compile_map({**spec, "image": "images/crypt.jpg",
                          "image_px": [3200, 4500],
                          "grid": {"cell_px": 100, "offset_x": 3200, "offset_y": 0}})


# ── what the geometry says ────────────────────────────────────────────────────

def test_art_geometry_counts_squares_and_leftover_pixels():
    """The one place `(px - offset) // cell_px` is written down. `3072x4096` at
    64px with no offset is 48x64 and nothing left over, which is the shape
    art_import accepts; a picture that does not divide leaves a strip, and the
    honest number is reported rather than stretched."""
    assert maps.art_geometry({"image_px": [3072, 4096],
                              "grid": {"cell_px": 64}}) == {
        "cell_px": 64.0, "offset_x": 0.0, "offset_y": 0.0,
        "image_px": [3072.0, 4096.0], "cells": [48, 64], "leftover": [0.0, 0.0]}


def test_art_geometry_counts_from_the_offset_and_reports_what_is_left():
    geo = maps.art_geometry({"image_px": [3210, 4500],
                             "grid": {"cell_px": 100, "offset_x": 10, "offset_y": 0}})
    # 3200 usable across at 100px is 32 squares with 0 left; the height is 45
    # squares with 0 left.
    assert geo["cells"] == [32, 45]
    assert geo["leftover"] == [0.0, 0.0]
    # And one that genuinely does not divide:
    leftover = maps.art_geometry({"image_px": [3250, 4500],
                                  "grid": {"cell_px": 100}})["leftover"]
    assert leftover == [50.0, 0.0]


def test_art_geometry_offsets_both_axes():
    geo = maps.art_geometry({"image_px": [3200, 4500],
                             "grid": {"cell_px": 100, "offset_x": 40, "offset_y": 25}})
    assert geo["cells"] == [31, 44]          # (3200-40)//100, (4500-25)//100
    assert geo["leftover"] == [60.0, 75.0]   # and the remainders


def test_grid_alignment_defaults_the_offsets_and_passes_unknown_keys_through():
    """Hex vocabulary will live in the same `grid` object (SPEC 4.2). Validation
    here must not become a silent key-stripper, so an unrecognised key is
    carried past for whoever owns it."""
    out = maps.grid_alignment({"grid": {"cell_px": 100, "shape": "hex",
                                        "orientation": "pointy"}})
    assert out["shape"] == "hex" and out["orientation"] == "pointy"
    assert out["offset_x"] == 0.0 and out["offset_y"] == 0.0


# ── what reaches the display ──────────────────────────────────────────────────

def test_the_size_and_the_alignment_reach_the_display_meta(spec):
    out = maps.compile_map({**spec, "image": "images/crypt.jpg",
                            "image_px": [3200, 4500],
                            "grid": {"cell_px": 100, "offset_x": 40,
                                     "offset_y": 25}})["meta"]
    assert out["image"] == "images/crypt.jpg"
    assert out["image_px"] == [3200.0, 4500.0]
    assert out["grid_align"] == {"cell_px": 100.0, "offset_x": 40.0, "offset_y": 25.0}


def test_a_map_with_no_image_px_keeps_the_legacy_shape(spec):
    """Every map shipped before this change has art and a `grid` block but no
    `image_px`. It must render exactly as it always did: `grid_align` present
    and `{}` when the map declares no block, and no `image_px` key invented."""
    out = maps.compile_map({**spec, "image": "images/crypt.jpg",
                            "grid": {"cell_px": 70}})["meta"]
    assert out["grid_align"] == {"cell_px": 70.0, "offset_x": 0.0, "offset_y": 0.0}
    assert "image_px" not in out


def test_every_shipped_map_still_compiles_and_keeps_its_rows(spec):
    """The real corpus, not a fixture. A map whose alignment is now refused would
    take the whole display's map list with it, so this asserts each one loads and
    that none of them grew an `image_px` out of nowhere."""
    names = maps.available()
    assert names, "no maps on disk; this test would check nothing"
    for name in names:
        out = maps.load(name)
        Grid.from_dict(out["grid"])                     # raises on anything odd
        assert out["meta"].get("image_px") is None, (
            f"{name} has no recorded picture size on disk, so it cannot have one")


# ── the invariant: terrain and rows do not move ───────────────────────────────

def test_the_image_keys_change_no_square_and_no_row(spec):
    """`grid.rows` is what every rule in the engine is computed from. Artwork is
    display-only, so adding `image`, `image_px` and a full `grid` block has to
    leave the row strings byte-identical -- same characters, same order."""
    bare = maps.compile_map(spec)
    arted = maps.compile_map({**spec, "image": "images/crypt.jpg",
                              "image_px": [3200, 4500],
                              "grid": {"cell_px": 100, "offset_x": 40,
                                       "offset_y": 25}})
    assert arted["grid"]["rows"] == bare["grid"]["rows"]
    assert arted["grid"]["diagonals"] == bare["grid"]["diagonals"]
    assert arted["grid"]["name"] == bare["grid"]["name"]
    # And with terrain painted, which is the case that matters for #151.
    painted = {**spec, "features": [{"type": "wall", "x": 2, "y": 1, "w": 3, "h": 2},
                                   {"type": "water", "x": 0, "y": 4, "w": 4, "h": 1}]}
    assert (maps.compile_map({**painted, "image": "images/crypt.jpg",
                              "image_px": [3200, 4500],
                              "grid": {"cell_px": 100}})["grid"]["rows"]
            == maps.compile_map(painted)["grid"]["rows"])


# ── the renderer ──────────────────────────────────────────────────────────────

def test_the_renderer_scales_the_art_uniformly_at_the_recorded_pitch():
    """`k = cell / cell_px`, on both axes, and the offset is subtracted in the
    same units. This is the assertion that matters: a picture stretched to the
    board instead has its 100px squares land at a different width than height."""
    got = _run("""const a = artAttrs('images/crypt.jpg', [3200, 4500],
                   {cell_px: 100, offset_x: 0, offset_y: 0}, 32, 45, 32);
                  return {x: a.x, y: a.y, w: a.width, h: a.height,
                          cropped: a.cropped === true,
                          stretched: a.preserveAspectRatio === 'none'};""")
    assert got["w"] == 32 * 32        # 3200px at k = 32/100
    assert got["h"] == 32 * 45
    assert got["cropped"] is True     # aligned art is cropped, never stretched
    assert got["stretched"] is False


def test_the_renderer_offsets_the_art_by_the_recorded_origin():
    got = _run("""const a = artAttrs('images/crypt.jpg', [3200, 4500],
                   {cell_px: 100, offset_x: 40, offset_y: 25}, 32, 45, 32);
                  return {x: a.x, y: a.y};""")
    assert got["x"] == pytest.approx(-40 * 32 / 100)
    assert got["y"] == pytest.approx(-25 * 32 / 100)


def test_a_non_square_pitch_keeps_both_axes_at_one_scale():
    """cell_px 64 on a 3072x4096 picture. The two axes must scale by the same k,
    which is what a non-square cell or a non-square picture would break."""
    got = _run("""const a = artAttrs('images/c.jpg', [3072, 4096],
                   {cell_px: 64, offset_x: 0, offset_y: 0}, 48, 64, 32);
                  return {kx: a.width / 3072, ky: a.height / 4096};""")
    assert got["kx"] == pytest.approx(got["ky"])
    assert got["kx"] == pytest.approx(0.5)


@pytest.mark.parametrize("imagePx, align", [
    (None, {"cell_px": 100}),                    # no recorded size
    ([3200, 4500], None),                         # no recorded pitch
    ([3200, 4500], {"offset_x": 0, "offset_y": 0}),   # pitch missing from the block
    ([0, 4500], {"cell_px": 100}),                # nonsense size
    ([3200, 4500], {"cell_px": 0}),               # nonsense pitch
    ([3200, 4500], {"cell_px": "100"}),           # a string pitch
    ([3200, 4500], {"cell_px": 100, "offset_x": "0"}),
])
def test_the_renderer_falls_back_to_the_legacy_stretch(imagePx, align):
    """Every one of these has to come out as `W*cell x H*cell` with
    `preserveAspectRatio: none` -- the draw every pre-existing map depends on --
    and never as a NaN attribute. A picture stretched like every other picture
    is a lesser wrong than one at `width="NaN"`."""
    got = _run(f"""const a = artAttrs('images/crypt.jpg', {json.dumps(imagePx)},
                   {json.dumps(align)}, 32, 45, 32);
                  return {{x: a.x, y: a.y, w: a.width, h: a.height,
                           stretched: a.preserveAspectRatio === 'none',
                           cropped: a.cropped === true}};""")
    assert got == {"x": 0, "y": 0, "w": 32 * 32, "h": 45 * 32,
                   "stretched": True, "cropped": False}


def test_the_renderer_treats_a_missing_offset_as_zero():
    got = _run("""const a = artAttrs('images/crypt.jpg', [3200, 4500],
                   {cell_px: 100}, 32, 45, 32);
                  return {x: a.x, y: a.y, cropped: a.cropped === true};""")
    assert got == {"x": 0, "y": 0, "cropped": True}


def test_the_board_cache_key_moves_when_the_alignment_moves():
    """A GM who saves a new alignment and sees the *old* picture under the new
    grid is a silent-wrong-answer failure, and the cached board layer is the
    thing that would hold the old picture. `boardKey` covers `image_px` and
    `grid_align` for that reason, so this pins it in the source."""
    src = JS.read_text(encoding="utf-8")
    start = src.index("function boardKey(")
    body = src[start:src.index("\n  }", start)]
    assert "meta.image_px" in body, "boardKey does not cover the recorded size"
    assert "meta.grid_align" in body, "boardKey does not cover the recorded pitch"


# ── the importers record what the renderer needs ──────────────────────────────

def test_art_import_records_the_picture_size_it_already_read():
    """art_import reads the JPEG header to check the filename's dimensions, so
    the size is already in hand; recording it is the difference between an
    alignment the renderer can use and one it has to guess a scale for."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import art_import
    spec = art_import.build_spec(
        "crypt", {"name": "Crypt", "px": (3200, 4500), "cells": (32, 45),
                  "cell_px": 100, "source": "Free - Crypt - 3200x4500 - 32x45 - 100px.jpg"},
        "images/crypt.jpg", "Ekrahir")
    assert spec["image_px"] == [3200, 4500]
    # And the engine agrees with it.
    maps.compile_map(spec)
    assert maps.art_geometry(spec)["cells"] == [32, 45]


def test_atlas_to_map_records_the_background_size(tmp_path, monkeypatch):
    """atlas_to_map computes the cell count from the background's pixels, so the
    size is likewise already in hand. Checked through the real spec builder so
    this cannot drift from what it writes."""
    import atlas_to_map
    png = tmp_path / "bg.png"
    # A 4x6 PNG header is enough: image_size only reads the first 24 bytes.
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
                    + (3200).to_bytes(4, "big") + (4500).to_bytes(4, "big")
                    + b"\x00" * 8)
    scene = {"type": "square", "background": "bg.png", "image": "bg.png",
             "grid": {"type": "square", "size": 100, "offsetX": 0, "offsetY": 0,
                      "unitDistance": 5, "unitType": "feet"}}
    (tmp_path / "s.atlasmap").write_text(json.dumps(scene), encoding="utf-8")
    spec = atlas_to_map.build_map(scene, "crypt", tmp_path / "s.atlasmap")
    assert spec["image_px"] == [3200, 4500]
    assert spec["width"] == 32 and spec["height"] == 45
    assert maps.art_geometry(spec)["leftover"] == [0.0, 0.0]


def test_atlas_to_map_reports_the_pixels_the_grid_does_not_reach(capsys):
    """A scene whose picture does not divide by its cell size used to have the
    remainder stretched into the last square. The renderer now crops it, and
    this is the GM being told rather than finding it at the table."""
    import atlas_to_map
    spec = {"image": "images/crypt.jpg", "image_px": [3250, 4500],
            "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0}}
    atlas_to_map._report_leftover(spec)
    out = capsys.readouterr().out
    assert "50.0px off the right" in out and "0.0px off the bottom" in out


def test_atlas_to_map_says_nothing_when_there_is_nothing_to_crop(capsys):
    import atlas_to_map
    atlas_to_map._report_leftover({"image": "images/crypt.jpg",
                                   "image_px": [3200, 4500],
                                   "grid": {"cell_px": 100}})
    assert capsys.readouterr().out == ""


def test_atlas_to_map_does_not_report_for_a_map_with_no_picture(capsys):
    """A scene with a remote background is written with `image: None`. There is
    no picture and nothing cropped, so it must not claim otherwise."""
    import atlas_to_map
    atlas_to_map._report_leftover({"image": None})
    assert capsys.readouterr().out == ""
