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
| `zones` | no | x positions of dashed vertical lines across the board (the Mage Tower halfway line) |
| `spawns` | no | Suggested token positions; `color` is a college (`quan`, `lore`, `pris`, `silv`, `with`), `danger` or `brass` |
| `token_art` | no | Per-spawn token portraits for `scripts/map_to_atlas.py`: `{"<spawn id>": "<path>"}`. See below |

### Token portraits

`token_art` gives a spawn a real picture instead of a coloured disc, keyed by the spawn's `id`:

```json
"token_art": {
  "K": "atlas-vtt/assets/XW4tQg9_1790718416294_uz4mul.webp",
  "Q1": "atlas-vtt/assets/XW4tQg9_1790718416294_uz4mul.webp"
}
```

Reach for it when generated art does not arrive named after the monster: a vault's real portrait is often `XW4tQg9_1790718416294.webp`, and only the map knows it is the quandrix student. The value may be absolute, already inside the Atlas vault (referenced where it lies), or relative to `display/maps` (copied in). `scripts/map_to_atlas.py` also accepts a `--token-art` directory of files named after the spawn, and checks that whatever it returns exists -- a typo falls back to the disc and is reported, rather than writing a token Atlas cannot load.

**The engine never reads `token_art`.** Only the Atlas exporter does, and a map with no `token_art` behaves exactly as before.

### Making art importable (the other half)

`token_art` is for *a map's* spawns. It is not how art reaches Atlas's asset library, and a portrait sitting in the vault is not a token: Atlas lists tokens from entries in `atlas-vtt/.atlas-data/assets-metadata.json`, so a folder of correct, correctly named PNGs still shows every token as **Missing image** in the Create-tokens panel.

Two halves, both required:

```bash
python3 scripts/register_tokens.py --vault ~/path/to/vault --art /path/to/art --dry-run
python3 scripts/register_tokens.py --vault ~/path/to/vault --art /path/to/art
python3 scripts/register_tokens.py --vault ~/path/to/vault --list    # any missing image, exit 1
```

It copies each picture into `atlas-vtt/collections/<collection>/tokens/`, writes the metadata entry, and generates the thumbnail at the name Atlas looks for — `assets/thumbnails/<stem>-<fnv1a(imagePath)>.webp`. That hash is Atlas's, and it is pinned by tests against five thumbnails from a working vault: get it wrong and every token renders a blank swatch with no error. Idempotent, so it is safe in a loop; `--list` exits non-zero when an entry points at a file that is gone, which is the one failure a user cannot otherwise see coming.

`map_to_atlas.py` deliberately still does not touch `assets-metadata.json` — it writes only scene files, so a half-finished export cannot corrupt an index Atlas owns.

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

`training-yard.json` is the tutorial map (`scripts/tactics/play.py tutorial`, see `docs/TUTORIAL.md`). The other five maps are ported from the player-facing tabs of `display/static/reference/strixhaven_map_table.html`, which is kept in step by `scripts/campus_extract.py --check`.

`mage-tower.json` is the exception worth reading before you paint on it. Its terrain was derived from the Strixhaven Mage Stadium art rather than clicked out: the octagons, the two towers and the two halfway lines are the mapmaker's own, and the water and the planking are read off the picture. The ground inside the pitch is the four numbered sections of the game, so a square you repaint changes the section it is in. Only the moat is out of bounds in those rules, so the terrace, the lawns and the stands beyond it are left as ordinary ground and the map's own artwork carries them; paint them out if you want the match contained.

**The map's rules live in its `info` field, and the engine does not read it.** `info` goes to the display, the catalog and `docs/CAMPUS.md`; nothing in `scripts/tactics/` reads it, so a GM driving this fight will not see the rules unless told to. The `## Mage Tower` section of `scripts/tactics.md` is that telling, and it names the advisor that keeps the score, the clock and the mascots. The terrain here is engine-owned and needs no narration; the match around it is not.

## Artwork

A map may carry a background image under the terrain, plus the grid alignment that lines the picture up with the 5 ft squares:

```json
"image": "images/bows-end-tavern.jpg",
"grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0}
```

`width`/`height` are the image's pixels divided by `cell_px`, so the board and the picture are the same size and `preserveAspectRatio=none` stretches one to the other. The display draws art → a translucent terrain wash → its own grid lines, so **use the gridless artwork**: a baked-in grid would show a second set of lines under the wash. The engine never reads `image` or `grid`; `grid.rows` and every rule are unchanged, and a map with no image behaves exactly as before.

### Importing creator JPGs

Battle-map JPGs carry their whole spec in the filename:

```bash
python3 scripts/art_import.py ~/Downloads/my-maps --credit "Artist Name" --dry-run
python3 scripts/art_import.py ~/Downloads/my-maps --credit "Artist Name"
```

It writes `images/<slug>.jpg` and a map file with the grid filled in and **`features` left empty** — a picture does not say which squares are wall, so terrain is painted afterwards in the browser (`/maps/<slug>/edit`). It refuses, rather than guesses, when the pixels are not a multiple of the cell size or the filename's square count disagrees with them: every distance at the table comes from the grid, not the picture, so a grid one square out plays wrong while looking fine.

Artwork is third-party and 5–13 MB a file, so `images/` is in `.gitignore`. The map JSONs are committed and list normally; on a clone without the images the maps still load, as bare terrain grids. Keep the `credit` and `source` fields — free to download is not free of the artist's claim.

### A large collection

One map is a folder. A hundred is a filing problem, and two tools exist for that:

```bash
python3 scripts/art_triage.py ~/Downloads/big-folder        # decide, write nothing
python3 scripts/art_triage.py ~/Downloads/big-folder --import-ok --credit "Artist"
python3 scripts/map_catalog.py --open                      # browse what you have
```

`art_triage.py` groups a folder before importing any of it, because a collection is not a list of maps: every scene arrives as a grid/gridless pair, some files are token sheets or Patreon promos, some are re-uploads under a second name, and a few filenames do not describe their own grid. It reports five buckets — `import`, `variant`, `refuse`, `not a map`, `duplicate` — and writes nothing without `--import-ok`. Refusals carry the reason, because a grid one square out looks correct on screen and plays wrong at the table.

`map_catalog.py` renders every map that loads into one static HTML page with a thumbnail, its size in feet, and its terrain. Thumbnails are inlined as data URIs, so it works from a USB stick and survives being emailed; a map with no artwork installed says so rather than drawing a blank tile that would be indistinguishable from an empty map.

Thumbnails need an image resizer, and none is a dependency of the engine. It looks for `magick`, `convert`, `ffmpeg` and `sips` (macOS only) in that order. With none of them installed the page is still written and every map still listed -- the artwork is simply not shown, and both the page and stdout say so, rather than passing off a missing resizer as missing artwork.
