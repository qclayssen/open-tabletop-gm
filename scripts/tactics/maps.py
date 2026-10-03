"""maps.py: load battle maps from display/maps/*.json.

A map file describes terrain as rectangles painted in order over a base
terrain (later rectangles win), which is how the Strixhaven reference page
draws its maps and is easy to write by hand. See display/maps/README.md.

load() compiles the rectangles into the row-string grid the engine uses and
keeps the rest (labels, spawn points, zone lines, info) for the display.

Artwork alignment
-----------------
A map may carry a picture under the terrain plus the grid's alignment in that
picture's own pixels:

    "image": "images/detention-bog.jpg",
    "image_px": [3200, 4500],
    "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0}

`cell_px` is how many image pixels one 5 ft square spans, the offsets are where
the first square's corner sits in the picture, and `image_px` is how big the
picture is. `art_geometry` is the one place those three are turned into a
board, so the renderer, the importers and the editor cannot disagree about it:
`display/static/tactics.js` draws the art at `C / cell_px`, which is the same
pitch it draws the grid lines at, and anything the picture has past its last
square is cropped and reported rather than stretched over the board.

Nothing here is the engine's business: `rows` is byte-for-byte what it was with
or without any of these keys, and a map with no `image_px` still renders, on
the legacy stretch every map written before the size was recorded depends on.

The numbers are validated on the way in and refused rather than passed through,
because the failure they prevent is invisible. An unvalidated `cell_px` used to
reach the display as `meta.grid_align` and was read by nothing at all, so a
declared grid was a number in a file that nothing honoured and a typo in it was
a map that looked right and measured wrong at the table.
"""

from __future__ import annotations

import json
import math
import pathlib
import re

from .grid import DEFAULT_LEGEND, TERRAIN, Grid, label

MAPS_DIR = pathlib.Path(__file__).resolve().parents[2] / "display" / "maps"

# Spare characters for custom terrain types a map defines.
_SPARE = "abcdefghijklmnpqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def available() -> list:
    return sorted(p.stem for p in MAPS_DIR.glob("*.json"))


#: `MAPS_DIR` resolved once. `_find` compares against this rather than the
#: unresolved path so a symlinked checkout cannot make the containment answer
#: depend on where the GM happens to be standing.
_MAPS_ROOT = MAPS_DIR.resolve()


def _owned_by_maps_dir(path: pathlib.Path) -> bool:
    """Is this file one of ours, once symlinks and `..` are followed?

    `resolve()` first, containment second. The order is the whole point: a
    `..` segment, an absolute path and a symlink pointing out of the tree are
    three spellings of the same question, and all three are invisible to a
    string comparison against `MAPS_DIR`.
    """
    try:
        return path.resolve().is_relative_to(_MAPS_ROOT)
    except OSError:                        # a broken symlink, a denied parent
        return False


def _find(name: str) -> pathlib.Path:
    p = pathlib.Path(name)
    if p.suffix == ".json" and p.exists():
        # The convenience branch, and the only one that can leave MAPS_DIR. It
        # takes a path rather than a slug on purpose -- `tactics show
        # display/maps/frog-pond.json` works from a repo-root shell -- so the
        # containment it never had is written here rather than the convenience
        # dropped. Narrower than `mapeditor.find` for the same reason that one is
        # narrow, and deliberately so: this is every map load in the tree, so it
        # is the last place before `json.loads` sees a caller-chosen path.
        if _owned_by_maps_dir(p):
            return p.resolve()
        raise FileNotFoundError(
            f"map {name!r} is not inside {MAPS_DIR}, so it is not a map this "
            f"module loads. Pass a slug, a display name, or a path under "
            f"{MAPS_DIR}. Maps: {', '.join(available())}")
    slug = name.strip().lower().replace(" ", "-")
    if (MAPS_DIR / f"{slug}.json").exists():
        return MAPS_DIR / f"{slug}.json"
    for cand in MAPS_DIR.glob("*.json"):              # match by display name too
        try:
            if json.loads(cand.read_text(encoding="utf-8")).get("name", "").lower() == name.lower():
                return cand
        except ValueError:
            continue
    raise FileNotFoundError(f"no map {name!r}. Maps: {', '.join(available())}")


def _slug(text) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")


# ─── artwork alignment ─────────────────────────────────────────────────────────

# The alignment keys this module owns. `grid` is also where a hex map's shape
# vocabulary will live (SPEC-grid-and-map 4.2), so an unrecognised key here is
# carried past rather than refused: these functions own three alignment numbers
# and nothing else.
ALIGN_KEYS = ("cell_px", "offset_x", "offset_y")


def _number(value, what: str) -> float:
    """A finite number, or ValueError. `True` is refused: it is an int here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number")
    return number


def grid_alignment(spec: dict) -> dict:
    """The map's `grid` block, validated. Always returns a dict.

    Keys this module does not own pass through untouched, so adding hex
    vocabulary to `grid` later does not have to come back here first. What it
    refuses is a *number that is not one*, because that is the case that used
    to pass silently into `meta.grid_align`.

    The offsets default to 0 rather than being required, so a map aligned at
    the top left of its art does not have to say so twice and no caller has to
    ask whether the key is there.
    """
    align = spec.get("grid")
    if align is None:
        return {}
    if not isinstance(align, dict):
        raise ValueError(f"grid must be an object naming {', '.join(ALIGN_KEYS)}")
    out = {k: v for k, v in align.items() if k not in ALIGN_KEYS}
    if "cell_px" in align:
        cell = _number(align["cell_px"], "grid.cell_px")
        if cell <= 0:
            raise ValueError(f"grid.cell_px must be greater than 0, not {cell}")
        out["cell_px"] = cell
    for axis in ("offset_x", "offset_y"):
        out[axis] = _number(align.get(axis, 0), f"grid.{axis}")
    return out


def image_px(spec: dict) -> list:
    """The artwork's own pixel size as `[width, height]`, or `[]` if unrecorded.

    Validated on the way in for the same reason the alignment is: a size that is
    not a size is a number that reaches the renderer and becomes `width="NaN"`.
    """
    size = spec.get("image_px")
    if size is None:
        return []
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise ValueError("image_px is a [width, height] pair")
    out = []
    for value, axis in zip(size, ("width", "height")):
        number = _number(value, f"image_px {axis}")
        if number <= 0:
            raise ValueError(f"image_px {axis} must be greater than 0, not {number}")
        out.append(number)
    return out


def art_geometry(spec: dict) -> dict:
    """Everything the renderer and the importers need to place a picture.

    Returns ``{"cell_px", "offset_x", "offset_y", "image_px", "cells",
    "leftover"}``:

    * `cells` is the `[width, height]` in 5 ft squares the picture covers, which
      is `atlas_to_map.grid_geometry`'s `(px - offset) // cell_px` and nothing
      more complicated;
    * `leftover` is the `[width, height]` in image pixels the picture has past
      its last square. A creator's `3072x4096 - 48x64 - 64px` leaves none; a
      picture whose pixels do not divide leaves some, and the honest response is
      to crop them and say how many there were, because stretching them into the
      last square is what makes a grid drift at the far edge of a long map.

    Raises ValueError rather than returning a partial answer. A board of zero
    squares is not a map, and a caller that got one would draw nothing at all.

    Callable on a spec whose `image` is still None, deliberately:
    `atlas_to_map` computes the geometry before it knows whether it can copy a
    local background. "No image on this map yet" is not a mistake, and it is not
    the same as a size with nothing to describe -- neither importer can write
    `image_px` without also writing `image`, so there is no writer here that can
    produce that state by accident.
    """
    align = grid_alignment(spec)
    size = image_px(spec)
    cell = align.get("cell_px")
    if cell is None:
        raise ValueError("image_px needs grid.cell_px: a picture's size is the scale of "
                         "nothing until the pitch says how many pixels a square spans")
    offsets = [align["offset_x"], align["offset_y"]]
    cells, leftover = [], []
    for axis, (pixels, offset) in enumerate(zip(size, offsets)):
        usable = pixels - offset
        if usable < cell:
            raise ValueError(
                f"image_px {('width', 'height')[axis]} is {pixels}px and grid."
                f"{('offset_x', 'offset_y')[axis]} is {offset}, so no {cell}px square "
                f"fits after the offset")
        count = int(usable // cell)
        cells.append(count)
        leftover.append(usable - count * cell)
    return {"cell_px": cell, "offset_x": offsets[0], "offset_y": offsets[1],
            "image_px": list(size), "cells": cells, "leftover": leftover}


def _landmarks(features: list, owner: list) -> list:
    """Every map feature as an addressable landmark: {name, type, label, squares}.

    `name` is the feature's own "name" (slugged, e.g. "north-door") or, when the
    map does not give one, type plus a per-type counter ("crate-1", "crate-2"),
    so every map written before names existed still gets stable handles. A
    duplicate explicit name gets "-2", "-3". `squares` are the labels of the
    squares the feature still covers after later rectangles painted over it, in
    reading order; a feature fully painted over is dropped (its counter still
    advanced, so the other handles do not shift). Display and GM text only: the
    engine's rules read `rows`, never this."""
    cells = {}
    for y, row in enumerate(owner):
        for x, i in enumerate(row):
            if i is not None:
                cells.setdefault(i, []).append(label((x, y)))
    counts, used, out = {}, set(), []
    for i, f in enumerate(features):
        t = f["type"]
        counts[t] = counts.get(t, 0) + 1
        name = _slug(f.get("name", "")) or f"{_slug(t)}-{counts[t]}"
        base, n = name, 1
        while name in used:
            n += 1
            name = f"{base}-{n}"
        used.add(name)
        if i in cells:
            out.append({"name": name, "type": t, "label": f.get("label", ""),
                        "squares": cells[i], "named": bool(f.get("name") or f.get("label"))})
    return out


def compile_map(spec: dict) -> dict:
    """Map file dict -> {"grid": <Grid dict>, "meta": {...}}. Validates as it goes."""
    w, h = int(spec["width"]), int(spec["height"])
    custom = spec.get("terrain", {})
    engine_terrain = {k: {kk: vv for kk, vv in v.items() if kk != "color"} for k, v in custom.items()}
    char_for = {v: k for k, v in DEFAULT_LEGEND.items()}
    legend = {}
    spare = iter(c for c in _SPARE if c not in DEFAULT_LEGEND)
    for name in custom:
        if name not in char_for:
            ch = next(spare)
            char_for[name] = ch
            legend[ch] = name
    known = set(TERRAIN) | set(custom)

    base = spec.get("base", "floor")
    if base not in known:
        raise ValueError(f"base terrain {base!r} is not a terrain type")
    cells = [[base] * w for _ in range(h)]
    owner = [[None] * w for _ in range(h)]              # which feature painted each square last
    labels = []
    for i, f in enumerate(spec.get("features", [])):
        t = f["type"]
        if t not in known:
            raise ValueError(f"feature {i}: unknown terrain {t!r} (known: {', '.join(sorted(known))})")
        x, y, fw, fh = int(f["x"]), int(f["y"]), int(f.get("w", 1)), int(f.get("h", 1))
        if x < 0 or y < 0 or x + fw > w or y + fh > h:
            raise ValueError(f"feature {i} ({t} at {x},{y} size {fw}x{fh}) runs off the {w}x{h} map")
        for yy in range(y, y + fh):
            for xx in range(x, x + fw):
                cells[yy][xx] = t
                owner[yy][xx] = i
        if f.get("label"):
            # The label's anchor is the CENTRE of the rectangle it names, and it
            # carries the rectangle's size. It used to be the top-left square,
            # with no size at all, which is what put a fixed-size label over the
            # wrong square and gave the display no budget to clamp it to: the
            # display cannot tell how wide a region is from an anchor alone.
            #
            # Display-only, and nothing reads these as a top-left origin:
            # mapeditor.py re-derives a saved map's labels from its `features`
            # rectangles (`cover`), never from this list, so a map file cannot be
            # written back with a centre where it meant a corner. `x`/`y` are
            # floats because an odd-width rectangle has a half-square centre.
            labels.append({"text": f["label"], "x": x + fw / 2, "y": y + fh / 2,
                           "w": fw, "h": fh})
    rows = ["".join(char_for[c] for c in row) for row in cells]
    grid = {"name": spec.get("name", ""), "rows": rows,
            "diagonals": str(spec.get("diagonals", "5"))}
    if legend:
        grid["legend"] = legend
    if engine_terrain:
        grid["terrain"] = engine_terrain
    Grid.from_dict(grid)                                   # fail early on anything odd
    meta = {"name": spec.get("name", ""), "info": spec.get("info", ""),
            "labels": labels,
            "landmarks": _landmarks(spec.get("features", []), owner), "zones": spec.get("zones", []),
            "spawns": spec.get("spawns", []),
            # Opt in to token portraits for a fight on this map. Off by default:
            # a portrait is art the GM has to have chosen, and a map that did not
            # ask should not start putting faces on its monsters. Display-only,
            # and read by sync.portrait_for -- the engine never sees it.
            "portraits": bool(spec.get("portraits", False)),
            "colors": {k: v.get("color", k) for k, v in custom.items()}}
    # A map may carry artwork under the terrain, with the grid aligned to it in the
    # image's own pixels. Both are display-only: the engine reads `rows` and never
    # looks at them, so a map without an image behaves exactly as before.
    #
    # `grid` is validated whether or not there is a picture, because a declared
    # alignment that nothing reads is precisely the defect this closes: it used to
    # be copied into meta verbatim and honoured by no code, so a typo in it was a
    # map that rendered happily and measured wrong. `image_px` is what makes an
    # alignment usable at all -- without the picture's own size there is no scale
    # to apply, which is why a map without it keeps the legacy stretch.
    image = spec.get("image")
    align = grid_alignment(spec)
    size = image_px(spec)
    if image:
        meta["image"] = image
        # Always present when there is a picture, and `{}` when the map declares
        # no grid block: tests/test_map_images.py pins that shape, and a consumer
        # that reads the key should not have to know whether the map had one.
        meta["grid_align"] = align
        if size:
            # Raises if the pitch is missing or the picture is smaller than one
            # square of it, so an unusable alignment never reaches the renderer.
            art_geometry(spec)
            meta["image_px"] = size
    return {"grid": grid, "meta": meta}


def load(name: str) -> dict:
    path = _find(name)
    out = compile_map(json.loads(path.read_text(encoding="utf-8")))
    out["meta"]["slug"] = path.stem
    return out
