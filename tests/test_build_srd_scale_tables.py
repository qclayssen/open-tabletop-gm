import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "systems" / "dnd5e"))
import build_srd  # noqa: E402

SCALE = {"type": "ScaleValue", "title": "Rage Damage",
         "configuration": {"type": "number", "identifier": "rage-damage",
                           "scale": {"1": {"value": 2}, "9": {"value": 3}}}}


def test_advancement_as_list():
    doc = {"system": {"advancement": [SCALE, {"type": "HitPoints"}]}}
    assert build_srd._parse_scale_tables(doc)["rage-damage"] == {"1": "+2", "9": "+3"}


def test_advancement_keyed_by_id():
    doc = {"system": {"advancement": {"abc123": SCALE, "def456": {"type": "HitPoints"}}}}
    assert build_srd._parse_scale_tables(doc)["rage-damage"] == {"1": "+2", "9": "+3"}


def test_advancement_missing_or_junk():
    assert build_srd._parse_scale_tables({"system": {}}) == {}
    assert build_srd._parse_scale_tables({"system": {"advancement": ["junk"]}}) == {}
