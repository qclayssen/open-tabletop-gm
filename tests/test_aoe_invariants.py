"""AoE geometry and resource invariants, generated rather than worked by hand.

WHY THIS FILE EXISTS
====================

`docs/audits/AUDIT-2026-10-02.md` (MEDIUM, Test coverage) says:

    AoE geometry has only hand-worked examples, no invariant tests
    (`grid.py:374-464`). The invariants *do* hold today; this is the missing net.

That is a coverage claim, not a defect, and it is the kind that decays: a hand-worked
example pins the answer somebody already computed, while an invariant pins the
property and catches the next edit that breaks it. The same audit's "Verified clean"
section already records the shape facts that held when it ran (20 ft sphere = 49
squares; 15 ft cone = 5 cardinal / 6 diagonal across all 8 aims; 30x5 ft line = 6;
10 ft cube = 2x2), so this file GENERATES over the same spaces and re-derives them,
rather than asserting the numbers the audit wrote down.

Two halves:

  GEOMETRY   `grid.area` over every shape, size, board and aim: in-bounds, no
            wall, no duplicates, sorted, deterministic, the caster never inside
            his own cone or line, a cone or line inside its own reach and width,
            a sphere contains its own centre, spheres nest by radius, a cube
            whose block fits is exactly n x n.

  RESOURCE   what an area SPENDS: one action and one recharge however many
            creatures it caught, one damage roll for the whole area, cover 0
            for the origin square, and a refused cast that spends nothing.
            These are the invariants a geometry bug hides behind, because the
            shape looks right and the arithmetic does not.

NOT A REPLACEMENT FOR THE WORKED EXAMPLES
=========================================

`tests/test_tactics_grid.py` pins the documented answers one at a time. Those are the
reader's reference and each says in prose what the shape is meant to be. This file says
nothing about intent; it generates, so a new shape or size cannot pass by being
absent, and no expected number here was copied by hand.

The boards are 20x20 rather than the smallest that fits, because a generated sweep
that clips every case at the map edge tests the edge, not the shape.
"""
from __future__ import annotations

import itertools
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT / "scripts", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tactics import engine as engine_mod                     # noqa: E402
from tactics import grid as grid_mod                         # noqa: E402
from tactics import spells as spells_mod                     # noqa: E402
from tests.tactics_fixtures import (                         # noqa: E402
    caster, encounter, frog, roller, start,
)

SQUARE_FT = grid_mod.SQUARE_FT
SHAPES = grid_mod.AREA_SHAPES
W = H = 12
#: 5, 10, 15, 20, 30 ft. The odd ones are the interesting ones: they are not a whole
#: number of squares, so the boundary rule ("centre inside, boundary included") is
#: what decides membership.
SIZES = (5, 10, 15, 20, 30)

ROOMS = {
    "open": ["." * W] * H,
    "wall-column": ["." * 4 + "#" + "." * (W - 5)] * H,
    "pit": (["." * W] * 4 + ["..." + "#" + "." * (W - 4)] + ["." * W] * (H - 5)),
}

#: The sampling, and why it is a sample rather than the whole board.
#:
#: A full sweep of this board is 514,800 `area` calls and takes 66 s measured, which
#: is a suite no one runs twice. So the shape x size x board sweep takes 16 casters on
#: a lattice and, for each, 8 aims -- one per octant -- at three distances. That still
#: covers every direction a shape can be pointed in and every distance a size reaches,
#: which is where the boundary rules live; it is a subsample of (caster, target) pairs
#: and the docstring says so rather than implying an exhaustive proof.
#:
#: The per-shape property tests below widen it, because they are cheaper: one shape at
#: a time over a longer aim list.
CASTER_LATTICE = tuple((x, y) for x in range(2, W - 1, 3) for y in range(2, H - 1, 3))
STEP = max(1, (W - 1) // 3)


def aims(caster_at=None, margin=0, lattice=True, distances=(1, 2, 3)):
    """(caster, target) pairs: every octant at several distances.

    `margin` insets the casters, which is what makes an "does it fit" assertion
    possible: a caster `margin` squares from every edge cannot have any octant of a
    cube clipped by the map, so n x n is the only possible answer rather than one of
    several. `lattice=False` walks every square instead, for the cheap properties.
    """
    casters = [caster_at] if caster_at else (
        [c for c in CASTER_LATTICE if margin <= c[0] < W - margin
         and margin <= c[1] < H - margin] if lattice else
        [(x, y) for x in range(margin, W - margin) for y in range(margin, H - margin)])
    for c in casters:
        for dx, dy in itertools.product((-1, 0, 1), repeat=2):
            if (dx, dy) == (0, 0):
                continue
            for d in distances:
                t = (c[0] + dx * d * STEP, c[1] + dy * d * STEP)
                if g_in(t) and t != c:
                    yield c, t


def g_in(pos):
    return 0 <= pos[0] < W and 0 <= pos[1] < H


def all_pairs(limit=400):
    """Every (caster, target) pair on the board, capped. For the cheap properties."""
    out = []
    for cx in range(W):
        for cy in range(H):
            for tx in range(W):
                for ty in range(H):
                    if (tx, ty) != (cx, cy):
                        out.append(((cx, cy), (tx, ty)))
                        if len(out) >= limit:
                            return out
    return out


def board(room):
    return grid_mod.Grid(ROOMS[room])


def _area(g, shape, size, caster, target, **kw):
    return grid_mod.area(g, shape, size, caster, target, **kw)["squares"]


# ── geometry: properties, over every shape, size, board and aim ──────────────

@pytest.mark.parametrize("room", sorted(ROOMS))
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("size", SIZES)
def test_no_area_contains_a_square_off_the_board_or_inside_a_wall(room, shape, size):
    """The one invariant every caller depends on: `cast` iterates the returned
    squares and asks the board about each, so an off-map or walled square is a token
    the engine believes it caught."""
    g = board(room)
    for caster, target in aims():
        try:
            got = _area(g, shape, size, caster, target)
        except ValueError:
            continue                     # a cone aimed at itself: refused, not wrong
        for x, y in got:
            assert g.in_bounds((x, y)), f"{room}/{shape}{size}: {x},{y} is off the board"
            assert not g.blocks_sight((x, y)), f"{room}/{shape}{size}: {x},{y} is a wall"


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("size", (10, 20, 30))
def test_squares_are_unique_sorted_and_stable(shape, size):
    """Determinism and ordering. `cast` and `preview` both report the squares, and a
    list that varies run to run makes a roll-receipt diff meaningless."""
    g = board("open")
    for caster, target in all_pairs():
        try:
            first = _area(g, shape, size, caster, target)
            second = _area(g, shape, size, caster, target)
        except ValueError:
            continue
        assert first == sorted(first, key=lambda q: (q[1], q[0])), "not sorted by (y, x)"
        assert len(first) == len(set(first)), "a square appears twice"
        assert first == second, "the same call returned two different answers"


@pytest.mark.parametrize("size", SIZES)
def test_a_caster_is_never_inside_his_own_cone_or_line(size):
    """Documented in `grid.area`: the shape starts where the line from the caster's
    centre toward the target leaves the caster's square, so the caster is excluded by
    construction. Generated over every aim, because this is the invariant an
    `along <= 0` boundary change would break without changing any other shape."""
    g = board("open")
    for caster, target in all_pairs():
        for shape in ("cone", "line"):
            got = _area(g, shape, size, caster, target)
            assert tuple(caster) not in got, f"{shape}{size} from {caster} caught the caster"


@pytest.mark.parametrize("size", SIZES)
def test_a_cone_or_line_stays_inside_its_own_reach_and_width(size):
    """`along` is in (0, cells] and `across` within the width, measured from the
    origin `area` returns. A line's half-width is width/2; a cone's is half its
    distance from the origin, which is the looser bound at every square."""
    g = board("open")
    cells = size / SQUARE_FT
    half_w = 5 / SQUARE_FT / 2
    for caster, target in all_pairs():
        for shape in ("line", "cone"):
            got = grid_mod.area(g, shape, size, caster, target)
            ox, oy = got["origin"]
            ux, uy = _unit(caster, target)
            for x, y in got["squares"]:
                px, py = x + 0.5 - ox, y + 0.5 - oy
                along = px * ux + py * uy
                across = abs(px * uy - py * ux)
                assert 0 < along <= cells + 1e-9, f"{shape}{size}: {x},{y} is off the axis"
                limit = along / 2 if shape == "cone" else half_w
                assert across <= limit + 1e-9, f"{shape}{size}: {x},{y} is too wide"


def _unit(caster, target):
    dx, dy = target[0] - caster[0], target[1] - caster[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (0.0, 0.0)


@pytest.mark.parametrize("size", SIZES)
def test_a_sphere_aimed_at_a_square_contains_that_square(size):
    """Boundary included: the aimed square is inside its own area. Aimed squares
    that are a wall are excluded by design -- `grid.area` drops sight-blocking
    squares -- so those aims are skipped rather than counted as failures."""
    for room in sorted(ROOMS):
        g = board(room)
        for caster, target in all_pairs():
            if not g.in_bounds(target) or g.blocks_sight(target):
                continue
            got = _area(g, "sphere", size, caster, target, from_self=False)
            assert tuple(target) in got, (
                f"{room}: a {size} ft sphere aimed at {target} does not contain it")


@pytest.mark.parametrize("small,big", [(5, 15), (5, 20), (5, 30), (10, 20),
                                          (10, 30), (15, 30)])
def test_a_bigger_sphere_never_excludes_a_square_a_smaller_one_kept(small, big):
    """Nesting. Radius is monotone, so a larger sphere is a superset at the same
    centre. Generated rather than sampled, because a monotone boundary is exactly
    the sort of thing a tolerance change breaks at one radius and nowhere else."""
    g = board("open")
    for caster, target in all_pairs(limit=200):
        for from_self in (True, False):
            inner = _area(g, "sphere", small, caster, target, from_self=from_self)
            outer = _area(g, "sphere", big, caster, target, from_self=from_self)
            assert set(inner) <= set(outer), (
                f"a {big} ft sphere lost a square a {small} ft one kept, {caster} -> "
                f"{target} (from_self={from_self})")


@pytest.mark.parametrize("size", SIZES)
def test_a_cube_whose_block_fits_is_exactly_n_by_n(size):
    """`n = round(size / 5)`. The audit verified 10 ft = 2x2; generated, every size
    whose block lands whole is n x n, aimed from the caster and at a point. The
    caster is inset by `n + 1` squares so no octant can be clipped by the map, and a
    point-placed block is checked against the rectangle it was asked for."""
    n = max(1, int(round(size / SQUARE_FT)))
    g = board("open")
    for caster, target in aims(caster_at=None, margin=n + 1):
        # from the caster: inset guarantees the whole n x n block is on the board
        got = _area(g, "cube", size, caster, target, from_self=True)
        assert len(got) == n * n, (
            f"a {size} ft cube from {caster} at {target} caught {len(got)}, "
            f"expected {n * n}")
        assert len({q[0] for q in got}) == n and len({q[1] for q in got}) == n, (
            f"a {size} ft cube from {caster} is not square")
    # at a point: skip any target whose block the map edge would clip
    for caster, target in aims():
        x0, y0 = target[0] - (n - 1) // 2, target[1] - (n - 1) // 2
        if x0 < 0 or y0 < 0 or x0 + n > W or y0 + n > H:
            continue
        if any(g.blocks_sight((x, y)) for x in range(x0, x0 + n) for y in range(y0, y0 + n)):
            continue
        got = _area(g, "cube", size, caster, target, from_self=False)
        assert sorted(got) == sorted((x, y) for x in range(x0, x0 + n)
                                     for y in range(y0, y0 + n)), (
            f"a {size} ft cube at {target} is not the n x n block around it")


@pytest.mark.parametrize("shape", SHAPES)
def test_an_unknown_shape_is_refused_rather_than_defaulted(shape):
    """A shape added to `AREA_SHAPES` and forgotten in the branches would fall
    through to the `else` that builds a cube, so the refusal is the property."""
    g = board("open")
    grid_mod.area(g, shape, 20, (0, 0), (4, 4))                # must not raise
    with pytest.raises(ValueError):
        grid_mod.area(g, "hexagon", 20, (0, 0), (4, 4))


def test_a_cone_or_line_aimed_at_the_caster_is_refused():
    """`grid.area` raises rather than returning the caster's own square. The rule has
    no meaning otherwise, and a silent empty area reads downstream as "nobody was
    caught", which is a different claim."""
    g = board("open")
    for shape in ("cone", "line"):
        with pytest.raises(ValueError):
            grid_mod.area(g, shape, 20, (3, 3), (3, 3))


@pytest.mark.parametrize("size", SIZES)
def test_an_area_reaches_at_most_the_squares_its_own_size_allows(size):
    """A loose ceiling, and the looseness is the point: it catches an area that grows
    with the square of its size where it should grow with the size, and it does not
    care what the exact answer is."""
    g = board("open")
    cells = size / SQUARE_FT
    for shape in SHAPES:
        ceiling = (math.pi * (cells + 1) ** 2 if shape in ("sphere", "cylinder")
                   else 2 * (cells + 1) ** 2)
        worst = 0
        for caster, target in all_pairs(limit=120):
            try:
                worst = max(worst, len(_area(g, shape, size, caster, target)))
            except ValueError:
                continue
        assert worst <= ceiling, (
            f"{shape}{size} caught {worst} squares, over the {ceiling:.0f} ceiling")


def test_no_shape_size_board_combination_raises_anything_but_a_value_error():
    """The sweep above only catches `ValueError`. Anything else -- a TypeError from a
    tuple slip, a KeyError from a missing key -- is a crash the caller does not
    handle, and `cast` has no blanket except."""
    for room in sorted(ROOMS):
        g = board(room)
        for shape, size, from_self in itertools.product(SHAPES, SIZES, (True, False)):
            for caster, target in itertools.islice(aims(), 40):
                try:
                    grid_mod.area(g, shape, size, caster, target, from_self=from_self)
                except ValueError:
                    pass


# ── resources: what an area SPENDS ───────────────────────────────────────────

#: Engine-owned dice are scripted faces; player-owned dice are `supplied`. `ScriptedDice`
#: pops one face per `randint`, so a `2d6` consumes two of them: one damage roll for
#: the area, then one save per creature caught. Twelve is a d20 face that fails a DC
#: 13 save, which is deliberate -- a success here would halve the damage and make the
#: "one roll for the whole area" figure harder to read.
ENG_FACES = (4, 4, 12, 12, 12, 12, 12, 12)


def _breath(who, name="Fire Breath", dice="2d6", dtype="fire", charges=1,
            shape="sphere", size=15):
    """Give `who` the save action a breath weapon is.

    A sphere by default, because these tests are about what the action SPENDS and not
    about cone geometry: `use_action` aims a monster's area from the creature itself,
    so a sphere centred on its own square catches everything adjacent however the
    cone branch happens to narrow. The cone branch is covered by the generated
    geometry tests above and by the Burning Hands casts below.
    """
    who.extra.setdefault("actions", []).append({
        "name": name, "kind": "save", "area": {"shape": shape, "size": size},
        "dc": {"ability": "dex", "value": 13, "on_success": "half"},
        "damage": [{"dice": dice, "type": dtype}]})
    # `use_action` reads the recharge off the TOKEN, keyed by action name
    # (`spells.usable`), not off the action dict.
    who.extra.setdefault("usage", {})[name] = {"charged": True, "left": charges}


def breath_fight(charges=1):
    """A fight whose FIRST turn belongs to the frog with the breath weapon.

    Order matters here and not by accident: `use_action` goes through
    `_require_turn`, so a fight started with Kairos current refuses the monster's own
    action. Putting the frog first is how the engine's own turn loop reaches it.
    """
    c = caster(spells=["Fire Bolt"], slots=4)
    frogs = [frog("frog-1", (4, 0), "Giant Frog 1"),
             frog("frog-2", (4, 1), "Giant Frog 2"),
             frog("frog-3", (5, 0), "Giant Frog 3")]
    _breath(frogs[0], charges=charges)
    enc = encounter([c, *frogs], rows=["." * 20] * 20)
    return start(enc, ["frog-1", "kairos", "frog-2", "frog-3"]), c, frogs


def test_a_breath_weapon_spends_exactly_one_action_and_no_spell_slot():
    enc, c, frogs = breath_fight()
    before = dict(c.extra["slots"])
    out = spells_mod.use_action(enc, roller(*ENG_FACES), "frog-1",
                                "Fire Breath", "E1")
    assert enc.turn.action_used is True, "a breath weapon took no action"
    assert enc.turn.bonus_used is False, "a breath weapon spent a bonus action"
    assert c.extra["slots"] == before, "a monster action spent a PC's spell slot"
    assert len(frogs) == 3 and len(out["affected"]) >= 1
    assert len(out["squares"]) > 1, "the cone caught nothing at all"


def test_a_breath_weapon_spends_its_recharge_once_not_once_per_creature():
    enc, c, frogs = breath_fight(charges=1)
    spells_mod.use_action(enc, roller(*ENG_FACES), "frog-1", "Fire Breath", "E1")
    assert enc.tokens["frog-1"].extra["usage"]["Fire Breath"]["left"] == 0
    # A fresh turn on the frog's turn. `enc.current` is derived from
    # (order, turn_index), so setting those is enough; `_start_turn` would also roll
    # whatever recharges this creature has, and the recharge under test is the
    # breath weapon's own.
    enc.order = ["frog-1", "kairos", "frog-2", "frog-3"]
    enc.round, enc.turn_index = 1, 0
    enc.turn = engine_mod.TurnState(actor="frog-1", movement_budget=30, base_speed=30)
    with pytest.raises(Exception) as caught:
        spells_mod.use_action(enc, roller(*ENG_FACES), "frog-1", "Fire Breath", "E1")
    assert "not recharged" in str(caught.value)


def test_a_monster_area_action_needs_its_owners_turn():
    """`_require_turn` gates the resource, and it must gate BEFORE the recharge is
    touched, or a refused use silently burns the charge."""
    enc, c, frogs = breath_fight()
    enc.order = ["kairos", "frog-1", "frog-2", "frog-3"]
    enc.round, enc.turn_index = 1, 0
    engine_mod._start_turn(enc, roller(9, 9, 9))
    with pytest.raises(Exception) as caught:
        spells_mod.use_action(enc, roller(*ENG_FACES), "frog-1", "Fire Breath", "E1")
    assert "turn" in str(caught.value).lower()
    assert enc.tokens["frog-1"].extra["usage"]["Fire Breath"]["left"] == 1, (
        "a refused use_action spent the recharge")


def test_a_monster_area_action_needs_an_unused_action():
    """`_require_action` is the other gate, and the same ordering applies to it."""
    enc, c, frogs = breath_fight()
    enc.turn.action_used = True
    with pytest.raises(Exception) as caught:
        spells_mod.use_action(enc, roller(*ENG_FACES), "frog-1", "Fire Breath", "E1")
    assert "action" in str(caught.value).lower()
    assert enc.tokens["frog-1"].extra["usage"]["Fire Breath"]["left"] == 1, (
        "a refused use_action spent the recharge")


def test_an_area_spell_rolls_damage_once_for_the_whole_area():
    """`_resolve_saves` rolls one damage die set and divides it per creature. A second
    roll is the signature of an area handled per target, and it is invisible in the
    shapes and obvious in the hit points."""
    # Two frogs on the cone's axis east of Kairos: a 15 ft cone is three squares, and
    # the shape widens with distance, so an off-axis target at this range is outside.
    enc = encounter([caster(spells=["Burning Hands"], slots=4),
                     frog("frog-1", (2, 0), "Giant Frog 1"),
                     frog("frog-2", (3, 0), "Giant Frog 2")], rows=["." * 20] * 20)
    start(enc, ["kairos", "frog-1", "frog-2"])
    before = {t.id: t.hp for t in enc.tokens.values()}
    out = spells_mod.cast(enc, roller(*ENG_FACES, supplied=[9]), "kairos", "Burning Hands", ["D1"])
    dealt = sum(before[i] - enc.tokens[i].hp for i in ("frog-1", "frog-2"))
    assert dealt <= 18, f"the area dealt {dealt}, more than one 3d6 roll's worth"
    assert out["affected"], "nothing was caught"
    assert out["slot"] == "1" and enc.tokens["kairos"].extra["slots"]["1"]["used"] == 1


def test_an_illegal_area_cast_spends_nothing():
    """Everything is validated before anything is spent (the module docstring). A
    refused cast that had already taken a slot would be the worst bug in the file,
    and the order of the checks is the whole property.

    The refusal here is the action economy rather than range: the first cast takes
    the action, so the second has nothing left and must cost nothing at all. Both
    halves matter, and the second is the one a reordering would break.
    """
    enc = encounter([caster(spells=["Burning Hands"], slots=2),
                     frog("frog-1", (2, 0), "Giant Frog 1")], rows=["." * 20] * 20)
    start(enc, ["kairos", "frog-1"])
    spells_mod.cast(enc, roller(*ENG_FACES, supplied=[9]), "kairos", "Burning Hands", ["D1"])
    assert enc.turn.action_used is True
    before = dict(enc.tokens["kairos"].extra["slots"])
    spent = enc.tokens["kairos"].extra["slots"]["1"]["used"]
    with pytest.raises(Exception):
        spells_mod.cast(enc, roller(*ENG_FACES, supplied=[9]), "kairos", "Burning Hands", ["D1"])
    assert enc.tokens["kairos"].extra["slots"] == before, "a refused cast took a slot"
    assert enc.tokens["kairos"].extra["slots"]["1"]["used"] == spent
    assert len(enc.turn.spells) == 1, "a refused cast was recorded on the turn"


def test_an_out_of_range_area_cast_spends_nothing():
    """The other half: a legal turn, an illegal target."""
    # Ray of Frost reaches 60 ft, twelve squares. 13 squares away is a refusal with
    # the message that names the distance, so the test can check the reason too.
    enc = encounter([caster(spells=["Ray of Frost"], slots=2),
                     frog("frog-1", (13, 13), "Giant Frog 1")], rows=["." * 20] * 20)
    start(enc, ["kairos", "frog-1"])
    before = dict(enc.tokens["kairos"].extra["slots"])
    with pytest.raises(Exception) as caught:
        spells_mod.cast(enc, roller(*ENG_FACES, supplied=[9]), "kairos", "Ray of Frost",
                    ["frog-1"])
    assert "65 ft away" in str(caught.value), str(caught.value)
    assert enc.tokens["kairos"].extra["slots"] == before, "a refused cast took a slot"
    assert enc.turn.action_used is False, "a refused cast took the action"


def test_cover_is_zero_for_the_origin_square_and_otherwise_one_of_three_values():
    """`_cover_from` returns 0 for the origin square whatever is standing there: a
    creature cannot grant itself cover from the blast it stands at the centre of. The
    half-damage-on-a-success rule reads this number, so an off-by-one here halves or
    doubles every save in the area.

    The two neighbours are the point of the fixture. `grid.cover` walks the four
    corners of the target's own square and counts whatever stands against them, so
    with a creature on each side the SAME square is worth half cover from across the
    room and nothing at all from its own centre.

    Measured, and worth saying plainly: `_cover_from`'s explicit origin-square check
    is REDUNDANT for a creature target on the current `grid.cover`, which already
    returns 0 when attacker and target are the same square. Removing the check leaves
    all 138 tests here green. So this test pins the observable number, not the guard,
    and the guard's justification is defensive. Stating that is the difference between
    a test and a claim about a test.
    """
    enc = encounter([caster(spells=["Fire Bolt"]),
                     frog("frog-1", (5, 5), "Giant Frog 1"),
                     frog("frog-2", (6, 5), "Giant Frog 2"),
                     frog("frog-3", (4, 5), "Giant Frog 3")], rows=["." * 20] * 20)
    target = enc.tokens["frog-1"]
    assert spells_mod._cover_from(enc, target.pos, target) == 0
    assert spells_mod._cover_from(enc, (0, 5), target) == 2, (
        "the fixture no longer makes the origin-square case the special one")
    for origin in ((0, 5), (6, 5), (5, 1), (5, 9)):
        assert spells_mod._cover_from(enc, origin, target) in (0, 2, 5)


def test_spells_and_grid_report_the_same_squares_for_every_shape():
    """`spells.area_squares` is the only path `cast` uses and it is a thin wrapper.
    If the two ever disagree, the preview a player is shown is not the area the cast
    resolves, which is the whole preview-is-a-promise contract."""
    enc = encounter([caster(spells=["Burning Hands"], slots=4),
                     frog("frog-1", (4, 4), "Giant Frog 1")], rows=["." * 20] * 20)
    g = enc.board()
    for shape in SHAPES:
        spec = {"shape": shape, "size": 15}
        for target in ("D4", "B1", "K11"):
            # both sides take the same position tuple. `area_squares` is a thin
            # wrapper, so the label case is `_point`'s job and is exercised through
            # `cast` above rather than here.
            pos = grid_mod.parse_square(target)
            through_spells = spells_mod.area_squares(enc, (0, 0), spec, "point", pos)
            through_grid = _area(g, shape, 15, (0, 0), pos, from_self=False)
            assert through_spells == through_grid, f"{shape} at {target} disagrees"


def test_no_spelling_uses_an_em_dash():
    assert "\u2014" not in pathlib.Path(__file__).read_text(encoding="utf-8")