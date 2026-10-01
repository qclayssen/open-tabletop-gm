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
# A lore panel is near-white and near-grey; a painting is saturated. Used to
# trim a measured box away from the paper beside the figure.
PANEL_SAT_MAX = 45
PAPER_LUM_MIN = 190
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

# Measured, NOT verified. A first pass over the other four colleges found 24
# boxes and roughly six of them were wrong: two portraits had a lore panel
# inside them, one caught the wrong figure, and several carried the name label
# across the chest. They are kept here so the work is not lost, and are NOT in
# BOXES, so nothing ships from them.
#
# The obvious fix -- mask the panel by colour, it is bright and low-chroma --
# does not work on these pages. Measured on the Silverquill sheet, a lore panel
# and the figure beside it are sat 13 vs 14 and lum 161 vs 167: the same
# colour. These pages are washed-out pink, and the paintings are as pale as the
# paper. There is no colour signature to separate them, which is why this has to
# be eyeballed per figure.
UNVERIFIED_BOXES: dict[str, list[tuple[str, tuple[int, int, int, int]]]] = {
    "witherbloom": [
        ("lisette", (90, 90, 1170, 990)),
        ("valentin", (1110, 1110, 2295, 1950)),
        ("willowdusk", (60, 1980, 1110, 2790)),
        ("verelda", (1140, 2850, 2280, 3690)),
        ("tivash", (90, 3720, 1140, 4560)),
        ("yedora", (1140, 4620, 2280, 5490)),
    ],
    "silverquill": [
        ("ambrose", (90, 120, 1170, 900)),
        ("shaile", (1110, 1170, 2295, 1860)),
        ("breena", (0, 2000, 1290, 2750)),
        ("nils", (1080, 2310, 2280, 3630)),
        ("fain", (0, 3960, 990, 4710)),
        ("mavinda", (1080, 4500, 2220, 5640)),
    ],
    "prismari": [
        ("uvilda", (60, 90, 1170, 1020)),
        ("nassari", (1140, 1140, 2280, 1800)),
        ("veyran", (30, 2040, 1260, 2725)),
        ("zaffai", (1200, 2850, 2250, 3660)),
        ("arkin", (60, 3900, 1200, 4560)),
        ("nivall", (840, 4530, 2280, 5700)),
    ],
    "lorhold": [
        ("augusta", (60, 120, 1170, 1020)),
        ("plargg", (1080, 1110, 2280, 1800)),
        ("hofri", (60, 2040, 1200, 2730)),
        ("osgir", (1110, 2870, 2280, 3540)),
        ("losheel", (30, 3960, 1170, 4470)),
        ("alibou", (1140, 4590, 2280, 5715)),
    ],
}


class Refused(Exception):
    """A condition that stops the run rather than being worked around."""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def college_of(sheet: pathlib.Path) -> str:
    return re.sub(r"teachers?$", "", sheet.stem, flags=re.IGNORECASE).lower() or sheet.stem.lower()


def trim_panels(im, box):
    """Shrink `box` away from any lore panel it overlaps.

    Hand-measured boxes are generous by nature, and a generous box on these
    sheets reaches the paper panel beside the figure -- the first pass put a
    whole page of prose inside two portraits. The panel is bright *and*
    low-chroma, which no figure is: a painting is saturated, a page of text on
    cream paper is not. So a column or row of the box that is more than half
    panel is dropped, which trims the paper off the edge and stops well short
    of the figure next to it.

    numpy is optional (display/requirements-audio.txt) and the import used to sit
    bare in the body, so a machine without it got a ModuleNotFoundError from the
    middle of a crop loop. It cannot fall back to the untrimmed box: this function
    is the reason a portrait is not a page of prose, so degrading to `box` would
    ship exactly the wrong crop this script refuses to guess. It refuses instead,
    which main() already reports per college.
    """
    try:
        import numpy as np
    except ImportError:
        raise Refused("numpy is needed to trim lore panels out of the portrait "
                      "boxes; install it with: pip3 install -r "
                      "display/requirements-audio.txt") from None
    x0, y0, x1, y1 = box
    a = np.asarray(im).astype(float)[y0:y1, x0:x1]
    mx, mn = a.max(2), a.min(2)
    sat, lum = mx - mn, a.mean(2)
    panel = (sat < PANEL_SAT_MAX) & (lum > PAPER_LUM_MIN)
    if not panel.any():
        return box
    keep_c = panel.mean(0) <= 0.5
    keep_r = panel.mean(1) <= 0.5
    if not keep_c.any() or not keep_r.any():
        return box
    cx0 = x0 + int(np.argmax(keep_c))
    cx1 = x0 + int(len(keep_c) - np.argmax(keep_c[::-1]))
    ry0 = y0 + int(np.argmax(keep_r))
    ry1 = y0 + int(len(keep_r) - np.argmax(keep_r[::-1]))
    # Keep at least a third of each axis, so a mostly-panel box is left alone
    # rather than collapsing to a sliver of someone's chin.
    if cx1 - cx0 < (x1 - x0) // 3 or ry1 - ry0 < (y1 - y0) // 3:
        return box
    return (cx0, ry0, cx1, ry1)


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
    # The input is checked BEFORE the optional dependency is imported. The import
    # used to come first, so a machine without Pillow raised ModuleNotFoundError
    # out of the top of this function and the "does not exist" refusal was
    # unreachable: asking about a file that is not there crashed instead of
    # saying so. Pillow is runtime-only and deliberately not installed by CI, so
    # the refusal path must not depend on it. A missing sheet is a question about
    # the filesystem, and the filesystem does not need Pillow to answer it.
    if not sheet.exists():
        raise Refused(f"{sheet} does not exist")
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
        sq = square_box(trim_panels(im, box), im.size)
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
    tiles = [p for c in colleges for p in sorted(out.glob(f"{c}-*.png"))
             if p.name != "_contact-sheet.png"]
    if not tiles:
        # Same ordering fix as run(), applied here too. "There are no crops to
        # compose" is a question about a directory, and the directory does not
        # need Pillow. Pillow is deliberately absent on CI, so with the import
        # above this refusal the GM is told on their own machine and handed a
        # ModuleNotFoundError on ours, which is the exact split #119 just closed
        # for run() and left open here.
        raise Refused(f"nothing to check in {out}")
    from PIL import Image
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
