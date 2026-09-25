"""Fakes for the localdm tests (not a test module itself)."""
from __future__ import annotations

import pathlib
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import llm              # noqa: E402
from localdm.bridge import Result    # noqa: E402,F401  (re-exported for the tests)


class FakeClient:
    """responder(model, messages, role) -> reply text. Records every call."""

    def __init__(self, responder):
        self.responder = responder
        self.calls = []
        self._lock = threading.Lock()

    def chat(self, model, messages, *, max_tokens=600, temperature=0.8, role="dm"):
        with self._lock:
            self.calls.append((model, role, messages))
        text = self.responder(model, messages, role)
        return llm.Reply(text, model, 100, 10, 0.0)

    def roles(self):
        return [c[1] for c in self.calls]


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

    def run(self, args):
        self.ran.append(list(args))
        h = self.handlers.get(args[0])
        return h(args) if h else Result(1, f"(fake) {args[0]} refused")
