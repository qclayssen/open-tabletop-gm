"""D-15 prototype tests: fixture determinism and revision handling.

Each test names its mutant: delete the fixture module and every test here
fails at collection; break determinism and test_fixture_deterministic fails;
let stale revs win and test_stale_rev_never_wins fails.
"""
from __future__ import annotations

import copy
import json

from display.prototype_d15.fixture import apply_snapshot, build_fixture, replay_sequence


def _norm(fx: dict) -> str:
    return json.dumps(fx, sort_keys=True)


def test_fixture_deterministic():
    a = build_fixture(seed=415)
    b = build_fixture(seed=415)
    assert _norm(a) == _norm(b)
    assert len(a["tokens"]) == 20
    assert a["grid"]["width"] == 20 and a["grid"]["height"] == 20
    assert a["rev"] == 1
    assert "FIXTURE-ONLY" in a["rev_note"]


def test_token_labels_and_footprints():
    fx = build_fixture()
    by_id = {t["id"]: t for t in fx["tokens"]}
    assert by_id["kairos"]["label"] == "C3"
    ogre = by_id["ogre-1"]
    assert (ogre["width"], ogre["height"]) == (2, 2)
    assert ogre["x"] == 8 and ogre["y"] == 12


def test_fog_is_supplied_visibility():
    fx = build_fixture()
    assert fx["visibility"]["kairos"] is True
    assert fx["visibility"]["goblin-2"] is False
    assert set(fx["visibility"]) == {t["id"] for t in fx["tokens"]}


def test_replay_sequence_revs_increase():
    seq = replay_sequence()
    revs = [s["rev"] for s in seq]
    assert revs == sorted(revs) and len(set(revs)) == len(revs)
    assert seq[0]["op"] == "select"


def test_stale_rev_never_wins():
    state = build_fixture()
    state = apply_snapshot(state, {"rev": 4, "selection": "kairos"})
    assert state["selection"] == "kairos" and state["rev"] == 4
    stale = apply_snapshot(state, {"rev": 3, "selection": "ogre-1"})
    assert stale["selection"] == "kairos" and stale["rev"] == 4
    same = apply_snapshot(state, {"rev": 4, "selection": "ogre-1"})
    assert same["selection"] == "kairos"
    newer = apply_snapshot(state, {"rev": 5, "selection": "ogre-1"})
    assert newer["selection"] == "ogre-1" and newer["rev"] == 5


def test_snapshot_does_not_mutate_loser():
    state = build_fixture()
    before = copy.deepcopy(state)
    apply_snapshot(state, {"rev": 0, "selection": "ogre-1"})
    assert state == before
