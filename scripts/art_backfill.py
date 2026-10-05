#!/usr/bin/env python3
"""Record `image_px` on map files that reference artwork but never recorded its size.

    scripts/art_backfill.py [--check] [maps_dir]

WHY THIS IS A SCRIPT AND NOT A GM PASS
=======================================
`meta.image_px` is the picture's own pixel dimensions -- the same two integers
`art_import.py` writes when it first imports art, read from the JPEG or PNG
header by `image_size()` in that module. It is a measurement, not a judgement,
so it does not need a human looking at a map.

It is nevertheless missing on every shipped map, because `dnd-gm#142` merged as
engine #225 (`ce98bb7`) and 0 of the 17 maps carrying an `image` recorded an
`image_px`. `compile_map` emits both keys, and `display/gm-display-app.py:1955`
gates the aligned-drawing branch on `spec.get("image") and spec.get("image_px")`,
so `artAttrs()` never reaches that branch for any map that exists today. From a
player's point of view #142 is indistinguishable from #142 not having happened.

The art itself is gitignored (`.gitignore:34`, `display/maps/images/` --
third-party, 5-13MB each, installed by `scripts/art_import.py`), so this has to
run on a machine that has the artwork. That is the whole reason it is a script
and not a merge: the measurement is mechanical but the input is local.

`--check` reports what would change and exits non-zero if anything is missing, so
CI can assert the backfill has been run without needing the artwork.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from art_import import image_size  # noqa: E402

DEFAULT_MAPS = pathlib.Path(__file__).resolve().parent.parent / "display" / "maps"


def needs_size(spec: dict) -> bool:
    """True when this map points at artwork but never recorded its size."""
    return bool(spec.get("image")) and not spec.get("image_px")


def image_path(maps_dir: pathlib.Path, spec: dict) -> pathlib.Path:
    """Where the art actually is. `image` is written relative to `maps/`."""
    rel = spec.get("image") or ""
    # `images/foo.jpg` is relative to maps/; some importers write `display/maps/...`
    # relative to the repo root. Try both rather than guessing one.
    first = maps_dir / rel
    if first.exists():
        return first
    root = maps_dir.parent.parent / rel
    return root


def survey(maps_dir: pathlib.Path) -> tuple[list[tuple[pathlib.Path, dict, pathlib.Path | None]], list[str]]:
    """Every map needing a size, and every reason we cannot record one."""
    found: list[tuple[pathlib.Path, dict, pathlib.Path | None]] = []
    problems: list[str] = []
    for path in sorted(maps_dir.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: unreadable ({exc})")
            continue
        if not needs_size(spec):
            continue
        art = image_path(maps_dir, spec)
        found.append((path, spec, art if art.exists() else None))
    return found, problems


def main() -> int:
    ap = argparse.ArgumentParser(prog="art_backfill.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("maps_dir", nargs="?", default=str(DEFAULT_MAPS),
                    help="directory of map JSON files (default: display/maps)")
    ap.add_argument("--check", action="store_true",
                    help="report only; exit 1 if any map is missing image_px. "
                         "Works without the artwork installed, so CI can use it")
    args = ap.parse_args()

    maps_dir = pathlib.Path(args.maps_dir).resolve()
    if not maps_dir.is_dir():
        print(f"art-backfill: {maps_dir} is not a directory", file=sys.stderr)
        return 2

    found, problems = survey(maps_dir)
    for line in problems:
        print(f"art-backfill: {line}", file=sys.stderr)

    if not found:
        print(f"art-backfill: nothing to do; every map in {maps_dir} that names "
              "artwork already records image_px")
        return 1 if problems else 0

    if args.check:
        print(f"art-backfill: {len(found)} map(s) name artwork but record no image_px:")
        for path, _spec, art in found:
            where = str(art) if art else "ARTWORK NOT INSTALLED"
            print(f"  {path.name:<44} {where}")
        print("\nRun scripts/art_backfill.py (without --check) on a machine that has "
              "the artwork. It is a header measurement, not a judgement.")
        return 1

    written = skipped = 0
    for path, spec, art in found:
        if art is None:
            skipped += 1
            continue
        try:
            width, height = image_size(art)
        except Exception as exc:                      # noqa: BLE001 - report, do not crash the pass
            print(f"art-backfill: {path.name}: cannot read {art.name} ({exc})",
                  file=sys.stderr)
            skipped += 1
            continue
        spec["image_px"] = [width, height]
        # Write back with the same shape the importer uses, so a diff between an
        # imported map and a backfilled one is only the key we added.
        path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"art-backfill: {path.name}: image_px = [{width}, {height}]")
        written += 1

    print(f"art-backfill: recorded {written}, skipped {skipped}")
    if skipped:
        print("art-backfill: skipped maps need the artwork installed; re-run once "
              "display/maps/images/ is populated", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())