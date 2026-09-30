"""The board's ruler, its templates and its camera memory (BV6, PV3).

The ruler must read feet the way the engine does, so the JavaScript helpers in
display/static/tactics.js are run under node and pinned against the Python
grid.distance and grid.area on the same inputs, for both diagonal rules. The
camera helpers are pure too. Skipped when node is absent.
"""
from __future__ import annotations

import itertools
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from tests.tactics_fixtures import encounter, goblin, kairos, start
from tactics import grid, sync

REPO = pathlib.Path(__file__).resolve().parent.parent
JS = REPO / "display" / "static" / "tactics.js"
CSS = REPO / "display" / "static" / "tactics.css"
NODE = shutil.which("node")
PURE = re.compile(r"/\* Pure helpers:.*?\*/(.*?)/\* end pure helpers \*/", re.S)


def run_js(js: str):
    if not NODE:
        pytest.skip("node is not installed")
    body = PURE.search(JS.read_text(encoding="utf-8")).group(1) + "\n" + js
    prog = "const out = (() => {" + body + "})();process.stdout.write(JSON.stringify(out));"
    r = subprocess.run([NODE, "-"], input=prog.encode("utf-8"), capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return json.loads(r.stdout.decode("utf-8"))


def open_grid(rule, w=25, h=25):
    return grid.Grid(["." * w] * h, diagonals=rule)


PAIRS = [(0, 0), (3, 0), (0, 4), (3, 3), (5, 2), (7, 7), (1, 9), (10, 10)]


@pytest.mark.parametrize("rule", ["5", "5-10-5"])
def test_js_distance_matches_grid_distance(rule):
    g = open_grid(rule)
    pairs = [((2, 3), (2 + dx, 3 + dy)) for dx, dy in PAIRS] + [((9, 9), (4, 1)), ((0, 0), (0, 0))]
    got = run_js(f"return {json.dumps(pairs)}.map(p => gridDistance({json.dumps(rule)}, 5, p[0], p[1]));")
    assert got == [g.distance(a, b) for a, b in pairs]


def test_the_two_diagonal_rules_really_differ_on_a_long_diagonal():
    # Guards the test above from passing on a JS that ignores the rule.
    assert open_grid("5").distance((0, 0), (4, 4)) == 20
    assert open_grid("5-10-5").distance((0, 0), (4, 4)) == 30
    assert run_js("return [gridDistance('5-10-5', 5, [0,0], [4,4]), gridDistance('5', 5, [0,0], [4,4])];") == [30, 20]


CASES = [("cone", 15, (10, 10), (13, 10)), ("cone", 30, (10, 10), (13, 13)),
         ("cone", 30, (10, 10), (8, 13)), ("cone", 15, (5, 5), (5, 2)),
         ("line", 30, (10, 10), (14, 10)), ("line", 60, (3, 3), (9, 6)), ("line", 30, (10, 10), (7, 7)),
         ("sphere", 20, (10, 10), (10, 10)), ("sphere", 15, (4, 4), (4, 4)), ("sphere", 5, (0, 0), (0, 0))]


@pytest.mark.parametrize("shape,size,caster,target", CASES)
def test_js_template_matches_grid_area_on_an_open_map(shape, size, caster, target):
    g = open_grid("5")
    if shape == "sphere":
        want = grid.area(g, "sphere", size, caster, target, from_self=False)["squares"]
        js_shape = "circle"
    else:
        want = grid.area(g, shape, size, caster, target)["squares"]
        js_shape = shape
    got = run_js(f"return templateSquares({json.dumps(js_shape)}, {size}, 5, {list(caster)}, {list(target)}, 25, 25, 5);")
    assert [tuple(p) for p in got] == [tuple(p) for p in want]


def test_a_template_is_clipped_to_the_board():
    got = run_js("return templateSquares('circle', 10, 5, [0,0], [0,0], 25, 25, 5);")
    assert all(x >= 0 and y >= 0 for x, y in got) and [0, 0] in got


def test_snapshot_carries_what_the_ruler_needs():
    for rule in ("5", "5-10-5"):
        enc = start(encounter([kairos(pos=(0, 0)), goblin("goblin-1", (4, 0))], rows=["......"] * 5), ["kairos", "goblin-1"])
        enc.grid["diagonals"] = rule
        snap = sync.snapshot(enc)
        assert snap["square_ft"] == grid.SQUARE_FT
        assert snap["grid"]["diagonals"] == rule


def test_camera_round_trip_and_zoom_passthrough():
    got = run_js("""
      let s = cameraPut({}, 'Frog Pond', { scrollL: 120, scrollT: 40 });
      s = cameraPut(s, 'Tavern', { scrollL: 5, scrollT: 6, zoom: 1.5 });
      return cameraParse(JSON.stringify(s));""")
    assert got == {"Frog Pond": {"scrollL": 120, "scrollT": 40},
                   "Tavern": {"scrollL": 5, "scrollT": 6, "zoom": 1.5}}


def test_camera_parse_survives_garbage_and_drops_bad_entries():
    got = run_js("""return [cameraParse('not json'), cameraParse(null), cameraParse('[1]'),
      cameraParse('{"a":{"scrollL":"x","scrollT":1},"b":{"scrollL":-9,"scrollT":2},"c":7}')];""")
    assert got == [{}, {}, {}, {"b": {"scrollL": 0, "scrollT": 2}}]


def test_camera_store_is_bounded_and_newest_survives():
    got = run_js("""let s = {};
      for (let i = 0; i < 60; i++) s = cameraPut(s, 'm' + i, { scrollL: i, scrollT: 0 });
      s = cameraPut(s, 'm59', { scrollL: 999, scrollT: 0 });
      return [Object.keys(s).length, 'm0' in s, s.m59.scrollL, Object.keys(s).pop()];""")
    assert got == [40, False, 999, "m59"]


def test_ruler_is_wired_and_never_persisted():
    src = JS.read_text(encoding="utf-8")
    assert "tx-camera" in src and "data-ruler" in src
    # Ephemeral: no ruler state reaches the engine call or the encounter.
    assert not re.search(r"call\(\s*['\"]ruler", src)
    css = CSS.read_text(encoding="utf-8")
    assert ".tx-ruler-layer { pointer-events: none; }" in css


def test_geometry_seams_for_a_later_hex_grid():
    got = run_js("const C = 32; return [cellCentre(2, 3), distFeet([0,0],[4,4],'5-10-5',5), distFeet([0,0],[4,4],'5',5)];")
    assert got == [{"px": 80, "py": 112}, 30, 20]
    # The ruler draws through the seams, not through raw cell math.
    src = JS.read_text(encoding="utf-8")
    body = src[src.index("function drawRuler"):src.index("// ── camera memory")]
    assert "cellCentre(" in body and "distFeet(" in body and "gridDistance(" not in body
