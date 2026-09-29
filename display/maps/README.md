# Battle maps

Each `.json` file here is a battle map for grid combat. The file name is the map's id: `frog-pond.json` is started with `combat.py start frog-pond ...`.

A map is a grid of 5 ft squares. You paint it with rectangles, in order, over a base terrain; later rectangles cover earlier ones. Coordinates start at `x: 0, y: 0` in the top left, so the square players call `A1` is `x: 0, y: 0` and `D5` is `x: 3, y: 4`.

## Example

```json
{
 "name": "Frog Pond",
 "width": 20,
 "height": 14,
 "diagonals": "5",
 "info": "One or two sentences shown beside the map.",
 "base": "floor",
 "terrain": {
  "finish": {"cost": 1, "blocks_sight": false, "cover": 0, "color": "feature"}
 },
 "features": [
  {"type": "water", "x": 3, "y": 0, "w": 14, "h": 14},
  {"type": "difficult", "x": 5, "y": 3, "w": 2, "h": 2},
  {"type": "finish", "x": 17, "y": 0, "w": 1, "h": 14, "label": "Finish line"}
 ],
 "spawns": [
  {"id": "K", "name": "Kairos", "color": "quan", "x": 1, "y": 6}
 ]
}
```

## Fields

| Field | Required | Meaning |
|-------|----------|---------|
| `name` | yes | Shown in the display and the session log |
| `width`, `height` | yes | Size in squares |
| `diagonals` | no | `"5"` (default, 2014 rule: every square costs 5 ft) or `"5-10-5"` (every second diagonal costs 10 ft) |
| `info` | no | A short description for the display |
| `base` | no | Terrain under everything, default `floor` |
| `features` | no | Rectangles: `type`, `x`, `y`, `w` and `h` (both default 1), optional `label`. The map editor rewrites this list; see below |
| `terrain` | no | Extra terrain types for this map (see below) |
| `zones` | no | x positions of dashed zone lines (the Mage Tower pitch) |
| `spawns` | no | Suggested token positions; `color` is a college (`quan`, `lore`, `pris`, `silv`, `with`), `danger` or `brass` |

## Terrain types

| Type | Moving in | Sight | Cover |
|------|-----------|-------|-------|
| `floor` | 5 ft | clear | none |
| `wall` | impassable | blocked | |
| `difficult` | 10 ft | clear | none |
| `water` | 10 ft, or 5 ft with a swim speed | clear | none |
| `hazard` | 5 ft; the GM is told when someone enters | clear | none |
| `feature` | 10 ft (furniture, rubble, a chimney) | clear | half (+2 AC) when it is between attacker and target |
| `void` | impassable (a drop, a gap between roofs) | clear | none |

A map-specific type in `terrain` takes `cost` (1 normal, 2 difficult, `null` impassable), `blocks_sight`, `cover` (0, 2 or 5) and `color` (which built-in terrain colour the display uses).

## Painting a map in the browser

With the display app running, `/maps/<name>/edit` paints terrain by clicking: pick a type, drag a rectangle, press Save. `/maps/<name>/features` does the writing. The engine is not involved: `grid.rows` changes only where you painted, and a map you did not paint on is left byte-identical.

Saving **merges** rather than appends. Your strokes are replayed onto the map's squares and `features` is re-derived from the result as a set of *disjoint* rectangles, so painting water over a wall replaces the wall's rectangle instead of stacking a second one on top. A few consequences worth knowing before you edit a hand-written map:

- **The file gets longer the first time.** A map written the human way (one big water rectangle, then reeds stamped over it) cannot be described by disjoint rectangles, so the cover has to describe the water *around* each reed. It is a one-off: the second save is the same size as the first, and further edits do not grow it.
- **A `label` follows its region, not its rectangle.** A region broken into several rectangles keeps one label, re-attached to the first of them, so it can move by a square or two. No label is dropped or duplicated.
- **Last rectangle wins, as everywhere else.** There is no undo history in the file: painting `floor` over a wall gives `floor`. Undo in the editor drops your stroke before you save; after a save, repaint what was underneath.
- Squares left at the base terrain with no label get no rectangle at all, which is how you erase.
- The original file is kept as `<name>.json.bak` (the first save keeps it; later saves do not overwrite it), and saving over a map that already has terrain asks you to confirm first.

The merge is `scripts/tactics/mapeditor.py`; `tests/test_mapseditor.py` covers it, including the guarantee that re-saving any map in this folder does not move a single square.

## Checking a new map

```bash
python3 -c "import sys; sys.path.insert(0, 'scripts'); from tactics import maps; print('\n'.join(maps.load('my-map')['grid']['rows']))"
```

It prints the map as text (`.` floor, `#` wall, `,` difficult, `~` water, `^` hazard, `o` feature, `_` void), or a clear error naming the rectangle that is wrong. `python3 -m pytest tests/test_tactics_cli.py` also checks that every map in this folder loads.

`training-yard.json` is the tutorial map (`scripts/tactics/play.py tutorial`, see `docs/TUTORIAL.md`). The other five maps are ported from the player-facing tabs of `display/static/reference/strixhaven_map_table.html`.
