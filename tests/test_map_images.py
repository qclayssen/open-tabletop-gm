"""Map images: a map may carry artwork under the terrain.

BV1. The design constraint, stated in docs/research/atlas-vtt/, is that the engine's
row-string grid must not change: `maps.py` still compiles down to the same
`grid.rows`, so `grid.py`, `sight.py` and every rule keep working untouched. These
tests are mostly about that constraint holding, a map without an image must behave
exactly as it did before, and no existing map may gain a field.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from tactics import maps
from tactics.grid import Grid


@pytest.fixture
def spec():
    return {"name": "Crypt", "width": 8, "height": 6, "base": "floor", "features": []}


# ── the constraint: grid.rows is unchanged ──────────────────────────────────

def test_an_image_does_not_alter_the_grid(spec):
    """The whole point. Terrain and geometry are the engine's, untouched."""
    plain = maps.compile_map(spec)
    with_art = maps.compile_map({**spec, "image": "images/crypt.png"})
    assert plain["grid"] == with_art["grid"]


def test_an_image_does_not_alter_a_terrain_map(spec):
    spec["features"] = [{"type": "water", "x": 1, "y": 1, "w": 3, "h": 2},
                        {"type": "wall", "x": 6, "y": 0, "w": 1, "h": 6}]
    plain = maps.compile_map(spec)
    with_art = maps.compile_map({**spec, "image": "images/crypt.png"})
    assert plain["grid"]["rows"] == with_art["grid"]["rows"]


def test_a_map_without_an_image_has_no_image_key():
    assert "image" not in maps.compile_map({"name": "X", "width": 2, "height": 2})["meta"]


def test_no_pre_existing_map_gained_an_image():
    """Every map that shipped before BV1 must be untouched, or a regression has
    been introduced somewhere nobody was looking.

    `crypt` is excluded because it is the BV1 fixture: it is the one map to carry
    artwork, and it is the one map expected to.
    """
    for name in maps.available():
        if name == "crypt":
            continue
        meta = maps.load(name)["meta"]
        assert "image" not in meta, f"{name} unexpectedly carries an image"


# ── what the image path carries ─────────────────────────────────────────────

def test_image_and_alignment_reach_the_display_meta(spec):
    """The display draws from `meta`, which is an explicit whitelist, so both the
    file and the grid alignment have to be put there deliberately."""
    out = maps.compile_map({**spec, "image": "images/crypt.png", "grid": {
        "cell_px": 70.0, "offset_x": 0.0, "offset_y": 0.0}})["meta"]
    assert out["image"] == "images/crypt.png"
    assert out["grid_align"] == {"cell_px": 70.0, "offset_x": 0.0, "offset_y": 0.0}


def test_alignment_defaults_to_empty_when_unset(spec):
    """A map may carry art with no recorded alignment; the display stretches the
    image to the board, so the alignment is informational, not required."""
    out = maps.compile_map({**spec, "image": "images/crypt.png"})["meta"]
    assert out["grid_align"] == {}


def test_grid_align_is_absent_without_an_image(spec):
    """Alignment alone is meaningless without art, so it does not travel alone."""
    out = maps.compile_map({**spec, "grid": {"cell_px": 70.0}})["meta"]
    assert "grid_align" not in out


def test_the_resulting_grid_still_validates(spec):
    compiled = maps.compile_map({**spec, "image": "images/crypt.png"})
    Grid.from_dict(compiled["grid"])          # raises on anything odd


# ── the Flask route ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def art():
    """A real PNG in display/maps/images/, created by this test.

    The route test needs a file that actually exists, and committing a fixture map
    would put a test artefact in the shipped map list, `maps.available()` is what
    `combat.py` offers a GM. So the image is written here and removed after.
    """
    import struct
    import zlib
    images = ROOT / "display" / "maps" / "images"
    images.mkdir(parents=True, exist_ok=True)
    path = images / "_bv1_test.png"

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    width = height = 8
    raw = b"".join(b"\x00" + bytes([30, 40, 50] * width) for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b""))
    yield path
    path.unlink(missing_ok=True)
    # Leave no empty directory behind in a repo that had none.
    try:
        images.rmdir()
    except OSError:
        pass


@pytest.fixture(scope="module")
def client(art):
    """The display app, imported as a module so its Flask test client is usable.

    gm-display-app.py runs the server on import, so the port is redirected first and
    the SystemExit it raises when it cannot bind is caught. That mirrors how the
    other display tests in this suite load it.
    """
    import importlib.util
    import os
    saved = os.environ.get("GM_DISPLAY_PORT")
    os.environ["GM_DISPLAY_PORT"] = "5099"
    spec_ = importlib.util.spec_from_file_location(
        "gm_app", ROOT / "display" / "gm-display-app.py")
    module = importlib.util.module_from_spec(spec_)
    try:
        spec_.loader.exec_module(module)
    except SystemExit:
        pass
    yield module.app.test_client()
    if saved is None:
        os.environ.pop("GM_DISPLAY_PORT", None)
    else:
        os.environ["GM_DISPLAY_PORT"] = saved


def test_the_maps_route_serves_artwork(client, art):
    """atlas_to_map.py writes an image into display/maps/images/; without this route
    the <image> 404s and the board renders as bare terrain, which is the bug this
    route exists to prevent."""
    response = client.get(f"/maps/images/{art.name}")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"
    assert len(response.data) > 0


def test_the_maps_route_cannot_escape_its_directory(client):
    assert client.get("/maps/../../etc/passwd").status_code in (400, 404)


def test_a_missing_image_is_a_clean_404(client):
    assert client.get("/maps/definitely-not-here.png").status_code == 404
