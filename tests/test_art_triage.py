"""art_triage.py and map_catalog.py: the two ends of a large collection.

art_triage decides what a folder of creator JPGs *is* before importing any of
it. Its value is entirely in the refusals -- a grid that is a square out looks
right on screen and plays wrong at the table -- so most of these tests are about
what it declines and why.

The fixtures are byte-level synthetic JPEGs rather than stubs, with a per-file
tag in an APP0 segment. That matters: an earlier version of the deduplication
keyed on raw file content, and identical headers made four different maps look
byte-identical, so the tests have to produce genuinely distinct files to mean
anything.
"""
from __future__ import annotations

import json
import pathlib
import struct
import sys

import pytest

from tests.tactics_fixtures import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import art_import
import art_triage
import map_catalog


def jpeg(w: int, h: int, tag: bytes = b"x") -> bytes:
    """SOI, an APP0 segment carrying `tag`, then a SOF0 frame header. The tag is
    what makes two fixtures different files rather than the same file twice."""
    sof = struct.pack(">BHHB", 0xC0, h, w, 3) + b"\x00" * 9
    app = b"JFIF\x00" + tag
    return (b"\xff\xd8\xff\xe0" + struct.pack(">H", len(app) + 2) + app
            + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9")


def png(w: int, h: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr


def make_jpeg(w: int, h: int) -> bytes:
    """A JPEG that any real decoder can read.

    Prefers an installed encoder so the bytes are genuinely valid, and falls back
    to a hand-written baseline file when there is none. The fallback is enough for
    sips and for the header walk, which is what the art_import tests need; the
    catalog's own thumbnail step is guarded separately.
    """
    import shutil
    import subprocess
    import tempfile
    for tool, argv in (("sips", ["sips", "-s", "format", "jpeg", "{src}", "--out", "{dest}"]),
                       ("ffmpeg", ["ffmpeg", "-v", "error", "-y", "-i", "{src}",
                                   "-q:v", "4", "{dest}"]),
                       ("convert", ["convert", "{src}", "{dest}"])):
        found = shutil.which(tool)
        if not found:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "in.ppm"
            dest = pathlib.Path(tmp) / "out.jpg"
            # A PPM is trivial to write correctly and every one of these reads it.
            header = f"P6\n{w} {h}\n255\n".encode()
            px = bytearray()
            for y in range(h):
                for x in range(w):
                    px += bytes([(x * 255) // max(w - 1, 1), (y * 255) // max(h - 1, 1), 128])
            src.write_bytes(header + bytes(px))
            args = [a.replace("{src}", str(src)).replace("{dest}", str(dest)) for a in argv]
            try:
                r = subprocess.run([found] + args[1:], capture_output=True,
                                 timeout=60, check=False)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                return dest.read_bytes()
    return decodable_jpeg(w, h, tag=b"fixture")


def decodable_jpeg(w: int, h: int, tag: bytes = b"x") -> bytes:
    """A grayscale baseline JPEG that a real decoder can read.

    A header stub is enough for art_import.image_size (which only walks to the
    SOF) but not for map_catalog, which hands the file to sips to make a
    thumbnail -- and sips exits 0 while writing nothing when it cannot decode
    the image. So this emits a complete minimal file: SOI, JFIF APP0, a
    quantisation table, a Huffman table, a real SOF0, an SOS and the entropy-coded
    data, then EOI. Pillow is not a dependency of the engine and this is only a
    test fixture, so the tables are written by hand.
    """
    qtable = bytes([16] * 64)
    # Standard Annex K luminance Huffman tables.
    dc_bits = bytes([0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0])
    dc_vals = bytes(range(12))
    ac_bits = bytes([0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d])
    ac_vals = bytes([
        0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
        0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08,
        0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0, 0x24, 0x33, 0x62, 0x72,
        0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
        0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45,
        0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
        0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74, 0x75,
        0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
        0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3,
        0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
        0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9,
        0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
        0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4,
        0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa])

    def seg(marker: int, payload: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

    app0 = seg(0xE0, b"JFIF\x00" + bytes([1, 1, 0]) + struct.pack(">HH", 1, 1)
               + bytes([0, 0]))
    dqt = seg(0xDB, bytes([0x00]) + qtable)
    sof0 = seg(0xC0, bytes([8]) + struct.pack(">HH", h, w) + bytes([1, 1, 0x11, 0]))
    dht_dc = seg(0xC4, bytes([0x00]) + dc_bits + dc_vals)
    dht_ac = seg(0xC4, bytes([0x10]) + ac_bits + ac_vals)
    sos = seg(0xDA, bytes([1, 1, 0x00, 0, 63, 0]))
    # Entropy-coded data: one DC diff, then EOB, byte-stuffed so no 0xFF appears.
    scan = bytes([0x00, 0xF0, 0x02])
    return b"\xff\xd8" + app0 + dqt + sof0 + dht_dc + dht_ac + sos + scan + b"\xff\xd9"


@pytest.fixture
def messy(tmp_path):
    """A folder shaped like a real collection: clean maps, a gridded twin, a
    re-upload under a second name, four unusable files, a token sheet, a promo
    and a readme."""
    d = tmp_path / "messy"
    d.mkdir()
    put = lambda n, b: (d / n).write_bytes(b)
    put("Free - Guild Hall - 3000x2000 - 30x20 - 100px - gridless.jpg",
        jpeg(3000, 2000, b"gh-glassless"))
    put("Free - Guild Hall - 3000x2000 - 30x20 - 100px - grid.jpg",
        jpeg(3000, 2000, b"gh-grid"))
    put("Free - Cellar Crypt - 2000x2000 - 20x20 - 100px - gridless.jpg",
        jpeg(2000, 2000, b"cc"))
    put("Free - Cellar Crypt (v2) - 2000x2000 - 20x20 - 100px - gridless.jpg",
        jpeg(2000, 2000, b"cc"))
    put("Free - Bad Grid - 3050x2000 - 30x20 - 100px - gridless.jpg",
        jpeg(3050, 2000, b"bg"))
    put("Free - Lying Name - 2000x2000 - 25x20 - 100px - gridless.jpg",
        jpeg(2000, 2000, b"ln"))
    put("Free - Wrong Pixels - 2000x2000 - 20x20 - 100px - gridless.jpg",
        jpeg(1000, 500, b"wp"))
    put("Free - Untitled Sketch.jpg", jpeg(500, 500, b"us"))
    put("Free - Token Sheet - 2000x2000 - 20x20 - 100px - gridless.jpg",
        jpeg(2000, 2000, b"ts"))
    put("Patreon promo.png", png(800, 400))
    put("readme.txt", b"hello")
    return d


def names(bucket):
    return {e["path"].name for e in bucket}


@pytest.fixture
def artwork():
    """One real map with a real image, installed for the duration of a test.

    `display/maps/images/` is gitignored -- third-party art, installed locally by
    art_import.py -- so on a clean clone no shipped map has artwork at all. A test
    that assumed otherwise would pass on the author's machine and fail in CI, and
    would be testing the developer's ~/Downloads rather than the code.

    Removed afterwards, and the map JSON is restored to its exact prior bytes so a
    dirty tree is never left behind.
    """
    import shutil
    images = art_import.MAPS_DIR / "images"
    images.mkdir(parents=True, exist_ok=True)
    slug = "_artwork_fixture"
    map_path = art_import.MAPS_DIR / f"{slug}.json"
    img_path = images / f"{slug}.jpg"
    had_map = map_path.read_bytes() if map_path.exists() else None
    had_img = img_path.read_bytes() if img_path.exists() else None

    spec = {"name": "Artwork Fixture", "width": 4, "height": 3, "diagonals": "5",
            "info": "fixture", "image": f"images/{slug}.jpg",
            "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0},
            "base": "floor", "features": [], "spawns": []}
    map_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    # A real, decodable JPEG rather than a header stub. map_catalog downsizes the
    # artwork, and every resizer refuses a file it cannot decode -- sips exits 0
    # without writing anything, ffmpeg exits 69 -- so a stub fixture would make the
    # thumbnail assertions depend on the resizer's error handling rather than on
    # the catalog. Written by a real encoder when one is available, so it decodes
    # under ffmpeg and ImageMagick too, not only sips.
    img_path.write_bytes(make_jpeg(400, 300))
    try:
        yield slug
    finally:
        for path, was in ((map_path, had_map), (img_path, had_img)):
            if was is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(was)
        shutil.rmtree(images / ".thumbs", ignore_errors=True)


# ── triage: the decisions ─────────────────────────────────────────────────────

def test_clean_maps_are_found(messy):
    b = art_triage.triage(messy)
    assert names(b["import"]) == {
        "Free - Guild Hall - 3000x2000 - 30x20 - 100px - gridless.jpg",
        "Free - Cellar Crypt - 2000x2000 - 20x20 - 100px - gridless.jpg",
    }


def test_the_gridded_twin_is_a_variant_not_a_second_map(messy):
    b = art_triage.triage(messy)
    assert "Free - Guild Hall - 3000x2000 - 30x20 - 100px - grid.jpg" in names(b["variant"])
    # and it must not also be offered as an import
    assert not any("grid.jpg" in n for n in names(b["import"]))


def test_every_unusable_file_is_refused_with_a_reason(messy):
    b = art_triage.triage(messy)
    refused = {e["path"].name: e["note"] for e in b["refuse"]}
    assert len(refused) == 4
    joined = " ".join(refused.values())
    assert "not a multiple" in joined
    assert "the name says 25 squares" in joined
    assert "file is 1000x500px" in joined
    assert "grid spec" in joined
    for note in refused.values():
        assert note, "a refusal without a reason is not actionable"


def test_sheets_and_promos_are_not_maps(messy):
    b = art_triage.triage(messy)
    assert "Free - Token Sheet - 2000x2000 - 20x20 - 100px - gridless.jpg" in names(b["notamap"])
    assert "Patreon promo.png" in names(b["notamap"])
    assert "readme.txt" in names(b["unknown"])


def test_a_re_upload_collapses_and_keeps_the_plainest_name(messy):
    """`Cellar Crypt (v2)` is the same picture under a second name. Importing
    both would silently overwrite one with the other; letting the version
    suffix win would slug the map to `cellar-crypt-v2`."""
    b = art_triage.triage(messy)
    dups = names(b["duplicate"])
    assert "Free - Cellar Crypt (v2) - 2000x2000 - 20x20 - 100px - gridless.jpg" in dups
    kept = [e["slug"] for e in b["import"] if "Cellar" in e["path"].name]
    assert kept == ["cellar-crypt"]


def test_dedup_does_not_eat_the_gridless_twin(messy):
    """Regression: deduping on the raw file list first made a gridless map and
    its gridded twin one group, and the gridless file was dropped as a
    'duplicate' -- losing the only version the display wants."""
    b = art_triage.triage(messy)
    assert any("Guild Hall" in n and "gridless" in n for n in names(b["import"]))


def test_distinct_maps_are_never_deduplicated(messy):
    b = art_triage.triage(messy)
    assert len(b["import"]) == 2


def test_nothing_is_written_without_import_ok(messy, tmp_path, capsys):
    assert art_triage.main([str(messy), "--credit", "Ekrahir"]) == 0
    out = capsys.readouterr().out
    assert "Nothing written" in out
    assert not list(art_import.MAPS_DIR.glob("guild-hall.json"))


def test_missing_folder_exits_2(tmp_path):
    assert art_triage.main([str(tmp_path / "nope")]) == 2


def test_an_empty_folder_reports_nothing_to_do(tmp_path, capsys):
    d = tmp_path / "empty"
    d.mkdir()
    assert art_triage.main([str(d)]) == 0
    assert "0 file(s)" in capsys.readouterr().out


# ── catalog ───────────────────────────────────────────────────────────────────

def test_catalog_lists_every_shipped_map():
    entries = map_catalog.collect(only_imported=False)
    names = {e["name"] for e in entries}
    for expected in ("frog-pond", "mage-tower", "training-yard", "blank"):
        assert expected in names


def test_catalog_measures_squares_from_the_row_strings():
    """`rows` is a list of strings. Reading a square count off a row as if it
    were a number is the obvious mistake, so it is asserted directly."""
    d = map_catalog.describe("frog-pond")
    assert d["width"] == 20 and d["height"] == 14
    d = map_catalog.describe("mage-tower")
    assert d["width"] == 30 and d["height"] == 12


def test_catalog_renders_and_names_the_maps():
    import html as htmllib
    entries = map_catalog.collect(only_imported=False)
    page = map_catalog.build_html(entries, ROOT / "display" / "maps" / "images" / ".thumbs")
    assert page.startswith("<!doctype html>")
    for e in entries:
        if e.get("title"):
            # Titles are HTML-escaped into the page, so compare escaped.
            assert htmllib.escape(e["title"]) in page, e["title"]
    assert page.count("<figure") == len([e for e in entries if not e.get("error")])


def test_only_imported_filters_to_maps_with_artwork(artwork):
    """`--only-imported` promises maps that have artwork on disk, so this needs
    artwork. `display/maps/images/` is gitignored, which is the normal state of a
    fresh clone -- so the test installs its own rather than assuming a developer's
    machine happens to have some."""
    entries = map_catalog.collect(only_imported=True)
    assert entries
    assert all(e["art"] for e in entries)
    # The artwork must actually be on disk, or the filter is lying.
    assert all(e["art"].exists() for e in entries)


def test_catalog_writes_a_file(tmp_path, artwork):
    """Writes a page with a card per map on any platform.

    The thumbnails themselves depend on an image resizer being installed, and
    sips alone is macOS-only -- so the earlier version of this test asserted
    `data:image/jpeg` and failed on every Linux and Windows CI runner. The page is
    the deliverable; thumbnails are a nicety that degrades to none.
    """
    out = tmp_path / "CATALOG.html"
    assert map_catalog.main(["--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "<figure" in page
    assert "Artwork Fixture" in page
    if map_catalog._resizer() is not None:
        assert "data:image/jpeg;base64," in page
    else:
        # Said out loud rather than silently blank.
        assert "No image resizer found" in page


def test_no_resizer_degrades_instead_of_failing(tmp_path, artwork, monkeypatch):
    """No resizer on this machine is a degraded catalog, not a broken tool. This
    is the Linux and Windows path."""
    monkeypatch.setattr(map_catalog, "_resizer", lambda: None)
    assert map_catalog.downscale(artwork_path(), 100, tmp_path / "t.jpg") is None
    entries = map_catalog.collect(only_imported=True)
    assert entries, "maps with artwork should still be listed"
    page = map_catalog.build_html(entries, tmp_path / "thumbs")
    assert "<figure" in page
    assert "No image resizer found" in page
    assert "artwork present but not resized" in page


def test_a_broken_resizer_is_reported_not_raised(tmp_path, artwork, monkeypatch):
    """A resizer that exists but fails must not take the catalog down with it."""
    monkeypatch.setattr(map_catalog, "_resizer", lambda: ("/nonexistent/tool", ["x"]))
    assert map_catalog.downscale(artwork_path(), 100, tmp_path / "t.jpg") is None


def artwork_path() -> pathlib.Path:
    return art_import.MAPS_DIR / "images" / "_artwork_fixture.jpg"


def test_catalog_says_so_when_artwork_is_missing():
    """A missing image and a genuinely empty map look identical on screen, so
    the catalog says which is which instead of drawing a blank tile."""
    entries = map_catalog.collect(only_imported=False)
    page = map_catalog.build_html(entries, ROOT / "display" / "maps" / "images" / ".thumbs")
    assert "no artwork installed" in page
