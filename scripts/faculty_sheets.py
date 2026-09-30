#!/usr/bin/env python3
"""faculty_sheets.py: cut a college faculty sheet into Atlas token portraits.

Why this is a measured table and not a detector
-----------------------------------------------
Strixhaven's five college sheets are one 2295x5940 JPG each, five painted
figures interleaved with five lore panels. The obvious implementation is to
detect the figures, and four were tried here. Each found the right number of
regions and the wrong ones: the panels bridge the figures above and below them
into a single band, so a band centred on a bridge crops the gap between two
people, and the first honest output was 27 crops of which most were a page of
lore. Tightening the crop fixed the labels and lost the faces.

A wrong crop is worse than no crop. It is a token that looks finished, passes
every check, and puts the wrong professor at the table mid-fight -- the exact
failure KC4 exists to refuse. So the boxes are measured, one per figure, and
recorded here with the sheet they came from. A college with no entry is
refused rather than guessed at.

To add a college: open the sheet, note each figure's art extent in sheet
pixels (the name label and the lore panels are NOT part of it), and add an
entry. `--check` renders a contact sheet so the boxes can be eyeballed.

Usage
-----
    python3 scripts/faculty_sheets.py --sheets ~/Downloads --out DIR
    python3 scripts/faculty_sheets.py --sheets ~/Downloads --out DIR --check

Deliberately not done: cutting the figures out of their background. Flood fill
from the page edge reaches 30% transparency, then eats a shoulder, then puts
holes in a robe, because these are painted against a soft glow rather than on a
flat field. Atlas masks a token to a circle, so a clean square costs nothing.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from utf8io import write_text

TOKEN_PX = 256
COLLEGES = ("quandrix", "witherbloom", "silverquill", "prismari", "lorhold")

# Measured by eye against each sheet, in sheet pixels (x0, y0, x1, y1), the
# figure's art only: no name label beneath, no lore panel beside.
# Verified by rendering the output and looking at it, not by assuming.
SHEET_SIZE = (2295, 5940)
BOXES: dict[str, list[tuple[str, tuple[int, int, int, int]]]] = {
    "quandrix": [
        ("kainne", (0, 200, 1160, 1010)),
        ("ibrahim", (1060, 1075, 2294, 1830)),
        ("adrix-nev", (40, 1995, 1145, 2760)),
        ("deekah", (1080, 2815, 2265, 3595)),
        ("ruxa", (0, 3735, 1185, 4500)),
    ],
    # Unmeasured. Deliberately empty: the script refuses these rather than
    # falling back to a detector, because a wrong portrait is the one output
    # here that cannot be spotted by a later automated check.
    "witherbloom": [],
    "silverquill": [],
    "prismari": [],
    "lorhold": [],
}


class Refused(Exception):
    """A condition that stops the run rather than being worked around."""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def college_of(sheet: pathlib.Path) -> str:
    return re.sub(r"teachers?$", "", sheet.stem, flags=re.IGNORECASE).lower() or sheet.stem.lower()


def square_box(box: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The largest square inside `box`, centred, clipped to the sheet.

    Never upscales: a figure smaller than the sheet keeps its own pixels, so a
    token is not a blurry enlargement of a crop.
    """
    x0, y0, x1, y1 = box
    W, H = size
    side = min(x1 - x0, y1 - y0, W, H)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    left = min(max(0, cx - side // 2), W - side)
    top = min(max(0, cy - side // 2), H - side)
    return (left, top, left + side, top + side)


def run(sheet: pathlib.Path, out: pathlib.Path) -> dict:
    from PIL import Image
    try:
        im = Image.open(sheet).convert("RGB")
    except FileNotFoundError:
        raise Refused(f"{sheet} does not exist") from None
    if im.size != SHEET_SIZE:
        raise Refused(
            f"{sheet.name} is {im.size[0]}x{im.size[1]}, but the measured boxes "
            f"are for {SHEET_SIZE[0]}x{SHEET_SIZE[1]}. Re-measure them; a scaled "
            f"box would land on the wrong pixels and nothing would say so."
        )
    college = college_of(sheet)
    boxes = BOXES.get(college)
    if boxes is None:
        raise Refused(f"{college} is not a known college sheet")
    if not boxes:
        raise Refused(
            f"{college}: no measured boxes yet. The detector that used to guess "
            f"them produced lore panels as portraits, so this refuses instead. "
            f"Add an entry to BOXES in scripts/faculty_sheets.py."
        )
    out.mkdir(parents=True, exist_ok=True)
    written, manifest = [], {"source": sheet.name, "college": college,
                             "token_px": TOKEN_PX, "figures": {}}
    for name, box in boxes:
        sq = square_box(box, im.size)
        fname = f"{college}-{slug(name)}.png"
        im.crop(sq).resize((TOKEN_PX, TOKEN_PX), Image.LANCZOS).save(out / fname)
        written.append(fname)
        manifest["figures"][fname] = {"name": name, "measured": list(box),
                                      "cropped": list(sq)}
    write_text(out / f"{college}-manifest.json",
               json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return {"college": college, "written": written, "out": str(out)}


def contact_sheet(colleges: list[str], out: pathlib.Path) -> None:
    """One image showing every crop, so the boxes can actually be checked."""
    from PIL import Image
    tiles = [p for c in colleges for p in sorted(out.glob(f"{c}-*.png"))
             if p.name != "_contact-sheet.png"]
    if not tiles:
        raise Refused(f"nothing to check in {out}")
    cols = min(len(tiles), 6)
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * TOKEN_PX, rows * TOKEN_PX), (25, 25, 25))
    for i, p in enumerate(tiles):
        with Image.open(p) as im:
            sheet.paste(im.resize((TOKEN_PX, TOKEN_PX)),
                        ((i % cols) * TOKEN_PX, (i // cols) * TOKEN_PX))
    dest = out / "_contact-sheet.png"
    sheet.save(dest)
    print(f"wrote {dest} ({len(tiles)} crops)")


def main(argv: list) -> int:
    p = argparse.ArgumentParser(
        description="cut a Strixhaven college faculty sheet into Atlas tokens")
    p.add_argument("--sheets", type=pathlib.Path, required=True,
                   help="folder holding <college>teachers.jpg")
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--check", action="store_true",
                   help="also write a contact sheet of every crop, to look at. "
                        "The portraits are written either way -- checking them "
                        "is the point, and --check used to skip them, which "
                        "meant a check could silently write nothing.")
    args = p.parse_args(argv)

    done, skipped = [], []
    for c in COLLEGES:
        sheet = args.sheets / f"{c}teachers.jpg"
        if not sheet.is_file():
            skipped.append(f"{c} (no sheet)")
            continue
        try:
            r = run(sheet, args.out)
        except Refused as e:
            skipped.append(f"{c} ({str(e).split('.')[0]})")
            continue
        done.append(r)
        print(f"{r['college']}: wrote {len(r['written'])} portrait(s)")
        for f in r["written"]:
            print(f"  {f}")
    for s in skipped:
        print(f"skipped {s}")
    if args.check and done:
        contact_sheet([r["college"] for r in done], args.out)
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
