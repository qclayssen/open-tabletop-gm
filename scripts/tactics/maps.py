"""maps.py: load battle maps from display/maps/*.json.

A map file describes terrain as rectangles painted in order over a base
terrain (later rectangles win), which is how the Strixhaven reference page
draws its maps and is easy to write by hand. See display/maps/README.md.

load() compiles the rectangles into the row-string grid the engine uses and
keeps the rest (labels, spawn points, zone lines, info) for the display.
"""

from __future__ import annotations

import json
import pathlib

from .grid import DEFAULT_LEGEND, TERRAIN, Grid

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
            "labels": labels, "zones": spec.get("zones", []),
            "spawns": spec.get("spawns", []),
            "colors": {k: v.get("color", k) for k, v in custom.items()}}
    return {"grid": grid, "meta": meta}


def load(name: str) -> dict:
    path = _find(name)
    out = compile_map(json.loads(path.read_text(encoding="utf-8")))
    out["meta"]["slug"] = path.stem
    return out
