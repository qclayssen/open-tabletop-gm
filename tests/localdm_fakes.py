"""Fakes for the localdm tests (not a test module itself)."""
from __future__ import annotations

import pytest

import pathlib
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import llm              # noqa: E402
from localdm.bridge import Result    # noqa: E402,F401  (re-exported for the tests)


class FakeClient:
    """responder(model, messages, role) -> reply text. Records every call.

    `finish_reason` scripts the truncation the real endpoint reports, so a test can
    replay a `finish_reason: length` overrun. A str applies to every call; a list is
    consumed one entry per call, and the last entry repeats, so
    `["length", "stop"]` is "overrun once, then answer properly".
    """

    def __init__(self, responder, finish_reason=None):
        self.responder = responder
        self.finish_reason = finish_reason
        self.calls = []
        self.reasoning = []
        self._lock = threading.Lock()

    def _finish(self) -> str:
        if self.finish_reason is None:
            return "stop"
        if isinstance(self.finish_reason, str):
            return self.finish_reason
        item = self.finish_reason[0] if len(self.finish_reason) > 1 else self.finish_reason[0]
        if len(self.finish_reason) > 1:
            self.finish_reason = self.finish_reason[1:]
        return item

    def chat(self, model, messages, *, max_tokens=600, temperature=0.8, role="dm",
             reasoning=None):
        with self._lock:
            self.calls.append((model, role, messages))
            self.reasoning.append((role, reasoning))
        text = self.responder(model, messages, role)
        return llm.Reply(text, model, 100, max_tokens, 0.0, self._finish())

    def roles(self):
        return [c[1] for c in self.calls]

    def dm_calls(self):
        """Only the DM tier's calls.

        A guardrail trip also consults the advisor council, so len(self.calls)
        no longer means "how many times did we ask the DM". Tests about retry
        counts want this one.
        """
        return [c for c in self.calls if c[1] == "dm"]

    def advisor_roles(self):
        return [c[1] for c in self.calls if c[1].startswith("advisor")]


class FakeBridge:
    """Scripted snapshots and command results. The last snapshot repeats.
    `handlers` maps a command's first word to function(args) -> Result;
    anything else is refused with code 1."""

    def __init__(self, snapshots=None, handlers=None):
        self.snapshots = list(snapshots or [None])
        self.handlers = handlers or {}
        self.ran = []

    def snapshot(self):
        return self.snapshots[0] if len(self.snapshots) == 1 else self.snapshots.pop(0)

    def is_combat_active(self) -> bool:
        snap = self.snapshot()
        return bool(snap and snap.get("status") == "active")

    def run(self, args):
        self.ran.append(list(args))
        h = self.handlers.get(args[0])
        return h(args) if h else Result(1, f"(fake) {args[0]} refused")


class FixedRoll:
    """A stand-in for `play._CHECK_RNG` that returns a chosen d20 and counts calls.

    Tests that need to know *what* a check rolled, or *how many times* one was
    rolled, used to do it by patching the `random` module:

        monkeypatch.setattr(random, "randint", lambda a, b: rolls.append((a, b)) or 14)

    That worked only because `play.py` read the module-level generator. It does
    not any more -- the check rolls off `_dice.new_rng()` so the face has a seed
    on it (dnd-gm#306) -- so the patch became a no-op that silently stopped
    counting. Worse than a failure: a test asserting "one roll" or "no roll"
    stopped being able to see either.

    The seam is this module's stream now, so a stub is both narrower and honest:
    it cannot accidentally divert some other module's dice.
    """

    def __init__(self, face: int = 14, calls: list | None = None):
        self.face = face
        self.seed_value = face
        self.calls = calls if calls is not None else []

    def randint(self, low, high):
        assert (low, high) == (1, 20), f"a skill check is a d20, got d{high - low + 1}"
        self.calls.append((low, high))
        return self.face


def fixed_check_roll(monkeypatch, face: int = 14, calls: list | None = None) -> list:
    """Point `play._CHECK_RNG` at a `FixedRoll`; returns the call log."""
    from localdm import play
    stub = FixedRoll(face, calls)
    monkeypatch.setattr(play, "_CHECK_RNG", stub)
    return stub.calls


class ForbiddenRoll:
    """A check stream that fails the test if it is ever used.

    For the assertions of the form "no die was rolled", where the point is that
    the engine refused and the table was told why. These used to patch the
    `random` module to `pytest.fail`, which stopped being able to see anything
    once the check moved onto `play._CHECK_RNG` -- an assertion that cannot fail
    is not an assertion, and this one had quietly become decoration.
    """

    seed_value = None

    def randint(self, low, high):
        pytest.fail(f"a d{high - low + 1} was rolled where none should be "
                    f"(asked for {low}..{high})")


def forbid_check_roll(monkeypatch) -> None:
    """Make any headless skill check in this test a failure."""
    from localdm import play
    monkeypatch.setattr(play, "_CHECK_RNG", ForbiddenRoll())
