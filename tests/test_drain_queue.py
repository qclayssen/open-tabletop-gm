"""drain_queue.py must never lose a player's action, and must never deliver one twice.

The drainer is the path a Party Input action takes to reach the GM. The Flask app
writes `.input_queue` concurrently via write-tmp + `os.replace`, so the drainer
cannot read-then-unlink: a player pressing Send between the read and the unlink
would have that action deleted without it ever being read (BUGS.md B1).

Claiming with `os.replace` first fixes that, and introduces the hazard these tests
cover: once the replace has happened the actions exist only in `.taken`, so any
failure after it must put them back rather than leave them stranded.

The companion `gm-watch.sh` is covered by `test_gm_watch_sh.py`.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "display"


def _load_drain():
    spec = importlib.util.spec_from_file_location("drain_queue_under_test",
                                                  str(DISPLAY / "drain_queue.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drain_queue_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


ACTION = [{"character": "Mira", "text": "I open the door"}]


class DrainQueue(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.q = self.dir / ".input_queue"
        self.mod = _load_drain()
        self.mod.QUEUE_FILE = str(self.q)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, payload=ACTION):
        self.q.write_text(json.dumps(payload), encoding="utf-8")

    def _run(self, *argv):
        """Run main() with argv, returning (stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        old = sys.argv
        sys.argv = ["drain_queue.py", *argv]
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = self.mod.main()
        finally:
            sys.argv = old
        return rc, out.getvalue(), err.getvalue()

    # ── the normal path ─────────────────────────────────────────────────────

    def test_an_action_is_delivered_and_the_queue_emptied(self):
        self._write()
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("I open the door", out)
        self.assertFalse(self.q.exists(), "the queue must be consumed")

    def test_no_claimed_file_is_left_behind(self):
        """`.taken` must be gone on success, or the next run trips over it."""
        self._write()
        self._run()
        self.assertFalse(pathlib.Path(str(self.q) + ".taken").exists())

    def test_peek_reports_without_consuming(self):
        self._write()
        rc, out, _ = self._run("--peek")
        self.assertEqual(rc, 0)
        self.assertIn("I open the door", out)
        self.assertTrue(self.q.exists(), "--peek must not consume")

    def test_an_empty_queue_is_not_an_error(self):
        self.q.write_text("[]", encoding="utf-8")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)

    # ── the data-loss hazard (B1) ───────────────────────────────────────────

    def test_the_queue_is_claimed_before_it_is_read(self):
        """The claim must precede the read.

        A read-then-unlink drainer loses an action written between the two, because
        the concurrent writer's `os.replace` lands in a file that is then unlinked.
        So the sequence is: replace first, then read the claimed file.
        """
        src = (DISPLAY / "drain_queue.py").read_text(encoding="utf-8")
        claim = src.index("os.replace(QUEUE_FILE, claimed)")
        read = src.index("open(claimed", claim)
        self.assertLess(claim, read,
                        "drain_queue.py reads before it claims: an action sent in "
                        "between would be deleted unread")

    def test_the_input_trigger_is_never_consumed(self):
        """B5: `.input_trigger` is not a drain artefact. It is the signal wrapper.py
        polls to know it may inject, so one drain cycle must not swallow it."""
        trigger = pathlib.Path(str(self.q) + ".trigger")
        self._write()
        trigger.write_text("1", encoding="utf-8")
        self._run()
        self.assertTrue(trigger.exists(), "the drainer deleted .input_trigger")

    # ── recovery: a failure after the claim must not strand the actions ─────

    def test_a_failure_after_claiming_restores_the_queue(self):
        """Once `os.replace` has run, the actions exist only in `.taken`.

        Anything that throws after that point — a read error, a decode failure —
        would otherwise leave them in a file no later poll looks at, which is the
        same data loss the claim was added to prevent, in a narrower window.
        """
        self._write()
        real_open = open

        def boom(f, *a, **k):
            if str(f).endswith(".taken"):
                raise OSError("simulated read failure after the claim")
            return real_open(f, *a, **k)

        self.mod.open = boom
        rc, _, err = self._run()

        self.assertTrue(self.q.exists(),
                        f"the claimed actions were stranded after the failure: {err}")
        self.assertEqual(json.loads(self.q.read_text(encoding="utf-8")), ACTION)
        self.assertFalse(pathlib.Path(str(self.q) + ".taken").exists())
        self.assertIn("restored", err.lower())

    def test_a_failure_after_claiming_does_not_also_deliver_the_action(self):
        """Restoring and delivering would hand the same action to the GM twice."""
        self._write()
        real_open = open

        def boom(f, *a, **k):
            if str(f).endswith(".taken"):
                raise OSError("simulated read failure after the claim")
            return real_open(f, *a, **k)

        self.mod.open = boom
        _, out, _ = self._run()
        self.assertNotIn("I open the door", out,
                         "the action was delivered *and* left queued: it would be "
                         "narrated twice")

    def test_the_next_poll_recovers_a_restored_action(self):
        """The restore only matters if a later run actually picks the action up."""
        self._write()
        real_open = open

        def boom(f, *a, **k):
            if str(f).endswith(".taken"):
                raise OSError("simulated read failure after the claim")
            return real_open(f, *a, **k)

        self.mod.open = boom
        self._run()                                   # fails, restores

        self.mod.open = real_open                     # the environment recovers
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("I open the door", out)
        self.assertFalse(self.q.exists())

    def test_a_malformed_queue_does_not_crash_the_drain(self):
        """A player action written by hand, or a half-written file, must not take
        the drainer down — the watcher is a serial loop."""
        self.q.write_text("[Mira]: I open the door\n[Piper]: I wait", encoding="utf-8")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("I open the door", out)


if __name__ == "__main__":
    unittest.main()
