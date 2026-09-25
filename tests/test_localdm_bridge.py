"""Milestone 6: running tactics commands in-process and reading the fight."""
from __future__ import annotations

import sys

import pytest

from tests.tactics_fixtures import ROOT, _build, _RAW, RULES
from localdm.bridge import Bridge, Result, parse_player_command

rules_mod = sys.modules[type(RULES).__module__]
KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")


@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    monkeypatch.setattr(rules_mod, "_lookup_monster",
                        lambda name: _build._norm_monster(_RAW[name.lower().replace(" ", "-")]))
    return d


def test_no_fight_means_no_snapshot_and_a_plain_error(camp):
    b = Bridge("demo", camp)
    assert b.snapshot() is None
    r = b.run(["status"])
    assert r.code == 1 and "No grid combat is running" in r.text


def test_start_then_snapshot(camp):
    b = Bridge("demo", camp)
    r = b.run(["start", "frog-pond", "--pc", "Kairos@B7", "--monster", "giant frog@J5",
               "--monster", "giant frog@M11", "--seed", "3"])
    assert r.code == 0 and r.text.startswith("Grid combat on Frog Pond")
    s = b.snapshot()
    assert s["status"] == "active" and s["key"] == "frog-1,frog-2,kairos"
    kairos = next(t for t in s["tokens"] if t["id"] == "kairos")
    assert (kairos["side"], kairos["hp"], kairos["controller"]) == ("pc", 8, "player")
    assert s["current"]["id"] in {"kairos", "frog-1", "frog-2"}


def test_argparse_errors_come_back_as_text_not_an_exit(camp):
    r = Bridge("demo", camp).run(["no-such-command"])
    assert r.code != 0 and "invalid choice" in r.text and not r.needs_roll


def test_a_garbage_encounter_file_reads_as_no_snapshot(camp):
    (camp / "combat").mkdir()
    (camp / "combat" / "encounter.json").write_text("{", encoding="utf-8")
    assert Bridge("demo", camp).snapshot() is None


def test_pending_markers():
    assert Result(2, "Re-run the same command with --roll <the d20 face>").needs_roll
    assert Result(2, "Take it? Re-run the same command with --react yes or --react no.").needs_react
    assert not Result(2, "usage: combat.py [--roll ROLL] [--react {yes,no}]").needs_roll
    assert not Result(2, "usage: combat.py [--react {yes,no}]").needs_react


@pytest.mark.parametrize("cmd,expected", [
    ("attack kairos frog-1 fire bolt", ["attack", "kairos", "frog-1", "fire", "bolt"]),
    ("move kairos D5", ["move", "kairos", "D5"]),
    ("end-turn", ["end-turn"]),
    ("adjust kairos hp=99", None),
    ("attack kairos frog-1 --for-me", None),
    ('move "kairos', None),
    ("", None),
])
def test_only_allowlisted_player_commands_pass(cmd, expected):
    assert parse_player_command(cmd) == expected
