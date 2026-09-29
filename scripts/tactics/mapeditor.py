"""mapeditor.py: paint terrain into a battle map by clicking, and merge it back.

The GM paints squares, but a map is stored as rectangles painted in order over
a base (see display/maps/README.md). Appending one rectangle per drag leaves a
pile of overlapping shapes nobody can maintain: paint a wall, then water over
half of it, and the file grows forever while describing the same picture.

So the editor keeps the picture, not the strokes. A drag is a *stroke*, a
rectangle replayed onto a matrix of cells, and the file is re-derived from
that matrix as a cover of disjoint rectangles. Painting water over a wall
therefore replaces the wall's rectangle instead of stacking on it, and the
number of rectangles is a function of the picture, not of how many times the
GM clicked. Cells still at the base terrain emit no rectangle at all.

`grid.rows` is untouched by any of this. Every cell is assigned explicitly, so
however the cover is cut, `compile_map` reads the same grid, the constraint
the whole engine rests on (see tests/test_map_images.py).

Two things the picture cannot carry on its own:

* A `label` belongs to a rectangle, not to a square, so it rides along on the
  cells it covered and is re-attached to the first rectangle of its region. A
  region broken into several rectangles gets one label, not several, so
  `meta.labels` does not grow on re-save. It can move by a square or two, and
  the labels come back in map order rather than the order the file listed them
  in, nothing reads that order, but the diff on a re-save is real.
* Strokes are rejected, not clipped, when they run off the map. Silently
  trimming half a drag is how a GM ends up with terrain they did not place.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil

from .maps import MAPS_DIR, compile_map

# How many times apply_strokes() re-derives the cover looking for a fixed
# point. Two is what every map here needs; the cap is there so a pathological
# cell matrix cannot spin.
_COVER_PASSES = 4

# One cell is (terrain name, label or None, label origin). The origin is the
# index of the feature the label came from, so two features that happen to
# share label text ("Reeds" twice on detention-bog) stay two labelled regions
# instead of collapsing into one.
Cell = tuple


def available() -> list:
    """Map names, for the editor's error messages. Same source as combat.py's."""
    return sorted(p.stem for p in MAPS_DIR.glob("*.json"))


def find(name: str):
    """The file a map name refers to, or None.

    Deliberately narrower than maps._find: this module *writes*, so a name has
    to be a slug in MAPS_DIR or a map's own display name, never a path. The
    editor is a browser form; it should not be a way to write a file whose name
    the caller chose.
    """
    slug = (name or "").strip().lower()
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        return None
    direct = MAPS_DIR / f"{slug}.json"
    if direct.is_file():
        return direct
    for cand in MAPS_DIR.glob("*.json"):          # by display name, as _find is
        try:
            if json.loads(cand.read_text(encoding="utf-8")).get("name", "").lower() == slug:
                return cand
        except (OSError, ValueError):
            continue
    return None


def _rect(f: dict) -> tuple:
    # w and h default to 1, per the README. x and y are required, as in
    # compile_map, so a malformed rectangle fails here rather than at index 0.
    return (int(f["x"]), int(f["y"]), int(f.get("w", 1)), int(f.get("h", 1)))


def cells_of(spec: dict) -> list:
    """The map as a matrix of (terrain, label), painted in the file's own order."""
    w, h = int(spec["width"]), int(spec["height"])
    base = spec.get("base", "floor")
    cells = [[(base, None, None)] * w for _ in range(h)]
    for i, f in enumerate(spec.get("features") or []):   # a map may have none at all
        x, y, fw, fh = _rect(f)
        label = f.get("label") or None
        for yy in range(y, y + fh):
            for xx in range(x, x + fw):
                cells[yy][xx] = (f["type"], label, i if label else None)
    return cells


def _grow(cells: list, taken: list, x: int, y: int, key: Cell) -> tuple:
    """The largest rectangle of `key` at (x, y) that is still unclaimed.

    Two candidates, wide-then-tall and tall-then-wide, because either fixed
    order alone cuts an L-shaped region into more pieces than it needs to. Not
    provably minimal, but deterministic and small, which is what a human
    editing the file by hand afterwards needs.
    """
    h = len(cells)
    w = len(cells[0]) if h else 0
    best = (1, 1)
    for across_first in (True, False):
        fw = fh = 1
        if across_first:
            while x + fw < w and cells[y][x + fw] == key and not taken[y][x + fw]:
                fw += 1
            while y + fh < h and all(cells[y + fh][x + i] == key and not taken[y + fh][x + i]
                                     for i in range(fw)):
                fh += 1
        else:
            while y + fh < h and cells[y + fh][x] == key and not taken[y + fh][x]:
                fh += 1
            while x + fw < w and all(cells[y + i][x + fw] == key and not taken[y + i][x + fw]
                                     for i in range(fh)):
                fw += 1
        if fw * fh > best[0] * best[1]:
            best = (fw, fh)
    return best


def cover(cells: list, base: str) -> list:
    """The cell matrix as a cover of disjoint rectangles, the file's features[].

    Document order, and the rectangles never overlap, so the order the engine
    paints them in cannot change the result. Cells still at the base terrain
    with no label are left out: the base already draws them, and a rectangle
    the size of the whole map saying "floor" is not information.
    """
    h = len(cells)
    w = len(cells[0]) if h else 0
    taken = [[False] * w for _ in range(h)]
    out = []
    labelled = set()
    for y in range(h):
        for x in range(w):
            if taken[y][x]:
                continue
            key = cells[y][x]
            if key == (base, None, None):
                taken[y][x] = True
                continue
            fw, fh = _grow(cells, taken, x, y, key)
            for yy in range(y, y + fh):
                for xx in range(x, x + fw):
                    taken[yy][xx] = True
            f = {"type": key[0], "x": x, "y": y, "w": fw, "h": fh}
            if key[1] and key not in labelled:  # one label per region, not per piece
                f["label"] = key[1]
                labelled.add(key)
            out.append(f)
    return out


def _stroke_rect(s, w: int, h: int, i: int) -> tuple:
    # Every failure here is a bad request from the browser, not a wrong type
    # inside this process, so they are all ValueError and all phrased for a GM.
    if not isinstance(s, dict) or "type" not in s:
        raise ValueError(f"stroke {i}: needs a terrain type and an x, y, w, h rectangle")
    try:
        t = s["type"]
        if not isinstance(t, str):
            raise TypeError("terrain type must be a string")
        x, y, fw, fh = _rect(s)
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"stroke {i}: needs a terrain type and an x, y, w, h rectangle")
    if fw < 1 or fh < 1:
        raise ValueError(f"stroke {i} ({t} at {x},{y}) has no area")
    if x < 0 or y < 0 or x + fw > w or y + fh > h:
        raise ValueError(f"stroke {i} ({t} at {x},{y} size {fw}x{fh}) runs off the {w}x{h} map")
    return t, x, y, fw, fh


def apply_strokes(spec: dict, strokes: list) -> dict:
    """spec + a list of {type, x, y, w, h} rectangles -> a new spec.

    Raises ValueError with a message meant to be read by a GM rather than a
    stack trace: an unknown terrain, or a drag off the edge of the map. The
    candidate goes through compile_map before it is returned, so a map this
    function produced is one the engine has already agreed to load.
    """
    w, h = int(spec["width"]), int(spec["height"])
    cells = cells_of(spec)
    for i, s in enumerate(strokes or []):
        t, x, y, fw, fh = _stroke_rect(s, w, h, i)
        for yy in range(y, y + fh):
            for xx in range(x, x + fw):
                cells[yy][xx] = (t, None, None)   # a new feature carries no label
    base = spec.get("base", "floor")
    out = dict(spec)
    out["features"] = cover(cells, base)
    # One pass can leave a labelled region split into a labelled piece and an
    # unlabelled one, and the unlabelled piece may then join a neighbour it was
    # only separated from by that label. Re-deriving from the result converges,
    # and never adds a rectangle, so the file a GM saves is the file a second
    # save would produce, rather than one that needs tidying to be sane.
    for _ in range(_COVER_PASSES):
        again = cover(cells_of(out), base)
        if again == out["features"]:
            break
        out["features"] = again
    compile_map(out)                        # the engine is the final word
    return out


def _terrain(spec: dict) -> dict:
    from .grid import TERRAIN

    known = {k: dict(v) for k, v in TERRAIN.items()}
    for k, v in (spec.get("terrain") or {}).items():
        known[k] = dict(v)
    return known


def palette(spec: dict) -> list:
    """The terrain types a GM can paint with: the built-ins, then the map's own.

    Shaped for display/static/mapseditor.js, which needs the movement cost and
    the cover to label each swatch, the same facts the README's table states.
    """
    cover_text = {0: "no cover", 2: "half cover", 5: "three-quarters cover"}
    out = []
    for name, t in _terrain(spec).items():
        cost = t.get("cost")
        out.append({
            "type": name,
            "color": t.get("color", name),
            "move": "impassable" if cost is None else f"{cost * 5} ft",
            "sight": "blocked" if t.get("blocks_sight") else "clear",
            "cover": cover_text.get(t.get("cover", 0), ""),
        })
    return out


def write(path, spec: dict):
    """Write the map atomically, keeping the original file as <name>.json.bak.

    The editor overwrites a file a GM hand-wrote, so the hand-written version
    has to stay recoverable. Unlike state.py's rolling .bak, this one is kept
    only if none exists: a second save must not replace the original with the
    first save's output.
    """
    path = pathlib.Path(path)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    bak = path.with_suffix(".json.bak")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)
    os.replace(tmp, path)
    return bak if bak.exists() else path


def editor_state(spec: dict, slug: str) -> dict:
    """Everything the editor page draws from, as one JSON blob.

    Cells go out as rows of terrain names rather than the engine's legend
    characters: the page is painting terrain, not reading a grid, and the
    legend is the engine's private encoding.
    """
    cells = cells_of(spec)
    compiled = compile_map(spec)
    return {
        "slug": slug,
        "name": spec.get("name", slug),
        "width": int(spec["width"]),
        "height": int(spec["height"]),
        "base": spec.get("base", "floor"),
        "cells": [[c[0] for c in row] for row in cells],
        "labels": compiled["meta"]["labels"],
        "zones": spec.get("zones", []),
        "image": compiled["meta"].get("image", ""),
        "palette": palette(spec),
        "has_features": bool(spec.get("features")),
    }
