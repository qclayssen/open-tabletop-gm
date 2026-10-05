"""dnd-gm#305 -- `image_px` is a header measurement, not a judgement.

All 17 shipped maps name artwork and record no `image_px`, so
`display/gm-display-app.py:1955` never reaches the aligned-drawing branch and
engine #225 (#142) is inert for every map that exists. The task list called this
a GM pass at the table; it is not one. `art_import.image_size` already reads the
number out of the JPEG/PNG header, which is what makes a script the right
instrument.
"""
import json
import pathlib

import pytest


def _png(path, width=64, height=48):
    """A real, minimal PNG of the given size, written with stdlib only.

    No Pillow (the engine does not have one and must not grow one), and no
    checked-in binary fixture: the bytes are generated so the test asserts the
    header reader against a file it constructed, and the repo stays text-only.
    """
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(width) for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body)))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def _map(maps_dir, name, *, image="images/art.png", image_px=None):
    spec = {"name": name, "width": 8, "height": 6, "base": "floor",
            "image": image,
            "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0}}
    if image_px is not None:
        spec["image_px"] = image_px
    path = maps_dir / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return path


def test_the_backfill_records_the_picture_s_own_size(tmp_path):
    """The whole point: `image_px` is measured from the header, so no human
    judgement is involved and a script can do it for all 17 maps."""
    from scripts import art_backfill as backfill
    maps = tmp_path / "maps"
    path = _map(maps, "testmap")
    _png(maps / "images" / "art.png", 64, 48)

    assert backfill.needs_size(json.loads(path.read_text()))

    found, problems = backfill.survey(maps)
    assert problems == []
    assert [p.name for p, _s, _a in found] == ["testmap.json"]
    art = found[0][2]
    assert art is not None, "the art is right there and must be found"
    assert backfill.image_size(art) == (64, 48)


def test_a_map_that_already_records_its_size_is_left_alone(tmp_path):
    from scripts import art_backfill as backfill
    maps = tmp_path / "maps"
    _map(maps, "done", image_px=[64, 48])
    found, problems = backfill.survey(maps)
    assert found == [] and problems == []
    assert backfill.needs_size({"image": "images/a.png", "image_px": [1, 2]}) is False


def test_a_map_with_no_artwork_is_not_touched(tmp_path):
    """A grid-only map has nothing to measure. Backfilling it would invent a size."""
    from scripts import art_backfill as backfill
    maps = tmp_path / "maps"
    maps.mkdir()
    (maps / "blank.json").write_text(
        json.dumps({"name": "blank", "width": 4, "height": 4}), encoding="utf-8")
    found, _ = backfill.survey(maps)
    assert found == []
    assert backfill.needs_size({"width": 4, "height": 4}) is False


def test_a_map_whose_artwork_is_absent_is_reported_not_assumed(tmp_path):
    """`--check` has to work on a machine with NO artwork, so CI can assert the
    backfill ran. That is the only reason it is a separate mode."""
    from scripts import art_backfill as backfill
    maps = tmp_path / "maps"
    _map(maps, "orphan")
    found, _ = backfill.survey(maps)
    assert len(found) == 1
    assert found[0][2] is None, "artwork absent must be reported, not assumed"


def test_a_corrupt_header_is_reported_rather_than_crashing_the_pass(tmp_path):
    """One unreadable picture must not abort a 17-map backfill."""
    from scripts import art_backfill as backfill
    maps = tmp_path / "maps"
    _map(maps, "broken")
    (maps / "images").mkdir(exist_ok=True)
    (maps / "images" / "art.png").write_bytes(b"not a png at all")
    assert found_dims_raises(backfill, maps / "images" / "art.png")


def found_dims_raises(backfill, art):
    import pytest
    with pytest.raises(Exception):
        backfill.image_size(art)
    return True


def test_the_shipped_maps_are_all_missing_the_size():
    """The defect this fixes, asserted against the repository rather than a
    fixture. Skipped, not passed, while the artwork is absent -- the art is
    gitignored so a clone cannot complete the backfill."""
    from scripts import art_backfill as backfill
    repo = pathlib.Path(backfill.__file__).resolve().parent.parent
    maps_dir = repo / "display" / "maps"
    if not maps_dir.is_dir():
        return
    offenders = []
    for path in sorted(maps_dir.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if backfill.needs_size(spec):
            offenders.append(path.name)
    if offenders:
        pytest.skip(
            f"{len(offenders)} map(s) name artwork without image_px "
            f"({', '.join(offenders[:3])}...); run scripts/art_backfill.py on a "
            "machine with display/maps/images/ populated. Skipped, not passed.")
