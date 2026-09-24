"""Grid geometry: labels, diagonals, movement cost, pathing, sight and cover.

Every expected number below is worked out by hand in the comment beside it.
"""
from __future__ import annotations

import pytest

from tests.tactics_fixtures import grid

Grid, MoveOptions = grid.Grid, grid.MoveOptions


def open_grid(w=13, h=13, diagonals="5"):
    return Grid(["." * w] * h, diagonals=diagonals)


# ─── labels ──────────────────────────────────────────────────────────────────

def test_square_labels_round_trip():
    assert grid.label((3, 4)) == "D5"
    assert grid.parse_square("D5") == (3, 4)
    assert grid.parse_square(" d5 ") == (3, 4)
    assert grid.label((26, 0)) == "AA1"
    assert grid.parse_square("AA1") == (26, 0)
    for p in [(0, 0), (25, 9), (27, 15), (51, 3)]:
        assert grid.parse_square(grid.label(p)) == p


def test_bad_square_is_rejected():
    with pytest.raises(ValueError):
        grid.parse_square("5D")


# ─── diagonals ───────────────────────────────────────────────────────────────

def test_diagonals_cost_five_by_default():
    g = open_grid()
    assert g.distance((0, 0), (3, 3)) == 15      # 3 diagonal squares x 5
    assert g.distance((0, 0), (4, 1)) == 20      # max(4, 1) squares


def test_variant_diagonals_alternate_five_and_ten():
    g = open_grid(diagonals="5-10-5")
    assert g.distance((0, 0), (1, 1)) == 5       # 5
    assert g.distance((0, 0), (2, 2)) == 15      # 5 + 10
    assert g.distance((0, 0), (3, 3)) == 20      # 5 + 10 + 5
    assert g.distance((0, 0), (4, 1)) == 20      # 3 straight + 1 diagonal = 15 + 5


def test_reachable_area_with_30_ft():
    # Rule "5": 30 ft reaches every square within 6 in both axes: 13 x 13 - start.
    assert len(open_grid().reachable((6, 6), 30)) == 168
    # Variant: the corner (0, 0) is 6 diagonals = 5+10+5+10+5+10 = 45 ft away.
    g = open_grid(diagonals="5-10-5")
    reach = g.reachable((6, 6), 30)
    assert (0, 0) not in reach
    assert reach[(2, 2)] == 30                   # 4 diagonals: 5+10+5+10
    assert reach[(0, 6)] == 30                   # 6 straight


# ─── terrain ─────────────────────────────────────────────────────────────────

def test_difficult_terrain_costs_double():
    g = Grid([".....",
              ".,,..",
              "....."])
    # A1 -> B1 (5) -> C2 diagonal into difficult (10) = 15.
    # The alternative A1 -> B2 (10) -> C2 (10) = 20 is worse.
    path, feet = g.path((0, 0), (2, 1))
    assert feet == 15
    assert path == [(0, 0), (1, 0), (2, 1)]


def test_difficult_does_not_stack_with_creature_space():
    g = Grid([".,."])
    opts = MoveOptions(occupied=frozenset({(1, 0)}))
    # B1 is difficult terrain AND an ally's space: 10 to enter, not 15. Then 5 into C1.
    assert g.path((0, 0), (2, 0), opts=opts)[1] == 15


def test_crawling_stacks_with_difficult_terrain():
    g = Grid(["..,"])
    opts = MoveOptions(crawling=True)
    # Crawl on floor 10, crawl into difficult 15 (PHB: 1 + 1 + 1 feet per foot).
    assert g.path((0, 0), (2, 0), opts=opts)[1] == 25


def test_water_needs_a_swim_speed_to_be_normal():
    g = Grid([".~~."])
    assert g.path((0, 0), (3, 0))[1] == 25                           # 10 + 10 + 5
    assert g.path((0, 0), (3, 0), opts=MoveOptions(swim=True))[1] == 15


def test_walls_block_and_force_a_detour():
    g = Grid(["...",
              ".#.",
              "..."])
    assert not g.passable((1, 1))
    # A2 to C2 must go round the wall: A2 -> B1 -> C2 = 10 ft.
    assert g.path((0, 1), (2, 1))[1] == 10


def test_no_squeezing_between_walls_that_touch_at_a_corner():
    g = Grid([".#",
              "#."])
    assert g.path((0, 0), (1, 1)) is None


def test_hostiles_block_allies_do_not():
    g = Grid(["...",
              "..."])
    hostile = MoveOptions(blocked=frozenset({(1, 0)}))
    assert (1, 0) not in g.path((0, 0), (2, 0), opts=hostile)[0]    # goes round
    ally = MoveOptions(occupied=frozenset({(1, 0), (1, 1)}))
    path, feet = g.path((0, 0), (2, 0), opts=ally)
    assert feet == 15                                              # 10 through the ally + 5
    assert (1, 0) not in g.reachable((0, 0), 30, ally)             # can't end in their space


# ─── sight and cover ─────────────────────────────────────────────────────────

def test_a_pillar_gives_half_cover():
    g = Grid([".....",
              ".....",
              "..#..",
              ".....",
              "....."])
    # From A3 to E3 with a pillar at C3: from any attacker corner, the two lines
    # to the target's top corners graze the pillar's edge and the two to its
    # bottom corners pass through it: 2 of 4 blocked = half cover.
    assert g.cover((0, 2), (4, 2)) == {"los": True, "cover": 2}


def test_a_full_wall_blocks_sight():
    g = Grid(["..#..",
              "..#..",
              "..#.."])
    assert not g.line_of_sight((0, 1), (4, 1))


def test_open_ground_has_no_cover():
    assert open_grid().cover((0, 0), (5, 3)) == {"los": True, "cover": 0}


def test_creatures_give_at_most_half_cover():
    g = open_grid()
    c = g.cover((0, 2), (4, 2), creatures={(2, 1), (2, 2), (2, 3)})
    assert c == {"los": True, "cover": 2}
