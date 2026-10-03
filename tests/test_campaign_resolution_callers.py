"""The entry points must ask `_is_campaign`, not whether the path exists.

`tests/test_paths_campaign_resolution.py` pins the resolver: a directory is a
campaign only if it has a `state.md`. Its docstring is explicit about the
obligation that puts on callers --

    On a miss that path is the not-found sentinel, which is `campaign_dir(name)`:
    a path that does not exist unless a shell is sitting there, so callers must
    not read its existence as a hit. Ask `_is_campaign`.

-- and those callers were not asking. Each of the four below guarded a
`find_campaign` result with `exists()` or `is_dir()`. When a campaign has moved
and left a shell at the old path, the shell *is* `campaign_dir(name)`, so the
guard passed and the entry point read an empty campaign, or wrote into it.

`display/preflight.py` already had the right form, and the comment explaining
why. These tests exist so the other four cannot regress to the name-only check,
which is the original bug, in a place the existing file does not cover.

Each test is a shell at the configured root: the exact shape that defeats a
name-only match, because the shell is at the root so nothing reaches the legacy
fallback or the real tree.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import paths  # noqa: E402

SHELL = "strixhaven-kairos"


@pytest.fixture
def root(tmp_path, monkeypatch):
    """An empty configured root, and a legacy root that does not exist either."""
    r = tmp_path / "root"
    r.mkdir()
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(r))
    monkeypatch.setattr(paths, "_default_root", lambda: tmp_path / "legacy")
    return r


def make_shell(root: pathlib.Path, name: str = SHELL) -> pathlib.Path:
    """A directory with the right name and the wrong contents.

    The two entries a leftover shell carries, left by other tools, and no
    `state.md`. Nothing about the name distinguishes it from a real campaign.
    """
    camp = root / "campaigns" / name
    (camp / "atlas-vtt").mkdir(parents=True)
    (camp / ".obsidian").mkdir()
    return camp


def run(argv: list[str], root: pathlib.Path) -> tuple[int, str]:
    """Run a script in a subprocess, with the configured root in the env."""
    import os
    env = {**os.environ, "GM_CAMPAIGN_ROOT": str(root), "PYTHONPATH": str(ROOT / "scripts")}
    proc = subprocess.run([sys.executable, *argv], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=120, cwd=str(ROOT))
    return proc.returncode, proc.stdout + proc.stderr


# ── the resolver's contract, asserted at the call sites ──────────────────────


@pytest.mark.parametrize("script,guard", [
    ("scripts/tactics/cli.py", "d.exists()"),
    ("scripts/localdm/play.py", "camp_dir.exists()"),
    ("scripts/npc_rename.py", "camp_dir.exists()"),
    ("scripts/map_to_atlas.py", "camp_dir.is_dir()"),
])
def test_no_entry_point_guards_a_find_campaign_result_on_existence(script, guard):
    """before fix: each of the four used exists()/is_dir() on a find_campaign result.

    A source-level check rather than a behavioural one, because the failure needs
    a shell at the configured root *and* an argv the script accepts before it
    reaches the guard -- `play.py` needs a live endpoint, and `map_to_atlas` needs
    a formation. The name-only check is the bug; `preflight.py` has the
    replacement. Pinning the shape of the guard is what survives a refactor of the
    surrounding CLI, where a behavioural test would have to be rewritten.
    """
    text = (ROOT / script).read_text(encoding="utf-8")
    offenders = [ln.strip() for ln in text.splitlines() if guard in ln]
    assert not offenders, (
        f"{script}: {offenders} tests existence on a find_campaign result. "
        f"Use _is_campaign, per the contract documented on paths.find_campaign."
    )


@pytest.mark.parametrize("script", [
    "scripts/tactics/cli.py",
    "scripts/localdm/play.py",
    "scripts/npc_rename.py",
    "scripts/map_to_atlas.py",
])
def test_every_entry_point_imports_is_campaign(script):
    """The import is the check. A script that resolves a campaign must be able
    to ask whether the result is one, or it is reading a name."""
    text = (ROOT / script).read_text(encoding="utf-8")
    assert "find_campaign" in text, f"{script} no longer resolves a campaign; drop this test"
    assert "_is_campaign" in text, f"{script} resolves a campaign but never asks _is_campaign"


# ── the behaviour, for the one caller that needs no endpoint ─────────────────


def test_tactics_cli_refuses_a_shell_and_names_it(root):
    """`play.py tactics`-side: a shell must be a miss, not an empty campaign.

    Runs the real CLI. `--help` is not enough to reach `_camp_dir`, and the
    commands that do reach it should stop on the campaign check before they
    touch an encounter, so the refusal is observable without a display.
    """
    make_shell(root)
    code, out = run(["scripts/tactics/play.py", "-c", SHELL, "status"], root)
    assert code != 0, f"a shell was accepted as a campaign:\n{out}"
    assert SHELL in out, out


def test_npc_rename_refuses_a_shell_before_writing(root):
    """The write case, and the reason this guard is not cosmetic.

    `npc_rename` renames in place. Guarded on `exists()`, a shell passes and the
    rename is attempted against a campaign with no `characters/` and no
    `state.md` -- and `npcs.md` in a shell is whatever another tool left there.
    """
    make_shell(root)
    code, out = run(["scripts/npc_rename.py", "--campaign", SHELL,
                     "--old", "Ash", "--new", "Ember"], root)
    assert code != 0, f"npc_rename accepted a shell:\n{out}"
    assert "not found" in out, out
    # And nothing was created in the shell.
    shell = root / "campaigns" / SHELL
    assert sorted(p.name for p in shell.iterdir()) == [".obsidian", "atlas-vtt"], \
        f"npc_rename wrote into the shell: {sorted(p.name for p in shell.iterdir())}"


def test_a_real_campaign_still_resolves_through_the_same_path(root):
    """The guard must not reject a real campaign.

    A fix that only ever refuses passes every test above.
    """
    camp = root / "campaigns" / SHELL
    camp.mkdir(parents=True)
    (camp / "state.md").write_text("# Campaign\n", encoding="utf-8")
    assert paths._is_campaign(paths.find_campaign(SHELL, migrate=False))
    code, out = run(["scripts/npc_rename.py", "--campaign", SHELL,
                     "--old", "Ash", "--new", "Ember"], root)
    # Not a 1: the campaign resolved, so it failed later or succeeded.
    assert "not found" not in out, out
