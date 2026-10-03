"""The advisor briefs have one source of truth, and drift is a test failure.

WHY (#T3)
=========
The briefs existed in two hand-edited copies with nothing between them, and
had already drifted twice. A test that only compares two directories that
happen to sit side by side on one machine does not close that hole, because
CI clones this repo alone and the comparison it would make is against
nothing. So there are two detectors here, and neither is enough alone:

1. the committed hash manifest, which records the outer copy as the last sync
   left it, and is checkable with no outer checkout at all;
2. the live byte comparison, which catches a hand-edit of the outer copy.

Plus the generation itself: `sync()` overwrites the outer copy from the
briefs, so the losing side cannot be edited in place and stay edited.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import advisor, briefs_sync  # noqa: E402


def _scratch(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A throwaway copy of the real briefs and a throwaway outer agents/.

    The real pairs cannot be used: a test that proves drift is detected has to
    create drift, and creating drift in the repository's own files would leave
    the repository dirty and would make the test order-dependent.
    """
    briefs = tmp_path / "briefs"
    briefs.mkdir()
    for src in briefs_sync.BRIEFS.glob("*.md"):
        (briefs / src.name).write_bytes(src.read_bytes())
    agents = tmp_path / "agents"
    agents.mkdir()
    # The dev council sits in a subdirectory, so creating it exercises the
    # parent-mkdir that a flat fixture would never need.
    for name in briefs_sync.DEV_COUNCIL:
        target = agents / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name}\n", encoding="utf-8")
    for src in briefs.glob("*.md"):
        (agents / src.name).write_bytes(src.read_bytes())
    return briefs, agents


def test_briefs_directory_is_exactly_the_council_advisor_py_can_ask():
    assert briefs_sync.council_problems() == []
    names = {n[: -len(".md")] for n in briefs_sync.brief_names()}
    assert names == set(advisor.ADVISORS) | {"_shared"}


def test_committed_manifest_matches_every_brief():
    assert briefs_sync.check_manifest() == []


def test_manifest_catches_a_brief_edited_without_syncing(tmp_path, monkeypatch):
    """The failure this whole mechanism exists for, on the CI-only path.

    A brief changes in this repo and the outer copy is not refreshed. Nothing
    in a plain clone can see that, which is why the manifest is committed.
    """
    briefs, agents = _scratch(tmp_path)
    path = tmp_path / "manifest.json"
    briefs_sync.write_manifest(agents, path)
    assert briefs_sync.check_manifest(briefs, path) == []

    # A brief changes here; the outer copy is not refreshed.
    target = briefs / "arbiter.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n- a new rule\n",
                      encoding="utf-8")

    problems = briefs_sync.check_manifest(briefs, path)
    assert [p for p in problems if "arbiter.md" in p and "changed" in p]
    # And the live comparison catches it too, when both checkouts are there.
    assert any("arbiter.md" in p for p in briefs_sync.compare(briefs, agents))


def test_manifest_notice_of_an_unrecorded_brief(tmp_path):
    briefs, agents = _scratch(tmp_path)
    path = tmp_path / "manifest.json"
    briefs_sync.write_manifest(agents, path)
    (briefs / "new-lens.md").write_text("# New\n", encoding="utf-8")
    assert any("new-lens.md" in p and "never synced" in p
               for p in briefs_sync.check_manifest(briefs, path))


def test_compare_catches_drift_in_either_direction(tmp_path):
    briefs, agents = _scratch(tmp_path)
    assert briefs_sync.compare(briefs, agents) == []

    # The source side edited, the generated copy left behind.
    (briefs / "designer.md").write_text("# Different\n", encoding="utf-8")
    assert any(p.startswith("drifted: designer.md") for p in briefs_sync.compare(briefs, agents))

    (briefs / "designer.md").write_bytes((agents / "designer.md").read_bytes())

    # The generated copy hand-edited, which is how the second drift happened.
    (agents / "designer.md").write_text("# Hand-edited\n", encoding="utf-8")
    assert any(p.startswith("drifted: designer.md") for p in briefs_sync.compare(briefs, agents))


def test_compare_catches_a_missing_brief_and_an_unexpected_file(tmp_path):
    briefs, agents = _scratch(tmp_path)
    (agents / "referee.md").unlink()
    assert any("missing from" in p and "referee.md" in p
               for p in briefs_sync.compare(briefs, agents))

    (agents / "referee.md").write_text("# Back\n", encoding="utf-8")
    (agents / "bard.md").write_text("# A new lens nobody declared\n", encoding="utf-8")
    assert any("unrecognised file" in p and "bard.md" in p
               for p in briefs_sync.compare(briefs, agents))


def test_sync_regenerates_the_outer_copy_and_never_deletes(tmp_path):
    briefs, agents = _scratch(tmp_path)
    (agents / "designer.md").write_text("# Hand-edited\n", encoding="utf-8")
    (agents / "bard.md").write_text("# Not a declared lens\n", encoding="utf-8")
    (briefs / "arbiter.md").write_text("# A rule that exists only on the source side\n",
                                       encoding="utf-8")

    written = briefs_sync.sync(briefs, agents, tmp_path / "manifest.json")

    assert "arbiter.md" in written and "designer.md" in written
    assert (agents / "designer.md").read_bytes() == (briefs / "designer.md").read_bytes()
    # No brief is left drifted. The undeclared file is still flagged, because
    # a sync is a copy, not a prune, and silence would read as approval.
    assert [p for p in briefs_sync.compare(briefs, agents) if not p.startswith("unrecognised")] == []
    assert any("bard.md" in p for p in briefs_sync.compare(briefs, agents))
    # The dev-council briefs and anything unrecognised survive: pruning would
    # delete a brief someone has not classified yet, which is the destructive
    # failure mode of a generator that owns its output directory. A dev brief
    # that moved is the same case one level down.
    assert (agents / "bard.md").exists()
    for name in briefs_sync.DEV_COUNCIL:
        assert (agents / name).exists()
    (agents / briefs_sync.DEV_DIR / "programmer.md").write_text(
        "# Undeclared\n", encoding="utf-8")
    written = briefs_sync.sync(briefs, agents, tmp_path / "manifest.json")
    assert written == []
    assert (agents / briefs_sync.DEV_DIR / "programmer.md").exists()


def test_sync_without_an_agents_dir_says_so_instead_of_guessing(tmp_path):
    with pytest.raises(ValueError, match="no outer agents"):
        briefs_sync.sync(briefs_sync.BRIEFS, tmp_path / "nope", tmp_path / "m.json")


def test_outer_agents_dir_discovery_ignores_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.delenv(briefs_sync.ENV_AGENTS_DIR, raising=False)
    assert briefs_sync.outer_agents_dir(tmp_path / "nope") is None
    monkeypatch.setenv(briefs_sync.ENV_AGENTS_DIR, str(tmp_path))
    assert briefs_sync.outer_agents_dir() == tmp_path
    monkeypatch.setenv(briefs_sync.ENV_AGENTS_DIR, str(tmp_path / "nope"))
    assert briefs_sync.outer_agents_dir() is None


def test_explicit_missing_agents_dir_fails_and_names_path(tmp_path, capsys):
    missing = tmp_path / "not-there"

    assert briefs_sync.main(["--check", "--agents-dir", str(missing)]) != 0
    output = capsys.readouterr().out
    assert str(missing) in output
    assert "advisor briefs are in sync" not in output


def test_explicit_non_directory_agents_dir_fails_and_names_path(tmp_path, capsys):
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("not a directory", encoding="utf-8")

    assert briefs_sync.main(["--check", "--agents-dir", str(not_a_directory)]) != 0
    output = capsys.readouterr().out
    assert str(not_a_directory) in output
    assert "advisor briefs are in sync" not in output


def test_no_agents_dir_argument_keeps_manifest_only_check(tmp_path, monkeypatch, capsys):
    """The documented case, and the reason this is not just "always fail".

    A plain clone of this repo has no outer checkout, so `--check` cannot answer
    the question at all; the committed manifest is the authority there. Degrading
    to a manifest-only check is correct, and the note says so. Making it loud
    instead would have broken the documented CI story to fix a different bug.
    """
    monkeypatch.setattr(briefs_sync, "outer_agents_dir", lambda _agents_dir=None: None)
    monkeypatch.setattr(briefs_sync, "council_problems", lambda: [])
    monkeypatch.setattr(briefs_sync, "check_manifest", lambda: [])

    assert briefs_sync.main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "no outer agents/ directory found" in output
    assert "advisor briefs are in sync" in output


def test_an_env_named_agents_dir_that_is_not_a_directory_fails(tmp_path, monkeypatch, capsys):
    """The same hole by the other route in.

    `ENV_AGENTS_DIR` is not discovery. Someone set it, on purpose, to point at a
    sibling checkout -- it is how the outer repo's verification reaches one from
    inside a worktree. A stale value is a mistake worth reporting, and it
    produced exactly the false "in sync" the flag now refuses.
    """
    monkeypatch.setattr(briefs_sync, "council_problems", lambda: [])
    monkeypatch.setattr(briefs_sync, "check_manifest", lambda: [])
    monkeypatch.setenv(briefs_sync.ENV_AGENTS_DIR, str(tmp_path / "gone"))

    assert briefs_sync.main(["--check"]) != 0
    output = capsys.readouterr().out
    assert str(tmp_path / "gone") in output
    assert "advisor briefs are in sync" not in output


def test_a_valid_env_named_agents_dir_still_compares(tmp_path, monkeypatch, capsys):
    """The env route must keep working, or the guard above is a trap.

    `_scratch` gives a throwaway pair of directories, so this does not skip when
    the outer checkout is not beside the code repo -- which is what made the
    two-repo verification runnable by hand in the first place.
    """
    _, agents = _scratch(tmp_path)
    monkeypatch.setattr(briefs_sync, "council_problems", lambda: [])
    monkeypatch.setattr(briefs_sync, "check_manifest", lambda: [])
    monkeypatch.setenv(briefs_sync.ENV_AGENTS_DIR, str(agents))

    assert briefs_sync.main(["--check"]) == 0
    output = capsys.readouterr().out
    assert f"compared against {agents}" in output
    assert "advisor briefs are in sync" in output


def test_a_drifted_env_named_agents_dir_is_reported(tmp_path, monkeypatch, capsys):
    """The other half: a named directory that *is* there still gets compared, so
    the stricter exit did not cost the env route its actual job."""
    _, agents = _scratch(tmp_path)
    (agents / "_shared.md").write_text("drifted\n", encoding="utf-8")
    monkeypatch.setattr(briefs_sync, "council_problems", lambda: [])
    monkeypatch.setattr(briefs_sync, "check_manifest", lambda: [])
    monkeypatch.setenv(briefs_sync.ENV_AGENTS_DIR, str(agents))

    assert briefs_sync.main(["--check"]) != 0
    assert "drifted: _shared.md" in capsys.readouterr().out


def test_install_agents_cannot_be_reached_by_the_stricter_check(tmp_path, capsys):
    """Acceptance point 3, checked rather than argued.

    `install_agents.py` drives this script with `[..., "--agents-dir", PATH]` and
    **no `--check`**, so it always takes the generation half. `sync()` resolves
    the same directory and raises `ValueError` on one that is not there, which
    `main()` has already turned into exit 1 since before this fix. So the stricter
    `--check` exit cannot reach any of `install_agents.py`'s three states, and
    `--no-sync` still bypasses the script entirely.

    If someone later adds `--check` to that call, this test is the one that says
    the state table has to be revisited.
    """
    missing = tmp_path / "not-there"

    # The generation half, which is the one install_agents.py calls.
    assert briefs_sync.main(["--agents-dir", str(missing)]) != 0
    assert str(missing) in capsys.readouterr().out


def test_declared_dev_council_is_the_set_the_outer_repo_actually_carries():
    """The dev-council list is a claim about another repo, so it is checked.

    A hard-kept list of the outer repo's files is only trustworthy while
    somebody checks it, and there is no test in this repo that would fail if
    the outer repo gained or lost one.
    """
    agents = briefs_sync.outer_agents_dir()
    if agents is None:
        pytest.skip("outer agents/ is not beside this checkout")
    present = set(briefs_sync.outer_names(agents))
    briefs = set(briefs_sync.brief_names())
    known = set(briefs_sync.DEV_COUNCIL) | set(briefs_sync.OUTER_DOCS)
    assert present - briefs == known


def test_an_unrecognised_file_in_the_dev_directory_is_reported(tmp_path):
    """`dev/` is hand-maintained, so a brief can land there without being declared.

    The top-level equivalent of this is covered above; the subdirectory is the
    likelier place for it to happen, because nothing generates that directory and
    the declaration lives in another repo.
    """
    briefs, agents = _scratch(tmp_path)
    assert briefs_sync.compare(briefs, agents) == []

    (agents / briefs_sync.DEV_DIR / "programmer.md").write_text(
        "# A dev brief nobody declared\n", encoding="utf-8")
    assert any("unrecognised" in p and "programmer.md" in p
               for p in briefs_sync.compare(briefs, agents))


def test_outer_names_covers_both_levels(tmp_path):
    """The dev subdirectory is included, or the install and the check both miss it."""
    agents = tmp_path / "agents"
    (agents / briefs_sync.DEV_DIR).mkdir(parents=True)
    (agents / "arbiter.md").write_text("x", encoding="utf-8")
    (agents / briefs_sync.DEV_DIR / "engineer.md").write_text("x", encoding="utf-8")

    assert set(briefs_sync.outer_names(agents)) == {
        "arbiter.md", f"{briefs_sync.DEV_DIR}/engineer.md"}


def test_the_two_live_copies_are_in_sync_when_both_checkouts_are_present():
    agents = briefs_sync.outer_agents_dir()
    if agents is None:
        pytest.skip("outer agents/ is not beside this checkout")
    problems = briefs_sync.compare(briefs_sync.BRIEFS, agents)
    assert problems == [], (
        "the outer copy has drifted from the briefs. Do not fix this by editing "
        "agents/ -- it is generated. Run briefs_sync.py, and check first whether "
        "the outer copy holds someone's unsynced work: a hand-edit there is the "
        f"failure this module was built to make visible.\n  {problems}")


def test_an_outer_document_is_not_reported_as_an_unregistered_brief(tmp_path):
    """`agents/README.md` documents the council; it is not a brief nobody asked for.

    Without this, the index for the two halves would itself be a drift report on
    every sync, which is the fastest way to get a check people learn to ignore.
    """
    briefs, agents = _scratch(tmp_path)
    (agents / "README.md").write_text("# The advisor council\n", encoding="utf-8")
    assert briefs_sync.compare(briefs, agents) == []

    (agents / "NOTES.md").write_text("# Not declared anywhere\n", encoding="utf-8")
    assert any("unrecognised" in p and "NOTES.md" in p
               for p in briefs_sync.compare(briefs, agents))
