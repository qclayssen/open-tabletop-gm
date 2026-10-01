#!/usr/bin/env python3
"""
pin.py — author and inspect note pins on a battle map.

A pin is a marker on a map that opens a campaign note, or another map. This is
the CLI that creates them, and it is a CLI rather than an HTTP route on purpose:
the display's write gate (`_token_ok`) is true for every browser on the LAN,
because the token is handed to every page, so a route would let any player at
the table plant a pin that the GM then clicks in good faith. A pin is campaign
state authored by the GM, and the GM has a shell. The same argument is why the
spec's "never written by the LLM directly" is satisfied here rather than
there: a skill invoking this is a narrower path than a skill reaching a
localhost server.

Pins live in <campaign>/pins/<map-slug>.json and are read by the display.

Usage:
    python3 scripts/pin.py -c <campaign> list --map <slug> [--all]
    python3 scripts/pin.py -c <campaign> add --map <slug> --note notes/harbour.md -x 4 -y 7 [-l "Fog"] [--revealed]
    python3 scripts/pin.py -c <campaign> add --map <slug> --map-slug the-rotunda -x 4 -y 7
    python3 scripts/pin.py -c <campaign> show --map <slug> --id <pin-id>
    python3 scripts/pin.py -c <campaign> remove --map <slug> --id <pin-id>
    python3 scripts/pin.py -c <campaign> reveal --map <slug> --id <pin-id> | --no-reveal

Notes:
    A note target must be .md under notes/, locations/ or handouts/. That is an
    allow-list, not a blocklist: a spoiler file added next month is refused by
    default rather than leaked until somebody remembers to add its name.

    --revealed puts the pin on the players' board. Off by default, because the
    display has no way to tell a GM from a player and a pin that is not
    revealed is the only safe default that exists here.

    A pin is off the players' board with --all, which also shows unrevealed
    ones, so a GM can see what they have made.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pins as _pins
from paths import require_campaign as _require_campaign

# The display's map list, read from the same module the display reads it from,
# so a map slug this CLI accepts is a map the display can actually load.
try:
    from tactics import maps as _maps
except ImportError:                                  # running outside scripts/
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from tactics import maps as _maps


def _fail(message: str) -> "SystemExit":
    """Print a refusal and return the exit to raise.

    Printed here and raised by the caller rather than `raise SystemExit(msg)`,
    which looks equivalent and is not: CPython writes a SystemExit string to the
    real stderr, past any `contextlib.redirect_stderr`, so a refusal cannot be
    asserted on without a capsys fixture. A GM sees the same output either way.
    """
    print(f"pin: {message}")
    return SystemExit(1)


def _known_maps() -> set:
    try:
        return set(_maps.available())
    except Exception:
        return set()


def _place(args) -> tuple:
    """The cell, from either --x/--y or a --square label like D5.

    Both, because a GM reads squares off the map by label and a script wants
    numbers. Parsing the label here keeps `tactics.js`'s label format from
    leaking into the CLI's arguments.
    """
    if args.square:
        from tactics.grid import parse_square
        try:
            return parse_square(args.square)
        except Exception as exc:
            raise _fail(f"{args.square!r} is not a square label ({exc})")
    if args.x is None or args.y is None:
        raise _fail("give --x and --y, or --square D5")
    return float(args.x), float(args.y)


def _emit(pin: dict) -> None:
    mark = "shown" if pin["revealed"] else "gm-only"
    print(f"  {pin['id']}  {pin['x']:g},{pin['y']:g}  [{pin['kind']}] "
          f"{pin['label']!r} -> {pin['target']} ({mark})")


def cmd_list(args, camp: pathlib.Path) -> int:
    known = _pins.load(camp, args.map)
    rows = known if args.all else _pins.revealed(known)
    if not rows:
        scope = "no pins" if not known else (
            "no revealed pins (--all to include gm-only)" if not args.all else "")
        print(f"pin: {scope} on {args.map}")
        return 0
    print(f"pin: {len(rows)} on {args.map}")
    for pin in rows:
        _emit(pin)
    return 0


def cmd_add(args, camp: pathlib.Path) -> int:
    known_maps = _known_maps() or None
    if args.note:
        target = args.note
        kind = "note"
    elif args.map_slug:
        target = args.map_slug
        kind = "map"
    else:
        raise _fail("add needs --note or --map-slug")

    x, y = _place(args)
    try:
        record = _pins.validate({
            "x": x, "y": y, "label": args.label or "",
            "kind": kind, "target": target,
            "revealed": bool(args.revealed),
        }, known_maps)
    except _pins.PinError as exc:
        raise _fail(f"refused: {exc}")

    existing = _pins.load(camp, args.map)
    existing.append(record)
    try:
        written = _pins.save(camp, args.map, existing, known_maps)
    except _pins.PinError as exc:
        raise _fail(f"refused: {exc}")
    print(f"pin: added to {args.map}")
    _emit(next(p for p in written if p["id"] == record["id"]))
    if not args.revealed:
        print("      gm-only: add --revealed to put it on the players' board")
    return 0


def _find(rows: list, pin_id: str):
    for row in rows:
        if row["id"] == pin_id:
            return row
    return None


def cmd_show(args, camp: pathlib.Path) -> int:
    rows = _pins.load(camp, args.map)
    row = _find(rows, args.id)
    if row is None:
        raise _fail(f"no pin {args.id!r} on {args.map}")
    _emit(row)
    if row["kind"] == "note" and not args.raw:
        print()
        try:
            print(_pins.note_body(camp, row))
        except _pins.PinError as exc:
            # Same message as a missing note, on purpose: a refusal that
            # distinguished "sealed" from "absent" would be an oracle.
            raise _fail("no such note")
    return 0


def cmd_remove(args, camp: pathlib.Path) -> int:
    rows = _pins.load(camp, args.map)
    row = _find(rows, args.id)
    if row is None:
        raise _fail(f"no pin {args.id!r} on {args.map}")
    _pins.save(camp, args.map, [p for p in rows if p["id"] != args.id], _known_maps() or None)
    print(f"pin: removed {args.id} from {args.map}")
    return 0


def cmd_reveal(args, camp: pathlib.Path) -> int:
    rows = _pins.load(camp, args.map)
    row = _find(rows, args.id)
    if row is None:
        raise _fail(f"no pin {args.id!r} on {args.map}")
    row["revealed"] = bool(args.reveal)
    _pins.save(camp, args.map, rows, _known_maps() or None)
    _emit(_find(_pins.load(camp, args.map), args.id))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="pin.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--campaign", required=True, help="campaign name")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_map(p, need_id=False):
        p.add_argument("--map", required=True, dest="map",
                       help="map slug, as listed by the display")
        if need_id:
            p.add_argument("--id", required=True, dest="id")

    p = sub.add_parser("list", help="list pins on a map")
    with_map(p)
    p.add_argument("--all", action="store_true",
                   help="include gm-only pins (off by default)")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("add", help="add a pin")
    with_map(p)
    p.add_argument("--note", help="campaign note, e.g. notes/harbour.md")
    p.add_argument("--map-slug", dest="map_slug",
                   help="another map, by slug -- not a path")
    p.add_argument("-x", type=float, help="cell column (may be fractional)")
    p.add_argument("-y", type=float, help="cell row (may be fractional)")
    p.add_argument("--square", help="cell label instead of -x/-y, e.g. D5")
    p.add_argument("-l", "--label", default="", help="short label on the map")
    p.add_argument("--revealed", action="store_true",
                   help="show it on the players' board (default: gm-only)")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("show", help="show one pin, and its note")
    with_map(p, need_id=True)
    p.add_argument("--raw", action="store_true",
                   help="metadata only; do not print the note body")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("remove", help="remove a pin")
    with_map(p, need_id=True)
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("reveal", help="put a pin on, or take it off, the board")
    with_map(p, need_id=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--reveal", dest="reveal", action="store_true")
    g.add_argument("--no-reveal", dest="reveal", action="store_false")
    p.set_defaults(fn=cmd_reveal)

    args = ap.parse_args(argv)
    try:
        camp = _require_campaign(args.campaign, migrate=False)
    except Exception as exc:
        raise _fail(str(exc))
    return args.fn(args, camp)


if __name__ == "__main__":
    raise SystemExit(main())
