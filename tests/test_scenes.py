"""What a scene has to be, and the one conversion it must not get wrong.

A scene is the map that exists when no fight is running. Six things carry the
design, and the tests here are one per thing:

  1. **Named places come from the `.cd`, and the coordinate is stored as a
     fraction.** `here biblioplex` reads `(700,935)` off a `1400x1895` extent and
     writes `0.5, 0.493404`, which is the same place after the artwork is
     re-exported at twice the size. A stored pixel would be wrong the moment the
     SVG changed, and nothing would report it.
  2. **The `.cd` to SVG scale is ONE factor.** `1400x1895` against
     `0 0 820 1109.93` gives `0.5857142857` across and `0.5857150396` down: a
     relative difference of 1.3e-6, which is the viewBox height written to two
     decimals. A per-axis conversion therefore misplaces the Biblioplex by about
     a thousandth of a pixel and looks perfect. This test compares the two axes
     **against each other**, which is the only comparison that kills the mutant.
  3. **`/c end` restores the scene.** Combat borrows the map slot; `cli._end`
     used to write the literal `*(none)*` over it, which erased the world rather
     than releasing it.
  4. **A scene survives a restart.** It is a file, written atomically with a
     `.bak`, exactly as `formations` is. A "where are we" that does not survive
     a crash is not a place.
  5. **An unrevealed marker never reaches a browser.** BV4's kill criterion
     (`SPEC-grid-and-map.md:324-326`) applies here: the display has one audience
     and no GM route, so a marker that is not `revealed` is absent from the
     payload entirely, and the default is `True` because a GM who placed the
     party meant to.
  6. **`maps.compile_map` is byte-identical.** A regression guard, not a
     demonstration: this one PASSES before the change and that is the point.

Every test writes only inside `tmp_path`. Nothing here reads the live campaign
repo, and `tests/test_scenes.py` has no fixture that could leak a campaign into
the checkout the way an un-scoped `GM_CAMPAIGN_ROOT` would.

Conventions follow the rest of `tests/`: own `sys.path` header, fixtures rather
than the gitignored SRD build, `tmp_path` campaigns, and a docstring that says
what is being defended rather than what is being called.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import tactics_fixtures as fx                                     # noqa: E402
from tactics import cli, maps                                     # noqa: E402

# `scenes` is imported lazily rather than at module scope, and that is not a
# style choice: `test_compile_map_is_byte_identical` is a regression guard that
# has to be *runnable on the pre-change code*, where `tactics.scenes` does not
# exist. A module-level import would make it fail with ImportError and it would
# stop being a guard. Every other test in this file needs scenes, so they pay the
# import; this one does not, so it does not.
def scenes():
    """`tactics.scenes`, imported on demand. See the note above."""
    from tactics import scenes as _scenes
    return _scenes

KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")

# The Strixhaven campus's own numbers, read off the committed files rather than
# invented: `strixhaven-campus.cd:28` declares this extent and the committed
# `strixhaven-campus.player.svg` carries this viewBox. Both are transcribed here
# so the test needs no campaign checkout and cannot be fooled by one that moved.
CAMPUS_EXTENT = (1400.0, 1895.0)
CAMPUS_VIEWBOX = (820.0, 1109.93)

# A cut-down `.cd` carrying the header and the four places the tests need. The
# placement lines are copied verbatim from `strixhaven-campus.cd:69-74`, which is
# what makes `biblioplex` at (700,935) a fact about the real map rather than a
# number that happens to divide nicely.
CAMPUS_CD = """\
; Strixhaven campus
;
; COORDINATES are read off the 1400x1895 crop of the map body, origin top-left.

# Strixhaven

map: region
extent: 1400x1895
compass: on

[water]
coastline coast : from (1400,300) via (1210,640) to (980,1895)

[features]
; --- The core ------------------------------------------------------------
landmark biblioplex "The Biblioplex" : (700,935)
landmark archway "Archway Commons" : (700,1205)
landmark firejolt "Firejolt Café" : (710,1060)
landmark bowsend "Bow's End Tavern" : (605,883)

[paths]
road northspoke : from (700,935) via (700,600) to (710,240)

[gm]
biblioplex : "Bigger inside than out."
"""

CAMPUS_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 1109.93" '
              'width="820" height="1109.93"><rect width="820" height="1109.93"/></svg>')


# ─── the campaign ─────────────────────────────────────────────────────────────

@pytest.fixture
def camp(tmp_path, monkeypatch):
    """A temp campaign with a Kairos sheet, a Chartdown source and its SVG.

    The `.cd` is written here rather than read from the campaign repo: a test
    that reaches into `~/github/strixhaven-kairos` is a test that fails on
    somebody else's machine and can pass against a map nobody shipped.
    """
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    chart = d / "maps" / "chartdown"
    chart.mkdir(parents=True)
    (chart / "strixhaven-campus.cd").write_text(CAMPUS_CD, encoding="utf-8")
    (chart / "strixhaven-campus.player.svg").write_text(CAMPUS_SVG, encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    return d


@pytest.fixture
def srd(monkeypatch):
    """The real SRD records for the two monsters `test_ending_combat` needs."""
    rules_module = sys.modules[type(fx.RULES).__module__]

    def lookup(name: str):
        key = name.lower().replace(" ", "-")
        if key not in fx._RAW:
            raise ValueError(f"no SRD monster {name!r}")
        return fx._build._norm_monster(fx._RAW[key])

    monkeypatch.setattr(rules_module, "_lookup_monster", lookup)
    monkeypatch.setattr(rules_module, "_srd_suggest", lambda n: None)
    return lookup


def run(capsys, *argv):
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def _get(client, url):
    """GET, capturing stdout so a route that prints cannot flood the test log."""
    with contextlib.redirect_stdout(io.StringIO()):
        return client.get(url)


# ─── 1. named places, stored as a fraction ────────────────────────────────────

def test_the_party_snaps_to_a_named_place(camp, capsys):
    """before fix: `AssertionError: That command is not complete: argument
    command: invalid choice: 'scene'`. With `scenes.py` removed as well it is
    `ImportError: cannot import name 'scenes' from 'tactics'`.

    `here biblioplex` has to land on a real coordinate rather than a plausible
    one, and it has to store it as a **fraction**. (700,935) on a 1400x1895
    extent is (0.5, 0.493404); stored as a pixel it would be correct only until
    the background was re-exported, and a marker in the wrong place produces no
    error anywhere.

    The number in the brief is 0.4935 and the correct one is 0.493404: 935/1895
    is 0.4934036939. The brief rounded 0.4934 up to 0.4935 by a transposition.
    The coordinate is the one in `strixhaven-campus.cd:69` and the divisor is the
    one in its `extent`, and the test asserts those.
    """
    code, out = run(capsys, "scene", "strixhaven-campus")
    assert code == 0 and "Scene set to" in out, out
    code, out = run(capsys, "here", "biblioplex")
    assert code == 0 and "The Biblioplex" in out, out

    spec = scenes().load(camp)
    assert spec["map"] == "strixhaven-campus"
    assert spec["background"] == "maps/chartdown/strixhaven-campus.player.svg"
    assert spec["extent"] == [1400.0, 1895.0]
    marker = spec["marker"]
    assert marker["place"] == "biblioplex"
    assert marker["x"] == pytest.approx(0.5, abs=1e-9)
    # 935 / 1895 == 0.4934036939..., stored at scenes.ROUNDING == 6 places.
    assert marker["y"] == pytest.approx(935 / 1895, abs=1e-6)
    assert marker["y"] == 0.493404
    # Not cells, not pixels: the stored coordinate is a fraction of `extent`.
    assert 0.0 <= marker["x"] <= 1.0 and 0.0 <= marker["y"] <= 1.0

    # And it is not a coincidence that this is a fraction. Re-export the same
    # drawing at twice the size and the stored position is still the same place,
    # because nothing in `scene.json` depends on the rendered size. The `.cd`
    # extent is the source's own and does not move; only the viewBox does.
    bigger = scenes().cd_path(camp, "strixhaven-campus").with_suffix(".player.svg")
    bigger.write_text('<svg xmlns="http://www.w3.org/2000/svg" '
                      'viewBox="0 0 1640 2219.86"/>', encoding="utf-8")
    view2 = scenes().viewbox(bigger)
    scale2 = scenes().uniform_scale(spec["extent"], view2)
    px2, py2 = scenes().scale_point((700, 935), scale2)
    # Twice the pixels, from the same stored fraction.
    assert px2 == pytest.approx(2 * 410.0, rel=1e-9)
    assert scenes().fraction((px2, py2), (spec["extent"][0] * scale2,
                                        spec["extent"][1] * scale2)) == \
        (marker["x"], marker["y"])


# ─── 2. one factor, not one per axis ──────────────────────────────────────────

def test_the_cd_to_svg_scale_is_one_uniform_factor(camp):
    """before fix: `ImportError: cannot import name 'scenes' from 'tactics'` --
    and the mutant that matters survives a naive version of this test.

    The two per-axis ratios are 820/1400 = 0.5857142857 and 1109.93/1895 =
    0.5857150396. They differ by 1.3e-6 **relative**, which is 0.0014 pixels on a
    1895-unit map. So a per-axis conversion puts the Biblioplex in the right place
    to within a thousandth of a pixel, every assertion that compares against a
    literal still passes, and the bug is invisible at the table.

    That is why this test compares x and y **against each other** as well as
    against the declared factor. A per-axis implementation makes the implied
    factor differ between the axes by 1.3e-6; a uniform one makes them identical
    to within float noise. `pytest.approx` at rel=1e-9 is far tighter than the
    1.3e-6 it has to catch and far looser than the ~1e-16 of float64.

    The refusal is the structural half. `uniform_scale` computes both ratios
    before returning either, so there is no code path that can hand back a
    per-axis answer.
    """
    cd = scenes().cd_path(camp, "strixhaven-campus")
    svg = camp / "maps" / "chartdown" / "strixhaven-campus.player.svg"

    extent = scenes().cd_extent(cd)
    view = scenes().viewbox(svg)
    assert extent == CAMPUS_EXTENT
    assert view == CAMPUS_VIEWBOX

    scale = scenes().uniform_scale(extent, view)
    assert scale == pytest.approx(0.5857142857142857, rel=1e-12)

    # The whole campus, not just the one place a happy path would check.
    for slug, label, point in (("biblioplex", "The Biblioplex", (700, 935)),
                               ("bowsend", "Bow's End Tavern", (605, 883)),
                               ("archway", "Archway Commons", (700, 1205)),
                               ("firejolt", "Firejolt Café", (710, 1060))):
        place = scenes().find_place(cd, slug)
        assert (place["x"], place["y"]) == point, slug
        px, py = scenes().scale_point(point, scale)
        # The implied factor, read back off each axis separately.
        fx, fy = px / point[0], py / point[1]
        assert fx == pytest.approx(fy, rel=1e-9), (
            f"{slug}: the two axes imply different scales, {fx!r} against {fy!r}. "
            "A per-axis conversion is wrong by a thousandth of a pixel and looks "
            "right, which is why they are compared to each other")
        assert fx == pytest.approx(scale, rel=1e-12)

    # The two places the investigation in the brief actually computed.
    assert scenes().scale_point((700, 935), scale) == pytest.approx((410.0, 547.6), abs=0.05)
    assert scenes().scale_point((605, 883), scale) == pytest.approx((354.4, 517.2), abs=0.05)

    # The structural half: a render that genuinely stretches one axis is refused,
    # and the refusal names both numbers instead of silently picking one.
    with pytest.raises(scenes().SceneError, match="not one scale"):
        scenes().uniform_scale((1400, 1895), (820, 1200))
    with pytest.raises(scenes().SceneError, match="not one scale"):
        scenes().uniform_scale((1400, 1895), (820, 1000))

    # And rounding that only the viewBox explains is not refused: 1.3e-6 is
    # three orders of magnitude inside scenes().ASPECT_TOLERANCE.
    assert scenes().uniform_scale((1400, 1895), (820, 1895 * 820 / 1400))


# ─── 3. combat suspends the scene, it does not erase it ───────────────────────

def test_ending_combat_restores_the_scene(camp, srd, capsys):
    """before fix: `assert 1 == 0` at `combat.py scene strixhaven-campus`, whose
    output is `That command is not complete: argument command: invalid choice:
    'scene'`. The first `/c end` in this test does write `*(none)*` on pre-change
    code and that half still passes, because there is no scene to restore.

    Two halves, and the second is the one that is easy to get wrong. First, `/c
    end` must leave the scene intact on disk. Second, it must **name** it in
    `## Active Combat`, because a section that says `*(none)*` while a scene
    exists is the GM being told the world is over. The brief's word for this is
    "suspend": combat borrows the slot, and the scene is current again the
    moment the fight is.

    A campaign with no `scene.json` still gets `*(none)*`, byte-identical, which
    is the no-op half of the change and is asserted here too: an engine that
    starts writing a scene file into every campaign on the first `/c end` is a
    different and much worse change than this one.
    """
    # No scene yet: `/c end` is exactly what it was.
    assert run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--seed", "3")[0] == 0
    code, out = run(capsys, "end")
    assert code == 0 and "Combat ended" in out
    assert "*(none)*" in (camp / "state.md").read_text(encoding="utf-8")
    assert not scenes().scene_path(camp).exists(), "a campaign with no scene got one"

    # Now with a scene, and a fight on top of it.
    assert run(capsys, "scene", "strixhaven-campus")[0] == 0
    assert run(capsys, "here", "biblioplex")[0] == 0
    before = scenes().load(camp)

    assert run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--seed", "3")[0] == 0
    code, out = run(capsys, "end")
    assert code == 0 and "Combat ended" in out

    # Still current, still positioned, byte-identical to what `here` wrote.
    after = scenes().load(camp)
    assert after == before, "combat changed the scene instead of borrowing it"

    # And the section says so, by name, rather than erasing it.
    state = (camp / "state.md").read_text(encoding="utf-8")
    assert "*(none)*" not in state.split("## Active Combat")[1].split("## Session Flags")[0]
    assert "strixhaven-campus" in state and "biblioplex" in state

    # A scene with no marker on it still names itself rather than going blank.
    (camp / "scene.json").unlink()
    bare = scenes().blank(camp, "strixhaven-campus")
    scenes().save(camp, bare)
    assert run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--seed", "3")[0] == 0
    assert run(capsys, "end")[0] == 0
    assert "strixhaven-campus" in (camp / "state.md").read_text(encoding="utf-8")


# ─── 4. it survives a restart ─────────────────────────────────────────────────

def test_a_scene_survives_a_restart(camp, capsys):
    """before fix: `assert 1 == 0` at `combat.py scene strixhaven-campus`
    (invalid choice), so `scene.json` never exists and the brief's
    `FileNotFoundError` never gets the chance to be the reported failure. The
    defect is the same one either way: there is no file to survive in.

    "Where they are" is the one thing that has to outlive the process, because
    the process is what crashes. This is `formations.py`'s discipline applied to
    a different fact: JSON, one file per campaign, atomic write via temp plus
    `os.replace`, the previous version kept as `.bak`, `encoding="utf-8"`, and a
    `SCHEMA_VERSION` with a `MIGRATIONS` table ready for the first migration.

    The restart is simulated the only honest way, which is by reading the file
    with nothing in memory: every process in this repo is fresh, so
    `scenes().load` is the whole of what a restart leaves behind. The `.bak` is
    asserted separately because a write that is not atomic is a write that can
    tear, and a torn scene is a party in the middle of the map.
    """
    assert run(capsys, "scene", "strixhaven-campus")[0] == 0
    assert run(capsys, "here", "bowsend", "--name", "The company")[0] == 0
    written = (camp / "scene.json").read_text(encoding="utf-8")
    stored = json.loads(written)

    # A restart: nothing carried over but the file itself.
    reread = scenes().load(camp)
    assert reread == stored
    assert reread["marker"]["name"] == "The company"
    assert reread["marker"]["place"] == "bowsend"
    assert (reread["marker"]["x"], reread["marker"]["y"]) == \
        scenes().fraction((605, 883), CAMPUS_EXTENT)

    # UTF-8 and ASCII-safe, and the accent in "Café" survives a round trip
    # because the file is read and written with encoding="utf-8" throughout.
    (camp / "scene.json").write_text(written, encoding="utf-8")
    assert scenes().load(camp) == stored
    firejolt = scenes().find_place(scenes().cd_path(camp, "strixhaven-campus"), "firejolt")
    assert firejolt["label"] == "Firejolt Café"

    # `.bak` and an atomic write: the second save keeps the first one whole.
    assert run(capsys, "here", "archway")[0] == 0
    assert (camp / "scene.json.bak").read_text(encoding="utf-8") == written
    assert scenes().load(camp)["marker"]["place"] == "archway"
    # No temp file left behind, which is what an interrupted write would leave.
    assert not (camp / "scene.json.tmp").exists()

    # And the version fields the first migration will need.
    assert stored["schema"] == scenes().SCENE_SCHEMA
    assert stored["version"] == scenes().SCHEMA_VERSION == 1
    assert isinstance(scenes().MIGRATIONS, dict)


# ─── 5. redaction, in the display's layer ─────────────────────────────────────

@pytest.fixture(scope="module")
def app_module():
    """The display app, imported as a module so its test client is usable.

    Same dance `tests/test_pins_routes.py` does, for the same reason: the script
    starts a server on import.
    """
    saved = os.environ.get("GM_DISPLAY_PORT")
    os.environ["GM_DISPLAY_PORT"] = "5096"
    spec = importlib.util.spec_from_file_location(
        "gm_scenes_app", ROOT / "display" / "gm-display-app.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    yield module
    if saved is None:
        os.environ.pop("GM_DISPLAY_PORT", None)
    else:
        os.environ["GM_DISPLAY_PORT"] = saved


def test_a_gm_only_marker_is_redacted_from_the_player_view(camp, app_module, monkeypatch):
    """before fix: `ImportError: cannot import name 'scenes' from 'tactics'`. With
    `scenes.py` in place and the display untouched, the first GET is the
    `AssertionError: assert ('0.493404' in '<!doctype html>... 404 Not Found')`
    that says the route does not exist.

    Guards BV4's kill criterion (`SPEC-grid-and-map.md:324-326`) as it was
    answered by the `feat-map-pins` cycle and handed down in
    `.claude/briefs/feat-persistent-campus-scene.md` section 6. Three decisions,
    each of which this test pins:

      1. The field is **`revealed`**, not `gm_only`. It is a table boundary, not
         access control, because there is nothing to control: `_token_ok()`
         (`gm-display-app.py:486-491`) returns True for every browser off
         `--lan`, and in LAN mode `index()` mints the token into the page for
         every browser that loads `/`. `gm_only` promises something the code
         cannot deliver.
      2. The filter is **`is True`**, not truthiness. `not marker.get(
         "revealed", True)` passes an absent key, a `null` and a `"yes"` all the
         same way, and a hand-edited `scene.json` is a supported input.
      3. The redaction is in the **display's Flask layer**, not in
         `sync.snapshot()`. `snapshot(enc, meta)` takes no viewer, `Encounter.meta`
         (`state.py:243`) is map display info rather than marker state, and a
         scene outlives any encounter so no snapshot could carry it. The
         precedent is `world.revealed_clocks` read at `gm-display-app.py:1392-1401`.

    The default is **True**: a GM who typed `here biblioplex` meant to put the
    party on the screen, so hiding it needs `--hide`.

    Asserted on the serialised blob rather than on a parsed field, because the
    promise is that the coordinate and the label are not in any form a browser
    could read.
    """
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(camp.parent.parent))
    monkeypatch.setattr(app_module, "CAMP_FILE", str(camp.parent / ".campaign"))
    (camp.parent / ".campaign").write_text("demo", encoding="utf-8")
    client = app_module.app.test_client()

    # The default is revealed: the party is on the players' board, because the
    # GM placed it there on purpose.
    shown = scenes().blank(camp, "strixhaven-campus")
    shown["marker"].update({"name": "The party", "place": "biblioplex",
                            "x": 0.5, "y": 0.493404, "revealed": True})
    scenes().save(camp, shown)
    body = _get(client, "/scene").get_data(as_text=True)
    assert "0.493404" in body and "biblioplex" in body
    assert json.loads(body)["scene"]["marker"]["name"] == "The party"

    # Not revealed: absent, not flagged. A flag would tell the players a secret
    # exists at a coordinate, which `world.revealed_clocks` drops for the same
    # reason.
    hidden = json.loads(json.dumps(shown))
    hidden["marker"]["revealed"] = False
    scenes().save(camp, hidden)
    body = _get(client, "/scene").get_data(as_text=True)
    assert "0.493404" not in body
    assert "biblioplex" not in body
    assert json.loads(body)["scene"]["marker"] is None
    # The map is still there. What is hidden is where the party is standing, not
    # which map they are on, and a payload that dropped the map would be a worse
    # answer than one that drops the marker.
    assert json.loads(body)["scene"]["map"] == "strixhaven-campus"

    # The predicate itself, on the three inputs that fool a truthiness check.
    assert scenes().revealed(shown)["x"] == 0.5
    assert scenes().revealed(hidden) is None
    assert scenes().revealed({"marker": {"x": 0.5, "y": 0.5, "name": "x"}}) is None, \
        "an absent `revealed` must not pass as true"
    assert scenes().revealed({"marker": {"x": 0.5, "y": 0.5, "revealed": None}}) is None
    assert scenes().revealed({"marker": {"x": 0.5, "y": 0.5, "revealed": "true"}}) is None
    assert scenes().revealed({"marker": {"x": 0.5, "y": 0.5, "revealed": 1}}) is None
    assert scenes().revealed(None) is None and scenes().revealed({}) is None

    # And a campaign with no scene is an empty answer, not a 500.
    scenes().scene_path(camp).unlink()
    assert json.loads(_get(client, "/scene").get_data(as_text=True))["scene"] is None


# ─── 6. compile_map is untouched ──────────────────────────────────────────────

def test_compile_map_is_byte_identical():
    """before fix: PASSES. That is the point: a regression guard, not a
    demonstration of the defect.

    `maps.compile_map` is the engine's path from a map file to a grid, and every
    shipped map plus the `grid.rows` byte-stability guarantee depends on it not
    changing. A scene is campaign data; putting it in the map format would mean
    one map file per campaign for the same drawing, which is the argument
    `display/maps/README.md:62-70` makes about formations and which applies here
    unchanged.

    This walks every shipped map and pins the compiled output as JSON, so a
    change anywhere in `compile_map` -- a new key in `meta`, a reordering, a
    different terrain char -- fails here. It is deliberately not a diff against a
    stored golden file: a golden file that is regenerated by the code that
    produced it guards nothing.
    """
    before = sys.modules.get("tactics.maps")
    out = {}
    for name in maps.available():
        compiled = maps.compile_map(json.loads(
            (maps.MAPS_DIR / f"{name}.json").read_text(encoding="utf-8")))
        out[name] = {"grid": compiled["grid"], "meta": compiled["meta"]}

    assert out, "the map registry is empty, so this asserts nothing"
    assert len(out) >= 25, f"only {len(out)} maps; the registry lost one"
    for name in ("frog-pond", "detention-bog", "biblioplex-stacks"):
        assert name in out, name
        assert out[name]["grid"]["rows"]
        assert out[name]["meta"]["name"]

    # No scene concept leaked into the compiled shape. This is the assertion the
    # brief cares about: `maps.compile_map` knows nothing about `scene.json`.
    #
    # Checked against the *keys* rather than the serialised text, because a map
    # is free text in its `info` field and "use a blank grid for any scene the
    # other maps don't cover" is a sentence in `blank.json` that has nothing to
    # do with this change. A key is the structural claim; prose is not.
    for name, compiled in out.items():
        extra = set(compiled["grid"]) - {"name", "rows", "diagonals", "legend", "terrain"}
        assert not extra, f"{name}: compile_map added grid key(s) {sorted(extra)}"
        extra_meta = set(compiled["meta"]) - {
            "name", "info", "labels", "landmarks", "zones", "spawns", "portraits",
            "colors", "image", "grid_align"}
        assert not extra_meta, f"{name}: compile_map added meta key(s) {sorted(extra_meta)}"
        assert "scene" not in compiled["meta"] and "marker" not in compiled["meta"]

    # And the module object is the same one, unmodified: `git diff maps.py` is
    # the real guard and this catches the import-level version of the mistake.
    assert sys.modules["tactics.maps"] is before or before is None
    # `maps.py` must not have learned about scenes. Checked against the module's
    # own namespace rather than by asserting scenes is unimportable, because the
    # test file above does import it and that is not a leak.
    assert "scenes" not in maps.__dict__, "maps.py imported scenes; it must not"


# ─── the parts that are not the six, and are still load-bearing ───────────────

def test_a_malformed_place_is_named_rather_than_guessed(camp):
    """before fix: `ImportError: cannot import name 'scenes' from 'tactics'`.

    The `.cd` is hand-written and a hand-written file has a bad line in it. What
    this module must not do is read a coordinate out of the wrong line, or read
    none and leave a party at (0,0) with no error. A line that cannot be read is
    returned with an `error` and no `x`/`y`, so a caller that wants a coordinate
    has to notice.
    """
    cd = camp / "maps" / "chartdown" / "strixhaven-campus.cd"
    # Appended *inside* `[features]`, because a line after `[paths]` is a road and
    # is skipped on purpose. The distinction is the parser working rather than
    # the test being unlucky.
    cd.write_text(CAMPUS_CD.replace(
        "\n[paths]", '\nlandmark broken "Broken" : area (0,0) (10,10)\n\n[paths]'),
        encoding="utf-8")
    found = scenes().places(cd)
    assert found["broken"]["x"] is None and found["broken"]["y"] is None
    assert "not one point" in found["broken"]["error"]
    with pytest.raises(scenes().SceneError, match="not one point"):
        scenes().snap(scenes().blank(camp, "strixhaven-campus"), found["broken"])

    # The header is parsed, not grepped: a bad extent is refused with its text in
    # the message, because the extent is the denominator every fraction uses.
    cd.write_text(CAMPUS_CD.replace("extent: 1400x1895", "extent: very wide"),
                  encoding="utf-8")
    with pytest.raises(scenes().SceneError, match="is not WxH"):
        scenes().cd_header(cd)


def test_a_missing_scene_is_a_no_op_not_an_error(camp):
    """before fix: `ImportError: cannot import name 'scenes' from 'tactics'`
    for the two `scenes()` calls, and nothing at all for the first `assert`,
    which is the point: the pre-change code has no scene to be a no-op about.

    Every caller of `scenes().load` has to treat a campaign with no scene as an
    ordinary state. `cli._end` is the one that matters and it is pinned in
    `test_ending_combat_restores_the_scene`; this covers the rest of the surface,
    including the corrupt file, which is None rather than an exception because a
    GM who hand-edited one badly should still get a working terminal.
    """
    assert scenes().load(camp) is None
    assert scenes().ended_body(camp) == "*(none)*"

    (camp / "scene.json").write_text("{not json", encoding="utf-8")
    assert scenes().load(camp) is None
    assert scenes().ended_body(camp) == "*(none)*"

    spec = scenes().blank(camp, "strixhaven-campus")
    spec["version"] = 99
    scenes().save(camp, spec)
    assert scenes().load(camp) is None, "a file from a newer engine is not ours to read"


def test_a_scene_refuses_what_it_cannot_place(camp):
    """before fix: `ImportError: cannot import name 'scenes' from 'tactics'`.

    `validate` is the gate a marker passes before it reaches the file, so the
    failure modes worth naming are the ones that would otherwise be a party in
    the wrong country: a fraction outside 0..1, an extent that is not a pair, a
    map name that is a path.
    """
    spec = scenes().blank(camp, "strixhaven-campus")
    assert scenes().validate(spec) == []

    off = json.loads(json.dumps(spec))
    off["marker"]["x"] = 1.5
    assert any("outside 0..1" in p for p in scenes().validate(off))
    with pytest.raises(scenes().SceneError, match="refusing to save"):
        scenes().save(camp, off)

    unpaired = json.loads(json.dumps(spec))
    unpaired["extent"] = [1400.0]
    assert scenes().validate(unpaired)

    traversal = json.loads(json.dumps(spec))
    traversal["map"] = "../../etc/passwd"
    assert scenes().validate(traversal)
    assert "plain slug" in str(scenes().validate(traversal))