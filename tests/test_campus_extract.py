"""The campus extractor, against the real reference page and the real maps.

The parser is four regexes over a hand-maintained HTML file, which is exactly
the shape of thing that breaks quietly: a pattern that stops matching returns
*fewer* rows, not an error, and an empty campus looks like a valid empty campus.
So these tests assert the extracted counts against numbers read from the page
itself, and cross-check the battle maps against display/maps/*.json -- the
engine files, not the page, so a divergence shows up as a difference.
"""
from __future__ import annotations

import json
import sys

import pytest

from tests.tactics_fixtures import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import campus_extract as ce
from tactics import maps as engine_maps

# Read the JSON with the lossless reader by name: test_encoding_utf8.py exempts
# `utf8io.read_text` and nothing else, so reading it as `ce.read_text` (the same
# function, re-exported) is what that guard exists to catch.
from utf8io import read_text

REFERENCE = ce.REFERENCE


@pytest.fixture(scope="module")
def data():
    return ce.extract(REFERENCE)


# ─── the page itself ──────────────────────────────────────────────────────────

def test_page_is_local_and_offline():
    """The extractor parses a local file. If this ever needs a fetch, the whole
    premise of the script changes and the tests should say so."""
    assert REFERENCE.exists()
    html = read_text(REFERENCE)
    # Only the Google Fonts stylesheet is remote; no map art, no data files.
    remote = {u for u in ce.re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
              if "fonts.googleapis" not in u}
    assert remote == set(), f"unexpected remote references: {remote}"


def test_colleges_are_the_five_plus_central(data):
    assert data["colleges"] == {
        "lore": "Lorehold", "pris": "Prismari", "quan": "Quandrix",
        "silv": "Silverquill", "with": "Witherbloom", "cent": "Central",
    }


def test_every_place_is_extracted(data):
    """18 places, in 5 colleges. A regex that quietly stops matching would
    return a shorter list and still pass a "is it non-empty" check."""
    assert len(data["places"]) == 18
    assert {p["college"] for p in data["places"]} <= set(data["colleges"])
    for p in data["places"]:
        assert p["id"] and p["name"] and p["description"], p
        assert 0 <= p["x"] <= 1000 and 0 <= p["y"] <= 700, p


def test_regions_and_paths(data):
    assert len(data["regions"]) == 5
    assert {r["college"] for r in data["regions"]} == {"lore", "quan", "silv", "pris", "with"}
    for r in data["regions"]:
        assert r["d"].startswith("M") and r["d"].endswith("Z"), r
    # Seven dashed walkways; every vertex inside the viewBox.
    assert len(data["paths"]) == 7
    for pts in data["paths"]:
        assert len(pts) >= 2
        for x, y in pts:
            assert 0 <= x <= 1000 and 0 <= y <= 700


def test_river_is_kept_as_a_curve(data):
    """A cubic is kept verbatim, not sampled: inventing a vertex list for it
    would put precision in the file that the page does not have."""
    assert data["river"].startswith("M0,520 C")
    assert data["river"].count("C") == 2


# ─── battle maps ──────────────────────────────────────────────────────────────

def test_all_five_battle_maps_found(data):
    assert {b["page_key"] for b in data["battle_maps"]} == set(ce.PAGE_MAP_KEYS)


@pytest.mark.parametrize("page_key,engine_name", sorted(ce.PAGE_MAP_KEYS.items()))
def test_battle_map_matches_engine(data, page_key, engine_name):
    """Checked against the engine twice, deliberately: `maps.load` proves the
    ported file still compiles and runs, and the raw JSON carries the fields
    (width, height, spawns) the compiled grid does not keep."""
    bm = next(b for b in data["battle_maps"] if b["page_key"] == page_key)
    assert bm["map"] == engine_name

    spec = json.loads(read_text(ce.MAPS_DIR / f"{engine_name}.json"))
    assert (bm["width"], bm["height"]) == (spec["width"], spec["height"])
    assert list(spec.get("zones", [])) == bm["zones"]

    compiled = engine_maps.load(engine_name)     # must still be a usable map
    assert compiled["grid"]["rows"]

    # The page's short terrain vocabulary maps onto real engine terrain.
    for f in bm["features"]:
        assert f["type"] in ce.TERRAIN_LEGEND, f
        assert f["x"] + f["w"] <= bm["width"] and f["y"] + f["h"] <= bm["height"], f


def test_features_including_unlabelled_ones_are_all_captured(data):
    """Regression: the feature pattern once required a trailing label, which
    dropped every unnamed rectangle -- frog-pond came back with 2 of 9."""
    expected = {"mage-tower": 9, "firejolt-rooftops": 17, "detention-bog": 11,
                "frog-pond": 9, "blank": 0}
    got = {b["map"]: len(b["features"]) for b in data["battle_maps"]}
    assert got == expected


def test_unlabelled_and_labelled_features_both_present(data):
    """The other half of the same regression: a label must still be read when
    present, not just tolerated when absent."""
    pond = next(b for b in data["battle_maps"] if b["map"] == "frog-pond")
    labels = [f["label"] for f in pond["features"]]
    assert "Start bank" in labels and "Finish bank" in labels
    assert None in labels, "frog-pond's lily pads are unlabelled and must survive"


def test_each_map_chunk_does_not_leak_into_the_next(data):
    """Regression: map bodies were once split on a `\\n },` terminator that the
    last entry lacks, so every map inherited the next one's rectangles."""
    counts = {b["map"]: len(b["features"]) for b in data["battle_maps"]}
    assert counts["blank"] == 0, "the empty map must stay empty"
    assert counts["mage-tower"] == 9, "stadium must not carry the cafe's features"


def test_token_squares_are_valid(data):
    for b in data["battle_maps"]:
        for t in b["tokens"]:
            assert 0 <= t["x"] < b["width"] and 0 <= t["y"] < b["height"], t
            col = chr(ord("A") + t["x"])
            assert t["square"] == f"{col}{t['y'] + 1}", t
            assert t["color"] in ce.TOKEN_SIDE, t


def test_token_names_are_a_subset_of_the_engine_spawns(data):
    """No token on the page is missing from the ported map. The reverse is
    expected and fine: the port splits and merges rectangles freely."""
    for b in data["battle_maps"]:
        spec = json.loads(read_text(ce.MAPS_DIR / f"{b['map']}.json"))
        engine_names = {s.get("name") for s in spec.get("spawns", [])}
        missing = {t["name"] for t in b["tokens"]} - engine_names
        assert not missing, f"{b['map']}: {missing} on the page but not ported"


def test_reconcile_reports_nothing(data):
    """`--check` exits 0 only when the page and the engine agree."""
    assert data["problems"] == []


# ─── rendering ────────────────────────────────────────────────────────────────

def test_markdown_covers_everything(data):
    md = ce.to_markdown(data)
    assert md.startswith("# Strixhaven campus")
    for p in data["places"]:
        assert p["name"] in md, p["name"]
    for b in data["battle_maps"]:
        assert f"`{b['map']}.json`" in md, b["map"]
    for key in data["colleges"].values():
        assert key in md, key


def test_markdown_numbers_are_not_floats(data):
    """Coordinates render as `500`, not `500.0` -- a GM reads these by eye."""
    md = ce.to_markdown(data)
    assert "(500, 350)" in md
    assert "500.0" not in md


def test_json_round_trips(data, tmp_path):
    out = tmp_path / "campus.json"
    assert ce.main(["--json", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["places"] == data["places"]


def test_missing_reference_exits_2(tmp_path):
    assert ce.main(["--reference", str(tmp_path / "nope.html")]) == 2


def test_truncated_page_raises_rather_than_returning_partial(tmp_path):
    """A page cut off mid-object must fail loudly. Silently returning the
    places parsed so far is how a campus quietly loses a wing."""
    bad = tmp_path / "bad.html"
    bad.write_text("<script>const places=[{id:\"a\",n:\"A\"", encoding="utf-8")
    with pytest.raises(ce.ExtractError):
        ce.extract(bad)
