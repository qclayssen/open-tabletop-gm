"""Persisted-state integrity: atomic writes, corruption recovery, one campaign resolver.

A state-fuzz style check: for every truncation point of a JSON file and for
simulated kills mid-write, a load must never come back as a silent empty dict
when a good copy (the file or its .bak) exists.
"""
from __future__ import annotations

import io
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
import oracle  # noqa: E402
import world_queue  # noqa: E402
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


# ── oracle: a chaos write must not be able to take the World Queue with it ────
#
# oracle.py and world_queue.py are two writers on the same state.md. #291: the
# oracle side was a bare Path.write_text, which truncates on open(). A kill, a
# full disk or a signal between open and write lost the queue, the faction moves
# and the state flags -- for the sake of persisting one integer between 1 and 9.
#
# The assertions below are about the *queue*, not the chaos factor. A test that
# only checked the factor round-trips would pass on the broken code, because the
# broken code is perfectly good at writing an integer.


QUEUE_STATE = """# demo

## Faction Moves
- the party arrived at the Salt Guild

## World Queue
- id: ev-1
  trigger: "Salt Guild clock reaches 4/6"
  event: the ledger burns
  ask: who profits?
  if_ignored: the guild moves
  demands: urgent
  surfaces_as: deadline
  visible_to_players: false
  expires_by: session 9
  status: pending

## Recent Events
- session 1: the party arrived

## Session Flags
- autorun: off
- chaos_factor: 5
"""


def _chaos_args(action, **kw):
    """The argparse.Namespace that `oracle chaos <action>` hands to cmd_chaos."""
    fields = dict(campaign="demo", chaos_action=action, value=None,
                  pc_won=False, pc_lost=False)
    fields.update(kw)
    return type("Args", (), fields)()


def _kill_at_the_rename(monkeypatch):
    """Simulate the process dying in the window the atomic protocol exists for:
    the new content is written and fsynced to a temp file, and the rename that
    would publish it never lands."""
    def killed(*_a, **_k):
        raise KeyboardInterrupt("killed between fsync and rename")
    monkeypatch.setattr(os, "replace", killed)


def _disk_gives_out_after_24_bytes(monkeypatch):
    """A disk that takes 24 bytes and then fails: ENOSPC, a pulled cable, a
    controller reset. The failure lands *during* the write, which is a different
    and worse window than the rename -- and the one a truncate-then-write cannot
    survive at all.

    Patched at `io.open`, the one chokepoint every writer goes through:
    `Path.write_text` reaches it via `Path.open`, and `safeio.atomic_write_text`
    via `os.fdopen`, which is a thin wrapper that calls `io.open` itself. So
    this fires on the broken code and on the fixed code alike, and the assertion
    can be about the invariant rather than about which API was called.
    """
    real_open = io.open

    class _DiesMidWrite:
        """Accepts the first 24 bytes, then fails. Fails exactly once, so the
        close-time flush that follows can drain instead of raising again."""
        def __init__(self, fh):
            self._fh, self._failed = fh, False

        def write(self, data):
            if self._failed:
                return self._fh.write(data)
            self._failed = True
            self._fh.write(data[:24])
            raise OSError(28, "No space left on device")

        def __getattr__(self, name):                  # flush/close/fileno/...
            return getattr(self._fh, name)

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    def half_full_disk(file, mode="r", *a, **k):
        fh = real_open(file, mode, *a, **k)
        return _DiesMidWrite(fh) if any(c in mode for c in "wax+") else fh

    monkeypatch.setattr(io, "open", half_full_disk)


@pytest.fixture
def queued(root):
    """A tmp campaign whose state.md carries a real World Queue entry."""
    camp = root / "campaigns" / "demo"
    (camp / "state.md").write_text(QUEUE_STATE, encoding="utf-8")
    # One queue entry, written by the writer that owns the file, so the fixture
    # is the real thing and not a hand-rolled approximation of it.
    entry = world_queue.add_entry("demo", {
        "trigger": "Salt Guild clock reaches 4/6", "event": "the ledger burns",
        "ask": "who profits?", "if_ignored": "the guild moves",
        "demands": "urgent", "surfaces_as": "deadline", "expires_by": "session 9",
    })
    assert entry["id"] == "ev-1"
    return camp


def _assert_queue_intact(camp, expected_chaos):
    """state.md must be whole: the queue parses, the prose around it is intact,
    and the chaos factor is what it was before the interrupted write."""
    text = (camp / "state.md").read_text(encoding="utf-8")
    _, entries = world_queue.read_queue(text)
    assert len(entries) == 1 and entries[0]["event"] == "the ledger burns"
    assert entries[0]["demands"] == "urgent" and entries[0]["status"] == "pending"
    assert "the party arrived at the Salt Guild" in text
    assert "- autorun: off" in text
    assert oracle.read_chaos(text) == expected_chaos


def test_chaos_set_that_dies_partway_through_the_write_keeps_the_world_queue(
        queued, monkeypatch):
    """The crash case a truncate-then-write cannot survive, and the one that
    loses data without the caller ever seeing an exception it could react to: the
    file is emptied by open(), a few more bytes land, and then the disk gives
    out. Every writer is failed the same way, so the assertion is the invariant
    and not the API: whatever oracle uses, state.md is the old file or the new
    one, and never a fragment of either."""
    before = (queued / "state.md").read_bytes()
    _disk_gives_out_after_24_bytes(monkeypatch)

    with pytest.raises(OSError):
        oracle.cmd_chaos(_chaos_args("set", value=8))

    assert (queued / "state.md").read_bytes() == before
    _assert_queue_intact(queued, 5)
    assert not [p.name for p in queued.iterdir() if p.name.endswith(".tmp")]


def test_chaos_set_killed_mid_write_keeps_the_world_queue(queued, monkeypatch):
    """The other crash case: a kill in the window between fsync and rename.
    `chaos set` is the first of the two call sites named in #291."""
    before = (queued / "state.md").read_bytes()
    _kill_at_the_rename(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        oracle.cmd_chaos(_chaos_args("set", value=8))

    # Byte-for-byte: not truncated to nothing, not half-written, not rewritten
    # with a chaos factor and no queue.
    assert (queued / "state.md").read_bytes() == before
    # And there is a second copy to fall back on.
    assert (queued / "state.md.bak").read_bytes() == before
    _assert_queue_intact(queued, 5)
    assert not [p.name for p in queued.iterdir() if p.name.endswith(".tmp")]


def test_chaos_adjust_killed_mid_write_keeps_the_world_queue(queued, monkeypatch):
    """The crash case on the second call site (#291). Same write, other branch."""
    before = (queued / "state.md").read_bytes()
    _kill_at_the_rename(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        oracle.cmd_chaos(_chaos_args("adjust", pc_won=True))

    assert (queued / "state.md").read_bytes() == before
    assert (queued / "state.md.bak").read_bytes() == before
    _assert_queue_intact(queued, 5)
    assert not [p.name for p in queued.iterdir() if p.name.endswith(".tmp")]


def test_chaos_write_never_opens_state_md_for_writing(queued, monkeypatch):
    """`Path.write_text` truncates the moment it opens; that truncation is the
    whole bug. Whatever oracle uses, it must never open the live state.md for
    writing -- only a temp file that os.replace can swap in whole. This holds
    independently of the kill tests above, and it holds even when the rename
    succeeds: the dangerous window opens at open(), not at the rename."""
    state = queued / "state.md"
    real_open = io.open

    def watched(file, open_mode="r", *a, **k):
        if any(c in open_mode for c in "wax+"):
            try:
                target = pathlib.Path(file)
            except TypeError:      # a raw fd, from os.fdopen on a temp file
                target = None
            if target == state:
                raise AssertionError(
                    f"state.md opened for writing (mode={open_mode!r}); a "
                    "truncating open is exactly what #291 was")
        return real_open(file, open_mode, *a, **k)

    monkeypatch.setattr(io, "open", watched)
    assert oracle.cmd_chaos(_chaos_args("set", value=8)) == 0
    _assert_queue_intact(queued, 8)


def test_chaos_set_writes_the_queue_through_unchanged(queued):
    """The happy path, and the shape the atomic protocol has to produce: the new
    content in state.md, the whole of the previous file in state.md.bak, and
    every line of the queue and the faction moves still there afterwards."""
    before = (queued / "state.md").read_text(encoding="utf-8")
    header, entries = world_queue.read_queue(before)
    assert oracle.cmd_chaos(_chaos_args("set", value=8)) == 0

    after = (queued / "state.md").read_text(encoding="utf-8")
    assert (queued / "state.md.bak").read_text(encoding="utf-8") == before
    assert world_queue.render_block(header, entries) in after
    _assert_queue_intact(queued, 8)
    # The fix changes the bytes on disk not at all: same output, plus a .bak.
    assert oracle.write_chaos(before, 8) == after


def test_chaos_clamps_out_of_range_without_losing_the_queue(queued):
    """--value outside 1-9 is clamped, not honoured, and the clamp must not be
    the thing that costs the campaign its queue."""
    for value, expected in [(0, 1), (99, 9), (-4, 1)]:
        assert oracle.cmd_chaos(_chaos_args("set", value=value)) == 0
        _assert_queue_intact(queued, expected)


def test_chaos_refuses_bad_invocations_before_touching_the_file():
    """Every refusal path returns 2 before touching the file."""
    no_campaign = _chaos_args("set", value=8)
    no_campaign.campaign = None
    assert oracle.cmd_chaos(no_campaign) == 2
    assert oracle.cmd_chaos(_chaos_args("set")) == 2          # no --value
    assert oracle.cmd_chaos(_chaos_args("adjust")) == 2      # neither flag
    both = _chaos_args("adjust", pc_won=True, pc_lost=True)
    assert oracle.cmd_chaos(both) == 2


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
