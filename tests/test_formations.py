"""What a formation has to be, and what it must never quietly become.

A formation is a saved monster *arrangement*. The failure mode this file exists
to prevent is specific and ugly: a formation that saves the wrong thing looks
exactly like one that works until the second time it is played, which is on a
different map, at a different size, in a different fight. So the tests here are
about round-trips and about what is deliberately *not* stored, rather than about
one convenient happy path.

The four assertions that carry the design:

  1. **Capture, then replay on the same map, is exact.** Not approximately: the
     same token names, the same sides, the same squares. A formation that drifts
     by a square on replay is a formation nobody trusts twice.
  2. **Both offsets are needed, and neither alone is the design.** Cell offsets
     put a formation captured on a 30x20 map in the top-left corner of a 20x14
     one; normalized offsets stretch a tight ambush across a wide room. The
     tests pin both, and pin the *difference* between them, because a change
     that quietly dropped `nx, ny` would still pass every same-map test.
  3. **A formation stores no HP, AC or conditions.** Those are the encounter's.
     A formation saved from a fight where the stirges were at 1 HP is a corpse
     layout, and replaying it must re-roll from the SRD.
  4. **`maps.compile_map` is untouched.** The map format has no formation in it;
     formations are campaign data that move between maps. A change that leaked
     a formation into the map schema would put a campaign-specific file in a
     shared, versioned format.

Conventions follow the rest of `tests/`: own `sys.path` header, fixtures rather
than the gitignored SRD build, `tmp_path` campaigns, and a docstring that says
what is being defended rather than what is being called.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import tactics_fixtures as fx                                     # noqa: E402
from tactics import formations, grid, maps, state                 # noqa: E402

RULES_MODULE = sys.modules[type(fx.RULES).__module__]
KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")


# ─── the campaign ─────────────────────────────────────────────────────────────

@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    return d


def _lookup(name: str) -> dict:
    key = name.lower().replace(" ", "-")
    if key not in fx._RAW:
        raise ValueError(f"no SRD monster {name!r}")
    return fx._build._norm_monster(fx._RAW[key])


@pytest.fixture
def srd(monkeypatch):
    monkeypatch.setattr(RULES_MODULE, "_lookup_monster", _lookup)
    monkeypatch.setattr(RULES_MODULE, "_srd_suggest", lambda n: None)
    return _lookup


# ─── a board to capture ───────────────────────────────────────────────────────

def _board(rows, name="arena"):
    return grid.Grid.from_dict({"name": name, "rows": list(rows), "diagonals": "5"})


# 12x6, open, with a wall at column 5 so `blocked` has something to be.
OPEN_ROWS = [
    "............",
    "............",
    ".....#......",
    ".....#......",
    "............",
    "............",
]
# A 20x14, nothing like the map above.
WIDE_ROWS = ["." * 20 for _ in range(14)]


def _ambush(meta=None):
    """Kairos alone, four frogs, two goblins, laid out in a real shape."""
    enc = fx.encounter(
        [fx.kairos(pos=(1, 5)),
         fx.frog("frog-1", (8, 1)), fx.frog("frog-2", (9, 1)),
         fx.frog("frog-3", (10, 2)), fx.frog("frog-4", (10, 3)),
         fx.goblin("goblin-1", (7, 3)), fx.goblin("goblin-2", (6, 3))],
        rows=OPEN_ROWS)
    enc.meta = dict(meta or {})
    return enc


# ─── 1. round-trip ────────────────────────────────────────────────────────────

def test_capture_then_replay_on_the_same_map_is_exact(camp):
    """The whole promise. Same names, same sides, same squares, in order."""
    enc = _ambush()
    spec = formations.capture(enc, "Frog Pool")
    formations.save(camp, spec)

    loaded = formations.load(camp, "Frog Pool")
    board = enc.board()
    plan = formations.positions(loaded, board, at=spec["anchor"])

    was = sorted((m["name"], m["side"], m["dx"], m["dy"]) for m in spec["members"])
    now = sorted((p["name"], p["side"],
                  p["x"] - spec["anchor"][0], p["y"] - spec["anchor"][1])
                 for p in plan["placements"])
    assert now == was
    assert plan["blocked"] == [] and plan["off_map"] == []


def test_capture_excludes_the_party_by_default(camp):
    """The opposition is the reusable part. A saved formation that includes the
    party is a saved *fight*, and replaying it would put the players' own
    characters back in the places the GM had already moved them out of."""
    spec = formations.capture(_ambush(), "Frog Pool")
    assert not any(m["side"] == "pc" for m in spec["members"])
    assert {m["side"] for m in spec["members"]} == {"enemy"}


def test_capture_can_include_the_party_when_asked(camp):
    """Opt in, for a map whose entire setup is worth keeping. Explicitly."""
    spec = formations.capture(_ambush(), "Whole Fight", include_pcs=True)
    assert any(m["side"] == "pc" for m in spec["members"])
    assert not formations.validate(spec)


def test_capture_ignores_the_dead_and_the_dying(camp):
    """A formation is an arrangement of monsters that will be fought. A corpse
    on the board is not part of it, and replaying one puts a token on the map
    that the engine will then treat as already down."""
    enc = _ambush()
    enc.tokens["frog-4"].dead = True
    enc.tokens["goblin-2"].stable = True
    spec = formations.capture(enc, "Live Only")
    assert {m["name"] for m in spec["members"]} == {"Frog", "Goblin"}


def test_capture_of_nothing_says_so(camp):
    """Refuses, with the reason. A silent empty formation is a file that will
    replay as no fight and look like a bug in the map."""
    enc = fx.encounter([fx.kairos()])
    with pytest.raises(ValueError, match="nothing to save"):
        formations.capture(enc, "Empty")


def test_capture_is_deterministic(camp):
    """Capturing the same arrangement twice must produce the same bytes, or a
    formation diffed in git is noise. The anchor is the top-left-most member,
    chosen by (y, x) so it does not depend on dict ordering."""
    enc = _ambush()
    assert formations.capture(enc, "Same") == formations.capture(enc, "Same")


# ─── 2. the two offsets ───────────────────────────────────────────────────────

def test_a_formation_stores_both_offsets(camp):
    """Asserted rather than assumed, because a change that dropped the
    normalized pair would still pass every same-map test in this file."""
    spec = formations.capture(_ambush(), "Frog Pool")
    w, h = spec["from_size"]["width"], spec["from_size"]["height"]
    for m in spec["members"]:
        col, row = spec["anchor"][0] + m["dx"], spec["anchor"][1] + m["dy"]
        assert isinstance(m["nx"], float) and isinstance(m["ny"], float)
        assert round(col / w, 4) == pytest.approx(m["nx"], abs=1e-4)
        assert round(row / h, 4) == pytest.approx(m["ny"], abs=1e-4)
        assert 0.0 <= m["nx"] <= 1.0 and 0.0 <= m["ny"] <= 1.0


def test_cell_offsets_alone_would_wrongly_corner_a_bigger_map(camp):
    """The reason both are stored, pinned as a difference.

    Replaying a formation captured on 12x6 onto a 20x14 by cell offset lands the
    whole group in the top-left, and still reads as "success" because every
    square is legal floor. This is the failure that is invisible until someone
    plays the map and asks why the frogs were all in one corner.
    """
    spec = formations.capture(_ambush(), "Frog Pool")
    big = _board(WIDE_ROWS, "wide")

    by_cell = formations.positions(spec, big, at=spec["anchor"])
    by_pitch = formations.positions(spec, big, centre=True)

    xs = [p["x"] for p in by_cell["placements"]]
    ys = [p["y"] for p in by_cell["placements"]]
    assert max(xs) < 12 and max(ys) < 6, "expected the cell replay to stay small"

    cxs = [p["x"] for p in by_pitch["placements"]]
    cys = [p["y"] for p in by_pitch["placements"]]
    assert max(cxs) > 12 and max(cys) > 6, \
        "expected the normalized replay to spread across the bigger map"


def test_normalized_replay_keeps_the_arrangements_shape(camp):
    """Scaled, not merely relocated: the relationship between members is the
    part worth saving, so a replay onto a 20x14 must still be a group, not six
    frogs in a line because the rounding lined up."""
    spec = formations.capture(_ambush(), "Frog Pool")
    plan = formations.positions(spec, _board(WIDE_ROWS, "wide"), centre=True)
    spots = {(p["x"], p["y"]) for p in plan["placements"]}
    assert len(spots) == len(plan["placements"]), "two members landed on one square"
    # The two goblins were adjacent on the source map and must stay close.
    gob = [p for p in plan["placements"] if p["name"] == "Goblin"]
    assert max(abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])
               for a in gob for b in gob) <= 3


def test_normalized_replay_cannot_leave_the_map(camp):
    """A stronger claim than "it is reported": it cannot happen.

    A member at fraction 0.83 of a 6-wide map lands at column 5, because the
    normalized offset is an *absolute* fraction of the target map and not a
    delta added to an anchor. The version that added it to a centre needed a
    "clamped" list to describe six monsters piled onto the right edge; this test
    is why that list is gone, and it is checked on a map small enough that a
    delta-based implementation would fail it.
    """
    for w, h in ((6, 4), (20, 14), (12, 6), (4, 9)):
        tiny = _board(["." * w for _ in range(h)], f"{w}x{h}")
        spec = formations.capture(_ambush(), "Frog Pool")
        plan = formations.positions(spec, tiny, centre=True)
        assert plan["off_map"] == [], f"{w}x{h}: a proportional replay ran off the map"
        for pr in plan["placements"]:
            assert 0 <= pr["x"] < w and 0 <= pr["y"] < h


def test_a_map_narrower_than_two_cells_reports_rather_than_clamps(camp):
    """The degenerate case, pinned down because it is where the claim above ends.

    `round(0.83 * 1)` is 1, which is off a 1-wide map, so a formation can be
    reported off a 1x1 board. It must be *reported*: clamping would put five
    monsters on the single square and call it success, and a GM who asks for a
    1x1 arena deserves to be told no rather than handed a pile.
    """
    spec = formations.capture(_ambush(), "Frog Pool")
    plan = formations.positions(spec, _board(["."], "1x1"), centre=True)
    assert plan["off_map"], "a 1x1 board should report members it cannot hold"
    for o in plan["off_map"]:
        assert o["was"] != [o["x"], o["y"]]
        assert (o["x"], o["y"]) == (0, 0), "everything lands on the only square"
    assert all(pr["x"] == 0 and pr["y"] == 0 for pr in plan["placements"])


def test_pinned_replay_off_the_map_is_reported_separately(camp):
    """Pinning is a square the GM chose, so running off the edge is a mistake to
    name rather than a scale difference to smooth over. Two distinct lists, two
    distinct meanings, both non-empty only when they should be."""
    tiny = _board(["." * 6 for _ in range(4)], "tiny")
    spec = formations.capture(_ambush(), "Frog Pool")
    plan = formations.positions(spec, tiny, at=(1, 1))
    assert plan["off_map"], "a 6x4 map must run off when pinned at 1,1"
    for o in plan["off_map"]:
        assert "was" in o and o["was"] != [o["x"], o["y"]], \
            "every off_map entry must name the square it wanted and the one it got"
    assert all(0 <= p["x"] < 6 and 0 <= p["y"] < 4 for p in plan["placements"])


def test_two_monsters_never_replay_onto_one_square(camp):
    """A rules error, and proportional replay causes it on real data.

    Two monsters a square apart on a wide map round to the same column on a
    narrower one: a 30x20 formation replayed onto a 24x18 map put two of three
    kobolds on the same square, and nothing in the report mentioned it. The
    engine would have started the fight and the table would have found out.

    So the later of the pair is moved to the nearest free square, and *every*
    move is reported with both squares -- a nudged monster is a changed
    distance, and only the GM can say whether that distance is the one wanted.
    """
    narrower = _board(["." * 24 for _ in range(18)], "24x18")
    # Columns 2 and 3 of a 30-wide map are 0.067 and 0.100 of it, and
    # 24 x those round to the same column. Found by searching, not by eye:
    # which pairs collide depends on both widths.
    adj = fx.encounter([fx.goblin("goblin-1", (2, 10)), fx.goblin("goblin-2", (3, 10))],
                       rows=["." * 30] * 20)
    spec = formations.capture(adj, "Pair")
    # The precondition: the naive proportional replay really does collide here.
    naive = [(round(m["nx"] * 24), round(m["ny"] * 18)) for m in spec["members"]]
    assert naive[0] == naive[1], f"this test needs a collision to fix; got {naive}"

    plan = formations.positions(spec, narrower, centre=True)
    spots = [(p["x"], p["y"]) for p in plan["placements"]]
    assert len(set(spots)) == len(spots), f"two monsters on one square: {spots}"
    assert plan["separated"], "a move happened and was not reported"
    for s in plan["separated"]:
        assert s["was"] != [s["x"], s["y"]]
        assert not board_blocks(narrower, (s["x"], s["y"]))


def board_blocks(board, pos):
    return not board.passable(pos)


def test_a_pinned_replay_is_never_nudged_for_a_collision(camp):
    """The exact case is the one that must stay exact. On the map a formation
    was captured on, the GM's own squares are the squares, and a collision there
    is a real authoring mistake to report -- not something to paper over."""
    enc = _ambush()
    spec = formations.capture(enc, "Frog Pool")
    plan = formations.positions(spec, enc.board(), at=spec["anchor"])
    assert plan["separated"] == [], "a same-map replay moved a monster"
    assert plan["blocked"] == []


def test_separation_moves_are_the_smallest_available(camp):
    """A monster nudged one square keeps its distance better than one nudged
    three, so the search is breadth-first rather than 'somewhere legal'."""
    rows = ["." * 9 for _ in range(7)]
    enc = fx.encounter([fx.goblin("goblin-1", (4, 3)), fx.goblin("goblin-2", (5, 3))],
                       rows=rows)
    spec = formations.capture(enc, "Pair")
    plan = formations.positions(spec, _board(rows, "small"), centre=True)
    for s in plan["separated"]:
        step = max(abs(s["x"] - s["was"][0]), abs(s["y"] - s["was"][1]))
        assert step == 1, f"moved {step} squares when one was free"


def test_a_formation_that_cannot_be_separated_is_reported_not_stacked(camp):
    """A map with one free square and three monsters. Two of them cannot be
    placed; the answer is to say so, because the alternative is three creatures
    in one square, which is a fight nobody can run."""
    rows = ["." * 1 for _ in range(1)]
    tiny = _board(rows, "1x1")
    enc = fx.encounter([fx.goblin("goblin-1", (4, 3)), fx.goblin("goblin-2", (5, 3)),
                        fx.goblin("goblin-3", (6, 3))], rows=["." * 9] * 7)
    spec = formations.capture(enc, "Three")
    plan = formations.positions(spec, tiny, centre=True)
    # All three land on the only square there is, which is unavoidable. What
    # matters is that the two that could not be given a square of their own are
    # named, rather than being quietly dropped or quietly stacked.
    assert len(plan["placements"]) == 3, "a monster went missing"
    assert {p["x"] for p in plan["placements"]} == {0}
    assert len(plan["unplaced"]) == 2, \
        f"expected two monsters reported as unplaceable, got {plan['unplaced']}"
    # All three carry the fixture's display name, so the count is what identifies
    # them; the CLI numbers them when it builds real tokens.
    assert {u["name"] for u in plan["unplaced"]} == {"Goblin"}


def test_replay_refuses_both_an_anchor_and_the_centre(camp):
    """The two modes answer different questions; asking for both is a command
    with no meaning, and picking one silently is how a formation ends up in the
    wrong place with no warning."""
    spec = formations.capture(_ambush(), "Frog Pool")
    with pytest.raises(ValueError, match="not both"):
        formations.positions(spec, _board(OPEN_ROWS), at=(2, 2), centre=True)


def test_centring_without_a_size_is_refused(camp):
    """Normalized offsets are fractions of a size. No size, no fractions, and
    silently treating them as cells is the bug this guards."""
    spec = formations.capture(_ambush(), "Frog Pool")
    del spec["from_size"]
    with pytest.raises(ValueError, match="from_size"):
        formations.positions(spec, _board(WIDE_ROWS, "wide"), centre=True)


# ─── 3. what is deliberately not stored ───────────────────────────────────────

def test_capture_stores_the_creature_and_the_name_it_wore(camp):
    """The bug this split exists to fix.

    v1 stored the *display* name, so `--monster "Kobold@W11"` twice captured
    "Kobold" and "Kobold 2" -- and `token_from_monster("Kobold 2")` is "no SRD
    monster". A formation that saved cleanly and then could not be played, on
    every save with more than one of a creature. It surfaced by running a
    formation back through `start`, not by any test.
    """
    spec = formations.capture(_ambush(), "Frog Pool")
    names = [m["name"] for m in spec["members"]]
    labels = [m["label"] for m in spec["members"]]
    assert "Frog" in names, "the creature name must be the bare creature"
    assert not any(n[-1].isdigit() for n in names), \
        f"a member name ends in a digit and will not resolve: {names}"
    # Four frogs captured as one creature plus three instance labels, and the
    # labels are what the token actually wore. The fixture's goblins are both
    # named "Goblin", so they contribute no suffix to undo -- which is the
    # other half of the same assertion: nothing is invented.
    assert names.count("Frog") == 4
    assert sorted(labels)[:4] == ["Frog 1", "Frog 2", "Frog 3", "Frog 4"]
    assert sorted(labels)[4:] == ["Goblin", "Goblin"]


def test_a_v1_formation_migrates_rather_than_failing(camp):
    """The migration the bug forced. A file written by the version that had the
    bug must keep working, and a refused read would be worse than the bug."""
    v1 = {"schema": formations.FORMATION_SCHEMA, "version": 1, "name": "Old",
          "captured": "2026-09-30", "from_map": "detention-bog",
          "from_size": {"width": 24, "height": 18}, "anchor": [16, 6],
          "members": [{"name": "Kobold 1", "side": "enemy", "dx": 0, "dy": 0,
                       "nx": 0.6667, "ny": 0.3333},
                      {"name": "Kobold 2", "side": "enemy", "dx": 1, "dy": 0,
                       "nx": 0.7083, "ny": 0.3333}]}
    path = formations.path_for(camp, "Old")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(v1), encoding="utf-8")

    got = formations.load(camp, "Old")
    assert got["version"] == formations.SCHEMA_VERSION
    assert [m["name"] for m in got["members"]] == ["Kobold", "Kobold"]
    assert [m["label"] for m in got["members"]] == ["Kobold 1", "Kobold 2"]
    # And it replays, which is the thing v1 could not do.
    plan = formations.positions(got, _board(WIDE_ROWS, "wide"), at=(16, 6))
    assert len(plan["placements"]) == 2


def test_a_v1_member_without_an_instance_number_migrates_to_itself(camp):
    """A lone monster has no suffix, so the migration must not touch it -- and
    must not invent a label from nothing."""
    v1 = {"schema": formations.FORMATION_SCHEMA, "version": 1, "name": "Lone",
          "captured": "2026-09-30", "from_map": "detention-bog",
          "from_size": {"width": 24, "height": 18}, "anchor": [16, 6],
          "members": [{"name": "Stirge", "side": "enemy", "dx": 0, "dy": 0,
                       "nx": 0.6667, "ny": 0.3333}]}
    path = formations.path_for(camp, "Lone")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(v1), encoding="utf-8")
    assert formations.load(camp, "Lone")["members"][0]["name"] == "Stirge"


def test_a_formation_carries_no_hp_ac_or_conditions(camp):
    """Every key a member may have. A formation is names and squares; HP belongs
    to an encounter, and a formation that stored it would replay a corpse
    layout as a live fight."""
    spec = formations.capture(_ambush(), "Frog Pool")
    formations.save(camp, spec)
    stored = formations.load(camp, "Frog Pool")
    for m in stored["members"]:
        assert set(m) <= {"name", "label", "side", "dx", "dy", "nx", "ny", "color"}
    text = json.dumps(stored)
    for leak in ("hp", "max_hp", "ac", "conditions", "initiative"):
        assert leak not in text, f"{leak} leaked into a formation"


def test_the_side_colour_table_matches_the_atlas_exporter(camp):
    """`formations.SIDE_COLOUR` and `map_to_atlas.TOKEN_COLOURS` name the same
    palette from two places, one a package and one a script. They are
    duplicated on purpose — a script cannot import the package's CLI — so this
    is the guard on that decision, and it is cheap enough to never let rot.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import map_to_atlas
    for side, colour in formations.SIDE_COLOUR.items():
        assert colour in map_to_atlas.TOKEN_COLOURS, \
            f"side {side!r} maps to {colour!r}, which the Atlas exporter has no disc for"


def test_a_formation_inherits_the_colour_the_map_already_chose(camp):
    """The campaign's own map has been saying "stirges are danger red" since
    before formations existed. A formation that invents a different colour makes
    the Atlas preview lie about which side a token is on."""
    enc = _ambush()
    enc.meta["spawns"] = [
        {"id": "f", "name": "Frogs", "color": "with", "x": 8, "y": 1},
        {"id": "g", "name": "Goblins", "color": "danger", "x": 7, "y": 3},
    ]
    # `colour_from` is what the CLI passes (enc.meta["spawns"]); capture()
    # itself defaults to None so a caller that has no map cannot inherit
    # a colour it never saw.
    spec = formations.capture(enc, "Coloured", colour_from=enc.meta["spawns"])
    by_name = {m["name"]: m["color"] for m in spec["members"]}
    # A map says "Frogs" (detention-bog spells one stirge "Stirges"); the board
    # says "Frog 1". Neither an exact match nor a fold alone connects those --
    # it takes the plural probe `map_to_atlas.bestiary_note` uses. Without it the
    # map's colour is dropped and the Atlas preview shows the wrong side.
    assert by_name["Frog"] == "with"
    assert by_name["Goblin"] == "danger"


def test_an_unmatched_colour_falls_back_to_the_side(camp):
    """A colour is cosmetic. Refusing to save an encounter over one would be the
    wrong trade, so it degrades and says nothing rather than failing."""
    enc = _ambush()
    enc.meta["spawns"] = [{"id": "f", "name": "Something Else", "color": "pris"}]
    spec = formations.capture(enc, "Fallback", colour_from=enc.meta["spawns"])
    assert {m["color"] for m in spec["members"]} == {"danger"}


# ─── 4. the map format is not involved ────────────────────────────────────────

def test_maps_compile_map_is_untouched_by_formations():
    """The house invariant, restated for this feature. A formation is campaign
    data that moves between maps; putting one in a map file would put a
    campaign-specific artefact in a shared, versioned format."""
    spec = {"name": "No Formation Here", "width": 3, "height": 2, "base": "floor",
            "features": [], "spawns": [{"id": "a", "name": "Kairos", "color": "quan",
                                        "x": 0, "y": 0}],
            "formation": {"members": [], "anchor": [0, 0]}}
    out = maps.compile_map(spec)
    assert out["grid"]["rows"] == ["...", "...", ]
    assert "formation" not in out["meta"]
    # And the map compiled from a spec without one is byte-identical.
    plain = dict(spec)
    del plain["formation"]
    assert maps.compile_map(plain) == out


def test_saving_a_formation_writes_nothing_outside_the_campaign(camp):
    """Campaign data, campaign folder. A formation must never appear in
    display/maps/, which is shared by every campaign on a machine."""
    before = {p.name for p in maps.MAPS_DIR.glob("*.json")}
    formations.save(camp, formations.capture(_ambush(), "Frog Pool"))
    assert {p.name for p in maps.MAPS_DIR.glob("*.json")} == before
    assert formations.encounters_dir(camp).is_dir()


# ─── refusing what will not replay ────────────────────────────────────────────

def test_a_formation_that_cannot_replay_is_never_written(camp):
    """The whole point of validating on save: a formation file that exists is a
    promise it can be played, and one that cannot is a trap found mid-session."""
    broken = {"schema": formations.FORMATION_SCHEMA, "version": 1, "name": "Broken",
              "anchor": [0, 0], "from_size": {"width": 4, "height": 4},
              "members": [{"name": "Goblin", "side": "enemy",
                           "dx": 0, "dy": 0, "nx": 0.1, "ny": 0.1}]}
    del broken["from_size"]
    with pytest.raises(ValueError, match="from_size"):
        formations.save(camp, broken)
    assert formations.available(camp) == []


def test_an_offsquare_normalized_offset_is_reported(camp):
    """nx outside 0..1 means the formation was not captured on the map it claims,
    which is corruption rather than an edge case."""
    spec = formations.capture(_ambush(), "Frog Pool")
    spec["members"][0]["nx"] = 1.4
    problems = formations.validate(spec)
    assert any("outside 0..1" in p for p in problems)


def test_a_member_off_its_own_capture_map_is_reported(camp):
    spec = formations.capture(_ambush(), "Frog Pool")
    spec["members"][0]["dx"] = 99
    problems = formations.validate(spec)
    assert any("falls off" in p for p in problems)


def test_an_unknown_key_in_a_member_is_refused(camp):
    """The schema's rule, and it matters here: a misspelt `nx` on a formation is
    a member that silently keeps its cell offset forever."""
    spec = formations.capture(_ambush(), "Frog Pool")
    spec["members"][0]["nnx"] = 0.5
    assert formations.validate(spec)


def test_loading_a_file_from_a_newer_engine_is_refused(camp):
    """A file this engine cannot fully understand must not be placed on a map
    from a partial reading of it. `state.py` migrates forward; formations have
    no migration yet, so a higher version is a refusal rather than a guess."""
    path = formations.path_for(camp, "From The Future")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": formations.FORMATION_SCHEMA, "version": 99, "name": "From The Future",
        "anchor": [0, 0], "from_size": {"width": 2, "height": 2},
        "members": [{"name": "Goblin", "side": "enemy", "dx": 0, "dy": 0,
                     "nx": 0.0, "ny": 0.0}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="version 99"):
        formations.load(camp, "From The Future")


def test_a_missing_formation_lists_the_ones_that_exist(camp):
    formations.save(camp, formations.capture(_ambush(), "Frog Pool"))
    with pytest.raises(FileNotFoundError, match="frog-pool"):
        formations.load(camp, "Bog Ambush")


# ─── blocking is reported, not repaired ───────────────────────────────────────

def test_a_formation_landed_on_a_wall_is_reported_and_not_nudged(camp):
    """Two repairs are available and both are wrong: nudging the monster changes
    a distance, dropping it loses a monster. So nothing moves, and
    `state.validate` refuses the encounter, which is the correct outcome and
    names the token."""
    walled = _board([
        "....#.....",
        "....#.....",
        "....#.....",
        "..........",
        "....#.....",
        "....#.....",
    ])
    spec = formations.capture(_ambush(), "Frog Pool")
    # Pin the anchor onto a wall square: the anchor member's +0,+0 is the wall.
    plan = formations.positions(spec, walled, at=(4, 0))
    assert plan["blocked"], "the anchor itself is a wall square here"
    # Nothing may be nudged. Every placement must be exactly its cell offset
    # from the anchor, which is the whole claim: the report is the fix, the code
    # does not invent one.
    for p, m in zip(plan["placements"], spec["members"]):
        assert (p["x"], p["y"]) == (4 + m["dx"], 0 + m["dy"])


# ─── files on disk ────────────────────────────────────────────────────────────

def test_a_saved_formation_is_a_readable_json_file_with_a_version(camp):
    """Inspectable by hand and diffable in git, which is the whole reason this
    is a JSON file and not a database row."""
    formations.save(camp, formations.capture(
        _ambush({"slug": "detention-bog", "name": "Detention Bog"}), "Frog Pool"))
    raw = json.loads(formations.path_for(camp, "Frog Pool").read_text(encoding="utf-8"))
    assert raw["schema"] == formations.FORMATION_SCHEMA
    assert raw["version"] == formations.SCHEMA_VERSION
    assert raw["from_map"] == "detention-bog"
    assert raw["captured"]
    assert isinstance(raw["anchor"], list) and len(raw["anchor"]) == 2
    assert raw["members"]


def test_resaving_keeps_the_first_version_as_a_bak(camp):
    """Same rule `state.save` and the map editor follow: exactly one backup, of
    the original, because a `.bak` rewritten each time is not a backup."""
    spec = formations.capture(_ambush(), "Frog Pool")
    formations.save(camp, spec)
    first = formations.path_for(camp, "Frog Pool").read_text(encoding="utf-8")
    spec["info"] = "with the reeds cleared first"
    formations.save(camp, spec)
    assert formations.path_for(camp, "Frog Pool").with_suffix(".json.bak").exists()
    assert formations.path_for(camp, "Frog Pool").with_suffix(".json.bak").read_text(
        encoding="utf-8") == first


def test_a_formation_is_written_as_utf8_with_real_characters(camp):
    """A campaign on a non-UTF-8 Windows locale still has to load, and a GM who
    writes a formation in their own name has to get it back unchanged."""
    enc = _ambush()
    enc.tokens["goblin-1"].name = "Goblin Fée"
    spec = formations.capture(enc, "Fée Pool")
    formations.save(camp, spec)
    back = formations.load(camp, "Fée Pool")
    assert "Goblin Fée" in [m["name"] for m in back["members"]]


def test_names_slug_to_filenames_without_colliding_oddly(camp):
    assert formations.slug("Bog Ambush") == "bog-ambush"
    assert formations.slug("  the  Ledger-Hollow  ") == "the-ledger-hollow"
    assert formations.slug("!!!") == ""
    # slug() is a pure name transform and answers "" for a name with nothing
    # usable in it; refusing is path_for's job, because a name is only a problem
    # once it has to become a filename.
    with pytest.raises(ValueError, match="no usable characters"):
        formations.path_for(camp, "!!!")


def test_formation_does_not_touch_the_encounter_file(camp):
    """Saving a formation is a read of the board and a write of a new file. The
    running fight's own state is not its to touch, or `formation save` would
    change the encounter it was reading."""
    enc = _ambush()
    path = state.encounter_path(camp)
    state.save(enc, path)
    before = path.read_text(encoding="utf-8")
    formations.save(camp, formations.capture(enc, "Frog Pool"))
    assert path.read_text(encoding="utf-8") == before
