"""Action-economy pips, 'why' chips and the hover forecast card (tactics.js).

The helpers that build them are in the DOM-free block, so node runs them out of
the shipped file. The rule under test: the display renders the numbers the
engine returned and nothing it worked out itself, and nothing is carried by
colour alone.
"""
import json
import re
import shutil
import unittest

from tests.test_display_tactics_ui import CSS, JS, _run

NODE = shutil.which("node")

ROW = {"hit_percent": 88, "advantage": "advantage", "reasons": ["Frog is prone"], "cover": 2,
       "expected_damage": 4.8, "attack_bonus": 5, "target_ac": 11, "need": 8, "provokes": True}


def run(expr):
    return _run(f"return {{v: {expr}}};")["v"]


@unittest.skipUnless(NODE, "node is not installed")
class Pips(unittest.TestCase):
    def test_four_pips_from_the_engines_turn_fields(self):
        pips = run('economyPips({action_used: true, bonus_used: false, reaction: true, movement_left: 15})')
        self.assertEqual([(p["key"], p["spent"]) for p in pips],
                         [("action", True), ("bonus", False), ("reaction", False), ("move", False)])
        self.assertEqual(pips[3]["short"], "15 ft")

    def test_spent_means_reaction_false_and_no_movement_left(self):
        pips = run('economyPips({action_used: false, bonus_used: true, reaction: false, movement_left: 0})')
        self.assertEqual([p["spent"] for p in pips], [False, True, True, True])

    def test_no_turn_no_pips(self):
        self.assertEqual(run("economyPips({})"), [])
        self.assertEqual(run("economyPips(null)"), [])


@unittest.skipUnless(NODE, "node is not installed")
class Chips(unittest.TestCase):
    def texts(self, row):
        return [c["text"] for c in run(f"whyChips({json.dumps(row)})")]

    def test_an_attack_row_explains_its_number(self):
        t = self.texts(ROW)
        self.assertEqual(t[:4], ["+5 to hit", "AC 11", "half cover +2 AC", "needs 8+ on the d20"])
        self.assertIn("advantage: Frog is prone", t)

    def test_three_quarters_cover_and_negative_bonus(self):
        t = self.texts({"hit_percent": 30, "attack_bonus": -1, "cover": 5})
        self.assertIn("-1 to hit", t)
        self.assertIn("three-quarters cover +5 AC", t)

    def test_a_save_row_says_dc_bonus_and_cover(self):
        t = self.texts({"fail_percent": 60, "dc": 14, "save_bonus": 4, "cover": 2,
                        "advantage": "disadvantage", "reasons": ["Kairos is restrained"]})
        self.assertEqual(t[:2], ["DC 14", "+4 to the save"])
        self.assertIn("disadvantage: Kairos is restrained", t)

    def test_a_field_the_engine_did_not_send_makes_no_chip(self):
        self.assertEqual(self.texts({"hit_percent": 75}), [])

    def test_advantage_carries_a_glyph_as_well_as_a_colour_class(self):
        chips = run(f"whyChips({json.dumps(ROW)})")
        adv = [c for c in chips if c["kind"] == "adv"]
        self.assertTrue(adv and adv[0]["glyph"].strip())
        dis = run('whyChips({hit_percent: 40, advantage: "disadvantage", reasons: ["long range"]})')
        self.assertTrue(dis[-1]["glyph"].strip() and dis[-1]["glyph"] != adv[0]["glyph"])


@unittest.skipUnless(NODE, "node is not installed")
class Forecast(unittest.TestCase):
    def test_the_engines_percent_direction_and_damage_are_quoted(self):
        f = run(f"forecast({json.dumps(ROW)})")
        self.assertEqual((f["percent"], f["phrase"], f["expected"], f["provokes"]),
                         (88, "88% to hit", 4.8, True))

    def test_a_save_reads_as_a_chance_to_fail_and_uses_expected(self):
        f = run('forecast({fail_percent: 65, expected: 7.5})')
        self.assertEqual((f["phrase"], f["expected"], f["provokes"]), ("65% to fail the save", 7.5, False))

    def test_zero_percent_is_a_real_answer_and_no_percent_is_no_card(self):
        self.assertEqual(run("forecast({hit_percent: 0})")["phrase"], "0% to hit")
        self.assertIsNone(run("forecast({})"))

    def test_provokes_is_only_true_when_the_engine_says_true(self):
        self.assertFalse(run('forecast({hit_percent: 50, provokes: "yes"})')["provokes"])


class Wiring(unittest.TestCase):
    js = JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    def test_every_class_the_new_ui_sets_is_styled(self):
        for cls in ("tx-econ", "tx-pip", "tx-spent", "tx-forecast", "tx-fc-pct", "tx-whys", "tx-why",
                    "tx-why-adv", "tx-why-dis", "tx-why-warn", "tx-why-cover", "tx-forecast-slot"):
            self.assertIn("." + cls, self.css, cls)
        for cls in ("tx-econ", "tx-pip", "tx-spent", "tx-forecast", "tx-fc-pct", "tx-whys",
                    "tx-why", "tx-why-warn", "tx-forecast-slot"):
            self.assertIn(cls, self.js, cls)
        # The chip kinds become classes as tx-why-<kind>; each kind must be styled.
        for kind in ("adv", "dis", "cover", "num"):
            self.assertIn(f"'{kind}'", self.js, kind)
        self.assertNotIn("tx-why-num", self.css)   # plain chips need no colour

    def test_spent_is_not_colour_alone(self):
        self.assertIn("line-through", re.search(r"\.tx-pip\.tx-spent \{[^}]*\}", self.css).group(0))
        self.assertIn("\\u25CB", self.js)            # hollow dot for spent
        self.assertIn("used", self.js)               # and the word, for screen readers

    def test_the_card_is_not_a_live_region_and_the_info_one_is_kept(self):
        self.assertIn('id="tx-info" class="tx-info" aria-live="polite"', self.js)
        self.assertNotRegex(self.js, r'id="tx-forecast"[^>]*aria-live')

    def test_the_economy_words_line_is_still_there(self):
        self.assertIn("function economy()", self.js)

    def test_the_keyboard_cursor_hovers_attack_targets(self):
        body = self.js[self.js.index("function hoverSquare"):]
        self.assertIn("ui.mode === 'attack'", body[:900])

    def test_nothing_new_is_animated(self):
        block = self.css[self.css.index("/* Action-economy pips"):self.css.index(".tx-why-cover")]
        self.assertNotRegex(block, r"animation|transition")

    def test_no_number_is_parsed_out_of_prose(self):
        helpers = self.js[self.js.index("function economyPips"):self.js.index("/* end pure helpers */")]
        self.assertNotRegex(helpers, r"\.match\(|\.exec\(|parseInt|parseFloat|\.test\(")


if __name__ == "__main__":
    unittest.main()
