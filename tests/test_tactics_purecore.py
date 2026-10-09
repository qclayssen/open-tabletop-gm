"""The pure rules core: apply(state, command) -> (state, events).

What is pinned: the core runs with the disk, the network, subprocesses and the
display sync unavailable; the same seed, state and command give byte-identical
output; the input is never mutated; a refusal changes nothing; the CLI routes
its commands through it.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from tests.tactics_fixtures import encounter, frog, kairos, start
from tactics import purecore
from tactics.core import CombatError

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _state(controller="gm") -> dict:
    """Kairos next to a frog, Kairos to act. GM-controlled so engine dice are used."""
    enc = start(encounter([kairos(pos=(2, 2), controller=controller), frog("frog-1", (3, 2))]),
                ["kairos", "frog-1"])
    return enc.to_dict()


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True)


# ─── the blocked-module shim ──────────────────────────────────────────────────

SHIM = textwrap.dedent('''
    import builtins, io, json, os, pathlib, sys, time

    class Blocked:
        """A meta-path finder that refuses a module and everything under it."""
        def __init__(self, *names): self.names = names
        def find_spec(self, name, path=None, target=None):
            if any(name == n or name.startswith(n + ".") for n in self.names):
                raise ImportError(f"blocked in the pure core: {name}")

    sys.meta_path.insert(0, Blocked("urllib", "subprocess", "socket", "http", "tactics.sync"))
    for gone in ("urllib", "subprocess", "socket", "tactics.sync"):
        sys.modules.pop(gone, None)

    sys.path[:0] = [sys.argv[1], sys.argv[2]]
    import tactics
    from tactics import purecore
    state = json.loads(sys.argv[3])

    def no_write(*a, **k):
        raise PermissionError("filesystem writes are unavailable")

    real_open = builtins.open
    def guarded_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            no_write()
        return real_open(file, mode, *a, **k)
    builtins.open = guarded_open
    io.open = guarded_open
    for name in ("replace", "rename", "remove", "unlink", "mkdir", "makedirs", "fsync"):
        setattr(os, name, no_write)
    pathlib.Path.write_text = pathlib.Path.write_bytes = pathlib.Path.mkdir = no_write
    time.time = time.time_ns = no_write       # no clock

    new, events = purecore.apply(state, {"cmd": "attack", "token": "kairos",
                                         "target": "frog-1", "attack": "Dagger", "seed": 11})
    assert "tactics.sync" not in sys.modules and "subprocess" not in sys.modules
    print(json.dumps([new, events], sort_keys=True))
''')


def test_core_runs_with_sync_network_subprocess_and_disk_writes_blocked():
    state = _state()
    root = SCRIPTS.parent
    proc = subprocess.run(
        [sys.executable, "-c", SHIM, str(SCRIPTS), str(root), json.dumps(state)],
        capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=root)
    assert proc.returncode == 0, proc.stderr
    new, events = json.loads(proc.stdout)
    assert events[-1]["type"] == "result" and "Dagger" in events[-1]["text"]
    assert new["tokens"]["kairos"]["hp"] == state["tokens"]["kairos"]["hp"]
    assert new["turn"]["action_used"] is True and state["turn"]["action_used"] is False


# ─── determinism and purity ───────────────────────────────────────────────────

def test_same_seed_state_and_command_give_byte_identical_output():
    state = _state()
    command = {"cmd": "attack", "token": "kairos", "target": "frog-1", "attack": "Dagger",
               "seed": 3}
    first = purecore.apply(copy.deepcopy(state), dict(command))
    second = purecore.apply(copy.deepcopy(state), dict(command))
    assert _dump(first) == _dump(second)
    assert first[1][0]["rolls"], "the attack rolled dice, so the comparison is not vacuous"


def test_a_different_seed_can_roll_differently():
    state = _state()
    outs = {_dump(purecore.apply(state, {"cmd": "attack", "token": "kairos",
                                         "target": "frog-1", "attack": "Dagger", "seed": s}))
            for s in range(8)}
    assert len(outs) > 1


def test_the_input_state_is_not_mutated():
    state = _state()
    frozen = _dump(state)
    purecore.apply(state, {"cmd": "move", "token": "kairos", "square": "C3", "seed": 1})
    assert _dump(state) == frozen


def test_a_command_that_rolls_with_no_roller_or_seed_is_refused_not_guessed():
    with pytest.raises(ValueError, match="roller or a seed"):
        purecore.apply(_state(), {"cmd": "attack", "token": "kairos", "target": "frog-1",
                                  "attack": "Dagger"})


def test_a_roll_free_command_needs_no_seed():
    new, events = purecore.apply(_state(), {"cmd": "dash", "token": "kairos"})
    assert new["turn"]["action_used"] is True
    assert [e["type"] for e in events] == ["log", "result"]


def test_a_refusal_raises_and_the_caller_keeps_its_state():
    state = _state()
    frozen = _dump(state)
    with pytest.raises(CombatError, match="Frog 1's turn|not"):
        purecore.apply(state, {"cmd": "move", "token": "frog-1", "square": "D4", "seed": 1})
    assert _dump(state) == frozen


def test_unknown_command_is_a_refusal():
    with pytest.raises(CombatError, match="unknown command"):
        purecore.apply(_state(), {"cmd": "teleport"})


def test_queries_return_the_state_unchanged():
    state = _state()
    new, events = purecore.apply(state, {"cmd": "status"})
    assert _dump(new) == _dump(state)
    assert events[-1]["text"].startswith("Round 1, Kairos's turn")


def test_the_core_writes_no_receipts_even_when_the_campaign_exists_on_disk(tmp_path, monkeypatch):
    camp = tmp_path / "test"
    (camp / "combat").mkdir(parents=True)
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(tmp_path))
    state = _state()
    state["campaign"] = "test"
    purecore.apply(state, {"cmd": "attack", "token": "kairos", "target": "frog-1",
                           "attack": "Dagger", "seed": 5})
    assert sorted(p.name for p in (camp / "combat").iterdir()) == []


HASHSEED = textwrap.dedent('''
    import json, sys
    sys.path[:0] = [sys.argv[1], sys.argv[2]]
    from tactics import purecore
    gm_turn, frog_turn = json.loads(sys.argv[3]), json.loads(sys.argv[4])
    runs = [purecore.apply(gm_turn, {"cmd": "attack", "token": "kairos", "target": "frog-1",
                                     "attack": "Dagger", "seed": 4}),
            purecore.apply(gm_turn, {"cmd": "move", "token": "kairos", "square": "C2", "seed": 4}),
            purecore.apply(gm_turn, {"cmd": "end-turn", "seed": 4}),
            purecore.apply(frog_turn, {"cmd": "choose", "token": "frog-1", "n": "auto",
                                       "difficulty": "easy", "seed": 4})]
    print(json.dumps(runs, sort_keys=True))
''')


def test_output_is_byte_identical_across_processes_with_different_hash_seeds():
    root = SCRIPTS.parent
    frog_turn = start(encounter([kairos(pos=(2, 2), controller="gm"), frog("frog-1", (3, 2))]),
                      ["frog-1", "kairos"]).to_dict()
    outs = []
    for hashseed in ("1", "2"):
        proc = subprocess.run(
            [sys.executable, "-c", HASHSEED, str(SCRIPTS), str(root), json.dumps(_state()),
             json.dumps(frog_turn)],
            capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=root,
            env={**os.environ, "PYTHONHASHSEED": hashseed})
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout)
    assert outs[0] == outs[1]
    assert "attack" in outs[0] and len(json.loads(outs[0])) == 4


def test_events_must_be_plain_json_not_reprs(monkeypatch):
    monkeypatch.setitem(purecore._HANDLERS, "dash", lambda enc, c, r: ("x", {"bad": {1, 2}}))
    with pytest.raises(TypeError):
        purecore.apply(_state(), {"cmd": "dash", "token": "kairos"})


def test_receipts_are_opt_in_events_and_the_default_is_unchanged():
    state = _state()
    command = {"cmd": "attack", "token": "kairos", "target": "frog-1", "attack": "Dagger",
               "seed": 3}
    _, plain = purecore.apply(state, command)
    _, with_receipts = purecore.apply(state, command, receipts=True)
    assert [e["type"] for e in plain] == ["log", "result"]
    assert [e["type"] for e in with_receipts] == ["log", "receipt", "result"]
    receipt = with_receipts[1]
    assert receipt["actor"] == "kairos" and receipt["kind"] == plain[0]["kind"]
    assert receipt["rolls"] == plain[0]["rolls"] and receipt["rolls"]


class _FixedDraw:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def test_choose_auto_takes_its_pick_from_the_injected_policy_rng():
    state = start(encounter([kairos(pos=(2, 2), controller="gm"), frog("frog-1", (3, 2))]),
                  ["frog-1", "kairos"]).to_dict()
    command = {"cmd": "choose", "token": "frog-1", "n": "auto", "difficulty": "easy", "seed": 4}

    def pick(draw):
        r = purecore.make_roller(command)
        if draw is not None:
            r.policy_rng = _FixedDraw(draw)
        return purecore.apply(state, command, roller=r)[1][-1]["data"]["option"]["n"]

    assert pick(None) == pick(None)                 # default stream: unchanged, deterministic
    assert {pick(0.0), pick(0.999999)} != set() and pick(0.0) != pick(0.999999)


# ─── pure queries ─────────────────────────────────────────────────────────────

def test_pure_queries_answer_from_the_state_alone():
    state = _state()
    walk = purecore.reachable(state, "kairos")["walk"]
    assert "B2" in walk and "D4" in walk and "C3" not in walk
    assert purecore.line_of_sight(state, "kairos", "frog-1") is True
    assert purecore.cover(state, "kairos", "frog-1") == {"los": True, "cover": 0}
    squares = purecore.area(state, "sphere", 10, "C3", "C3", from_self=False)
    assert squares["squares"], squares


def test_a_wall_between_two_tokens_blocks_sight_and_gives_no_line():
    rows = ["..#" + "." * 5] * 8
    enc = start(encounter([kairos(pos=(0, 1), controller="gm"), frog("frog-1", (4, 1))],
                          rows=rows), ["kairos", "frog-1"])
    state = enc.to_dict()
    assert purecore.line_of_sight(state, "kairos", "frog-1") is False
    assert purecore.cover(state, "kairos", "frog-1")["los"] is False


# ─── the CLI goes through it ─────────────────────────────────────────────────

def test_the_cli_routes_commands_through_the_core_and_keeps_the_disk_edge(
        tmp_path, monkeypatch, capsys):
    from tactics import cli, state as state_mod
    root = tmp_path / "root"
    camp = root / "campaigns" / "demo"
    (camp / "combat").mkdir(parents=True)
    (camp / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n",
                                   encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    enc = start(encounter([kairos(pos=(2, 2), controller="gm"), frog("frog-1", (3, 2))]),
                ["kairos", "frog-1"])
    enc.campaign = "demo"
    state_mod.save(enc, state_mod.encounter_path(camp))

    seen = []
    real = purecore.execute

    def spy(e, command, roller):
        seen.append(command["cmd"])
        return real(e, command, roller)

    monkeypatch.setattr(purecore, "execute", spy)
    assert cli.main(["-c", "demo", "move", "kairos", "C2"]) == 0
    assert seen == ["move"]
    capsys.readouterr()
    # The edge saved the result and wrote the roll receipt the core parked.
    saved = state_mod.load(state_mod.encounter_path(camp))
    assert saved.tokens['kairos'].square == 'C2'
    assert cli.main(["-c", "demo", "attack", "kairos", "frog-1", "--seed", "2"]) == 0
    assert "Frog 1" in capsys.readouterr().out
    assert (camp / "combat" / "rolls.jsonl").exists()
