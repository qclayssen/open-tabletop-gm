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

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
WATCH = (REPO / "display" / "gm-watch.sh").read_text(encoding="utf-8")


class WatcherDoesNotLoseActions(unittest.TestCase):
    def test_the_exit_status_is_captured(self):
        """Without `RC=$?` immediately after the run, every failure reads as success."""
        self.assertRegex(WATCH, r"opencode run[^\n]*\n\s*RC=\$\?",
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
        piles up unseen while the display still shows "Sent"."""
        self.assertRegex(WATCH, r"timeout --signal=TERM --kill-after=\d+ \d+",
                         "gm-watch.sh runs the GM turn with no timeout")

    def test_success_is_logged_only_on_exit_zero(self):
        """The inverse error: reporting success when nothing was delivered."""
        zero_branch = re.search(r"if \[\[ \$RC -eq 0 \]\]; then\s*\n\s*log \"GM turn complete\"",
                                WATCH)
        self.assertIsNotNone(zero_branch,
                             "'GM turn complete' is not gated on RC=0")


if __name__ == "__main__":
    unittest.main()
