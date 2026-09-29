"""art_import.py: filename parsing and the grid guarantees.

The grid is load-bearing. Every distance, cover and diagonal at the table is
computed from the 5 ft square grid, not from the picture, so a map whose grid
is one square out looks fine on screen and plays wrong. Most of these tests are
therefore about refusing: the importer's job is as much to say no as to say yes.

The image tests use real JPEGs written byte-by-byte rather than a stub, because
the header walk is the other half of the input we do not control.
"""
from __future__ import annotations

import json
import struct
import sys

import pytest

from tests.tactics_fixtures import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import art_import
from tactics import maps as engine_maps

# A real, minimal JPEG header: SOI, a SOF0 with the size, then EOI. Enough for
# image_size(), which only walks to the first frame header.
MINIMAL_JPEG_W, MINIMAL_JPEG_H = 3000, 4000


def jpeg_bytes(w: int = MINIMAL_JPEG_W, h: int = MINIMAL_JPEG_H) -> bytes:
    """SOI then a SOF0 frame header. image_size() returns at the SOF."""
    sof = struct.pack(">BHHB", 0xC0, h, w, 3) + b"\x00" * 9
    return b"\xff\xd8\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9"


def png_bytes(w: int, h: int) -> bytes:
    """PNG signature + IHDR, which is all image_size() reads. The real IHDR is 13
    bytes; the header walk only needs the first 8 after the signature."""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr


# ─── filename parsing ─────────────────────────────────────────────────────────

def test_parses_a_creator_filename():
    m = art_import.parse_name(pathlib_name(
        "Free - Silverquill Dormitory - First Floor - 3000x4000 - 30x40 - 100px - gridless.jpg"))
    assert m["name"] == "Silverquill Dormitory - First Floor"
    assert m["slug"] == "silverquill-dormitory-first-floor"
    assert m["px"] == (3000, 4000)
    assert m["cells"] == (30, 40)
    assert m["cell_px"] == 100
    assert m["variant"] == "gridless"


def test_missing_space_before_dimensions_still_parses():
    """One of the two zips writes `Tavern -4000x3000` with no space after the
    dash, so the pattern cannot require one."""
    m = art_import.parse_name(pathlib_name(
        "Free - Bow's End Tavern -4000x3000 - 40x30 - 100px - grid.jpg"))
    assert m["px"] == (4000, 3000)
    assert m["cells"] == (40, 30)


def test_apostrophes_do_not_become_word_breaks():
    m = art_import.parse_name(pathlib_name(
        "Free - Bow's End Tavern - 4000x3000 - 40x30 - 100px - gridless.jpg"))
    assert m["slug"] == "bows-end-tavern"


def test_a_large_map_is_not_a_misread():
    """40x30 is a legitimate tavern. The size guard exists to catch a misparsed
    filename, not to keep big maps out -- the board scrolls."""
    m = art_import.parse_name(pathlib_name(
        "Free - Bow's End Tavern - 4000x3000 - 40x30 - 100px - gridless.jpg"))
    assert m["cells"] == (40, 30)
    assert m["cells"][0] * m["cells"][1] < art_import.MAX_CELLS


# ─── the guards: refuse rather than guess ─────────────────────────────────────

def test_pixels_not_divisible_by_the_cell_is_refused():
    with pytest.raises(art_import.ImportError_, match="not a multiple"):
        art_import.parse_name(pathlib_name(
            "Free - Odd - 3050x4000 - 30x40 - 100px - gridless.jpg"))


def test_square_count_disagreeing_with_pixels_is_refused():
    """A creator's filename can be a square out. Catching it here is the whole
    point: the engine would otherwise compute every distance on a wrong grid."""
    with pytest.raises(art_import.ImportError_, match="squares"):
        art_import.parse_name(pathlib_name(
            "Free - Wrong - 3000x4000 - 32x40 - 100px - gridless.jpg"))


def test_a_misread_filename_trips_the_size_guard():
    with pytest.raises(art_import.ImportError_, match="over the"):
        art_import.parse_name(pathlib_name(
            "Free - Huge - 4000x3000 - 400x300 - 10px - gridless.jpg"))


def test_filename_without_a_grid_spec_is_refused():
    with pytest.raises(art_import.ImportError_, match="grid spec"):
        art_import.parse_name(pathlib_name("holiday dinner table.jpg"))


# ─── image headers ────────────────────────────────────────────────────────────

def test_jpeg_header_is_read(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(jpeg_bytes())
    assert art_import.image_size(p) == (MINIMAL_JPEG_W, MINIMAL_JPEG_H)


def test_png_header_is_read(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(png_bytes(64, 32))
    assert art_import.image_size(p) == (64, 32)


def test_a_non_image_is_refused_not_guessed(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"this is not a jpeg at all")
    with pytest.raises(art_import.ImportError_):
        art_import.image_size(p)


@pytest.mark.parametrize("name,payload", [
    ("truncated", b"\xff\xd8"),
    ("eoi_only", b"\xff\xd8\xff\xd9"),
    ("zero_length_segment", b"\xff\xd8\xff\xfe\x00\x00\xff\xd9"),
    ("d9_repeated", b"\xff\xd8\xff\xd9\xff\xd9\xff\xd9"),
])
def test_a_malformed_jpeg_raises_instead_of_hanging(tmp_path, name, payload):
    """A zero segment length made the header walk seek backwards, and at EOF a
    seek is a no-op -- so a truncated JPEG spun forever instead of failing.
    These would hang the suite rather than fail it."""
    p = tmp_path / f"{name}.jpg"
    p.write_bytes(payload)
    with pytest.raises(art_import.ImportError_):
        art_import.image_size(p)


# ─── the written map ──────────────────────────────────────────────────────────

def test_built_map_compiles_in_the_engine():
    """compile_map is the engine's own path from spec to grid, so this is the
    same check the engine will make when the fight starts."""
    meta = {"name": "Bow's End Tavern", "px": (4000, 3000), "cells": (40, 30),
            "cell_px": 100, "source": "src.jpg"}
    spec = art_import.build_spec("bows-end-tavern", meta, "images/bows-end-tavern.jpg",
                                 "Ekrahir")
    out = engine_maps.compile_map(spec)
    assert out["grid"]["name"] == "Bow's End Tavern"
    assert len(out["grid"]["rows"]) == 30
    assert len(out["grid"]["rows"][0]) == 40
    assert out["meta"]["image"] == "images/bows-end-tavern.jpg"
    assert out["meta"]["grid_align"]["cell_px"] == 100


def test_terrain_is_left_unpainted():
    """The importer must not invent terrain. A picture does not say which
    squares are wall, and the engine's rules would then sit on a guess."""
    meta = {"name": "T", "px": (100, 100), "cells": (1, 1), "cell_px": 100, "source": "s.jpg"}
    spec = art_import.build_spec("t", meta, "images/t.jpg", "X")
    assert spec["features"] == []
    assert spec["base"] == "floor"
    assert "unpainted" in spec["info"]


def test_credit_travels_with_the_map():
    meta = {"name": "T", "px": (100, 100), "cells": (1, 1), "cell_px": 100, "source": "s.jpg"}
    spec = art_import.build_spec("t", meta, "images/t.jpg", "Ekrahir")
    assert spec["credit"] == "Ekrahir"
    assert "Ekrahir" in spec["info"]


# ─── end to end ───────────────────────────────────────────────────────────────

def test_import_writes_a_map_and_an_image(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    art = src / "Free - Bow's End Tavern - 4000x3000 - 40x30 - 100px - gridless.jpg"
    art.write_bytes(jpeg_bytes(4000, 3000))
    # The gridded twin must not be imported alongside it.
    (src / "Free - Bow's End Tavern - 4000x3000 - 40x30 - 100px - grid.jpg").write_bytes(
        jpeg_bytes(4000, 3000))

    out = tmp_path / "maps"
    code = art_import.main([str(src), "--credit", "Ekrahir", "--out-dir", str(out),
                            "--image-dir", str(out / "images")])
    assert code == 0
    spec = json.loads((out / "bows-end-tavern.json").read_text(encoding="utf-8"))
    assert (spec["width"], spec["height"]) == (40, 30)
    assert spec["image"] == "images/bows-end-tavern.jpg"
    assert (out / "images" / "bows-end-tavern.jpg").exists()
    # Exactly one map, not one per variant.
    assert sorted(p.stem for p in out.glob("*.json")) == ["bows-end-tavern"]


def test_dry_run_writes_nothing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Free - Tavern - 400x300 - 4x3 - 100px - gridless.jpg").write_bytes(
        jpeg_bytes(400, 300))
    out = tmp_path / "maps"
    assert art_import.main([str(src), "--out-dir", str(out),
                            "--image-dir", str(out / "images"), "--dry-run"]) == 0
    assert not out.exists()


def test_existing_map_is_not_clobbered(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Free - Tavern - 400x300 - 4x3 - 100px - gridless.jpg").write_bytes(
        jpeg_bytes(400, 300))
    out = tmp_path / "maps"
    args = [str(src), "--out-dir", str(out), "--image-dir", str(out / "images")]
    assert art_import.main(args) == 0
    (out / "tavern.json").write_text('{"name":"mine"}', encoding="utf-8")
    assert art_import.main(args) == 0
    assert json.loads((out / "tavern.json").read_text(encoding="utf-8"))["name"] == "mine"
    assert art_import.main(args + ["--overwrite"]) == 0
    assert json.loads((out / "tavern.json").read_text(encoding="utf-8"))["name"] == "Tavern"


def test_a_pulled_map_gets_its_artwork_back_without_overwrite(tmp_path, capsys):
    """The map JSON is committed and `images/` is gitignored, so a clone can hold
    one without the other. Restoring the missing picture is a repair, and must not
    require --overwrite -- otherwise a pulled map stays blank forever with no
    obvious way to recover it."""
    src = tmp_path / "src"
    src.mkdir()
    art = src / "Free - Tavern - 400x300 - 4x3 - 100px - gridless.jpg"
    art.write_bytes(jpeg_bytes(400, 300))
    out = tmp_path / "maps"
    args = [str(src), "--out-dir", str(out), "--image-dir", str(out / "images")]
    assert art_import.main(args) == 0

    # Simulate the clone: map file kept, artwork gone.
    (out / "images" / "tavern.jpg").unlink()
    capsys.readouterr()
    assert art_import.main(args) == 0
    out_text = capsys.readouterr().out
    assert "restore art for tavern" in out_text
    assert (out / "images" / "tavern.jpg").exists()
    # And the map file was left exactly as it was.
    assert json.loads((out / "tavern.json").read_text(encoding="utf-8"))["name"] == "Tavern"


def test_missing_folder_exits_2(tmp_path):
    assert art_import.main([str(tmp_path / "nope")]) == 2


def test_empty_folder_exits_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert art_import.main([str(empty)]) == 2


def test_a_bad_file_reports_and_exits_1(tmp_path):
    """One unusable file must not stop the good ones importing, but it must not
    pass silently either."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "holiday dinner.jpg").write_bytes(jpeg_bytes(100, 100))
    out = tmp_path / "maps"
    assert art_import.main([str(src), "--out-dir", str(out),
                            "--image-dir", str(out / "images")]) == 1
    assert not list(out.glob("*.json"))


def pathlib_name(name: str):
    import pathlib
    return pathlib.Path(name)
