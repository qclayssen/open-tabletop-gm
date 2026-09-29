"""queue_claim.py: the claim-then-read primitive every .input_queue consumer uses.

These are the tests for the data-loss window (BUGS.md B6). The property under
test is not "the file is read"; it is that an action written by the Flask app
*while a consumer is mid-drain* survives, because the app replaces the file
atomically and a read-then-unlink consumer deletes the version it never read.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import pytest

DISPLAY = pathlib.Path(__file__).resolve().parents[1] / "display"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(DISPLAY / filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


qc = _load("queue_claim", "queue_claim.py")

ACTION = "[Mira]: I open the door"


@pytest.fixture
def queue(tmp_path):
    return tmp_path / ".input_queue"


def test_a_queued_action_is_read_and_the_file_released(queue):
    queue.write_text(ACTION, encoding="utf-8")
    text, delivered = qc.claim_and_read(queue)
    assert (text, delivered) == (ACTION, True)
    assert not queue.exists()
    assert not (queue.parent / (queue.name + ".taken")).exists()


def test_an_absent_queue_is_not_an_error(queue):
    """The common case: nothing queued, nothing to do."""
    assert qc.claim_and_read(queue) == ("", True)


def test_an_empty_queue_is_not_an_error(queue):
    queue.write_text("", encoding="utf-8")
    assert qc.claim_and_read(queue) == ("", True)
    assert not queue.exists()


# ── the data-loss window (B6) ───────────────────────────────────────────────

def test_an_action_written_mid_drain_survives(queue, monkeypatch):
    """The defect itself, and the reason for the whole module.

    The Flask app appends via write-tmp + `os.replace`, so a player who taps Send
    while a consumer is draining lands a brand new file at the queue path. A
    consumer that read the old file and then unlinked the path destroys that new
    action unread: the player saw "Sent" and nothing is ever narrated.

    The claim moves the old file aside first, so the new one is written to a path
    this consumer has no further business touching. Simulated by writing a fresh
    action the instant the claim happens.
    """
    queue.write_text(ACTION, encoding="utf-8")
    real_replace = os.replace

    def replace_and_a_player_taps_send(src, dst, *a, **k):
        real_replace(src, dst, *a, **k)
        with open(queue, "w", encoding="utf-8") as handle:
            handle.write("[Piper]: I wait")

    monkeypatch.setattr(qc.os, "replace", replace_and_a_player_taps_send)

    text, delivered = qc.claim_and_read(queue)

    assert delivered
    assert text == ACTION, "the consumer must deliver the file it claimed"
    assert queue.exists(), "the concurrent action was deleted unread"
    assert queue.read_text(encoding="utf-8") == "[Piper]: I wait"


def test_a_read_then_unlink_consumer_would_lose_that_action(queue, monkeypatch):
    """The same scenario against the old pattern, to prove the test has teeth.

    This is not a claim about the old code being reachable; it is the control.
    If this stops failing, the test above has stopped testing anything.
    """
    queue.write_text(ACTION, encoding="utf-8")

    def read_then_unlink():
        with open(queue, encoding="utf-8") as handle:
            handle.read()
        with open(queue, "w", encoding="utf-8") as handle:
            handle.write("[Piper]: I wait")
        os.unlink(queue)

    read_then_unlink()
    assert not queue.exists(), "if this passes, the race is not being modelled"


# ── the narrower window the claim itself opens ─────────────────────────────

def test_a_failed_read_after_the_claim_restores_the_queue(queue):
    """Once the replace has run the actions exist only in .taken.

    Anything that throws after that leaves them in a file no later poll reads,
    which is the same data loss in a different place. So a failure restores.
    Truncated multi-byte writes make this real: a read can raise UnicodeError.
    """
    # A lone continuation byte is not valid UTF-8, so the read raises.
    queue.write_bytes(b"[Mira]: I open the \xffdoor")
    real_open = open

    def boom(target, *a, **k):
        if str(target).endswith(".taken"):
            raise OSError("simulated read failure after the claim")
        return real_open(target, *a, **k)

    qc.open = boom
    try:
        text, delivered = qc.claim_and_read(queue)
    finally:
        qc.open = real_open

    assert delivered is False, "a failed read must not report delivery"
    assert text == ""
    assert queue.exists(), "the actions were stranded in .taken"
    assert not (queue.parent / (queue.name + ".taken")).exists()


def test_a_decode_failure_after_the_claim_also_restores(queue):
    """Not just OSError: invalid bytes are the likelier real cause."""
    queue.write_bytes(b"[Mira]: I open the \xffdoor")
    _text, delivered = qc.claim_and_read(queue)
    assert delivered is False
    assert queue.exists()
    assert queue.read_bytes().endswith(b"\xffdoor"), "the bytes must come back intact"


def test_a_restore_that_itself_fails_leaves_the_file_recoverable(queue, monkeypatch, capsys):
    """If the restore cannot happen, .taken is named loudly and left in place.

    The one thing this path must never do is delete it: those are the actions,
    and a human can still recover them from a named file.
    """
    queue.write_text(ACTION, encoding="utf-8")
    real_replace = os.replace
    calls = {"n": 0}

    def replace_failing_the_restore(src, dst, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:      # the claim succeeds
            return real_replace(src, dst, *a, **k)
        raise OSError("simulated restore failure")

    monkeypatch.setattr(qc.os, "replace", replace_failing_the_restore)
    qc.open = lambda target, *a, **k: (_ for _ in ()).throw(OSError("read failed"))

    text, delivered = qc.claim_and_read(queue)

    assert (text, delivered) == ("", False)
    claimed = queue.parent / (queue.name + ".taken")
    assert claimed.exists(), "the actions were destroyed instead of named"
    assert claimed.read_text(encoding="utf-8") == ACTION
    assert "CRITICAL" in capsys.readouterr().out


# ── one implementation, so there is one thing to get wrong ─────────────────

@pytest.mark.parametrize("consumer", [
    "wrapper.py", "autorun_wait.py", "check_input.py", "drain_queue.py",
])
def test_every_consumer_uses_the_shared_primitive(consumer):
    """B6 was four hand-rolled copies of one idea, two of them wrong.

    `drain_queue.py` originally copied the read-then-unlink pattern out of
    `wrapper.py` rather than out of the two correct lines beside it in
    `check_input.py`. A private second copy is how that recurs, so each consumer
    must delegate, and none may claim the file itself any more.
    """
    src = (DISPLAY / consumer).read_text(encoding="utf-8")
    assert "queue_claim.claim_and_read" in src, f"{consumer} does not use the shared helper"
    assert "os.replace(" not in src, f"{consumer} rolled its own claim again"
    assert "os.unlink(Q" not in src and "os.unlink(QUEUE_FILE" not in src, \
        f"{consumer} unlinks the queue directly again"
