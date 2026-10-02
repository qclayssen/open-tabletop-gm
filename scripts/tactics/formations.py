"""formations.py: a placed monster arrangement, kept so it can be played again.

A *formation* is not a map and not an encounter. It is the one thing neither of
them can hold: **where the GM put the monsters.** When he walks two stirges onto
the island in the Bog and sets a bog shrub north-east of them, that arrangement
is real work, and today it evaporates. It lives in `combat/encounter.json`,
which is overwritten at the start of the next fight and gone at the end of this
one, so reproducing it means remembering the coordinates and typing
`--monster "Stirge@H7"` again from memory.

Why this is not `spawns[]`, which maps already have. Two reasons, and the second
is the one that matters:

  1. `spawns` is *per map*, and a formation is not. The same four stirges have to
     work on the island in the Detention Bog and in the restricted stacks of the
     Biblioplex. A map that restates its monsters is one map per encounter, and
     the worklist's own tables show the cost: every map with creatures is a
     gridless map invented for that one fight.
  2. `spawns` is a *layout*, and this is an *arrangement*. A layout says "the
     tavern's regulars are here". An arrangement says "these six, in this
     relationship to each other", which is the part that survives being moved to
     a different room.

`maps.compile_map` copies `spawns` into `meta` and nothing in `scripts/tactics/`
reads it. That gap is the reason this module exists, and it is why the store is
separate from the map format: **`compile_map` is untouched**, so every rule, every
shipped map and the byte-stable `grid.rows` guarantee are unaffected.

## The two offsets

A formation is stored twice, and this is the whole trick — lifted from Atlas, which
keeps `spriteTransform` and `spriteNormalized` on the same token for the same reason:

  * `dx, dy` — **cell offsets** from the anchor. Exact, and correct on the map it
    was captured on.
  * `nx, ny` — **pitch-normalized offsets**, as fractions of the map's own width
    and height. Aspect-distorting, and correct on a map of a *different* size.

Neither alone is right. Cell offsets put a formation captured on a 30x20 map in
the top-left corner of a 20x14 one. Normalized offsets stretch a tight six-square
ambush across a wide room. So both are stored, and the caller picks: `--at SQ`
pins the anchor by cell and uses `dx, dy`; `--centre` drops the formation in the
middle of whatever map it is landing on and uses `nx, ny`.

## Where it lives

`<campaign>/encounters/<slug>.json` — one small file per formation, campaign-scoped,
sitting beside `combat/encounter.json` and `tracker.json`. Plain JSON, written
atomically with a `.bak` alongside, `encoding="utf-8"` so a campaign on a
non-UTF-8 Windows locale still loads. `SCHEMA_VERSION` plus a `MIGRATIONS` table,
because the first version of a persisted format is the version that has to be
migrated.

## What this deliberately does not do

  * It does not own HP, AC or conditions. Those are the encounter's, and a
    formation carries names and squares. Replaying a formation re-rolls every
    monster from the SRD, which is the correct answer: a formation saved from a
    fight where the stirges were at 1 HP is not a formation, it is a corpse
    layout.
  * It does not capture player characters by default. The opposition is the
    reusable part; the party is placed by `--pc` as it always was, and
    `capture(..., include_pcs=True)` exists for a map whose *whole* setup is
    worth keeping.
  * It does not store a map's terrain, a map's artwork, or a map at all beyond
    the size it was captured at. A formation moves; a map does not.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re

from slug import slug as shared_slug

from . import grid as grid_mod
from .schemas import (AnyField, ListField, NumberField, OptionalField, SchemaField,
                      StringField)

FORMATION_SCHEMA = "otg-formation"
SCHEMA_VERSION = 2

MIGRATIONS: dict[tuple[int, int], object] = {
    # v1 stored the *display* name ("Kobold 2") as the member's `name`, which
    # `token_from_monster` cannot resolve -- so a formation saved by v1 replayed
    # only in the one case where there was exactly one of a creature, and failed
    # with "no SRD monster 'Kobold 2'" on every other. That is the worst kind of
    # bug in this module: a file that saves cleanly and then cannot be played.
    #
    # The migration moves the old name to `label` and strips the suffix, which is
    # exactly what `capture` now does on the way in.
    (1, 2): lambda spec: {
        **spec,
        "version": 2,
        "members": [{**m, "label": m["name"],
                     "name": _INSTANCE_SUFFIX.sub("", m["name"]).strip() or m["name"]}
                    for m in spec.get("members", [])],
    },
}

# Sides a formation can hold. PCs are excluded on purpose: see the module
# docstring. A formation is the opposition, and the party is placed by --pc.
FORMED_SIDES = ("enemy", "ally", "neutral")
# `capture(include_pcs=True)` puts the party in a formation too, so the schema
# has to admit `pc` even though `FORMED_SIDES` is what the default capture keeps.
# Two tuples rather than one, because the default is the contract and the
# opt-in should not weaken it: `FORMED_SIDES` says what a formation *is*.
STORED_SIDES = FORMED_SIDES + ("pc",)

# A side colour, using the same keys `spawns[].color` uses so a token looks the
# same in Atlas, on the display and in a saved formation. These are the
# `TOKEN_COLOURS` / `--tx-*` keys from the reference page the display palette is
# taken from, and they are duplicated here rather than imported because
# `map_to_atlas.py` is a script and `tactics/` is a package; the duplication is
# guarded by a test that compares the two tables.
SIDE_COLOUR = {
    "enemy": "danger",
    "ally": "brass",
    "neutral": "cent",
    "pc": "quan",
}

ROUNDING = 4        # decimals kept on a normalized offset
FRACTION = 1 / 3    # a deliberately awkward pitch, used in the docstring examples


# ─── the schema ───────────────────────────────────────────────────────────────

FORMATION_SCHEMA_FIELDS = SchemaField({
    "schema": StringField(choices=(FORMATION_SCHEMA,)),
    "version": NumberField(),
    "name": StringField(),
    "captured": StringField(),
    "from_map": OptionalField(StringField()),
    "from_size": OptionalField(SchemaField({
        "width": NumberField(), "height": NumberField(),
    })),
    # A pair of cells. `ListField` has no length argument, so the length is
    # checked in `validate()` below, where the message can name the square.
    "anchor": ListField(NumberField()),
    "members": ListField(SchemaField({
        "name": StringField(),
        # The name the token wore on the board, e.g. "Kobold 2". Display only.
        "label": OptionalField(StringField()),
        "side": StringField(choices=STORED_SIDES),
        # Cell offset from the anchor. Integers, because a formation is stored in
        # the cells it was played in; a fractional offset is a mis-placement.
        "dx": NumberField(),
        "dy": NumberField(),
        # Pitch-normalized offset, as a fraction of the captured map's size.
        "nx": NumberField(),
        "ny": NumberField(),
        "color": OptionalField(StringField()),
    }, required=("name", "side", "dx", "dy", "nx", "ny"))),
    "info": OptionalField(StringField()),
}, required=("schema", "version", "name", "anchor", "members"))


# ─── naming and paths ─────────────────────────────────────────────────────────

def slug(name: str) -> str:
    return shared_slug(name)


def encounters_dir(camp_dir) -> pathlib.Path:
    return pathlib.Path(camp_dir) / "encounters"


def path_for(camp_dir, name: str) -> pathlib.Path:
    """Where formation `name` lives. Refuses a name that is not a filename."""
    s = slug(name)
    if not s:
        raise ValueError(f"formation name {name!r} has no usable characters in it")
    return encounters_dir(camp_dir) / f"{s}.json"


def available(camp_dir) -> list:
    folder = encounters_dir(camp_dir)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json") if p.is_file())


# ─── capture ──────────────────────────────────────────────────────────────────

def _fold(name: str) -> str:
    """A comparison key loose enough for how maps and encounters name creatures.

    The same problem `map_to_atlas._fold` solves, and for the same reason: a
    creature is named three ways that all mean one thing. "Stirge" on the map,
    "Stirges" in prose, "Stirge 2" as a token id, "Giant Frog" in the SRD and
    "Frog 1" once it is on a board. An exact match finds none of those, and a
    formation that therefore misses its inherited colour falls back to the side
    default — which is how one map's stirges end up a different colour from the
    same stirges on another map.

    Kept local rather than imported from `map_to_atlas`, because that is a
    script and this is a package. `tests/test_formations.py` guards that the two
    colour tables agree; this function is small enough to read twice.
    """
    text = re.sub(r"\s*#?\s*\d+\s*$", "", str(name).casefold().strip())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# The instance suffix the engine itself appends when a fight places two of the
# same creature: `--monster "Kobold@W11"` twice yields "Kobold" and "Kobold 2"
# (`cli.cmd_start`). A formation stores the name to *re-create* a creature with,
# and `token_from_monster` cannot resolve "Kobold 2" -- so capture undoes the
# suffix it was given rather than storing a name the SRD does not have.
#
# Undoing it is well-defined rather than a guess because the engine is the thing
# that added it, and the CLI renumbers on replay anyway (`seen[key]`). No 5e
# monster name ends in a digit, so the pattern cannot eat a real name.
_INSTANCE_SUFFIX = re.compile(r"\s+\d+\s*$")


def _creature_name(token) -> str:
    return _INSTANCE_SUFFIX.sub("", token.name).strip() or token.name


def _colour_for(token, colour_from) -> str:
    """The side colour this member should carry into an Atlas export.

    Preference order, and the order is the point:

    1. A map that already names this creature. The campaign's own map has been
       saying "stirges are danger red" since before this module existed, and a
       formation that invents a different colour for the same creature makes the
       Atlas preview lie about which side a token is on.
    2. The token's own side, via `SIDE_COLOUR`.

    Falls back rather than refusing: a colour is a cosmetic label, and refusing
    to save an encounter over it would be the wrong trade.

    Three probes per spawn name, most exact first: the folded name as written, the
    same with a trailing plural dropped ("Frogs" -> "frog"), and the plural added.
    That is the same probe set `map_to_atlas.bestiary_note` uses for the same
    reason, and without it "Frogs" on the map and "Frog 1" on the board do not
    match — so the colour the map chose is silently dropped.
    """
    by_name: dict[str, str] = {}
    for spawn in colour_from or ():
        found = str(spawn.get("color") or "").strip().lower()
        if found:
            by_name.setdefault(_fold(spawn.get("name") or ""), found)
    key = _fold(token.name)
    for probe in (key, key.rstrip("s"), key + "s"):
        if probe in by_name:
            return by_name[probe]
    return SIDE_COLOUR.get(token.side, "cent")


def capture(enc, name: str, *, include_pcs: bool = False,
            colour_from=None, info: str = "") -> dict:
    """The running encounter's monster positions, as a formation.

    `colour_from` is an iterable of `spawns[]` dicts, normally
    `enc.meta.get("spawns")`, used only to inherit a side colour the map already
    chose (see `_colour_for`).

    The anchor is the *top-left-most member*, chosen by `(y, x)` so it is
    deterministic. Any other rule would work, but a deterministic one matters:
    capturing the same arrangement twice must produce the same file, or a
    formation diffed in git is noise.
    """
    wanted = set(FORMED_SIDES) | ({"pc"} if include_pcs else set())
    members = [t for t in enc.tokens.values()
               if t.side in wanted and not t.dead and not t.stable]
    if not members:
        raise ValueError(
            f"nothing to save: no living {' or '.join(sorted(wanted))} on the board. "
            "A formation is an arrangement of monsters, and a fight that is over "
            "has none")
    members.sort(key=lambda t: (t.y, t.x, t.id))

    w, h = enc.board().width, enc.board().height
    ax, ay = members[0].x, members[0].y
    return {
        "schema": FORMATION_SCHEMA,
        "version": SCHEMA_VERSION,
        "name": str(name).strip() or "formation",
        "captured": _dt.date.today().isoformat(),
        "from_map": str(enc.meta.get("slug") or enc.meta.get("name") or "").strip() or None,
        "from_size": {"width": w, "height": h},
        "anchor": [ax, ay],
        "members": [{
            # `name` is the *creature*, not the instance: it is what
            # `token_from_monster` and the bestiary fold both need. `label` is
            # what the token was called on the board, kept for display only —
            # Atlas wants a name on the token it can show, and three tokens all
            # reading "Goblin" is a worse preview than three reading
            # "Goblin 1..3", even though the statblock link needs "Goblin".
            "name": _creature_name(t),
            "label": t.name,
            "side": t.side,
            "dx": int(t.x) - ax,
            "dy": int(t.y) - ay,
            "nx": round(t.x / w, ROUNDING),
            "ny": round(t.y / h, ROUNDING),
            "color": _colour_for(t, colour_from),
        } for t in members],
        "info": str(info).strip() or None,
    }


# ─── validate, save, load ─────────────────────────────────────────────────────

def validate(spec: dict) -> list:
    """Problems with a formation file, as sentences. Empty means it is usable."""
    try:
        FORMATION_SCHEMA_FIELDS.validate(spec)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    if not spec["members"]:
        problems.append("a formation with no members is an empty file, not an encounter")
    anchor = spec["anchor"]
    if len(anchor) != 2:
        problems.append(f"anchor {anchor!r} is not a pair of cells; a formation's "
                        "origin is one square, not a region")
        return problems
    size = spec.get("from_size")
    ax, ay = int(anchor[0]), int(anchor[1])
    if not size:
        # A formation always records the size it was captured at; without it the
        # normalized offsets are meaningless, so this is a hard problem rather
        # than a degraded mode.
        problems.append("no from_size, so the pitch-normalized offsets have no "
                        "reference and only --at can replay this")
    else:
        w, h = int(size["width"]), int(size["height"])
        for i, m in enumerate(spec["members"]):
            # nx is a fraction of the *captured* width, so nx * w must land back
            # on a real cell. A value outside 0..1 is a formation that was
            # captured off its own map, which is a corruption, not an edge case.
            for key, lim in (("nx", w), ("ny", h)):
                if not 0.0 <= float(m[key]) <= 1.0:
                    problems.append(
                        f"member {i} ({m['name']!r}): {key}={m[key]} is outside 0..1, "
                        f"so it was not captured on a {w}x{h} map")
            if not 0 <= ax + m["dx"] < w or not 0 <= ay + m["dy"] < h:
                problems.append(
                    f"member {i} ({m['name']!r}): anchor {ax},{ay} + offset "
                    f"{m['dx']},{m['dy']} falls off the {w}x{h} map it was captured on")
    return problems


def save(camp_dir, spec: dict) -> pathlib.Path:
    """Validate, then write. The original is kept as `.bak` on the first save."""
    problems = validate(spec)
    if problems:
        raise ValueError("refusing to save a formation that will not replay: "
                         + "; ".join(problems))
    path = path_for(camp_dir, spec["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(spec, indent=1, ensure_ascii=False) + "\n"
    if path.exists() and not path.with_suffix(".json.bak").exists():
        path.with_suffix(".json.bak").write_text(path.read_text(encoding="utf-8"),
                                                 encoding="utf-8")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)                       # atomic: a half-written formation is worse
    return path                              # than no formation, and both read the same


def load(camp_dir, name: str) -> dict:
    path = path_for(camp_dir, name)
    if not path.exists():
        raise FileNotFoundError(
            f"no formation {name!r} in {encounters_dir(camp_dir)}. "
            f"Formations: {', '.join(available(camp_dir)) or '(none yet)'}")
    spec = json.loads(path.read_text(encoding="utf-8"))
    version = int(spec.get("version") or 0)
    while (version, SCHEMA_VERSION) in MIGRATIONS:
        spec = MIGRATIONS[(version, SCHEMA_VERSION)](spec)
        version = int(spec.get("version") or 0)
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"formation {name!r} is version {version}, this engine writes "
            f"{SCHEMA_VERSION}. A file from a newer engine is not a formation we "
            "can read, and guessing at it would place monsters in the wrong squares")
    problems = validate(spec)
    if problems:
        raise ValueError(f"formation {name!r} will not replay: " + "; ".join(problems))
    return spec


# ─── replay ───────────────────────────────────────────────────────────────────

def positions(spec: dict, board, *, at=None, centre: bool = False) -> dict:
    """Where this formation's members land on `board`, and what had to move.

    `board` is a `grid.Grid` — the *target* map, which need not be the one the
    formation was captured on. Three modes, and which one you want depends
    entirely on whether the grid changed:

      * `at=(x, y)` — pin the anchor to that cell, and use the **cell** offsets
        (`dx, dy`). Exact, and the right answer whenever the target map is the
        original one, because then the squares the GM chose are still the
        squares.
      * neither — **proportional**, and the default. Use the **normalized**
        offsets directly: a member goes to `round(nx * width), round(ny * height)`.
        The formation keeps its shape scaled to the new map *and* its position
        on the map proportionally, which is what "survives a change of grid"
        has to mean for it to be worth anything.
      * `centre=True` — the same proportional placement, then shifted so the
        formation's bounding box is centred on the target map. For a formation
        captured in one corner of a big map and replayed onto a room you want it
        in the middle of.

    The normalized offset is an *absolute* fraction of the map, not a delta to be
    added to an anchor. Treating it as a delta is a bug worth naming, because it
    looks reasonable: on a 12x6 formation replayed onto a 20x14 map it lands
    every member past the right edge and they all clamp to the same column. Six
    monsters on one square is a fight that cannot be played, produced by code
    that reported no error at all.

    The result always says what happened:

        {"placements": [{"name","side","color","x","y","moved"}...],
         "blocked": [...], "off_map": [...], "mode": ...}

    Note what proportional placement does *not* need. A member at fraction 0.83
    of a 20-wide map lands at column 16 — not at 16-plus-an-anchor. An earlier
    version of this function added the offset to a chosen centre instead, which
    pushed every member past the right edge and needed a "clamped" list to
    describe the damage; the fix was to delete the clamping, not to report it.

    So on any map a formation could sensibly be replayed onto, a proportional
    replay lands every member on the map. The degenerate case is a map narrower
    than two cells: `round(0.83 * 1)` is 1, which is off a 1-wide map. That is
    reported through `off_map` rather than clamped, because a formation on a
    1x1 board is something the GM needs to see and refuse, not something to be
    quietly piled into the corner.

    `off_map` is otherwise a **pinned** replay running off the edge: the GM named
    a square and the formation does not fit around it. A different problem from a
    scale change, with a different fix, so it gets its own list.

    `blocked` is a member on a square the target map does not let a creature
    stand on — a wall, a chasm, deep water. Nothing is moved for it, because the
    only two repairs available are both wrong: nudging it silently changes a
    distance, and dropping it silently loses a monster. `state.validate` refuses
    the encounter, which is the correct outcome and names the token.

    `separated` is a member that was moved because something else already held
    its square. Proportional replay causes this on real data: two monsters a
    square apart on a wide map round to the same column on a narrower one. The
    move is the smallest one available (`_nearest_free`) and it is always
    reported, because a nudged monster is a changed distance.

    `unplaced` is a member that collided and had nowhere to go — a map with one
    free square and three monsters. It is reported rather than quietly dropped,
    because "I could not put this one anywhere" and "I put it in the corner" are
    very different sentences for the GM to read afterwards.
    """
    if at is not None and centre:
        raise ValueError("give an anchor (--at) or ask for the centre, not both")
    w, h = board.width, board.height
    size = spec.get("from_size") or {}
    if at is None and not size:
        raise ValueError(
            f"formation {spec['name']!r} records no from_size, so it has no "
            "pitch-normalized offsets to replay. Pin it with --at instead, which "
            "uses the cell offsets and needs no size")

    if at is not None:
        mode = "at"
        ax, ay = int(at[0]), int(at[1])
        cells = [(ax + int(m["dx"]), ay + int(m["dy"])) for m in spec["members"]]
    else:
        mode = "centre" if centre else "proportional"
        # Proportional: each member's normalized offset is where it sat on the
        # map it was captured on, as a fraction, so that fraction of the target
        # map is where it belongs.
        cells = [(round(float(m["nx"]) * w), round(float(m["ny"]) * h))
                 for m in spec["members"]]
        shift_x = shift_y = 0
        if centre and cells:
            mid_x = (min(c[0] for c in cells) + max(c[0] for c in cells)) // 2
            mid_y = (min(c[1] for c in cells) + max(c[1] for c in cells)) // 2
            shift_x, shift_y = w // 2 - mid_x, h // 2 - mid_y
            cells = [(c[0] + shift_x, c[1] + shift_y) for c in cells]
        ax, ay = int(spec["anchor"][0]) + shift_x, int(spec["anchor"][1]) + shift_y

    placements, blocked, off_map, separated, unplaced = [], [], [], [], []
    taken: set = set()
    for m, (x, y) in zip(spec["members"], cells):
        moved = None
        if not (0 <= x < w and 0 <= y < h):
            # Only reachable in pinned mode -- see the docstring. Pulled back onto
            # the map so the caller can still show a square, and reported so the
            # GM learns that the square they chose is not the square a monster
            # ended up on.
            cx, cy = min(max(x, 0), w - 1), min(max(y, 0), h - 1)
            off_map.append({**m, "was": [x, y], "x": cx, "y": cy})
            x, y = cx, cy
            moved = [x, y]
        # Three cases, and they get three different answers.
        #
        # 1. Passable and free -- placed. The ordinary case.
        #
        # 2. Passable but **taken**. Two creatures on one square is not a fight,
        #    it is a rules error, and proportional replay causes it: a formation
        #    captured with two monsters one square apart, replayed onto a map
        #    narrower than the gap it had, rounds both to the same column. A
        #    30x20 formation on a 24x18 map does this on the real data. So the
        #    later of the pair moves to the nearest free square, and the move is
        #    reported in `separated` with both squares -- a nudged monster is a
        #    changed distance, and only the GM can say whether that distance is
        #    the one they wanted.
        #
        # 3. **Impassable** -- a wall, a bookcase, deep water. Nothing moves.
        #    This is the map telling the truth about where a creature cannot be,
        #    and the only two repairs for it are both wrong: nudging it changes a
        #    distance the GM chose, dropping it loses a monster. It is reported
        #    as `blocked` and `state.validate` refuses the encounter, which
        #    names the token and is the correct outcome.
        #
        # Case 3 deliberately does *not* try case 2's repair. An earlier version
        # treated "taken or blocked" as one condition and moved monsters out of
        # bookcases, which quietly deleted the most useful thing this function
        # says: that the formation does not fit this room.
        if (x, y) in taken:
            free = _nearest_free(board, taken, x, y)
            if free is not None:
                separated.append({**m, "was": [x, y], "x": free[0], "y": free[1]})
                x, y = free
                moved = [x, y]
            else:
                unplaced.append({**m, "x": x, "y": y})
        taken.add((x, y))
        if not board.passable((x, y)):
            blocked.append({**m, "x": x, "y": y})
        placements.append({
            "name": m["name"], "side": m["side"],
            # Carried through so a consumer can show a nameplate. The CLI renumbers
            # from `name` and ignores this; the Atlas exporter prefers it, because
            # three tokens all reading "Kobold" is a worse preview than three
            # reading "Kobold 1..3" even though the statblock fold needs "Kobold".
            "label": m.get("label") or m["name"],
            "color": m.get("color") or SIDE_COLOUR.get(m["side"], "cent"),
            "x": x, "y": y, "moved": moved,
        })
    return {
        "name": spec["name"],
        "mode": mode,
        "anchor": [ax, ay],
        "placements": placements,
        "blocked": blocked, "off_map": off_map, "separated": separated,
        "unplaced": unplaced,
    }


def _nearest_free(board, taken, x, y):
    """The closest square a creature could stand on that nobody else holds.

    Breadth-first over the 5 ft neighbourhood in `grid.SQUARE_FT` steps, so the
    move is the *smallest* one that fixes the problem: a monster nudged one
    square keeps the distance it had better than one nudged three. Ties break
    toward the lower square, which is arbitrary but has to be *something* — a
    tie that broke randomly would make the same command produce two different
    fights.

    Returns None when the map is full, which is the caller's cue to report a
    monster it could not place rather than to stack it.
    """
    if not board.in_bounds((x, y)) or not board.passable((x, y)):
        start = (min(max(x, 0), board.width - 1), min(max(y, 0), board.height - 1))
        if not board.passable(start) or start in taken:
            pass
        else:
            return start
    seen = {(x, y)}
    queue = [(x, y)]
    for cx, cy in queue:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                p = (cx + dx, cy + dy)
                if p in seen or not board.in_bounds(p):
                    continue
                seen.add(p)
                if p in taken or not board.passable(p):
                    queue.append(p)
                    continue
                return p
    return None


def label(pos) -> str:
    """`positions` output in the CLI's own square notation, for the report text."""
    return grid_mod.label(pos)
