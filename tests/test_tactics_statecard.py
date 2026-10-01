"""Named landmarks and the state card: addressable map features, engine feet,
cover and line of sight from the actor, and no leak of unseen creatures."""
from __future__ import annotations

import json

from tests.tactics_fixtures import encounter, goblin, kairos, start
from tests.test_tactics_cli import begin, camp, run  # noqa: F401  (camp is a fixture)
from tactics import maps, statecard

ROWS = ["..#...",
        "..#...",
        "..#...",
        "....o.",
        "......"]


def spec(features):
    return {"name": "t", "width": 6, "height": 5, "features": features}


def test_unnamed_features_get_type_index_handles():
    m = maps.compile_map(spec([{"type": "feature", "x": 1, "y": 1},
                               {"type": "wall", "x": 3, "y": 0},
                               {"type": "feature", "x": 4, "y": 2, "w": 2}]))
    names = [(l["name"], l["squares"]) for l in m["meta"]["landmarks"]]
    assert names == [("feature-1", ["B2"]), ("wall-1", ["D1"]), ("feature-2", ["E3", "F3"])]


def test_explicit_names_are_slugged_and_unique():
    m = maps.compile_map(spec([{"type": "feature", "x": 0, "y": 0, "name": "North Door"},
                               {"type": "feature", "x": 1, "y": 0, "name": "north-door"}]))
    assert [l["name"] for l in m["meta"]["landmarks"]] == ["north-door", "north-door-2"]


def test_painted_over_feature_keeps_counters_stable():
    m = maps.compile_map(spec([{"type": "feature", "x": 0, "y": 0},
                               {"type": "floor", "x": 0, "y": 0},
                               {"type": "feature", "x": 2, "y": 0}]))
    assert [l["name"] for l in m["meta"]["landmarks"]] == ["floor-1", "feature-2"]


def test_every_installed_map_still_compiles_with_landmarks():
    for name in maps.available():
        marks = maps.load(name)["meta"]["landmarks"]
        assert len({l["name"] for l in marks}) == len(marks), name


def fight(gob=(4, 0), extra=None):
    enc = start(encounter([kairos(pos=(0, 0)), goblin("goblin-1", gob)], rows=ROWS),
                ["kairos", "goblin-1"])
    enc.meta.update(maps.compile_map(spec([{"type": "feature", "x": 4, "y": 3, "name": "altar"},
                                           {"type": "feature", "x": 0, "y": 4, "h": 1, "w": 2}]))["meta"])
    return enc


def test_card_distances_match_the_grid_and_landmarks_are_named():
    enc = fight(gob=(3, 4))
    card = statecard.statecard(enc, "kairos")
    gob = next(c for c in card["creatures"] if c["id"] == "goblin-1")
    assert gob["distance_ft"] == enc.board().distance((0, 0), (3, 4)) == 20
    assert gob["square"] == "D5" and gob["hp"] == enc.tokens["goblin-1"].hp
    altar = next(m for m in card["landmarks"] if m["name"] == "altar")
    assert altar["squares"] == ["E4"] and altar["distance_ft"] == 20
    assert "feature-2" in [m["name"] for m in card["landmarks"]]
    assert "North is up" in card["text"] and "altar" in card["text"]
    assert card["creatures"][0]["id"] == "kairos"


def test_card_reports_line_of_sight_from_the_actor():
    enc = fight(gob=(4, 0))                       # behind the wall on column C
    card = statecard.statecard(enc, "kairos")
    gob = next(c for c in card["creatures"] if c["id"] == "goblin-1")
    assert gob["sight"] == "no line of sight"


def test_players_view_hides_hidden_and_fogged_creatures():
    enc = fight(gob=(4, 0))
    gm = statecard.statecard(enc, "kairos")
    assert "goblin-1" in gm["text"]
    players = statecard.statecard(enc, "kairos", players=True)
    assert [c["id"] for c in players["creatures"]] == ["kairos"]
    assert "goblin" not in players["text"].lower()
    enc.meta["fog"] = "off"
    enc.tokens["goblin-1"].add_condition("hidden")
    assert "goblin-1" not in statecard.statecard(enc, "kairos", players=True)["text"]


def test_card_cli_command_text_and_json(camp, capsys):
    begin(capsys)
    code, out = run(capsys, "card", "kairos")
    assert code == 0 and out.startswith("State card, kairos at B7") and "North is up" in out
    assert "Giant Frog 1" in out and "ft" in out
    code, out = run(capsys, "card", "kairos", "--json")
    data = json.loads(out)["result"]
    assert data["actor"] == "kairos" and data["creatures"][0]["distance_ft"] == 0
