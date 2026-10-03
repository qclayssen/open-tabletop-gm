"""`scripts/art_attach.py`: artwork onto a map that is already playable (#151).

The gap this closes, verified against the code rather than the plan: on `main`,
`art_import.py` has exactly one non-destructive path, and it does not reach the
case that needs it. The write branch reads

    if not (map_exists and not img_exists):
        target.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")

so "the map exists and the image does not" restores the artwork and leaves the
JSON alone, while **every** other case replaces the JSON wholesale. A map that
needs its `grid` or `image_px` recorded -- which, after #142, is what the
renderer needs to draw art at the right pitch -- is in that second case. The only
options today are hand-editing the JSON or `--overwrite`, and `--overwrite`
destroys the `features` a GM has painted.

Every test runs against a `tmp_path` map directory and a `tmp_path` picture. No
test reads or writes `display/maps/`: those are the shared playable maps and a
test that touched one would be a test that could lose a session.

`before fix:` for every case is `ModuleNotFoundError: scripts.art_attach`.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import art_attach as _attach   # noqa: E402
from art_import import ImportError_  # noqa: E402


def _png(width: int, height: int, path: pathlib.Path) -> pathlib.Path:
    """A PNG whose header declares `width` x `height`.

    `art_import.image_size` reads the first 24 bytes and nothing else, so the
    pixels are never decoded. A real image would make these tests depend on an
    encoder and on Pillow being absent; the thing under test is the header read
    plus everything downstream of it.
    """
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
                     + width.to_bytes(4, "big") + height.to_bytes(4, "big")
                     + b"\x00" * 8)
    return path


def _map(directory: pathlib.Path, name: str = "crypt", **over) -> pathlib.Path:
    """A playable map: painted terrain, spawns, labels and all.

    Deliberately not a fresh `art_import` output. The whole point is that the
    things `--overwrite` would destroy are here, so a test that preserved
    `features` on an empty map would pass on a script that dropped them.
    """
    spec = {"name": "The Crypt", "width": 4, "height": 3, "diagonals": "5",
            "info": "20 by 15 feet.",
            "base": "floor",
            "features": [{"type": "wall", "x": 0, "y": 0, "w": 1, "h": 3},
                         {"type": "water", "x": 2, "y": 1, "w": 2, "h": 2,
                          "label": "Pool"}],
            "spawns": [{"type": "monster", "id": "goblin-1", "x": 3, "y": 0}],
            "zones": [{"name": "crypt-floor", "points": [[0, 0], [4, 0], [4, 3]]}],
            "credit": "Someone Else"}
    spec.update(over)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    return path


def run(*argv):
    """Run the CLI. Returns (code, stdout, stderr); `SystemExit` never escapes."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _attach.main([str(a) for a in argv])
    except SystemExit as exc:
        code = exc.code
    return (0 if code is None else code), out.getvalue(), err.getvalue()


@pytest.fixture
def world(tmp_path):
    """A map directory holding one playable map, and a folder of pictures."""
    maps = tmp_path / "maps"
    _map(maps)
    return tmp_path, maps


# ── the operation ─────────────────────────────────────────────────────────────

def test_attach_writes_only_the_artwork_metadata(world):
    """Criterion "artwork metadata only", asserted as a key list rather than by
    spot-checking a few fields: the promise is that everything *else* is
    untouched, so the assertion has to be about the whole key set."""
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    before = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 0, err
    after = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert changed == ["grid", "image", "image_px"], out
    assert after["image_px"] == [400, 300]
    assert after["grid"] == {"cell_px": 100.0, "offset_x": 0.0, "offset_y": 0.0}


def test_a_picture_larger_than_the_board_is_refused_rather_than_resized(world):
    """The mutant that matters most, and the reason `width`/`height` are not
    written: a script that resized the board to the picture would keep
    `features`, keep `spawns` and keep the key set, and pass every preservation
    test above -- while moving every creature one square from where the GM put
    it and every range with it.

    This case exists so that mutant cannot pass: the picture covers **six**
    columns on a four-column map, so a resize would have to change `width` from 4
    to 6, and the only correct answer is to refuse.
    """
    tmp, maps = world
    art = _png(600, 300, tmp / "wide.png")
    before = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 1
    after = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    assert after == before
    assert after["width"] == 4 and after["height"] == 3


def test_the_unit_refuses_a_resize_rather_than_doing_one():
    """Same guarantee at the function the CLI calls, so a caller that uses
    `attach` directly gets it too."""
    spec = {"name": "Crypt", "width": 4, "height": 3, "base": "floor",
            "features": [{"type": "wall", "x": 3, "y": 2, "w": 1, "h": 1}]}
    with pytest.raises(ImportError_):
        _attach.attach(spec, "images/c.png", (600, 300), 100)
    # And the spec it was handed is not the thing that was resized.
    assert spec["width"] == 4
    assert spec["features"][0]["x"] == 3


def test_attach_preserves_terrain_features_and_spawns_exactly(world):
    """Criterion "preserve terrain/features/spawns". Compared whole, not
    field-by-field: a rectangle moved by one square still has a `features` key."""
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    before = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    after = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    assert after["features"] == before["features"]
    assert after["spawns"] == before["spawns"]
    assert after["zones"] == before["zones"]
    assert after["base"] == before["base"]
    assert after["name"] == before["name"]
    assert after["info"] == before["info"]


def test_the_saved_map_still_compiles_and_its_rows_are_unchanged(world):
    """The engine's invariant, and the reason the board size is not rewritten:
    attaching art must not move a single square the rules are computed from."""
    from tactics.maps import compile_map
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    after = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    before_rows = compile_map(json.loads(
        _map(maps / "elsewhere", "crypt2").read_text(encoding="utf-8")))["grid"]["rows"]
    now = compile_map(after)["grid"]
    assert now["rows"] == before_rows
    assert (now["width"] if "width" in now else len(now["rows"][0])) == 4


def test_the_picture_is_copied_next_to_the_maps(world):
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 0, err
    copied = maps / "images" / "crypt.png"
    assert copied.is_file()
    assert copied.read_bytes() == art.read_bytes()
    stored = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    assert stored["image"] == "images/crypt.png"


def test_credit_is_recorded_only_when_given(world):
    """The map already had a credit from its creator. Passing none must not
    blank it -- erasing an attribution to record nothing is worse than not
    writing."""
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    stored = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    assert stored["credit"] == "Someone Else"
    # And a new one is written when the GM names a different creator.
    run("crypt", art, "--cell-px", "100", "--out-dir", maps,
        "--credit", "Ekrahir")
    stored = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    assert stored["credit"] == "Ekrahir"


def test_offsets_are_recorded_and_honoured_by_the_engine(world):
    """An offset is what lets a picture whose squares do not start at its corner
    still line up. Recorded verbatim, and the engine agrees with it."""
    from tactics.maps import art_geometry
    tmp, maps = world
    # 410x330 at 100px with a 10,30 offset is exactly 4x3 squares.
    art = _png(410, 330, tmp / "crypt.png")
    code, out, err = run("crypt", art, "--cell-px", "100", "--offset-x", "10",
                         "--offset-y", "30", "--out-dir", maps)
    assert code == 0, err
    stored = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    assert stored["grid"] == {"cell_px": 100.0, "offset_x": 10.0, "offset_y": 30.0}
    geo = art_geometry(stored)
    assert geo["cells"] == [4, 3] and geo["leftover"] == [0.0, 0.0]


# ── refusals: incompatible input, no writes ──────────────────────────────────

def test_a_picture_that_does_not_fit_the_board_is_refused(world):
    """The important refusal, and the reason this script exists. A 600x300
    picture at 100px covers 6x3 squares on a 4x3 map. Attaching it anyway would
    put the grid somewhere nobody chose, which is precisely the defect #142 fixed
    on the rendering side."""
    tmp, maps = world
    art = _png(600, 300, tmp / "wide.png")
    before = (maps / "crypt.json").read_text(encoding="utf-8")
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 1
    assert "covers 6x3 squares" in err and "4x3" in err
    # No writes at all: not the map, not the image, not even the directory.
    assert (maps / "crypt.json").read_text(encoding="utf-8") == before
    assert not (maps / "images").exists()
    assert sorted(p.name for p in maps.iterdir()) == ["crypt.json"]


def test_a_wrong_cell_size_names_the_one_that_would_fit(world):
    """The refusal has to be actionable. On a 4x3 map a 1000x750 picture fits at
    250px or at 100px-with-a-different-picture, so the message names a cell size
    that works rather than only saying no."""
    tmp, maps = world
    art = _png(1000, 750, tmp / "big.png")
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 1
    assert "250" in err, err
    assert not (maps / "images").exists()


def test_a_picture_that_does_not_divide_is_refused_and_says_so(world):
    """Cropping leftovers is right for a renderer (#142 does it) and wrong for an
    attach: the GM asked for this picture on this map, and quietly dropping a
    strip of it is not what they asked for."""
    tmp, maps = world
    art = _png(450, 300, tmp / "odd.png")
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 1
    assert "does not divide" in err and "50px" in err
    assert not (maps / "images").exists()


@pytest.mark.parametrize("cell", [0, -100])
def test_a_cell_size_that_is_not_one_is_refused(world, cell):
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    code, out, err = run("crypt", art, "--cell-px", str(cell), "--out-dir", maps)
    assert code == 1 and "greater than 0" in err
    assert not (maps / "images").exists()


def test_an_unreadable_picture_is_refused_with_no_writes(world):
    """`image_size` raises for a stub, a truncated header, or a text file with a
    .jpg name. Each used to be a crash or a wrong number."""
    tmp, maps = world
    art = tmp / "broken.png"
    art.write_bytes(b"not an image at all")
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 1
    assert "not a JPEG or PNG" in err or "unreadable" in err
    assert not (maps / "images").exists()


def test_an_unknown_map_lists_the_ones_that_exist(world):
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    code, out, err = run("nope", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 1
    assert "no map" in err and "crypt" in err


def test_a_missing_picture_is_refused(world):
    tmp, maps = world
    code, out, err = run("crypt", tmp / "nope.png", "--cell-px", "100",
                         "--out-dir", maps)
    assert code == 1 and "no such file" in err


def test_a_non_picture_is_refused_by_suffix(world):
    tmp, maps = world
    doc = tmp / "notes.txt"
    doc.write_text("hi", encoding="utf-8")
    code, out, err = run("crypt", doc, "--cell-px", "100", "--out-dir", maps)
    assert code == 1 and "not a picture" in err


def test_a_map_file_that_is_not_a_map_is_refused(world):
    tmp, maps = world
    (maps / "broken.json").write_text("{not json", encoding="utf-8")
    art = _png(400, 300, tmp / "crypt.png")
    code, out, err = run("broken", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 1 and "could not be read" in err


def test_a_refusal_leaves_the_original_intact_after_a_later_success(world):
    """The order matters: refuse, then attach, and the map must still be the one
    it was. A script that wrote first and checked afterwards would pass every
    single-refusal test above and lose the terrain here."""
    tmp, maps = world
    before = (maps / "crypt.json").read_text(encoding="utf-8")
    bad = _png(600, 300, tmp / "wide.png")
    assert run("crypt", bad, "--cell-px", "100", "--out-dir", maps)[0] == 1
    good = _png(400, 300, tmp / "crypt.png")
    assert run("crypt", good, "--cell-px", "100", "--out-dir", maps)[0] == 0
    after = json.loads((maps / "crypt.json").read_text(encoding="utf-8"))
    reference = json.loads(before)
    for key in ("features", "spawns", "zones", "base", "name", "width", "height"):
        assert after[key] == reference[key], key


# ── dry run and backup ───────────────────────────────────────────────────────

def test_dry_run_writes_nothing_and_says_what_would_change(world):
    """Criterion "dry-run/backup". The dry run prints the key list, so a GM can
    check the promise before making it rather than after."""
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    before = (maps / "crypt.json").read_text(encoding="utf-8")
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps,
                         "--dry-run")
    assert code == 0, err
    assert "dry run: nothing written" in out
    assert "change:  grid, image, image_px" in out
    assert "keep:" in out and "features" in out
    assert (maps / "crypt.json").read_text(encoding="utf-8") == before
    assert not (maps / "images").exists()


def test_the_write_keeps_the_original_as_a_backup(world):
    """`mapeditor.write` is the repo's one map-write policy: atomic, and the
    first original kept as .bak and never overwritten. Reused rather than
    reimplemented so there is one policy for the browser editor and this."""
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    original = (maps / "crypt.json").read_text(encoding="utf-8")
    run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    bak = maps / "crypt.json.bak"
    assert bak.is_file()
    assert bak.read_text(encoding="utf-8") == original
    # A second attach does not replace the first original with a saved version.
    run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert bak.read_text(encoding="utf-8") == original


def test_the_report_names_the_terrain_and_spawns_it_is_preserving(world):
    """So the GM is not taking the preservation on trust."""
    tmp, maps = world
    art = _png(400, 300, tmp / "crypt.png")
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps,
                         "--dry-run")
    assert code == 0, err
    assert "2 terrain rectangle(s)" in out
    assert "1 spawn(s) preserved" in out


# ── the unit behind the CLI ───────────────────────────────────────────────────

def test_attach_is_a_pure_function_of_the_spec():
    """The CLI does path handling and reporting; this is the rule, and it works on
    a dict without touching the filesystem, which is what makes the refusals
    testable without a map on disk."""
    spec = {"name": "Crypt", "width": 4, "height": 3, "base": "floor",
            "features": [{"type": "wall", "x": 0, "y": 0, "w": 1, "h": 1}]}
    out = _attach.attach(spec, "images/c.png", (400, 300), 100)
    assert spec == {"name": "Crypt", "width": 4, "height": 3, "base": "floor",
                    "features": [{"type": "wall", "x": 0, "y": 0, "w": 1, "h": 1}]}
    assert out["image"] == "images/c.png" and out["image_px"] == [400, 300]
    assert out["features"] == spec["features"]


@pytest.mark.parametrize("px, cell, offset, message", [
    ((600, 300), 100, (0, 0), "covers 6x3 squares"),      # wrong board size
    ((450, 300), 100, (0, 0), "does not divide"),          # leftovers
    ((50, 300), 100, (0, 0), r"no 100\.0px square fits"),  # smaller than a square
    ((400, 300), 0, (0, 0), "greater than 0"),
])
def test_check_fit_refuses_what_the_cli_refuses(px, cell, offset, message):
    """The unit and the CLI must refuse the same things, for the same reasons, or
    a caller using one gets a different answer from the other. Each case here has
    a CLI test above or below it, on the same input."""
    spec = {"name": "Crypt", "width": 4, "height": 3}
    with pytest.raises(ImportError_, match=message):
        _attach.check_fit(spec, px, cell, *offset)


def test_check_fit_returns_the_geometry_when_it_fits():
    spec = {"name": "Crypt", "width": 4, "height": 3}
    geo = _attach.check_fit(spec, (400, 300), 100)
    assert geo["cells"] == [4, 3] and geo["leftover"] == [0.0, 0.0]


@pytest.mark.parametrize("px, cell, offsets", [
    ((50, 300), 100, (0, 0)),          # narrower than one square
    ((400, 300), 100, (390, 0)),        # an offset that eats the picture
])
def test_check_fit_raises_one_exception_type(px, cell, offsets):
    """The engine's `art_geometry` raises `ValueError`; this function raises
    `ImportError_`. Without the translation a caller has to know that two
    exceptions mean the same thing, and the CLI would be the only place that
    did -- which is how the tiny-picture refusal ends up as a traceback.

    Found by writing a test whose `pytest.raises(ImportError_)` failed with the
    engine's `ValueError` still propagating out.
    """
    spec = {"name": "Crypt", "width": 4, "height": 3}
    with pytest.raises(ImportError_):
        _attach.check_fit(spec, px, cell, *offsets)


def test_a_tiny_picture_is_refused_by_the_cli_too(world):
    """The same input through the CLI, so the translation is exercised end to end
    rather than only in the unit."""
    tmp, maps = world
    art = _png(50, 300, tmp / "sliver.png")
    code, out, err = run("crypt", art, "--cell-px", "100", "--out-dir", maps)
    assert code == 1
    assert "square fits" in err
    assert "Traceback" not in err
    assert not (maps / "images").exists()


def test_the_diff_reports_only_what_moved():
    before = {"name": "Crypt", "features": [], "width": 4}
    after = {**before, "image": "images/c.png", "image_px": [400, 300]}
    assert _attach._diff(before, after) == ["image", "image_px"]
    assert _attach._diff(before, before) == []
