"""A disputed roll has to settle into a fact, not an argument.

WHY
===
The engine owns the dice: `Roller.rng` is an injected `random.Random` and
`Roll` carries every face, the notation, the natural, the total and the source.
But evidence that a process owns its dice is not the same as evidence the
process ran. `enc.log` cannot carry it: it lives in `encounter.json` and is
rewritten wholesale on every save, then truncated to the last 8 entries on the
wire. A commit boundary that overwrites is not a record.

So `scripts/tactics/receipts.py` keeps a second, append-only log
(`<campaign>/combat/rolls.jsonl`) in which every line is signed with an
HMAC-SHA256 chain over a per-campaign key. Editing any past roll breaks every
signature after it, which turns "my player HAS to be cheating" into a
one-command empirical question.

WHAT THESE TESTS PIN DOWN
========================
- a clean chain verifies, and `verify` says so in words a player can read
- editing a roll in the middle is caught, at the exact receipt it breaks
- a torn final line costs a line, not the file
- the write never raises: a fight resolves with or without a receipt (the
  xp-ledger rule, and the reason this can be wired into core.log at all)
- the key is 0600 on POSIX, is created once, and is never replaced over a log
  that already has receipts (a lost key must not silently start a new chain)
- the engine is actually wired: an attack through scripts/tactics/engine.py
  writes receipts, with the pre-roll state the roller saw
"""
from __future__ import annotations

import json
import os
import stat
import sys
import types

import pytest

from tests.tactics_fixtures import (encounter, engine, frog, kairos, roller, start)
from tactics import receipts


# ─── helpers ───────────────────────────────────────────────────────────────────

def _fight(tmp_path, roll_mode="players"):
    """Kairos vs a giant frog, with the campaign stamped on so receipts land
    in the temp tree rather than in a real one."""
    enc = start(encounter([kairos(), frog("frog-1", (5, 0))], roll_mode=roll_mode),
                ["kairos", "frog-1"])
    enc.campaign_dir = tmp_path / "camp"
    (enc.campaign_dir / "combat").mkdir(parents=True, exist_ok=True)
    return enc


def _attack(enc, *faces, supplied=None):
    """Kairos attacks the frog. Under roll_mode players the dice are supplied
    (that is what the display sends); under auto the engine rolls them."""
    r = roller(*faces, supplied=supplied)
    r.state_fn = lambda: receipts.state_hash(enc)
    return engine.attack(enc, r, "kairos", "frog-1", "fire bolt")


def _receipts(tmp_path):
    return receipts._read(receipts.log_path(tmp_path / "camp"))


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


# ─── the chain verifies ───────────────────────────────────────────────────────

def test_an_attack_writes_one_receipt_per_roll(tmp_path):
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])                  # hit, then 1d10+5 damage
    recs = _receipts(tmp_path)
    assert [r["seq"] for r in recs] == [1, 2]
    assert [r["roll"]["notation"] for r in recs] == ["1d20+5", "1d10"]
    assert recs[0]["roll"]["total"] == 19
    assert recs[0]["roll"]["dice"] == [14]         # the face, not just the total
    assert recs[0]["actor"] == "kairos" and recs[0]["kind"] == "attack"
    assert recs[0]["at"].endswith("+00:00")        # ISO-8601 UTC, seconds


def test_the_receipt_records_the_state_the_dice_were_rolled_against(tmp_path):
    """The pre-roll hash, not the state the roll produced."""
    enc = _fight(tmp_path)
    before = receipts.state_hash(enc)
    _attack(enc, supplied=[14, 7])
    recs = _receipts(tmp_path)
    assert recs[0]["state"] == before
    assert recs[0]["state"] != receipts.state_hash(enc)   # the fight moved
    assert all(len(r["state"]) == 64 for r in recs)


def test_a_clean_chain_verifies(tmp_path):
    _attack(_fight(tmp_path), supplied=[14, 7])
    _attack(_fight(tmp_path), supplied=[20, 9])
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is True
    assert out["checked"] == 4
    assert out["first_bad"] is None
    assert any("VERIFIED" in line for line in out["lines"])


def test_the_verifier_exits_zero_and_speaks_plain_english(tmp_path, capsys):
    """The kill criterion: one command, and a player who does not read code
    can tell what it said."""
    _attack(_fight(tmp_path), supplied=[14, 7])
    code = receipts.main(["--dir", str(tmp_path / "camp")])
    out = capsys.readouterr().out
    assert code == 0
    assert "VERIFIED" in out
    assert "receipt" in out.lower()


# ─── tampering is caught ──────────────────────────────────────────────────────

def _tamper_middle(tmp_path):
    """Six receipts, then receipt 2's total changed from 9 to 20."""
    _attack(_fight(tmp_path), supplied=[14, 7])
    _attack(_fight(tmp_path), supplied=[20, 9])
    _attack(_fight(tmp_path), supplied=[20, 9])
    path = receipts.log_path(tmp_path / "camp")
    lines = _lines(path)
    assert len(lines) == 6
    bad = json.loads(lines[1])                      # receipt 2, in the middle
    bad["roll"]["total"] = 20                       # the classic: a 9 becomes a 20
    lines[1] = json.dumps(bad, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_an_edited_roll_breaks_the_chain_at_that_receipt(tmp_path):
    _tamper_middle(tmp_path)
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is False
    assert out["first_bad"] == 2
    assert out["checked"] == 1
    assert any("BROKEN at receipt 2" in line for line in out["lines"])


def test_the_verifier_exits_non_zero_on_a_break(tmp_path, capsys):
    _tamper_middle(tmp_path)
    code = receipts.main(["--dir", str(tmp_path / "camp")])
    assert code == 1
    assert "BROKEN" in capsys.readouterr().out


def test_a_deleted_receipt_shows_up_as_a_gap(tmp_path):
    path = _tamper_middle(tmp_path)
    lines = _lines(path)
    del lines[1]                                    # drop a whole line
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is False
    assert out["first_bad"] == 2
    assert "removed" in out["reason"]


def _write(path, recs):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                    encoding="utf-8")


def test_one_resigned_receipt_does_not_rescue_the_chain(tmp_path):
    """Re-signing the edited line is not enough: receipt 3 still carries a
    signature chained from the receipt 2 that was replaced."""
    _tamper_middle(tmp_path)
    path = receipts.log_path(tmp_path / "camp")
    key = receipts.load_key(tmp_path / "camp", create=False)
    recs = [json.loads(line) for line in _lines(path)]
    clean = [dict(r) for r in recs]
    # Re-sign receipt 2 on its own, leaving the rest exactly as written.
    clean[1]["sig"] = receipts.sign(key, bytes.fromhex(clean[0]["sig"]),
                                    {k: v for k, v in clean[1].items() if k != "sig"})
    _write(path, clean)
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is False and out["first_bad"] == 3


def test_resigning_the_whole_chain_is_the_only_way_to_rewrite_history(tmp_path):
    """The honest limit of a keyed chain: whoever holds the key can rewrite the
    whole file. That is why the key is 0600 and kept out of the log, and why
    the receipts are worth keeping at all."""
    _tamper_middle(tmp_path)
    path = receipts.log_path(tmp_path / "camp")
    key = receipts.load_key(tmp_path / "camp", create=False)
    recs = [json.loads(line) for line in _lines(path)]
    prev = receipts.GENESIS
    for rec in recs:
        rec["sig"] = receipts.sign(key, prev, {k: v for k, v in rec.items() if k != "sig"})
        prev = bytes.fromhex(rec["sig"])
    _write(path, recs)
    assert receipts.verify(tmp_path / "camp")["ok"] is True


# ─── a torn line costs a line, not the file ───────────────────────────────────

def test_a_torn_final_line_does_not_break_reading(tmp_path):
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    path = receipts.log_path(tmp_path / "camp")
    with open(path, "a", encoding="utf-8") as f:      # a write cut in half
        f.write('{"seq": 3, "at": "2026-09-29T21:00:00+00:00", "rol')
    assert len(_receipts(tmp_path)) == 2
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is True                         # the two good receipts stand
    assert out["checked"] == 2
    assert any("unreadable line" in line for line in out["lines"])


def test_the_log_keeps_appending_after_a_torn_line(tmp_path):
    """canon.py's rule: a corrupted append costs one line, not the file. The
    next roll is still recorded and chains from the last good record, so the
    unreadable line is a note and nothing is missing."""
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    path = receipts.log_path(tmp_path / "camp")
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not json at all\n")
    receipts.record(enc, "kairos", "attack", [{"who": "kairos", "total": 9}])
    recs = _receipts(tmp_path)
    assert [r["seq"] for r in recs] == [1, 2, 3]
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is True
    assert any("unreadable line" in line for line in out["lines"])


def test_a_partial_line_with_no_newline_does_not_break_the_chain(tmp_path):
    """The process died mid-write: the tail has no newline. The next append
    must isolate the fragment, and verify must not call that a break."""
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    path = receipts.log_path(tmp_path / "camp")
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"seq": 3, "at": "2026-09-29T21:00:00+00:00", "rol')
    assert not path.read_text(encoding="utf-8").endswith("\n")
    receipts.record(enc, "kairos", "attack", [{"who": "kairos", "total": 9}])
    receipts.record(enc, "kairos", "attack", [{"who": "kairos", "total": 4}])
    recs = _receipts(tmp_path)
    assert [r["seq"] for r in recs] == [1, 2, 3, 4]      # head skipped the scrap
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is True
    assert out["checked"] == 4
    assert any("unreadable line" in line for line in out["lines"])


def test_a_receipt_replaced_by_garbage_in_the_middle_is_still_a_break(tmp_path):
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    receipts.record(enc, "kairos", "attack", [{"who": "kairos", "total": 9}])
    path = receipts.log_path(tmp_path / "camp")
    ls = _lines(path)
    ls[1] = "{garbage"
    path.write_text("\n".join(ls) + "\n", encoding="utf-8")
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is False
    assert out["first_bad"] == 2


def test_the_verdict_does_not_claim_the_tail_is_intact(tmp_path):
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    text = "\n".join(receipts.verify(tmp_path / "camp")["lines"])
    assert "external anchor" in text
    assert "removed after the fact" not in text


def test_each_batch_is_one_write_and_is_fsynced(tmp_path, monkeypatch):
    enc = _fight(tmp_path)
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    receipts.record(enc, "kairos", "attack", [{"total": 1}, {"total": 2}])
    assert synced
    assert len(_receipts(tmp_path)) == 2


# ─── concurrent writers ───────────────────────────────────────────────────────

def _spawn_record(camp, n):
    """multiprocessing target: n one-roll receipts into the same campaign."""
    enc = types.SimpleNamespace(campaign_dir=camp, campaign="x", round=1,
                                turn_index=0, turn=None, tokens={})
    for i in range(n):
        receipts.record(enc, "p", "attack", [{"who": "p", "total": i}])


def _assert_gapless(tmp_path, n):
    recs = _receipts(tmp_path)
    assert [r["seq"] for r in recs] == list(range(1, n + 1))
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is True, out["reason"]


def test_two_threads_recording_together_keep_a_gapless_chain(tmp_path):
    import threading
    enc = _fight(tmp_path)
    ts = [threading.Thread(target=_spawn_record, args=(enc.campaign_dir, 25))
          for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    _assert_gapless(tmp_path, 50)


@pytest.mark.skipif(os.name != "posix", reason="fork start method")
def test_two_processes_recording_together_keep_a_gapless_chain(tmp_path):
    import multiprocessing
    enc = _fight(tmp_path)
    ctx = multiprocessing.get_context("fork")
    ps = [ctx.Process(target=_spawn_record, args=(enc.campaign_dir, 20))
          for _ in range(2)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
        assert p.exitcode == 0
    _assert_gapless(tmp_path, 40)


# ─── the write never raises ───────────────────────────────────────────────────

def test_a_write_failure_does_not_cost_the_player_their_combat(tmp_path, monkeypatch):
    """The xp-ledger rule. Every roll in this file is already resolved by the
    time the receipt is written; losing the receipt must cost nothing."""
    enc = _fight(tmp_path)
    monkeypatch.setattr(receipts, "_append",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    res = _attack(enc, supplied=[14, 7])
    assert res["hit"] is True
    assert enc.tokens["frog-1"].hp < 13
    assert enc.log[-1]["rolls"]                     # the engine's own log is intact


def test_an_unwritable_campaign_does_not_stop_a_roll(tmp_path, monkeypatch):
    enc = _fight(tmp_path)
    monkeypatch.setattr(receipts, "campaign_dir_for",
                        lambda e: (_ for _ in ()).throw(OSError("nope")))
    res = _attack(enc, supplied=[14, 7])
    assert res["hit"] is True


def test_a_fight_with_no_campaign_on_disk_records_nothing(tmp_path, monkeypatch):
    """demo.py and the engine tests narrate a fight in memory. There is no
    campaign directory, so nothing is created to hold a receipt."""
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(tmp_path / "empty-root"))
    enc = start(encounter([kairos(), frog("frog-1", (5, 0))]), ["kairos", "frog-1"])
    assert receipts.campaign_dir_for(enc) is None
    res = _attack(enc, supplied=[14, 7])
    assert res["hit"] is True


def test_the_write_warning_goes_to_stderr_and_not_the_gms_output(tmp_path, capsys):
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    receipts.key_path(tmp_path / "camp").unlink()
    receipts.record(enc, "kairos", "attack", [{"who": "kairos", "total": 1}])
    err = capsys.readouterr()
    assert "key" in err.err
    assert err.out == ""


# ─── the key ──────────────────────────────────────────────────────────────────

@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_the_key_is_created_once_at_mode_0600(tmp_path):
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    key = receipts.key_path(tmp_path / "camp")
    assert key.exists()
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    first = key.read_bytes()
    _attack(_fight(tmp_path), supplied=[2, 3])
    assert key.read_bytes() == first               # never re-minted


def test_a_lost_key_is_reported_not_replaced(tmp_path):
    """A new key over an old log would leave every earlier receipt
    unverifiable, so the log stops growing and says so instead."""
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    receipts.key_path(tmp_path / "camp").unlink()
    receipts.record(enc, "kairos", "attack", [{"who": "kairos", "total": 9}])
    assert not receipts.key_path(tmp_path / "camp").exists()
    assert len(_receipts(tmp_path)) == 2            # the forged line never landed
    out = receipts.verify(tmp_path / "camp")
    assert out["ok"] is False
    assert "key" in out["reason"]


def test_a_lost_key_still_leaves_the_record_readable(tmp_path):
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    receipts.key_path(tmp_path / "camp").unlink()
    recs = _receipts(tmp_path)
    assert recs[0]["roll"]["total"] == 19           # the roll is still on record
    assert recs[0]["seq"] == 1


def test_no_receipts_at_all_is_not_reported_as_clean(tmp_path):
    out = receipts.verify(tmp_path / "nothing-here")
    assert out["ok"] is False
    assert "no receipts" in out["reason"]


def test_a_name_the_console_cannot_show_does_not_crash_the_verdict(tmp_path,
                                                                  monkeypatch, capsys):
    """Windows consoles under a non-UTF-8 code page cannot print every name.
    A verdict is the worst possible place to raise."""
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    path = receipts.log_path(tmp_path / "camp")
    lines = _lines(path)
    rec = json.loads(lines[0])
    rec["roll"]["who"] = "Kairos Ω"
    rec["roll"]["label"] = "Fire Bolt"
    # A receipt with a name the console cannot encode still has to verify, so
    # re-sign the whole chain around the edited line.
    key = receipts.load_key(tmp_path / "camp", create=False)
    recs = [rec] + [json.loads(line) for line in lines[1:]]
    prev = receipts.GENESIS
    for r in recs:
        r["sig"] = receipts.sign(key, prev, {k: v for k, v in r.items() if k != "sig"})
        prev = bytes.fromhex(r["sig"])
    _write(path, recs)
    # A console that cannot show the name, standing in for a Windows one.
    fake = types.SimpleNamespace(stdout=types.SimpleNamespace(encoding="ascii"),
                                 stderr=sys.stderr)
    monkeypatch.setattr(receipts, "sys", fake)
    assert receipts.main(["--dir", str(tmp_path / "camp"), "--rolls", "2"]) == 0
    assert "VERIFIED" in capsys.readouterr().out


# ─── the engine is actually wired ─────────────────────────────────────────────

def test_core_log_records_for_every_engine_command(tmp_path):
    """Not just one command: every roll that passes through core.log is
    receipted, and the numbering runs on across encounters."""
    _attack(_fight(tmp_path), supplied=[14, 7])
    _attack(_fight(tmp_path, roll_mode="auto"), 20, 9, 7)
    _attack(_fight(tmp_path, roll_mode="auto"), 20, 9, 7)
    recs = _receipts(tmp_path)
    assert len(recs) == 6
    assert [x["seq"] for x in recs] == list(range(1, 7))
    assert receipts.verify(tmp_path / "camp")["ok"] is True


def test_log_with_no_roller_writes_no_receipt(tmp_path):
    from tactics import core
    enc = _fight(tmp_path)
    core.log(enc, "condition", "kairos", "GM: stunned", roller=None, mark=0)
    assert _receipts(tmp_path) == []
    assert enc.log[-1]["text"] == "GM: stunned"


def test_the_state_hash_moves_when_the_fight_moves(tmp_path):
    enc = _fight(tmp_path)
    first = receipts.state_hash(enc)
    enc.tokens["frog-1"].hp -= 1
    assert receipts.state_hash(enc) != first
    enc.tokens["frog-1"].hp += 1
    assert receipts.state_hash(enc) == first          # and back again
    enc.round = 2
    assert receipts.state_hash(enc) != first


def test_the_campaign_is_resolved_through_the_configured_root(tmp_path, monkeypatch):
    """An encounter knows its campaign by name. The lookup must honour
    GM_CAMPAIGN_ROOT, and must not be find_campaign: that one copies a whole
    campaign out of its legacy home as a side effect."""
    root = tmp_path / "elsewhere"
    (root / "campaigns" / "c" / "combat").mkdir(parents=True)
    enc = encounter([kairos(), frog()])
    enc.campaign = "c"
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    assert receipts.campaign_dir_for(enc) == root / "campaigns" / "c"


def test_an_empty_key_file_is_regenerated_for_a_new_log(tmp_path):
    """A zero-byte key (crash after create, before write) must not silently
    switch receipts off on a campaign that has recorded nothing yet."""
    enc = _fight(tmp_path)
    receipts.key_path(tmp_path / "camp").write_bytes(b"")
    assert receipts.record(enc, "kairos", "attack", [{"total": 1}]) == 1
    assert len(receipts.key_path(tmp_path / "camp").read_bytes()) == 32
    assert receipts.verify(tmp_path / "camp")["ok"] is True


def test_an_empty_key_file_over_an_existing_log_is_refused(tmp_path, capsys):
    enc = _fight(tmp_path)
    _attack(enc, supplied=[14, 7])
    receipts.key_path(tmp_path / "camp").write_bytes(b"")
    assert receipts.record(enc, "kairos", "attack", [{"total": 1}]) == 0
    assert len(_receipts(tmp_path)) == 2
    assert "key" in capsys.readouterr().err
    assert receipts.verify(tmp_path / "camp")["ok"] is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_the_key_is_never_visible_with_a_loose_mode_or_left_as_a_temp(tmp_path):
    camp = tmp_path / "camp"
    (camp / "combat").mkdir(parents=True)
    key = receipts.load_key(camp)
    assert len(key) == 32
    assert stat.S_IMODE(receipts.key_path(camp).stat().st_mode) == 0o600
    assert [p.name for p in (camp / "combat").iterdir()
            if p.name.startswith(".rolls.key")] == []


def test_a_racing_creator_cannot_overwrite_the_winners_key(tmp_path):
    camp = tmp_path / "camp"
    (camp / "combat").mkdir(parents=True)
    winner = receipts.load_key(camp)
    receipts._mint_key(receipts.key_path(camp))       # the loser arrives late
    assert receipts.key_path(camp).read_bytes() == winner


def test_a_fresh_campaign_with_no_file_verifies_not_ok(tmp_path):
    """Documented behaviour: nothing recorded is not proof of anything."""
    camp = tmp_path / "camp"
    (camp / "combat").mkdir(parents=True)
    out = receipts.verify(camp)
    assert out["ok"] is False and out["checked"] == 0
