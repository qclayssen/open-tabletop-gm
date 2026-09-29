"""
atlas_to_map.py: turn an Atlas VTT scene into an engine battle map.

Run: python3 scripts/atlas_to_map.py <scene.atlasmap>

Experiment B (docs/research/atlas-vtt/OBSIDIAN-INTEGRATION-DECISION.md) returned a
NO-GO on converting an Atlas scene into engine terrain, and that stands. The obstacle
is categorical: `grep -rniE "terrain"` over Atlas `src/` returns zero matches. Atlas
has no terrain vocabulary, `cover` has no source, and its walls are un-snapped line
segments whose faithful rasterization yields zero wall squares on a room map.

What DOES convert is the part Atlas is genuinely good at and we are not: the picture
and the grid laid over it. Atlas's `GridState` is square/hex, `size` in pixels, with
`offsetX`/`offsetY` and `unitType`/`unitDistance` defaulting to 5 feet per cell,
the same 5 ft square the engine assumes (`grid.py:29`). So a scene's image plus its
grid alignment is exactly the input our map format is missing.

This script takes that and nothing else:

    atlas scene (.atlasmap)  ->  display/maps/<name>.json  +  the background image

It reads `background` and `grid`. It deliberately ignores `objects`, tokens, fog,
pins, drawings, walls, lights, because those are the parts that do not convert. Fog
is discarded outright (incommensurable: ours is LOS-derived, Atlas stores a paint
replay log), and walls are ignored rather than guessed, because guessing them is the
per-map human judgement Experiment B measured.

What you get is a map with a real image, a real grid, and terrain that is all
`floor` until you paint it. The `features` array is left empty with a comment, because
terrain is the part only you can decide.

Usage:
    python3 scripts/atlas_to_map.py ~/atlas-vault/atlas-vtt/scenes/dungeon.atlasmap
    python3 scripts/atlas_to_map.py scene.atlasmap --name crypt --out-dir display/maps
    python3 scripts/atlas_to_map.py scene.atlasmap --dry-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
from pathlib import Path

_MAPS = pathlib.Path(__file__).resolve().parents[1] / "display" / "maps"
if str(_MAPS.parent) not in sys.path:
    sys.path.insert(0, str(_MAPS.parent))

from tactics.maps import compile_map

ATLAS_SCHEMA = "atlas-vtt"

# Our engine is square-only. Atlas hex grids are rejected loudly rather than
# silently converted: axial (q, r) does not fit a rectangular row-string, and
# pretending otherwise changes movement, distance and diagonals. Experiment B
# called this a hard NO (KC4).
SUPPORTED = {"square"}


def load_scene(path: pathlib.Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        scene = json.load(fh)
    if scene.get("schema") != ATLAS_SCHEMA:
        raise ValueError(
            f"{path.name}: expected schema {ATLAS_SCHEMA!r}, got {scene.get('schema')!r}. "
            "Is this an Atlas scene file?")
    return scene


def resolve_background(scene_path: pathlib.Path, background: str | None) -> pathlib.Path | None:
    """Find the background image, which Atlas stores vault-relative."""
    if not background:
        return None
    if background.startswith(("http://", "https://", "data:")):
        return None                       # remote: nothing to copy locally
    candidate = scene_path.parent / background
    if candidate.exists():
        return candidate
    # Atlas writes scenes under its own folder, so walk up to the vault root and
    # retry from there, the path is relative to the vault, not the scene.
    for parent in scene_path.parents:
        trial = parent / background
        if trial.exists():
            return trial
    return None


def grid_geometry(scene: dict, scene_path: Path) -> tuple[int, int, int, int]:
    """(cell_px, width_cells, height_cells, feet_per_cell) from an Atlas grid.

    Atlas's grid is anchored at a pixel offset with a pixel cell size, and the
    image is a known number of pixels wide. Cells are whatever fits after the
    offset. `unitDistance` defaults to 5, matching `SQUARE_FT` in grid.py.
    """
    grid = scene.get("grid") or {}
    size = float(grid.get("size") or 0)
    if size <= 0:
        raise ValueError("scene has no usable grid size; align a grid in Atlas first")

    bg = scene.get("background")
    if not bg:
        raise ValueError(
            "scene has no background image. A battle map here is a picture with a "
            "grid over it, so import the map art into the scene first.")

    # The image's pixel size is the one number we cannot get from the .atlasmap:
    # only the path is stored. Read it from the file itself.
    image_path = resolve_background(scene_path, bg)
    if image_path is None:
        raise ValueError(
            f"background {bg!r} is remote or missing; a local image is required so "
            "the map can be copied next to its JSON")
    px_w, px_h = image_size(image_path)

    off_x = float(grid.get("offsetX") or 0)
    off_y = float(grid.get("offsetY") or 0)
    width = max(1, int((px_w - off_x) // size))
    height = max(1, int((px_h - off_y) // size))
    feet = grid.get("unitDistance") or 5
    return size, width, height, feet


def image_size(path: pathlib.Path) -> tuple[int, int]:
    """Pixel dimensions of a PNG or JPEG, read from the header.

    Pillow is not a dependency of the engine, and the display is a browser, so
    the only thing needed here is the header, which both formats put first.

    The segment walk requires forward progress at every step. A JPEG that ends
    before its frame header used to spin here forever: a zero segment length
    made `seek(-2, 1)` run backwards, and at EOF a seek is a no-op, so the
    cursor never moved. Markers that are not length-prefixed segments (EOI, SOS,
    RSTn) also end the walk, since there is no length to read after them.
    """
    with open(path, "rb") as fh:
        head = fh.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        if head[:2] == b"\xff\xd8":                      # JPEG: walk the segments
            fh.seek(2)
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    break
                if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3):
                    fh.read(3)
                    h = int.from_bytes(fh.read(2), "big")
                    w = int.from_bytes(fh.read(2), "big")
                    return w, h
                if marker[1] in (0xD8, 0xD9, 0xDA) or 0xD0 <= marker[1] <= 0xD7:
                    break                               # not a length-prefixed segment
                raw = fh.read(2)
                if len(raw) < 2:
                    break
                length = int.from_bytes(raw, "big")
                if length < 2:                           # would seek backwards
                    break
                fh.seek(length - 2, 1)
    raise ValueError(f"{path.name}: not a PNG or JPEG, or its header is unreadable")


def build_map(scene: dict, name: str, scene_path: Path) -> dict:
    grid = scene.get("grid") or {}
    grid_type = grid.get("type") or "square"
    if grid_type not in SUPPORTED:
        raise ValueError(
            f"scene grid is {grid_type!r}; only 'square' converts. Atlas hex is axial "
            "(q, r) and does not fit the engine's rectangular row-string, see "
            "OBSIDIAN-INTEGRATION-DECISION.md KC4.")

    cell, width, height, feet = grid_geometry(scene, scene_path)
    if feet != 5:
        raise ValueError(
            f"scene grid is {feet} ft per cell; the engine assumes 5 ft squares "
            "(SQUARE_FT in grid.py). Re-align the grid in Atlas, or change the engine "
            "deliberately, do not let it drift per map.")

    # The image is copied and named in main(), once --out-dir and --image-dir are
    # known; `image` is filled in there.
    spec: dict = {
        "name": name.replace("-", " ").replace("_", " ").title(),
        "width": width,
        "height": height,
        "image": None,
        # Grid alignment, in Atlas's own pixels, carried through unchanged.
        "grid": {
            "cell_px": cell,
            "offset_x": round(float(grid.get("offsetX") or 0), 2),
            "offset_y": round(float(grid.get("offsetY") or 0), 2),
        },
        "base": "floor",
        # Terrain is the part Experiment B proved cannot be derived, so it is left
        # empty and flagged rather than guessed. Add rectangles here, the format
        # is documented in display/maps/README.md.
        "features": [],
    }
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scene", type=pathlib.Path, help="path to a .atlasmap file")
    parser.add_argument("--name", help="map id (default: the scene's file stem)")
    parser.add_argument("--out-dir", type=pathlib.Path, default=_MAPS,
                        help="where to write the map JSON (default: %(default)s)")
    parser.add_argument("--image-dir", type=pathlib.Path, default=None,
                        help="where to copy the background (default: <out-dir>/images)")
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = parser.parse_args(argv)

    scene = load_scene(args.scene)
    name = args.name or args.scene.stem

    spec = build_map(scene, name, args.scene)

    # Validate against the real engine before writing anything, so a bad scene
    # never lands in display/maps/. compile_map is the engine's own path from a
    # map spec to grid.rows, so this checks exactly what will be loaded later.
    compile_map(spec)

    if args.dry_run:
        print(json.dumps({**spec, "features": ["<paint terrain here>"]}, indent=2))
        print(f"\ncell {spec['grid']['cell_px']}px, {spec['width']}x{spec['height']} cells, "
              f"5 ft each, image {scene.get('background') or '(none)'}")
        return 0

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / f"{name}.json"
    if map_path.exists():
        print(f"refusing to overwrite {map_path}; pass --name or delete it", file=sys.stderr)
        return 1

    # Copy the background next to the map, named after the map so a renamed map
    # keeps its image. Remote backgrounds are not fetched: a map that cannot be
    # opened offline is not a battle map.
    source = resolve_background(args.scene, scene.get("background"))
    if source is None:
        if scene.get("background"):
            print("background not found locally; writing the map without an image",
                  file=sys.stderr)
    else:
        image_dir = args.image_dir or out_dir / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        image_name = f"{name}{source.suffix}"
        dest = image_dir / image_name
        if not dest.exists():
            shutil.copy2(source, dest)
        spec["image"] = f"images/{image_name}"

    map_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {map_path}")
    if spec.get("image"):
        print(f"image  {out_dir / spec['image']}")

    check = (
        "python3 -c \"import sys; sys.path.insert(0,'scripts'); "
        f"from tactics import maps; print(maps.load('{name}')['grid']['name'])\""
    )
    print(
        "\nNext: paint terrain into the `features` array. The image and grid are\n"
        "ready; terrain is the part that cannot be derived. See\n"
        "display/maps/README.md for the rectangle format, then check it with:\n"
        f"  {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
