#!/usr/bin/env python3
"""cut_portraits.py: turn generated character art into 256px circular tokens.

Run:
    python3 scripts/cut_portraits.py --in SHEET.png --sheet \
        mabli-quenn:0:0 theodric-vane:0:1 dace-orrin:0:2 \
        petra-lune:1:0 tam:1:1
    python3 scripts/cut_portraits.py --in YSOLDE.png --split 2 \
        --names ysolde-marrow stopped-hand
    python3 scripts/cut_portraits.py --in KAIROS.png --name kairos --no-ring

WHY THIS EXISTS
===============
The cast portraits are generated, so they arrive as whatever shape the model
produced: a cast sheet on a 3x2 grid, a two-panel diptych, or a single square
with a rim drawn into it. Every token in `display/tokens/` is a 256x256 circle,
because that is what a VTT board shows and what `register_tokens.py` thumbnails
against. So each file has to become one, and doing it by hand for ten files is
where a token ends up 12px off centre or 4px too tight.

The rims are the awkward part, and the reason for `--no-ring`. Three of these
already have a rim painted INTO the image by the model -- and not the same one
each time, because a model reinvents a frame on every generation. Two answers:

  * A cell cut from a sheet has no frame, so it gets the plain bronze ring below
    and matches the hearden pack.
  * A single image that already has a frame keeps ITS frame, cropped to just
    inside it. Adding a second ring on top of the first is a double frame, and
    the two rims are never the same width, so the result reads as a mistake.

That is why the ring is opt-out rather than mandatory: this script's job is to
make every file a usable circular token, not to make them identical, and the
frames were never going to be identical.

The crop is a circle inscribed in the square cell, so nothing important is cut
off by the shape: the inscribed circle touches all four edges' midpoints and
leaves the corners, which is where the transparency goes.
"""
from __future__ import annotations

import argparse
import pathlib

TOKENS = pathlib.Path(__file__).resolve().parent.parent / "display" / "tokens"
SIZE = 256           # the pack's resolution, and register_tokens' thumbnail size
RING = (150, 126, 82)   # the bronze the hearden rims are closest to


def _pillow():
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("Pillow is required: pip install Pillow")
    return Image


def _cell_box(w: int, h: int, row: int, col: int, cols: int = 3, rows: int = 2):
    """The (left, upper, right, lower) box of one cell of a cols x rows grid.

    Integer arithmetic with a remainder handed to the last column and row, so a
    width that does not divide by three -- 1680 into 560 into 560 into 560 is
    exact, 1000 into 333 into 333 into 334 is not -- loses its pixels in the last
    cell rather than dropping a column of the face off the right edge.
    """
    cw, ch = w // cols, h // rows
    left = col * cw
    top = row * ch
    right = w if col == cols - 1 else left + cw
    bottom = h if row == rows - 1 else top + ch
    return left, top, right, bottom


def _inset(box, amount: int):
    """Shrink a box by `amount` pixels on every side, never past its middle."""
    l, t, r, b = box
    w, h = r - l, b - t
    amount = min(amount, (w // 2) - 1, (h // 2) - 1)
    return l + amount, t + amount, r - amount, b - amount


def to_token(im, ring: bool = True):
    """Centre-crop to a square, resize, and cut it into a circle."""
    Image = _pillow()
    w, h = im.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    square = im.convert("RGBA").crop((left, top, left + side, top + side))
    square = square.resize((SIZE, SIZE), Image.LANCZOS)

    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    mask = Image.new("L", (SIZE * 4, SIZE * 4), 0)
    from PIL import ImageDraw
    ImageDraw.Draw(mask).ellipse((0, 0, SIZE * 4 - 1, SIZE * 4 - 1), fill=255)
    mask = mask.resize((SIZE, SIZE), Image.LANCZOS)
    out.paste(square, (0, 0), mask)

    if ring:
        draw = ImageDraw.Draw(out)
        draw.ellipse((1, 1, SIZE - 2, SIZE - 2), outline=RING + (255,), width=4)
    return out


def _save(im, name: str, out_dir: pathlib.Path) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{name}.png"
    im.save(dest, format="PNG", optimize=True)
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--in", dest="src", type=pathlib.Path, required=True,
                    help="the generated image to cut")
    ap.add_argument("--out", type=pathlib.Path, default=TOKENS,
                    help="where the tokens go (default display/tokens/)")
    ap.add_argument("--sheet", nargs="+", metavar="NAME:ROW:COL",
                    help="cut a cols x rows grid: one NAME:ROW:COL per cell")
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--split", type=int, metavar="N",
                    help="cut a diptych into N panels side by side")
    ap.add_argument("--names", nargs="+", metavar="NAME",
                    help="names for --split panels, left to right")
    ap.add_argument("--name", help="name for a single portrait")
    ap.add_argument("--no-ring", action="store_true",
                    help="keep the frame already in the image; do not add a ring")
    ap.add_argument("--inset", type=int, default=0,
                    help="pixels to trim inside each cell before cutting, for "
                         "cropping just within a frame that is already there")
    args = ap.parse_args(argv)

    if not args.src.is_file():
        raise SystemExit(f"no image at {args.src}")
    Image = _pillow()
    im = Image.open(args.src)
    ring = not args.no_ring
    written = []

    if args.sheet:
        for spec in args.sheet:
            name, row, col = spec.rsplit(":", 2)
            box = _cell_box(im.width, im.height, int(row), int(col),
                            args.cols, args.rows)
            if args.inset:
                box = _inset(box, args.inset)
            written.append(_save(to_token(im.crop(box), ring), name, args.out))
    elif args.split:
        if not args.names or len(args.names) != args.split:
            raise SystemExit(f"--split {args.split} needs exactly {args.split} "
                             f"--names")
        edge = im.width // args.split
        for i, name in enumerate(args.names):
            box = (i * edge, 0, im.width if i == args.split - 1 else (i + 1) * edge,
                   im.height)
            if args.inset:
                box = _inset(box, args.inset)
            written.append(_save(to_token(im.crop(box), ring), name, args.out))
    elif args.name:
        written.append(_save(to_token(im, ring), args.name, args.out))
    else:
        raise SystemExit("one of --sheet, --split or --name is required")

    for dest in written:
        print(f"  {dest}  {dest.stat().st_size:,}B")
    print(f"\n{len(written)} token(s) written to {args.out}")
    print("Run: python3 scripts/install_tokens.py   to add manifest entries")
    print("Then: python3 scripts/statblock_art.py --campaign DIR --vault DIR "
          "--stats")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
