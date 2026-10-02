"""overview_map.py: the campaign's overview map, a `kind: overview` spec.

An overview map is the map the story is set on (the Strixhaven campus), not a
battle map. It has no cell grid, so it is not a `display/maps/*.json` file and
never goes near `tactics.maps.compile_map`: a pin on it is a **fraction** of the
image, `0..1` on each axis, the same coordinate system `tactics/scenes.py` uses
for the party marker, and for the same reason (a fraction survives the art
being re-exported at another size; a pixel does not).

Where it lives
--------------

``<campaign>/maps/overview/<slug>.json``, campaign-side, because the art is the
campaign's (often copyrighted, never committed to this repo) and the spec names
it by a campaign-relative path. JSON is the source of truth (BV4 Option Y,
decided 2026-10-02): Chartdown stays an export, so nothing here parses `.cd`.

The spec::

    {"kind": "overview", "slug": "campus", "name": "Strixhaven Campus",
     "image": "maps/chartdown/strixhaven-campus.player.svg",
     "extent": [1400, 1895],
     "pins": [{"id": "biblioplex", "label": "Biblioplex",
               "x": 0.5, "y": 0.4934, "revealed": true}]}

What a browser may see
----------------------

The display's audience is the player, and the player must stay unspoiled, so
the two rules `pins.py` applies are applied here too, and for the same reasons:

* **`revealed` fails closed.** A pin is on the page only when it says
  ``"revealed": true``; absent, ``null`` and ``"yes"`` all mean no. The filter
  is `revealed()`, and the route calls it before anything is rendered, so an
  unrevealed pin's label and position never reach the HTML.
* **The image is an allow-list, not a deny-list.** It must be an image file
  under ``maps/``, and the rule is re-applied to the path the filesystem
  resolved, so a ``maps/x.svg`` symlink pointing elsewhere in the campaign is
  refused for what it opens. A Chartdown ``.gm.svg`` is refused by name: that
  is the GM render, the one file under ``maps/`` that is a spoiler by
  convention.

Every refusal on the read path raises one message, so the route cannot be used
to ask which files exist. The specific reason is in ``validate``'s message,
for a GM running it from a shell.
"""

from __future__ import annotations

import json
import math
import pathlib
import re

from paths import campaign_path

KIND = "overview"
# The one folder an overview image may come from. See the module docstring.
IMAGE_DIR = "maps"
IMAGE_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg", ".webp")
MAX_LABEL = 120
MAX_PINS = 200
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PIN_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")


class OverviewMapError(ValueError):
    """An overview spec, or an overview image, this module refuses."""


def _number(value, what: str) -> float:
    """A finite number. ``True`` is refused: it is an ``int`` in Python."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OverviewMapError(f"{what} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise OverviewMapError(f"{what} must be a finite number")
    return number


def _check_image(rel: str) -> str:
    """Raise unless `rel` names an image file under ``maps/`` and is no GM render.

    Called twice, on the spelled path and on the resolved one, so the two can
    never be checked by two different rules.
    """
    pure = pathlib.PurePosixPath(rel)
    if pure.parts[:1] != (IMAGE_DIR,) or len(pure.parts) < 2:
        raise OverviewMapError(f"an overview image lives under {IMAGE_DIR}/")
    if pure.suffix.lower() not in IMAGE_SUFFIXES:
        raise OverviewMapError("an overview image is " + ", ".join(IMAGE_SUFFIXES))
    if ".gm." in pure.name.lower():
        raise OverviewMapError("a .gm. render is the GM's copy; use the player one")
    return rel


def validate(spec) -> dict:
    """One clean overview spec, or raise OverviewMapError with the reason.

    Pins keep ``revealed`` as a strict boolean (``is True``), so a hand-edited
    file cannot reveal a pin by writing something truthy.
    """
    if not isinstance(spec, dict) or spec.get("kind") != KIND:
        raise OverviewMapError(f"an overview spec says \"kind\": \"{KIND}\"")
    slug = spec.get("slug")
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise OverviewMapError("an overview spec needs a plain slug")
    image = spec.get("image")
    if not isinstance(image, str) or not image.strip():
        raise OverviewMapError("an overview spec needs an image path")
    image = image.strip()
    if "\\" in image or image.startswith("/") or any(
            seg in ("", ".", "..") for seg in image.split("/")):
        raise OverviewMapError("an overview image is a plain campaign-relative path")
    _check_image(image)
    extent = spec.get("extent")
    if not isinstance(extent, list) or len(extent) != 2:
        raise OverviewMapError("extent is a [width, height] pair")
    extent = [_number(v, "extent") for v in extent]
    if min(extent) <= 0:
        raise OverviewMapError("extent has no positive size in it")
    pins = spec.get("pins", [])
    if not isinstance(pins, list):
        raise OverviewMapError("pins is a list")
    if len(pins) > MAX_PINS:
        raise OverviewMapError(f"an overview map carries at most {MAX_PINS} pins")
    clean, seen = [], set()
    for pin in pins:
        if not isinstance(pin, dict):
            raise OverviewMapError("a pin is an object")
        pin_id = pin.get("id")
        if not isinstance(pin_id, str) or not _PIN_ID.fullmatch(pin_id):
            raise OverviewMapError("a pin id is letters, digits, dash or underscore")
        if pin_id in seen:
            raise OverviewMapError(f"pin id {pin_id!r} is used twice")
        seen.add(pin_id)
        label = pin.get("label")
        if not isinstance(label, str) or not label.strip():
            raise OverviewMapError(f"pin {pin_id!r} needs a label")
        point = {}
        for axis in ("x", "y"):
            value = _number(pin.get(axis), f"pin {pin_id!r} {axis}")
            if not 0.0 <= value <= 1.0:
                raise OverviewMapError(
                    f"pin {pin_id!r} {axis}={value} is not a fraction of the image (0..1)")
            point[axis] = value
        clean.append({"id": pin_id, "label": " ".join(label.split())[:MAX_LABEL],
                      **point, "revealed": pin.get("revealed") is True})
    name = spec.get("name")
    return {"kind": KIND, "slug": slug,
            "name": " ".join(name.split())[:MAX_LABEL] if isinstance(name, str) and name.strip() else slug,
            "image": image, "extent": extent, "pins": clean}


def _fraction(point, what: str) -> tuple:
    """A normalized image point as an ``(x, y)`` pair."""
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise OverviewMapError(f"{what} is an (x, y) fraction pair")
    x, y = (_number(value, f"{what} {axis}")
            for value, axis in zip(point, ("x", "y")))
    if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
        raise OverviewMapError(f"{what} coordinates must be fractions of the image (0..1)")
    return x, y


def _image_size(size) -> tuple:
    """A positive ``(width, height)`` image extent."""
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise OverviewMapError("image size is a [width, height] pair")
    width, height = (_number(value, "image size") for value in size)
    if width <= 0 or height <= 0:
        raise OverviewMapError("image size must have positive width and height")
    return width, height


def fraction_to_feet(start, end, image_size, ft_per_px: float) -> float:
    """Distance in feet between image-fraction points at a supplied scale.

    ``image_size`` is the source image's ``(width, height)`` in pixels;
    ``ft_per_px`` is an explicit scale, commonly from :func:`calibrate`.
    """
    x1, y1 = _fraction(start, "start")
    x2, y2 = _fraction(end, "end")
    width, height = _image_size(image_size)
    scale = _number(ft_per_px, "ft_per_px")
    if scale <= 0:
        raise OverviewMapError("ft_per_px must be positive")
    return math.hypot((x2 - x1) * width, (y2 - y1) * height) * scale


def bearing(start, end, image_size, north_deg: float = 0) -> str:
    """Eight-point compass bearing from one image fraction to another.

    Image y increases downward. ``north_deg`` is the true bearing of image-up,
    measured clockwise from north, so zero means image-up is north. Ties at
    sector boundaries round clockwise.
    """
    x1, y1 = _fraction(start, "start")
    x2, y2 = _fraction(end, "end")
    width, height = _image_size(image_size)
    north = _number(north_deg, "north_deg")
    east = (x2 - x1) * width
    northing = -(y2 - y1) * height
    if east == 0 and northing == 0:
        raise OverviewMapError("bearing points are coincident")
    degrees = (math.degrees(math.atan2(east, northing)) + north) % 360.0
    index = int(math.floor((degrees + 22.5 + 1e-12) / 45.0)) % 8
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[index]


def calibrate(start, end, real_feet: float, image_size) -> float:
    """Calculate feet per pixel from two image fractions and a real distance."""
    x1, y1 = _fraction(start, "first reference pin")
    x2, y2 = _fraction(end, "second reference pin")
    width, height = _image_size(image_size)
    distance = _number(real_feet, "real distance")
    if distance <= 0:
        raise OverviewMapError("real distance must be positive")
    pixels = math.hypot((x2 - x1) * width, (y2 - y1) * height)
    if pixels == 0:
        raise OverviewMapError("calibration pins are coincident")
    return distance / pixels


def revealed(spec: dict) -> dict:
    """The spec as a players' page may carry it: unrevealed pins are absent.

    Absent, not flagged, for the reason `pins.revealed` gives: a flag would tell
    the table a secret exists at a point. The ``revealed`` key itself is
    dropped too, since every pin left is revealed.
    """
    shown = [{k: v for k, v in p.items() if k != "revealed"}
             for p in spec["pins"] if p.get("revealed") is True]
    return {**spec, "pins": shown}


def spec_path(camp_dir, slug: str) -> pathlib.Path:
    """Where one overview spec lives. A slug that needs cleaning is refused."""
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise OverviewMapError("no such overview map")
    return pathlib.Path(camp_dir) / "maps" / "overview" / f"{slug}.json"


def available(camp_dir) -> list:
    """The slugs of every overview spec in the campaign, sorted. May be empty."""
    folder = pathlib.Path(camp_dir) / "maps" / "overview"
    try:
        return sorted(p.stem for p in folder.glob("*.json")
                      if _SLUG.fullmatch(p.stem))
    except OSError:
        return []


def load(camp_dir, slug: str) -> tuple:
    """``(spec, image_path)`` for one overview map, or raise OverviewMapError.

    The spec is validated and its image resolved through `campaign_path`
    (containment, symlinks included), then the image rule is applied again to
    the resolved path. Every refusal here is the same "no such overview map".
    """
    try:
        path = spec_path(camp_dir, slug)
        spec = validate(json.loads(path.read_text(encoding="utf-8")))
        if spec["slug"] != slug:
            raise OverviewMapError("no such overview map")
        root = pathlib.Path(camp_dir).expanduser().resolve()
        image = campaign_path(root, spec["image"])
        _check_image(image.relative_to(root).as_posix())
        if not image.is_file():
            raise OverviewMapError("no such overview map")
    except (OSError, ValueError) as exc:     # OverviewMapError is a ValueError
        raise OverviewMapError("no such overview map") from exc
    return spec, image
