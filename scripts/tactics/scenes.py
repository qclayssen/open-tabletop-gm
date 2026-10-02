"""scenes.py: the map that exists when no fight is running.

A *scene* is the map the story is happening on, and it has to be the thing
combat borrows rather than owns. Today the campaign has exactly one map slot,
`combat/encounter.json`, and `cli._end` writes the literal `*(none)*` over the
`## Active Combat` section of `state.md` when a fight finishes. That is not
"combat stopped": it is the world being erased, so "the campus is the background
of the story" has nowhere to survive between sessions.

So this is the second owner. `encounter.json` is combat's: a fight's grid, its
tokens, its HP, and it is overwritten at the start of the next fight. `scene.json`
is the world's: one per campaign, the map, its background and where the party is
standing on it. Neither knows about the other beyond the section of `state.md`
that names whichever is current.

## What a marker is, and where it lives

The party's position on a scene is a **token**, at a **fraction** of the
background's own size, not a cell and not a pixel. A region map has no cell
grid, and a pixel does not survive the artwork being re-exported. A fraction
does, which is the whole reason the coordinate system is a fraction and the
whole reason the same file works for the campus SVG today and a 4000px re-render
tomorrow.

## The one scale factor, and why there is only one

A Chartdown `.cd` declares its `extent` (`1400x1895` on the Strixhaven campus)
and the rendered SVG carries a `viewBox` (`0 0 820 1109.93`). Those two are the
same drawing at two sizes, so the mapping between them is **one number**:
`820 / 1400 = 0.5857142857...`, and it is applied to both axes by `scale_point`.

The tempting alternative is to convert each axis with its own ratio. It is
wrong by almost nothing: the two aspects are `1895/1400 = 1.3535714` and
`1109.93/820 = 1.3535732`, which differ by 1.3e-6 because the viewBox height is
rounded to two decimals in the file. A per-axis conversion therefore misplaces
the Biblioplex by about a thousandth of a pixel and looks perfect, which is the
worst possible failure, because nothing anywhere reports it. So the comparison
is made structurally. `uniform_scale` reads both ratios, refuses when they
disagree by more than the viewBox rounding can explain, and returns a single
float. Every conversion multiplies by that float. There is no code path in this
module that can produce a per-axis result.

**And note who calls it: today, only the tests.** `fraction()` is what
`scene.json` stores, and it divides by the extent rather than multiplying by the
scale, so it needs no viewBox and stays right if the artwork is re-exported at
another size. That is the better coordinate system and it is the one in use.
`viewbox`/`uniform_scale`/`scale_point` are the `.cd`-to-rendered-pixels half,
kept because a caller that *does* need to address the SVG (a hand-placed pin, a
draw command, a future drag that starts from a pixel) must not be handed a
per-axis conversion by someone who does not know this paragraph. Their being
test-only is therefore a fact about today's callers, not a dead-code claim: the
one thing they exist to prevent is a plausible future mistake.

## What this deliberately does not do

  * **It does not implement pins.** `scripts/pins.py` (BV4) owns markers that
    open a note or another map, and it is the right owner: a pin is an
    annotation on a battle map, a scene is the map itself. A scene owns the
    map, a pin opens something, the party marker is a token. Forking pins here
    would put two formats for one idea in one campaign.
  * **It does not touch `maps.compile_map`.** The same invariant `formations`
    is built on: the map format is shared across campaigns and must not restate
    campaign data. A map that carries the party's position is one map per
    campaign.
  * **It adds nothing to `Encounter`.** Combat and the world are separate
    owners. The one thing they share is a line of `state.md`.
  * **It does not decide who may see a marker.** `revealed()` says what belongs
    on a players' screen; the display's Flask layer is what enforces it,
    because the engine does not know who is looking. See `revealed()`.

## Where it lives

`<campaign>/scene.json`, beside `combat/encounter.json`, `tracker.json` and
`state.md`. Plain JSON, written atomically with a `.bak` alongside and
`encoding="utf-8"` so a campaign on a non-UTF-8 Windows locale still loads.
`SCHEMA_VERSION` plus a `MIGRATIONS` table, because the first version of a
persisted format is the version that has to be migrated.

Spec: `docs/specs/SPEC-persistent-scene.md`.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re

import safeio   # scripts/safeio.py (on sys.path via tactics/__init__)
from slug import slug as shared_slug

from .schemas import (BooleanField, ListField, NumberField, OptionalField,
                      SchemaField, StringField)

SCENE_SCHEMA = "otg-scene"
SCHEMA_VERSION = 1

# An empty table is not an oversight. `formations.py` shipped with one migration
# in it, and this is the file that will need the first one: the loader walks
# (version, SCHEMA_VERSION) pairs, and an empty dict says nothing has been
# migrated yet, which is the truth for a format with exactly one version so far.
MIGRATIONS: dict[tuple[int, int], object] = {}

# Decimals kept on a fractional coordinate. Six rather than formations' four,
# because the fraction *is* the format here: the cell offsets formations also
# stores do not survive a change of grid, and this one has nothing to fall back
# on. Six places on a 1895-unit map is a fortieth of a pixel.
ROUNDING = 6

# How far the two per-axis ratios may differ and still be one number. The campus
# renders at 1.3e-6 apart because the SVG height is written to two decimals, so
# there are three orders of magnitude of room for that rounding and none at all
# for an export that genuinely stretches one axis. Refusing is the point: the
# alternative is a map that looks right and is not.
ASPECT_TOLERANCE = 1e-4

DEFAULT_MARKER_NAME = "The party"
NO_COMBAT = "*(none)*"


class SceneError(ValueError):
    """A scene, a scene file, or a Chartdown source this module refuses.

    One exception for every refusal, on purpose, so the CLI turns one into a
    sentence for the GM rather than a traceback.
    """


# ─── the schema ───────────────────────────────────────────────────────────────

SCENE_FIELDS = SchemaField({
    "schema": StringField(choices=(SCENE_SCHEMA,)),
    "version": NumberField(),
    # The name the GM calls the scene, for display text. Falls back to the `.cd`
    # title, which is the map's own name.
    "name": OptionalField(StringField()),
    # The map's slug: the `.cd` stem, and the stem of the committed SVG beside
    # it. A slug and not a path, for the same reason `pins.pins_path` takes a
    # slug: two inputs quietly sanitised to the same file put one scene's
    # marker on another scene's map.
    "map": StringField(),
    # Campaign-relative path to the background artwork, drawn by the display's
    # existing traversal-guarded route. The committed SVG, never the JPG in
    # `maps/art/`: that one is gitignored third-party art.
    "background": StringField(),
    # The `.cd` extent the stored fractions are of. `[w, h]`, checked as a pair
    # in `validate()` because ListField has no length argument.
    "extent": ListField(NumberField()),
    "marker": SchemaField({
        "name": StringField(),
        # The place this marker was snapped to, or None when it was placed by
        # hand. Recorded so a diff says *where* and not only *how far along*.
        "place": OptionalField(StringField()),
        "x": NumberField(),
        "y": NumberField(),
        # On the players' screen, or not. See `revealed()` for what this is and
        # is not.
        "revealed": BooleanField(),
    }, required=("name", "x", "y", "revealed")),
    "saved": OptionalField(StringField()),
}, required=("schema", "version", "map", "background", "extent", "marker"))


# ─── naming and paths ─────────────────────────────────────────────────────────

def slug(name: str) -> str:
    """The handle a place or a scene is written and looked up under."""
    return shared_slug(name)


def _plain_slug(name, what: str) -> str:
    """A slug passed through untouched, or refused.

    Same rule as `pins.pins_path`: sanitising would turn two different inputs
    into one file, so a value that needs cleaning is a value that is wrong.
    """
    text = str(name).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text):
        raise SceneError(f"a scene {what} needs a plain slug, not {name!r}")
    return text


def scene_path(camp_dir) -> pathlib.Path:
    """Where this campaign's one scene lives."""
    return pathlib.Path(camp_dir) / "scene.json"


def cd_path(camp_dir, map_slug: str) -> pathlib.Path:
    """The Chartdown source for one map, inside the campaign."""
    return pathlib.Path(camp_dir) / "maps" / "chartdown" / f"{_plain_slug(map_slug, 'map')}.cd"


def background_rel(map_slug: str) -> str:
    """The background a scene defaults to: the committed *player* SVG.

    The pair beside the `.cd` is `.player.svg` and `.gm.svg`, and the player one
    is the right default for the same reason the JPG in `maps/art/` is not used
    at all: the player SVG is committed, reviewable and free.
    """
    return f"maps/chartdown/{_plain_slug(map_slug, 'map')}.player.svg"


# ─── the Chartdown reader ─────────────────────────────────────────────────────
#
# A minimal parser for the two things this module needs from a `.cd`: the header
# keys before the first `[section]`, and the placement lines of `[features]`.
# Not a Chartdown implementation, and it does not try to be one: no node, no
# @chartdown/core, nothing shelled out. A line it cannot read is reported as a
# problem *inside the record*, never replaced by a guessed coordinate.

_HEADER_KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.*?)\s*$")
_EXTENT = re.compile(r"^\s*(?P<w>\d+(?:\.\d+)?)\s*[xX]\s*(?P<h>\d+(?:\.\d+)?)\s*$")
_FEATURE_HEAD = re.compile(
    r"^(?P<kind>[A-Za-z][A-Za-z0-9_-]*)\s+(?P<slug>[A-Za-z0-9_-]+)"
    r"""(?:\s+(?P<quote>["'])(?P<label>.*?)(?P=quote))?\s*$"""
)
_POINT = re.compile(r"^\(\s*(?P<x>-?\d+(?:\.\d+)?)\s*,\s*(?P<y>-?\d+(?:\.\d+)?)\s*\)$")


def _read(path) -> str:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SceneError(f"no Chartdown source at {path}") from exc
    except UnicodeDecodeError as exc:
        raise SceneError(f"{path} is not UTF-8: {exc}") from exc
    except OSError as exc:
        raise SceneError(f"cannot read {path}: {exc}") from exc


def _strip_comment(line: str) -> str:
    """Drop a trailing `;` comment, ignoring a `;` inside a quoted label."""
    out, quoted = [], None
    for ch in line:
        if quoted:
            if ch == quoted:
                quoted = None
        elif ch in "\"'":
            quoted = ch
        elif ch == ";":
            break
        out.append(ch)
    return "".join(out)


def cd_header(path) -> dict:
    """The keys before the first `[section]`: title, `map`, `extent`, `compass`.

    A real parse, not a grep. An `extent` that is not `WxH` is a refusal naming
    the offending text, because the extent is the denominator every stored
    fraction is measured against and a wrong one is a marker in the wrong half
    of the map.
    """
    header: dict = {"title": "", "map": "", "compass": ""}
    for raw in _read(path).splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            break                                    # the header ends at the first section
        if line.startswith("#"):
            header["title"] = line.lstrip("#").strip()
            continue
        found = _HEADER_KEY.match(line)
        if not found:
            continue
        key, value = found.group("key").lower(), found.group("value")
        if key == "extent":
            size = _EXTENT.match(value)
            if not size:
                raise SceneError(
                    f"{path}: extent {value!r} is not WxH, so every stored "
                    "fraction would be measured against a size that is not there")
            header["extent"] = (float(size.group("w")), float(size.group("h")))
        else:
            header[key] = value
    return header


def cd_extent(path) -> tuple[float, float]:
    """The `.cd`'s declared extent, or a refusal naming what was declared."""
    extent = cd_header(path).get("extent")
    if not extent:
        raise SceneError(f"{path} declares no extent, so a marker has nothing "
                         "to be a fraction of")
    return extent


def places(path) -> dict:
    """The `[features]` block, keyed by slug: `{slug: {name, kind, label, x, y}}`.

    Every entry has the same keys. A line this parser cannot read is returned
    with an `error` and **no** `x`/`y`, so a caller that wants a coordinate has
    to notice the failure rather than reading a default. Guessing is the one
    unforgivable move in this module: a landmark silently placed at 0,0 is a
    party in the wrong country, and nothing downstream can tell.
    """
    found: dict = {}
    in_features = False
    for raw in _read(path).splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            in_features = line.lower() == "[features]"
            continue
        if not in_features:
            continue
        record = _feature(line)
        if record is None:
            continue
        entry = found.setdefault(record["slug"], {
            "slug": record["slug"], "kind": "", "label": "", "x": None, "y": None,
            "error": None,
        })
        if entry["x"] is not None:
            entry["error"] = (f"{record['slug']} is placed twice in [features]; "
                              "the first placement is kept")
            continue
        entry["kind"] = record["kind"] or entry["kind"]
        entry["label"] = record["label"] or entry["label"]
        if record["error"]:
            entry["error"] = record["error"]
            continue
        entry["x"], entry["y"] = record["x"], record["y"]
    return found


def _feature(line: str) -> dict | None:
    """One `[features]` line -> a record, or None when it is not a place.

    The grammar here is `kind slug ["label"] : (x,y)`, which is all the campus
    uses. An entry placed as a shape (`area (0,0) (10,10)`) is a *feature* that
    is not a *place*, so it comes back with an error rather than being dropped:
    a place the GM can read in the source and cannot reach from `here` is worth
    a sentence.
    """
    head, sep, tail = line.rpartition(":")
    if not sep:
        return None
    parsed = _FEATURE_HEAD.match(head.strip())
    if not parsed:
        return None                                  # a course or a range, not a place
    record = {"slug": parsed.group("slug").lower(),
              "kind": parsed.group("kind").lower(),
              "label": (parsed.group("label") or "").strip(),
              "x": None, "y": None, "error": None}
    point = _POINT.match(tail.strip())
    if not point:
        record["error"] = (f"{record['kind']} {record['slug']} is placed "
                           f"{tail.strip() or 'with nothing at all'}, which is not one "
                           "point; a scene marker can only snap to a single coordinate")
        return record
    record["x"] = float(point.group("x"))
    record["y"] = float(point.group("y"))
    return record


def find_place(path, name: str) -> dict:
    """One place, by slug or by label, case-insensitively. Raises `SceneError`.

    Both spellings are accepted because the GM has both in front of them: the
    slug is in the `.cd`, and the label is what is written on the artwork.
    """
    everything = places(path)
    key = slug(name)
    if key in everything:
        return everything[key]
    folded = _fold(name)
    for entry in everything.values():
        if entry["label"] and _fold(entry["label"]) == folded:
            return entry
    known = sorted(everything)
    shown = ", ".join(known[:12]) + (", ..." if len(known) > 12 else "")
    raise SceneError(f"no place {name!r} in {path}. Places: {shown or '(none)'}")


def _fold(name) -> str:
    """A comparison key for a label, so `Bow's End` matches `bows end`."""
    return re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower()).strip()


# ─── the scale, and only one of it ────────────────────────────────────────────

_VIEWBOX = re.compile(
    r"""viewBox\s*=\s*["']\s*([-\d.eE+]+)[\s,]+([-\d.eE+]+)[\s,]+"""
    r"""([-\d.eE+]+)[\s,]+([-\d.eE+]+)\s*["']"""
)


def viewbox(path) -> tuple[float, float]:
    """The width and height of an SVG's `viewBox`, or a refusal.

    Read from the committed file rather than from the `.cd`, because the viewBox
    is the renderer's own statement of how big it drew the thing. A `.cd`
    coordinate is worth converting into; a `.cd` coordinate already in rendered
    pixels would be a guess.
    """
    found = _VIEWBOX.search(_read(path))
    if not found:
        raise SceneError(f"{path} declares no viewBox, so its rendered size is unknown")
    try:
        width, height = float(found.group(3)), float(found.group(4))
    except ValueError as exc:
        raise SceneError(f"{path} has an unreadable viewBox: {exc}") from exc
    if width <= 0 or height <= 0:
        raise SceneError(f"{path} has a viewBox of {width:g}x{height:g}")
    return width, height


def uniform_scale(extent, view) -> float:
    """**The** factor from a `.cd` extent to rendered SVG pixels. One float.

    Both axes' ratios are computed and compared before either is used, and a
    pair that differs by more than `ASPECT_TOLERANCE` is a refusal rather than
    an average. Averaging would be the quiet version of the per-axis bug: it
    returns a number, reports nothing, and is wrong on one axis. Refusing puts
    both numbers in the message, where the GM can go and look at the export.
    """
    ew, eh = (float(v) for v in extent)
    vw, vh = (float(v) for v in view)
    if min(ew, eh, vw, vh) <= 0:
        raise SceneError(f"extent {ew:g}x{eh:g} and viewBox {vw:g}x{vh:g} cannot be "
                         "scaled together: one of them has no size")
    fx, fy = vw / ew, vh / eh
    if abs(fx - fy) > ASPECT_TOLERANCE * max(fx, fy):
        raise SceneError(
            f"this is not one scale: {extent[0]:g}x{extent[1]:g} would need "
            f"{fx:.9f} across and {fy:.9f} down, which differ by more than the "
            f"{ASPECT_TOLERANCE:g} that viewBox rounding explains. One factor cannot "
            "place both axes, and a per-axis conversion would be wrong by a fraction "
            "of a pixel with nothing to report it")
    return fx


def scale_point(point, scale: float) -> tuple[float, float]:
    """A `.cd` point in rendered pixels: **one** factor, both axes.

    This is the only coordinate conversion in the module, and it is one
    multiplication per axis by the same number. There is no second reading of
    `uniform_scale` to reach for, which is what makes the single-factor property
    structural rather than a promise.
    """
    x, y = (float(v) for v in point)
    return x * scale, y * scale


def fraction(point, extent) -> tuple[float, float]:
    """A `.cd` point as a fraction of `extent`. What `scene.json` stores.

    Independent of the rendered size on purpose: a fraction of a background is
    the same fraction whether the background is 820px or 4000px, so this needs
    no viewBox, no scale, and nothing else to go stale.
    """
    x, y = (float(v) for v in point)
    w, h = (float(v) for v in extent)
    if min(w, h) <= 0:
        raise SceneError(f"an extent of {w:g}x{h:g} has no fraction of it")
    return round(x / w, ROUNDING), round(y / h, ROUNDING)


# ─── build, validate, store ───────────────────────────────────────────────────

def blank(camp_dir, map_slug: str, *, name: str = "", background=None,
          extent=None) -> dict:
    """A scene on `map_slug`, with its size known and no party on it yet.

    `background` defaults to the committed `.player.svg` beside the `.cd`, and
    `extent` and `name` come out of that `.cd`'s own header, so the GM types a
    map name and gets a scene that is already the right size and the right
    title.
    """
    slugged = _plain_slug(map_slug, "map")
    header = cd_header(cd_path(camp_dir, slugged))
    return {
        "schema": SCENE_SCHEMA,
        "version": SCHEMA_VERSION,
        "name": str(name).strip() or header.get("title", ""),
        "map": slugged,
        "background": background or background_rel(slugged),
        "extent": [float(v) for v in (extent or header.get("extent") or ())],
        "marker": {"name": DEFAULT_MARKER_NAME, "place": None,
                   "x": 0.0, "y": 0.0, "revealed": True},
    }


def validate(spec: dict) -> list:
    """Problems with a scene file, as sentences. Empty means it is usable."""
    try:
        SCENE_FIELDS.validate(spec)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    extent = spec.get("extent")
    if not isinstance(extent, list) or len(extent) != 2:
        problems.append(f"extent {extent!r} is not a [width, height] pair; a marker's "
                        "fraction needs a size to be a fraction of")
    elif min(extent) <= 0:
        problems.append(f"extent {extent} has no positive size in it")
    marker = spec.get("marker") or {}
    for key in ("x", "y"):
        value = marker.get(key)
        if isinstance(value, (int, float)) and not 0.0 <= float(value) <= 1.0:
            problems.append(f"marker {key}={value} is outside 0..1, so it was not "
                            f"placed on this scene's extent {extent}")
    # A slug and not a path, checked here as well as in `_plain_slug`: a scene
    # file is hand-editable and `load` reads whatever is in it. The same rule
    # `pins.pins_path` applies, for the same reason, which is that sanitising
    # two different map names into one file puts one scene's marker on another
    # scene's background.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(spec.get("map") or "")):
        problems.append(f"map {spec.get('map')!r} is not a plain slug")
    return problems


def save(camp_dir, spec: dict) -> pathlib.Path:
    """Validate, then write atomically, keeping the previous version as `.bak`.

    Validation first, so a refused scene leaves the filesystem exactly as it
    found it. `safeio` does the temp-file-plus-`os.replace` and the fsync that
    `formations.save` and `state.save` both rely on, because a half-written scene
    is worse than no scene and the two read the same to anybody who has to.
    """
    problems = validate(spec)
    if problems:
        raise SceneError("refusing to save a scene that will not place a marker: "
                         + "; ".join(problems))
    path = scene_path(camp_dir)
    safeio.atomic_write_text(path, json.dumps(spec, indent=1, ensure_ascii=False) + "\n")
    return path


def load(camp_dir) -> dict | None:
    """This campaign's scene, or None when there is none.

    None rather than an exception: a campaign that has never had a scene is the
    normal case, and every caller, `cli._end` above all, has to treat it as a
    no-op instead of a failure. A file that will not parse or will not validate
    is also None, for the same reason, and `save` refuses to overwrite one
    until a human has read what it said.
    """
    path = scene_path(camp_dir)
    if not path.exists():
        return None
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(spec, dict):
        return None
    version = int(spec.get("version") or 0)
    while (version, SCHEMA_VERSION) in MIGRATIONS:
        spec = MIGRATIONS[(version, SCHEMA_VERSION)](spec)
        version = int(spec.get("version") or 0)
    if version != SCHEMA_VERSION:
        return None
    return spec if not validate(spec) else None


def snap(spec: dict, where: dict, *, name=None, revealed=None) -> dict:
    """`spec` with its marker moved onto the place `where` describes.

    `where` is a record from `places()`. One carrying an `error` is refused: the
    module is built on not inventing a coordinate, and the caller cannot tell a
    place that failed to parse from one that parsed to (0,0).

    `revealed` defaults to whatever the marker already said, and an explicit
    `False` is honoured: the GM authored this, so a marker they deliberately hid
    stays hidden.
    """
    if where.get("error") or where.get("x") is None or where.get("y") is None:
        raise SceneError(where.get("error")
                         or f"place {where.get('slug')!r} has no coordinate to snap to")
    extent = spec.get("extent")
    if not isinstance(extent, list) or len(extent) != 2:
        raise SceneError("this scene records no extent, so a place cannot be stored "
                         "as a fraction of it")
    marker = dict(spec.get("marker") or {})
    x, y = fraction((where["x"], where["y"]), extent)
    marker.update({"name": str(name).strip() if name else marker.get("name") or DEFAULT_MARKER_NAME,
                   "place": where.get("slug"),
                   "x": x, "y": y})
    if revealed is not None:
        marker["revealed"] = bool(revealed)
    out = dict(spec)
    out["marker"] = marker
    out["saved"] = _dt.date.today().isoformat()
    return out


# ─── who may see it ───────────────────────────────────────────────────────────

def revealed(spec) -> dict | None:
    """The marker that belongs on a players' screen, or None.

    The field is `revealed` and it **defaults to True**, which is the opposite
    of `pins.revealed`. The difference is who authors the two: a pin is
    something somebody might add carelessly to a battle map, so forgetting the
    flag there should hide it; a scene marker is placed on purpose with
    `here <place>`, so a missing flag should not make the party invisible.

    The test is `is True` and never truthiness. `not marker.get("revealed",
    True)` passes an absent key, a `null` and a `"yes"` all the same way, and a
    hand-edited `scene.json` is a supported input rather than a hypothetical.

    It is a **table** boundary and not access control, because there is nothing
    to control: `_token_ok()` in the display returns True for every browser off
    `--lan`, and in LAN mode the token is minted into `index.html` for every page
    the app serves. There is no GM route, no viewer parameter and no second
    port. So an unrevealed marker is visible to nobody in a browser, the GM
    included, who reads it from the terminal instead.

    The caller is the display's Flask layer and not `sync.snapshot()`:
    `snapshot(enc, meta)` takes no viewer, and `Encounter.meta` is map display
    info rather than marker state. The precedent for campaign state the display
    reads and filters itself is `world.revealed_clocks`.
    """
    marker = (spec or {}).get("marker")
    if not isinstance(marker, dict) or marker.get("revealed") is not True:
        return None
    return marker


# ─── what /c end leaves behind ────────────────────────────────────────────────

def ended_body(camp_dir) -> str:
    """The `## Active Combat` body for a campaign whose fight has just ended.

    `*(none)*` when there is no scene, which is what `cli._end` wrote before this
    module existed and still writes: a campaign that has never opened a scene
    gets byte-identical output and no new file.

    With a scene, the body says the fight is over **and names the world the
    party is put back into**, with the marker's fraction in it. Combat borrows
    the map slot for the length of a fight, and erasing it on the way out is
    what made "the campus is the background of the story" impossible to keep.
    """
    spec = load(camp_dir)
    if not spec:
        return NO_COMBAT
    marker = spec.get("marker") or {}
    where = marker.get("place") or "no named place"
    return (f"Combat ended. The party is back on {spec.get('name') or spec['map']} "
            f"({spec['map']}), the marker at {float(marker.get('x', 0)):g}, "
            f"{float(marker.get('y', 0)):g} of the background: {where}. "
            f"`scene.json` holds it.")