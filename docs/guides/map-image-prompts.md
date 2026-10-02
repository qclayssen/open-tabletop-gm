# Image prompts for battle maps

Prompts for the maps in [map-plan-strixhaven-kairos.md](map-plan-strixhaven-kairos.md),
written for a text-to-image model, plus the post-processing that makes the output
loadable. Written 2026-09-30.

**The prompt does not decide whether a map works.** `art_import.py:156-167` verifies
three claims in the filename against the real pixels — that each dimension is a
multiple of `cell_px`, that dividing by `cell_px` gives the stated square count, and
that the total is under 10,000 squares. An image model will not emit 2000x1400 on
request; it emits 1024x1024 or 1820x1365. So every generated image needs a **resize
to an exact multiple before import**, or `art_import.py` refuses it. That resize is
not optional polish — it is the step that makes the file legal.

## What still needs generating

Status as of **2026-10-02**, read off `display/maps/` rather than from the plan,
because the two disagree in ways that matter. Reconciled against the nine
prompts below and the build order in the
[map plan](map-plan-strixhaven-kairos.md).

| # | Map | Map file | Grid declared | Artwork | What is left |
|---|-----|----------|---------------|---------|--------------|
| 1 | Hesper's walled garden | `hesper-walled-garden` 20x14 | `cell_px: 100` | no | generate, resize, import |
| 2 | Biblioplex stacks | `biblioplex-stacks` 30x20 | `cell_px: 100` | no | generate, resize, import |
| 3 | Enrollment Ledger rotunda | `enrollment-ledger-rotunda` 24x18 | `cell_px: 100` | no | generate, resize, import |
| 4 | Detention Bog | `detention-bog` 24x18 | **none** | no | declare grid, then generate |
| 5 | Vess's Ledger-Hollow | **none** | - | - | create the map, then generate |
| 6 | Fields of Strife | **none** | - | - | create the map, then generate |
| 7 | The Unwritten Stacks | **none** | - | - | create the map, then generate |
| 8 | Firejolt Café and Rooftops | `firejolt-rooftops` 24x16 | **none** | no | declare grid, then generate, resize, import |
| 9 | Training Yard | `training-yard` 12x9 | **none** | no | declare grid, then generate, resize, import |

**Six of the nine exist as playable maps and only lack a picture.** They already
carry terrain and spawns, so they are fought today and merely look bare.
**Three do not exist as maps at all**, which is a different job: those need
a map JSON written before there is anything to hang art on.

**#4 is the one to do first, and it is not only artwork.** `detention-bog.json` is
24x18, the table says 2400x1800, and `art_import.py`'s three-claim check passes
against those numbers. It needs `grid: {cell_px: 100, offset_x: 0, offset_y: 0}`
declared first — the only difference between it and #1–#3, which already have it.
**#8 and #9 are in the same position**: `firejolt-rooftops.json` and
`training-yard.json` carry terrain and spawns but declare no grid, so each needs
the same `cell_px: 100` declaration before its art can be imported.

The reason to start there is frequency, not readiness: the plan calls it "the
most-recurring site in the campaign: 1.3, 2.3, 4.3" and "the one map worth
generating carefully". All six maps carry spawns (`hesper-walled-garden` 3,
`enrollment-ledger-rotunda` 5, `biblioplex-stacks` 6, `detention-bog` 6,
`firejolt-rooftops` 6, `training-yard` 3), so all six become scenes with working
statblock links the moment a picture lands — the link path is blocked by pixels,
not by anything in the code.

### Two traps in the list

**`detention-bog.json` and `the-detention-bog.json` are different maps.** They
are 24x18 and 32x45, "Detention Bog" and "The Detention Bog", and only the second
has artwork on disk. `detention-bog.json` is the played one: it is in
`tests/test_tactics_cli.py`'s required set, it is the map `tests/test_formations.py`
captures its fixtures from, and it is what prompt #4 describes. The near-identical
filename is exactly the thing that makes a "does this map have art yet" check
answer about the wrong file, so import into **`detention-bog.json`**.

**Eight maps in the repo have no artwork, and six are on this list.** The two
left out are `blank` and `frog-pond`. `blank` is infrastructure, a bare grid with
no scene. `frog-pond.json` is the Witherbloom race course that
`scripts/tactics/play.py` runs as its `frogs` scenario and that the test harness
defaults to; it is free-play ground, not a scene this campaign stages, so it is
not prompted. (The tutorial map `start.py` plays is `training-yard`, prompted
below as #9.)

### Do not generate a placeholder

`ROADMAP-ideas.md` is blunt about it: a generated placeholder "would produce maps
that look playable and are not, which is the failure KC4 exists to refuse and the
one that gets found mid-fight." A blank terrain map is honest about being one. If
the art queue stays empty, the smaller alternative is to declare the grid on the
six maps and play them with terrain only.

## The pipeline

```bash
# 1. generate (see prompts below) -> download to ~/maps/<slug>-raw.png
# 2. resize to the exact grid-exact size for a 100px cell
sips -Z 2000 ~/maps/garden-raw.png --out ~/maps/garden.jpg     # -Z caps the long edge
#    or, when the long edge is the width:
ffmpeg -i in.png -vf scale=2000:1400 out.jpg

# 3. name it so the grid is in the filename
mv ~/maps/garden.jpg "Free - Hesper's Garden - 2000x1400 - 20x14 - 100px - gridless.jpg"

# 4. import
python3 scripts/art_import.py ~/maps --credit "<who made it>" --dry-run
python3 scripts/art_import.py ~/maps --credit "<who made it>"

# 5. paint terrain — the picture does not say where the walls are
#    /maps/hesper-garden/edit
```

Note step 3: `sips -Z 2000` fits *within* 2000x2000 preserving aspect, so it will not
give you 2000x1400 unless the source is already 10:7. Use `sips --resampleHeightWidth
1400 2000` when the exact pair matters, since it does not preserve aspect — and then
check the source was close to the target ratio, or the art is stretched. The display
itself stretches (`preserveAspectRatio: 'none'` in `tactics.js:405`), so a mismatched
ratio silently distorts every distance you measure by eye.

The three-claim verification in `art_import.py` is the reason for `cell_px: 100`.
Every existing map uses 100px cells at that exact pitch; keeping it means
`display/maps/README.md`'s existing examples, the `--credit` fixtures and the
tolerance in `map_catalog.py` all keep working unchanged.

## Sizes

Aspect ratio first, because that is what the prompt has to state, and the pixel count
follows from it. Ratios are `width/height`.

| Map | Squares | Ratio | Exact px @100 |
|-----|---------|-------|---------------|
| Hesper's walled garden | 20x14 | 1.429 | 2000x1400 |
| Biblioplex stacks, restricted wing | 30x20 | 1.500 | 3000x2000 |
| Enrollment Ledger rotunda | 24x18 | 1.333 | 2400x1800 |
| Detention Bog | 24x18 | 1.333 | 2400x1800 |
| Firejolt Café and Rooftops | 24x16 | 1.500 | 2400x1600 |
| Training Yard | 12x9 | 1.333 | 1200x900 |
| Vess's Ledger-Hollow | 24x16 | 1.500 | 2400x1600 |
| Fields of Strife | 40x30 | 1.333 | 4000x3000 |
| Unwritten Stacks | 30x40 | 0.750 | 3000x4000 |

All are under the 10,000-square cap. Fields of Strife is 1,200 and Mage Tower Stadium
is the largest shipped map at 1,312, so there is headroom if a map needs to grow.

## The base prompt

Every prompt below is this shape. The four clauses are not stylistic — each one exists
because something downstream breaks without it.

> **top-down orthographic battle map, [SUBJECT]. flat overhead view, no perspective, no
> foreshortening. [LIGHTING]. hand-painted, muted earth tones, soft ambient occlusion.
> empty floor, no characters, no creatures, no text, no labels, no UI elements.
> seamless rectangular composition, [RATIO] aspect ratio.**

The clauses that matter:

- **top-down orthographic, no perspective** — an angled view makes every square a
  different size, and distance at the table comes from the grid, not the picture.
- **no grid lines, no checkerboard** — `display/maps/README.md`: use the gridless
  artwork, because the display draws its own grid over a translucent terrain wash and a
  baked-in grid shows a second set of lines underneath it.
- **no text, no labels** — models render plausible gibberish, and it sits on the art
  forever.
- **empty floor, no creatures** — a ghoul painted into the bog is a ghoul the players
  see before initiative is rolled.
- **aspect ratio stated** — the single highest-leverage clause. Every failure mode of
  this pipeline is a wrong ratio.

A negative prompt helps on some models and is ignored by others:

> grid, gridlines, tiles, checkerboard, perspective, isometric, tilted camera, text,
> labels, watermark, signature, UI, HUD, characters, creatures, monsters, people,
> weapons, drop shadows baked into the floor, fisheye, vignette border, ornate border,
> framed picture, parchment texture, hand-drawn border

## The prompts

> All nine. Numbers 1–4 are the ones the next sessions actually block on. Numbers
> 8–9 are the other maps that already exist as terrain and only lack a picture, so
> generate them in the same batch. Numbers 5–7 do not exist as maps yet: they are
> reachable only from Act 2 onward, and 7 is the last thing needed. Number 7 covers
> two locations — the Unwritten Stacks and the Lower Hall of Oracles, chapters 4.4
> and 4.5 — on one 30x40 grid, because the finale moves between them and a fight
> split across two boards costs more than the accuracy is worth.

### 1. Hesper's walled garden — 20x14, 1.429

The first grid the campaign runs, so it has to be legible before it is atmospheric.

> Top-down orthographic battle map, a small walled herb garden behind a scholar's
> house at dusk. Flat overhead view, no perspective, no foreshortening. Four raised
> planting beds of dark soil in a rough grid, edged with broken orange potsherds. A
> weathered stone lintel lying flat in one corner, worn smooth on its top face, used as
> a bench. A weathered stone sphinx head, its nose broken away, sitting in a shallow
> circular basin that holds a pool of still rainwater. A narrow arched green door set
> into the north wall, lintel and hinge visible, slightly ajar. Cracked flagstone path
> between the beds. Low herb planting, a few stone borders. Warm copper dusk light
> raking in from the west, long soft shadows to the east. Hand-painted, muted earth
> tones, soft ambient occlusion. Empty floor, no characters, no creatures, no text, no
> labels, no UI elements. Seamless rectangular composition, 10:7 aspect ratio.
> No grid, no perspective, no text, no characters.

The three landmarks are load-bearing in the scene itself — `SCENE-0B-DESIGN.md` has
Kairos arrive at a mark he left on the lintel, and the sphinx head is the image that
closes the cold open and recurs for forty minutes. Name them in the prompt; do not
rely on the model inventing them recognisably.

### 2. Biblioplex stacks, restricted wing — 30x20, 1.500

Sight-blocking is the mechanic, so the shelves must be unmistakable as walls.

> Top-down orthographic battle map, the restricted inner stacks of a vast library.
> Flat overhead view, no perspective, no foreshortening. Six long parallel rows of
> tall dark wooden shelving running the length of the room, forming narrow aisles
> between them. The aisles are about one person wide. Each shelf is packed edge to
> edge with books, crates and rolled scrolls. A single narrow reading desk with a
> green shaded lamp near the centre. Iron ladder rails against two of the shelves. A
> locked iron-bound door in the far wall. Bare stone floor worn pale in the aisles.
> Cool low lamplight, deep shadow in the gaps between shelves, one warm pool at the
> desk. Hand-painted, muted earth tones, soft ambient occlusion. Empty floor, no
> characters, no creatures, no text, no labels, no UI elements. Seamless rectangular
> composition, 3:2 aspect ratio. No grid, no perspective, no text, no characters.

The aisles being one-person wide is the design constraint. This map recurs in 1.3,
2.4 and 4.3 and the shelving is what makes those fights different from each other.

### 3. Enrollment Ledger rotunda + omenpath gate — 24x18, 1.333

The gate is where the campaign ends in 4.5, so it needs to read as a threshold.

> Top-down orthographic battle map, a circular stone rotunda inside a university
> records office, with an arched doorway in one wall. Flat overhead view, no
> perspective, no foreshortening. Ring of eight slender columns around the perimeter,
> a curved desk of dark wood in an arc across the middle, open ledgers and stacked
> paper on it. A ring mosaic set into the flagstone floor at the centre. Beyond the
> doorway, a short flight of stone steps rising to a tall iron archway set in a thick
> outer wall, faint green-gold light spilling through the arch. Marble floor, worn
> brass inlay. Cool interior light, the doorway lit much warmer than the room.
> Hand-painted, muted earth tones, soft ambient occlusion. Empty floor, no
> characters, no creatures, no text, no labels, no UI elements. Seamless rectangular
> composition, 4:3 aspect ratio. No grid, no perspective, no text, no characters.

### 4. Detention Bog — 24x18, 1.333

Most-recurring site in the campaign: 1.3, 2.3, 4.3. The one map worth generating
carefully.

> Top-down orthographic battle map, a shallow fetid bog inside a walled campus
> graveyard. Flat overhead view, no perspective, no foreshortening. Standing black
> water in irregular pools across most of the ground, with a raised grassy hummock in
> the centre reached by two narrow plank walkways. Half-sunken headstones and a
> broken marble plinth in the water. Cattails and reeds thick along every edge. A
> rusted iron cage on a post, half in the water. Rotten plank decking collapsed into
> the bog at one corner. Dusk, flat overcast light, green-grey water, mist lying in the
> low ground. Hand-painted, muted earth tones, soft ambient occlusion. Empty floor,
> no characters, no creatures, no text, no labels, no UI elements. Seamless
> rectangular composition, 4:3 aspect ratio. No grid, no perspective, no text, no
> characters.

The hummock plus two walkways gives the fight a shape: one defensible position with
exactly two approaches. That is a design decision, not decoration, and it is cheaper
to get into the art than to paint afterwards.

### 5. Vess's Ledger-Hollow — 24x16, 1.500

Below the Bog. Ledger-themed, not gore-themed — Vess is a function, not a monster.

> Top-down orthographic battle map, a sunken stone chamber that is half archive and
> half ledger office. Flat overhead view, no perspective, no foreshortening. Floor is
> dark slate, scored with long straight grooves like the columns of an accounts book.
> Iron shelving on three walls loaded with bound ledgers. A heavy writing desk with a
> balance scale, an inkwell and a ledger open on it. A brass floor grate in one corner
> with faint pale light rising through it. Water staining down one wall. Cold
> blue-green light from below, one small warm lamp on the desk. Hand-painted, muted
> earth tones, soft ambient occlusion. Empty floor, no characters, no creatures, no
> text, no labels, no UI elements. Seamless rectangular composition, 3:2 aspect ratio.
> No grid, no perspective, no text, no characters.

### 6. Fields of Strife — 40x30, 1.333

An open field. Resist describing it: every object you add is something the engine has
to be told about, and open ground is genuinely the right terrain here.

> Top-down orthographic battle map, a wide open field of dry summer grass. Flat
> overhead view, no perspective, no foreshortening. Mostly featureless grass with
> wheel-rutted dirt tracks running across it. A shallow dry streambed cutting diagonally
> through one corner, banks about chest high, offering cover. Three isolated boulders
> and one dead bare tree, widely spaced. Low stone boundary walls along two edges, low
> enough to see over. Harsh late-afternoon sun, long shadows. Hand-painted, muted
> earth tones, soft ambient occlusion. Empty floor, no characters, no creatures, no
> text, no labels, no UI elements. Seamless rectangular composition, 4:3 aspect ratio.
> No grid, no perspective, no text, no characters.

The streambed is the only real terrain, and it is the whole map. Dry grass is
`difficult`, not wall — getting that in the JSON is what makes it play correctly, and
the art only has to make it readable.

### 7. The Unwritten Stacks — 30x40, 0.750

Portrait orientation, finale. Same family as #2 so the two read as the same building.
Covers the Unwritten Stacks and the Lower Hall of Oracles — the stair down and the
hall it arrives at, in one grid, so the finale does not need two boards.

> Top-down orthographic battle map, a deep archive stair descending into bedrock.
> Flat overhead view, no perspective, no foreshortening. A long stone stair running
> the length of the room, down to a landing and a second flight. Shelving on both
> sides, the shelves on the lower flight holding nothing but blank pale tablets and
> unfilled frames. A cracked stone archway at the bottom, black beyond it. Roots
> breaking through the ceiling and the upper steps. Water seeping down one wall in a
> dark stain. No light of its own beyond a faint cold glow from the archway.
> Hand-painted, muted earth tones, soft ambient occlusion. Empty floor, no
> characters, no creatures, no text, no labels, no UI elements. Seamless rectangular
> composition, 3:4 aspect ratio. No grid, no perspective, no text, no characters.

### 8. Firejolt Café and Rooftops — 24x16, 1.500

Firejolt is the map plan's row 12, a download candidate for 1.1 and 1.4, but its
terrain is already written as `firejolt-rooftops.json`, so only the picture is
missing. The fighting shape is the gap: a café floor on the left and four flat
rooftops on the right, split by two- to three-square gaps.

> Top-down orthographic battle map, a ground-floor café beside a cluster of flat
> rooftops at dusk. Flat overhead view, no perspective, no foreshortening. The left
> side, just under half the width, is the inside of a café: a plank floor with a
> stone hearth in one corner, two square tables, and a long serving counter along
> the lower wall. A timber wall runs down the café's right edge with a doorway
> broken through it. To the right of the café the floor drops away into open air,
> and four separate flat rooftops sit beyond the gap, each a rectangle of slate
> tiles. The rooftops are offset from one another, so narrow two- to three-square
> gaps of dark drop separate them on every side. Three square stone chimneys rise
> from the roofs, one each on three of them. Cool blue evening light, the café warm
> and lamplit, deep shadow down in the gaps. Hand-painted, muted earth tones, soft
> ambient occlusion. Empty floor, no characters, no creatures, no text, no labels,
> no UI elements. Seamless rectangular composition, 3:2 aspect ratio. No grid, no
> perspective, no text, no characters.

The gaps and the chimneys are the map's rules, not decoration: the `info` field
makes a two- to three-square gap a jump or an Acrobatics check, and a chimney is
the only half cover on the roofs. Keep both unmistakable. Declare
`grid: {cell_px: 100, offset_x: 0, offset_y: 0}` on `firejolt-rooftops.json`
before importing, the same as #4.

### 9. Training Yard — 12x9, 1.333

The tutorial map (`start.py` runs `play.py tutorial` against it), and a
deliberately plain one: a single dividing wall is the only sight blocker, so the
prompt keeps the few objects isolated against open ground.

> Top-down orthographic battle map, a walled practice yard behind a row of
> dormitories. Flat overhead view, no perspective, no foreshortening. A low stone
> wall encloses the yard on every side, with a wooden fence along the back edge.
> One stone dividing wall juts down from the top edge near the centre, ending
> partway into the yard. A single wooden training post stands in the upper left. A
> stack of hay bales sits right of centre, and a patch of churned mud fills the
> lower left. A long stone water trough runs along the bottom right. Worn dirt and
> thin grass cover the rest of the ground. Flat overcast daylight, muted greens and
> browns. Hand-painted, muted earth tones, soft ambient occlusion. Empty floor, no
> characters, no creatures, no text, no labels, no UI elements. Seamless
> rectangular composition, 4:3 aspect ratio. No grid, no perspective, no text, no
> characters.

The stone wall and the hay bales are the whole encounter: the wall blocks sight
and the bales give half cover, so both have to read at a glance. Declare
`grid: {cell_px: 100, offset_x: 0, offset_y: 0}` on `training-yard.json` before
importing.

## What to do when the output is wrong

The pipeline refuses rather than guesses, so every failure is a message naming the
problem. These are the four you'll hit.

| Message | Cause | Fix |
|---|---|---|
| `width 1820px is not a multiple of the 100px cell` | generator didn't hit the size | resize (step 2). Never round the cell size to fit the art. |
| `is 20 cells at 100px, but the name says 20 squares` after a rename | filename and pixels disagree | one of the two is a typo. Re-measure with `sips -g pixelWidth -g pixelHeight`. |
| `20x14 = 280 squares is over the 10000-square limit` | filename misread, usually a missing cell count | check the filename convention, not the map |
| loads but everything looks stretched | source ratio ≠ target ratio | regenerate at the right ratio; `preserveAspectRatio: 'none'` will not warn you |

The rule underneath all four: **the grid is authoritative and the picture is
decoration.** `maps.py:compile_map` compiles terrain rectangles to `grid.rows` and
never reads `image` or `grid`; an image changes `meta` only. If the art and the grid
disagree, the art is wrong. `art_import.py` is written to refuse in exactly that
situation, because a grid one square out looks correct on screen and plays wrong at
the table.

## Two things to settle first

**Credit.** `build_spec` writes a `credit` and a `source` field, and `display/maps/README.md`
is blunt about why: free to download is not free of the artist's claim. For generated
art, put the model and the prompt in `--credit` and the export filename in `source`, so
the map file records what produced it. Generated art is cleaner legally than scraped
art — but only if you don't then use it to imitate a named artist's style, which is a
different question and worth deciding deliberately rather than by accident.

**Terrain still gets painted by hand.** Nothing here changes that. `art_import.py`
deliberately writes `features: []`, and the browser painter at `/maps/<name>/edit` is
what fills it in. That is not a limitation of generated art — it is the finding from
Experiment B in the decision doc: a swamp and a plank floor are the same JPEG, so
terrain cannot be derived from pixels. Generated art has the same property as
downloaded art, and the same second step.
