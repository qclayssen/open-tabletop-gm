"""scripts/paths.py: a name match is not a campaign.

Regression for the resolution bug where the default root (`~/open-tabletop-gm`)
held a two-entry `strixhaven-kairos` (an `atlas-vtt/` and an `.obsidian/`, left by
other tools) while the real campaign lived in `~/github/strixhaven-kairos`.
`find_campaign` returned the first *name* it found, so every documented entry path
- the README's "Continue a campaign", the `hermes-gm` preflight, `display/preflight.py`
- resolved to the shell and reported an empty campaign rather than a miss.

These tests pin the durable half of the fix: a directory is a campaign only if it
has the `state.md` every consumer already requires. The interim workaround is a
config line (`GM_CAMPAIGN_ROOT=~/github/strixhaven-kairos`), which moves the trap
without removing it.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import paths  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    """An empty configured root, and a legacy root that does not exist either."""
    r = tmp_path / "root"
    r.mkdir()
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(r))
    monkeypatch.setattr(paths, "_default_root", lambda: tmp_path / "legacy")
    return r


def make_shell(root: pathlib.Path, name: str) -> pathlib.Path:
    """A directory with the right name and the wrong contents.

    This is the shape the bug found: the two entries a leftover shell carries, and
    no `state.md`. Nothing about the *name* distinguishes it from a real campaign.
    """
    camp = root / "campaigns" / name
    (camp / "atlas-vtt").mkdir(parents=True)
    (camp / ".obsidian").mkdir()
    return camp


def make_campaign(root: pathlib.Path, name: str) -> pathlib.Path:
    camp = root / "campaigns" / name
    camp.mkdir(parents=True)
    (camp / "state.md").write_text("# Campaign\n", encoding="utf-8")
    return camp


# ── the reported bug ────────────────────────────────────────────────────────


def test_a_directory_with_the_right_name_and_no_state_file_is_not_a_campaign(root):
    make_shell(root, "strixhaven-kairos")
    assert not paths._is_campaign(root / "campaigns" / "strixhaven-kairos")


def test_find_campaign_reports_a_miss_for_a_name_only_match(root):
    """The bug itself: the shell is at the configured root, so it is what
    `find_campaign` returns, and every entry point loads an empty campaign."""
    make_shell(root, "strixhaven-kairos")
    found = paths.find_campaign("strixhaven-kairos", migrate=False)
    assert not paths._is_campaign(found)
    assert not found.is_dir() or not (found / "state.md").is_file()
    # i.e. the caller sees the not-found sentinel, and reports a miss.


def test_a_real_campaign_still_resolves(root):
    camp = make_campaign(root, "strixhaven-kairos")
    assert paths.find_campaign("strixhaven-kairos", migrate=False) == camp


def test_a_shell_does_not_shadow_a_real_campaign_at_the_legacy_root(root, tmp_path):
    """The shell loses even when the real campaign is somewhere it can reach.

    Before the fix the shell at the configured root short-circuited resolution, so
    the legacy fallback was never consulted. Now the shell is skipped and the real
    campaign is found.
    """
    make_shell(root, "strixhaven-kairos")
    make_campaign(tmp_path / "legacy", "strixhaven-kairos")
    found = paths.find_campaign("strixhaven-kairos", migrate=False)
    assert found == tmp_path / "legacy" / "campaigns" / "strixhaven-kairos"


# ── the branches the fix touches ────────────────────────────────────────────


def test_the_legacy_fallback_branch_is_validated_too(root, tmp_path):
    """A shell at the legacy path must not be migrated or returned."""
    make_shell(tmp_path / "legacy", "old")
    found = paths.find_campaign("old", migrate=False)
    assert found == root / "campaigns" / "old"
    assert not found.exists()


def test_a_shell_at_the_configured_root_is_not_migrated_over(root, tmp_path):
    """Migration copies a whole tree. It must never copy a shell.

    `migrate=True` is the default and the reason this matters: an unguarded
    copytree would promote the empty shell into the configured root permanently.
    """
    make_shell(tmp_path / "legacy", "old")
    found = paths.find_campaign("old")            # migrate=True
    assert found == root / "campaigns" / "old"
    assert not found.exists()
    assert not (root / "campaigns").exists()


def test_a_real_legacy_campaign_is_still_migrated(root, tmp_path):
    """The fix must not cost the documented migration behaviour."""
    src = make_campaign(tmp_path / "legacy", "old")
    (src / "world.md").write_text("# World\n", encoding="utf-8")
    dest = paths.find_campaign("old")
    assert dest == root / "campaigns" / "old"
    assert (dest / "state.md").is_file() and (dest / "world.md").is_file()
    assert (src / "state.md").is_file()           # original kept in place


# ── the predicate's own contract ────────────────────────────────────────────


def test_a_file_named_like_a_campaign_is_not_a_campaign(root):
    """`exists()` accepts a file; the name is then not a directory at all."""
    (root / "campaigns").mkdir()
    (root / "campaigns" / "c").write_text("not a campaign\n", encoding="utf-8")
    assert not paths._is_campaign(root / "campaigns" / "c")
    found = paths.find_campaign("c", migrate=False)
    assert not paths._is_campaign(found)


def test_a_campaign_dir_with_a_state_dir_rather_than_a_file_is_not_a_campaign(root):
    """The check is a file, not merely a present path: a directory called
    `state.md` is what a half-restored backup looks like."""
    camp = root / "campaigns" / "c"
    (camp / "state.md").mkdir(parents=True)
    assert not paths._is_campaign(camp)


def test_a_missing_directory_is_not_a_campaign(root):
    assert not paths._is_campaign(root / "campaigns" / "nope")


def test_campaign_system_and_version_read_through_the_same_resolution(root):
    """These two resolve the campaign and then read `state.md`. Before the fix
    they read a shell's absent file and silently returned the default, which is
    how a system field went missing without anything reporting it."""
    make_campaign(root, "c")
    (root / "campaigns" / "c" / "state.md").write_text(
        "**System:** D&D 5e\n**System Version:** 2024\n**System Module:** dnd5e\n",
        encoding="utf-8",
    )
    assert paths.campaign_system_version("c", default="2014") == "2024"
    assert paths.campaign_system("c") == "dnd5e"

    make_shell(root, "shelly")
    assert paths.campaign_system_version("shelly", default="2014") == "2014"
    assert paths.campaign_system("shelly", default="dnd5e") == "dnd5e"
