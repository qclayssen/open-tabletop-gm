/**
 * chartdown_to_atlas.mjs — turn a Chartdown map document into an Atlas VTT scene.
 *
 *   npx @chartdown/cli check bows.cd            # author here, fail loud there
 *   node scripts/chartdown_to_atlas.mjs bows.cd -o scene.atlasmap --background path.jpg
 *
 * WHY THIS EXISTS
 * ---------------
 * Experiment B (docs/research/atlas-vtt/OBSIDIAN-INTEGRATION-DECISION.md) returned a
 * NO-GO on converting an Atlas scene into engine terrain. That still stands, and this
 * script is not a counterexample to it — it runs the conversion the OTHER way, and the
 * asymmetry is the whole point.
 *
 * Going Atlas -> engine, the walls were freeform hand-dragged line segments, un-snapped
 * to the grid. The faithful rule (a square is wall iff a segment crosses its interior)
 * yields 0 wall squares out of 240 on a room map, because a wall drawn along a grid line
 * enters no square's interior. Which side of a line is wall "is not in the file. That is
 * human judgement, roughly 200 times per map." That cost is what killed the import.
 *
 * Going Chartdown -> Atlas, the walls arrive already snapped: `resolveScene` returns them
 * as cell-edge segments, one per grid line, with the blocking side unambiguous. The
 * judgement Experiment B could not get out of the file is computed upstream by the
 * Chartdown parser, from the building footprints themselves. So the 200-per-map human
 * cost goes to zero, and what remains is a unit conversion.
 *
 * So this is not "Atlas data is convertible after all". It is: do not ask Atlas to be the
 * source of geometry. Author in a language that computes geometry, and use Atlas only for
 * what it is actually good at — dragging tokens around a picture with fog and sight.
 *
 * Atlas's wall schema, read out of the installed plugin bundle
 * (.obsidian/plugins/atlas-vtt/main.js, `addWall`):
 *
 *     { id, kind: "wall", type: "solid" | "door" | "secret-door",
 *       p1: {x, y}, p2: {x, y}, closed?: bool, chainId?: string, direction?: string }
 *
 * Coordinates are in BACKGROUND PIXELS, not cells. `chainId` groups segments drawn as one
 * polyline; it is cosmetic (selection), so we omit it and let every segment stand alone.
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, basename } from "node:path";

import { parse } from "@chartdown/core";
import { resolveScene } from "@chartdown/render-svg";

/** Atlas scene schema version this script writes. The plugin migrates older ones. */
const ATLAS_VERSION = 4;

/**
 * Atlas grid defaults, matching the plugin's own GridState. `size` is pixels per cell and
 * must agree with `background`'s pixel dimensions, or every token lands off its square.
 */
const DEFAULT_GRID = {
  enabled: true,
  visible: true,
  snapToGrid: true,
  type: "square",
  size: 100,
  offsetX: 0,
  offsetY: 0,
  color: "#000000",
  opacity: 0.35,
  lineType: "solid",
  lineWidth: 1,
  unitType: "feet",
  unitDistance: 5,
  measurementType: "units",
};

/** Counter for Atlas's `wall_<timestamp>_<rand>` id shape. Deterministic, unlike Date.now(). */
let wallSeq = 0;
const wallId = () => `wall_cd_${String(++wallSeq).padStart(4, "0")}`;

/**
 * A cell-edge segment to pixels.
 *
 * Chartdown cells are 1-BASED on both axes and its line coordinates are 1-based too: the
 * north edge of cell (x, y) is the line y = y, and cell (1,1)'s north-west corner is (1,1).
 * Atlas pixels are 0-based from the image's top-left. So a Chartdown LINE n sits at pixel
 * (n - 1) * cellPx. Getting this wrong is a silent one-cell shift of every wall, so it is
 * worth stating rather than deriving: `resolveScene` hands back line numbers, not corners.
 */
const lineToPx = (n, cellPx, offsetPx) => (n - 1) * cellPx + offsetPx;

/** A cell centre to pixels — used for tokens, which Atlas centres on their cell. */
const cellCentreToPx = (cell, cellPx, offsetPx) => ({
  x: (cell.x - 1) * cellPx + cellPx / 2 + offsetPx,
  y: (cell.y - 1) * cellPx + cellPx / 2 + offsetPx,
});

function segmentToWall(seg, type, extra, cellPx, offsetX, offsetY) {
  return {
    id: wallId(),
    kind: "wall",
    type,
    p1: { x: lineToPx(seg.a.x, cellPx, offsetX), y: lineToPx(seg.a.y, cellPx, offsetY) },
    p2: { x: lineToPx(seg.b.x, cellPx, offsetX), y: lineToPx(seg.b.y, cellPx, offsetY) },
    ...extra,
  };
}

/**
 * Chartdown's `blockers` are the solid wall runs; `portals` are openings punched through
 * them. Atlas has no notion of a hole in a wall, so a portal is a wall of type "door"
 * laid over the same segment — which is exactly what Atlas's own door tool does (see
 * `confirmDoorPlacement` in the bundle: it deletes the wall and re-adds the flanks plus a
 * `door` segment between them).
 */
function wallsToAtlas(scene, { cellPx, offsetX, offsetY }) {
  const walls = [];
  for (const seg of scene.walls.blockers) {
    walls.push(segmentToWall(seg, "solid", {}, cellPx, offsetX, offsetY));
  }
  for (const portal of scene.walls.portals) {
    // Chartdown marks a portal closed when the door word carries a closed-ish state.
    // Default to closed, which is the safe direction: a door that blocks is a door the
    // players have to open, and an unopened door is never a lost secret.
    walls.push(
      segmentToWall(portal.seg, "door", { closed: portal.closed !== false }, cellPx, offsetX, offsetY),
    );
  }
  return walls;
}

/**
 * Tokens. Only in GM mode: a `hidden` token is stripped from a player render by Chartdown
 * itself, and carrying it into the scene would put Vess on the players' screen — which is
 * the one failure this whole pipeline exists to make impossible.
 */
function tokensToAtlas(scene, { cellPx, offsetX, offsetY, mode, ringColor }) {
  const tokens = {};
  let i = 0;
  for (const feature of scene.features) {
    if (feature.section !== "tokens") continue;
    if (feature.flags.includes("hidden") && mode !== "gm") continue;
    const cells = feature.geometry.cells ?? [];
    if (cells.length === 0) continue;
    const at = cellCentreToPx(cells[0], cellPx, offsetX, offsetY);
    tokens[feature.anchor] = {
      id: feature.anchor,
      kind: "character",
      name: feature.label?.text ?? feature.word,
      x: at.x,
      y: at.y,
      size: 1,
      showRing: true,
      ringColor,
      showNameplate: true,
    };
    i += 1;
  }
  return tokens;
}

function buildAtlasMap(scene, { title, background, cellPx, offsetX, offsetY, mode, ringColor }) {
  const grid = {
    ...DEFAULT_GRID,
    size: cellPx,
    offsetX,
    offsetY,
    unitDistance: scene.grid?.unitDistance ?? DEFAULT_GRID.unitDistance,
  };

  const state = {
    schema: "atlas-vtt",
    version: ATLAS_VERSION,
    mapPath: background,
    background,
    title,
    grid,
    objects: {
      tokens: tokensToAtlas(scene, { cellPx, offsetX, offsetY, mode, ringColor }),
      fog: {},
      pins: {},
      texts: {},
      drawings: {},
      walls: wallsToAtlas(scene, { cellPx, offsetX, offsetY }),
      lights: [],
    },
    camera: null,
    widgetValues: {},
    widgetSettings: {},
    dmNotePath: null,
    tokenSettings: {
      showNameplates: true,
      showHPBars: true,
      showStressBars: false,
      showInstanceBadges: true,
      tokenRingSize: 1,
    },
    initiative: [],
    initiativeTrackerOpen: false,
    diceLog: [],
    pinnedNotePreviews: [],
    lootRoller: null,
  };

  // Atlas stores the zustand envelope `{state, version}`, NOT the bare state. The original
  // atlas_to_map.py fixture was a bare MapFile, which Atlas never writes — the parser
  // passed 18 tests while refusing every real scene. Do not repeat that.
  return { state, version: ATLAS_VERSION };
}

function parseArgs(argv) {
  const opts = { out: null, background: null, cellPx: 100, offsetX: 0, offsetY: 0, mode: "gm", ringColor: "#1E7F74", force: false, fresh: false };
  const rest = [];
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "-o" || a === "--out") opts.out = argv[++i];
    else if (a === "--background") opts.background = argv[++i];
    else if (a === "--cell-px") opts.cellPx = Number(argv[++i]);
    else if (a === "--offset-x") opts.offsetX = Number(argv[++i]);
    else if (a === "--offset-y") opts.offsetY = Number(argv[++i]);
    else if (a === "--mode") opts.mode = argv[++i];
    else if (a === "--ring-color") opts.ringColor = argv[++i];
    else if (a === "--force") opts.force = true;
    else if (a === "--fresh") opts.fresh = true;
    else if (a === "-h" || a === "--help") opts.help = true;
    else rest.push(a);
  }
  opts.input = rest[0];
  return opts;
}

const USAGE = `chartdown_to_atlas.mjs — Chartdown map document -> Atlas VTT scene

  node scripts/chartdown_to_atlas.mjs <map.cd> -o <out.atlasmap> [options]

  --background <path>   vault-relative path to the map JPEG Atlas draws under the grid
  --cell-px <n>         pixels per cell (default 100)
  --offset-x <n>        grid origin x in pixels (default 0)
  --offset-y <n>        grid origin y in pixels (default 0)
  --mode player|gm      hidden tokens are dropped unless gm (default gm)
  --ring-color <#hex>   token ring colour (default #1E7F74)
  --force               replace tokens in an existing scene (default: keep them)
  --fresh               build a new scene, ignoring any existing one entirely
`;

/**
 * Scene state this script does NOT own, and therefore must never destroy.
 *
 * Chartdown knows about walls and tokens. It knows nothing about fog the GM painted at
 * the table, note pins, freehand drawings, lights, text labels, the camera, or the
 * initiative order. Those are hours of GM work sitting in the same file.
 *
 * This function came from getting that wrong: on 2026-09-30 this script overwrote a live
 * Bow's End scene that had a hand-painted fog stroke in it, and because `.atlasmap` files
 * were not yet tracked by git, the stroke was gone. So: read the existing scene, and
 * carry forward everything not in `OWNED`.
 */
const OWNED = new Set(["walls", "tokens"]);

function carryForward(existing, fresh) {
  if (!existing) return { scene: fresh, carried: [] };
  const oldObjects = existing?.state?.objects ?? {};
  const carried = [];
  for (const key of Object.keys(oldObjects)) {
    if (OWNED.has(key)) continue;
    const value = oldObjects[key];
    const isEmpty =
      value == null ||
      (Array.isArray(value) && value.length === 0) ||
      (typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0);
    if (isEmpty) continue;
    fresh.state.objects[key] = value;
    carried.push(key);
  }
  // Camera and initiative are top-level, not under objects, and are equally un-owned.
  for (const key of ["camera", "initiative", "initiativeTrackerOpen", "diceLog", "pinnedNotePreviews", "lootRoller", "widgetValues", "widgetSettings", "dmNotePath"]) {
    const value = existing?.state?.[key];
    if (value == null) continue;
    // A `false` flag is not GM work; carrying it just makes the report noisy.
    if (value === false) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    if (typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0) continue;
    fresh.state[key] = value;
    if (!carried.includes(key)) carried.push(key);
  }
  return { scene: fresh, carried };
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help || !opts.input) {
    process.stdout.write(USAGE);
    process.exit(opts.help ? 0 : 1);
  }
  if (!opts.background) {
    process.stderr.write("error: --background is required; Atlas needs an image to draw the grid over\n");
    process.exit(1);
  }

  const source = readFileSync(opts.input, "utf8");
  const { document, diagnostics } = parse(source);

  // Chartdown's own diagnostics are errors-or-nothing: do not emit a scene from a document
  // the parser rejected. A half-converted map is worse than no map.
  const errors = (diagnostics ?? []).filter((d) => d.severity === "error");
  if (errors.length > 0) {
    for (const e of errors) process.stderr.write(`error: ${opts.input}:${e.line}: ${e.message}\n`);
    process.exit(1);
  }
  for (const w of (diagnostics ?? []).filter((d) => d.severity === "warning")) {
    process.stderr.write(`warning: ${opts.input}:${w.line}: ${w.message}\n`);
  }

  const scene = resolveScene(document, { mode: opts.mode });
  const built = buildAtlasMap(scene, {
    title: document.title,
    background: opts.background,
    cellPx: opts.cellPx,
    offsetX: opts.offsetX,
    offsetY: opts.offsetY,
    mode: opts.mode,
    ringColor: opts.ringColor,
  });

  const out = opts.out ?? opts.input.replace(/\.cd$/, ".atlasmap");

  // Never silently destroy a scene. Fog, pins, drawings, lights, camera and initiative
  // are GM work that lives in this file and that Chartdown has no opinion about.
  let existing = null;
  if (!opts.fresh && existsSync(out)) {
    try {
      existing = JSON.parse(readFileSync(out, "utf8"));
    } catch (err) {
      process.stderr.write(`error: ${out} exists but is not valid JSON (${err.message}).\n`);
      process.stderr.write("       Move it aside, or pass --fresh to replace it deliberately.\n");
      process.exit(1);
    }
  }

  let atlasMap = built;
  let carried = [];
  let keptTokens = 0;
  if (existing) {
    ({ scene: atlasMap, carried } = carryForward(existing, built));
    // Tokens are ours to own, but the GM may have dragged portraits onto the board. Keep
    // theirs unless told otherwise; --force is the explicit "the .cd file is the truth".
    if (!opts.force) {
      const oldTokens = existing?.state?.objects?.tokens ?? {};
      const oldIds = new Set(Object.keys(oldTokens));
      for (const [id, tok] of Object.entries(atlasMap.state.objects.tokens)) {
        if (!oldIds.has(id)) continue;
        atlasMap.state.objects.tokens[id] = { ...oldTokens[id], ...tok, x: tok.x, y: tok.y };
        keptTokens += 1;
      }
    }
  }

  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, `${JSON.stringify(atlasMap, null, 2)}\n`, "utf8");

  const walls = atlasMap.state.objects.walls;
  const doors = walls.filter((w) => w.type === "door").length;
  const tokens = Object.keys(atlasMap.state.objects.tokens).length;
  process.stdout.write(
    `wrote ${out}\n  ${basename(out)}: ${scene.extent.w}x${scene.extent.h} cells, ` +
      `${walls.length - doors} solid walls, ${doors} doors, ${tokens} tokens (mode: ${opts.mode})\n`,
  );
  if (carried.length > 0) {
    process.stdout.write(`  carried forward from the existing scene: ${carried.join(", ")}\n`);
  }
  if (keptTokens > 0) {
    process.stdout.write(
      `  kept ${keptTokens} existing token(s); pass --force to let the .cd file overwrite them\n`,
    );
  }
}

main();
