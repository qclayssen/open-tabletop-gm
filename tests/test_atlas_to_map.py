"""atlas_to_map.py: Atlas scene -> engine battle map.

The conversion this script does is deliberately narrow. Experiment B found the
data is not in the file, Atlas has no terrain vocabulary (`grep -rniE terrain` over
its src/ returns zero matches), `cover` has no source, and walls are un-snapped line
segments whose faithful rasterization yields zero wall squares on a room map. So the
tests here are about the two things that DO convert, image and grid, and about
refusing everything else loudly rather than guessing.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import struct
import zlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "scripts")
if sys_path not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path)


def _load():
    spec = importlib.util.spec_from_file_location(
        "atlas_to_map", ROOT / "scripts" / "atlas_to_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


atm = _load()


def _png(width: int, height: int) -> bytes:
    raw = b"".join(b"\x00" + bytes([30, 30, 40] * width) for _ in range(height))
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


@pytest.fixture
def scene(tmp_path):
    """A realistic Atlas scene: 1400x980 art, 70px grid, a wall, a token, fog."""
    (tmp_path / "dungeon.png").write_bytes(_png(1400, 980))
    (tmp_path / "scenes").mkdir()
    scene = {
        "schema": "atlas-vtt", "version": 4, "name": "Sunken Crypt",
        "background": "dungeon.png",
        "grid": {"enabled": True, "type": "square", "size": 70, "offsetX": 0,
                 "offsetY": 0, "unitType": "feet", "unitDistance": 5},
        "objects": {
            "tokens": {"t1": {"kind": "character", "name": "Ghoul"}},
            "fog": {"f1": {"type": "brush", "isErasing": False, "points": []}},
            "walls": {"w1": {"type": "solid", "p1": {"x": 0, "y": 0},
                             "p2": {"x": 0, "y": 500}}},
            "pins": {}, "texts": {}, "drawings": {}, "lights": {},
        },
        "camera": {"x": 0, "y": 0, "scale": 1},
    }
    path = tmp_path / "scenes" / "dungeon.atlasmap"
    path.write_text(json.dumps(scene), encoding="utf-8")
    return path


# ── geometry: the part that must be right ──────────────────────────────────

def test_cell_count_follows_image_pixels_and_grid_size(scene):
    """1400x980 at 70px is exactly 20x14 cells. This is the conversion's whole
    reason to exist: Atlas did the hard part (reading the grid off the art)."""
    cell, width, height, feet = atm.grid_geometry(atm.load_scene(scene), scene)
    assert (cell, width, height, feet) == (70.0, 20, 14, 5)


def test_offset_shrinks_the_cell_count(scene):
    """A grid anchored away from the corner leaves fewer cells, and the remainder
    of a partial cell must not become a phantom square."""
    data = atm.load_scene(scene)
    data["grid"]["offsetX"] = 40          # 1400 - 40 = 1360 -> 19 cells, 30px spare
    data["grid"]["offsetY"] = 30          # 980 - 30 = 950 -> 13 cells, 40px spare
    _, width, height, _ = atm.grid_geometry(data, scene)
    assert (width, height) == (19, 13)


def test_offsets_are_carried_through_unchanged(scene):
    """The display needs Atlas's own pixels to line its overlay up with the art."""
    data = atm.load_scene(scene)
    data["grid"]["offsetX"] = 12.5
    data["grid"]["offsetY"] = 7.25
    spec = atm.build_map(data, "crypt", scene)
    assert spec["grid"]["offset_x"] == 12.5
    assert spec["grid"]["offset_y"] == 7.25
    assert spec["grid"]["cell_px"] == 70.0


def test_image_size_reads_a_png_header(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(_png(321, 123))
    assert atm.image_size(path) == (321, 123)


def test_image_size_rejects_a_non_image(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("not an image at all", encoding="utf-8")
    with pytest.raises(ValueError, match="PNG or JPEG"):
        atm.image_size(path)


# ── the refusals: everything Experiment B proved does not convert ─────────

def test_hex_is_refused_by_name(scene):
    """Atlas hex is axial (q, r); the engine's format is a rectangular row-string.
    Converting would silently change movement, distance and diagonals (KC4)."""
    data = atm.load_scene(scene)
    data["grid"]["type"] = "hex-horizontal"
    with pytest.raises(ValueError, match="only 'square' converts"):
        atm.build_map(data, "crypt", scene)


def test_non_five_foot_cells_are_refused(scene):
    """The engine assumes 5 ft squares. A 10 ft scene must not drift in silently."""
    data = atm.load_scene(scene)
    data["grid"]["unitDistance"] = 10
    with pytest.raises(ValueError, match="5 ft squares"):
        atm.build_map(data, "crypt", scene)


def test_missing_unit_distance_defaults_to_five(scene):
    """Atlas defaults unitDistance to 5, which is ours, so an unset value is fine."""
    data = atm.load_scene(scene)
    del data["grid"]["unitDistance"]
    assert atm.grid_geometry(data, scene)[3] == 5


def test_a_scene_with_no_image_is_refused(scene):
    data = atm.load_scene(scene)
    data["background"] = None
    with pytest.raises(ValueError, match="no background image"):
        atm.build_map(data, "crypt", scene)


def test_a_scene_with_no_aligned_grid_is_refused(scene):
    data = atm.load_scene(scene)
    data["grid"]["size"] = 0
    with pytest.raises(ValueError, match="align a grid in Atlas"):
        atm.build_map(data, "crypt", scene)


def test_a_foreign_schema_is_refused(tmp_path):
    path = tmp_path / "x.atlasmap"
    path.write_text(json.dumps({"schema": "something-else", "version": 1}),
                   encoding="utf-8")
    with pytest.raises(ValueError, match="expected schema"):
        atm.load_scene(path)


def test_a_remote_background_is_refused_rather_than_fetched(tmp_path):
    """A map that cannot be opened offline is not a battle map."""
    (tmp_path / "scenes").mkdir()
    path = tmp_path / "scenes" / "s.atlasmap"
    path.write_text(json.dumps({
        "schema": "atlas-vtt", "version": 4,
        "background": "https://example.invalid/map.png",
        "grid": {"type": "square", "size": 70, "offsetX": 0, "offsetY": 0},
    }, indent=1), encoding="utf-8")
    with pytest.raises(ValueError, match="remote or missing"):
        atm.build_map(atm.load_scene(path), "s", path)


# ── what the output must and must not contain ──────────────────────────────

def test_terrain_is_left_empty_rather_than_guessed(scene):
    """The one thing Experiment B proved cannot be derived. Guessing it would
    produce a plausible map with wrong cover, the worst possible failure."""
    spec = atm.build_map(atm.load_scene(scene), "crypt", scene)
    assert spec["features"] == []
    assert spec["base"] == "floor"


def test_objects_are_not_carried_over(scene):
    """Tokens, fog, walls, lights and pins are exactly what does not convert.

    Fog in particular is incommensurable: ours is LOS-derived and observer-dependent,
    Atlas stores a timestamped paint replay log. Importing it would put a stale
    bitmap in front of a fog that recomputes every render.
    """
    spec = atm.build_map(atm.load_scene(scene), "crypt", scene)
    blob = json.dumps(spec)
    for leak in ("Ghoul", "fog", "walls", "brush", "solid", "pins"):
        assert leak not in blob, f"{leak!r} leaked into the map"


def test_the_result_compiles_in_the_real_engine(scene):
    """The check that matters: the engine must accept what we wrote."""
    from tactics.maps import compile_map
    spec = atm.build_map(atm.load_scene(scene), "crypt", scene)
    compiled = compile_map(spec)
    assert len(compiled["grid"]["rows"]) == 14
    assert len(compiled["grid"]["rows"][0]) == 20


def test_a_written_map_loads_by_name(scene, tmp_path, monkeypatch):
    """End to end: file written, image copied, engine reads it back."""
    from tactics import maps
    out = tmp_path / "maps"
    rc = atm.main([str(scene), "--name", "crypt", "--out-dir", str(out)])
    assert rc == 0
    assert (out / "crypt.json").exists()
    assert (out / "images" / "crypt.png").exists()
    spec = json.loads((out / "crypt.json").read_text(encoding="utf-8"))
    assert spec["image"] == "images/crypt.png"
    # compile_map is what load() calls, so this is the real acceptance path
    assert maps.compile_map(spec)["grid"]["name"] == "Crypt"


def test_an_existing_map_is_not_silently_overwritten(scene, tmp_path):
    out = tmp_path / "maps"
    out.mkdir()
    (out / "crypt.json").write_text('{"name": "precious"}', encoding="utf-8")
    assert atm.main([str(scene), "--name", "crypt", "--out-dir", str(out)]) == 1
    assert json.loads((out / "crypt.json").read_text(encoding="utf-8"))["name"] == "precious"


def test_dry_run_writes_nothing(scene, tmp_path):
    out = tmp_path / "maps"
    assert atm.main([str(scene), "--name", "crypt", "--out-dir", str(out),
                     "--dry-run"]) == 0
    assert not out.exists()
