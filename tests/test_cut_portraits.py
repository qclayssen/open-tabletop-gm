"""cut_portraits.py: a grid crop that is off by a column loses a face.

The whole tool is arithmetic on a rectangle, and arithmetic on a rectangle does
not fail loudly -- it produces a plausible image of the wrong thing. A 1000px
sheet divided into three is 333/333/334, and a naive `col * 560` on a 1680px
sheet works by luck. So the cell geometry is pinned, including the case that
divides exactly and the two that do not.
"""
from __future__ import annotations

import importlib.util
import itertools
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
Image = pytest.importorskip("PIL.Image", reason="Pillow is needed to re-read the cut")

spec = importlib.util.spec_from_file_location(
    "cut_portraits", ROOT / "scripts" / "cut_portraits.py")
cut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut)


def test_a_sheet_dividing_exactly_covers_every_pixel():
    """1680x940 into 3x2 is 560x470 exactly. The three columns and two rows must
    tile the sheet with no gap and no overlap, or a cell boundary falls inside a
    face."""
    boxes = [cut._cell_box(1680, 940, r, c, 3, 2) for r in range(2) for c in range(3)]
    assert boxes[0] == (0, 0, 560, 470)
    assert boxes[2] == (1120, 0, 1680, 470)
    assert boxes[3] == (0, 470, 560, 940)
    assert boxes[5] == (1120, 470, 1680, 940)
    for a, b in itertools.pairwise(boxes):
        if a[3] == b[1] and a[1] == b[3]:      # same row, left to right
            assert a[2] == b[0], f"{a} and {b} leave a gap or overlap"
        if a[3] == b[1] and a[0] == b[0]:      # same column, top to bottom
            assert a[2] == b[2], f"{a} and {b} are not the same width"


@pytest.mark.parametrize("w,h", [(1000, 667), (999, 501), (1679, 941)])
def test_an_undivisible_sheet_keeps_every_pixel_in_the_grid(w, h):
    """The remainder belongs to the LAST cell. Losing it would crop the right
    edge or the bottom off the final portrait, and the bottom is where a chest
    lives."""
    boxes = [cut._cell_box(w, h, r, c, 3, 2) for r in range(2) for c in range(3)]
    assert boxes[0][0] == 0 and boxes[0][1] == 0
    assert boxes[-1][2] == w, "the last column does not reach the right edge"
    assert boxes[-1][3] == h, "the last row does not reach the bottom edge"
    for row in (boxes[0:3], boxes[3:6]):
        for a, b in itertools.pairwise(row):
            assert a[2] == b[0], f"{a} and {b} are not contiguous"
    for col in (boxes[0::3], boxes[1::3], boxes[2::3]):
        for a, b in itertools.pairwise(col):
            assert a[3] == b[1], f"{a} and {b} are not contiguous"
            assert a[0] == b[0] and a[2] == b[2], "cells in a column differ in width"


def test_a_token_is_a_circle_with_transparent_corners():
    """The point of the tool. A square token with a photo's corners still in it
    reads as a sticker on the board."""
    src = Image.new("RGB", (1680, 940), (200, 40, 40))
    out = cut.to_token(src.crop(cut._cell_box(1680, 940, 0, 0, 3, 2)))
    assert out.size == (256, 256)
    assert out.mode == "RGBA"
    assert out.getpixel((1, 1))[3] == 0, "corner is not transparent"
    assert out.getpixel((128, 128))[3] == 255, "centre is not opaque"
    assert out.getpixel((128, 128))[:3] == (200, 40, 40), "colour was altered"


def test_the_ring_is_off_when_the_image_already_has_one():
    """Three of the cast images have a rim painted in, and no two rims are the
    same width -- adding a second ring reads as a mistake, so `--no-ring` has to
    actually skip it."""
    src = Image.new("RGB", (512, 512), (90, 90, 90))
    with_ring = cut.to_token(src, ring=True)
    without = cut.to_token(src, ring=False)
    # identical except at the edge, where the ring is drawn
    assert with_ring.getpixel((128, 2))[:3] == cut.RING
    assert without.getpixel((128, 2))[:3] == (90, 90, 90)
    assert with_ring.getpixel((128, 128)) == without.getpixel((128, 128))


def test_an_inset_never_collapses_a_cell():
    """`--inset` is for cropping inside a frame. An inset larger than the cell
    must clamp rather than invert the box, which would crop from the wrong edge
    and silently return the neighbouring panel."""
    box = (0, 0, 100, 100)
    l, t, r, b = cut._inset(box, 10_000)
    assert l < r and t < b, "the box inverted"
    assert (r - l) >= 2 and (b - t) >= 2
