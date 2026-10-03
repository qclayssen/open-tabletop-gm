"""A formation is the creatures; a map is the room. What that has to guarantee.

`map_to_atlas.py` grew `--formation` for one reason, recorded in the roadmap as
BV9: *"the blocker is pixels, not code."* Every map with artwork had no creatures
to link a statblock to, and every map with creatures was a gridless hand-written
map, so no scene could carry a `statblockPath` and the link path — proven against
the real 334-note bestiary — had nothing to attach to. The stated fix was to
import artwork. This is the other fix, and it needs no pixels at all: a formation
is campaign data that applies to *any* map, so it breaks the disjointness.

The properties defended here:

  1. **The preview and the fight use one placement function.** The exporter calls
     `tactics.formations.positions`, not a second implementation of it. A preview
     that placed tokens differently from the fight would be worse than no
     preview, and the only real guarantee against that is a single function with
     two callers — so this is asserted structurally, not just behaviourally.
  2. **`build_scene` is untouched.** 38 existing tests describe it exactly, and
     the formation path works by synthesising *spawns* and handing them to the
     same code rather than by growing a second scene builder.
  3. **Nothing is written back.** One-way stays one-way: the map file is not
     modified and the formation store is not modified, even when the replay had
     to move a monster.
  4. **A formation that does not fit is loud.** Blocked, off-map, separated and
     unplaced are all reported, because the symptom otherwise is a token standing
     in a bookcase in someone else's Obsidian.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import map_to_atlas                                       # noqa: E402
import tactics_fixtures as fx                             # noqa: E402
from tactics import formations                            # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "m2a_under_test", ROOT / "scripts" / "map_to_atlas.py")
m2a = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m2a)

# A terrain-only map with a grid block and no spawns: exactly the shape BV9's
# no-artwork path is for, and the shape that could not previously carry a
# statblock because it had nothing to attach one to.
TERRAIN_ONLY = {
    "name": "Test Hall", "width": 20, "height": 14, "diagonals": "5",
    "base": "floor",
    "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0},
    "features": [{"type": "wall", "x": 0, "y": 0, "w": 20, "h": 1}],
    "spawns": [],
}


@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    d.mkdir(parents=True)
    # A state.md, or it is not a campaign: map_to_atlas resolves through
    # find_campaign, which validates the configured root, so an empty directory
    # is a miss rather than a campaign (see
    # tests/test_paths_campaign_resolution.py). This fixture was a two-entry
    # shell -- the exact shape that made the name-only guard pass.
    (d / "state.md").write_text("# Demo\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    return d


@pytest.fixture
def vault(tmp_path):
    """A vault with a real bestiary folder, because `bestiary_index` scans the
    directory and a link is only ever returned for a file that exists."""
    v = tmp_path / "vault"
    (v / "Bestiary").mkdir(parents=True)
    (v / "Bestiary" / "Kobold.md").write_text("---\nstatblock: true\n---\n# Kobold\n",
                                              encoding="utf-8")
    (v / "Bestiary" / "Giant Frog.md").write_text("---\nstatblock: true\n---\n# Giant Frog\n",
                                                 encoding="utf-8")
    return v


def _formation(camp, name="Pair", cols=(2, 3), rows=30, height=20):
    enc = fx.encounter([fx.goblin("goblin-1", (cols[0], 10)),
                        fx.goblin("goblin-2", (cols[1], 10))],
                       rows=["." * rows] * height)
    formations.save(camp, formations.capture(enc, name))
    return name


# ─── 1. one placement function ────────────────────────────────────────────────

def test_the_exporter_calls_the_engines_placement_function(camp):
    """Structural, not behavioural. A copy of `positions` inside the exporter
    would pass every other test in this file and then drift, and the drift would
    only show up as a preview disagreeing with a fight nobody can reconstruct."""
    src = (ROOT / "scripts" / "map_to_atlas.py").read_text(encoding="utf-8")
    assert "F.positions(" in src, "the exporter must call tactics.formations.positions"
    assert "def positions" not in src, \
        "the exporter must not define its own placement function"
    # And it must build the grid the engine builds, not re-read the JSON.
    assert "compile_map" in src


def test_the_grid_the_exporter_places_on_is_the_engines_grid(camp):
    """If the two grids ever disagreed, tokens would land on squares the fight
    does not have — off by a whole terrain rectangle, silently."""
    from tactics.maps import compile_map
    from tactics.grid import Grid
    spec = dict(TERRAIN_ONLY)
    spec["features"] = [{"type": "wall", "x": 3, "y": 3, "w": 4, "h": 2}]
    board = Grid.from_dict(compile_map(spec)["grid"])
    name = _formation(camp)
    out, _ = m2a.apply_formations(spec, camp, [name], at=(1, 1))
    for s in out["spawns"]:
        assert (s["x"], s["y"]) in [(x, y) for y in range(14) for x in range(20)
                                    if board.in_bounds((x, y))]


# ─── 2. build_scene untouched, spawns synthesised ─────────────────────────────

def test_a_formation_becomes_ordinary_spawns(camp):
    """The whole mechanism. `build_scene` cannot tell a formation-supplied spawn
    from a hand-written one, which is why its 38 tests still describe it."""
    name = _formation(camp)
    spec, report = m2a.apply_formations(TERRAIN_ONLY, camp, [name], at=(5, 5))
    assert [s["name"] for s in spec["spawns"]] == ["Goblin", "Goblin"]
    assert all(s["color"] for s in spec["spawns"])
    assert report[0]["formation"] == "Pair"
    assert report[0]["tokens"] == 2


def test_the_map_file_is_never_modified(camp, tmp_path):
    """One-way stays one-way. A formation is campaign data, not a property of the
    map, and writing it into the map would put campaign state in a shared format."""
    name = _formation(camp)
    spec = dict(TERRAIN_ONLY)
    before = json.dumps(spec, sort_keys=True)
    m2a.apply_formations(spec, camp, [name], at=(5, 5))
    assert json.dumps(spec, sort_keys=True) == before


def test_the_formation_store_is_never_modified(camp):
    """Same reason, other direction: replaying a formation must not consume it
    or normalise it, or a second export would differ from the first."""
    name = _formation(camp)
    path = formations.path_for(camp, name)
    before = path.read_text(encoding="utf-8")
    m2a.apply_formations(TERRAIN_ONLY, camp, [name], at=(5, 5))
    assert path.read_text(encoding="utf-8") == before


def test_map_spawns_and_formation_spawns_coexist(camp):
    """A map may already have its own residents; the formation is added, not
    substituted. The garden is party-only and the stacks have kobolds, and
    sometimes a fight wants both."""
    name = _formation(camp)
    spec = dict(TERRAIN_ONLY, spawns=[{"id": "K", "name": "Kairos",
                                       "color": "quan", "x": 1, "y": 1}])
    out, _ = m2a.apply_formations(spec, camp, [name], at=(5, 5))
    ids = [s["id"] for s in out["spawns"]]
    assert ids[0] == "K"
    assert len(ids) == 3
    assert len(set(ids)) == 3, f"Atlas keys tokens by id; duplicates: {ids}"


def test_no_formation_means_no_change(camp):
    """The default path must be byte-identical to before this feature existed."""
    spec = dict(TERRAIN_ONLY, spawns=[{"id": "K", "name": "Kairos",
                                       "color": "quan", "x": 1, "y": 1}])
    out, report = m2a.apply_formations(spec, camp, [])
    assert out == spec
    assert report == []


# ─── 3. statblock links, the point of the whole thing ────────────────────────

def test_formation_creatures_link_to_real_bestiary_notes(camp, vault, tmp_path):
    """BV9, asserted. A terrain-only map with no spawns cannot carry a
    statblock; a terrain-only map plus a formation can. The note path is checked
    against the real directory listing, so a link that points at nothing fails
    here rather than in someone else's Obsidian."""
    enc = fx.encounter([fx.frog("frog-1", (5, 5), name="Giant Frog"),
                        fx.frog("frog-2", (6, 5), name="Giant Frog 2")],
                       rows=["." * 20] * 14)
    formations.save(camp, formations.capture(enc, "Frogs"))
    spec, _ = m2a.apply_formations(TERRAIN_ONLY, camp, ["frogs"], at=(5, 5))

    tokens = {"danger": {"vault_path": "atlas-vtt/x/tokens/danger.png",
                         "per_spawn": {},
                         "statblocks": {}}}
    tokens["danger"]["statblocks"] = {
        s["name"]: {"vault_path": note}
        for s in spec["spawns"]
        if (note := m2a.bestiary_note(vault, s["name"]))}

    scene = m2a.build_scene(spec, "test-hall", None, tokens)
    placed = scene["objects"]["tokens"]
    assert placed, "the formation produced no tokens"
    for t in placed.values():
        assert t["statblockPath"] == "Bestiary/Giant Frog.md", \
            f"token {t['id']} did not link; got {t.get('statblockPath')!r}"
    assert (vault / "Bestiary" / "Giant Frog.md").exists()


def test_a_formation_creature_keeps_a_readable_nameplate(camp, vault):
    """The `name`/`label` split, from the Atlas side, with a real link.

    A formation of three frogs stores `name: "Giant Frog"` — what the SRD lookup
    needs, and what `bestiary_note` folds to — and `label: "Giant Frog 1..3"`,
    what a person looking at the board wants to read. The exporter uses the label
    for the nameplate and the name for the link; getting it backwards shows three
    identical tokens, and not splitting at all links nothing for a formation of
    anything repeated, which is most formations.
    """
    enc = fx.encounter([fx.frog("frog-1", (5, 5), name="Giant Frog 1"),
                        fx.frog("frog-2", (6, 5), name="Giant Frog 2")],
                       rows=["." * 20] * 14)
    formations.save(camp, formations.capture(enc, "Frogs"))
    assert sorted(m["name"] for m in
                  formations.load(camp, "frogs")["members"]) == ["Giant Frog"] * 2

    spec, _ = m2a.apply_formations(TERRAIN_ONLY, camp, ["frogs"], at=(5, 5))
    names = sorted(s["name"] for s in spec["spawns"])
    assert names == ["Giant Frog 1", "Giant Frog 2"], \
        f"the nameplate should be the label, got {names}"
    # The link is built from the note folder, and resolves for each.
    for s in spec["spawns"]:
        assert m2a.bestiary_note(vault, s["name"]) == "Bestiary/Giant Frog.md", \
            f"{s['name']!r} did not resolve to a note"


def test_a_numbered_member_still_finds_its_creature_note(camp, vault):
    """`Giant Frog 2` is how the CLI names the second of two, and the bestiary
    has one `Giant Frog`. The fold drops the trailing number, and without it a
    formation of anything repeated links to nothing at all — which is most
    formations, since repeating a monster is the point of one."""
    enc = fx.encounter([fx.frog("frog-1", (5, 5), name="Giant Frog"),
                        fx.frog("frog-2", (6, 5), name="Giant Frog 2")],
                       rows=["." * 20] * 14)
    formations.save(camp, formations.capture(enc, "Frogs"))
    spec, _ = m2a.apply_formations(TERRAIN_ONLY, camp, ["frogs"], at=(5, 5))
    for s in spec["spawns"]:
        assert m2a.bestiary_note(vault, s["name"]), f"{s['name']} found no note"


# ─── 4. a formation that does not fit is loud ────────────────────────────────

def test_a_blocked_member_is_reported(camp):
    """The symptom otherwise is a token standing in a bookcase, noticed by a
    player rather than by us."""
    walled = dict(TERRAIN_ONLY)
    walled["features"] = [{"type": "wall", "x": 5, "y": 5, "w": 1, "h": 1}]
    name = _formation(camp)
    _, report = m2a.apply_formations(walled, camp, [name], at=(5, 5))
    assert report[0]["blocked"], "a formation pinned onto a wall must be reported"


def test_a_pinned_replay_off_the_map_is_reported(camp):
    tiny = dict(TERRAIN_ONLY, width=4, height=4, features=[])
    name = _formation(camp)
    _, report = m2a.apply_formations(tiny, camp, [name], at=(3, 3))
    assert report[0]["off_map"], "a formation that does not fit must be reported"


def test_a_collision_is_reported_separately_from_a_wall(camp):
    """Three different problems — blocked, off-map, separated — with three
    different fixes. Merging them into one "warning" list is how the wall case
    started moving monsters out of bookcases."""
    name = _formation(camp)
    _, report = m2a.apply_formations(dict(TERRAIN_ONLY, width=24, height=18),
                                     camp, [name], centre=True)
    assert "separated" in report[0]
    assert set(report[0]) >= {"blocked", "off_map", "separated"}


def test_a_clean_replay_reports_nothing_wrong(camp):
    """The quiet path has to actually be quiet, or nobody will ever read the
    loud one."""
    name = _formation(camp)
    _, report = m2a.apply_formations(TERRAIN_ONLY, camp, [name], at=(5, 5))
    assert report[0]["blocked"] == []
    assert report[0]["off_map"] == []
    assert report[0]["separated"] == []


# ─── the CLI surface ──────────────────────────────────────────────────────────

def test_formation_without_a_campaign_is_a_clear_refusal(capsys, tmp_path):
    """`--formation` needs a campaign to look in. A bare KeyError from deep in
    the loader is not a usable message for a GM at 9pm."""
    rc = m2a.main(["biblioplex-stacks", "--formation", "stacks-ambush",
                   "--allow-no-image", "--vault", str(tmp_path / "v")])
    assert rc == 1
    assert "--campaign" in capsys.readouterr().err


def test_a_missing_formation_names_the_ones_that_exist(capsys, tmp_path, camp):
    _formation(camp, "Stacks Ambush")
    from paths import find_campaign
    root = pathlib.Path(camp).parent.parent
    rc = m2a.main(["biblioplex-stacks", "--formation", "bog-ambush",
                   "--campaign", "demo", "--allow-no-image",
                   "--vault", str(tmp_path / "v")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "stacks-ambush" in err, "the error should list what does exist"
    assert find_campaign is not None and root is not None


def test_a_bad_anchor_is_refused_before_anything_is_written(capsys, tmp_path, camp):
    """A typo in `--at` must not leave half a scene in the vault."""
    _formation(camp)
    v = tmp_path / "v"
    rc = m2a.main(["biblioplex-stacks", "--formation", "pair", "--campaign", "demo",
                   "--at", "not-a-square", "--allow-no-image", "--vault", str(v)])
    assert rc == 1
    assert "--at" in capsys.readouterr().err
    assert not v.exists() or not list(v.rglob("*.atlasmap"))
