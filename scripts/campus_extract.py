#!/usr/bin/env python3
"""campus_extract.py: the Strixhaven campus map as data, not as a browser tab.

    python3 scripts/campus_extract.py                    # markdown to stdout
    python3 scripts/campus_extract.py --json             # machine-readable
    python3 scripts/campus_extract.py --out docs/CAMPUS.md
    python3 scripts/campus_extract.py --check            # verify, write nothing

WHY THIS EXISTS
---------------
`display/static/reference/strixhaven_map_table.html` is a single self-contained
page: the campus is an inline SVG and every place, college, region and battle-map
rectangle lives in one `<script>` block as JS object literals. It is a good
reference *in a browser* and useless as data -- there is no JSON, no API, no
linked file, and no way to ask "what is at (360, 640)?" without a DOM.

So the page has to be parsed rather than fetched. `crawl`/`scrape` are the wrong
tools here on purpose: the file is local and fully offline (its only external
references are two Google Fonts stylesheets), and the five battle maps in
`display/maps/` were already ported from it by hand. What was *not* extracted is
the campus itself -- 18 places, 5 college regions, the path network and the
river -- which is exactly the part a GM wants to read as a list.

This reads that data out of the local page. It does not fetch anything, and it
does not consult the user's own copy of the page or any DM-only folder: per
CLAUDE.md the reference page is a player-facing copy, and this keeps it that way.

WHAT IT EXTRACTS
----------------
  * `colleges`  - id -> college name, including Central
  * `regions`   - one SVG path per college district, with its label anchor
  * `places`    - id, name, college, x/y pin, description
  * `paths`     - the dashed walkway network, as polyline vertex lists
  * `river`     - the watercourse crossing the campus
  * `maps`      - the five battle maps, cross-checked against display/maps/*.json

The battle maps are verified against the engine's own JSON, not the page, so a
divergence in either direction is reported instead of silently exported.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from utf8io import read_text

REFERENCE = ROOT / "display" / "static" / "reference" / "strixhaven_map_table.html"
MAPS_DIR = ROOT / "display" / "maps"

# The page keys its five maps by short id; the engine names its files by a
# different, longer id. This is the one mapping, and it is asserted below rather
# than guessed at each use, so a rename on either side is a loud failure.
PAGE_MAP_KEYS = {
    "tower": "mage-tower",
    "cafe": "firejolt-rooftops",
    "bog": "detention-bog",
    "pond": "frog-pond",
    "grid": "blank",
}

# The page's short terrain vocabulary, in the order its legend shows it.
TERRAIN_LEGEND = {
    "floor": "Floor",
    "wall": "Wall / blocking",
    "water": "Water",
    "diff": "Difficult terrain",
    "feat": "Feature / cover",
    "void": "Drop / off-map",
}

# token colour -> what the display calls it, from the page's own add-token form.
TOKEN_SIDE = {
    "danger": "Enemy",
    "brass": "Object",
    "quan": "Ally (teal)",
    "lore": "Lorehold",
    "pris": "Prismari",
    "silv": "Silverquill",
    "with": "Witherbloom",
    "cent": "Central",
}


class ExtractError(ValueError):
    """The reference page is not shaped the way this parser expects."""


# ─── the JS object literals ───────────────────────────────────────────────────
# Parsed with narrow regexes rather than eval: the page is trusted input, but
# `eval` on text scraped out of any document is a habit worth not having, and
# these five objects are flat enough that the patterns are exact.

def _obj(text: str, name: str) -> str:
    """The body of `const <name>={...}` or `const <name>=[...]`.

    Brace-matched, so a brace inside a description cannot end the match early.
    The closing character differs by which of the two the page used.
    """
    m = re.search(rf"const {name}=([{{\[])", text)
    if not m:
        raise ExtractError(f"no `const {name}=` in the reference page")
    open_ch, close_ch = m.group(1), {"{": "}", "[": "]"}[m.group(1)]
    i, depth = m.end(), 1
    while i < len(text) and depth:
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
        i += 1
    if depth:
        raise ExtractError(f"`const {name}` is not closed; the page is truncated?")
    return text[m.end():i - 1]


def _pairs(body: str, pattern: str, keys: str) -> list[dict]:
    """Every `{...}` in `body` matching `pattern`, as a dict of `keys`.

    `keys` names groups that the pattern declares by name, so a renamed group
    fails here rather than producing a dict silently missing a field.
    """
    out = []
    for m in re.finditer(pattern, body):
        g = m.groupdict()
        missing = [k for k in keys if k not in g]
        if missing:
            raise ExtractError(f"pattern {pattern!r} has no group(s) {missing}")
        out.append({k: (_js_num(v) if k in ("lx", "ly") else v) for k, v in g.items()
                    if k in keys})
    return out


def _js_string(value: str) -> str:
    """Unescape a JS string literal's contents.

    The regexes below capture either the bare contents (`[^"]*`) or the whole
    quoted literal, depending on whether a group needs the surrounding quotes to
    anchor itself. So quotes are stripped when present rather than required --
    the value is the contents either way.
    """
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _js_num(value: str) -> float | int:
    n = float(value)
    return int(n) if n.is_integer() else n


# ─── campus ───────────────────────────────────────────────────────────────────

def colleges(html: str) -> dict[str, str]:
    body = _obj(html, "colleges")
    return dict(re.findall(r'(\w+)\s*:\s*"([^"]*)"', body))


def regions(html: str) -> list[dict]:
    body = _obj(html, "regions")
    return _pairs(body,
                  r'\{c:"(?P<college>\w+)",\s*d:"(?P<d>[^"]+)",\s*lx:(?P<lx>[\d.]+),'
                  r'\s*ly:(?P<ly>[\d.]+)\}',
                  ("college", "d", "lx", "ly"))


def places(html: str) -> list[dict]:
    body = _obj(html, "places")
    out = []
    for m in re.finditer(
            r'\{id:"([\w-]+)",\s*n:"([^"]*)",\s*c:"(\w+)",\s*x:([\d.]+),\s*y:([\d.]+),\s*d:"([^"]*)"',
            body):
        out.append({"id": m[1], "name": m[2], "college": m[3],
                    "x": _js_num(m[4]), "y": _js_num(m[5]), "description": m[6]})
    return out


def _path_vertices(d: str) -> list[tuple[int | float, int | float]]:
    """`M500,350 L500,610` -> [(500,350), (500,610)].

    Only the straight-line `M`/`L` subset the page actually uses. A curve in the
    river (`C`) is kept as its raw `d` string instead, because sampling a cubic
    to a vertex list would invent precision the source does not have.
    """
    return [(_js_num(x), _js_num(y)) for x, y in re.findall(r"[ML]([\d.]+),([\d.]+)", d)]


def paths(html: str) -> list[list[tuple[float, float]]]:
    """The dashed walkway network, from the `<g stroke-dasharray="2 8">` block."""
    m = re.search(r'stroke-dasharray="2 8"[^>]*>(.*?)</g>', html, re.DOTALL)
    if not m:
        raise ExtractError("no dashed walkway group in the campus SVG")
    return [_path_vertices(d) for d in re.findall(r'<path d="([^"]+)"', m.group(1))]


def river(html: str) -> str:
    m = re.search(r'<path d="([^"]+)" stroke="var\(--water\)"', html)
    if not m:
        raise ExtractError("no river path in the campus SVG")
    return m.group(1)


def map_key(spec: str) -> str:
    """The engine's file id for a page map key, verified to exist on disk."""
    name = PAGE_MAP_KEYS.get(spec)
    if name is None:
        raise ExtractError(f"unknown page map key {spec!r}; add it to PAGE_MAP_KEYS")
    if not (MAPS_DIR / f"{name}.json").exists():
        raise ExtractError(
            f"page map {spec!r} maps to {name!r} but display/maps/{name}.json is "
            f"missing; fix PAGE_MAP_KEYS or restore the map")
    return name


def battle_maps(html: str) -> list[dict]:
    """The five battle maps, each paired with the engine's own JSON for the same
    map so the two can be compared rather than trusted."""
    body = _obj(html, "maps")
    out = []
    # Split on entry starts rather than looking for a closing "\n },": the last
    # entry has no terminator at all, and an earlier guess at the separator made
    # every map after the first inherit the next one's features.
    starts = list(re.finditer(
        r'(\w+):\{name:"([^"]*)",\s*dm:(\d+),\s*w:(\d+),\s*h:(\d+),\s*info:"((?:[^"\\]|\\.)*)"',
        body))
    for idx, m in enumerate(starts):
        key, name, dm, w, h, info = m.groups()
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(body)
        chunk = body[m.end():end]
        # The trailing label is optional: most features on the page carry none,
        # and requiring one silently dropped every unnamed rectangle (frog-pond
        # went 9 -> 2 features this way).
        feats = [
            {"type": t, "x": _js_num(x), "y": _js_num(y), "w": _js_num(wd),
             "h": _js_num(ht), "label": _js_string(lb) or None}
            for t, x, y, wd, ht, lb in re.findall(
                r'\["(\w+)",([\d.]+),([\d.]+),([\d.]+),([\d.]+)(?:,"([^"]*)")?\]', chunk)
        ]
        toks = [
            {"id": tid, "name": tname, "color": col,
             "square": f"{chr(ord('A') + _js_num(x))}{_js_num(y) + 1}",
             "x": _js_num(x), "y": _js_num(y)}
            for tid, tname, col, x, y in re.findall(
                r'\["([^"]*)","([^"]*)","(\w+)",([\d.]+),([\d.]+)\]', chunk)
        ]
        # Most maps declare no zones at all; only the stadium has them.
        zone_lists = re.findall(r'zones:\[([\d.,\s]*)\]', chunk)
        zones = [_js_num(z) for z in zone_lists[0].split(",") if z.strip()] if zone_lists else []
        out.append({
            "page_key": key, "map": map_key(key), "name": _js_string(name),
            "dm_only": bool(_js_num(dm)), "width": _js_num(w), "height": _js_num(h),
            "info": _js_string(info), "features": feats, "zones": zones, "tokens": toks,
        })
    if len(out) != len(PAGE_MAP_KEYS):
        raise ExtractError(
            f"found {len(out)} battle maps in the page, expected {len(PAGE_MAP_KEYS)}: "
            f"{[m['page_key'] for m in out]}")
    return out


# ─── cross-check against the engine ───────────────────────────────────────────

def reconcile(bmaps: list[dict]) -> list[str]:
    """Differences between the page's battle maps and display/maps/*.json.

    The engine JSON is authoritative -- it is what combat actually runs on -- so
    this reports page/engine divergence instead of picking a winner.
    """
    problems = []
    for bm in bmaps:
        path = MAPS_DIR / f"{bm['map']}.json"
        try:
            spec = json.loads(read_text(path))
        except (OSError, ValueError) as exc:
            problems.append(f"{bm['map']}: cannot read {path.name}: {exc}")
            continue
        if spec.get("width") != bm["width"] or spec.get("height") != bm["height"]:
            problems.append(
                f"{bm['map']}: size {spec.get('width')}x{spec.get('height')} in "
                f"{path.name} but {bm['width']}x{bm['height']} on the page")
        page_feats = len(bm["features"])
        if page_feats != len(spec.get("features", [])):
            problems.append(
                f"{bm['map']}: {page_feats} features on the page, "
                f"{len(spec.get('features', []))} in {path.name} (expected: a hand "
                f"port splits or merges rectangles)")
        page_toks = {t["name"] for t in bm["tokens"]}
        engine_toks = {s.get("name") for s in spec.get("spawns", [])}
        missing = sorted(page_toks - engine_toks)
        if missing:
            problems.append(f"{bm['map']}: tokens on the page but not in {path.name}: {missing}")
        if list(spec.get("zones", [])) != bm["zones"]:
            problems.append(
                f"{bm['map']}: zones {bm['zones']} on the page, "
                f"{spec.get('zones', [])} in {path.name}")
    return problems


# ─── render ───────────────────────────────────────────────────────────────────

def to_markdown(data: dict) -> str:
    L: list[str] = []
    col = data["colleges"]
    L.append("# Strixhaven campus")
    L.append("")
    L.append("Extracted by `scripts/campus_extract.py` from "
             "`display/static/reference/strixhaven_map_table.html`. "
             "The page is the source; this file is the readable copy.")
    L.append("")
    L.append(f"{len(data['places'])} places · {len(data['regions'])} college districts · "
             f"{len(data['paths'])} walked paths · {len(data['battle_maps'])} battle maps")
    L.append("")

    L.append("## Colleges")
    L.append("")
    L.append("| Key | College |")
    L.append("|---|---|")
    for key, name in col.items():
        L.append(f"| `{key}` | {name} |")
    L.append("")

    L.append("## Districts")
    L.append("")
    L.append("Each district is one SVG path on the campus map, drawn at 16% opacity "
             "in its college colour.")
    L.append("")
    L.append("| College | Path | Label at |")
    L.append("|---|---|---|")
    for r in data["regions"]:
        L.append(f"| {col.get(r['college'], r['college'])} | `{r['d']}` | "
                 f"({_js_num(r['lx'])}, {_js_num(r['ly'])}) |")
    L.append("")

    L.append("## Places")
    L.append("")
    L.append("Pins are in campus SVG coordinates (a 1000×700 viewBox, y down).")
    L.append("")
    by_college: dict[str, list[dict]] = {}
    for p in data["places"]:
        by_college.setdefault(p["college"], []).append(p)
    for ckey, ps in by_college.items():
        L.append(f"### {col.get(ckey, ckey)}")
        L.append("")
        for p in sorted(ps, key=lambda q: q["name"]):
            L.append(f"- **{p['name']}** (`{p['id']}`) — pin at "
                 f"({_js_num(p['x'])}, {_js_num(p['y'])})  ")
            L.append(f"  {p['description']}")
        L.append("")

    L.append("## Paths")
    L.append("")
    L.append("The dashed walkways. Each is a straight run between two pins.")
    L.append("")
    for pts in data["paths"]:
        if len(pts) == 2:
            (x1, y1), (x2, y2) = pts
            L.append(f"- ({x1}, {y1}) → ({x2}, {y2})")
        else:
            L.append(f"- {' → '.join(f'({x}, {y})' for x, y in pts)}")
    L.append("")

    L.append("## River")
    L.append("")
    L.append(f"`{data['river']}`")
    L.append("")
    L.append("A cubic curve, kept verbatim rather than sampled: the page draws it "
             "at 70% opacity across the lower third of the campus.")
    L.append("")

    L.append("## Battle maps")
    L.append("")
    L.append("The page's five maps, and the engine file each was ported to. "
             "Squares are 5 ft; `A1` is x=0, y=0.")
    L.append("")
    L.append("| Page key | Engine map | Name | Size | Features | Tokens |")
    L.append("|---|---|---|---|---|---|")
    for bm in data["battle_maps"]:
        L.append(f"| `{bm['page_key']}` | `{bm['map']}` | {bm['name']} | "
                 f"{bm['width']}×{bm['height']} | {len(bm['features'])} | "
                 f"{len(bm['tokens'])} |")
    L.append("")
    for bm in data["battle_maps"]:
        L.append(f"### {bm['name']} — `{bm['map']}.json`")
        L.append("")
        L.append(f"{bm['width']}×{bm['height']} squares "
                 f"({bm['width'] * 5}×{bm['height'] * 5} ft). {bm['info']}")
        L.append("")
        if bm["zones"]:
            L.append(f"Zone lines at x = {', '.join(str(z) for z in bm['zones'])}.")
            L.append("")
        if bm["features"]:
            L.append("| Type | x | y | w | h | Label |")
            L.append("|---|---|---|---|---|---|")
            for f in bm["features"]:
                L.append(f"| {TERRAIN_LEGEND.get(f['type'], f['type'])} | {f['x']} | "
                         f"{f['y']} | {f['w']} | {f['h']} | {f['label'] or ''} |")
            L.append("")
        if bm["tokens"]:
            L.append("| Token | Name | Side | Square |")
            L.append("|---|---|---|---|")
            for t in bm["tokens"]:
                L.append(f"| `{t['id']}` | {t['name']} | "
                         f"{TOKEN_SIDE.get(t['color'], t['color'])} | `{t['square']}` |")
            L.append("")

    if data["problems"]:
        L.append("## Differences from the engine")
        L.append("")
        for p in data["problems"]:
            L.append(f"- {p}")
        L.append("")

    return "\n".join(L)


def extract(path: pathlib.Path = REFERENCE) -> dict:
    html = read_text(path)
    bmaps = battle_maps(html)
    return {
        "source": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "colleges": colleges(html),
        "regions": regions(html),
        "places": places(html),
        "paths": paths(html),
        "river": river(html),
        "battle_maps": bmaps,
        "problems": reconcile(bmaps),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    ap.add_argument("--out", type=pathlib.Path, help="write here instead of stdout")
    ap.add_argument("--check", action="store_true",
                    help="report differences against display/maps/ and write nothing")
    ap.add_argument("--reference", type=pathlib.Path, default=REFERENCE,
                    help="the reference page (default: %(default)s)")
    args = ap.parse_args(argv)

    if not args.reference.exists():
        print(f"no reference page at {args.reference}", file=sys.stderr)
        return 2
    try:
        data = extract(args.reference)
    except ExtractError as exc:
        print(f"cannot parse {args.reference.name}: {exc}", file=sys.stderr)
        return 2

    if args.check:
        for p in data["problems"]:
            print(p)
        if data["problems"]:
            return 1
        print(f"ok: {len(data['places'])} places, {len(data['battle_maps'])} battle maps, "
              f"all matching display/maps/")
        return 0

    text = json.dumps(data, indent=2, ensure_ascii=False) if args.json else to_markdown(data)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text.splitlines())} lines)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
