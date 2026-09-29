#!/usr/bin/env python3
"""art_triage.py: decide what to do with a folder of 150 creator JPGs.

    python3 scripts/art_triage.py ~/Downloads/big-folder
    python3 scripts/art_triage.py ~/Downloads/big-folder --suggest
    python3 scripts/art_triage.py ~/Downloads/big-folder --import-ok

WHY THIS EXISTS
---------------
`art_import.py` imports a map. A creator collection is not one map: it is a
folder where every scene arrives as two or four files, several share a base name
with only a floor number apart, some have art the filename does not describe,
and a few are not maps at all (a title card, a token sheet, a shop ad).

Reading that as one flat list of "N files" is how a hundred-map import turns
into an afternoon of squinting at filenames. So this groups first and decides
second, and prints the decision before anything is written.

THE GROUPS
----------
  import   a clean map, grid verified against the real pixels
  variant  a second file of a map already seen (the grid/gridless twin, or a
           night/fog version) -- kept out unless asked for
  refuse   the name does not carry a trustworthy grid, or the file is not an
           image. Never guessed at; listed with the reason.
  unknown  a file that is not a .jpg at all

Refusals are the point of the exercise. A grid that is a square out plays wrong
at the table while looking correct on screen, so "I could not confirm this" is a
useful answer and "here is my guess" is not.

DUPLICATES
----------
Identical artwork is detected by content hash, not by filename. Collections
routinely re-upload the same map under a new name, and near-identical pairs are
how a folder quietly doubles.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import art_import

# Words that mean "this is not a battle map". Matched against the filename, so
# `Token Sheet` and `tokens.png` both land here.
NOT_A_MAP = re.compile(r"\b(tokens?|icons?|sheets?|cover|logo|banners?|ad|ads|promo|"
                       r"patreon|preview|title)\b", re.IGNORECASE)


def content_hash(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def variant_rank(name: str) -> tuple[int, str]:
    """Lower sorts first, so gridless wins over gridded without a flag."""
    low = name.lower()
    if "gridless" in low:
        return (0, low)
    if "grid" in low:
        return (2, low)
    return (1, low)


def triage(folder: pathlib.Path) -> dict:
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith("."))
    buckets: dict[str, list] = collections.defaultdict(list)
    by_slug: dict[str, list] = collections.defaultdict(list)

    # Parse and validate first, and only deduplicate among files that were going
    # to be imported as maps. Deduplicating on the raw file list first is wrong:
    # a gridless file and its gridded twin differ in name, and a sheet and a map
    # are not interchangeable, so a group can only be collapsed when every member
    # is the same map by name *and* byte-identical.
    parsed: list[dict] = []
    for p in files:
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            buckets["unknown"].append({"path": p, "note": f"{p.suffix or 'no'} file"})
            continue
        if NOT_A_MAP.search(p.stem):
            buckets["notamap"].append({"path": p, "note": "looks like a sheet or promo"})
            continue
        try:
            meta = art_import.parse_name(p)
        except art_import.ImportError_ as exc:
            buckets["refuse"].append({"path": p, "note": str(exc).split(": ", 1)[-1]})
            continue
        px = art_import.image_size(p)
        if px != meta["px"]:
            buckets["refuse"].append(
                {"path": p, "note": f"name says {meta['px'][0]}x{meta['px'][1]}px, "
                                     f"file is {px[0]}x{px[1]}px"})
            continue
        if meta["cells"][0] * meta["cells"][1] > art_import.MAX_CELLS:
            buckets["refuse"].append(
                {"path": p, "note": f"{meta['cells'][0]}x{meta['cells'][1]} is over the "
                                     f"{art_import.MAX_CELLS}-square limit"})
            continue
        parsed.append({**meta, "path": p, "slug": meta["slug"],
                       "_hash": content_hash(p)})

    # Collapse re-uploads of the same artwork. Keyed on content alone, not
    # (slug, content): a creator re-uploading "Cellar Crypt (v2)" is one map
    # under two names, and importing both would have the second overwrite the
    # first with no trace. A gridless file and its gridded twin are NOT
    # byte-identical, so they survive this and are separated by variant below.
    #
    # When several identical files compete, the shortest name wins: "(v2)",
    # "final", "copy" and friends are re-upload noise, and letting one of them
    # decide the slug would leave maps called `cellar-crypt-v2`.
    groups: dict[str, list] = collections.defaultdict(list)
    for entry in parsed:
        groups[entry["_hash"]].append(entry)
    for entries in groups.values():
        entries.sort(key=lambda e: (len(e["path"].name), str(e["path"])))
        keep, rest = entries[0], entries[1:]
        for extra in rest:
            buckets["duplicate"].append(
                {"path": extra["path"], "note": f"identical to {keep['path'].name}"})
        if keep["variant"] == "grid":
            by_slug[keep["slug"]].append(keep)
        else:
            buckets["import"].append(keep)

    # A gridded file whose gridless twin is present is a variant, not a map.
    for slug, entries in by_slug.items():
        for e in sorted(entries, key=lambda x: variant_rank(x["path"].name)):
            buckets["variant"].append({**e, "note": f"gridded variant of {slug}"})

    for items in buckets.values():
        items.sort(key=lambda e: str(e.get("path", "")))
    buckets["_files"] = files
    return buckets


def report(buckets: dict) -> None:
    total = len(buckets["_files"])
    print(f"{total} file(s) under the folder\n")
    for key, title in (("import", "IMPORT  (clean map, grid verified)"),
                       ("variant", "VARIANT (gridded twin, keep only if you want the grid)"),
                       ("refuse", "REFUSE  (name does not carry a trustworthy grid)"),
                       ("notamap", "NOT A MAP (sheet, promo, cover)"),
                       ("duplicate", "DUPLICATE (byte-identical)"),
                       ("unknown", "UNKNOWN (not an image)")):
        items = buckets.get(key, [])
        print(f"{title}: {len(items)}")
        for e in items:
            w, h = e.get("cells", (0, 0))
            extra = f"  {w}x{h} squares" if w else ""
            print(f"    {e['path'].name}{extra}")
            if e.get("note"):
                print(f"        - {e['note']}")
        print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=pathlib.Path)
    ap.add_argument("--import-ok", action="store_true",
                    help="after reporting, import the clean maps")
    ap.add_argument("--credit", default=None, help="creator (default: the folder name)")
    ap.add_argument("--dry-run", action="store_true", help="pass --dry-run to the importer")
    args = ap.parse_args(argv)

    if not args.folder.is_dir():
        print(f"no such folder: {args.folder}", file=sys.stderr)
        return 2

    buckets = triage(args.folder)
    report(buckets)

    if not args.import_ok:
        print("Nothing written. Re-run with --import-ok to import the clean maps.")
        return 0

    n = len(buckets.get("import", []))
    if not n:
        print("no clean maps to import", file=sys.stderr)
        return 1
    extra = ["--dry-run"] if args.dry_run else []
    cmd = [str(args.folder), "--credit", args.credit or args.folder.name] + extra
    print(f"Importing {n} map(s)...\n")
    return art_import.main(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
