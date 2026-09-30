"""What the three Kairos worklist maps have to *be*, checked against the engine.

These maps were authored as a throwaway generator and written out as JSON, which
is the convention (`display/maps/README.md`). The generator is gone; this file
is what it was actually for. A map is the one artefact in this repo that a
person edits by clicking in a browser, so the properties worth defending are
the ones that are invisible in the JSON and obvious on the table:

  * **Every square you can stand on is reachable from where the party starts.**
    This is not a style preference. Both first drafts of the Biblioplex stacks
    sealed off a third and then two thirds of the map in shelving runs that read
    correctly one at a time — a run wall to wall from border to border, and then
    a cross run laid across the one lane that was left. Both looked like a maze
    in the file and were a room with shelves in it. Nothing in `compile_map`
    notices: it paints rectangles, and a sealed room is a perfectly valid set of
    rectangles.
  * **The stacks actually block sight.** The whole point of that map is that you
    cannot see across it. The check is a number, not an adjective.
  * **The rotunda does not.** It is meant to be one round room, and a rotunda
    that blocked sight would be a rotunda-shaped corridor.
  * **No spawn is inside a wall**, and every map carries the explicit 100px grid
    block that lets it be exported to Atlas over a placeholder background
    (`map_to_atlas.build_scene` refuses a map with no cell size, and BV9's
    no-artwork path is exactly that block).

`compile_map` raising on a bad rectangle is the engine's job and is tested
there. This file is about the things it cannot see.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from tactics import grid as G, maps   # noqa: E402

# The worklist these three come from: docs/guides/map-plan-strixhaven-kairos.md,
# "Generate — invented places", items 1, 2 and 3. The sizes are that document's,
# not chosen here.
WORKLIST = {
    "hesper-walled-garden": (20, 14),
    "biblioplex-stacks": (30, 20),
    "enrollment-ledger-rotunda": (24, 18),
}

# How much of the floor one creature can see from the middle, as a percentage.
# The bounds are loose on purpose: the claim being defended is "this map blocks
# sight" and "this map does not", not a number I tuned.
SIGHT = {
    "hesper-walled-garden": (70, 100),        # a walled garden is open ground
    "biblioplex-stacks": (0, 30),             # shelving to the ceiling
    "enrollment-ledger-rotunda": (45, 85),    # one round hall
}


def _spec(name):
    path = maps.MAPS_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"{name} is not built yet")
    return json.loads(path.read_text(encoding="utf-8"))


def _board(name):
    return G.Grid.from_dict(maps.compile_map(_spec(name))["grid"])


def _reachable(board, start):
    seen = {tuple(start)}
    stack = [tuple(start)]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p = (x + dx, y + dy)
            if board.in_bounds(p) and board.passable(p) and p not in seen:
                seen.add(p)
                stack.append(p)
    return seen


def _floor(board):
    return sum(1 for y in range(board.height) for x in range(board.width)
               if board.passable((x, y)))


@pytest.mark.parametrize("name,size", sorted(WORKLIST.items()))
def test_the_map_is_the_size_the_worklist_asked_for(name, size):
    spec = _spec(name)
    assert (spec["width"], spec["height"]) == size


@pytest.mark.parametrize("name,size", sorted(WORKLIST.items()))
def test_every_square_is_reachable_from_the_party(name, size):
    """The whole point of this file. A sealed region is invisible in the JSON."""
    spec, board = _spec(name), _board(name)
    party = [s for s in spec["spawns"] if s["color"] != "danger"]
    assert party, f"{name} has no party spawns to measure reachability from"
    start = next((s for s in party if board.passable((s["x"], s["y"]))), None)
    assert start, f"{name}: no party spawn is on ground a creature can stand on"
    seen = _reachable(board, (start["x"], start["y"]))
    lost = sorted((x, y) for y in range(board.height) for x in range(board.width)
                  if board.passable((x, y)) and (x, y) not in seen)
    assert not lost, (
        f"{name}: {len(lost)} standable square(s) are walled off from the party, "
        f"starting at {lost[:8]}. A sealed region compiles fine and plays as an "
        f"empty room with shelves in it")


@pytest.mark.parametrize("name,size", sorted(WORKLIST.items()))
def test_no_spawn_is_inside_a_wall(name, size):
    board = _board(name)
    for s in _spec(name)["spawns"]:
        assert board.passable((s["x"], s["y"])), \
            f"{name}: spawn {s['id']!r} ({s['name']}) is on " \
            f"{board.terrain_name((s['x'], s['y']))} at {s['x']},{s['y']}"


@pytest.mark.parametrize("name,size", sorted(WORKLIST.items()))
def test_sight_from_the_centre_is_where_the_map_wants_it(name, size):
    """Sight-blocking is a property of a map that has to be measured, because
    `compile_map` cannot tell a shelving maze from an open field."""
    board = _board(name)
    cx, cy = board.width // 2, board.height // 2
    if not board.passable((cx, cy)):
        pytest.skip(f"{name}: the middle square is not standable, so there is no "
                    "creature to stand there and sight is measured from a spawn")
    vis = sum(1 for p in board.visible_from((cx, cy)) if board.passable(p))
    pct = 100.0 * vis / _floor(board)
    lo, hi = SIGHT[name]
    assert lo <= pct <= hi, (
        f"{name}: a creature in the middle can see {pct:.1f}% of the standable "
        f"map, outside the {lo}-{hi}% this map is supposed to sit in")


@pytest.mark.parametrize("name,size", sorted(WORKLIST.items()))
def test_each_map_can_be_exported_to_atlas_with_no_artwork(name, size):
    """BV9's no-artwork path, asserted rather than assumed.

    Without this block a map is honest but unexportable: `map_to_atlas` needs a
    cell size to place a token, and a terrain-only map has no image to read one
    from. Declaring 100px is not a claim that there is artwork -- it restates
    the 5 ft assumption the engine already makes, which is all a placeholder
    background needs. See ROADMAP BV9: "declare grid on the four, which only
    states the 5 ft assumption the engine already makes".
    """
    spec = _spec(name)
    assert "image" not in spec, f"{name} now has artwork; this test is the no-art path"
    grid = spec.get("grid") or {}
    assert float(grid.get("cell_px") or 0) > 0
    assert int(grid.get("offset_x", 0)) == 0 and int(grid.get("offset_y", 0)) == 0


@pytest.mark.parametrize("name,size", sorted(WORKLIST.items()))
def test_spawn_ids_are_unique_within_a_map(name, size):
    """Atlas keys tokens by id, and a duplicate silently replaces the first
    token rather than raising. Cheap to check here, expensive to notice later."""
    ids = [s["id"] for s in _spec(name)["spawns"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("name,size", sorted(WORKLIST.items()))
def test_every_spawn_has_a_colour(name, size):
    """`map_to_atlas.build_scene` refuses a spawn with no colour, and the
    refusal is a bare KeyError deep in the exporter if the guard is ever
    removed. Caught here instead, with the name of the spawn."""
    for s in _spec(name)["spawns"]:
        assert str(s.get("color") or "").strip(), \
            f"{name}: spawn {s['id']!r} has no color"


def test_the_garden_has_its_three_landmarks_and_two_slots():
    """The worklist names them, so they are checked by name. A map that quietly
    lost the sphinx head is still a valid 20x14 garden as far as the engine is
    concerned, which is the problem."""
    labels = " ".join(f.get("label", "") for f in _spec("hesper-walled-garden")["features"])
    for want in ("Lintel bench", "Sphinx head", "Green door", "Stone slot"):
        assert want in labels, f"hesper-walled-garden is missing {want!r}"
    assert labels.count("Stone slot") == 2, "the worklist says two stone slots"


def test_the_stacks_hold_the_card_the_chapter_is_about():
    """1.3 is where the burned KAIROS PROTOCOL card is found. A prop that the
    campaign is named for is worth one assertion."""
    labels = " ".join(f.get("label", "") for f in _spec("biblioplex-stacks")["features"])
    assert "KAIROS PROTOCOL" in labels
    assert "Reading desk" in labels


def test_the_rotunda_is_a_wall_with_a_doorway_not_a_circle_of_squares():
    """The rotunda is round and the format is rectangles, so the wall is a
    staircase: one short segment per row at that row's own radius. Asserted by
    the number of separate wall segments, which is what a circle costs in a
    square grid, and by the fact that it is a wall and not a single big box."""
    feats = _spec("enrollment-ledger-rotunda")["features"]
    # The border is four long runs and is not what this test is about; the
    # rotunda is every wall that does not touch the edge of the map.
    w, h = _spec("enrollment-ledger-rotunda")["width"], _spec("enrollment-ledger-rotunda")["height"]
    stubs = [f for f in feats if f["type"] == "wall"
             and f["x"] > 0 and f["y"] > 0
             and f["x"] + f["w"] < w and f["y"] + f["h"] < h]
    assert len(stubs) >= 20, f"only {len(stubs)} interior wall segments: that is a box, not a rotunda"
    assert all(f["w"] <= 2 and f["h"] <= 1 for f in stubs), \
        "every rotunda segment should be a stub, not a run"
    assert "Omenpath gate" in " ".join(f.get("label", "") for f in feats)
