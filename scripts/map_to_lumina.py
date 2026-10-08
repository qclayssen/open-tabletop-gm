"""
map_to_lumina.py: export an engine battle map as a Lumina HD-2D level file.

Run: python3 scripts/map_to_lumina.py <map> [<map> ...] [--out DIR]

Strictly one-way, like its siblings `map_to_atlas.py` and
`map_to_chartdown.py`: it writes files and never reads one back, so the
engine stays the only authority for a fight.

    display/maps/<name>.json  ->  <out>/<name>.json  (format: lumina-level, v1)

The map is read the way the engine reads it: `tactics/maps.py`
`compile_map()` turns the painted rectangles into the row-string grid, and
this script emits from that grid, not from the raw rectangles.

Terrain mapping (explicit table TILE_FOR, lossy in looks, faithful in
meaning). A terrain name not in the table is REFUSED, never guessed; the map
is not written and the error names the terrain and its square count.
`--terrain NAME=TILE` supplies a tile for a map's own custom terrain
(e.g. finish=c):

    floor     -> g  Grass (walkable)
    difficult -> d  Dirt (walkable)
    water     -> o  Still pond (blocked water)
    wall      -> x  Rock, blocked (raised one level)
    void      -> ' ' Void
    hazard    -> s  Sand (walkable; the hazard rule is NOT carried)
    feature   -> c  Cobblestone (walkable; cover is NOT carried)

Heights: flat level 2 everywhere, water beds at 0, walls at 3. Enough relief
for the diorama; real terracing is editor work.

WHAT THIS CANNOT CARRY (each printed as LOSS on every run):

  * rules: movement cost, cover, sight, swim, hazards, diagonals. A tile keeps
    its own Lumina meaning, not the engine's numbers.
  * artwork: `image` / `grid` alignment / `image_px` are dropped (Lumina bakes
    its own look from tiles).
  * encounters: HP, AC, conditions, initiative. Spawns become a player spawn
    plus static NPC markers (position and name only).

Usage:
    python3 scripts/map_to_lumina.py frog-pond --terrain finish=c
    python3 scripts/map_to_lumina.py mage-tower --out ~/lumina-levels
    python3 scripts/map_to_lumina.py mage-tower --dry-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MAPS = _ROOT / "display" / "maps"
_SCRIPTS = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from tactics.grid import TERRAIN  # noqa: E402
from tactics.maps import compile_map  # noqa: E402

LEVEL_FORMAT = "lumina-level"
LEVEL_VERSION = 1
MIN_SIZE = 8
MAX_SIZE = 128
BASE_LEVEL = 2

# Engine terrain name -> Lumina tile char (see module docstring for why).
TILE_FOR = {
    "floor": "g",
    "difficult": "d",
    "water": "o",
    "wall": "x",
    "void": " ",
    "hazard": "s",
    "feature": "c",
}

# Tile char -> Lumina legend entry (from src/engine/level/LevelFormat.js
# TILE_TYPES, trimmed to what this exporter can emit).
LEGEND = {
    "g": {"top": "grass", "side": "cliff", "lip": "grass_side", "walkable": True},
    "d": {"top": "dirt", "side": "dirt_side", "walkable": True},
    "c": {"top": "cobblestone", "side": "stone_wall", "walkable": True},
    ".": {"top": "dirt_path", "side": "dirt_side", "walkable": True},
    "s": {"top": "sand", "side": "dirt_side", "walkable": True},
    "o": {"top": "riverbed", "side": "cliff", "water": True, "walkable": False, "flow": 0},
    "~": {"top": "riverbed", "side": "cliff", "water": True, "walkable": False},
    "x": {"top": "moss_stone", "side": "cliff", "walkable": False},
    "T": {"top": "grass_dark", "side": "cliff", "lip": "grass_side", "walkable": False},
    " ": {"void": True},
}

DEFAULT_WATER = {"flow": [0, 0.45], "reflect": 0.2, "neutral": 0.2}
DEFAULT_ENV = {"timeOfDay": 16.8, "clock": True, "weather": "clear",
               "border": "none", "outerScenery": False, "godRays": True,
               "dust": True, "music": True, "camera": None, "highGround": None}


class Refused(ValueError):
    """A map that cannot be exported faithfully. Nothing is written for it."""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def parse_terrain_args(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items or []:
        name, sep, tile = item.partition("=")
        name, tile = name.strip(), tile.strip()
        if not sep or not name or len(tile) != 1 or tile not in LEGEND:
            raise ValueError(
                f"--terrain {item!r}: expected NAME=TILE, TILE one of "
                f"{''.join(sorted(LEGEND))!r} (e.g. finish=c)")
        out[name] = tile
    return out


def _level_char(n: int) -> str:
    n = max(0, min(35, round(n)))
    return chr(48 + n) if n < 10 else chr(87 + n)


def plan_tiles(compiled: dict, custom: dict[str, str]) -> tuple[dict[str, set], list[str]]:
    """Every square's terrain name -> Lumina tile char, or refuse.

    Returns ({tile: set(cells)}, notes).
    """
    grid = compiled["grid"]
    legend = {"#": "wall", ".": "floor", ",": "difficult", "~": "water",
              "^": "hazard", "o": "feature", "_": "void"}
    legend.update(grid.get("legend", {}))
    custom_defs = grid.get("terrain", {})

    counts: dict[str, int] = {}
    by_name: dict[str, set] = {}
    for y, row in enumerate(grid["rows"]):
        for x, ch in enumerate(row):
            name = legend[ch]
            counts[name] = counts.get(name, 0) + 1
            by_name.setdefault(name, set()).add((x, y))

    problems, notes = [], []
    placed: dict[str, set] = {}
    for name in sorted(by_name):
        if name in TERRAIN and name in custom_defs and custom_defs[name] != TERRAIN[name]:
            if name not in custom:
                problems.append(
                    f"{name!r} ({counts[name]} squares) is redefined by this map, "
                    "so the stock tile would misstate it")
                continue
        if name in custom:
            tile = custom[name]
            notes.append(f"terrain {name!r} -> tile {tile!r} by --terrain (not the engine's numbers)")
        elif name in TILE_FOR:
            tile = TILE_FOR[name]
        else:
            problems.append(f"terrain {name!r} ({counts[name]} squares) has no Lumina tile")
            continue
        placed.setdefault(tile, set()).update(by_name[name])
    if problems:
        raise Refused("; ".join(problems) + ". Nothing written. Map each with "
                      "--terrain NAME=TILE (e.g. finish=c).")
    return placed, notes


def build_level(spec: dict, map_id: str,
                custom: dict[str, str] | None = None) -> tuple[dict, list[str], dict]:
    """(level dict, loss lines, stats). Raises Refused. Pure: touches no files."""
    compiled = compile_map(spec)
    grid, meta = compiled["grid"], compiled["meta"]
    width, height = len(grid["rows"][0]), len(grid["rows"])
    if width > MAX_SIZE or height > MAX_SIZE:
        raise Refused(f"{width}x{height} squares exceeds Lumina max {MAX_SIZE}; nothing written.")
    placed, notes = plan_tiles(compiled, custom or {})

    # Paint tiles row-major from the resolved sets.
    tile_at = {}
    for tile, cells in placed.items():
        for c in cells:
            tile_at[c] = tile
    tiles = ["".join(tile_at[(x, y)] for x in range(width)) for y in range(height)]
    heights = []
    for y in range(height):
        row = ""
        for x in range(width):
            t = tile_at[(x, y)]
            row += _level_char(0 if t in ("o", "~") else 3 if t == "x" else BASE_LEVEL)
        heights.append(row)

    # Pad up to Lumina MIN_SIZE with blocked forest border tiles.
    pad_w, pad_h = max(0, MIN_SIZE - width), max(0, MIN_SIZE - height)
    if pad_w or pad_h:
        width += pad_w
        height += pad_h
        tiles = ["T" * width] + [("T" * ((pad_w + 1) // 2) + r + ("T" * (pad_w // 2)))[:width]
                                   for r in tiles]
        # keep rectangular if pad_w is odd on one side only
        tiles = [r.ljust(width, "T")[:width] for r in tiles] + ["T" * width] * (pad_h - 1)
        heights = [_level_char(BASE_LEVEL) * width] + \
            [(("2" * ((pad_w + 1) // 2) + r + ("2" * (pad_w // 2)))[:width]).ljust(width, "2")[:width]
             for r in heights] + [_level_char(BASE_LEVEL) * width] * (pad_h - 1)
        notes.append(f"padded to Lumina minimum {width}x{height} with blocked border")

    spawns = meta.get("spawns") or []
    if spawns:
        s0 = spawns[0]
        spawn = {"x": int(s0["x"]) + 0.5, "z": int(s0["y"]) + 0.5, "facing": "down"}
    else:
        spawn = {"x": width / 2, "z": height / 2, "facing": "down"}

    objects = []
    for i, s in enumerate(spawns[1:], 1):
        if "frog" in str(s.get("name") or s.get("id") or "").lower():
            continue  # frogs group below as one critters object
        objects.append({"id": f"npc_{i}",
                        "type": "npc",
                        "x": int(s["x"]) + 0.5, "z": int(s["y"]) + 0.5,
                        "name": str(s.get("name") or s.get("id") or f"spawn {i}"),
                        "preset": "villager", "facing": "down",
                        "wander": 0, "speed": 0.8, "behaviour": "static",
                        "dialogue": [str(s.get("name") or "A traveller.")],
                        "opts": {}})
    frogs = [s for s in spawns[1:]
             if "frog" in str(s.get("name") or s.get("id") or "").lower()]
    if frogs:
        fx = sum(int(s["x"]) for s in frogs) / len(frogs)
        fz = sum(int(s["y"]) for s in frogs) / len(frogs)
        objects.append({"id": "critters_1", "type": "critters",
                        "x": round(fx) + 0.5, "z": round(fz) + 0.5,
                        "kind": "frog", "count": len(frogs), "radius": 2.5})
    for i, lab in enumerate(meta.get("labels") or []):
        objects.append({"id": f"signpost_{i + 1}", "type": "signpost",
                        "x": round(float(lab["x"]), 2), "z": round(float(lab["y"]), 2),
                        "rotation": 0, "text": [str(lab["text"])], "opts": {"boards": 1}})
    for i, z in enumerate(meta.get("zones") or []):
        try:
            objects.append({"id": f"region_{i + 1}", "type": "region",
                            "minX": float(z["x0"]) if "x0" in z else 0,
                            "maxX": float(z["x1"]) if "x1" in z else width,
                            "minZ": float(z["y0"]) if "y0" in z else 0,
                            "maxZ": float(z["y1"]) if "y1" in z else height,
                            "name": str(z.get("label") or z.get("name") or f"zone {i + 1}")})
        except (TypeError, ValueError):
            notes.append(f"zone {i} unreadable, skipped")

    used = sorted({ch for row in tiles for ch in row})
    # A spawn that lands on water/void/blocked is normal in the engine (water
    # is swimmable, not blocked) but unplayable in Lumina. Stamp a 1-tile dirt
    # pad under each stranded marker rather than shipping NPCs on water.
    pads = 0
    for o in objects:
        if o["type"] not in ("npc", "signpost", "critters"):
            continue
        ox, oz = int(o["x"]), int(o["z"])
        if 0 <= ox < width and 0 <= oz < height and not LEGEND[tiles[oz][ox]].get("walkable"):
            tiles[oz] = tiles[oz][:ox] + "d" + tiles[oz][ox + 1 :]
            heights[oz] = heights[oz][:ox] + _level_char(BASE_LEVEL) + heights[oz][ox + 1 :]
            pads += 1
    if pads:
        notes.append(f"{pads} dirt pad(s) stamped under markers that stood on water/blocked")
        used = sorted({ch for row in tiles for ch in row})
    level = {"format": LEVEL_FORMAT, "version": LEVEL_VERSION,
             "name": " ".join(str(spec.get("name") or map_id).split()) or map_id,
             "subtitle": "", "author": "dnd-gm map_to_lumina",
             "description": " ".join(str(meta.get("info") or "").split()),
             "width": width, "depth": height,
             "legend": {ch: LEGEND[ch] for ch in used},
             "tiles": tiles, "heights": heights,
             "waterLevel": 0.4, "water": dict(DEFAULT_WATER),
             "environment": dict(DEFAULT_ENV),
             "spawn": spawn, "objects": objects,
             "source": {"tool": "map_to_lumina", "map": map_id,
                        "diagonals": str(grid.get("diagonals", "5"))}}
    # Keep the spawn walkable: if it landed on water/void/blocked, nudge to the
    # nearest walkable square rather than shipping an unplayable level.
    sx, sz = int(min(max(spawn["x"], 0), width - 1)), int(min(max(spawn["z"], 0), height - 1))
    if not LEGEND[tiles[sz][sx]].get("walkable"):
        best = None
        for y in range(height):
            for x in range(width):
                if LEGEND[tiles[y][x]].get("walkable"):
                    d = abs(x - sx) + abs(y - sz)
                    if best is None or d < best[0]:
                        best = (d, x, y)
        if best is None:
            raise Refused("no walkable square for the spawn; nothing written.")
        _, bx, by = best
        level["spawn"] = {"x": bx + 0.5, "z": by + 0.5, "facing": "down"}
        notes.append(f"spawn moved to walkable {(bx, by)}")

    losses = [
        "rules beyond the tile (movement cost, cover, sight, swim, hazard damage, "
        f"diagonals={grid.get('diagonals', '5')!r}) are not carried",
        "artwork and grid alignment (image, grid.cell_px, image_px) are dropped",
    ]
    if meta.get("labels"):
        losses.append(f"{len(meta['labels'])} label(s) became signposts (text only)")
    if meta.get("zones"):
        losses.append(f"{len(meta['zones'])} zone(s) became regions (rectangles only)")
    if spawns:
        losses.append(f"{len(spawns)} spawn(s): first is the player start, the rest are static "
                      "NPC markers (no stats, no sides); frog-named spawns become one frog "
                      "critters group")
    else:
        losses.append("no spawns: player start is map centre")
    stats = {"width": width, "height": height, "notes": notes,
             "tiles": used, "objects": len(objects)}
    return level, losses, stats


def serialize_level(level: dict) -> str:
    q = json.dumps
    lines = ["{"]
    for k in ["format", "version", "name", "subtitle", "author", "description",
              "width", "depth", "waterLevel", "water", "environment", "spawn"]:
        lines.append(f"  {q(k)}: {q(level[k])},")
    lines.append('  "legend": {')
    lk = list(level["legend"])
    for i, k in enumerate(lk):
        lines.append(f"    {q(k)}: {q(level['legend'][k])}{',' if i < len(lk) - 1 else ''}")
    lines.append("  },")
    for k in ["tiles", "heights"]:
        lines.append(f"  {q(k)}: [")
        for i, r in enumerate(level[k]):
            lines.append(f"    {q(r)}{',' if i < len(level[k]) - 1 else ''}")
        lines.append("  ],")
    lines.append('  "objects": [')
    for i, o in enumerate(level["objects"]):
        lines.append(f"    {q(o)}{',' if i < len(level['objects']) - 1 else ''}")
    extra = [k for k in level if k not in
             {"format", "version", "name", "subtitle", "author", "description",
              "width", "depth", "legend", "tiles", "heights", "waterLevel",
              "water", "environment", "spawn", "objects"}]
    lines.append("  ]," if extra else "  ]")
    for i, k in enumerate(extra):
        lines.append(f"  {q(k)}: {q(level[k])}{',' if i < len(extra) - 1 else ''}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def load_spec(name: str, maps_dir: pathlib.Path) -> dict:
    path = pathlib.Path(name)
    if path.suffix != ".json":
        path = maps_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"no map {name!r} in {maps_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("maps", nargs="+", help="map ids, as in display/maps/<id>.json")
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("lumina-out"),
                        help="folder to write <id>.json into (default: %(default)s)")
    parser.add_argument("--maps-dir", type=pathlib.Path, default=_MAPS,
                        help="where the engine's map files live (default: %(default)s)")
    parser.add_argument("--terrain", action="append", default=[], metavar="NAME=TILE",
                        help="Lumina tile for one of a map's own custom terrains "
                             "(repeatable), e.g. finish=c")
    parser.add_argument("--dry-run", action="store_true",
                        help="print each level, write nothing")
    args = parser.parse_args(argv)

    try:
        custom = parse_terrain_args(args.terrain)
    except ValueError as exc:
        print(f"map_to_lumina: {exc}", file=sys.stderr)
        return 1

    failed = 0
    for map_id in args.maps:
        try:
            spec = load_spec(map_id, args.maps_dir)
            stem = slug(pathlib.Path(map_id).stem if map_id.endswith(".json") else map_id)
            level, losses, stats = build_level(spec, stem or map_id, custom)
        except (Refused, ValueError, FileNotFoundError, KeyError) as exc:
            print(f"{map_id}: REFUSED: {exc}", file=sys.stderr)
            failed += 1
            continue
        if args.dry_run:
            print(f"# --- {stem}.json ---")
            print(serialize_level(level), end="")
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            target = args.out / f"{stem}.json"
            target.write_text(serialize_level(level), encoding="utf-8", newline="\n")
            print(f"{map_id}: {stats['width']}x{stats['height']} squares -> {target}")
        for note in stats["notes"]:
            print(f"  note {note}", file=sys.stderr)
        for loss in losses:
            print(f"  LOSS {loss}", file=sys.stderr)
    if args.dry_run:
        print("Dry run. Nothing written.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
