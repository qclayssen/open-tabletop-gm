#!/usr/bin/env python3
"""art_import.py: turn a folder of VTT battle-map JPGs into engine maps.

    python3 scripts/art_import.py ~/Downloads/some-folder
    python3 scripts/art_import.py ~/Downloads --dry-run
    python3 scripts/art_import.py ~/Downloads/maps --feet 5

WHY THIS EXISTS
---------------
`scripts/atlas_to_map.py` imports an Atlas scene: it reads a grid the GM has
already aligned by hand, and it deliberately leaves `features` empty because
terrain is the part that cannot be derived from a picture.

Battlemap JPGs from a creator are the other shape. They come as a flat folder of
files whose *names* carry the whole spec, and their grid is already printed into
the art:

    Free - Silverquill Dormitory - First Floor - 3000x4000 - 30x40 - 100px - gridless.jpg
    └ name ─────────────┘                                    │ │    │    └ variant
                                                 pixels wide×tall │    └ px per square
                                                            squares wide×tall

So the grid is derivable here, and this script derives it and writes a real map
file. What it still does NOT do is invent terrain: `features` is left empty and
`base` is `floor`, exactly like the Atlas importer, because "which of these 1200
squares are wall" is per-map human judgement. Paint that in the browser map
editor (`/maps/<name>/edit`) afterwards.

THE GRID IS THE ONE THING THAT MUST BE RIGHT
-------------------------------------------
Every rule the engine owns -- movement, range, cover, diagonals -- is computed
from the 5 ft square grid, not from the picture. If the grid is off by a few
pixels the map looks fine and every distance at the table is subtly wrong. So
this script is deliberately strict, and refuses rather than guesses:

  * the pixel size must divide evenly by the cell size, with no remainder;
  * the square counts in the filename must match the pixel size / cell size;
  * the resulting cell count must be within sane bounds.

`--dry-run` prints what it would write and touches nothing.

ATTRIBUTION
-----------
Creator art carries the creator's name on the image. The map file records
`credit` and `source` so the provenance travels with the map, and a map with a
credit is reported by `--list`. Please keep it: these are free to download, not
free of the artist's claim on the work.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tactics.maps import compile_map

MAPS_DIR = ROOT / "display" / "maps"
IMAGE_DIR = MAPS_DIR / "images"

# Free - Silverquill Dormitory - First Floor - 3000x4000 - 30x40 - 100px - gridless.jpg
#
# Every group but the variant is optional, because creators are inconsistent
# about which ones they include. Named groups, so a reordering of the file's own
# prefixes does not silently swap the dimensions for the square counts.
SPEC = re.compile(
    r"^(?:(?P<free>Free)\s*-\s*)?"
    r"(?P<name>.+?)\s*-\s*"
    r"(?P<px_w>\d+)x(?P<px_h>\d+)\s*-\s*"
    r"(?P<cells_w>\d+)x(?P<cells_h>\d+)\s*-\s*"
    r"(?P<cell_px>\d+)px"
    r"(?:\s*-\s*(?P<variant>grid|gridless))?\.jpg$",
    re.IGNORECASE,
)

# A single square at 100px is 5 ft, which is what the engine assumes
# (SQUARE_FT in grid.py). A map drawn at some other scale is still fine -- the
# cell size is just pixels -- but the feet per cell is what has to be 5.
SQUARE_FT = 5

# A sanity bound, not a display one. The board scrolls rather than shrinking when
# a map is too big (see display/README.md), so a 40x30 tavern is legitimate and
# must import. This only catches a filename misread -- a `4000x3000` read as
# 400x300 squares lands here instead of in the engine.
MAX_CELLS = 10_000


class ImportError_(ValueError):
    """A file whose name does not carry a usable grid spec."""


def image_size(path: pathlib.Path) -> tuple[int, int]:
    """Pixel dimensions from a JPEG or PNG header. No Pillow dependency: the
    engine does not have one and does not need one.

    The segment walk is bounded on purpose. A JPEG that ends before its frame
    header -- truncated, or a stub -- used to spin here forever: a segment
    length of 0 made `seek(-2, 1)` run *backwards*, and at EOF a seek does
    nothing, so the cursor never advanced and the loop never ended. Every step
    now requires forward progress, and any marker that is not a real segment
    (EOI, SOS, a stray 0xFF) ends the walk.
    """
    with open(path, "rb") as fh:
        head = fh.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        if head[:2] == b"\xff\xd8":
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
                # EOI, SOS and 0x01 are not length-prefixed segments. Reaching
                # one without having met a frame header means this is not a
                # usable image, so stop rather than read a length that is not
                # there.
                if marker[1] in (0xD8, 0xD9, 0xDA) or 0xD0 <= marker[1] <= 0xD7:
                    break
                raw = fh.read(2)
                if len(raw) < 2:
                    break
                length = int.from_bytes(raw, "big")
                if length < 2:                    # would seek backwards
                    break
                fh.seek(length - 2, 1)
    raise ImportError_(f"{path.name}: not a JPEG or PNG, or its header is unreadable")


def parse_name(path: pathlib.Path) -> dict:
    """Filename -> {slug, name, px, cells, cell_px, variant}.

    Raises rather than returning a partial: every field here is load-bearing, and
    a map built from a misread dimension is worse than no map.
    """
    m = SPEC.match(path.name)
    if not m:
        raise ImportError_(
            f"{path.name}: name does not carry a grid spec; expected something like "
            f"'... - 3000x4000 - 30x40 - 100px - gridless.jpg'")
    g = m.groupdict()
    px = (int(g["px_w"]), int(g["px_h"]))
    cells = (int(g["cells_w"]), int(g["cells_h"]))
    cell_px = int(g["cell_px"])

    # The three claims about the grid have to agree with each other. A creator's
    # filename can be off by one square, and finding that out here is the point.
    for axis, (p, c) in enumerate(zip(px, cells)):
        if p % cell_px:
            raise ImportError_(
                f"{path.name}: {'width' if axis == 0 else 'height'} {p}px is not a "
                f"multiple of the {cell_px}px cell -- the grid does not land on a "
                f"square boundary")
        if p // cell_px != c:
            raise ImportError_(
                f"{path.name}: {'width' if axis == 0 else 'height'} is {p}px, which is "
                f"{p // cell_px} cells at {cell_px}px, but the name says {c} squares")
    if cells[0] * cells[1] > MAX_CELLS:
        raise ImportError_(
            f"{path.name}: {cells[0]}x{cells[1]} = {cells[0] * cells[1]} squares is "
            f"over the {MAX_CELLS}-square limit; is the filename misread?")

    name = re.sub(r"\s+", " ", g["name"]).strip(" -")
    # Apostrophes are dropped, not treated as word breaks: "Bow's End" is one
    # word, and slugging it to "bow-s-end" puts a hyphen where the name has none.
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")
    if not slug:
        raise ImportError_(f"{path.name}: name {name!r} slugifies to nothing")
    return {"slug": slug, "name": name, "px": px, "cells": cells,
            "cell_px": cell_px, "variant": (g["variant"] or "").lower(),
            "source": path.name}


def build_spec(slug: str, meta: dict, image_rel: str, credit: str) -> dict:
    """The map file. Terrain is left at base floor with no features: a picture
    does not say which squares are wall, and guessing would put the engine's
    rules on top of a guess."""
    w, h = meta["cells"]
    return {
        "name": meta["name"],
        "width": w,
        "height": h,
        "diagonals": "5",
        "info": f"{w * SQUARE_FT} by {h * SQUARE_FT} feet"
                 f" ({w}x{h} squares of {SQUARE_FT} ft). Artwork by {credit}. "
                 f"Terrain is unpainted: open /maps/{slug}/edit and paint walls, "
                f"water and cover before running a fight here.",
        "image": image_rel,
        # The picture's own size, from the header read in main(). Together with
        # cell_px below it is what lets the display draw the art at the recorded
        # pitch instead of stretching it to the board (SPEC-grid-and-map 4.1).
        "image_px": [meta["px"][0], meta["px"][1]],
        "grid": {"cell_px": meta["cell_px"], "offset_x": 0, "offset_y": 0},
        "credit": credit,
        "source": meta["source"],
        "base": "floor",
        "features": [],
        "spawns": [],
    }


def credit_for(name: str) -> str:
    """Creator name from the path or a --credit flag. The art carries the
    watermark; this is how it ends up in the map file."""
    return name


def rel(path: pathlib.Path) -> str:
    """A repo-relative path for printing, or the absolute one when the caller
    pointed --out-dir outside the repo. `relative_to` raises rather than
    degrading, which would crash a perfectly valid --out-dir run."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=pathlib.Path, help="folder of map JPGs")
    ap.add_argument("--gridless", action="store_true",
                    help="only import the gridless variant of each map (default)")
    ap.add_argument("--variant", choices=["gridless", "grid"],
                    help="which variant to import when both are present "
                         "(default: gridless)")
    ap.add_argument("--credit", default=None,
                    help="creator to credit (default: the folder's name)")
    ap.add_argument("--out-dir", type=pathlib.Path, default=MAPS_DIR)
    ap.add_argument("--image-dir", type=pathlib.Path, default=None)
    ap.add_argument("--overwrite", action="store_true",
                    help="replace a map file and image that already exist")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args(argv)

    if not args.folder.is_dir():
        print(f"no such folder: {args.folder}", file=sys.stderr)
        return 2

    want = args.variant or "gridless"
    credit = args.credit or args.folder.name
    image_dir = args.image_dir or (args.out_dir / "images")

    jpgs = sorted(p for p in args.folder.rglob("*.jpg") if not p.name.startswith("."))
    if not jpgs:
        print(f"no .jpg files under {args.folder}", file=sys.stderr)
        return 2

    parsed: dict[str, dict] = {}
    skipped: list[str] = []
    problems: list[str] = []
    for p in jpgs:
        try:
            meta = parse_name(p)
        except ImportError_ as exc:
            problems.append(str(exc))
            continue
        if meta["variant"] == "grid" and want == "gridless":
            skipped.append(f"{p.name} (gridded variant, {want} preferred)")
            continue
        # A map present in both variants lands on the same slug; last one wins
        # unless the variant filtered it out, which is what --variant is for.
        parsed[meta["slug"]] = {**meta, "path": p}

    plans = []
    for slug, meta in sorted(parsed.items()):
        px = image_size(meta["path"])
        if px != meta["px"]:
            problems.append(
                f"{meta['path'].name}: name says {meta['px'][0]}x{meta['px'][1]}px, "
                f"the file is {px[0]}x{px[1]}px")
            continue
        spec = build_spec(slug, meta, f"images/{slug}.jpg", credit)
        try:
            compile_map(spec)                    # the engine's own validation
        except ValueError as exc:
            problems.append(f"{slug}: the engine rejected the map: {exc}")
            continue
        plans.append((slug, meta, spec))

    for note in skipped:
        print(f"skip  {note}")
    for note in problems:
        print(f"ERROR {note}", file=sys.stderr)

    for slug, meta, spec in plans:
        target = args.out_dir / f"{slug}.json"
        img = image_dir / f"{slug}.jpg"
        map_exists = target.exists()
        img_exists = img.exists()

        # The map file and its artwork are committed separately -- the JSON is
        # tracked, images/ is gitignored -- so a clone can hold one without the
        # other. If the map is here and the picture is not, copying the picture
        # is a repair, not an overwrite, and must not need --overwrite: otherwise
        # a pulled map is permanently blank with no obvious way back.
        if map_exists and img_exists and not args.overwrite:
            print(f"skip  {slug}: {target.name} exists (pass --overwrite)")
            continue
        if map_exists and not img_exists:
            verb = "restore art for"
        elif map_exists:
            verb = "overwrite"
        else:
            verb = "write"

        w, h = spec["width"], spec["height"]
        print(f"{verb} {slug}: {w}x{h} squares, {w * SQUARE_FT}x{h * SQUARE_FT} ft, "
              f"{meta['cell_px']}px cell, art {meta['px'][0]}x{meta['px'][1]}")
        if args.dry_run:
            continue
        image_dir.mkdir(parents=True, exist_ok=True)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(meta["path"], img)
        # Only the artwork-only repair leaves the map file alone; a real
        # --overwrite still replaces it, which is what that flag is for.
        if not (map_exists and not img_exists):
            target.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
        else:
            print(f"        {target.name} left as is (artwork restored only)")
        print(f"        {rel(target)}  +  {rel(img)}")

    if args.dry_run:
        print(f"\ndry run: {len(plans)} map(s) would be written, nothing touched")
    elif plans:
        print("\nTerrain is unpainted. Paint each map before a fight:")
        for slug, _, _ in plans:
            print(f"  http://localhost:5001/maps/{slug}/edit")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
