# D-15 prototype: shared fixture (qclayssen/dnd-gm#415)

Bounded comparison spike. The engine remains authoritative for positions,
range, cover, damage, visibility and action economy. Renderers consume state
and emit user intent.

## Baseline

Existing SVG board: `display/static/tactics.js` (2851 lines) with
`display/static/tactics.css`. No behavior change in this spike; it is the
reference for parity.

## Consumed fields

From `fixture.build_fixture()`:

- `grid.width`, `grid.height`, `grid.squares_ft` (5), `grid.rows` (map strings,
  see `scripts/tactics/grid.py` legend), `grid.diagonal` ("5").
- `tokens[]`: `id`, `name`, `side` (pc/ally/enemy/neutral), `x`, `y`,
  `width`, `height` (footprint anchored at top-left square), `hp`, `max_hp`,
  `label` (for example "C3").
- `visibility{} `: token id to bool, SUPPLIED by the engine. The renderer
  never computes visibility or rules from it; hidden tokens stay hidden.
- `selection`, `target`: current intent state.
- `rev`: FIXTURE-ONLY temporary monotonic revision pending D-10 (#433).
  Apply a snapshot only when `rev > applied_rev` (`apply_snapshot`).
  Stale or out-of-order snapshots never win; the frontend never invents a rev.

## Coordinate transforms

Grid `(x, y)`, `(0, 0)` top left. Label: column letter + 1-based row.
World feet: `wx = x * 5`, `wy = y * 5`; square centre `(wx + 2.5, wy + 2.5)`.
Footprints extend `+width` / `+height` squares from the anchor.

## Replay

`replay_sequence()` is the fixed intent script every renderer runs:
select kairos, movement intent, applied move, target intent, select ogre.
Revs strictly increase.

## Out of scope for this slice

three.js board, Canvas 2D board, Playwright harness, metrics table,
migration plan and Flask route. Tracked as follow-ups in the change brief.
No Lumina-derived code until the D-16 license gate clears.
