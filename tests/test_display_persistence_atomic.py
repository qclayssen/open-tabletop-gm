"""Persisted display state must survive a write that fails part-way through.

`before fix:` both `_persist_log` and `_persist_stats` opened their target with
`open(path, "w")` and called `json.dump`. The "w" truncates before the first byte
is written, so anything that raised during serialisation left an empty or
half-written file where the previous session's state used to be. The display
carries on, so the next `/gm load` reads the wreckage and the table loses the
narration log or, for stats, every party's HP, XP and turn order.

The swallow is the second half. Both wrapped the write in `except Exception:
pass`, so a full disk or a read-only mount produced no output anywhere. A GM
watching a display that has stopped recording has no way to learn that from the
display.

The fix routes both through `scripts/safeio.py`, which serialises first, then
writes a sibling tempfile, fsyncs it, and renames over the target. The ordering
is what makes the difference: a failure while serialising now happens before the
target is touched at all, and a failure while writing hits the tempfile. Either
way the previous bytes survive, and the error is reported on stderr naming the
path and the underlying OSError.

Both `json.dump` and `json.dumps` are stubbed to raise. That is the shape that
separates the two implementations rather than the shape that happens to fail
today: pre-fix the target is truncated by `open(..., "w")` and *then* the dump
raises, so the bytes are already gone; post-fix serialisation precedes any write,
so nothing was opened. Patching only `json.dump` would leave the fixed path
calling `json.dumps` and quietly writing a success the test then read as a
failure, which is how a test starts passing for the wrong reason.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_atomic_persist", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class AtomicPersistenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()

    def setUp(self):
        m = self.mod
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.log = self.tmp / "text_log.json"
        self.stats = self.tmp / "stats.json"
        m._get_log_file = lambda: str(self.log)
        m.STATS_FILE = str(self.stats)
        m._get_tail_file = lambda: None
        m._persist_tail = lambda: None

    # ── fixtures ────────────────────────────────────────────────────────────

    SEEDED_LOG = [{"text": "the door stays shut", "author": "seat-dm"}]
    SEEDED_STATS = {"players": [{"name": "Kairos", "hp": 11}]}

    def _seed(self):
        """Put known-good bytes on disk through the real persist path."""
        m = self.mod
        m._text_log.clear()
        m._text_log.extend(self.SEEDED_LOG)
        m._persist_log()
        m._text_log.clear()
        m._current_stats.clear()
        m._current_stats.update(self.SEEDED_STATS)
        m._persist_stats()
        m._current_stats.clear()
        self.assertTrue(self.log.read_bytes(), "the log seed must not be empty")
        self.assertTrue(self.stats.read_bytes(), "the stats seed must not be empty")

    def _fail_serialisation(self, target):
        """Run `target()` with JSON serialisation raising an OSError.

        Returns whatever the target wrote to stderr. Stubbing the serialiser
        rather than the file object matters: a patch that made `open` raise
        would fail before any truncation and would pass against the pre-fix
        code, pinning nothing.
        """
        def boom(*args, **kwargs):
            raise OSError(28, "No space left on device")

        with mock.patch.object(json, "dump", boom), mock.patch.object(json, "dumps", boom):
            with redirect_stderr(io.StringIO()) as caught:
                target()
        return caught.getvalue()

    # ── the previous bytes survive ──────────────────────────────────────────

    def test_a_failed_log_write_leaves_the_previous_bytes(self):
        self._seed()
        self.mod._text_log.append({"text": "the pick snaps in the ward-lock"})
        self._fail_serialisation(self.mod._persist_log)
        self.assertEqual(
            json.loads(self.log.read_text(encoding="utf-8")), self.SEEDED_LOG,
            "a failed log write must leave the previous session's log readable")

    def test_a_failed_stats_write_leaves_the_previous_bytes(self):
        self._seed()
        self.mod._current_stats.update({"players": [{"name": "Mira", "hp": 3}]})
        self._fail_serialisation(self.mod._persist_stats)
        self.assertEqual(
            json.loads(self.stats.read_text(encoding="utf-8")), self.SEEDED_STATS,
            "a failed stats write must not cost the table its HP and turn order")

    # ── and the failure is visible ──────────────────────────────────────────

    def test_a_log_write_failure_is_reported_on_stderr(self):
        self._seed()
        self.mod._text_log.append({"text": "a later line"})
        err = self._fail_serialisation(self.mod._persist_log)
        self.assertIn(str(self.log), err,
                      "the diagnostic must name the file it could not write")
        self.assertIn("No space left on device", err,
                      "the diagnostic must carry the underlying OSError")

    def test_a_stats_write_failure_is_reported_on_stderr(self):
        self._seed()
        self.mod._current_stats.update({"players": []})
        err = self._fail_serialisation(self.mod._persist_stats)
        self.assertIn(str(self.stats), err)
        self.assertIn("No space left on device", err)

    # ── the happy path is unaffected ────────────────────────────────────────

    def test_a_successful_write_still_lands_and_stays_valid_json(self):
        m = self.mod
        m._text_log.clear()
        m._text_log.append({"text": "first", "author": "seat-dm"})
        m._text_log.append({"text": "second", "author": "seat-dm"})
        m._persist_log()
        self.assertEqual([d["text"] for d in
                          json.loads(self.log.read_text(encoding="utf-8"))],
                         ["first", "second"])
        m._text_log.clear()

        m._current_stats.clear()
        m._current_stats.update({"players": [{"name": "Kairos", "hp": 11}],
                                 "turn_order": None})
        m._persist_stats()
        self.assertEqual(
            json.loads(self.stats.read_text(encoding="utf-8"))["players"],
            [{"name": "Kairos", "hp": 11}])

    def test_the_on_disk_form_is_unchanged_by_the_durability_fix(self):
        """The point of this change is durability. The bytes must not move.

        `safeio.atomic_write_json` defaults to `indent=2`, which is right for a
        state file a human reads and wrong for these: `_persist_log` fires after
        every chunk, and pretty-printing a full 60-entry log costs ~15% more
        bytes on the display's hottest write. A test that only checked the
        parsed content would pass against that regression, since JSON does not
        care how it is spaced.
        """
        self._seed()
        self.assertNotIn(b"\n  ", self.log.read_bytes(),
                         "the text log must stay compact")
        self.assertNotIn(b"\n  ", self.stats.read_bytes(),
                         "stats must stay compact")
        self.assertEqual(self.log.read_bytes(),
                         json.dumps(self.SEEDED_LOG).encode("utf-8"))

    def test_no_tempfile_is_left_behind_after_a_successful_write(self):
        self._seed()
        leftovers = [p.name for p in self.tmp.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [],
                         "safeio's tempfiles are renamed into place; none may remain")


if __name__ == "__main__":
    unittest.main()