"""test_localdm_stall.py: tests for in-fiction stall line selection."""
from __future__ import annotations

import unittest
from localdm.stall import NEUTRAL_CONTEXT, NEUTRAL_LINES, STALL_LINES, get_stall_line

# Furniture from a campaign the call sites know nothing about. The 09-29 audit
# caught "An awkward silence falls over the tavern table" printed during a
# library scene: the line was chosen before the DM had a scene, from a pool that
# assumed a tavern. Any of these words in a line is the same regression back.
BANNED = (
    "tavern", "innkeeper", "inn ", "goblin", "portcullis", "courtyard", "battlefield",
    "commander", "merchant", "scholar", "bloodstained", "torchlight", "rain", "dungeon",
    "cave", "cavern", "corridor", "underground", "goblet", "parchment", "coin", "coins",
    "torch", "library", "ale", "barrel", "stairs",
)


def assert_clean(case, line, label):
    low = line.lower()
    for word in BANNED:
        case.assertNotIn(word, low, f"{label} line asserts a scene it was not told: {line!r}")
    # and it stays in fiction: no meta "please wait" either (Applied Standard 15)
    for word in ("wait", "process", "loading", "api", "model"):
        case.assertNotIn(word, low, f"{label} line breaks the fourth wall: {line!r}")


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

    def test_social_lines_carry_no_tavern_or_goblin_furniture(self):
        """The regression itself: the pool a scene-blind call site can reach must
        not name a place or an NPC the scene never established."""
        self.assertTrue(STALL_LINES["social"], "the social pool must not be empty")
        for line in STALL_LINES["social"]:
            assert_clean(self, line, "social")

    def test_every_context_draws_scene_clean_lines(self):
        """No draw from any pool, over enough seeds to cover it, may assert a
        specific location or a specific named NPC."""
        for ctx in list(STALL_LINES) + ["unknown_context", "combat nonsense", ""]:
            for seed in range(200):
                assert_clean(self, get_stall_line(ctx, seed=seed), ctx or "empty")

    def test_unknown_context_uses_the_neutral_pool(self):
        """The real defect at the call sites: combat-or-not is all the bridge
        knows, so a scene the caller cannot describe must not be read as a
        tavern, an inn or a council chamber."""
        self.assertIs(STALL_LINES["social"], NEUTRAL_LINES)
        self.assertIs(STALL_LINES[NEUTRAL_CONTEXT], NEUTRAL_LINES)
        for seed in range(200):
            self.assertIn(get_stall_line(NEUTRAL_CONTEXT, seed=seed), NEUTRAL_LINES)
            self.assertIn(get_stall_line("unknown_context", seed=seed), NEUTRAL_LINES)
            self.assertIn(get_stall_line(seed=seed), NEUTRAL_LINES)   # default arg


if __name__ == "__main__":
    unittest.main()
