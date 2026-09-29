"""Map editor: painting terrain by clicking, and merging it back into the file.

The merge is the whole point. A map is rectangles painted in order, so the easy
implementation, append a rectangle per drag, produces a file that grows
forever and describes a fixed picture. These tests hold the merge to that: paint
over an existing rectangle and the result must be a *sane* features[], not a
pile, and the only thing allowed to change in `grid.rows` is terrain the GM
actually painted.

The load-bearing constraint from tests/test_map_images.py is repeated here on
purpose: the editor is allowed to change what a map file looks like, never what
`compile_map` produces for a map nobody painted on.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from tactics import mapeditor, maps
from tactics.grid import Grid

# A scratch map the tests paint on. Not committed to display/maps/ on purpose:
# `maps.available()` is what combat.py offers a GM, and a test fixture in that
# folder would show up in the GM's map list. Every test that writes goes through
# this spec, in memory or in tmp_path, and the shipped maps are only ever read.
BARE = {"name": "Scratch", "width": 10, "height": 8, "base": "floor"}
WATER = {"name": "Scratch", "width": 10, "height": 8, "base": "floor",
         "features": [{"type": "water", "x": 2, "y": 1, "w": 6, "h": 6}]}


def rows(spec):
    return maps.compile_map(spec)["grid"]["rows"]


def stroke(t, x, y, w=1, h=1):
    return {"type": t, "x": x, "y": y, "w": w, "h": h}


# ── the merge, without Flask ─────────────────────────────────────────────────

def test_a_stroke_becomes_a_rectangle_in_the_file():
    out = mapeditor.apply_strokes(dict(BARE), [stroke("wall", 3, 2, 2, 1)])
    assert {"type": "wall", "x": 3, "y": 2, "w": 2, "h": 1} in out["features"]


def test_painted_terrain_reaches_grid_rows():
    """The end-to-end claim: what the GM painted is what the engine reads."""
    out = mapeditor.apply_strokes(dict(BARE), [stroke("water", 1, 1, 3, 2)])
    assert Grid.from_dict(maps.compile_map(out)["grid"])
    assert rows(out)[1][1:4] == "~~~"
    assert rows(out)[1][0] == "."          # untouched


def test_a_map_with_no_features_key_works():
    """`features` is optional in the README, and blank.json has none at all."""
    assert "features" not in BARE
    out = mapeditor.apply_strokes(dict(BARE), [stroke("hazard", 0, 0)])
    assert out["features"] == [{"type": "hazard", "x": 0, "y": 0, "w": 1, "h": 1}]


def test_a_map_with_no_features_key_and_no_strokes_keeps_it_that_way():
    """Nothing painted means nothing written, the cover is empty, not [{}]."""
    out = mapeditor.apply_strokes(dict(BARE), [])
    assert out["features"] == []


# ── merge behaviour: the reason this module exists ───────────────────────────

def test_painting_over_a_rectangle_produces_a_disjoint_cover():
    """The failure mode this is built to avoid: a second water rect painted on
    top of the first, leaving features[] that only make sense as a diff.

    Note what is *not* claimed: that the cover is shorter than what it replaced.
    A solid water block with a hole punched in it needs four rectangles to
    describe disjointly, where the original needed one. Disjointness costs
    rectangles and buys the property that actually matters, see the next two
    tests, which is that the count is bounded by the picture rather than by the
    number of clicks.
    """
    out = mapeditor.apply_strokes(dict(WATER), [stroke("difficult", 4, 3, 2, 2)])
    # The water is now a frame around the difficult patch, not a solid block.
    assert rows(out)[2][2:8] == "~~~~~~"
    assert rows(out)[3][4:6] == ",,"


def test_painting_the_same_hole_ten_times_looks_like_painting_it_once():
    """A GM fiddling with one rock should not leave nine rectangles of rock."""
    spec = dict(WATER)
    once = mapeditor.apply_strokes(spec, [stroke("difficult", 4, 3, 2, 2)])
    many = spec
    for _ in range(10):
        many = mapeditor.apply_strokes(many, [stroke("difficult", 4, 3, 2, 2)])
    assert many["features"] == once["features"]


def test_painting_two_different_things_grows_by_two_not_by_a_history():
    """The bound that matters: N strokes on one map give a file whose size
    depends on the picture, not on N."""
    spec = dict(WATER)
    for i in range(6):
        spec = mapeditor.apply_strokes(spec, [stroke("hazard", i, 0)])
        spec = mapeditor.apply_strokes(spec, [stroke("difficult", i, 0)])
    assert len(spec["features"]) < 25
    assert Grid.from_dict(maps.compile_map(spec)["grid"])


def test_repeated_painting_of_the_same_terrain_does_not_grow_the_file():
    """Idempotence is the maintainability guarantee. A GM who re-saves, or
    paints the same water twice, must not make the file longer."""
    spec = dict(WATER)
    once = mapeditor.apply_strokes(spec, [stroke("difficult", 4, 3, 2, 2)])
    twice = mapeditor.apply_strokes(once, [stroke("difficult", 4, 3, 2, 2)])
    assert twice["features"] == once["features"]


def test_a_save_with_no_strokes_does_not_renormalise_the_file_either():
    """Opening the editor and hitting save must be a no-op on the rectangles."""
    spec = dict(WATER)
    assert mapeditor.apply_strokes(spec, [])["features"] == spec["features"]


def test_painting_back_over_a_wall_leaves_the_new_terrain_not_the_old():
    """Last rectangle wins, in the file and in the editor alike. Undoing a wall
    means painting the terrain that was underneath, which the GM can see on the
    board, not that the editor is keeping a stack of what was there before.
    Painting `floor` over a wall in the water gives floor, and the file says so."""
    spec = dict(WATER)
    there = mapeditor.apply_strokes(spec, [stroke("wall", 4, 3, 2, 2)])
    back = mapeditor.apply_strokes(there, [stroke("floor", 4, 3, 2, 2)])
    assert rows(back)[3] == "..~~..~~.."
    # The base terrain is the way back: repaint the water and the map is whole.
    whole = mapeditor.apply_strokes(there, [stroke("water", 4, 3, 2, 2)])
    assert rows(whole) == rows(spec)


def test_a_merged_map_never_has_overlapping_rectangles():
    """Why paint order stops mattering: the cover is a partition, so the engine
    cannot read it differently from the cell matrix the editor drew."""
    out = mapeditor.apply_strokes(dict(WATER), [stroke("wall", 3, 2, 2, 2),
                                                stroke("hazard", 7, 6, 2, 1)])
    seen = set()
    for f in out["features"]:
        for y in range(f["y"], f["y"] + f["h"]):
            for x in range(f["x"], f["x"] + f["w"]):
                assert (x, y) not in seen, f"rectangles overlap at {x},{y}: {out['features']}"
                seen.add((x, y))


def test_a_labelled_feature_keeps_exactly_one_label_after_a_merge():
    """Labels are a property of a region, not of a rectangle: splitting a
    labelled rectangle must not duplicate its label onto both halves."""
    spec = dict(BARE, features=[{"type": "feature", "x": 1, "y": 1, "w": 6, "h": 4,
                                 "label": "Rubble"}])
    out = mapeditor.apply_strokes(spec, [stroke("water", 3, 2, 1, 2)])
    labels = [f["label"] for f in out["features"] if f.get("label")]
    assert labels == ["Rubble"]
    assert maps.compile_map(out)["meta"]["labels"] == [
        {"text": "Rubble", "x": 1, "y": 1}]


def test_two_features_sharing_label_text_stay_two_labels():
    """detention-bog labels three separate patches "Reeds". Merging on the text
    alone would collapse them into one, which is a visible loss on the map."""
    spec = dict(BARE, features=[
        {"type": "difficult", "x": 0, "y": 0, "w": 2, "h": 2, "label": "Reeds"},
        {"type": "difficult", "x": 5, "y": 5, "w": 2, "h": 2, "label": "Reeds"}])
    out = mapeditor.apply_strokes(spec, [stroke("water", 8, 0, 2, 2)])
    assert len(maps.compile_map(out)["meta"]["labels"]) == 2


def test_base_terrain_cells_do_not_become_a_map_sized_rectangle():
    """Painting the base type is how a GM erases. Emitting a whole-map floor
    rect saying "floor" would be noise in a file a human maintains."""
    out = mapeditor.apply_strokes(dict(WATER), [stroke("floor", 0, 0, 10, 8)])
    assert rows(out) == ["." * 10] * 8
    assert out["features"] == []


def test_painting_the_base_under_a_feature_leaves_no_rectangle_behind():
    """The same rule where it is not the whole map: an erased patch is base
    again, and base needs no rectangle, so the water describes the hole by
    being four rectangles around it rather than three plus an eraser."""
    out = mapeditor.apply_strokes(dict(WATER), [stroke("floor", 3, 2, 2, 2)])
    assert rows(out)[2] == "..~..~~~.."
    assert all(f["type"] != "floor" for f in out["features"])
    assert out["features"] == [
        {"type": "water", "x": 2, "y": 1, "w": 6, "h": 1},
        {"type": "water", "x": 2, "y": 2, "w": 1, "h": 5},
        {"type": "water", "x": 5, "y": 2, "w": 3, "h": 5},
        {"type": "water", "x": 3, "y": 4, "w": 2, "h": 3},
    ]


def test_a_map_specific_terrain_type_can_be_painted():
    """`terrain` types are first-class in the palette, frog-pond's `finish`."""
    spec = dict(BARE, terrain={"finish": {"cost": 1, "blocks_sight": False,
                                          "cover": 0, "color": "feature"}})
    out = mapeditor.apply_strokes(spec, [stroke("finish", 8, 0, 1, 8)])
    assert maps.compile_map(out)["grid"]["legend"] == {"a": "finish"}
    assert rows(out)[0][8] == "a"


# ── validation: reject, do not clip, do not 500 ─────────────────────────────

def test_an_unknown_terrain_type_is_rejected_by_name():
    with pytest.raises(ValueError, match="unknown terrain 'lava'"):
        mapeditor.apply_strokes(dict(BARE), [stroke("lava", 1, 1)])


def test_a_rectangle_off_the_map_is_rejected_not_trimmed():
    with pytest.raises(ValueError, match="runs off the 10x8 map"):
        mapeditor.apply_strokes(dict(BARE), [stroke("wall", 8, 7, 4, 4)])


def test_a_rectangle_off_the_left_edge_is_rejected():
    with pytest.raises(ValueError, match="runs off"):
        mapeditor.apply_strokes(dict(BARE), [stroke("wall", -1, 2)])


def test_an_empty_rectangle_is_rejected():
    """A zero-width drag is a misclick, not a request for a 0x0 rectangle."""
    with pytest.raises(ValueError, match="no area"):
        mapeditor.apply_strokes(dict(BARE), [{"type": "wall", "x": 2, "y": 2, "w": 0, "h": 3}])


def test_a_malformed_stroke_is_rejected_with_a_readable_message():
    with pytest.raises(ValueError, match="needs a terrain type"):
        mapeditor.apply_strokes(dict(BARE), [{"x": 1, "y": 1}])


def test_a_rejected_stroke_leaves_the_spec_alone():
    spec = dict(WATER)
    with pytest.raises(ValueError):
        mapeditor.apply_strokes(spec, [stroke("lava", 1, 1)])
    assert spec == WATER            # the caller's dict is not mutated in place


# ── the shipped maps ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", maps.available())
def test_re_deriving_a_shipped_map_does_not_change_a_single_row(name):
    """The load-bearing constraint, on every real map in the folder. Painting
    nothing may reorganise features[]; it may not move a square."""
    spec = json.loads((maps.MAPS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    out = mapeditor.apply_strokes(spec, [])
    assert rows(out) == rows(spec)


@pytest.mark.parametrize("name", maps.available())
def test_every_label_on_a_shipped_map_survives_a_re_save(name):
    """Labels may move by a square, a label rides on the cells it covered, and
    the first rectangle of a region is not always its old top-left, but none
    may be lost or duplicated. A dropped label is a lost place name on the map.
    """
    spec = json.loads((maps.MAPS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    before = sorted(l["text"] for l in maps.compile_map(spec)["meta"]["labels"])
    after = sorted(l["text"] for l in maps.compile_map(mapeditor.apply_strokes(spec, []))
                   ["meta"]["labels"])
    assert after == before


@pytest.mark.parametrize("name", maps.available())
def test_a_shipped_map_stays_loadable_after_a_save(name):
    spec = json.loads((maps.MAPS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    out = mapeditor.apply_strokes(spec, [stroke("hazard", 0, 0)])
    assert Grid.from_dict(maps.compile_map(out)["grid"])


def test_repainting_a_shipped_maps_own_terrain_leaves_no_duplicates():
    """The case a GM hits on the first real edit: the water rect is repainted
    exactly as the file already describes it. Last-wins, so the lily pads under
    it go, the GM painted water there and gets water. What must not happen is
    a second copy of the water rectangle."""
    spec = json.loads((maps.MAPS_DIR / "frog-pond.json").read_text(encoding="utf-8"))
    out = mapeditor.apply_strokes(spec, [stroke("water", 3, 0, 14, 14)])
    water = [f for f in out["features"] if f["type"] == "water"]
    assert water == [{"type": "water", "x": 3, "y": 0, "w": 14, "h": 14}]
    # The lily pads the old water sat behind are gone, and the banks are not:
    # last-wins, and only the water was repainted.
    assert "difficult" not in [f["type"] for f in out["features"]]
    assert [f.get("label") for f in out["features"] if f.get("label")] == \
        ["Start bank", "Finish bank"]


def test_a_hand_written_map_gets_longer_on_its_first_save_and_then_stops():
    """The cost of disjointness, stated rather than hidden.

    detention-bog is written the way a human writes: one big water rectangle,
    then reeds stamped on top of it. That is 11 rectangles and it only works
    because later rectangles win. A disjoint cover cannot say "water, then
    reeds on top", it has to describe the water *around* each reed, so the
    first save through the editor makes the file longer, once.

    What matters is that it stops there. The cover is a function of the cells,
    so the second save is the same size as the first, and no number of further
    edits grows it again.
    """
    spec = json.loads((maps.MAPS_DIR / "detention-bog.json").read_text(encoding="utf-8"))
    hand_written = len(spec["features"])
    first = mapeditor.apply_strokes(spec, [stroke("wall", 0, 0, 2, 1)])
    assert len(first["features"]) > hand_written       # the honest cost
    again = mapeditor.apply_strokes(first, [stroke("wall", 0, 0, 2, 1)])
    assert len(again["features"]) == len(first["features"])
    # And the picture is untouched by any of it.
    assert rows(first)[0].startswith("##")
    assert Grid.from_dict(maps.compile_map(first)["grid"])


def test_find_refuses_a_path_rather_than_a_map_name():
    """This module writes, so it resolves names more narrowly than maps._find."""
    assert mapeditor.find("../../etc/passwd") is None
    assert mapeditor.find("/etc/passwd") is None
    assert mapeditor.find("") is None
    assert mapeditor.find("frog-pond").name == "frog-pond.json"


# ── writing to disk ──────────────────────────────────────────────────────────

def test_write_keeps_the_previous_file_as_a_bak(tmp_path):
    """The editor overwrites a file a GM hand-wrote, so that version has to be
    recoverable, the same promise state.save() makes for the encounter file."""
    path = tmp_path / "map.json"
    mapeditor.write(path, dict(BARE, features=[]))
    mapeditor.write(path, dict(BARE, features=[stroke("wall", 1, 1)]))
    assert json.loads(path.read_text(encoding="utf-8"))["features"]
    bak = json.loads((tmp_path / "map.json.bak").read_text(encoding="utf-8"))
    assert bak["features"] == []


def test_a_second_write_keeps_the_first_original_in_the_bak(tmp_path):
    """The .bak is the hand-written file. A second save must not replace it
    with the first save's output."""
    path = tmp_path / "map.json"
    path.write_text(json.dumps(dict(BARE, name="hand written")), encoding="utf-8")
    mapeditor.write(path, dict(BARE, features=[stroke("wall", 1, 1)]))
    mapeditor.write(path, dict(BARE, features=[stroke("water", 2, 2)]))
    bak = json.loads((tmp_path / "map.json.bak").read_text(encoding="utf-8"))
    assert bak["name"] == "hand written"


@pytest.mark.parametrize("bad", [["wall"], {"a": 1}, 5, None])
def test_a_stroke_type_that_is_not_a_string_is_a_value_error(bad):
    with pytest.raises(ValueError):
        mapeditor.apply_strokes(BARE, [{"type": bad, "x": 0, "y": 0, "w": 1, "h": 1}])


def test_write_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "map.json"
    mapeditor.write(path, dict(BARE))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["map.json"]


# ── the Flask routes ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_module():
    """The display app, imported as a module so its test client is usable.

    gm-display-app.py runs the server on import, so the port is redirected first
    and the SystemExit it raises when it cannot bind is caught, the same dance
    tests/test_map_images.py does.
    """
    saved = os.environ.get("GM_DISPLAY_PORT")
    os.environ["GM_DISPLAY_PORT"] = "5098"
    spec_ = importlib.util.spec_from_file_location(
        "gm_app", ROOT / "display" / "gm-display-app.py")
    module = importlib.util.module_from_spec(spec_)
    try:
        spec_.loader.exec_module(module)
    except SystemExit:
        pass
    yield module
    if saved is None:
        os.environ.pop("GM_DISPLAY_PORT", None)
    else:
        os.environ["GM_DISPLAY_PORT"] = saved


@pytest.fixture
def client(app_module, monkeypatch, tmp_path):
    """A test client whose MAPS_DIR is tmp_path, so no test writes a shipped map.

    Monkeypatching the constant is the point: the routes resolve names through
    mapeditor.find, so redirecting that one name is enough to keep
    display/maps/ read-only for the whole suite.
    """
    monkeypatch.setattr(mapeditor, "MAPS_DIR", tmp_path)
    return app_module.app.test_client()


@pytest.fixture
def scratch(client, tmp_path):
    (tmp_path / "scratch.json").write_text(json.dumps(BARE), encoding="utf-8")
    return tmp_path / "scratch.json"


def test_the_editor_page_loads(client, scratch):
    response = client.get("/maps/scratch/edit")
    assert response.status_code == 200
    assert b"MAP_EDITOR" in response.data
    assert b"mapseditor.js" in response.data


def test_the_editor_page_carries_the_map_to_paint_on(client, tmp_path):
    (tmp_path / "pond.json").write_text(json.dumps(WATER), encoding="utf-8")
    body = client.get("/maps/pond/edit").data.decode("utf-8")
    start = body.index("window.MAP_EDITOR = ") + len("window.MAP_EDITOR = ")
    state = json.loads(body[start:body.index(";</script>", start)])
    assert state["width"] == 10 and state["height"] == 8
    assert state["cells"][1][2] == "water"
    assert {p["type"] for p in state["palette"]} >= {"floor", "wall", "water"}
    assert state["has_features"] is True


def test_an_unknown_map_is_a_readable_404(client):
    response = client.get("/maps/definitely-not-here/edit")
    assert response.status_code == 404
    assert "No map" in response.get_json()["error"]


def test_a_save_round_trips_through_the_map_file(client, scratch):
    """POST rectangles -> the JSON contains them -> compile_map accepts it ->
    grid.rows show the terrain. All four, in that order, because each is a
    separate way this could have been wired wrong."""
    response = client.post("/maps/scratch/features",
                           json={"strokes": [stroke("water", 1, 1, 4, 3)]})
    assert response.status_code == 200
    assert response.get_json()["features"] == [
        {"type": "water", "x": 1, "y": 1, "w": 4, "h": 3}]

    spec = json.loads(scratch.read_text(encoding="utf-8"))
    assert spec["features"] == [{"type": "water", "x": 1, "y": 1, "w": 4, "h": 3}]

    compiled = maps.compile_map(spec)
    Grid.from_dict(compiled["grid"])
    assert compiled["grid"]["rows"][2] == ".~~~~....."   # 10 squares, 4 of water


def test_a_save_that_does_not_change_terrain_leaves_the_grid_alone(client, tmp_path):
    (tmp_path / "pond.json").write_text(json.dumps(WATER), encoding="utf-8")
    before = rows(WATER)
    response = client.post("/maps/pond/features", json={"strokes": [], "confirm": True})
    assert response.status_code == 200
    spec = json.loads((tmp_path / "pond.json").read_text(encoding="utf-8"))
    assert rows(spec) == before


def test_saving_over_a_map_with_features_asks_first(client, tmp_path):
    """Constraint 4. Ten minutes of painting is not something a stray click
    should cost, so the route refuses and says what it would have done."""
    (tmp_path / "pond.json").write_text(json.dumps(WATER), encoding="utf-8")
    response = client.post("/maps/pond/features", json={"strokes": [stroke("wall", 0, 0)]})
    assert response.status_code == 409
    assert "confirm" in response.get_json()["error"]
    # Refused means unchanged.
    assert json.loads((tmp_path / "pond.json").read_text(encoding="utf-8")) == WATER


def test_a_confirmed_save_keeps_a_bak(client, tmp_path):
    (tmp_path / "pond.json").write_text(json.dumps(WATER), encoding="utf-8")
    response = client.post("/maps/pond/features",
                           json={"strokes": [stroke("wall", 4, 3, 2, 2)], "confirm": True})
    assert response.status_code == 200
    assert response.get_json()["backup"] == "pond.json.bak"
    assert json.loads((tmp_path / "pond.json.bak").read_text(encoding="utf-8")) == WATER


def test_a_non_string_stroke_type_is_a_400(client, scratch):
    response = client.post("/maps/scratch/features",
                           json={"strokes": [{"type": ["wall"], "x": 0, "y": 0, "w": 1, "h": 1}]})
    assert response.status_code == 400


def test_saving_a_corrupt_map_is_a_400(client, tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    response = client.post("/maps/broken/features", json={"strokes": [stroke("wall", 0, 0)]})
    assert response.status_code == 400
    assert "broken.json" in response.get_json()["error"]


def test_a_bare_map_needs_no_confirm(client, scratch):
    """The gate is about overwriting work, not about having a features key. An
    empty features list is nothing to lose, so the first save just goes."""
    assert "features" not in json.loads(scratch.read_text(encoding="utf-8"))
    response = client.post("/maps/scratch/features", json={"strokes": [stroke("wall", 0, 0)]})
    assert response.status_code == 200
    assert response.get_json()["backup"] == "scratch.json.bak"


def test_a_save_that_merges_returns_the_merged_state(client, tmp_path):
    """The page re-renders from the response, so it must carry the file's own
    answer back, including the rectangle count, which is the visible proof
    that a stroke merged rather than piled up."""
    (tmp_path / "pond.json").write_text(json.dumps(WATER), encoding="utf-8")
    response = client.post("/maps/pond/features",
                           json={"strokes": [stroke("difficult", 4, 3, 2, 2)], "confirm": True})
    state = response.get_json()["state"]
    assert len(state["cells"][3]) == 10
    assert state["cells"][3][4] == "difficult"
    assert state["cells"][2][4] == "water"


def test_an_unknown_terrain_type_comes_back_as_a_readable_error(client, scratch):
    before = scratch.read_text(encoding="utf-8")
    response = client.post("/maps/scratch/features", json={"strokes": [stroke("lava", 1, 1)]})
    assert response.status_code == 400                      # not a 500
    assert "unknown terrain 'lava'" in response.get_json()["error"]
    assert scratch.read_text(encoding="utf-8") == before    # and nothing written


def test_an_off_map_rectangle_comes_back_as_a_readable_error(client, scratch):
    before = scratch.read_text(encoding="utf-8")
    response = client.post("/maps/scratch/features",
                           json={"strokes": [stroke("wall", 8, 7, 4, 4)]})
    assert response.status_code == 400
    assert "runs off the 10x8 map" in response.get_json()["error"]
    assert scratch.read_text(encoding="utf-8") == before


def test_a_body_that_is_not_a_list_of_strokes_is_rejected(client, scratch):
    response = client.post("/maps/scratch/features", json={"strokes": "not a list"})
    assert response.status_code == 400
    assert scratch.read_text(encoding="utf-8") == json.dumps(BARE)


def test_saving_to_an_unknown_map_is_a_404(client):
    response = client.post("/maps/nope/features", json={"strokes": []})
    assert response.status_code == 404


# ── the editor must not touch a map just by being opened ────────────────────

def test_opening_the_editor_does_not_modify_the_map(client, scratch):
    """A GET is a read. If loading the page rewrote features[], normalising
    them, say, then simply looking at a map would put it at risk."""
    before = scratch.read_text(encoding="utf-8")
    stat_before = scratch.stat().st_mtime_ns
    assert client.get("/maps/scratch/edit").status_code == 200
    assert scratch.read_text(encoding="utf-8") == before
    assert scratch.stat().st_mtime_ns == stat_before
    assert not (scratch.parent / "scratch.json.bak").exists()


def test_the_editor_leaves_every_shipped_map_byte_identical(app_module):
    """Opening the editor for each real map must not rewrite any of them.

    Uses the unredirected client on purpose: `client` points MAPS_DIR at
    tmp_path so the other tests cannot reach a real map, which means this one
    has to do the reaching to prove the GET is a read.
    """
    real = app_module.app.test_client()
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in maps.MAPS_DIR.glob("*.json")}
    for name in maps.available():
        assert real.get(f"/maps/{name}/edit").status_code == 200, name
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in maps.MAPS_DIR.glob("*.json")}
    assert after == before


def test_the_write_route_cannot_be_used_to_reach_outside_the_maps_folder(client, tmp_path):
    """The write path resolves names, never paths. maps._find accepts a path if
    it exists; this module does not, and the route inherits that.

    405 is Werkzeug refusing the method after normalising `..` out of the URL,
    which is the traversal guard working one layer earlier than find(), so it
    counts as refused.
    """
    for name in ["..", "../..", "..%2fsecret", "..\\secret", "%2e%2e"]:
        status = client.post(f"/maps/{name}/features", json={"strokes": []}).status_code
        assert status in (400, 404, 405), f"{name} was not refused: {status}"


def test_a_shipped_map_has_no_bak_left_behind_by_the_suite():
    assert list(maps.MAPS_DIR.glob("*.bak")) == []
