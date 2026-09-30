"""
test_chartdown_to_atlas.py: guard the Chartdown -> Atlas conversion.

Run: python3 -m pytest tests/test_chartdown_to_atlas.py -q

WHY THESE TESTS EXIST
---------------------
`atlas_to_map.py` shipped a fixture that was a bare Atlas `MapFile` rather than the
zustand envelope `{state, version}` that Atlas actually writes. The parser passed 18
tests while refusing every real scene on disk, because the fixture agreed with the
parser and the parser had only ever seen the fixture. The lesson recorded in
`docs/guides/handoff-obsidian-atlas.md` §4.7 is to build a fixture from the source you
just read, not from the shape you assumed.

So one test here reads a REAL `.atlasmap` written by the installed Atlas plugin and treats it
as the reference for the envelope shape, and the next asserts the converter agrees with it.
The rest pin the one piece of arithmetic that can be silently wrong: Chartdown line coordinates
are 1-based, Atlas pixels are 0-based, and getting it wrong shifts every wall by one cell
without failing anything visible.

THE OPTIONAL DEPENDENCY
-----------------------
`chartdown_to_atlas.mjs` is ours, but the Chartdown parser it drives is not: `@chartdown/core`
and `@chartdown/render-svg` are npm packages, they are the thing that computes the wall
geometry, and reimplementing them here is out of the question. So every test that shells out to
the converter carries `@requires_chartdown` and skips when Chartdown is not installed.

That marker is not decoration. CI installs no npm packages, and every one of these tests
except `test_envelope_is_the_zustand_envelope` was already wearing it, which is why one test
was red and the rest green: the envelope test was written without the marker and ran straight
into `ERR_MODULE_NOT_FOUND`. The rule this file now follows is that any test touching the
converter wears the marker, and the probe tests below keep the marker honest.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "chartdown_to_atlas.mjs"

# The npm packages `chartdown_to_atlas.mjs` imports at the top of the file. Every one of
# them has to resolve, because a missing one is a hard `ERR_MODULE_NOT_FOUND` at converter
# startup, not a degraded run. This is a Node/ESM dependency that the Python engine does
# not need, exactly like the gitignored battle-map artwork, so it is optional here: these
# tests run for anyone who has run `npm install` and skip cleanly for everyone else.
CHARTDOWN_PACKAGES = ("@chartdown/core", "@chartdown/render-svg")


def _node_on_path() -> bool:
    return shutil.which("node") is not None


def _chartdown_installed() -> bool:
    """Is the converter actually runnable here? Never raises, so collection cannot fail.

    The `shutil.which` guard is load-bearing. This function is called at import time, before
    any skipif is evaluated, so spawning `node` unconditionally raised FileNotFoundError on a
    checkout with no Node. That is a collection ERROR, not a skip, and one bad module
    interrupts the whole suite: a machine without Node ran zero tests instead of all of them.
    """
    if not _node_on_path():
        return False
    for package in CHARTDOWN_PACKAGES:
        probe = subprocess.run(
            ["node", "-e", f"import('{package}').then(()=>process.exit(0),()=>process.exit(1))"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            return False
    return True


CHARTDOWN_INSTALLED = _chartdown_installed()

requires_chartdown = pytest.mark.skipif(
    not CHARTDOWN_INSTALLED,
    reason=(
        "Chartdown is not installed here. Run `npm install` in open-tabletop-gm "
        "(needs node plus @chartdown/core and @chartdown/render-svg)."
    ),
)


# --- the probe itself ---------------------------------------------------------
#
# These guard the guard. `requires_chartdown` is the only thing standing between a clone
# without Node and a red suite, so it gets tested as carefully as the converter.


def test_probe_reports_not_installed_when_node_is_missing(monkeypatch):
    """No Node means not installed. It must not raise on the way to that answer."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    def explode(*_args, **_kwargs):
        raise AssertionError("the probe spawned node with no node on PATH")

    monkeypatch.setattr(subprocess, "run", explode)
    assert _chartdown_installed() is False


def _chartdown_imports() -> set:
    """The @chartdown/* specifiers the converter imports at the top of the script."""
    return set(re.findall(r'from "(@chartdown/[^"]+)"', SCRIPT.read_text(encoding="utf-8")))


def test_probe_covers_every_package_the_converter_imports():
    """The probe list and the script's imports must not drift apart.

    This is the invariant that was violated when this suite went red: the script imported
    `@chartdown/render-svg` too, and the probe only asked about `@chartdown/core`. So it
    reported 'installed' for a tree that could not import the script, and the test failed with
    the ERR_MODULE_NOT_FOUND the probe existed to pre-empt. Deriving both sides makes the
    omission impossible to reintroduce by editing one list and forgetting the other.
    """
    imported = _chartdown_imports()
    assert imported, "the converter no longer imports @chartdown/*; has this test outlived it?"
    assert imported == set(CHARTDOWN_PACKAGES), (
        f"the script imports {sorted(imported)} but the probe checks {sorted(CHARTDOWN_PACKAGES)}"
    )


@pytest.mark.parametrize("missing", CHARTDOWN_PACKAGES)
def test_probe_rejects_a_partial_install(monkeypatch, missing):
    """One import present and one absent is not 'installed'.

    Every test below shells out to the converter, so a probe that is too generous turns the
    whole file red on a clone that simply has not run `npm install`.
    """
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        code = "raise SystemExit(1)" if missing in cmd[2] else "pass"
        return real_run([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _chartdown_installed() is False


def run_converter(tmp_path: Path, source: str, *args: str) -> dict:
    """Write a Chartdown document, run the converter, return the parsed .atlasmap."""
    cd = tmp_path / "map.cd"
    cd.write_text(source, encoding="utf-8")
    out = tmp_path / "map.atlasmap"
    proc = subprocess.run(
        [
            "node",
            str(SCRIPT),
            str(cd),
            "-o",
            str(out),
            "--background",
            "atlas-vtt/collections/Strixhaven/maps/bows-end-tavern.jpg",
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"converter failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


SIMPLE = """# Probe
map: battlemap
grid: square 10x10
scale: 5ft

[structures]
building b "B" : C3..F6
  door : D6.s
"""


# --- the envelope ------------------------------------------------------------


@requires_chartdown
def test_envelope_is_the_zustand_envelope(tmp_path):
    """Atlas stores `{state, version}`. A bare state is what Atlas never writes."""
    scene = run_converter(tmp_path, SIMPLE)
    assert set(scene) == {"state", "version"}
    assert scene["state"]["schema"] == "atlas-vtt"
    assert scene["state"]["version"] == scene["version"]


def test_envelope_matches_a_real_atlas_file():
    """Guard against the atlas_to_map.py fixture bug, using Atlas's own output."""
    real = (
        Path.home()
        / "github/strixhaven-kairos/campaigns/strixhaven-kairos"
        / "atlas-vtt/collections/Strixhaven/Bow's End Tavern.atlasmap"
    )
    if not real.exists():
        pytest.skip("no installed Atlas scene to compare against")
    data = json.loads(real.read_text(encoding="utf-8"))
    assert set(data) == {"state", "version"}, "Atlas's own file is the reference"


# --- the 1-based -> 0-based conversion ---------------------------------------


@requires_chartdown
def test_wall_lines_land_on_grid_multiples(tmp_path):
    """A wall on Chartdown line n must sit at pixel (n-1) * cellPx.

    Building C3..F6 occupies columns 3-6 and rows 3-6, so its north face is line 6
    (pixel 500 at 100 px/cell), its west face is line 3 (pixel 200), its south face is
    line 7 (pixel 600) and its east face is line 7 (pixel 600).
    """
    scene = run_converter(tmp_path, SIMPLE)
    walls = scene["state"]["objects"]["walls"]
    solid = [w for w in walls if w["type"] == "solid"]

    ys = {w["p1"]["y"] for w in solid} | {w["p2"]["y"] for w in solid}
    xs = {w["p1"]["x"] for w in solid} | {w["p2"]["x"] for w in solid}
    assert ys == {200, 300, 400, 500, 600}, f"unexpected wall y lines: {sorted(ys)}"
    assert xs == {200, 300, 400, 500, 600}, f"unexpected wall x lines: {sorted(xs)}"


@requires_chartdown
def test_door_sits_on_the_south_face(tmp_path):
    """The door at D6.s must be a horizontal segment on the building's south wall."""
    scene = run_converter(tmp_path, SIMPLE)
    doors = [w for w in scene["state"]["objects"]["walls"] if w["type"] == "door"]
    assert len(doors) == 1
    door = doors[0]
    assert door["p1"]["y"] == door["p2"]["y"] == 600, "south face is line 7 -> pixel 600"
    assert door["p1"]["x"] == 300 and door["p2"]["x"] == 400, "D6 is column 4 -> pixel 300"
    assert door["closed"] is True


@requires_chartdown
def test_cell_px_is_honoured(tmp_path):
    scene = run_converter(tmp_path, SIMPLE, "--cell-px", "50")
    walls = scene["state"]["objects"]["walls"]
    ys = {w["p1"]["y"] for w in walls}
    assert ys == {100, 150, 200, 250, 300}, f"halving cellPx must halve every line: {sorted(ys)}"
    assert scene["state"]["grid"]["size"] == 50


# --- the redaction guarantee -------------------------------------------------


HIDDEN = """# Probe
map: battlemap
grid: square 10x10
scale: 5ft

[structures]
building b "B" : C3..F6
  door : D6.s

[tokens]
start party "Kairos" : D4
vess "Vess" : E5 hidden gm="She is reading his page."
"""


@requires_chartdown
def test_hidden_token_present_in_gm_mode(tmp_path):
    scene = run_converter(tmp_path, HIDDEN, "--mode", "gm")
    names = {t["name"] for t in scene["state"]["objects"]["tokens"].values()}
    assert "Vess" in names


@requires_chartdown
def test_hidden_token_absent_in_player_mode(tmp_path):
    """The one failure this pipeline exists to make impossible."""
    scene = run_converter(tmp_path, HIDDEN, "--mode", "player")
    names = {t["name"] for t in scene["state"]["objects"]["tokens"].values()}
    assert "Vess" not in names, "a hidden token leaked into the player scene"
    assert "Kairos" in names, "dropping the hidden token must not drop the rest"


@requires_chartdown
def test_gm_notes_never_reach_the_scene_file(tmp_path):
    """Chartdown's gm= text is never emitted into an Atlas scene in either mode."""
    for mode in ("gm", "player"):
        scene = run_converter(tmp_path, HIDDEN, "--mode", mode)
        blob = json.dumps(scene)
        assert "reading his page" not in blob, f"GM prose leaked into the {mode} scene"


# --- tokens sit on cell centres ---------------------------------------------


@requires_chartdown
def test_token_is_centred_in_its_cell(tmp_path):
    scene = run_converter(tmp_path, HIDDEN, "--mode", "gm")
    kairos = scene["state"]["objects"]["tokens"]["party"]
    # Cell D4 -> column 4, row 4 -> pixel (3*100+50, 3*100+50) = (350, 350)
    assert (kairos["x"], kairos["y"]) == (350, 350)


# --- refusal -----------------------------------------------------------------


@requires_chartdown
def test_a_rejected_document_produces_no_scene(tmp_path):
    """Fail loud. A half-converted map is worse than no map."""
    broken = SIMPLE + '\nbroken line with no colon\n'
    cd = tmp_path / "broken.cd"
    cd.write_text(broken, encoding="utf-8")
    out = tmp_path / "broken.atlasmap"
    proc = subprocess.run(
        ["node", str(SCRIPT), str(cd), "-o", str(out), "--background", "x.jpg"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode != 0
    assert not out.exists(), "a rejected document must not leave a scene behind"


@requires_chartdown
def test_background_is_required(tmp_path):
    cd = tmp_path / "map.cd"
    cd.write_text(SIMPLE, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(SCRIPT), str(cd), "-o", str(tmp_path / "m.atlasmap")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode != 0
    assert "--background" in proc.stderr


# --- the converter must never destroy GM work --------------------------------
#
# On 2026-09-30 this script overwrote a live Bow's End scene that had a hand-painted fog
# stroke in it. `.atlasmap` files were not tracked by git at the time, so the stroke was
# unrecoverable. These tests exist because of that.


def _seed_scene_with_gm_work(out: Path) -> dict:
    """An existing scene carrying state Chartdown has no opinion about."""
    scene = {
        "state": {
            "schema": "atlas-vtt",
            "version": 4,
            "background": "old.jpg",
            "grid": {"size": 100},
            "objects": {
                "tokens": {},
                "fog": {"fog_1": {"id": "fog_1", "kind": "fog", "points": [{"x": 1, "y": 2}]}},
                "pins": {"p1": {"id": "p1", "notePath": "arcs/secret.md"}},
                "texts": {"t1": {"id": "t1", "text": "DO NOT LOSE ME"}},
                "drawings": {"d1": {"id": "d1", "points": [{"x": 3, "y": 4}]}},
                "walls": [{"id": "old_wall", "kind": "wall", "type": "solid", "p1": {"x": 0, "y": 0}, "p2": {"x": 100, "y": 0}}],
                "lights": [{"id": "l1", "x": 50, "y": 50}],
            },
            "camera": {"x": 10, "y": 20, "zoom": 2},
            "initiative": [{"id": "K", "name": "Kairos"}],
            "dmNotePath": "arcs/GM.md",
        },
        "version": 4,
    }
    out.write_text(json.dumps(scene, indent=2), encoding="utf-8")
    return scene


@requires_chartdown
def test_existing_fog_survives_regeneration(tmp_path):
    """The regression that cost a painted fog stroke."""
    out = tmp_path / "map.atlasmap"
    _seed_scene_with_gm_work(out)
    scene = run_converter(tmp_path, SIMPLE, "-o", str(out))
    assert "fog_1" in scene["state"]["objects"]["fog"], "hand-painted fog was destroyed"


@requires_chartdown
def test_existing_pins_texts_drawings_lights_survive(tmp_path):
    out = tmp_path / "map.atlasmap"
    _seed_scene_with_gm_work(out)
    objects = run_converter(tmp_path, SIMPLE, "-o", str(out))["state"]["objects"]
    assert "p1" in objects["pins"]
    assert "DO NOT LOSE ME" in json.dumps(objects["texts"])
    assert "d1" in objects["drawings"]
    assert objects["lights"] and objects["lights"][0]["id"] == "l1"


@requires_chartdown
def test_existing_camera_initiative_and_dmnote_survive(tmp_path):
    out = tmp_path / "map.atlasmap"
    _seed_scene_with_gm_work(out)
    state = run_converter(tmp_path, SIMPLE, "-o", str(out))["state"]
    assert state["camera"] == {"x": 10, "y": 20, "zoom": 2}
    assert state["initiative"][0]["name"] == "Kairos"
    assert state["dmNotePath"] == "arcs/GM.md"


@requires_chartdown
def test_walls_are_replaced_because_we_own_them(tmp_path):
    out = tmp_path / "map.atlasmap"
    _seed_scene_with_gm_work(out)
    walls = run_converter(tmp_path, SIMPLE, "-o", str(out))["state"]["objects"]["walls"]
    assert not any(w.get("id") == "old_wall" for w in walls), "stale walls must be replaced"


@requires_chartdown
def test_fresh_discards_the_existing_scene_entirely(tmp_path):
    out = tmp_path / "map.atlasmap"
    _seed_scene_with_gm_work(out)
    objects = run_converter(tmp_path, SIMPLE, "-o", str(out), "--fresh")["state"]["objects"]
    assert objects["fog"] == {}
    assert objects["texts"] == {}


@requires_chartdown
def test_corrupt_existing_scene_is_refused_not_overwritten(tmp_path):
    out = tmp_path / "map.atlasmap"
    out.write_text("{not json", encoding="utf-8")
    cd = tmp_path / "map.cd"
    cd.write_text(SIMPLE, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(SCRIPT), str(cd), "-o", str(out), "--background", "x.jpg"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode != 0
    assert out.read_text(encoding="utf-8") == "{not json", "a corrupt scene must be left alone"
