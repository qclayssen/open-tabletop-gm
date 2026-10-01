"""test_oracle.py: chaos factor, yes/no and event focus oracles.

Ported from the mature tree; the ChaosTests, YesNoTests and EventFocusTests
classes are unchanged. The scene-meaning tests are dropped because
scene_meaning() is deliberately not ported (see scripts/oracle.py).

Run from repo root:
    PYTHONPATH=. pytest tests/test_oracle.py
"""
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import oracle  # noqa: E402


class ChaosTests(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(oracle.clamp_chaos(0), 1)
        self.assertEqual(oracle.clamp_chaos(99), 9)
        self.assertEqual(oracle.clamp_chaos(5), 5)

    def test_adjust_direction(self):
        self.assertEqual(oracle.adjust_chaos(5, pc_proactive=True), 4)
        self.assertEqual(oracle.adjust_chaos(5, pc_proactive=False), 6)
        self.assertEqual(oracle.adjust_chaos(1, pc_proactive=True), 1)
        self.assertEqual(oracle.adjust_chaos(9, pc_proactive=False), 9)

    def test_read_default(self):
        self.assertEqual(oracle.read_chaos("## Session Flags\n- autorun: off\n"), 5)

    def test_read_explicit(self):
        self.assertEqual(oracle.read_chaos("## Session Flags\n- chaos_factor: 7\n"), 7)

    def test_write_inserts_under_section(self):
        text = "# Campaign\n\n## Session Flags\n- autorun: off\n"
        out = oracle.write_chaos(text, 8)
        self.assertIn("chaos_factor: 8", out)
        self.assertEqual(oracle.read_chaos(out), 8)

    def test_write_in_place_update(self):
        text = "## Session Flags\n- chaos_factor: 3\n"
        out = oracle.write_chaos(text, 6)
        self.assertEqual(oracle.read_chaos(out), 6)
        self.assertEqual(out.count("chaos_factor"), 1)

    def test_write_appends_section_when_missing(self):
        out = oracle.write_chaos("# Campaign\n", 4)
        self.assertIn("## Session Flags", out)
        self.assertEqual(oracle.read_chaos(out), 4)


class YesNoTests(unittest.TestCase):
    def test_seeded_reproducible(self):
        a = oracle.yes_no("50/50", 5, rng=random.Random(42))
        b = oracle.yes_no("50/50", 5, rng=random.Random(42))
        self.assertEqual(a, b)

    def test_verdict_shape(self):
        verdict, roll = oracle.yes_no("likely", 5, rng=random.Random(1))
        self.assertTrue(1 <= roll <= 100)
        base = verdict.split("-")[0]
        self.assertIn(base, {"yes", "no"})

    def test_likely_skews_yes(self):
        rng = random.Random(7)
        yes = sum(1 for _ in range(500)
                  if oracle.yes_no("sure-thing", 5, rng=rng)[0].startswith("yes"))
        self.assertGreater(yes, 400)

    def test_doubles_give_and(self):
        # Find a seed whose roll is a double; verdict must carry -and.
        for s in range(1000):
            verdict, roll = oracle.yes_no("50/50", 5, rng=random.Random(s))
            rs = str(roll).zfill(2)
            if rs[0] == rs[1]:
                self.assertTrue(verdict.endswith("-and"), f"{roll} -> {verdict}")
                return
        self.fail("no double roll found in 1000 seeds")


class EventFocusTests(unittest.TestCase):
    def test_in_table(self):
        labels = {lbl for _, lbl in oracle._FOCUS_TABLE}
        roll, label = oracle.random_event_focus(rng=random.Random(3))
        self.assertIn(label, labels)
        self.assertTrue(1 <= roll <= 100)

    def test_low_roll_is_remote(self):
        # Seed whose first randint(1,100) is <=5 maps to "remote event".
        for s in range(2000):
            roll, label = oracle.random_event_focus(rng=random.Random(s))
            if roll <= 5:
                self.assertEqual(label, "remote event")
                return
        self.fail("no roll <=5 found")


class NotPortedTests(unittest.TestCase):
    def test_scene_meaning_is_absent(self):
        self.assertFalse(hasattr(oracle, "scene_meaning"))
        self.assertFalse(hasattr(oracle, "_ACTIONS"))


class DiceRoutingTests(unittest.TestCase):
    def test_d100_goes_through_dice_module(self):
        import dice
        calls = []
        real = dice.run
        dice.run = lambda notation, silent=False, rng=None: calls.append(notation) or 42
        try:
            self.assertEqual(oracle.random_event_focus(), (42, "introduce thread"))
        finally:
            dice.run = real
        self.assertEqual(calls, ["d100"])


class CliTests(unittest.TestCase):
    def _run(self, root, *args):
        env = dict(os.environ, GM_CAMPAIGN_ROOT=root, PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(SCRIPTS / "oracle.py"), *args],
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def test_chaos_reads_and_writes_session_flags(self):
        with tempfile.TemporaryDirectory() as root:
            camp = pathlib.Path(root) / "campaigns" / "demo"
            camp.mkdir(parents=True)
            (camp / "state.md").write_text("# C\n\n## Session Flags\n", encoding="utf-8")
            r = self._run(root, "-c", "demo", "chaos")
            self.assertIn("chaos factor: 5", r.stdout, r.stderr)
            r = self._run(root, "-c", "demo", "chaos", "set", "--value", "8")
            self.assertEqual(r.returncode, 0, r.stderr)
            text = (camp / "state.md").read_text(encoding="utf-8")
            self.assertEqual(oracle.read_chaos(text), 8)
            r = self._run(root, "chaos", "adjust", "--pc-won", "-c", "demo")
            self.assertIn("8 -> 7", r.stdout, r.stderr)

    def test_event_is_seedable(self):
        with tempfile.TemporaryDirectory() as root:
            a = self._run(root, "event", "--seed", "12").stdout
            b = self._run(root, "event", "--seed", "12").stdout
            self.assertTrue(a.startswith("event focus:"), a)
            self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
