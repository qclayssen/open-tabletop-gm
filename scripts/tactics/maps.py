"""maps.py: load battle maps from display/maps/*.json.

A map file describes terrain as rectangles painted in order over a base
terrain (later rectangles win), which is how the Strixhaven reference page
draws its maps and is easy to write by hand. See display/maps/README.md.

load() compiles the rectangles into the row-string grid the engine uses and
keeps the rest (labels, spawn points, zone lines, info) for the display.

Artwork alignment
-----------------
A map may carry a JPEG under the terrain (`image`) plus the grid's alignment
in that picture's own pixels:

    "image": "images/detention-bog.jpg",
    "image_px": [3200, 4500],
    "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0}

`cell_px` is how many image pixels one 5 ft square spans and the offsets are
where the first square's corner sits in the picture. Together with `image_px`
they are the whole of the alignment, and `display/static/tactics.js` draws the
art at `C / cell_px` so the grid it draws on top is the grid the GM lined up
against the file. Nothing here is the engine's business: `rows` is byte-for-byte
what it was with or without any of these keys, and a map that has none of them
still renders, on the legacy stretch.

Both are validated on the way in and refused rather than passed through, because
the failure they prevent is invisible: an unvalidated `cell_px` used to reach
the display and was never read there, so a map with a typo in it looked correct
and measured wrong at the table.
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


def _find(name: str) -> pathlib.Path:
    p = pathlib.Path(name)
    if p.suffix == ".json" and p.exists():
        return p
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


def _number(value, what: str) -> float:
    """A finite number, or ValueError. `True` is refused: it is an int here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number")
    return number


def _grid_align(spec: dict) -> dict:
    """The map's recorded artwork alignment, validated. See module docstring.

    `grid` is also where a hex map's shape vocabulary will live (SPEC-grid-and-map
    4.2), so an unrecognised key here is carried past rather than refused: this
    function owns the three alignment numbers and nothing else. What it does
    refuse is a *number that is not one*, because that is the case that used to
    pass silently: an unvalidated `cell_px` reached the display as
    `meta.grid_align` and nothing read it, so a typo there was a map that looked
    fine and sat on a grid nobody could line up with.
    """
    align = spec.get("grid", {})
    if align is None:
        return {}
    if not isinstance(align, dict):
        raise ValueError("grid must be an object with cell_px, offset_x and offset_y")
    known = [k for k in ("cell_px", "offset_x", "offset_y") if k in align]
    if not known:
        return {}
    if "cell_px" in align:
        cell = _number(align["cell_px"], "grid.cell_px")
        if cell <= 0:
            raise ValueError(f"grid.cell_px must be greater than 0, not {cell}")
    else:
        cell = None
    out = {}
    if cell is not None:
        out["cell_px"] = cell
    # The offsets default to 0 rather than being required, so a map that is
    # aligned at the top-left of its art does not have to say so twice, and so
    # the renderer never has to ask whether the key is there.
    out["offset_x"] = _number(align.get("offset_x", 0), "grid.offset_x")
    out["offset_y"] = _number(align.get("offset_y", 0), "grid.offset_y")
    return out


def _image_px(spec: dict) -> list:
    """The artwork's own pixel size, validated. See module docstring."""
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
            labels.append({"text": f["label"], "x": x, "y": y})
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
    # `image_px` is what makes the alignment usable. `cell_px` alone is a scale with
    # nothing to scale: the display has to know how big the picture is to draw it at
    # the recorded pitch, and until now it only knew how big the *board* was, so it
    # stretched the art to the board and the grid lines fell wherever the stretch
    # put them. Recorded and validated here, refused when nonsense, and honoured by
    # `tactics.js`'s artAttrs.
    image = spec.get("image")
    if image:
        meta["image"] = image
        align = _grid_align(spec)
        if align:
            meta["grid_align"] = align
        size = _image_px(spec)
        if size:
            meta["image_px"] = size
    elif "image_px" in spec and spec.get("image_px") is not None:
        # A size with no picture is a typo, not a placeholder: there is nothing for
        # it to describe, and silently dropping it would let the same map file be
        # "fixed" twice in two different directions.
        raise ValueError("image_px describes an image, but this map has no image")
    return {"grid": grid, "meta": meta}


def load(name: str) -> dict:
    path = _find(name)
    out = compile_map(json.loads(path.read_text(encoding="utf-8")))
    out["meta"]["slug"] = path.stem
    return out
