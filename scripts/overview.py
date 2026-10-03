#!/usr/bin/env python3
"""
overview.py — ask the campaign overview map where one pin is from another.

The overview map is the map the story is set on, not a battle map: it has no
cell grid, and a pin on it is a fraction of the image (see
``scripts/overview_map.py``). So "how far is the library from the rotunda" is a
question the engine cannot answer from a grid, and there are exactly two honest
ways to answer it: feet, which needs a scale, and a compass bearing, which
needs only the two points. This CLI does both and refuses whatever it cannot
answer exactly.

    python3 scripts/overview.py -c <campaign> list
    python3 scripts/overview.py -c <campaign> between --from library --to rotunda
    python3 scripts/overview.py -c <campaign> between --from library --to rotunda --json
    python3 scripts/overview.py -c <campaign> calibrate --from library --to rotunda --feet 400
    python3 scripts/overview.py -c <campaign> calibrate --from library --to rotunda --feet 400 --save

Why a CLI, and what it must never do
------------------------------------
The geometry is already implemented and tested in ``overview_map.py``
(``fraction_to_feet``, ``bearing``, ``calibrate``). This file resolves pin ids
to points and calls those functions. It recomputes nothing: a bearing computed
here rather than in the engine is a second answer to a question the engine
already answers, and the one that drifts.

The feet answer additionally needs a **scale**, and there is a specific trap in
that. An overview map is a picture with no grid, so nothing in the file says how
many feet a pixel is; the GM supplies one by naming two places and the real
distance between them. Ask for feet without one and the tempting answers are
both wrong: assume a pitch and every range at the table is off by a factor the
DM cannot see, or guess from the image size. So this CLI **refuses** rather
than assuming, and says which of the two it is missing.

``--revealed`` note: unrevealed pins are GM-only and are excluded unless asked
for, because this output is the kind of thing that ends up read aloud.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import overview_map as _om
from paths import require_campaign as _require_campaign

# Pins live in one place per overview map. Keys are the pin ids the spec names;
# this is the vocabulary the display already renders, so an id that resolves here
# resolves there too.
_X = "x"
_Y = "y"


class Refused(SystemExit):
    """A refusal, carrying its message so a caller can still read it.

    `pin.py::_fail` returns a bare `SystemExit(1)`, which is enough there because
    every refusal is raised straight out of `main`. It is not enough here:
    `cmd_between` catches the scale refusal so it can still print the bearing,
    and then needs the text to say why the other half is missing. Subclassing
    keeps `raise _fail(...)` working everywhere else unchanged.
    """

    def __init__(self, message: str):
        super().__init__(1)
        self.message = message


class MissingScale(Refused):
    """No scale for this overview map, as distinct from a scale that is wrong.

    The two look alike to a GM and must not look alike in code: "there is no
    scale" leaves the bearing answerable and the distance open, while a scale
    somebody typed and got wrong is a plain refusal. Collapsing them would let a
    typo in `--ft-per-px` print as a soft "not answerable", which is a wrong
    number replaced by no number and no complaint.
    """


def _fail(message: str) -> Refused:
    """Print a refusal and return the exit to raise.

    Printed here and raised by the caller, for the reason `pin.py::_fail` gives:
    CPython writes a `SystemExit` *string* to the real stderr, past any
    `redirect_stderr`, so a refusal cannot be asserted on without a fixture. A GM
    sees the same output either way.
    """
    print(f"overview: {message}")
    return Refused(message)


def _fail_soft(message: str) -> MissingScale:
    """As `_fail`, but a gap in what can be answered rather than an error.

    Prints to **stderr**. A refusal raised out of `main` ends the process, so
    stdout and stderr go to the same place and either is fine; this one does not,
    because `cmd_between` catches it and then prints a JSON document on stdout.
    A human line in front of that document would make `overview.py ... --json |
    jq` fail on the caller's machine rather than in a test.
    """
    print(f"overview: {message}", file=sys.stderr)
    return MissingScale(message)


def _find_pins(spec: dict) -> dict:
    """`{id: pin}` for the pins the caller may see."""
    return {p["id"]: p for p in spec["pins"]}


def _load(camp: pathlib.Path, slug: str):
    """`(spec, raw)` for one overview map, with the scale problem named.

    `overview_map.load` collapses every read-path refusal to one message on
    purpose: a browser must not be able to tell a sealed folder from a missing
    file, or it becomes an oracle for enumerating the campaign's spoilers. That
    is right for `/atlas/<slug>` and wrong here, where the caller is the GM's own
    shell and "your ft_per_px is not a number" is the single most useful thing
    the program can say.

    So the spec is read and validated directly first, and only a failure of that
    -- a file that is not there, not JSON, or not a spec at all -- is allowed to
    collapse to "no such overview map". A scale that does not validate is
    reported as itself.

    The returned `raw` is the unvalidated dict, so `calibrate --save` can write
    back without re-serialising a validated spec and losing the fields
    `validate` does not model.
    """
    path = _om.spec_path(camp, slug)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _fail("no such overview map")
    try:
        return _om.validate(raw), raw
    except _om.OverviewMapError as exc:
        message = str(exc)
        if "ft_per_px" in message:
            raise _fail(f"{slug}: {message}")
        raise _fail("no such overview map")


def _resolve(pins: dict, pin_id: str):
    """One pin by id, or None.

    Ids are unique per spec — `overview_map.validate` refuses a duplicate — so
    there is no ambiguous case to disambiguate here, and pretending otherwise
    would be inventing a failure the spec cannot produce. An unknown id is a
    refusal, not a default to the first pin.
    """
    pin = pins.get(pin_id)
    if pin is None:
        known = ", ".join(sorted(pins)) or "(none)"
        raise _fail(f"no pin {pin_id!r}. Pins: {known}")
    return pin


def _point(pin: dict) -> list:
    return [pin[_X], pin[_Y]]


def _explicit_scale(raw) -> float:
    """Validate a scale the GM typed on the command line.

    A shell argument is a string, so `_number` alone would refuse every value a
    GM actually types -- `overview.py ... --ft-per-px 0.5` is the normal case and
    `_number("0.5")` is a refusal. So the string is converted first and the
    engine's rule is then applied to the converted value, which is what keeps
    this honest: `float("nan")` and `float("inf")` convert fine and are refused
    by `_number`, and `True` is caught by the `type(value) is bool` guard before
    any float arithmetic happens to it.
    """
    if isinstance(raw, bool):
        raise _fail("--ft-per-px must be a number")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise _fail("--ft-per-px must be a number")
    try:
        scale = _om._number(value, "--ft-per-px")
    except _om.OverviewMapError as exc:
        raise _fail(str(exc))
    if scale <= 0:
        raise _fail("--ft-per-px must be positive")
    return scale


def _scale_from(spec: dict, args) -> float:
    """Feet per pixel: the GM's number, or a refusal naming what is missing.

    Two sources, in order. `--ft-per-px` is explicit and wins. Otherwise a
    `ft_per_px` in the spec, which `calibrate --save` writes.

    Neither present is a refusal, and the message says which half is missing,
    because "I need a scale" alone leaves the GM hunting. Pin ids, not labels:
    this CLI's contract is that a pin id is the handle.
    """
    if args.ft_per_px is not None:
        return _explicit_scale(args.ft_per_px)
    recorded = spec.get("ft_per_px")
    if recorded is not None:
        try:
            return _om._number(recorded, "ft_per_px")
        except _om.OverviewMapError as exc:
            raise _fail(f"the spec's ft_per_px is unusable: {exc}")
    known = ", ".join(sorted(_find_pins(spec))) or "(none)"
    raise _fail_soft(
        "no scale for this overview map, so there is no feet answer to give. "
        "Measure two pins whose real distance you know:\n"
        f"       overview.py -c <campaign> calibrate --from <pin> --to <pin> --feet <n> --save\n"
        f"  Pins here: {known}")


def cmd_list(args, camp: pathlib.Path) -> int:
    """Every pin on one overview map, with the id you would pass to `between`."""
    spec, _ = _load(camp, args.map)
    rows = _find_pins(spec)
    if not rows:
        print(f"overview: {args.map} has no pins")
        return 0
    if not args.all:
        rows = {k: v for k, v in rows.items() if v.get("revealed") is True}
        if not rows:
            print(f"overview: {args.map} has no revealed pins (--all for gm-only)")
            return 0
    print(f"overview: {args.map} ({spec['name']}), {spec['extent'][0]:g}x"
          f"{spec['extent'][1]:g}px, {len(rows)} pin(s)")
    if spec.get("ft_per_px") is not None:
        print(f"  scale: {spec['ft_per_px']:g} ft per pixel")
    for pin_id in sorted(rows):
        pin = rows[pin_id]
        mark = "shown" if pin.get("revealed") is True else "gm-only"
        print(f"  {pin_id:>16}  {pin[_X]:g},{pin[_Y]:g}  {pin['label']!r}  ({mark})")
    return 0


def cmd_between(args, camp: pathlib.Path) -> int:
    """Feet and bearing between two pins, both from the engine."""
    spec, _ = _load(camp, args.map)
    pins = _find_pins(spec)
    start = _resolve(pins, args.frm)
    end = _resolve(pins, args.to)
    extent = (spec["extent"][0], spec["extent"][1])

    try:
        point_to = _om.bearing(_point(start), _point(end), extent,
                                north_deg=args.north)
    except _om.OverviewMapError as exc:
        raise _fail(str(exc))

    # Feet are computed only when a scale exists; a refusal here must not stop
    # the bearing being printed, since a bearing needs no scale and is the half
    # of this that is always answerable.
    feet = None
    scale_error = None
    try:
        scale = _scale_from(spec, args)
    except MissingScale as exc:
        # Only "there is no scale" is soft. A scale the GM supplied and got
        # wrong is a refusal, not a gap, and swallowing it into a soft
        # "not answerable" would hide a typo in the one number they typed.
        scale_error = exc
    else:
        try:
            feet = _om.fraction_to_feet(_point(start), _point(end), extent, scale)
        except _om.OverviewMapError as exc:
            raise _fail(str(exc))

    if args.json:
        out = {"map": args.map, "from": start["id"], "to": end["id"],
               "from_label": start["label"], "to_label": end["label"],
               "bearing": point_to, "feet": feet}
        if feet is None:
            out["feet_error"] = f"no scale; {args.frm} -> {args.to} has no feet answer"
        print(json.dumps(out, indent=2))
        return 0

    print(f"overview: {start['label']} -> {end['label']}")
    print(f"  bearing: {point_to}")
    if feet is None:
        # The whole refusal, indented, not its first line. The first line is
        # "no scale for this overview map" and the second is how to get one;
        # a GM who sees only the first knows they are stuck and not why.
        print(f"  feet:    not answerable:")
        for line in scale_error.message.splitlines():
            print(f"            {line}")
    else:
        print(f"  feet:    {feet:.0f} ft ({feet / 5:.1f} five-foot squares)")
    return 0


def cmd_calibrate(args, camp: pathlib.Path) -> int:
    """Feet per pixel from two pins and a real distance.

    `--save` writes it into the spec, and refuses rather than guessing if the
    spec is not where it is expected. Without `--save` the number is printed and
    nothing on disk moves, which is the right default for a measurement: it
    takes two tries to get a distance right and a wrong one silently in the
    spec is a wrong one nobody notices.
    """
    spec_path = _om.spec_path(camp, args.map)
    spec, raw = _load(camp, args.map)
    pins = _find_pins(spec)
    start = _resolve(pins, args.frm)
    end = _resolve(pins, args.to)
    extent = (spec["extent"][0], spec["extent"][1])
    try:
        ft_per_px = _om.calibrate(_point(start), _point(end), args.feet, extent)
    except _om.OverviewMapError as exc:
        raise _fail(str(exc))

    print(f"overview: {start['label']} to {end['label']} is {args.feet:g} ft "
          f"= {ft_per_px:.6g} ft per pixel")
    if not args.save:
        print("        pass --save to record it in the overview spec")
        return 0

    # The file as it is, not the validated spec: `validate` returns only the
    # keys it models, so writing that back would silently delete everything
    # else in the GM's overview file. The merged spec is validated before the
    # write, which is what proves the number did not make the file unloadable --
    # the order matters, because the write is the irreversible half.
    raw["ft_per_px"] = ft_per_px
    try:
        _om.validate(raw)
    except _om.OverviewMapError as exc:
        raise _fail(f"refusing to write a scale the spec cannot carry: {exc}")
    spec_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    print(f"        recorded in {spec_path.name}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="overview.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--campaign", required=True, help="campaign name")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_map(p, pair=False):
        p.add_argument("--map", required=True, dest="map", help="overview map slug")
        if pair:
            p.add_argument("--from", required=True, dest="frm",
                           help="a pin id, as `list` prints them")
            p.add_argument("--to", required=True, dest="to", help="another pin id")

    p = sub.add_parser("list", help="list the pins on an overview map")
    with_map(p)
    p.add_argument("--all", action="store_true", help="include gm-only pins")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("between", help="feet and bearing from one pin to another")
    with_map(p, pair=True)
    p.add_argument("--ft-per-px", dest="ft_per_px", default=None,
                   help="scale, if the spec has not recorded one. Deliberately "
                        "not type=float: argparse would coerce \"0.25\" and a "
                        "bool into numbers before anything could refuse them, "
                        "and the engine's _number is the thing that decides "
                        "what a usable number is")
    p.add_argument("--north", type=float, default=0.0,
                   help="true bearing of image-up, clockwise from north "
                        "(default 0, so image-up is north)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(fn=cmd_between)

    p = sub.add_parser("calibrate", help="derive ft per pixel from a known distance")
    with_map(p, pair=True)
    p.add_argument("--feet", type=float, required=True,
                   help="the real distance between those two pins, in feet")
    p.add_argument("--save", action="store_true",
                   help="record the scale in the overview spec")
    p.set_defaults(fn=cmd_calibrate)

    args = ap.parse_args(argv)
    try:
        camp = _require_campaign(args.campaign, migrate=False)
    except Exception as exc:
        raise _fail(str(exc))
    try:
        return args.fn(args, camp)
    except _om.OverviewMapError as exc:
        # `overview_map` collapses its own refusals to one message on the read
        # path (a missing file and a sealed folder are deliberately
        # indistinguishable), and that behaviour is right for a browser. Here it
        # would surface as an unhandled traceback instead of a line a GM reads,
        # so it is turned back into a refusal here. The message is the module's
        # own, not a rewritten one: this is not a read path with an oracle to
        # protect, it is the GM's own shell.
        raise _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
