"""Persisted-state integrity: atomic writes, corruption recovery, one campaign resolver.

A state-fuzz style check: for every truncation point of a JSON file and for
simulated kills mid-write, a load must never come back as a silent empty dict
when a good copy (the file or its .bak) exists.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import paths  # noqa: E402
import safeio  # noqa: E402
import tracker  # noqa: E402
import calendar as gm_calendar  # noqa: E402  (scripts/calendar.py shadows stdlib only if first on path)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setattr(paths, "_default_root", lambda: tmp_path / "nolegacy")
    camp = tmp_path / "campaigns" / "demo"
    camp.mkdir(parents=True)
    (camp / "state.md").write_text("# demo\n", encoding="utf-8")
    return tmp_path


GOOD_OLD = {"kairos": {"conditions": ["prone"], "concentration": "Shield"}}
GOOD_NEW = {"kairos": {"conditions": ["prone", "poisoned"], "concentration": None}}


def test_atomic_write_keeps_bak(tmp_path):
    p = tmp_path / "x.json"
    safeio.atomic_write_json(p, GOOD_OLD)
    safeio.atomic_write_json(p, GOOD_NEW)
    assert json.loads(p.read_text(encoding="utf-8")) == GOOD_NEW
    assert json.loads((tmp_path / "x.json.bak").read_text(encoding="utf-8")) == GOOD_OLD
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_kill_mid_write_leaves_old_file(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    safeio.atomic_write_json(p, GOOD_OLD)

    def boom(*a, **k):
        raise KeyboardInterrupt("killed")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        safeio.atomic_write_json(p, GOOD_NEW)
    monkeypatch.undo()
    assert safeio.load_json_safe(p) == GOOD_OLD
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_every_truncation_recovers_from_bak(tmp_path, capsys):
    p = tmp_path / "x.json"
    safeio.atomic_write_json(p, GOOD_OLD)
    safeio.atomic_write_json(p, GOOD_NEW)
    full = p.read_bytes()
    for cut in range(0, len(full)):        # strict prefixes are all invalid JSON
        p.write_bytes(full[:cut])
        got = safeio.load_json_safe(p)
        assert got == GOOD_OLD, f"silent loss at cut={cut}"
        for q in tmp_path.glob("x.json.corrupt-*"):
            q.unlink()
    assert "corrupt" in capsys.readouterr().err


def test_corrupt_without_bak_is_quarantined_and_loud(tmp_path, capsys):
    p = tmp_path / "x.json"
    p.write_text('{"a": ', encoding="utf-8")
    assert safeio.load_json_safe(p) == {}
    err = capsys.readouterr().err
    assert "corrupt" in err and "no backup" in err
    assert not p.exists()
    assert list(tmp_path.glob("x.json.corrupt-*"))


def test_wrong_type_counts_as_corrupt(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("[1, 2]", encoding="utf-8")
    assert safeio.load_json_safe(p) == {}
    assert list(tmp_path.glob("x.json.corrupt-*"))


def test_tracker_torn_file_does_not_wipe_conditions(root, capsys):
    camp = root / "campaigns" / "demo"
    tracker._save("demo", GOOD_OLD)
    tracker._save("demo", GOOD_NEW)
    (camp / "tracker.json").write_text('{"kairos": {"condi', encoding="utf-8")
    assert tracker._load("demo") == GOOD_OLD
    assert list(camp.glob("tracker.json.corrupt-*"))
    assert "recovered" in capsys.readouterr().err


def test_calendar_torn_file_recovers(root):
    camp = root / "campaigns" / "demo"
    gm_calendar._save("demo", {"day": 3})
    gm_calendar._save("demo", {"day": 4})
    (camp / "calendar.json").write_text("", encoding="utf-8")
    assert gm_calendar._load("demo") == {"day": 3}


def test_transcript_torn_line_skipped_and_reported(tmp_path, capsys):
    p = tmp_path / "transcript.jsonl"
    p.write_text('{"role": "dm", "text": "a"}\n{"role": "pla', encoding="utf-8")
    recs, bad = safeio.read_jsonl_tolerant(p)
    assert [r["text"] for r in recs] == ["a"]
    assert len(bad) == 1 and "skipped 1" in capsys.readouterr().err


def test_memory_turns_survives_torn_tail(tmp_path):
    from localdm.memory import Memory
    m = Memory(tmp_path)
    m.add("dm", "hello")
    with open(m._transcript, "a", encoding="utf-8") as f:
        f.write('{"role": "player", "te')
    assert [t["text"] for t in m.turns()] == ["hello"]


# ── campaign root ────────────────────────────────────────────────────────────

def test_tracker_does_not_create_shell_campaign(root, capsys):
    with pytest.raises(SystemExit) as e:
        tracker._load("typo-name")
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "typo-name" in err and "demo" in err       # lists campaigns found
    assert not (root / "campaigns" / "typo-name").exists()


def test_calendar_does_not_create_shell_campaign(root):
    with pytest.raises(SystemExit):
        gm_calendar._load("nope")
    assert not (root / "campaigns" / "nope").exists()


def test_require_campaign_and_list(root):
    (root / "campaigns" / "shell").mkdir()          # no state.md: not a campaign
    assert paths.list_campaigns() == ["demo"]
    assert paths.require_campaign("demo").name == "demo"
    with pytest.raises(paths.CampaignNotFound, match="Campaigns found: demo"):
        paths.require_campaign("shell")
