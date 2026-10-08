"""traces.py: golden traces recorded from the pure core, and the replay that grades them.

Regenerate every trace and the manifest, from a clean tree, with one command:

    python3 scripts/tactics/traces.py --generate

Regenerating is a deliberate act, not a fix for a failing test. A replay that goes
red asks whether the rules changed on purpose. If they did, regenerate in the same
PR (D-16: anything that changes rules output lands in the Python oracle first, with
its traces). If they did not, the engine is wrong and the trace is right. Never
edit a trace by hand.

These files are the oracle the TypeScript port (phases 15-16) is graded against,
so the format avoids everything only CPython can reproduce:

  - dice are the FACES that were rolled, `tape`: [[sides, face], ...] in call
    order, including the second d20 of advantage. A replay feeds them back
    through `roller=`. The Mersenne Twister never has to be reproduced, and the
    `seed` is kept only as a note on where the faces came from.
  - the one draw that is not a die, the pick `choose auto` makes among options,
    is `policy_draws`: [float, ...], fed back through `roller.policy_rng`.
  - the randomised grid cases store their generated inputs. Replay needs no RNG.
  - files are written with `receipts.canonical` (sorted keys, compact, UTF-8) and
    are strict JSON: a value JSON cannot hold fails the recording instead of
    becoming a repr string.

What is recorded, and the choice behind each:

  - kind "apply": `purecore.apply(state, command, roller=..., receipts=True)`.
    `receipts=True` because the edge writes the roll receipts and a port must
    write the same ones. The result is {"state", "events"}, or {"raises", "message"}
    (plus "pending": the exception's `to_dict()` for a PendingRoll or
    DecisionNeeded). Refusals and pauses are traces: the port must refuse in the
    same places.
  - kind "query": one of the four pure queries on an encounter state
    (`purecore.reachable|area|cover|line_of_sight`).
  - kind "grid": the same geometry on a bare grid dict, with no tokens.
  - every `test_tactics_*` scenario, by wrapping the engine entry points the tests
    call while a pytest run is recording (TACTICS_RECORD_TRACES=<dir>, see
    tests/conftest.py). The wrappers record from the OUTSIDE, so no engine code
    changes. Each record's result is what `purecore.apply` produced from the
    recorded inputs, and it is kept only if it matches what the live call did.
    A call that cannot be expressed as a purecore command, or does not replay to
    the same outcome, is not recorded and its test says why in the manifest.
  - `manifest.json`: every test id in tests/test_tactics_*.py, mapped to trace ids
    or to a reason it has none, plus the engine commit the traces were recorded
    from (stored once there, not in each record).

Comparison across languages is on PARSED JSON, never bytes: Python's round() rounds
halves to even and prints 3.0 where JS prints 3. Byte equality is the Python to
Python check only (TSP-03).
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import functools
import hashlib
import inspect
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import dice                                                    # noqa: E402
from tactics import (actions, ai, engine, policy, purecore, receipts,   # noqa: E402
                     sight, spells, statecard)
from tactics.core import CombatError, DecisionNeeded           # noqa: E402
from tactics.grid import Grid, MoveOptions, area as grid_area, footprint, label  # noqa: E402
from tactics.roller import BadFace, PendingRoll, Roller        # noqa: E402
from tactics.state import Encounter, Token, TurnState          # noqa: E402

SCHEMA = "tactics-trace/1"
ENV_VAR = "TACTICS_RECORD_TRACES"
TRACES_DIR = ROOT / "tests" / "traces"
TEST_GLOB = "tests/test_tactics_*.py"
# The replay test reads the traces; recording it would record the recorder.
EXCLUDED_MODULES = frozenset({"test_tactics_traces.py"})
GRID_SEED = 20261008
GRID_CASES = 500
# A whole-fight scenario (the demo, a play-through) makes 30 to 60 calls, each a
# full state in and a full state out. Every record replays on its own, so such a
# test keeps this many, spaced evenly from its first call to its last, and the
# manifest says how many it made. Without the cap the tree is ~9.4 MB.
MAX_PER_TEST = 20

# Exceptions that are rules outcomes. Anything else (KeyError, TypeError) is a
# Python-specific failure whose class and message a port cannot be asked to match.
RECORDED_RAISES = (CombatError, PendingRoll, DecisionNeeded, BadFace)


class TraceError(Exception):
    """A trace is malformed or no longer replays: the tape ran short or long, or
    the stored inputs cannot be run at all. Never swallowed as a rules refusal."""


# ─── canonical JSON ───────────────────────────────────────────────────────────

def canonical(value) -> bytes:
    """The only serialiser for trace files: sorted keys, compact, UTF-8, strict."""
    return receipts.canonical(value)


def plain(value):
    """`value` as plain JSON data (tuples become lists), strictly: a set or an
    object raises TypeError rather than becoming a repr."""
    return json.loads(canonical(value))


# A sheet token remembers where its character file was read from, and the suite
# reads it from a temporary campaign. That path is the one machine-specific thing
# in a recorded state, so it is rewritten to its campaign-relative tail.
_CAMPAIGN_PATH = re.compile(r"^(?:[A-Za-z]:)?[\\/].*?[\\/](campaigns[\\/].*)$")
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:\\|/)[^\s]*[\\/][^\s]+")


def scrub(value):
    """`value` with temporary-campaign paths made relative ("<root>/campaigns/...")."""
    if isinstance(value, str):
        m = _CAMPAIGN_PATH.match(value)
        return "<root>/" + m.group(1).replace("\\", "/") if m else value
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def machine_paths(value) -> list:
    """Absolute filesystem paths still in `value`: they would make a trace differ
    from machine to machine, so a record that holds one is not written."""
    if isinstance(value, str):
        return [value] if _ABSOLUTE_PATH.match(value) else []
    if isinstance(value, dict):
        return [p for v in value.values() for p in machine_paths(v)]
    if isinstance(value, list):
        return [p for v in value for p in machine_paths(v)]
    return []


# ─── dice from a tape ─────────────────────────────────────────────────────────

class RecordingRng:
    """Wraps whatever rng a roller holds and logs [sides, face] per randint.

    A proxy rather than a subclass of random.Random, because the suite's own
    rng is sometimes a ScriptedDice, which has nothing but randint."""

    def __init__(self, inner, log: list):
        self._inner, self._log = inner, log

    def randint(self, lo, hi):
        face = self._inner.randint(lo, hi)
        if lo != 1:
            raise TraceError(f"the engine rolled randint({lo}, {hi}); a tape holds d-sides from 1")
        self._log.append([hi, face])
        return face

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TapeRng(random.Random):
    """Hands back the recorded faces in order. Only `randint` is overridden, so
    the Roller needs no change; anything else the engine might ask of an rng is
    refused, because a face that is not on the tape would be invented."""

    def __init__(self, tape):
        super().__init__(0)
        self._tape, self._at, self.error = [list(x) for x in tape], 0, None

    def randint(self, lo, hi):
        if lo != 1 or self._at >= len(self._tape):
            self.error = self.error or "the tape ran out before the command finished"
            raise TraceError(self.error)
        sides, face = self._tape[self._at]
        if sides != hi:
            self.error = self.error or f"tape entry {self._at} is a d{sides}, the engine rolled a d{hi}"
            raise TraceError(self.error)
        self._at += 1
        return face

    def random(self):
        raise TraceError("a dice roll asked for a float; only randint is on the tape")

    def getrandbits(self, k):
        raise TraceError("a dice roll asked for raw bits; only randint is on the tape")

    @property
    def exhausted(self) -> bool:
        return self._at == len(self._tape)


class DrawLog:
    """Wraps the object `choose auto` draws its pick from and logs each random()."""

    def __init__(self, inner, log: list):
        self._inner, self._log = inner, log

    def random(self):
        value = self._inner.random()
        self._log.append(value)
        return value

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TapeDraws:
    """The recorded `choose auto` draws, in order."""

    def __init__(self, draws):
        self._draws, self._at = list(draws), 0

    def random(self):
        if self._at >= len(self._draws):
            raise TraceError("policy_draws ran out before the command finished")
        self._at += 1
        return self._draws[self._at - 1]

    @property
    def exhausted(self) -> bool:
        return self._at == len(self._draws)


class _PolicyRandomShim:
    """Stands in for the `random` module inside policy.py for one recorded call,
    so the pick's own crc32-seeded stream is logged without touching policy.py."""

    def __init__(self, log: list):
        self._log = log

    def Random(self, seed=None):                                # noqa: N802
        return DrawLog(dice.new_rng(seed), self._log)

    def __getattr__(self, name):
        return getattr(random, name)


# ─── running a command ────────────────────────────────────────────────────────

def _roller_for(command: dict, rng, policy_rng=None) -> Roller:
    """The roller a trace implies: the tape (or any rng) for engine dice, and the
    command's own `rolls` / `player_roll` / `for_me` for the player's faces."""
    roller = Roller(rng=rng, supplied=list(command.get("rolls") or []),
                    supplied_source="player" if command.get("player_roll") else "verbal",
                    for_me=bool(command.get("for_me")))
    if policy_rng is not None:
        roller.policy_rng = policy_rng
    return roller


def _raised(exc: BaseException) -> dict:
    out = {"raises": type(exc).__name__, "message": str(exc)}
    if hasattr(exc, "to_dict"):
        out["pending"] = plain(exc.to_dict())
    return out


def _apply_outcome(state: dict, command: dict, roller: Roller) -> dict:
    try:
        new_state, events = purecore.apply(state, command, roller=roller, receipts=True)
    except RECORDED_RAISES as exc:
        return _raised(exc)
    return {"state": plain(new_state), "events": plain(events)}


def _query_outcome(kind: str, state: dict, command: dict) -> dict:
    q, args = command["query"], {k: v for k, v in command.items() if k != "query"}
    try:
        if kind == "query":
            if q == "reachable":
                value = purecore.reachable(state, args["token"])
            elif q == "area":
                value = purecore.area(state, args["shape"], args["size"], args["caster"],
                                      args.get("target"), width=args["width"],
                                      from_self=args["from_self"])
            elif q == "cover":
                value = purecore.cover(state, args["attacker"], args["target"])
            elif q == "line_of_sight":
                value = purecore.line_of_sight(state, args["a"], args["b"])
            else:
                raise TraceError(f"unknown query {q!r}")
        else:
            value = _grid_value(Grid.from_dict(state), q, args)
    except (ValueError, CombatError) as exc:
        return _raised(exc)
    return {"value": plain(value)}


def _pos(p) -> tuple:
    return (p[0], p[1])


def _grid_value(grid: Grid, q: str, a: dict):
    if q == "reachable":
        opts = MoveOptions(blocked=frozenset(map(_pos, a["blocked"])),
                           occupied=frozenset(map(_pos, a["occupied"])),
                           crawling=a["crawling"], swim=a["swim"])
        got = grid.reachable(_pos(a["start"]), a["budget"], opts, parity=a["parity"])
        return sorted([[list(k), v] for k, v in got.items()])
    if q == "area":
        return grid_area(grid, a["shape"], a["size"], _pos(a["caster"]),
                         _pos(a["target"]) if a.get("target") else None,
                         a["width"], a["from_self"])
    if q == "cover":
        return grid.cover(_pos(a["attacker"]), _pos(a["target"]),
                          creatures=frozenset(map(_pos, a["creatures"])),
                          attacker_size=tuple(a["attacker_size"]),
                          target_size=tuple(a["target_size"]))
    if q == "line_of_sight":
        return grid.line_of_sight(_pos(a["a"]), _pos(a["b"]))
    raise TraceError(f"unknown grid query {q!r}")


def replay_outcome(record: dict) -> dict:
    """Run a record's inputs again with nothing but its own tape. The outcome has
    the shape of record["result"]. Raises TraceError if the tape is short or long."""
    kind = record["kind"]
    if kind != "apply":
        return _query_outcome(kind, record["state"], record["command"])
    rng, draws = TapeRng(record["tape"]), TapeDraws(record["policy_draws"])
    outcome = _apply_outcome(record["state"], record["command"],
                             _roller_for(record["command"], rng, draws))
    if rng.error:
        raise TraceError(rng.error)
    if not rng.exhausted:
        raise TraceError("the tape was not fully consumed: the replay rolled fewer dice")
    if not draws.exhausted:
        raise TraceError("policy_draws were not fully consumed")
    return outcome


def replay(record: dict) -> bytes:
    """Canonical bytes of the replayed outcome."""
    return canonical(replay_outcome(record))


def check(record: dict) -> None:
    """Replay a record and raise TraceError unless it matches its stored result."""
    if record.get("schema") != SCHEMA:
        raise TraceError(f"{record.get('id')}: schema {record.get('schema')!r}, expected {SCHEMA!r}")
    if replay(record) != canonical(record["result"]):
        raise TraceError(f"{record['id']} ({record['source']}): replay differs from the stored result")


# ─── building a record ────────────────────────────────────────────────────────

def _module_of(source: str) -> str:
    return source.split("::", 1)[0].rsplit("/", 1)[-1]


def build_record(source: str, kind: str, state, command: dict, seed, tape: list,
                 policy_draws: list, result: dict) -> dict:
    rec = {"schema": SCHEMA, "source": source, "kind": kind, "state": state, "command": command,
           "seed": seed, "tape": tape, "policy_draws": policy_draws, "result": result}
    digest = hashlib.sha256(canonical({k: rec[k] for k in
                                       ("kind", "state", "command", "tape", "policy_draws")})).hexdigest()
    stem = (re.sub(r"^test_tactics_|\.py$", "", _module_of(source)) if source.startswith("tests/")
            else source.split(":", 1)[0])
    rec["id"] = f"{stem}-{digest[:12]}"
    return rec


def record(state: dict, command: dict, seed=None, *, source: str = "adhoc") -> dict:
    """Record one command: run it for real on a copy of `state`, log every die,
    and return the trace. `seed` feeds the CPython stream the faces come from;
    with none, a command that needs a die raises, exactly as `apply` does."""
    command, state = plain(command), plain(state)       # the file holds sorted keys: run on exactly that
    tape, draws = [], []
    inner = dice.new_rng(seed) if seed is not None else purecore._NoSeed()
    shim = _PolicyRandomShim(draws)
    real, policy.random = policy.random, shim
    try:
        outcome = _apply_outcome(copy.deepcopy(state), command,
                                 _roller_for(command, RecordingRng(inner, tape)))
    finally:
        policy.random = real
    return build_record(source, "apply", state, command, seed, tape, draws, outcome)


# ─── the recorder: wraps the engine entry points during a pytest run ──────────

def _verb(cmd, **fields):
    return dict(cmd=cmd, **fields)


def _spec(module, name, command, expect=None, *, roller=True):
    return dict(module=module, name=name, command=command, expect=expect, roller=roller)


def _skip_unless(cond, why):
    if not cond:
        raise _Unmappable(why)


class _Unmappable(Exception):
    """This call has no purecore command. The text becomes the manifest reason."""


def _cmd_execute(a):
    return dict(a["command"])


def _cmd_move(a):
    _skip_unless(not a["as_reaction"], "move(as_reaction=True) has no purecore verb")
    return _verb("move", token=a["token_ref"], square=a["square"], reactions=a["reactions"])


def _cmd_cast(a):
    _skip_unless(a["readied"] is None, "cast(readied=...) has no purecore verb")
    return _verb("cast", token=a["caster_ref"], words=[a["spell"], *(a["targets"] or [])],
                 level=a["level"], reactions=a["reactions"])


def _cmd_choose(a):
    _skip_unless(a["limit"] == 5, "choose(limit != 5) has no purecore verb")
    return _verb("choose", token=a["token_ref"], n=a["n"], reactions=a["reactions"])


def _cmd_options(a):
    _skip_unless(a["limit"] == 5, "options(limit != 5) has no purecore verb")
    return _verb("options", token=a["token_ref"])


def _cmd_ready(a):
    return _verb("ready", token=a["token_ref"], kind=a["kind"],
                 what=[a["what"]] if a["what"] else None, target=a["target"],
                 trigger=a["trigger_text"], level=a["level"])


def _cmd_death_save(a):
    return _verb("death-save", token=a["enc"].current.id)


def _tok(cmd):
    return lambda a: _verb(cmd, token=a["token_ref"])


def _data(key=None):
    """Expected `data` of the result event: the returned dict, or {key: returned}."""
    return (lambda ret: {"data": ret}) if key is None else (lambda ret: {"data": {key: ret}})


def _text_only(ret):
    return {"text": ret["text"], "data": {}}


def _dict_text(ret):
    return {"text": ret["text"], "data": ret}


_ENTRY_POINTS = [
    _spec(purecore, "execute", _cmd_execute, lambda ret: {"text": ret[0], "data": ret[1]}),
    _spec(engine, "move", _cmd_move, _dict_text),
    _spec(engine, "attack", lambda a: _verb("attack", token=a["attacker_ref"], target=a["target_ref"],
                                            attack=a["attack_name"], reactions=a["reactions"],
                                            advantage=a["advantage"]), _dict_text),
    _spec(engine, "multiattack", lambda a: _verb("multiattack", token=a["attacker_ref"],
                                                 target=a["target_ref"], option=a["routine"],
                                                 reactions=a["reactions"]), _dict_text),
    _spec(engine, "check", lambda a: _verb("check", token=a["token_ref"], name=a["name"], dc=a["dc"],
                                           advantage=a["advantage"], sense=a["sense"],
                                           by=a["by_ref"], source=a["source_ref"]), _dict_text),
    _spec(engine, "dash", _tok("dash"), _text_only, roller=False),
    _spec(engine, "disengage", _tok("disengage"), _text_only, roller=False),
    _spec(engine, "dodge", _tok("dodge"), _text_only, roller=False),
    _spec(engine, "stand_up", _tok("stand"), _text_only, roller=False),
    _spec(engine, "end_turn", lambda a: _verb("end-turn"), _text_only),
    _spec(engine, "death_save", _cmd_death_save, lambda ret: {"text": ret["text"], "data": {}}),
    _spec(engine, "undo_move", lambda a: _verb("undo-move"), _text_only, roller=False),
    _spec(engine, "reachable", _tok("reachable"), lambda ret: {"data": ret}, roller=False),
    _spec(engine, "approach", lambda a: _verb("approach", token=a["token_ref"], target=a["target_ref"]),
          _dict_text, roller=False),
    _spec(engine, "preview_move", lambda a: _verb("preview", token=a["token_ref"], square=a["square"]),
          _dict_text, roller=False),
    _spec(engine, "attack_options", lambda a: _verb("targets", token=a["attacker_ref"]),
          lambda ret: {"data": {"targets": ret}}, roller=False),
    _spec(spells, "cast", _cmd_cast, _dict_text),
    _spec(spells, "use_action", lambda a: _verb("use", token=a["token_ref"],
                                                words=[a["action_name"], a["target"]],
                                                reactions=a["reactions"]), _dict_text),
    _spec(spells, "castable", lambda a: _verb("spells", token=a["caster_ref"]),
          lambda ret: {"data": {"spells": ret}}, roller=False),
    _spec(ai, "choose", _cmd_choose, lambda ret: {"data": ret}),
    _spec(ai, "options", _cmd_options, lambda ret: {"data": {"options": ret}}, roller=False),
    _spec(policy, "choose_auto", lambda a: _verb("choose", token=a["token_ref"], n="auto",
                                                 difficulty=a["difficulty"], reactions=a["reactions"]),
          lambda ret: {"data": ret}),
    _spec(actions, "hide", _tok("hide"), _dict_text),
    _spec(actions, "escape", _tok("escape"), lambda ret: {"text": ret["text"], "data": {}}),
    _spec(actions, "ready", _cmd_ready, _dict_text),
    _spec(actions, "trigger", lambda a: _verb("trigger", token=a["token_ref"], target=a["target"],
                                              reactions=a["reactions"]),
          lambda ret: {"text": ret["text"], "data": {}}),
    _spec(actions, "help_action", lambda a: _verb("help", token=a["helper_ref"], ally=a["ally_ref"],
                                                  target=a["target_ref"]),
          lambda ret: {"text": ret["text"], "data": {}}, roller=False),
    _spec(sight, "sight", lambda a: _verb("sight", token=a["ref"], players=bool(a["players"])),
          _dict_text, roller=False),
    _spec(statecard, "statecard", lambda a: _verb("card", token=a["ref"], players=bool(a["players"])),
          _dict_text, roller=False),
]


class Recorder:
    """Collects records while a test runs. Inert unless `current` is set."""

    def __init__(self):
        self.current = None          # the pytest node id being recorded
        self.depth = 0               # >0 inside a recorded call: only the outermost call records
        self.records, self.skipped, self.ran = [], [], []
        self.installed = False
        self._originals = []         # (module, name, function) for every entry point wrapped

    def begin(self, nodeid: str) -> None:
        self.install()
        self.current, self.depth = nodeid, 0
        self.ran.append(nodeid)

    def end(self) -> None:
        self.current = None

    def skip(self, call: str, reason: str) -> None:
        self.skipped.append({"test": self.current, "call": call, "reason": reason})

    def install(self) -> None:
        if self.installed:
            return
        for spec in _ENTRY_POINTS:
            orig = getattr(spec["module"], spec["name"])
            self._originals.append((spec["module"], spec["name"], orig))
            setattr(spec["module"], spec["name"], self._wrap(orig, spec))
        self.installed = True

    @contextlib.contextmanager
    def _pristine(self):
        """The unwrapped entry points, for the check that replays a call. A test
        may have put a spy over one of them (test_tactics_purecore does, on
        execute) and a check that ran through the spy would be counted as a
        second call by the test."""
        saved = [(m, n, getattr(m, n)) for m, n, _ in self._originals]
        for m, n, orig in self._originals:
            setattr(m, n, orig)
        try:
            yield
        finally:
            for m, n, current in saved:
                setattr(m, n, current)

    def _wrap(self, orig, spec):
        sig = inspect.signature(orig)

        @functools.wraps(orig)
        def wrapper(*args, **kwargs):
            if self.current is None or self.depth:
                return orig(*args, **kwargs)
            return self._intercept(orig, spec, sig, args, kwargs)
        return wrapper

    def _intercept(self, orig, spec, sig, args, kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        a, name = bound.arguments, f"{spec['module'].__name__.rsplit('.', 1)[-1]}.{spec['name']}"
        enc = a["enc"]
        roller = a.get("roller") if spec["roller"] else None
        try:
            command = spec["command"](a)
            pre_raw = json.loads(json.dumps(enc.to_dict()))   # dict order kept: what the live call saw
            pre = scrub(plain(pre_raw))                        # sorted keys: what the file will hold
            command = scrub(plain(command))
            if machine_paths([pre, command]):
                raise _Unmappable("the state holds a machine-specific path")
            if roller is not None:
                if roller.supplied:
                    command["rolls"] = [int(x) for x in roller.supplied]
                if roller.supplied_source == "player":
                    command["player_roll"] = True
                if roller.for_me:
                    command["for_me"] = True
        except _Unmappable as why:
            command, pre, pre_raw, why = None, None, None, str(why)
        except TypeError as why:
            command, pre, pre_raw, why = None, None, None, \
                f"arguments are not plain JSON ({why.__class__.__name__})"
        except (AttributeError, KeyError):
            command, pre, pre_raw, why = None, None, None, \
                "the call's arguments do not map to a purecore command"
        if command is None:
            return orig(*bound.args, **bound.kwargs)

        tape, draws = [], []
        had_rng = roller is not None
        saved_rng = roller.rng if had_rng else None
        had_policy = had_rng and hasattr(roller, "policy_rng")
        saved_policy = getattr(roller, "policy_rng", None) if had_rng else None
        if had_rng:
            roller.rng = RecordingRng(saved_rng, tape)
            if had_policy and saved_policy is not None:
                roller.policy_rng = DrawLog(saved_policy, draws)
        if "rng" in a and a["rng"] is not None:                 # policy.choose_auto(rng=...)
            bound.arguments["rng"] = DrawLog(a["rng"], draws)
        real_random, policy.random = policy.random, _PolicyRandomShim(draws)
        self.depth += 1
        exc = None
        try:
            ret = orig(*bound.args, **bound.kwargs)
        except BaseException as caught:                          # noqa: BLE001
            exc = caught
        finally:
            self.depth -= 1
            policy.random = real_random
            if had_rng:
                roller.rng = saved_rng
                if had_policy:
                    roller.policy_rng = saved_policy
                elif hasattr(roller, "policy_rng"):
                    del roller.policy_rng
        self.depth += 1                      # the check below runs purecore.apply: do not record it
        try:
            with self._pristine():
                self._finish(name, spec, pre_raw, pre, command, tape, draws, enc,
                             ret if exc is None else None, exc)
        except Exception as problem:                              # noqa: BLE001
            self.skip(name, f"recording failed: {type(problem).__name__}")
        finally:
            self.depth -= 1
        if exc is not None:
            raise exc
        return ret

    def _finish(self, name, spec, pre_raw, pre, command, tape, draws, enc, ret, exc) -> None:
        if exc is not None and not isinstance(exc, RECORDED_RAISES):
            return self.skip(name, f"raised {type(exc).__name__}, a Python-specific error")
        try:
            post = plain(enc.to_dict())
        except TypeError:
            return self.skip(name, "state is not plain JSON")
        inputs = {"kind": "apply", "command": command, "tape": tape, "policy_draws": draws}
        # 1. Does the command mean what the live call did? Replay it on the state
        #    exactly as the live call saw it (dict order kept) and compare.
        try:
            live = replay_outcome(dict(inputs, state=pre_raw))
        except TraceError as why:
            return self.skip(name, f"the recorded tape does not replay ({why})")
        except TypeError:
            return self.skip(name, "the core refuses a result JSON cannot hold, with a Python TypeError")
        if exc is not None:
            want = _raised(exc)
            if any(live.get(k) != want.get(k) for k in ("raises", "message", "pending")):
                return self.skip(name, "purecore.apply does not refuse the way the live call did")
        else:
            if "state" not in live or canonical(live["state"]) != canonical(post):
                return self.skip(name, "purecore.apply does not reproduce the live call's state")
            if spec["expect"] is not None:
                try:
                    want = plain(spec["expect"](ret))
                except (TypeError, KeyError):
                    return self.skip(name, "the live call's result is not plain JSON")
                got = live["events"][-1]
                if any(canonical(got.get(k)) != canonical(v) for k, v in want.items()):
                    return self.skip(name, "purecore.apply does not reproduce the live call's result")
        # 2. The file holds the state with sorted keys (canonical JSON), so the
        #    stored result is what apply gives for THAT state. It differs from the
        #    live outcome only where the engine iterates a dict (the order of
        #    `affected`), and it is what a replay reproduces.
        try:
            outcome = replay_outcome(dict(inputs, state=pre))
        except TraceError as why:
            return self.skip(name, f"the tape does not replay on the sorted state ({why})")
        if ("state" in outcome) != ("state" in live):
            return self.skip(name, "the outcome depends on the order of the state's dictionaries")
        if machine_paths(outcome):
            return self.skip(name, "the result holds a machine-specific path")
        self.records.append((self.current, build_record(
            self.current, "apply", pre, command, command.get("seed"), tape, draws, outcome)))

    def dump(self, directory: pathlib.Path) -> None:
        directory = pathlib.Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"records": [[t, r] for t, r in self.records], "skipped": self.skipped,
                   "ran": self.ran}
        (directory / "raw.json").write_bytes(canonical(payload))


RECORDER = Recorder()


def _eligible(nodeid: str) -> bool:
    path = nodeid.split("::", 1)[0]
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_tactics_") and name not in EXCLUDED_MODULES


class _Entropy:
    """A deterministic stand-in for `secrets` while recording: an unseeded roller
    draws its seed from here, derived from the test id and a counter, so a second
    recording rolls the same faces. A test that passes only for some seeds would
    fail while recording, which is a finding, not something to paper over."""

    def __init__(self, nodeid: str):
        self._base, self._n = zlib.crc32(nodeid.encode("utf-8")), 0

    def randbits(self, k):
        self._n += 1
        return dice.new_rng(f"{self._base}:{self._n}").getrandbits(k)

    def __getattr__(self, name):
        import secrets
        return getattr(secrets, name)


@contextlib.contextmanager
def recording(nodeid: str):
    """Record the engine calls made inside this block, tagged with `nodeid`.
    A no-op for a test outside tests/test_tactics_*.py."""
    if not _eligible(nodeid):
        yield
        return
    RECORDER.begin(nodeid)
    real, dice.secrets = dice.secrets, _Entropy(nodeid)
    try:
        yield
    finally:
        dice.secrets = real
        RECORDER.end()


def flush(directory) -> None:
    """Write what this run recorded. Called once, from tests/conftest.py."""
    RECORDER.dump(directory)


# ─── seeded randomised grid cases ─────────────────────────────────────────────

_SHAPES = ("sphere", "cylinder", "cone", "line", "cube")
# One entry per kind of case. Case i is combos[i % len(combos)], so every query
# and every area shape appears at both levels, evenly.
_COMBOS = ([("query", "reachable", None), ("query", "cover", None), ("query", "line_of_sight", None)]
           + [("query", "area", s) for s in _SHAPES]
           + [("grid", "reachable", None), ("grid", "cover", None), ("grid", "line_of_sight", None)]
           + [("grid", "area", s) for s in _SHAPES])


def _board(rng: random.Random) -> dict:
    """A random map: 8x8 to 20x20, 0-30% walls, a sprinkle of the other terrain."""
    w, h = rng.randint(8, 20), rng.randint(8, 20)
    walls = rng.randint(0, 30)
    rows = []
    for _ in range(h):
        row = []
        for _ in range(w):
            roll = rng.randint(1, 100)
            if roll <= walls:
                row.append("#")
            elif roll <= walls + 4:
                row.append(",")
            elif roll <= walls + 6:
                row.append("~")
            elif roll <= walls + 8:
                row.append("o")
            elif roll <= walls + 9:
                row.append("^")
            else:
                row.append(".")
        rows.append("".join(row))
    return {"name": "grid-random", "rows": rows, "diagonals": rng.choice(["5", "5-10-5"])}


def _place(rng: random.Random, grid: Grid, how_many: int) -> list:
    """1-4 tokens of size M (1x1) or L (2x2) on walkable squares, not overlapping."""
    placed, taken = [], set()
    for _ in range(how_many):
        for _try in range(40):
            size = rng.choice([1, 1, 2])
            x, y = rng.randrange(grid.width - size + 1), rng.randrange(grid.height - size + 1)
            body = footprint((x, y), (size, size))
            if body & taken or any(grid.terrain(p)["cost"] is None for p in body):
                continue
            taken |= body
            placed.append((x, y, size))
            break
    return placed or [(0, 0, 1)]


def _free(rng: random.Random, grid: Grid, avoid: set = frozenset()) -> tuple:
    for _try in range(200):
        p = (rng.randrange(grid.width), rng.randrange(grid.height))
        if p not in avoid:
            return p
    return (0, 0)


def generate_grid_case(seed: int, i: int) -> dict:
    """Case number `i` of the set drawn from `seed`. Every case has its own rng,
    derived from (seed, i) with string seeding, so any case can be drawn alone and
    phase 15 can draw 10,000 without replaying the first 500."""
    rng = dice.new_rng(f"tactics-grid:{seed}:{i}")
    kind, q, shape = _COMBOS[i % len(_COMBOS)]
    board = _board(rng)
    grid = Grid.from_dict(board)
    spots = _place(rng, grid, rng.randint(1, 4))
    source = f"grid-random:{seed}:{i}"

    tokens, bodies = {}, {}
    for n, (x, y, size) in enumerate(spots, 1):
        tid = f"t{n}"
        bodies[tid] = footprint((x, y), (size, size))
        tokens[tid] = Token(id=tid, name=f"T{n}", side="pc" if n == 1 else "enemy", x=x, y=y, hp=10,
                            max_hp=10, ac=12, width=size, height=size,
                            speed=rng.choice([20, 30, 40]), swim_speed=rng.choice([0, 0, 30]))
    ids = list(tokens)
    solo = ids[0]
    other = ids[1] if len(ids) > 1 else ids[0]

    if kind == "query":
        enc = Encounter(campaign="grid-random", grid=board, tokens=tokens, order=list(ids),
                        round=1, turn_index=0)
        mover = tokens[solo]
        enc.turn = TurnState(actor=solo, movement_budget=mover.speed, base_speed=mover.speed,
                             movement_used=rng.choice([0, 0, 5, 10]))
        state = plain(enc.to_dict())
        if q == "reachable":
            command = {"query": q, "token": rng.choice(ids)}
        elif q == "area":
            caster = _free(rng, grid)
            target = _free(rng, grid, {caster}) if shape in ("cone", "line") or rng.random() < .5 else caster
            from_self = True if shape in ("cone", "line") else rng.choice([True, False])
            if not from_self and shape == "cube":
                target = _free(rng, grid)
            command = {"query": q, "shape": shape, "size": rng.choice([5, 10, 15, 20, 30, 60]),
                       "caster": label(caster), "target": label(target),
                       "width": rng.choice([5, 10]), "from_self": from_self}
        else:
            a, b = (solo, other) if len(ids) > 1 else (solo, solo)
            command = ({"query": q, "attacker": a, "target": b} if q == "cover"
                       else {"query": q, "a": a, "b": b})
        value = _query_outcome("query", state, command)
    else:
        state = plain(board)
        if q == "reachable":
            start = _pos(spots[0])
            others = set().union(*(bodies[t] for t in ids[1:])) if len(ids) > 1 else set()
            command = {"query": q, "start": list(start), "budget": rng.choice([10, 20, 30, 40, 60]),
                       "blocked": sorted(map(list, others if rng.random() < .5 else set())),
                       "occupied": sorted(map(list, others)), "crawling": rng.random() < .2,
                       "swim": rng.random() < .3, "parity": rng.choice([0, 0, 1])}
        elif q == "area":
            caster = _free(rng, grid)
            from_self = True if shape in ("cone", "line") else rng.choice([True, False])
            target = _free(rng, grid, {caster})
            command = {"query": q, "shape": shape, "size": rng.choice([5, 10, 15, 20, 30, 60]),
                       "caster": list(caster), "target": list(target),
                       "width": rng.choice([5, 10]), "from_self": from_self}
        else:
            a_sq = _pos(spots[0])
            b_sq = _pos(spots[1]) if len(spots) > 1 else _free(rng, grid, {a_sq})
            a_size = (spots[0][2],) * 2
            b_size = (spots[1][2],) * 2 if len(spots) > 1 else (1, 1)
            if q == "cover":
                crowd = set().union(*(bodies[t] for t in ids)) if rng.random() < .7 else set()
                command = {"query": q, "attacker": list(a_sq), "target": list(b_sq),
                           "creatures": sorted(map(list, crowd)),
                           "attacker_size": list(a_size), "target_size": list(b_size)}
            else:
                command = {"query": q, "a": list(a_sq), "b": list(b_sq)}
        value = _query_outcome("grid", state, command)
    return build_record(source, kind, state, plain(command), seed, [], [], value)


def generate_grid_cases(seed: int = GRID_SEED, n: int = GRID_CASES, start: int = 0) -> list:
    """Cases start..start+n-1 of the set drawn from `seed`. The generator is the
    same one phase 15 draws its 10,000 from; the inputs are stored in each record,
    so a replay never touches an RNG."""
    return [generate_grid_case(seed, i) for i in range(start, start + n)]


# ─── files, manifest, generation ──────────────────────────────────────────────

def dump_file(header: dict, records: list) -> bytes:
    """A JSON file with one canonical record per line, so a diff names the record.
    Still one valid JSON document; every byte comes from `canonical`."""
    head = canonical(header)[:-1]
    body = b",\n".join(canonical(r) for r in records)
    return head + b',"traces":[\n' + body + b"\n]}\n"


def _header(about: str) -> dict:
    return {"_about": about, "schema": SCHEMA}


_ABOUT = ("Generated by scripts/tactics/traces.py --generate. Do not edit by hand and do not "
          "regenerate to make a failing replay pass: the question a failure asks is whether the "
          "rules changed on purpose. If they did, regenerate in the same PR.")


def grid_file(cases: list, seed: int = GRID_SEED) -> bytes:
    """The bytes of grid-random.json for these cases."""
    return dump_file(dict(_header(_ABOUT), seed=seed, cases=len(cases)), cases)


def load_file(path) -> list:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))["traces"]


def trace_files(directory=TRACES_DIR) -> list:
    return sorted(p for p in pathlib.Path(directory).glob("*.json") if p.name != "manifest.json")


def _git(*args) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return out.stdout.strip() if out.returncode == 0 else ""


def engine_sha(allow_dirty: bool) -> str:
    """The commit the traces were recorded from. The recording must come from a
    clean tree (traces themselves excluded), so this names the PARENT of the
    commit that adds them; a dirty tree names nothing and is refused."""
    sha = _git("rev-parse", "HEAD") or "unknown"
    dirty = _git("status", "--porcelain", "--", ".", ":(exclude)tests/traces")
    if dirty and not allow_dirty:
        raise SystemExit("traces: the tree has uncommitted changes; commit them first so the "
                         "manifest can name the commit the traces came from (or --allow-dirty "
                         "for a scratch run):\n" + dirty)
    return sha + ("+dirty" if dirty else "")


def test_files(modules=None) -> list:
    """tests/test_tactics_*.py (minus the replay module), or just the named ones."""
    found = sorted(p for p in (ROOT / "tests").glob("test_tactics_*.py") if p.name not in EXCLUDED_MODULES)
    if modules:
        wanted = {m if m.endswith(".py") else m + ".py" for m in modules}
        unknown = wanted - {p.name for p in found}
        if unknown:
            raise SystemExit(f"traces: no such test module: {', '.join(sorted(unknown))}")
        found = [p for p in found if p.name in wanted]
    return [str(p.relative_to(ROOT)) for p in found]


def collect_ids(modules=None) -> list:
    """The pytest ids of every test in tests/test_tactics_*.py, minus the replay module."""
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          *test_files(modules)],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                         env=dict(os.environ, PYTHONPATH=str(ROOT)))
    ids = sorted(line.strip() for line in out.stdout.splitlines() if "::" in line)
    if out.returncode or not ids:
        raise SystemExit(f"traces: could not collect the tests:\n{out.stdout[-2000:]}{out.stderr[-2000:]}")
    return ids


def record_suite(raw_dir: pathlib.Path, hashseed: str = None, modules=None) -> dict:
    """Run tests/test_tactics_*.py once with the recorder on; return its raw output."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), **{ENV_VAR: str(raw_dir)})
    if hashseed is not None:
        env["PYTHONHASHSEED"] = str(hashseed)
    env.pop("PYTEST_ADDOPTS", None)
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                          *test_files(modules)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    if out.returncode:
        raise SystemExit("traces: the suite failed while recording, so nothing was written:\n"
                         + out.stdout[-4000:] + out.stderr[-2000:])
    return json.loads((raw_dir / "raw.json").read_text(encoding="utf-8"))


# Why a test has no trace, by module, when it made no recordable engine call at all.
_NO_CALL = {
    "test_tactics_ai.py": "asserts ai option building or multiattack parsing directly; the options "
                          "that run are replayed in the choose traces",
    "test_tactics_cli.py": "CLI edge (start, sheets, maps, end, write-back, error text); outside the "
                           "core's commands",
    "test_tactics_encounter_generator.py": "encounter building and budget: out of the core "
                                           "(phase 16 TSP-12 gives it function-level traces)",
    "test_tactics_engine.py": "asserts an engine helper directly (spotting, move options, initiative "
                              "order, state validation); no purecore command wraps it",
    "test_tactics_fightq.py": "fight queue and its parser; not a core command",
    "test_tactics_grid.py": "pure geometry asserted on Grid directly; the same queries are replayed "
                            "from grid-random.json (kind grid)",
    "test_tactics_journal.py": "journal and receipt writing: an edge of the core, not the core",
    "test_tactics_play.py": "drives the interactive play loop and its prompts; not a core command",
    "test_tactics_policy.py": "asserts policy scoring directly (profile, pick); the whole choose auto "
                              "decision is replayed in the choose traces",
    "test_tactics_purecore.py": "tests the core's own guarantees (sandboxed import, cross-process "
                                "bytes, an unseeded roll is refused); not a rules scenario",
    "test_tactics_rules_dnd5e.py": "asserts the dnd5e rules module directly (no engine entry point); "
                                   "its outcomes appear in the attack/cast/check traces",
    "test_tactics_sight.py": "asserts the sight and fog snapshot and its redaction; a display read, "
                             "not a core command",
    "test_tactics_spells.py": "asserts an effects, area or snapshot helper directly; no purecore "
                              "command wraps it",
    "test_tactics_statecard.py": "asserts map compile, landmarks and handle naming; no core command",
}
_DEFAULT_NO_CALL = "no engine entry point was called that purecore can express"


def _spread(items: list, limit: int) -> list:
    """At most `limit` items, always the first and the last, evenly spaced between."""
    if len(items) <= limit:
        return items
    at = sorted({round(i * (len(items) - 1) / (limit - 1)) for i in range(limit)})
    return [items[i] for i in at]


def assemble(raw: dict, ids: list, engine: str, seed: int, n: int, modules=None) -> dict:
    """{filename: bytes} for the whole tests/traces tree, from one recording."""
    made, sampled = {}, {}
    for test, rec in raw["records"]:
        made.setdefault(test, []).append(rec)
    by_id, by_test = {}, {}
    for test, recs in made.items():
        unique = list({r["id"]: r for r in recs}.values())         # first call order, repeats dropped
        kept = _spread(unique, MAX_PER_TEST)
        if len(kept) < len(unique):
            sampled[test] = {"made": len(unique), "kept": len(kept)}
        for rec in kept:
            by_id.setdefault(rec["id"], []).append(rec)
            by_test.setdefault(test, set()).add(rec["id"])
    records = {}
    for rid, recs in by_id.items():
        # the same inputs reached by several tests: the lowest test id owns it
        recs.sort(key=lambda r: r["source"])
        records[rid] = recs[0]
    files = {}
    per_module = {}
    for rec in records.values():
        per_module.setdefault(_module_of(rec["source"]).replace(".py", ".json"), []).append(rec)
    for name, recs in per_module.items():
        recs.sort(key=lambda r: r["id"])
        files[name] = dump_file(_header(_ABOUT), recs)
    grid = generate_grid_cases(seed, n)
    files["grid-random.json"] = grid_file(grid, seed)

    skipped = {}
    for s in raw["skipped"]:
        skipped.setdefault(s["test"], []).append(f"{s['call']}: {s['reason']}")
    tests = {}
    for tid in ids:
        mine = sorted(by_test.get(tid, ()))
        if mine:
            entry = {"traces": mine}
            if tid in sampled:
                entry["sampled"] = sampled[tid]
            if tid in skipped:
                entry["unrecorded_calls"] = sorted(set(skipped[tid]))
        elif tid in skipped:
            entry = {"reason": "; ".join(sorted(set(skipped[tid])))}
        else:
            entry = {"reason": _NO_CALL.get(_module_of(tid), _DEFAULT_NO_CALL)}
        tests[tid] = entry
    covered = sum(1 for e in tests.values() if "traces" in e)
    manifest = {
        "_about": _ABOUT, "schema": SCHEMA, "engine": engine,
        "counts": {"tests": len(tests), "tests_with_traces": covered,
                   "tests_with_reason": len(tests) - covered,
                   "apply_traces": len(records), "grid_cases": len(grid),
                   "grid_seed": seed},
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in sorted(files.items())},
        "tests": tests,
    }
    if modules:
        manifest["partial"] = sorted(modules)
    files["manifest.json"] = (json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False)
                              + "\n").encode("utf-8")
    return files


def write_tree(files: dict, out: pathlib.Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.json"):
        stale.unlink()
    for name, data in files.items():
        (out / name).write_bytes(data)


def generate(out: pathlib.Path = TRACES_DIR, *, allow_dirty: bool = False, hashseed: str = None,
             seed: int = GRID_SEED, n: int = GRID_CASES, modules=None) -> dict:
    if modules and pathlib.Path(out).resolve() == TRACES_DIR.resolve():
        raise SystemExit("traces: --modules writes a partial tree; give it another --out")
    sha = engine_sha(allow_dirty)
    ids = collect_ids(modules)
    with tempfile.TemporaryDirectory(prefix="tactics-traces-") as raw_dir:
        raw = record_suite(pathlib.Path(raw_dir), hashseed, modules)
    files = assemble(raw, ids, sha, seed, n, modules)
    write_tree(files, pathlib.Path(out))
    return json.loads(files["manifest.json"])


def check_manifest(manifest: dict, ids: list, directory=TRACES_DIR) -> list:
    """Problems with a manifest against a live list of test ids; [] when sound."""
    problems = []
    tests = manifest["tests"]
    for tid in sorted(set(ids) - set(tests)):
        problems.append(f"missing from the manifest: {tid}")
    for tid in sorted(set(tests) - set(ids)):
        problems.append(f"in the manifest but no longer collected: {tid}")
    known = set()
    for path in trace_files(directory):
        known |= {r["id"] for r in load_file(path)}
    for tid, entry in sorted(tests.items()):
        if not entry.get("traces") and not entry.get("reason"):
            problems.append(f"neither traces nor a reason: {tid}")
        for rid in entry.get("traces", ()):
            if rid not in known:
                problems.append(f"{tid} names trace {rid}, which is in no trace file")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--generate", action="store_true",
                    help="record the suite and write tests/traces/ (run from a clean tree)")
    ap.add_argument("--out", default=str(TRACES_DIR), help="where to write (default tests/traces)")
    ap.add_argument("--allow-dirty", action="store_true", help="scratch run: do not require a clean tree")
    ap.add_argument("--hashseed", help="PYTHONHASHSEED for the recording subprocess")
    ap.add_argument("--modules", help="comma-separated test modules (partial run, needs another --out)")
    ap.add_argument("--check-manifest", metavar="IDS_FILE",
                    help="check tests/traces/manifest.json against a file of live test ids")
    args = ap.parse_args(argv)
    if args.check_manifest:
        ids = [x.strip() for x in pathlib.Path(args.check_manifest).read_text(encoding="utf-8").splitlines()
               if "::" in x and _module_of(x.strip()) not in EXCLUDED_MODULES]
        manifest = json.loads((TRACES_DIR / "manifest.json").read_text(encoding="utf-8"))
        problems = check_manifest(manifest, ids)
        print("\n".join(problems) if problems else f"manifest ok: {len(ids)} tests")
        return 1 if problems else 0
    if not args.generate:
        print("nothing written; pass --generate")
        return 2
    manifest = generate(pathlib.Path(args.out), allow_dirty=args.allow_dirty, hashseed=args.hashseed,
                        modules=args.modules.split(",") if args.modules else None)
    c = manifest["counts"]
    print(f"wrote {args.out}: {c['apply_traces']} apply traces for {c['tests_with_traces']} of "
          f"{c['tests']} tests ({c['tests_with_reason']} with a reason), {c['grid_cases']} grid cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
