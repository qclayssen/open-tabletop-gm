"""
map_to_atlas.py: push an engine battle map into an Atlas VTT vault.

Run: python3 scripts/map_to_atlas.py <map> [<map> ...]

`atlas_to_map.py` goes the other way and is the seam this project settled on:
Atlas paints, the engine decides. This script is its counterpart, and it is
strictly one-way too. It writes files and never reads an encounter back, so
there is still exactly one authority for a fight.

    display/maps/<name>.json  ->  <vault>/atlas-vtt/collections/<coll>/<Name>.atlasmap
                              +  .../scenes/<name>.json   (the scene's index sidecar)
                              +  .../maps/<name>.<ext>    (the artwork, copied in)
                              +  .../tokens/<colour>.png  (one disc per token colour)

Why files and not a plugin API: Atlas exposes no headless entry point. It has
no URI scheme, no local server and no command-line. What it does have is a
vault reconciler -- `reconcileIndex` -> `adoptUnindexedFiles` walks every file
in the vault and adopts any `.atlasmap` no record owns, creating the record
itself (`vault-sync/assetAdoption.ts`). The same pass adopts images in a
collection's `tokens/` folder as token assets. Atlas watches the vault for
`create`, `rename` and `delete` events behind a 1s debounce, so a file written
from a shell shows up in the scene browser on its own.

That is the whole integration surface, and it is why this script writes only
files: it never touches `assets-metadata.json`. Atlas builds its own index from
what it finds, so there is no shared document to keep in sync and no way for a
half-written index to lose a fight.

What this CANNOT carry, and does not pretend to:

  * terrain. Atlas has no terrain vocabulary at all, so `features[]` is dropped.
    A water square painted in the engine is floor in the Atlas scene.
  * the diagonal rule. Our maps carry `diagonals` ("5" or "5-10-5"); Atlas has
    no such field, and its own dnd5e preset is "5-10-5" while our default is
    "5". A map whose rule is "5" is silently played under the other one in
    Atlas. This is why `--diagonals` is reported, not carried.
  * HP, AC, conditions, initiative. Those live in the encounter, not in the map
    file, and the engine owns them. A token here is a *position and a name*.
    Nothing in this script sets `hasVision` either: Atlas draws its own fog
    from vision sources, and letting it would be a second fog authority.

So a scene written here is a **static layout preview** -- useful for setting up
in front of players, for showing a player view on a second machine, and for
handing a map to someone who has Atlas and not this engine. It is not a
running encounter, and the engine stays the only thing that adjudicates one.

Usage:
    python3 scripts/map_to_atlas.py mage-tower
    python3 scripts/map_to_atlas.py mage-tower --vault ~/atlas-vault --collection Strixhaven
    python3 scripts/map_to_atlas.py mage-tower --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import struct
import sys
import zlib
from pathlib import Path

_MAPS = pathlib.Path(__file__).resolve().parents[1] / "display" / "maps"
_SCRIPTS = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

ATLAS_SCHEMA = "atlas-vtt"
# MapPersistence.ts: ATLAS_VERSION. Written into the file so a future Atlas
# migrates it rather than misreading it; the reconciler adopts the file as it
# stands and only rewrites it once the scene is opened in the UI.
ATLAS_VERSION = 4

ATLAS_DIR = "atlas-vtt"
COLLECTIONS_DIR = f"{ATLAS_DIR}/collections"

# The engine is square-only (grid.py) and so is this: an Atlas hex scene would
# be axial (q, r) with no rectangular row-string to land in.
SQUARE_FT = 5

# Token side colours, from the reference page the display's palette is taken
# from (`display/static/reference/strixhaven_map_table.html`, `:root`). These
# are the same keys `spawns[].color` uses, so a token looks the same here as it
# does on the display. An unknown key is a cosmetic problem, not a wrong
# distance, so it gets a neutral and a warning rather than a refusal.
TOKEN_COLOURS = {
    "lore": "#B8752E",    # Lorehold
    "pris": "#B03A48",    # Prismari
    "quan": "#1E7F74",    # Quandrix
    "silv": "#5A6475",    # Silverquill
    "with": "#5E7A3A",    # Witherbloom
    "cent": "#5A6A72",    # Central
    "brass": "#A87A26",   # Object
    "danger": "#A33A3A",  # Enemy
}
NEUTRAL_COLOUR = "#6E5A3A"   # --tx-muted

# Artwork size. Atlas masks every token to a circle of its own diameter
# (SpriteFactory.ts: `sprite.mask = circleMask`, radius tokenSize/2), so a disc
# is the shape the file wants to be anyway, and it needs no alpha-aware source.
TOKEN_PX = 256


# ─── the token discs ──────────────────────────────────────────────────────────

def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"not a hex colour: {hex_colour!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def disc_png(hex_colour: str, px: int = TOKEN_PX, rim: float = 0.06) -> bytes:
    """A filled disc with a darker rim, as a PNG. Standard library only.

    Pillow is not a dependency of the engine (`atlas_to_map.py` reads JPEG and
    PNG headers by hand for the same reason), so the PNG is assembled here:
    zlib for the IDAT, struct for the chunks, and a distance test per pixel.
    Edges are antialiased by 4x4 supersampling, which matters here because
    Atlas scales the disc down to a grid cell and a hard jaggy rim is visible
    at that size.
    """
    r, g, b = _rgb(hex_colour)
    # A rim reads as the ring a tabletop token has, and separates a dark token
    # from dark map art. Darkened rather than lightened so it works on a light
    # parchment map as well as a dark one.
    rim_px = max(1.0, px * rim)
    radius = px / 2.0
    inner = radius - rim_px
    centre = (px - 1) / 2.0
    samples = 4
    step = 1.0 / samples

    rows = bytearray()
    for y in range(px):
        rows.append(0)                                   # PNG filter type 0
        for x in range(px):
            covered = 0.0
            inner_hits = 0
            for sy in range(samples):
                for sx in range(samples):
                    dx = x + (sx + 0.5) * step - centre - 0.5
                    dy = y + (sy + 0.5) * step - centre - 0.5
                    if dx * dx + dy * dy <= radius * radius:
                        covered += 1.0
                        if dx * dx + dy * dy <= inner * inner:
                            inner_hits += 1
            if covered == 0:
                rows += b"\x00\x00\x00\x00"
                continue
            # Blend fill and rim by how much of the covered area is inside.
            t = inner_hits / covered
            alpha = round(covered / (samples * samples) * 255)
            rows += bytes((
                round(r * t + r * 0.45 * (1 - t)),
                round(g * t + g * 0.45 * (1 - t)),
                round(b * t + b * 0.45 * (1 - t)),
                alpha,
            ))

    ihdr = struct.pack(">IIBBBBB", px, px, 8, 6, 0, 0, 0)   # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + _png_chunk(b"IEND", b""))


# ─── formations ───────────────────────────────────────────────────────────────

def apply_formations(spec: dict, camp_dir: Path, names, *,
                     at=None, centre: bool = False) -> tuple:
    """`(spec_with_creatures, report)`. Formations are the creatures; the map is
    the room.

    This is the seam that unblocks BV9, and it is worth being precise about why.
    BV9's blocker was recorded as "the blocker is pixels, not code": every map
    with artwork had no creatures to link a statblock to, and every map with
    creatures was gridless and hand-written, so no scene could carry a
    `statblockPath` and the link path — though proven against the real 372-note
    bestiary — had nothing to attach to. Importing artwork was the stated fix.

    A formation is the other fix, and it does not need a single pixel. A
    formation is campaign data that applies to *any* map, so it breaks the
    disjointness: give `biblioplex-stacks` (terrain, grid, no art) a formation of
    kobolds, and the scene has creatures, and every kobold links to a real note.
    What it costs is stated plainly: the scene sits on Atlas's placeholder
    background, because the one thing a formation cannot supply is a picture.

    The placement is `tactics.formations.positions` — **the engine's own
    function, not a second implementation of it.** A preview that placed tokens
    differently from the fight would be worse than no preview, and the way to
    guarantee they cannot differ is to have exactly one placement function with
    two callers. That is also why this takes a formation's creatures rather than
    re-deriving anything: `build_scene` is left completely untouched, and its 38
    existing tests still describe it exactly.

    The returned spec is a **copy**; the map file on disk is never written, and
    the formation store is never written. Both directions of this project stay
    one-way.
    """
    if not names:
        return spec, []
    from tactics import formations as F
    from tactics.grid import Grid
    from tactics.maps import compile_map

    # The grid the *engine* would build from this map, not a re-read of the
    # JSON: if the two ever disagreed, the preview would place tokens on squares
    # the fight does not have.
    board = Grid.from_dict(compile_map(spec)["grid"])
    out = dict(spec)
    spawns = list(spec.get("spawns") or [])
    used = {str(s.get("id")) for s in spawns}
    report = []

    for name in names:
        f = F.load(camp_dir, name)
        plan = F.positions(f, board, at=at, centre=centre)
        for i, p in enumerate(plan["placements"], start=1):
            tid = f"{F.slug(f['name'])}-{i}"
            while tid in used:                       # Atlas keys tokens by id
                tid = f"{tid}x"
            used.add(tid)
            # `label` is what the token was called on the board ("Kobold 2") and
            # makes a better nameplate; `name` is the bare creature ("Kobold") and
            # is what the statblock fold needs. Prefer the label, fall back for a
            # member saved before v2.
            spawns.append({"id": tid, "name": p.get("label") or p["name"],
                           "color": p["color"], "x": p["x"], "y": p["y"]})
        report.append({
            "formation": f["name"],
            "mode": plan["mode"],
            "tokens": len(plan["placements"]),
            "blocked": [f"{b['name']} at {b['x']},{b['y']}" for b in plan["blocked"]],
            "off_map": [f"{b['name']} wanted {b['was'][0]},{b['was'][1]}"
                        for b in plan["off_map"]],
            "separated": [f"{b['name']} wanted {b['was'][0]},{b['was'][1]}, which "
                          f"another monster had" for b in plan["separated"]],
        })
    out["spawns"] = spawns
    return out, report


# ─── the scene ───────────────────────────────────────────────────────────────

def load_map(name: str, maps_dir: Path) -> dict:
    path = maps_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"no map {name!r} in {maps_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def token_image_name(colour: str) -> str:
    """A safe, stable file name for a side's disc."""
    slug = re.sub(r"[^a-z0-9]+", "-", colour.strip().lower()).strip("-")
    return f"{slug or 'token'}.png"


# ─── real token art ──────────────────────────────────────────────────────────

IMAGE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def find_token_art(spec: dict, spawn: dict, art_dir: Path | None,
                   vault: Path, maps_root: Path) -> Path | None:
    """A real picture for this spawn, or None to fall back to a colour disc.

    Two ways in, in order of reliability:

    1. `token_art` in the map file, `{"<spawn id>": "<path>"}`. This is the one
       to reach for, because generated art rarely arrives named after the
       monster — a vault's real portrait is often `XW4tQg9_1790718416294.webp`
       and only the map knows it is the quandrix student. The value may be an
       absolute path, a path already inside the Atlas vault (referenced where it
       lies), or a path relative to the maps dir (copied in).
    2. A file in `--token-art` named after the spawn: `Quandrix guard` matches
       `quandrix-guard.webp`, `.png`, `.jpg` or `.jpeg`.

    Nothing is returned that does not exist, so a typo falls back to the disc
    and is reported rather than producing a token whose image Atlas cannot load.
    """
    tid = str(spawn.get("id") or "").strip()
    name = str(spawn.get("name") or tid)
    declared = (spec.get("token_art") or {}).get(tid)
    if declared:
        raw = Path(str(declared)).expanduser()
        for candidate in ([raw] if raw.is_absolute()
                          else [vault / raw, maps_root / raw]):
            if candidate.exists():
                return candidate
    if art_dir and art_dir.is_dir():
        for suffix in IMAGE_SUFFIXES:
            candidate = art_dir / f"{slug(name)}{suffix}"
            if candidate.exists():
                return candidate
    return None


def build_scene(spec: dict, map_id: str, background: str | None,
                tokens: dict[str, dict]) -> dict:
    """The `MapFile` Atlas persists, ready to be wrapped in its envelope.

    Every field here is Atlas's own, read off `MapPersistence.ts` (`MapFile`,
    `GridState`, `BaseToken`) and `types.ts` (`Character`) at tag 0.4.1.
    """
    grid_in = spec.get("grid") or {}
    cell = float(grid_in.get("cell_px") or 0)
    if cell <= 0:
        raise ValueError(
            f"map {map_id!r} has no grid cell size; a scene is a picture with a "
            "grid over it, so give the map an image and its grid alignment first "
            "(see scripts/art_import.py)")

    # `GridState` requires enabled/size/offsetX/offsetY/opacity; the rest are
    # Atlas's own defaults for a square 5 ft grid, restated so the file does not
    # depend on which defaults a future version picks.
    grid = {
        "enabled": True,
        "visible": True,
        "snapToGrid": True,
        "type": "square",
        "size": cell,
        "offsetX": round(float(grid_in.get("offset_x") or 0), 2),
        "offsetY": round(float(grid_in.get("offset_y") or 0), 2),
        "color": "#000000",
        "opacity": 0.35,
        "lineType": "solid",
        "lineWidth": 1,
        "unitType": "feet",
        "unitDistance": SQUARE_FT,
        "measurementType": "units",
    }

    placed: dict[str, dict] = {}
    for spawn in spec.get("spawns") or []:
        tid = str(spawn.get("id") or "").strip()
        if not tid:
            raise ValueError(f"map {map_id!r} has a spawn with no id: {spawn!r}")
        if tid in placed:
            raise ValueError(
                f"map {map_id!r} has two spawns with id {tid!r}; Atlas keys tokens "
                "by id, so the second would silently replace the first")
        col, row = int(spawn["x"]), int(spawn["y"])
        width, height = int(spec["width"]), int(spec["height"])
        if not (0 <= col < width and 0 <= row < height):
            raise ValueError(
                f"map {map_id!r}: spawn {tid!r} at {col},{row} is off the "
                f"{width}x{height} map. A token outside the grid is a token in "
                "the wrong place, which is the failure this script refuses")

        colour = str(spawn.get("color") or "").strip().lower()
        # `tokens` is seeded from the spawn colours with "" filtered out, so a
        # colour missing from it IS the empty string. That is a bare `KeyError: ''`
        # with no message -- the only failure in this function that does not say
        # what went wrong or what to do about it, and a spawn with no colour is the
        # most likely thing a map author gets wrong.
        if colour not in tokens:
            raise ValueError(
                f"map {map_id!r}: spawn {tid!r} has no color. Every spawn needs one "
                f"so its token can be told from the rest; add a \"color\" naming a "
                f"side, or any value -- an unrecognised one still gets a disc, and "
                f"is reported as unknown rather than refused")
        entry = tokens[colour]
        # Real art wins over the side's disc; a spawn with neither is refused
        # below rather than written with a path Atlas cannot load.
        image = entry.get("per_spawn", {}).get(tid) or entry.get("vault_path")
        if not image:
            raise ValueError(
                f"map {map_id!r}: spawn {tid!r} has no image. Give it one in the "
                f"map's `token_art` block, or in --token-art, or let it fall back "
                f"to the {colour!r} disc")
        token: dict = {
            "id": tid,
            # `kind: 'character'` rather than 'token' only so the name can be
            # carried and shown. No hp is set, and that is deliberate: Atlas
            # shows no HP bar without one, which is the correct answer here
            # because HP belongs to the engine's encounter, not to a map.
            "kind": "character",
            "name": str(spawn.get("name") or tid),
            # Token x/y is the CENTRE of the token in world pixels, snapped by
            # `GridSystem.snapToCellCenter`: col * size + offsetX + size / 2.
            "x": round(grid["offsetX"] + (col + 0.5) * grid["size"], 2),
            "y": round(grid["offsetY"] + (row + 0.5) * grid["size"], 2),
            "imagePath": image,
            "size": 1,
            "showRing": True,
            "ringColor": TOKEN_COLOURS.get(colour, NEUTRAL_COLOUR),
            # The nameplate is the one thing that makes a preview worth
            # looking at; Atlas defaults it off.
            "showNameplate": True,
        }
        statblock = tokens[colour].get("statblocks", {}).get(str(spawn.get("name") or ""))
        if statblock:
            # Linking a statblock gives Atlas a nameplate fallback and a click
            # through to the note. It also means Atlas can draw an HP bar from
            # the note's printed HP, which is NOT the engine's HP for this
            # fight -- see the module docstring and --no-statblocks.
            token["statblockPath"] = statblock["vault_path"]
            token["statblockName"] = str(spawn.get("name") or "")
        placed[tid] = token

    return {
        "schema": ATLAS_SCHEMA,
        "version": ATLAS_VERSION,
        # `title`, not `name`: `MapPersistence.ts` reads the scene's display name
        # from `title`, so a scene carrying only `name` opens untitled. `name` is
        # kept alongside it because the sidecar in `scenes/<id>.json` is what the
        # asset index reads the name from, and the two files are read by different
        # code paths.
        "title": spec.get("name") or map_id,
        "name": spec.get("name") or map_id,
        # Atlas stores the background image twice. `mapPath` is what the asset
        # index resolves the scene against, `background` is what the renderer
        # paints; a scene with only one of them shows a grid and no art.
        "mapPath": background,
        "background": background,
        "grid": grid,
        "objects": {
            "tokens": placed,
            "fog": {},
            "pins": {},
            "texts": {},
            "drawings": {},
            # Arrays, not dicts. `MapPersistence.ts:472` reads these straight into
            # the store and `WallRenderer` iterates them, so `{}` iterates as zero
            # walls only by luck of the empty case and breaks on the first segment.
            "walls": [],
            "lights": [],
        },
        "camera": None,
        # Atlas's own defaults. Left unset, a scene opens with fog on, widgets
        # showing and a saved camera, none of which a fresh export wants.
        "widgetValues": {},
        "widgetSettings": {},
        "dmNotePath": None,
        "tokenSettings": {
            "showNameplates": True, "showHPBars": True,
            "showStressBars": False, "showInstanceBadges": True,
            "tokenRingSize": 1,
        },
        "initiative": [],
        "initiativeTrackerOpen": False,
        "diceLog": [],
        "pinnedNotePreviews": [],
        "lootRoller": None,
    }


def envelope(scene: dict) -> dict:
    """Wrap in the zustand `persist` envelope, the way Atlas stores it.

    `MapLoader.ts` unwraps `state` before reading, and accepts a bare MapFile
    too; the envelope is written because it is what Atlas itself writes, so a
    file we produce is indistinguishable from one the UI made.
    """
    return {"state": scene, "version": ATLAS_VERSION}


# ─── bestiary links ──────────────────────────────────────────────────────────

def _fold(name: str) -> str:
    """A comparison key for a monster name, loose enough for how maps write them.

    A spawn's name is a label at the table, and maps write it three ways that all
    mean the same creature: with different case ("Giant frog" vs the SRD's "Giant
    Frog"), with an instance number ("Kobold 1", "Kobold 2" — the normal way to
    place two of the same thing), and in the plural ("Stirges" for one "Stirge").
    Matching on the literal name finds none of those.
    """
    text = name.casefold().strip()
    text = re.sub(r"\s*#\s*\d+\s*$", "", text)          # "Kobold #2"
    text = re.sub(r"\s*\d+\s*$", "", text)              # "Kobold 2"
    text = re.sub(r"\s*\(\s*[a-z0-9]+\s*\)\s*$", "", text)   # "Kobold (b)"
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def bestiary_index(vault: Path) -> dict[str, str]:
    """`_fold`ed monster name -> vault path, for every note actually present.

    The folder is the source of truth rather than a name we construct: it means
    a link is only ever returned for a file that exists, and the folding above
    does the matching. Scanned once per export, not once per token.
    """
    index: dict[str, str] = {}
    for folder in ("Bestiary", "bestiary", "NPCs", "npcs", "Characters", "characters"):
        directory = vault / folder
        if not directory.is_dir():
            continue
        for note in sorted(directory.glob("*.md")):
            index.setdefault(_fold(note.stem), f"{folder}/{note.name}")
    return index


# `NPCs/` and `Characters/` are here, not just `Bestiary/`, and the reason is
# Atlas's behaviour rather than a preference. Atlas watches the vault and rewrites
# any note in its **Bestiary Folder** that a token already references: it wrote nine
# of the NPC notes as `BestiaryAdrixmdAdrix.md` with `name:` set to
# `[[Bestiary/Adrix.md|Adrix]]`, on every run, and the mangling is unrecoverable
# because the note's own `name` had become a link to itself. Those folders are
# outside the Bestiary Folder, so they are left alone, and scanning them here is
# what keeps a token's `statblockPath` resolving to a file that exists.


def bestiary_note(vault: Path, name: str, index: dict[str, str] | None = None) -> str | None:
    """Vault path of a Fantasy Statblocks note for `name`, if one is there.

    The path always comes from the folder listing rather than from a name we
    construct, so the returned path is byte-identical to a file that exists. That
    matters more than it looks: a case-insensitive filesystem happily resolves
    `Bestiary/Giant frog.md` when the real file is `Giant Frog.md`, and the link
    would then break the first time the vault is synced somewhere case-sensitive
    (Obsidian Sync, a Linux checkout, git).

    Three probes, most explicit first: the name as folded, the same with a
    trailing plural dropped ("Stirges" -> "Stirge"), and the plural added.

    Atlas recognises a statblock by `statblock: true` frontmatter or a
    ```statblock fence, which is exactly what `export_bestiary.py` emits
    (`statblockFrontmatter.ts`).
    """
    if index is None:
        index = bestiary_index(vault)
    key = _fold(name)
    for probe in (key, key.rstrip("s"), key + "s"):
        if probe in index:
            return index[probe]
    return None


# ─── writing ──────────────────────────────────────────────────────────────────

def scene_file_name(spec: dict, map_id: str) -> str:
    raw = str(spec.get("name") or map_id)
    # A collection folder is a folder: no slashes, and nothing Obsidian rejects.
    cleaned = re.sub(r'[\\/:*?"<>|#^\[\]]', "-", raw).strip().lstrip(".").strip()
    return f"{cleaned or map_id}.atlasmap"


def export(spec: dict, map_id: str, vault: Path, collection: str,
           maps_root: Path, link_statblocks: bool = True,
           allow_no_image: bool = False, art_dir: Path | None = None) -> dict:
    """Write the scene, its sidecar, the artwork and the discs. Returns a report."""
    image = spec.get("image")
    source: Path | None = None
    if image:
        # `image` is vault-relative to the map file, e.g. "images/crypt.jpg", so
        # it resolves against the maps dir itself -- not against maps/images.
        source = maps_root / image
        if not source.exists():
            raise FileNotFoundError(
                f"map {map_id!r} points at {image!r}, which is not under {maps_root}. "
                "The artwork is the scene; a map whose art is missing is not a map")
    elif not allow_no_image:
        raise ValueError(
            f"map {map_id!r} has no image. A battle map in Atlas is a picture with "
            "a grid over it, and a scene with no background is a placeholder. "
            "Import the art first (scripts/art_import.py), or --allow-no-image to "
            "write the layout anyway")

    coll_dir = vault / COLLECTIONS_DIR / collection
    maps_dir = coll_dir / "maps"
    tokens_dir = coll_dir / "tokens"
    scenes_dir = coll_dir / "scenes"

    # One disc per distinct side colour, shared by every token in the scene.
    # `adoptTokenArtwork` picks images up from a collection's `tokens/` folder
    # as token assets, so these show up in the collection's Characters tab too.
    colours = sorted({str(s.get("color") or "").strip().lower()
                      for s in (spec.get("spawns") or [])} - {""})
    unknown = [c for c in colours if c not in TOKEN_COLOURS]
    tokens: dict[str, dict] = {
        c: {"vault_path": None, "per_spawn": {}, **({"statblocks": {}} if link_statblocks else {})}
        for c in colours
    }
    written_discs: list[str] = []
    art_sources: dict[str, str] = {}
    discs_needed: set[str] = set()

    for spawn in spec.get("spawns") or []:
        colour = str(spawn.get("color") or "").strip().lower()
        tid = str(spawn.get("id") or "").strip()
        if colour not in tokens:
            # build_scene refuses this a moment later, with a message that names
            # the spawn. Skipping the art here is fine; skipping it silently and
            # then failing on an empty-string key is not.
            continue
        found = find_token_art(spec, spawn, art_dir, vault, maps_root)
        if found is None:
            discs_needed.add(colour)
            continue
        # Art already inside the vault is referenced where it lies; anything else
        # is copied in beside the discs, so the collection owns its token art.
        # `as_posix` because this string goes into the scene document and Atlas
        # is not a Windows program: `str(relative)` writes `atlas-vtt\assets\...`
        # there, and the same scene exported on another machine no longer matches
        # the file it names. Every other path in this module is built with "/"
        # for the same reason.
        try:
            relative = found.resolve().relative_to(vault.resolve())
            vault_path = relative.as_posix()
        except ValueError:
            art_dir_out = tokens_dir / "art"
            art_dir_out.mkdir(parents=True, exist_ok=True)
            dest = art_dir_out / f"{slug(tid) or slug(str(spawn.get('name') or tid))}{found.suffix.lower()}"
            if not dest.exists() or dest.read_bytes() != found.read_bytes():
                shutil.copy2(found, dest)
            vault_path = f"{COLLECTIONS_DIR}/{collection}/tokens/art/{dest.name}"
        tokens[colour]["per_spawn"][tid] = vault_path
        art_sources[tid] = found.name

    # One disc per side colour that still needs one, shared by every token on
    # that side. `adoptTokenArtwork` picks images up from a collection's
    # `tokens/` folder as token assets, so these show up in its Characters tab.
    for colour in sorted(discs_needed):
        name = token_image_name(colour)
        disc = tokens_dir / name
        if not disc.exists():
            disc.parent.mkdir(parents=True, exist_ok=True)
            disc.write_bytes(disc_png(TOKEN_COLOURS.get(colour, NEUTRAL_COLOUR)))
            written_discs.append(f"{COLLECTIONS_DIR}/{collection}/tokens/{name}")
        tokens[colour]["vault_path"] = f"{COLLECTIONS_DIR}/{collection}/tokens/{name}"

    if link_statblocks:
        # Built once, not per token: the Bestiary folder is scanned and folded
        # a single time however many spawns the map has.
        notes = bestiary_index(vault)
        for spawn in spec.get("spawns") or []:
            colour = str(spawn.get("color") or "").strip().lower()
            name = str(spawn.get("name") or "")
            if colour not in tokens or "statblocks" not in tokens[colour]:
                continue
            note = bestiary_note(vault, name, notes)
            if note:
                tokens[colour]["statblocks"][name] = {"vault_path": note}

    # The artwork is copied in rather than referenced, because a token image and
    # a background are both vault-relative paths and the campaign vault is not
    # the Atlas vault.
    background: str | None = None
    if source is not None:
        maps_dir.mkdir(parents=True, exist_ok=True)
        art = maps_dir / f"{map_id}{source.suffix.lower()}"
        if not art.exists() or art.read_bytes() != source.read_bytes():
            shutil.copy2(source, art)
        background = f"{COLLECTIONS_DIR}/{collection}/maps/{art.name}"

    scene = build_scene(spec, map_id, background, tokens)
    scene_name = scene_file_name(spec, map_id)
    scene_path = coll_dir / scene_name
    scene_path.write_text(json.dumps(envelope(scene), indent=1) + "\n", encoding="utf-8")

    # The sidecar is what gives the scene its real name. `adoptSceneJson` runs
    # before `adoptSceneMap` in `adoptUnindexedFiles` and keeps `name` and
    # `tags` from the payload; without it the scene is named after its file.
    scenes_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {
        "name": spec.get("name") or map_id,
        "tags": ["open-tabletop-gm"],
        "mapPath": f"{COLLECTIONS_DIR}/{collection}/{scene_name}",
    }
    sidecar_path = scenes_dir / f"{map_id}.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=1) + "\n", encoding="utf-8")

    linked = sum(1 for t in scene["objects"]["tokens"].values() if "statblockPath" in t)
    return {
        "map": map_id,
        "scene": f"{COLLECTIONS_DIR}/{collection}/{scene_name}",
        "sidecar": f"{COLLECTIONS_DIR}/{collection}/scenes/{map_id}.json",
        "artwork": background,
        "discs": written_discs,
        "tokens": len(scene["objects"]["tokens"]),
        "statblocks": linked,
        # Every token's picture is accounted for: real art by file name, or the
        # side's disc. A disc standing in for art that exists somewhere is the
        # one case worth seeing, so it is counted rather than assumed.
        "art": {tid: ("art " + art_sources[tid]) if tid in art_sources
                else "disc " + str(next(s.get("color", "") for s in spec.get("spawns", [])
                                        if str(s.get("id")) == tid)).lower()
                for tid in scene["objects"]["tokens"]},
        "art_matched": len(art_sources),
        "art_discs": len(scene["objects"]["tokens"]) - len(art_sources),
        "unknown_colours": unknown,
        "diagonals": str(spec.get("diagonals", "5")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("maps", nargs="+", help="map ids, as in display/maps/<id>.json")
    parser.add_argument("--vault", type=pathlib.Path,
                        default=pathlib.Path(os.environ.get("ATLAS_VAULT", "~/atlas-vault")).expanduser(),
                        help="the Atlas vault (default: $ATLAS_VAULT or ~/atlas-vault)")
    parser.add_argument("--collection", default="OpenTabletopGM",
                        help="Atlas collection folder to write into (default: %(default)s)")
    parser.add_argument("--maps-dir", type=pathlib.Path, default=_MAPS,
                        help="where the engine's map files live (default: %(default)s)")
    parser.add_argument("--no-statblocks", action="store_true",
                        help="do not link Bestiary notes, so Atlas draws no HP bar")
    parser.add_argument("--token-art", type=pathlib.Path, default=None,
                        help="folder of token images named after the spawn "
                             "(`Quandrix guard.png`); a map's `token_art` block "
                             "overrides this. Anything unmatched gets a colour disc")
    parser.add_argument("--formation", action="append", metavar="NAME", default=[],
                        help="replay a saved monster arrangement onto this map "
                             "(repeatable). The formation supplies the creatures, so "
                             "a map with terrain but no spawns still exports a "
                             "scene with tokens and working statblock links")
    parser.add_argument("--campaign", default=os.environ.get("GM_CAMPAIGN", ""),
                        help="campaign holding the formations (default: $GM_CAMPAIGN)")
    parser.add_argument("--at", metavar="SQ",
                        help="pin the formations' anchor to this square (cell offsets)")
    parser.add_argument("--centre", action="store_true",
                        help="drop the formations in the middle of the map, scaled to it")
    parser.add_argument("--allow-no-image", action="store_true",
                        help="write the layout even without artwork (Atlas shows a "
                             "placeholder background, so this is for tokens only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be written, write nothing")
    args = parser.parse_args(argv)

    vault: Path = args.vault
    reports, failed = [], 0

    # Formations live in a campaign, not in the map file, so they need a campaign
    # to look in. Resolved once, and only when one was actually asked for --
    # `--formation` on a machine with no campaign is a clear error, and a
    # `--campaign` nobody used is not.
    camp_dir: Path | None = None
    if args.formation:
        if not args.campaign:
            print("map_to_atlas: --formation needs --campaign (or $GM_CAMPAIGN) to "
                  "find the formations in", file=sys.stderr)
            return 1
        from paths import find_campaign
        camp_dir = find_campaign(args.campaign, migrate=False)
        if not camp_dir.is_dir():
            print(f"map_to_atlas: campaign folder {camp_dir} not found", file=sys.stderr)
            return 1
    at = None
    if args.at:
        from tactics.grid import parse_square
        try:
            at = parse_square(args.at)
        except ValueError as exc:
            print(f"map_to_atlas: --at {args.at!r}: {exc}", file=sys.stderr)
            return 1

    for map_id in args.maps:
        try:
            spec = load_map(map_id, args.maps_dir)
            formations_report = []
            if args.formation:
                spec, formations_report = apply_formations(
                    spec, camp_dir, args.formation, at=at, centre=args.centre)
            if args.dry_run:
                # Validate everything except the writes, so a dry run that
                # succeeds means the real run will too.
                image = spec.get("image")
                if image:
                    if not (args.maps_dir / image).exists():
                        raise FileNotFoundError(
                            f"map {map_id!r} points at {image!r}, which is not "
                            f"under {args.maps_dir}")
                elif not args.allow_no_image:
                    raise ValueError(
                        f"map {map_id!r} has no image; import the art first, or "
                        "pass --allow-no-image")
                build_scene(spec, map_id, "(dry-run)", {
                    c: {"vault_path": token_image_name(c)} for c in
                    {str(s.get('color') or '').strip().lower()
                     for s in (spec.get("spawns") or [])}
                })
                reports.append({
                    "map": map_id,
                    "scene": f"{COLLECTIONS_DIR}/{args.collection}/"
                             f"{scene_file_name(spec, map_id)}",
                    "artwork": image,
                    "tokens": len(spec.get("spawns") or []),
                    "diagonals": str(spec.get("diagonals", "5")),
                    "unknown_colours": sorted(
                        {str(s.get("color") or "").strip().lower()
                         for s in (spec.get("spawns") or [])}
                        - set(TOKEN_COLOURS) - {""}),
                })
                continue
            reports.append(export(spec, map_id, vault, args.collection, args.maps_dir,
                                  link_statblocks=not args.no_statblocks,
                                  allow_no_image=args.allow_no_image,
                                  art_dir=args.token_art))
            if formations_report:
                reports[-1]["formations"] = formations_report
        except (ValueError, FileNotFoundError, KeyError) as exc:
            print(f"{map_id}: {exc}", file=sys.stderr)
            failed += 1

    if args.dry_run:
        print(json.dumps(reports, indent=1))
        print(f"\nDry run. Nothing written. Target would be {vault}")
    else:
        for r in reports:
            print(f"{r['map']}: {r['tokens']} token(s)"
                  + (f", {r['statblocks']} linked to a statblock" if r["statblocks"] else "")
                  + f", rule {r['diagonals']}")
            print(f"  scene   {r['scene']}")
            print(f"  sidecar {r['sidecar']}")
            print(f"  art     {r['artwork']}")
            for d in r["discs"]:
                print(f"  disc    {d}")
            for c in r["unknown_colours"]:
                print(f"  warning no colour is defined for {c!r}; drew a neutral disc",
                      file=sys.stderr)
            for fr in r.get("formations", []):
                print(f"  formation {fr['formation']!r} ({fr['mode']}): "
                      f"{fr['tokens']} token(s)")
                # A formation replayed onto a map it does not fit is a preview
                # that lies. Said here rather than left for someone to spot in
                # Atlas, where the symptom is a token standing in a bookcase.
                for b in fr["blocked"]:
                    print(f"           warning {b} is not standable on this map",
                          file=sys.stderr)
                for b in fr["off_map"]:
                    print(f"           warning {b}, off the edge of this map",
                          file=sys.stderr)
                for b in fr["separated"]:
                    print(f"           warning {b}; moved to the nearest free square",
                          file=sys.stderr)
            if r.get("art_matched"):
                print(f"  art     {r['art_matched']} real image(s), "
                      f"{r['art_discs']} colour disc(s) standing in")
                for tid, src in sorted(r["art"].items()):
                    if src.startswith("art "):
                        print(f"           {tid}: {src[4:]}")
                if r["art_discs"]:
                    fell_back = sorted(t for t, s2 in r["art"].items()
                                       if s2.startswith("disc "))
                    print(f"           no art, using a disc: {', '.join(fell_back)}",
                          file=sys.stderr)

    # The one thing that is genuinely lost, said out loud every run.
    for r in reports:
        if r.get("diagonals", "5") != "5-10-5":
            print(f"  warning {r['map']} uses diagonals={r['diagonals']!r}, which Atlas "
                  "cannot store. Atlas will measure that map as 5-10-5.", file=sys.stderr)

    if not args.dry_run and reports:
        print(f"\nOpen {vault} in Obsidian. Atlas adopts the files on its own "
              "(~1s); the scene is under Atlas VTT -> Open dashboard -> Scenes.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
