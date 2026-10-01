"""`srd-art` is the last art pool searched, and it has to stay last.

WHY
===
`display/srd-art/` is 334 machine-generated, unlicensed, unevenly good portraits
fetched by `scripts/install_srd_art.py`. It closes 264 holes in the bestiary at
once, which is a large enough win that it will quietly become the pool everything
else is measured against -- and then a curated portrait or a reviewed approval
loses to a download, and nobody notices because the coverage number went UP.

So the precedence is pinned here rather than left to the order of a tuple:

  * `srd-art` is searched after every pool a human chose from;
  * a curated file still wins a collision, and the loser is REPORTED, because a
    precedence nobody can see is indistinguishable from a bug;
  * a missing directory is an empty pool and not an error, because the art is
    gitignored and a fresh clone will not have it.

The other thing worth pinning is what this pool must never do: fill a gap. A
creature with no file here has to stay uncovered and be printed by name. There is
no nearest-neighbour path, and a test that says so is cheaper than a token that
puts the wrong creature at the table.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import statblock_art as sa


def test_srd_art_is_searched_after_every_human_chosen_pool():
    """Last, and the assertion is on the INDEX not just the tuple, so a reordered
    `POOLS` cannot pass while `POOL_DIRS` still says otherwise."""
    assert sa.POOLS[-1] == "srd-art"
    for human in ("bestiary", "faculty", "display-tokens"):
        assert sa.POOLS.index(human) < sa.POOLS.index("srd-art"), human


def test_every_pool_named_in_pools_has_a_directory_entry():
    """A pool added to `POOLS` without a `POOL_DIRS` row is looked up under a
    missing key and the whole run dies on a fresh clone."""
    assert set(sa.POOLS) == set(sa.POOL_DIRS)


def test_a_curated_picture_beats_a_downloaded_one(tmp_path):
    """The whole reason this pool is last. `aboleth` is in both pools with
    different pictures, and the curated one has to win."""
    curated = tmp_path / "curated"
    downloaded = tmp_path / "downloaded"
    curated.mkdir()
    downloaded.mkdir()
    (curated / "aboleth.png").write_bytes(b"curated")
    (downloaded / "aboleth.png").write_bytes(b"downloaded")

    index = sa.ArtIndex()
    index.add_pool("bestiary", curated)
    index.add_pool("srd-art", downloaded)

    hit = index.resolve("Aboleth")
    assert hit is not None
    assert hit.pool == "bestiary", "a download outranked a curated portrait"
    assert index.shadowed, "the loser was not recorded, so the choice is invisible"


def test_a_missing_art_directory_is_an_empty_pool_not_an_error(tmp_path):
    """The art is gitignored. A clone that has never run the installer must still
    produce a full report, with the creatures simply uncovered."""
    index = sa.ArtIndex()
    index.add_pool("srd-art", tmp_path / "never-installed")
    assert index.resolve("Aboleth") is None


def test_the_pool_fills_nothing_and_guesses_nothing(tmp_path):
    """`resolve` is exact on the slug. There is no prefix match, no substring
    match and no nearest name, and that is the property that makes a downloaded
    portrait safe to hand a statblock at all."""
    pool = tmp_path / "srd-art"
    pool.mkdir()
    (pool / "adult-black-dragon.png").write_bytes(b"x")
    (pool / "goblin.png").write_bytes(b"x")

    index = sa.ArtIndex()
    index.add_pool("srd-art", pool)

    assert index.resolve("Adult Black Dragon") is not None
    for near_miss in ("Black Dragon", "Adult Black", "Dragon",
                      "Young Black Dragon", "Goblin King", "gob"):
        assert index.resolve(near_miss) is None, near_miss


def test_the_pool_holds_no_generic_stand_ins():
    """Nothing that could be mistaken for a named creature. The Strixhaven pack's
    `scholar-N` tokens are refused elsewhere by name; this pins that the
    downloaded pool, which is machine-named, holds nothing of that shape."""
    pool = ROOT / "display" / "srd-art"
    if not pool.is_dir():
        pytest.skip("srd art is not installed")
    bad = [p.name for p in pool.glob("*.png")
           if "-scholar-" in p.stem or "-student-" in p.stem
           or p.stem.endswith("-apprentice") or "-mascot-" in p.stem]
    assert not bad, bad
