"""test_map_to_lumina.py: guard the engine map -> Lumina level export.

Run: python3 -m pytest tests/test_map_to_lumina.py -q
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("map_to_lumina", ROOT / "scripts" / "map_to_lumina.py")
m2l = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m2l)


def _spec_map(**kw) -> dict:
    spec = {"name": "Test Room", "width": 10, "height": 8, "base": "floor", "features": []}
    spec.update(kw)
    return spec


def test_frog_pond_converts(tmp_path):
    spec = json.loads((ROOT / "display" / "maps" / "frog-pond.json").read_text(encoding="utf-8"))
    level, losses, stats = m2l.build_level(spec, "frog-pond", {"finish": "c"})
    assert level["format"] == "lumina-level" and level["version"] == 1
    assert (level["width"], level["depth"]) == (20, 14)
    assert len(level["tiles"]) == 14 and all(len(r) == 20 for r in level["tiles"])
    assert len(level["heights"]) == 14
    # water beds sunken, everything else flat or raised
    assert set("".join(level["heights"])) <= set("023")
    # spawn is the first spawn square, centred
    assert level["spawn"] == {"x": 1.5, "z": 6.5, "facing": "down"}
    # frog-named spawns group into one frog critters object, the rest are NPCs
    kinds = [(o["type"], o.get("kind") or o.get("name")) for o in level["objects"]
             if o["type"] in ("npc", "critters")]
    assert ("critters", "frog") in kinds
    assert sum(1 for t, _ in kinds if t == "npc") == 2  # Juno + Theodric
    # every marker stands on walkable ground (pads stamped under water spawns)
    for o in level["objects"]:
        if o["type"] in ("npc", "signpost"):
            ch = level["tiles"][int(o["z"])][int(o["x"])]
            assert level["legend"][ch].get("walkable"), (o, ch)
    assert any("spawn" in loss for loss in losses)


def test_unknown_terrain_refused():
    spec = _spec_map(terrain={"lava": {"cost": 1}}, features=[{"type": "lava", "x": 0, "y": 0}])
    with pytest.raises(m2l.Refused, match="lava"):
        m2l.build_level(spec, "test-room")


def test_custom_terrain_flag():
    spec = _spec_map(terrain={"finish": {"cost": 1}}, features=[{"type": "finish", "x": 0, "y": 0}])
    level, _, _ = m2l.build_level(spec, "test-room", {"finish": "c"})
    assert level["tiles"][0][0] == "c"


def test_redefined_builtin_needs_flag():
    from tactics.grid import TERRAIN

    assert TERRAIN["water"]["cost"] == 2
    spec = _spec_map(terrain={"water": {"cost": 99}},
                     features=[{"type": "water", "x": 0, "y": 0}])
    with pytest.raises(m2l.Refused, match="redefined"):
        m2l.build_level(spec, "test-room")


def test_small_map_padded_to_minimum():
    level, _, stats = m2l.build_level(_spec_map(), "test-room")
    assert (level["width"], level["depth"]) == (10, 8)  # already >= 8
    tiny = _spec_map(width=5, height=5)
    level, _, _ = m2l.build_level(tiny, "tiny")
    assert (level["width"], level["depth"]) == (8, 8)
    assert level["tiles"][0] == "T" * 8


def test_output_serializes_as_json(tmp_path):
    spec = json.loads((ROOT / "display" / "maps" / "frog-pond.json").read_text(encoding="utf-8"))
    level, _, _ = m2l.build_level(spec, "frog-pond", {"finish": "c"})
    text = m2l.serialize_level(level)
    back = json.loads(text)
    assert back["tiles"] == level["tiles"] and back["heights"] == level["heights"]
