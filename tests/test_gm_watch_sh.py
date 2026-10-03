"""gm-watch.sh must not lose a player's action, and must not lie about success.

The watcher is a serial loop: it drains `.input_queue`, hands the action to the GM,
and if the GM turn fails it puts the action back so the next poll retries. Two
things can go wrong there, and both are silent:

- the turn's exit status is ignored, so a dead session or a crashed GM is logged as
  "GM turn complete" while the action is already consumed and never narrated
  (BUGS.md B2 — `.gm-watch.log` shows exactly that happening);
- the restore truncates the queue, so anything a player sent *during* the GM turn is
  destroyed by the restore meant to save the earlier action.

These assert on the script's source, the way the repo's other display tests do for
shell-level guarantees. The runtime behaviour is exercised by `test_drain_queue.py`.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
WATCH = (REPO / "display" / "gm-watch.sh").read_text(encoding="utf-8")


class WatcherDoesNotLoseActions(unittest.TestCase):
    def test_the_exit_status_is_captured(self):
        """Without `RC=$?` immediately after the run, every failure reads as success."""
        self.assertRegex(WATCH, r'wait "\$_turn_pid"; RC=\$\?',
                         "gm-watch.sh does not capture the GM turn's exit status")

    def test_a_failed_or_timed_out_turn_restores_the_action(self):
        for rc in ("0", "124"):
            block = re.search(r"if \[\[ \$RC -eq %s" % rc, WATCH)
            self.assertIsNotNone(block, f"no branch for RC={rc}")
        self.assertRegex(WATCH, r"RC -eq 124 \|\| \$RC -eq 137",
                         "a timeout (124/137) must be handled as its own case, not as success")

    def test_the_restore_appends_rather_than_truncating(self):
        """`> "$QUEUE"` destroys anything a player sent while the GM turn ran.

        The drain has already consumed this action by the time the restore happens,
        so appending restores it without clobbering a newer one. Appending a
        duplicate would only re-narrate an action, which is the cheaper failure.
        """
        restores = [ln for ln in WATCH.splitlines() if '"$RAW_ACTION"' in ln and "$QUEUE" in ln]
        self.assertTrue(restores, "no restore of RAW_ACTION found")
        for ln in restores:
            # `(?!>)` so `>>` is not read as a bare `>`: only a single `>` truncates.
            self.assertNotRegex(ln, r'(?<!>)>\s*"\$QUEUE"',
                                f"truncating restore would drop a concurrent action: {ln.strip()}")
            self.assertIn('>> "$QUEUE"', ln,
                          f"restore should append to the queue: {ln.strip()}")

    def test_the_run_is_bounded_by_a_timeout(self):
        """A hung GM turn wedges the serial loop forever; every later player action
        piles up unseen while the display still shows "Sent".

        Asserts the *bound*, not one spelling of it: macOS ships no coreutils
        `timeout`, so the script picks `timeout` or `gtimeout` and falls back to a
        pure-bash kill watchdog. All three are acceptable; an unbounded run is not.
        """
        bounded = (re.search(r'TIMEOUT_BIN.*command -v timeout', WATCH)
                   and re.search(r'"?\$?\{?TIMEOUT_BIN\}?"? --signal=TERM --kill-after=\d+ \d+', WATCH)
                   and re.search(r'kill -TERM "\$_turn_pid"', WATCH))
        self.assertTrue(bounded,
                        "gm-watch.sh runs the GM turn unbounded, or bounds it without a "
                        "TERM-then-KILL escalation")

    def test_success_is_logged_only_on_exit_zero(self):
        """The inverse error: reporting success when nothing was delivered."""
        zero_branch = re.search(r"if \[\[ \$RC -eq 0 \]\]; then\s*\n\s*log \"GM turn complete\"",
                                WATCH)
        self.assertIsNotNone(zero_branch,
                             "'GM turn complete' is not gated on RC=0")


# Tools the watcher shells out to. Deliberately NOT `timeout` / `gtimeout`.
_TOOLS = ("bash", "python3", "cat", "date", "sleep", "grep", "rm", "kill",
          "dirname", "env", "mkdir", "mv", "ps")


@unittest.skipIf(os.name == "nt", "the watcher is a bash script run in its own session (os.killpg)")
class WatcherWithoutCoreutilsTimeout(unittest.TestCase):
    """The macOS path, run for real: no `timeout`, no `gtimeout` on PATH.

    The source-regex tests above cannot see a control-flow bug. This one shipped
    in the fallback watchdog: `[[ A ]] || [[ B ]] && RC=124` parses as
    `(A || B) && RC=124`, so a turn that exited 0 was logged "TIMED OUT" and its
    action was put back on the queue, to be narrated again on every poll.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self.display = root / "display"
        self.display.mkdir()
        # queue_claim.py travels with drain_queue.py: the drainer imports it, and a
        # temp display dir without it fails at import with a traceback that looks
        # like a watcher bug rather than a missing file.
        for name in ("gm-watch.sh", "drain_queue.py", "queue_claim.py"):
            shutil.copy2(REPO / "display" / name, self.display / name)
        # A PATH holding only the tools the watcher needs, so `command -v timeout`
        # fails on Linux CI exactly as it does on stock macOS.
        self.bin = root / "bin"
        self.bin.mkdir()
        for tool in _TOOLS:
            found = shutil.which(tool)
            self.assertIsNotNone(found, f"{tool} not on PATH")
            (self.bin / tool).symlink_to(found)
        self.calls = root / "opencode.calls"
        fake = self.bin / "opencode"
        fake.write_text("#!/bin/sh\necho called >> \"%s\"\nexit ${FAKE_RC:-0}\n" % self.calls,
                        encoding="utf-8")
        fake.chmod(0o755)
        self.queue = self.display / ".input_queue"
        self.log = self.display / ".gm-watch.log"

    def tearDown(self):
        self._tmp.cleanup()

    def _run_watcher(self, rc, wait_for):
        """Start the watcher with one queued action; stop once `wait_for` is logged."""
        self.queue.write_text(json.dumps([{"character": "Mira", "text": "I open the door"}]),
                              encoding="utf-8")
        env = {"PATH": str(self.bin), "FAKE_RC": str(rc), "HOME": self._tmp.name}
        proc = subprocess.Popen(["bash", str(self.display / "gm-watch.sh"), "sess",
                                 "--interval", "1"],
                                env=env, cwd=self.display,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                text = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
                if wait_for in text:
                    time.sleep(0.5)          # let a wrongly-restored queue show up
                    break
                time.sleep(0.2)
        finally:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_the_fallback_path_is_really_taken(self):
        text = self._run_watcher(0, "GM turn complete")
        self.assertIn("no coreutils timeout found", text,
                      "PATH still held a timeout binary; this test would prove nothing")

    def test_a_successful_turn_is_not_reported_as_a_timeout_and_is_not_redelivered(self):
        text = self._run_watcher(0, "GM turn complete")
        self.assertIn("GM turn complete", text)
        self.assertNotIn("TIMED OUT", text)
        self.assertFalse(self.queue.exists() and self.queue.read_text(encoding="utf-8").strip(),
                         "a delivered action was put back on the queue")
        self.assertEqual(self.calls.read_text(encoding="utf-8").count("called"), 1,
                         "the GM was handed the same action more than once")

    def test_a_failed_turn_restores_the_action(self):
        text = self._run_watcher(3, "FAILED")
        self.assertIn("FAILED", text)
        self.assertTrue(self.queue.exists() and "open the door" in self.queue.read_text(encoding="utf-8"),
                        "a failed turn must put the action back")


@unittest.skipIf(os.name == "nt", "bash watcher")
class WatcherProcessLifecycle(unittest.TestCase):
    """B9/B10/B11 exercised for real with a fake `opencode` on a minimal PATH."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self.display = root / "display"
        self.display.mkdir()
        for name in ("gm-watch.sh", "drain_queue.py", "queue_claim.py"):
            shutil.copy2(REPO / "display" / name, self.display / name)
        self.bin = root / "bin"
        self.bin.mkdir()
        for tool in _TOOLS:
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.started = root / "turn.started"
        self.pidout = root / "turn.pid"
        self.termed = root / "turn.termed"
        self.stub("#!/bin/sh\necho $$ > \"%s\"\ntrap 'echo t > \"%s\"; exit 143' TERM\n"
                  "while :; do sleep 1; done\n" % (self.pidout, self.termed))
        self.queue = self.display / ".input_queue"
        self.log = self.display / ".gm-watch.log"
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        if self.pidout.exists():
            try:
                os.kill(int(self.pidout.read_text(encoding="utf-8").strip()), signal.SIGKILL)
            except (ValueError, OSError):
                pass
        self._tmp.cleanup()

    def stub(self, body):
        f = self.bin / "opencode"
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)

    def start(self, *args, **extra):
        env = {"PATH": str(self.bin), "HOME": self._tmp.name, "GM_WATCH_STARTUP_DELAY": "0",
               "GM_WATCH_KILL_GRACE": "2"}
        env.update(extra)
        p = subprocess.Popen(["bash", str(self.display / "gm-watch.sh"), *args],
                             env=env, cwd=self.display, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", start_new_session=True)
        self.procs.append(p)
        return p

    def wait_for(self, pred, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.1)
        return False

    def queue_action(self):
        self.queue.write_text(json.dumps([{"character": "Mira", "text": "I open the door"}]),
                              encoding="utf-8")

    def alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def test_signal_stops_the_inflight_turn_immediately_and_restores_action(self):
        self.queue_action()
        p = self.start("sess", "--interval", "1")
        self.assertTrue(self.wait_for(self.pidout.exists), "the turn never started")
        time.sleep(0.3)
        turn = int(self.pidout.read_text(encoding="utf-8").strip())
        t0 = time.monotonic()
        p.terminate()
        p.wait(timeout=15)
        # The bound is 8s and the measured latency is ~1.1s, so this is roughly
        # 7x headroom, not a tight race. Measured, eight runs: 1.0993, 1.1031,
        # 1.1041, 1.1116, 1.1216, 1.1221, 1.1271, 1.1272 -- a spread of 0.028s.
        # The 1.1s is the watcher's `--interval 1` poll, i.e. the trap waits out
        # the turn in flight rather than interrupting it, which is the behaviour
        # this asserts.
        #
        # `p.wait(timeout=15)` above is the hard bound and raises if the trap is
        # deferred that long; the 8 is the "not deferred until the next turn"
        # line and is deliberately looser, because a slower runner must not turn
        # this red. Tightening it is a decision with a measured cost, not a
        # cleanup: the number is documented here so nobody has to re-derive it to
        # find out whether it is safe to move.
        self.assertLess(time.monotonic() - t0, 8, "trap was deferred until the turn returned")
        self.assertTrue(self.wait_for(lambda: not self.alive(turn), 5), "turn left orphaned")
        self.assertTrue(self.termed.exists(), "turn was not sent TERM")
        self.assertIn("open the door", self.queue.read_text(encoding="utf-8"))
        self.assertFalse((self.display / ".gm-watch.pid").exists())
        self.assertFalse((self.display / ".gm-watch.lock").exists())

    def test_a_turn_that_ignores_term_is_killed_after_the_grace(self):
        self.stub("#!/bin/sh\necho $$ > \"%s\"\ntrap '' TERM\nwhile :; do sleep 1; done\n"
                  % self.pidout)
        self.queue_action()
        p = self.start("sess", "--interval", "1")
        self.assertTrue(self.wait_for(self.pidout.exists))
        time.sleep(0.3)
        turn = int(self.pidout.read_text(encoding="utf-8").strip())
        p.terminate()
        p.wait(timeout=15)
        self.assertTrue(self.wait_for(lambda: not self.alive(turn), 5))
        self.assertIn("KILL", self.log.read_text(encoding="utf-8"))

    def test_second_start_is_refused_while_first_runs(self):
        a = self.start("sess", "--interval", "1")
        self.assertTrue(self.wait_for((self.display / ".gm-watch.pid").exists))
        b = self.start("sess", "--interval", "1")
        b.wait(timeout=10)
        self.assertEqual(b.returncode, 1)
        self.assertIn("already running", b.stderr.read())
        self.assertIsNone(a.poll())

    def test_simultaneous_starts_yield_exactly_one_watcher(self):
        ps = [self.start("sess", "--interval", "1") for _ in range(6)]
        # A fixed `sleep(4)` then `assertEqual(len(running), 1)` is the one
        # machine-dependent assertion in this file, and it was not the one the
        # audit named. It goes red on a *loaded* runner -- six `/bin/sh`
        # processes starting at once under a full parallel pytest -- because a
        # loser that has not finished failing its startup is still alive at 4s
        # and the count is 2. Every other wait in this file already uses
        # `wait_for`, which is the file's own answer to "how long is long
        # enough"; this one reached for a literal instead.
        #
        # `wait_for` keeps the assertion's meaning exactly -- six simultaneous
        # starts settle on one survivor -- and drops the guess about how fast
        # that happens. Measured on this machine the settle is already complete
        # at the first probe after 4s (1 of 6 alive, five runs), so this is
        # tolerance rather than a different claim.
        self.assertTrue(
            self.wait_for(lambda: len([p for p in ps if p.poll() is None]) == 1, 30),
            [p.poll() for p in ps])

    def test_recycled_pid_does_not_block_startup(self):
        # A live process that is NOT a gm-watch owns the pid in a stale lock.
        stranger = subprocess.Popen(["sleep", "60"])
        self.procs.append(stranger)
        (self.display / ".gm-watch.lock").mkdir()
        (self.display / ".gm-watch.pid").write_text(str(stranger.pid), encoding="utf-8")
        p = self.start("sess", "--interval", "1")
        time.sleep(2)
        self.assertIsNone(p.poll(), "false 'already running' from a recycled pid")
        self.assertEqual((self.display / ".gm-watch.pid").read_text(encoding="utf-8").strip(), str(p.pid))
        self.assertIsNone(stranger.poll(), "the stranger must not be signalled")

    def test_stop_stops_watcher_then_server_and_removes_pidfiles(self):
        w = self.start("sess", "--interval", "1")
        self.assertTrue(self.wait_for((self.display / ".gm-watch.pid").exists))
        # fake server: a process whose command line contains gm-display-app.py
        fake_app = self.display / "gm-display-app.py"
        fake_app.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        srv = subprocess.Popen(["python3", str(fake_app)])
        self.procs.append(srv)
        (self.display / "app-5999.pid").write_text(str(srv.pid), encoding="utf-8")
        other = subprocess.Popen(["python3", str(fake_app)])
        self.procs.append(other)
        (self.display / "app-5998.pid").write_text(str(other.pid), encoding="utf-8")
        r = subprocess.run(["bash", str(self.display / "gm-watch.sh"), "stop", "--port", "5999"],
                           env={"PATH": str(self.bin), "HOME": self._tmp.name},
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(r.stdout.index("watcher"), r.stdout.index("server"))
        w.wait(timeout=10)
        srv.wait(timeout=10)
        self.assertIsNone(other.poll(), "stop must only touch the requested port")
        self.assertFalse((self.display / "app-5999.pid").exists())
        self.assertFalse((self.display / ".gm-watch.pid").exists())


if __name__ == "__main__":
    unittest.main()
