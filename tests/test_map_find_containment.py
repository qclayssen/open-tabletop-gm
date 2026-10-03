"""`maps._find()` takes a path as well as a slug, so the path needs a boundary.

    def _find(name: str) -> pathlib.Path:
        p = pathlib.Path(name)
        if p.suffix == ".json" and p.exists():
            return p

That first branch was the only one in the module that could leave `MAPS_DIR`,
and it had no containment in it. `name` is a caller-supplied string all the way
down from `tactics/cli.py --map` and `map_catalog.describe()`, so `../x.json` or
an absolute path resolved, and `load()` then handed whatever was there to
`json.loads`.

**This is not reachable over HTTP, and that is stated first because it sets the
severity.** The map routes and `map_to_atlas.py` validate ids before calling in;
`docs/guides/backlog-state.md` §6 reached the same conclusion when it surveyed
this area. So this is defence in depth on a local-CLI surface, not a
vulnerability with a live path to a user. It is filed because §6 recorded the
weakness and then, correctly, did not file it.

What makes it worth the four lines is that `_find` is the one function every map
load in the tree funnels through, so it is the cheapest place to put the
boundary and the most expensive to rediscover later if anyone ever exposes a
map-picking route.

The repo already had the answer one file over. `mapeditor.find()` is
"Deliberately narrower than maps._find" for exactly this reason -- it *writes*,
so a name must never be a way to choose a file. The difference was that
`mapeditor` said so and `maps` did not.

These tests pin three things, one per branch of `_find`, plus the ordering that
makes the check mean anything: containment is decided *after* `resolve()`, so
`..`, an absolute path and a symlink out of the tree are one question with one
answer.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tactics import maps

# The smallest thing `compile_map` accepts, written out so these tests do not
# depend on a shipped map staying byte-identical.
TINY = {"name": "Test Pond", "width": 3, "height": 2,
        "base": "floor", "features": [], "spawns": []}


@pytest.fixture
def maps_dir(tmp_path, monkeypatch):
    """Point the module's authority at a temporary directory.

    `_find` resolves containment against `MAPS_DIR`, so redirecting that one
    name relocates the boundary and lets a test plant a real file *outside* it.
    A test that wrote into the repo's own `display/maps/` would be testing the
    wrong thing: the interesting input is a file that exists and is not ours.
    """
    root = tmp_path / "maps"
    root.mkdir()
    monkeypatch.setattr(maps, "MAPS_DIR", root)
    monkeypatch.setattr(maps, "_MAPS_ROOT", root.resolve())
    return root


def _write(path: pathlib.Path, spec: dict) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


# ─── branch 1: the path branch is now contained ───────────────────────────────

def test_a_path_inside_the_maps_dir_still_loads(maps_dir):
    """The convenience branch is not the bug; the missing boundary was.

    `tactics show display/maps/frog-pond.json` from a repo-root shell is a real
    thing a GM types, and it worked. Removing the branch would satisfy the
    issue's acceptance criteria by deleting the feature, which is not what it
    asked for.
    """
    inside = _write(maps_dir / "test-pond.json", TINY)

    assert maps.load(str(inside))["meta"]["name"] == "Test Pond"
    assert maps._find(str(inside)) == inside.resolve()


def test_a_shipped_map_still_loads_by_its_own_absolute_path():
    """The same branch, against a file this module really owns.

    `maps_dir` redirects the boundary at a temporary directory, which would let
    a broken containment pass every other test in this file. This one refuses
    the redirect and asks about a real shipped map, so a `_find` that had been
    rewritten to compare against a hardcoded or relative root fails here.
    """
    target = maps.MAPS_DIR / "blank.json"

    assert target.is_file(), f"{target} is not a shipped map any more"
    assert maps.load(str(target.resolve()))["meta"]["slug"] == "blank"


def test_a_dotdot_traversal_is_refused_even_though_the_file_exists(maps_dir, tmp_path):
    """The case in the issue: the file is real, loadable JSON, and not ours.

    Pre-fix this returned `tmp_path/"outside.json"` and `load()` read it. The
    assertion is on the refusal *and* on the file being genuinely present, so a
    `p.exists()` that went false cannot make this pass.
    """
    outside = _write(tmp_path / "outside.json", TINY)
    escape = maps_dir / ".." / outside.name
    assert escape.exists(), "the traversal target must exist or this proves nothing"

    with pytest.raises(FileNotFoundError) as exc:
        maps._find(str(escape))
    assert "not inside" in str(exc.value)


def test_a_deeper_traversal_is_refused(maps_dir, tmp_path):
    """`../../` as well as `../`: a checker that only strips one segment is a
    checker that has not been met.

    The target sits two levels above the maps directory, which is `tmp_path`'s
    parent, so the `..` count in `escape` is real rather than cosmetic.
    """
    outside = _write(tmp_path.parent / "deeper" / "further" / "outside.json", TINY)
    escape = maps_dir / ".." / ".." / "deeper" / "further" / "outside.json"
    assert escape.resolve() == outside.resolve()
    assert escape.resolve().is_file()

    with pytest.raises(FileNotFoundError, match="not inside"):
        maps._find(str(escape))


def test_an_absolute_path_outside_the_maps_dir_is_refused(maps_dir, tmp_path):
    """`pathlib` does not care that the string has no `..` in it."""
    outside = _write(tmp_path / "outside.json", TINY)

    with pytest.raises(FileNotFoundError, match="not inside"):
        maps._find(str(outside))


def test_a_symlink_out_of_the_maps_dir_is_refused(maps_dir, tmp_path):
    """Why the check resolves first and compares second.

    The path reads `display/maps/innocent.json`, the directory is the maps
    directory, and every string comparison says yes. Only `resolve()` sees that
    the file lives somewhere else. A containment check that compared strings
    would pass this, which is why the order is the test.
    """
    outside = _write(tmp_path / "outside.json", TINY)
    link = maps_dir / "innocent.json"
    link.symlink_to(outside)

    with pytest.raises(FileNotFoundError, match="not inside"):
        maps._find(str(link))


def test_the_refusal_names_both_the_path_and_the_directory(maps_dir, tmp_path):
    """A GM who typed a path deserves to be told which directory it had to be in.

    The old failure mode for a bad path was `no map '../../x.json'`, which reads
    as "no such map" and sends the reader looking for a typo in a slug.
    """
    outside = _write(tmp_path / "outside.json", TINY)

    with pytest.raises(FileNotFoundError) as exc:
        maps._find(str(outside))
    message = str(exc.value)
    assert str(outside) in message
    assert str(maps_dir) in message


def test_a_refused_path_never_reaches_json_loads(maps_dir, tmp_path):
    """The property that matters, stated at the seam rather than at `_find`.

    Pre-fix, `_find` returned the path and `load()` read it. A test that only
    asserts `_find` raises could be satisfied by a `_find` that raises *after*
    loading; this one watches `load()`, which is what every caller actually
    calls.
    """
    outside = _write(tmp_path / "outside.json", TINY)

    with pytest.raises(FileNotFoundError):
        maps.load(str(outside))


def test_the_containment_helper_cannot_be_satisfied_by_a_string_prefix(maps_dir, tmp_path):
    """`is_relative_to`, proved on the sibling that a prefix test would accept.

    `/tmp/maps-evil/x.json` starts with `/tmp/maps`, so a `str(...).startswith()`
    check says yes. This is the assertion that separates the two
    implementations, and it is why the helper is a `Path` question and not a
    string one.
    """
    sibling = tmp_path / f"{maps_dir.name}-evil"
    escape = _write(sibling / "outside.json", TINY)

    assert str(escape).startswith(str(maps_dir))     # the trap this test sets
    assert not maps._owned_by_maps_dir(escape)
    assert not maps._owned_by_maps_dir(maps_dir / ".." / "outside.json")


# ─── branches 2 and 3: the ordinary lookups must not regress ──────────────────

def test_a_slug_still_resolves(maps_dir):
    """Branch two, and the common path. Untouched by the fix, and pinned so it
    cannot be broken by it."""
    _write(maps_dir / "test-pond.json", TINY)

    assert maps._find("test-pond") == (maps_dir / "test-pond.json").resolve()


def test_a_slug_is_found_even_though_a_same_named_file_exists_outside(maps_dir, tmp_path):
    """Containment must not cost the fallback branches their reach.

    A `../test-pond.json` now raises, and that has to stop at the first branch
    rather than being allowed to fall through into the slug lookup -- so a
    traversal cannot quietly resolve to an unrelated in-tree map of the same
    name. Both halves of that are asserted here.
    """
    outside = _write(tmp_path / "test-pond.json", {"name": "The Wrong One"})
    inside = _write(maps_dir / "test-pond.json", TINY)
    escape = maps_dir / ".." / "test-pond.json"

    with pytest.raises(FileNotFoundError, match="not inside"):
        maps._find(str(escape))
    assert maps._find("test-pond") == inside.resolve()
    assert json.loads(outside.read_text(encoding="utf-8"))["name"] == "The Wrong One"


def test_a_display_name_still_resolves(maps_dir):
    """Branch three, matched by the map's own `"name"`."""
    _write(maps_dir / "test-pond.json", TINY)

    assert maps._find("Test Pond") == (maps_dir / "test-pond.json").resolve()


def test_a_display_name_lookup_cannot_escape_either(maps_dir, tmp_path):
    """The display-name loop globs `MAPS_DIR`, so it was never the hole.

    Asserted anyway, because "the glob makes it safe" is the kind of reasoning
    that stops being true when someone adds a second glob. A file outside the
    directory whose `name` matches must not be returned.
    """
    outside = _write(tmp_path / "elsewhere.json", {"name": "Test Pond",
                                                   "width": 3, "height": 2,
                                                   "base": "floor", "features": []})
    _write(maps_dir / "test-pond.json", TINY)

    found = maps._find("Test Pond")
    assert found == (maps_dir / "test-pond.json").resolve()
    assert found != outside.resolve()


def test_an_unknown_name_still_reports_the_available_maps(maps_dir):
    """The error a GM actually reads is unchanged."""
    _write(maps_dir / "test-pond.json", TINY)

    with pytest.raises(FileNotFoundError, match="Maps:.*test-pond"):
        maps._find("atlantis")
