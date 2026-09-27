"""test_localdm_stall.py: tests for in-fiction stall line selection."""
from __future__ import annotations

import unittest
from localdm.stall import STALL_LINES, get_stall_line


class TestLocaldmStall(unittest.TestCase):
    def test_get_stall_line_contexts(self):
        for ctx in ("social", "combat", "exploration"):
            line = get_stall_line(ctx, seed=42)
            self.assertIsInstance(line, str)
            self.assertIn(line, STALL_LINES[ctx])

    def test_get_stall_line_fallback(self):
        line = get_stall_line("unknown_context", seed=42)
        self.assertIn(line, STALL_LINES["social"])

    def test_deterministic_seed(self):
        line1 = get_stall_line("combat", seed=10)
        line2 = get_stall_line("combat", seed=10)
        self.assertEqual(line1, line2)


if __name__ == "__main__":
    unittest.main()
