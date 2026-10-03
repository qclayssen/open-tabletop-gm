#!/usr/bin/env python3
"""art_attach.py: attach artwork to a map that is already playable.

    python3 scripts/art_attach.py <map-slug> <image> --cell-px 100
    python3 scripts/art_attach.py <map-slug> <image> --cell-px 100 --dry-run

WHY THIS IS A SEPARATE SCRIPT
-----------------------------
`art_import.py` creates a map. This attaches art to one that already exists and
is already in play, and the two have opposite risk profiles: a map `art_import`
writes has no terrain yet, so overwriting it loses nothing, while a map somebody
has fought a session on has terrain, spawns and hand-placed labels that a
`--overwrite` destroys silently. `art_import`'s own repair path only covers the
artwork-missing case ("map exists, image does not -> restore the image, leave
the JSON alone"); the moment the JSON needs changing too -- which is the moment
`image_px` and the grid pitch have to be recorded -- there is no non-destructive
option and the choices are hand-editing or `--overwrite`.

So this is one narrow operation: **write artwork metadata, change nothing else.**

What it writes, and why only that
---------------------------------
Four keys, all about the picture: `image`, `image_px`, `grid`, and `credit`.
Everything else -- `features`, `base`, `spawns`, `zones`, `labels`, `width`,
`height`, `name`, `info` -- is the map the GM has been playing, and it is copied
through untouched.

The one that matters most is `width`/`height`, and they are NOT written even
though they are derived from the picture. A map's board size is what every rule
in the engine is computed from; resizing a map to match a new picture would
silently move every creature's position relative to the terrain. So instead the
picture has to fit the board already, and that is checked:

* `(image_px - offset) / cell_px` must equal the map's `width` x `height`
  exactly, and
* `cell_px` must divide the picture evenly, so no pixels are cropped away.

A picture that does not fit is **refused with no writes at all** -- not resized,
not cropped, not attached. The alternative, attaching it anyway, produces a map
that renders at a scale nobody chose, which is the exact failure #142 fixed on
the display side. Refusing costs the GM one command; attaching costs them a
session.

Safety
------
`--dry-run` prints the diff and touches nothing. A write goes through
`mapeditor.write`, which is atomic and keeps the first original as
`<name>.json.bak` -- the same policy the browser map editor uses, so there is one
write policy in this repo rather than two.

Nothing is read from a campaign directory and nothing outside `display/maps/` is
touched: this is repo tooling for the shared map set, not campaign state.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from art_import import ImportError_, image_size  # noqa: E402
from tactics import mapeditor as _mapeditor        # noqa: E402
from tactics.maps import MAPS_DIR, art_geometry, compile_map  # noqa: E402

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


def _fail(message: str) -> int:
    """A refusal. Printed, and returned rather than raised, so `main` is one
    readable flow and a caller testing this gets a code and a message."""
    print(f"art_attach: {message}", file=sys.stderr)
    return 1


def _cell_sizes_that_divide(px: tuple, limit: int = 6) -> str:
    """Cell sizes that would divide this picture with nothing left over.

    A refusal the GM cannot act on is half a refusal, so the message has to say
    what to type instead. The candidates are the divisors of both axes, largest
    first, capped: an unhelpful list of forty numbers is as bad as none. The first
    entry is normally the one they want, since a creator's art is normally drawn
    at a round pitch.

    Only divisors that leave at least one whole square on both axes are listed --
    a "cell size" larger than the picture is not one.
    """
    def divisors(n: int):
        out = []
        i = 1
        while i * i <= n:
            if n % i == 0:
                out.append(i)
                if i != n // i:
                    out.append(n // i)
            i += 1
        return sorted(out, reverse=True)

    shared = sorted(set(divisors(px[0])) & set(divisors(px[1])), reverse=True)
    usable = [d for d in shared if d <= min(px)][:limit]
    if not usable:
        return f"No cell size divides {px[0]}x{px[1]} exactly, so this picture "\
               f"cannot be attached without cropping it."
    listed = ", ".join(f"--cell-px {d}" for d in usable)
    return f"Cell sizes that divide {px[0]}x{px[1]} exactly: {listed}."


def check_fit(spec: dict, px: tuple, cell_px: float,
              offset_x: float = 0, offset_y: float = 0) -> dict:
    """Does this picture belong on this board? Returns the geometry, or raises.

    Three refusals, in the order that makes the message most useful:

    1. **The picture is smaller than one square** after the offset -- there is
       no board to draw at all.
    2. **The cell size does not divide the picture**, so pixels would be cropped
       away. Cropping is legitimate on its own (#142 crops leftovers), but it is
       not something to do silently to a GM who asked to attach a picture to a
       map they already play, so here it is a refusal naming the remainder.
    3. **The board size does not match**, which is the important one. Either the
       picture is for a different map or `cell_px` is wrong, and in both cases
       attaching would produce a map whose grid sits somewhere nobody chose.

    `ImportError_` rather than a bare ValueError because that is the error
    `art_import` already raises for an unreadable picture and a caller catching
    one type is simpler than two.
    """
    if not px or px[0] <= 0 or px[1] <= 0:
        raise ImportError_(f"the picture has no readable size ({px[0]}x{px[1]}px)")
    if cell_px <= 0:
        raise ImportError_(f"--cell-px must be greater than 0, not {cell_px}")
    probe = {"image_px": [px[0], px[1]],
             "grid": {"cell_px": cell_px, "offset_x": offset_x, "offset_y": offset_y}}
    try:
        geometry = art_geometry(probe)
    except ValueError as exc:
        # `art_geometry` is the engine's and raises its own ValueError. It is
        # re-raised as ImportError_ so this function has ONE exception type for a
        # caller to catch; left as-is it would be a second, and the CLI -- which
        # catches both -- would be the only place that knew. "No square fits after
        # the offset" is also reworded to name the offset, which is the thing a GM
        # typed and the thing the engine cannot see.
        raise ImportError_(str(exc).replace("grid.offset_x", "the x offset")
                           .replace("grid.offset_y", "the y offset")) from exc
    leftover = geometry["leftover"]
    if any(leftover):
        raise ImportError_(
            f"a {cell_px:g}px cell does not divide the picture: "
            f"{leftover[0]:g}px left over on the right and {leftover[1]:g}px at "
            f"the bottom. {_cell_sizes_that_divide(px)}")
    cells = geometry["cells"]
    want = (int(spec["width"]), int(spec["height"]))
    if tuple(cells) != want:
        # The two directions fail for different reasons and want different
        # answers. Too many cells across means the pitch is too small for this
        # board, and the fix is a bigger cell; too few means the pitch is too big
        # for the board, and the fix is a smaller one -- so say which.
        hint = ""
        if cells[0] > want[0] and px[0] % want[0] == 0:
            hint = f" For a {want[0]}x{want[1]} board this picture wants "\
                   f"--cell-px {px[0] // want[0]}" + \
                   (f" (or {px[1] // want[1]} from its height)." if px[1] % want[1] == 0 else ".")
        elif cells[0] < want[0] and want[0] % px[0] == 0 and cells[1] < want[1] and want[1] % px[1] == 0:
            hint = f" For a {want[0]}x{want[1]} board this picture wants "\
                   f"--cell-px {px[0] // want[0]}."
        raise ImportError_(
            f"the picture covers {cells[0]}x{cells[1]} squares at {cell_px:g}px, "
            f"but {spec.get('name', 'this map')} is {want[0]}x{want[1]}. "
            f"Attaching it anyway would put the grid somewhere nobody chose, so "
            f"nothing was written. Check --cell-px, or attach the picture that "
            f"belongs to this map.{hint}")
    return geometry


def attach(spec: dict, image_rel: str, px: tuple, cell_px: float,
           offset_x: float = 0, offset_y: float = 0, credit: str = "") -> dict:
    """The map with artwork metadata on it and everything else untouched.

    Works on a copy and only assigns four keys, so a caller cannot accidentally
    hand in a spec and get back one with the terrain re-derived: the terrain
    rectangles, spawns and zones are the same objects, untouched. `compile_map`
    runs on the result, so what this returns is a map the engine has agreed to
    load -- the same gate `art_import` and the browser editor both use.
    """
    geometry = check_fit(spec, px, cell_px, offset_x, offset_y)
    out = dict(spec)
    out["image"] = image_rel
    out["image_px"] = [geometry["image_px"][0], geometry["image_px"][1]]
    out["grid"] = {"cell_px": cell_px, "offset_x": offset_x, "offset_y": offset_y}
    if credit:
        out["credit"] = credit
    compile_map(out)
    return out


def _diff(before: dict, after: dict) -> list:
    """The keys that actually changed, for `--dry-run` and for the confirmation.

    Reported as a key list rather than a text diff: this file's whole promise is
    that nothing but the artwork moved, and a key list is that promise in a form
    a GM can check by eye against the four names above.
    """
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="art_attach.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map", help="map slug, as listed by the display")
    ap.add_argument("image", type=pathlib.Path, help="the picture to attach")
    ap.add_argument("--cell-px", type=float, required=True,
                    help="how many picture pixels one 5 ft square spans")
    ap.add_argument("--offset-x", type=float, default=0.0,
                    help="where the first square starts across (default 0)")
    ap.add_argument("--offset-y", type=float, default=0.0,
                    help="where the first square starts down (default 0)")
    ap.add_argument("--credit", default=None, help="creator to credit")
    ap.add_argument("--out-dir", type=pathlib.Path, default=MAPS_DIR,
                    help="where the map files are (default: display/maps/)")
    ap.add_argument("--image-dir", type=pathlib.Path, default=None,
                    help="where to copy the picture (default: <out-dir>/images)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change, write nothing")
    args = ap.parse_args(argv)

    out_dir = pathlib.Path(args.out_dir)
    target = out_dir / f"{args.map.strip().lower()}.json"
    if not target.is_file():
        known = ", ".join(sorted(p.stem for p in out_dir.glob("*.json"))) or "(none)"
        return _fail(f"no map {args.map!r}. Maps: {known}")
    source = pathlib.Path(args.image)
    if not source.is_file():
        return _fail(f"no such file: {source}")
    if source.suffix.lower() not in IMAGE_SUFFIXES:
        return _fail(f"{source.suffix or source.name} is not a picture this can read "
                     f"({', '.join(IMAGE_SUFFIXES)})")

    try:
        before = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _fail(f"{target.name} could not be read: {exc}")
    if not isinstance(before, dict):
        return _fail(f"{target.name} is not a map object")

    try:
        px = image_size(source)
    except ImportError_ as exc:
        return _fail(str(exc))

    image_dir = pathlib.Path(args.image_dir) if args.image_dir else out_dir / "images"
    dest = image_dir / f"{target.stem}{source.suffix.lower()}"
    image_rel = f"images/{dest.name}"

    try:
        after = attach(before, image_rel, px, args.cell_px,
                       args.offset_x, args.offset_y, args.credit or "")
    except (ImportError_, ValueError) as exc:
        return _fail(str(exc))

    changed = _diff(before, after)
    kept = sorted(k for k in before if k not in changed)
    print(f"art_attach: {target.stem} ({before.get('name', target.stem)}) "
          f"{before['width']}x{before['height']} squares, picture {px[0]}x{px[1]}px "
          f"at {args.cell_px:g}px")
    print(f"  change:  {', '.join(changed)}")
    print(f"  keep:    {', '.join(kept) if kept else '(nothing else)'}")
    terrain = len(before.get("features") or [])
    if terrain:
        print(f"  {terrain} terrain rectangle(s) and "
              f"{len(before.get('spawns') or [])} spawn(s) preserved")

    if args.dry_run:
        print("  dry run: nothing written")
        return 0

    image_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    bak = _mapeditor.write(target, after)
    print(f"  wrote {target.name}"
          + (f" (original kept as {bak.name})" if bak != target else ""))
    print(f"  image {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
