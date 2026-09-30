"""
map_to_chartdown.py: export an engine battle map as a Chartdown (.cd) document.

Run: python3 scripts/map_to_chartdown.py <map> [<map> ...] [--out DIR]

Chartdown (github.com/Nossimonov/Chartdown, spec v0.8) is a plain-text language
for TTRPG maps. This is `map_to_atlas.py`'s sibling and has the same shape: a
strictly one-way text export. It writes files and never reads one back, so the
engine stays the only authority for a fight. There is no plugin and nothing to
install to run it (stdlib only).

    display/maps/<name>.json  ->  <out>/<name>.cd

The map is read the way the engine reads it: `scripts/tactics/maps.py`
`compile_map()` turns the painted rectangles into the row-string grid, and this
script emits from that grid, not from the raw rectangles. Later rectangles that
covered earlier ones are already resolved, so the two never disagree.

The syntax was written against the spec's own `docs/spec/digest.md` and
`grammar.ebnf` (v0.8), and every file carries `chartdown: 0.8` because the
language is pre-1.0 and a minor bump may break it. `@chartdown/core`'s `parse()`
is used by the tests as an optional dev-time oracle only (skipped when node or
the package is absent). It is never needed to run this script.

Square grids only, for now: `grid: square WxH`. A hex export is a later change to
that one header line, and nothing in the cell-address grammar (`A1`, `A1..C3`)
changes, which the spec is explicit about.

TERRAIN. An engine terrain character becomes a Chartdown vocabulary word through
an explicit table (TERRAIN_WORDS). It is lossy in spelling and meant to be faithful
in meaning. A terrain name that is not in the table is REFUSED, never guessed: the
map is not written and the error names the terrain and how many squares it covers.
`--terrain NAME=WORD` supplies a word for a map's own custom terrain (`finish=grass`).

WHAT THIS CANNOT CARRY, and says so on every run (each is printed as LOSS lines):

  * the diagonal rule. The map's `diagonals` ("5" or "5-10-5") has no Chartdown
    field. Whatever plays the .cd measures diagonals its own way.
  * terrain rules beyond the word: movement cost, cover, sight, swim and hazard
    damage. A word carries its own stdlib meaning (`rubble` is difficult, `water`
    is difficult), not the engine's numbers. The engine keeps deciding those.
  * pixel cell size. Chartdown has `scale: 5ft` and no pixels. Artwork, `grid`
    alignment, `zones`, `labels` and `portraits` are dropped.
  * tokens. With `--tokens` each spawn becomes a `[tokens]` line, but that is a
    position and a name only (no HP, AC, conditions or initiative), and Chartdown's
    UVTT export does not export tokens at all, so they do not survive it.

Usage:
    python3 scripts/map_to_chartdown.py frog-pond --terrain finish=grass
    python3 scripts/map_to_chartdown.py mage-tower --tokens --out ~/maps
    python3 scripts/map_to_chartdown.py mage-tower --dry-run
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

from tactics.grid import TERRAIN, col_label  # noqa: E402
from tactics.maps import compile_map  # noqa: E402

CHARTDOWN_VERSION = "0.8"

# The engine's terrain -> Chartdown vocabulary. Each entry is (word, section, why).
# `floor` is absent on purpose: an unmarked square is floor in Chartdown, so floor
# emits nothing. Words marked stdlib are in the spec's standard library (digest.md,
# "Standard library"); `hazard` is not, so it is declared in `[vocab]`.
#
#   difficult -> rubble   stdlib battlemap terrain, difficult by default
#   water     -> water    stdlib battlemap terrain, difficult by default
#   hazard    -> hazard   no stdlib word; declared `hazard : terrain` in [vocab]
#   wall      -> earth    stdlib level surface: impassable and occludes. A wall
#                         SQUARE is solid, and Chartdown's `wall` is a barrier on
#                         a cell EDGE, so `earth` is the meaning-faithful word.
#   void      -> void     stdlib `void : air`, unfloored: see across, cannot walk
#   feature   -> boulder  stdlib feature, one per square (an obstacle that gives
#                         cover). Lives in [features], not [terrain].
TERRAIN_WORDS = {
    "difficult": ("rubble", "terrain"),
    "water": ("water", "terrain"),
    "hazard": ("hazard", "terrain"),
    "wall": ("earth", "terrain"),
    "void": ("void", "terrain"),
    "feature": ("boulder", "features"),
}
# Words the spec's standard library already defines, so no `[vocab]` line is needed.
STDLIB_WORDS = frozenset({
    "mud", "sand", "grass", "snow", "ice", "water", "rubble", "slope",
    "earth", "air", "void", "roof", "terrace", "boulder", "forest", "hills",
    "plains", "desert", "marsh", "road", "trail",
})
# Words the language reserves as archetype names (grammar, not type words).
ARCHETYPES = frozenset({"terrain", "path", "feature", "structure", "barrier",
                        "opening", "token", "zone", "field"})

_WORD = re.compile(r"[a-z][a-z0-9-]*")


class Refused(ValueError):
    """A map that cannot be exported faithfully. Nothing is written for it."""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _string(text: str) -> str:
    """A Chartdown quoted string: no quotes, newlines or semicolons-as-comment risk."""
    return '"' + " ".join(str(text).replace('"', "'").split()) + '"'


def parse_terrain_args(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items or []:
        name, sep, word = item.partition("=")
        name, word = name.strip(), word.strip()
        if not sep or not name or not _WORD.fullmatch(word):
            raise ValueError(f"--terrain {item!r}: expected NAME=WORD, WORD like 'grass'")
        if word in ARCHETYPES:
            raise ValueError(f"--terrain {item!r}: {word!r} is a Chartdown archetype, "
                             "not a word a map can use")
        out[name] = word
    return out


def _rectangles(cells: set[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """Cover a set of (x, y) squares with disjoint rectangles (x, y, w, h).

    Greedy, row-major, deterministic: take the first unclaimed square, run right,
    then grow down while the whole run matches. Not minimal, always exact.
    """
    left = set(cells)
    out = []
    for y, x in sorted((y, x) for x, y in cells):
        if (x, y) not in left:
            continue
        w = 1
        while (x + w, y) in left:
            w += 1
        h = 1
        while all((x + i, y + h) in left for i in range(w)):
            h += 1
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                left.discard((xx, yy))
        out.append((x, y, w, h))
    return out


def grid_header(meta: dict) -> str:
    """The one place the grid shape is spelled. `meta` carries `width` and `height`.

    Square only today. A hex export (`grid: hex WxH pointy odd-row`) is a one-line
    branch here and nowhere else: cell addresses and everything below are unchanged.
    """
    return f"grid: square {meta['width']}x{meta['height']}"


def _address(x: int, y: int) -> str:
    return f"{col_label(x)}{y + 1}"


def _range(x: int, y: int, w: int, h: int) -> str:
    a = _address(x, y)
    return a if (w, h) == (1, 1) else f"{a}..{_address(x + w - 1, y + h - 1)}"


def plan_terrain(compiled: dict, custom_words: dict[str, str]) -> tuple[dict, list[str]]:
    """Resolve every square's terrain name to (word, section), or refuse.

    Returns ({(word, section): set(cells)}, notes). Refusal (Refused) names each
    unknown terrain with its square count, so the fix is one flag away.
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
    placed: dict[tuple[str, str], set] = {}
    for name in sorted(by_name):
        if name == "floor":
            continue
        # A map may redefine a built-in name with different numbers. The word would
        # then describe the stock terrain and not this one, so it is not translated.
        if name in TERRAIN and name in custom_defs and custom_defs[name] != TERRAIN[name]:
            if name not in custom_words:
                problems.append(f"{name!r} ({counts[name]} squares) is redefined by this "
                                "map, so the stock word would misstate it")
                continue
        if name in custom_words:
            word, section = custom_words[name], "terrain"
        elif name in TERRAIN_WORDS:
            word, section = TERRAIN_WORDS[name]
        else:
            problems.append(f"terrain {name!r} ({counts[name]} squares) has no Chartdown word")
            continue
        placed.setdefault((word, section), set()).update(by_name[name])
        if name in custom_words:
            notes.append(f"terrain {name!r} -> {word!r} by --terrain (not the engine's numbers)")
    if problems:
        raise Refused("; ".join(problems) + ". Nothing written. Map each with "
                      "--terrain NAME=WORD (a word Chartdown knows, e.g. grass, mud, sand).")
    return placed, notes


def _token_lines(spawns: list) -> list[str]:
    lines, used = [], set()
    for i, s in enumerate(spawns):
        name = str(s.get("name") or s.get("id") or f"token {i + 1}")
        type_word = slug(name) or "creature"
        if type_word in ARCHETYPES or not type_word[0].isalpha():
            type_word = "creature-" + type_word
        ident = slug(s.get("id") or i + 1)
        if not ident[:1].isalpha():
            ident = "t" + ident
        if ident == type_word:
            ident += "-1"
        base, n = ident, 2
        while ident in used:
            ident, n = f"{base}-{n}", n + 1
        used.add(ident)
        line = f"{type_word} {ident} {_string(name)} : {_address(int(s['x']), int(s['y']))}"
        side = slug(s.get("color") or "")
        if side:
            line += f" side={side}"
        lines.append(line)
    return lines


def build_document(spec: dict, map_id: str, *, tokens: bool = False,
                   custom_words: dict[str, str] | None = None) -> tuple[str, list[str], dict]:
    """(document text, loss lines, stats). Raises Refused. Pure: touches no files."""
    compiled = compile_map(spec)
    grid, meta = compiled["grid"], compiled["meta"]
    width, height = len(grid["rows"][0]), len(grid["rows"])
    placed, notes = plan_terrain(compiled, custom_words or {})

    out = []
    title = " ".join(str(spec.get("name") or map_id).split())
    out.append(f"# {title}")
    out.append("map: battlemap")
    out.append(f"chartdown: {CHARTDOWN_VERSION}")
    if _WORD.fullmatch(slug(map_id)):
        out.append(f"id: {slug(map_id)}")
    out.append(grid_header({"width": width, "height": height}))
    out.append("scale: 5ft")
    info = " ".join(str(meta.get("info") or "").split())
    if info:
        out.append(f"; {info}")

    vocab = sorted({w for (w, _s) in placed if w not in STDLIB_WORDS})
    if vocab:
        out.append("[vocab]")
        out.extend(f"{w} : terrain" for w in vocab)

    order = list(TERRAIN_WORDS.values())
    for section in ("terrain", "features"):
        words = sorted((w for (w, s) in placed if s == section),
                       key=lambda w: (order.index((w, section)) if (w, section) in order else 99, w))
        lines = []
        for word in words:
            cells = placed[(word, section)]
            if section == "features":
                lines.extend(f"{word} : {_address(x, y)}" for y, x in sorted((y, x) for x, y in cells))
            else:
                lines.extend(f"{word} : area {_range(*r)}" for r in _rectangles(cells))
        if lines:
            out.append(f"[{section}]")
            out.extend(lines)

    spawns = meta.get("spawns") or []
    if tokens and spawns:
        out.append("[tokens]")
        out.extend(_token_lines(spawns))

    diag = str(grid.get("diagonals", "5"))
    losses = [
        f"the diagonal rule ({diag!r}) has no Chartdown field; the .cd is measured by "
        "whatever plays it, and the engine's rule is not carried",
        "terrain rules beyond the word (movement cost, cover, sight, swim, hazard damage) "
        "are not carried; each word keeps its own Chartdown meaning",
        "no pixel cell size: artwork and `grid` alignment are dropped (scale is 5ft)",
    ]
    if meta.get("labels"):
        losses.append(f"{len(meta['labels'])} label(s) dropped")
    if meta.get("zones"):
        losses.append(f"{len(meta['zones'])} zone line(s) dropped")
    if any(ch == "#" for row in grid["rows"] for ch in row) or ("earth", "terrain") in placed:
        losses.append("wall squares became solid rock (`earth`); Chartdown walls are cell "
                      "edges of structures, and a UVTT export may not draw these as walls")
    if spawns and tokens:
        losses.append(f"{len(spawns)} token(s) written as position and name only; they do "
                      "not survive Chartdown's UVTT export, and HP, AC and conditions are "
                      "not carried")
    elif spawns:
        losses.append(f"{len(spawns)} spawn(s) not exported (pass --tokens to include them)")
    stats = {"width": width, "height": height, "notes": notes,
             "words": sorted({w for (w, _s) in placed})}
    return "\n".join(out) + "\n", losses, stats


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
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("chartdown-out"),
                        help="folder to write <id>.cd into (default: %(default)s)")
    parser.add_argument("--maps-dir", type=pathlib.Path, default=_MAPS,
                        help="where the engine's map files live (default: %(default)s)")
    parser.add_argument("--terrain", action="append", default=[], metavar="NAME=WORD",
                        help="Chartdown word for one of a map's own custom terrains "
                             "(repeatable), e.g. finish=grass")
    parser.add_argument("--tokens", action="store_true",
                        help="also write spawns as [tokens] (position and name only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print each document, write nothing")
    args = parser.parse_args(argv)

    try:
        custom_words = parse_terrain_args(args.terrain)
    except ValueError as exc:
        print(f"map_to_chartdown: {exc}", file=sys.stderr)
        return 1

    failed = 0
    for map_id in args.maps:
        try:
            spec = load_spec(map_id, args.maps_dir)
            stem = pathlib.Path(map_id).stem if map_id.endswith(".json") else map_id
            text, losses, stats = build_document(spec, stem, tokens=args.tokens,
                                                 custom_words=custom_words)
        except (Refused, ValueError, FileNotFoundError, KeyError) as exc:
            print(f"{map_id}: REFUSED: {exc}", file=sys.stderr)
            failed += 1
            continue
        if args.dry_run:
            print(f"# --- {stem}.cd ---")
            print(text, end="")
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            target = args.out / f"{stem}.cd"
            target.write_text(text, encoding="utf-8", newline="\n")
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
