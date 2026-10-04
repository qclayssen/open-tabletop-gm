"""The pad must show what a spend costs, before and after (RS2.2).

Server-side tests cannot reach this. What a player sees on their phone when they
press a marked offer is the difference between a feature that reads as a real
resource and one that reads as a button.

Three states, and all three are measured here in a real browser through
`window._onDiceRequest` — the real broadcast entry point:

- the button shows the count the SERVER sent, and greys out at 0;
- tapping marks it and the Roll button names it, before the dice are thrown;
- a refusal shows the server's sentence and leaves the mark in place, because a
  refused spend spent nothing.

A count of `null` is deliberately not 0: it means the GM never ran
`--resource-set`, which is an unconfigured display rather than an exhausted
character, and greying out every offer there would read as a broken phone.
"""
import json

from tests._browser import BrowserTestCase

RECALL = {"key": "kenku_recall", "label": "Kenku Recall",
          "effect": {"advantage": True}}


def _req(**over):
    body = {"request_id": "req0001", "characters": ["Kairos"], "spec": "1d20",
            "modifier": 0, "advantage": "normal", "label": "Stealth"}
    body.update(over)
    return body


class OfferCountsOnThePad(BrowserTestCase):
    module_name = "gm_display_app_pad_counts"

    def open_pad(self):
        # harness_path is only consulted for a static server; a flask one always
        # loads "/", and the pad is phone-only markup.
        return self.open_page(path="/?view=input&character=Kairos")

    def prescribe(self, page, offers):
        page.evaluate("req => window._onDiceRequest(req)", _req(offers=offers))
        page.wait_for_timeout(150)

    def offers(self, page):
        return page.evaluate("""() => Array.from(
            document.querySelectorAll('.dp-offer')).map(b => ({
              key: b.dataset.offer, text: b.textContent,
              disabled: b.hasAttribute('disabled'),
              active: b.classList.contains('active'),
            }))""")

    def test_the_button_shows_the_count_the_server_sent(self):
        page = self.open_pad()
        self.prescribe(page, [{**RECALL, "uses": 2}])
        self.assertEqual(self.offers(page),
                         [{"key": "kenku_recall", "text": "Kenku Recall · 2",
                           "disabled": False, "active": False}])

    def test_a_spent_offer_renders_greyed_out_rather_than_hidden(self):
        """The difference from a milestone, on the player's own screen. A player
        who just failed a check they had a resource for needs to see it existed."""
        page = self.open_pad()
        self.prescribe(page, [{**RECALL, "uses": 0}])
        rows = self.offers(page)
        self.assertEqual(len(rows), 1, "a spent offer must still render")
        self.assertTrue(rows[0]["disabled"])
        self.assertEqual(rows[0]["text"], "Kenku Recall · 0")

    def test_an_uncounted_offer_stays_live(self):
        """`uses: null` is the GM never having run --resource-set. Disabling it
        would grey out every offer on a display that is merely unconfigured."""
        page = self.open_pad()
        self.prescribe(page, [{**RECALL, "uses": None}])
        rows = self.offers(page)
        self.assertFalse(rows[0]["disabled"])
        self.assertEqual(rows[0]["text"], "Kenku Recall")

    def test_two_offers_show_their_own_counts(self):
        page = self.open_pad()
        self.prescribe(page, [{**RECALL, "uses": 2},
                              {"key": "bless", "label": "Bless",
                               "effect": {"modifier": 2}, "uses": 1}])
        self.assertEqual([o["text"] for o in self.offers(page)],
                         ["Kenku Recall · 2", "Bless · 1"])


class MarkingAndCommitting(BrowserTestCase):
    module_name = "gm_display_app_pad_commit"

    def open_pad(self):
        return self.open_page(path="/?view=input&character=Kairos")

    def test_the_mark_is_visible_before_the_roll_is_committed(self):
        page = self.open_pad()
        page.evaluate("req => window._onDiceRequest(req)",
                      _req(offers=[{**RECALL, "uses": 2}]))
        page.wait_for_timeout(150)
        self.assertEqual(page.text_content("#dp-roll"), "Roll")
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(80)
        self.assertEqual(page.text_content("#dp-roll"), "Roll (Kenku Recall)")

    def test_a_refusal_shows_the_servers_sentence_and_keeps_the_mark(self):
        page = self.open_pad()
        page.route("**/player-input/dice", lambda route: route.fulfill(
            status=400, content_type="application/json",
            body=json.dumps({"error": "Kenku Recall is spent — 2/2 used, "
                                      "nothing was spent"})))
        page.evaluate("req => window._onDiceRequest(req)",
                      _req(offers=[{**RECALL, "uses": 2}]))
        page.wait_for_timeout(150)
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(80)
        page.click("#dp-roll")
        page.wait_for_timeout(2500)
        line = page.text_content("#dp-result-line")
        self.assertIn("spent", line)
        self.assertIn("2/2", line,
                      "the pad shows counts, so the refusal should too")
        self.assertNotEqual(line.strip(), "roll failed")
        # Nothing was spent, so the mark stays: the player can drop it and roll
        # straight rather than re-picking the feature that was refused.
        self.assertEqual(page.text_content("#dp-roll"), "Roll (Kenku Recall)")

    def test_a_successful_roll_clears_the_mark(self):
        page = self.open_pad()
        page.route("**/player-input/dice", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"spec": "1d20", "modifier": 0,
                             "advantage": "advantage",
                             "kept": [17], "both": [17, 4], "subtotal": 17,
                             "total": 17, "resource": {"used": 0, "max": 2},
                             "text": "Kairos rolls 1d20: [17, 4] → keep 17"})))
        page.evaluate("req => window._onDiceRequest(req)",
                      _req(offers=[{**RECALL, "uses": 2}]))
        page.wait_for_timeout(150)
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(80)
        page.click("#dp-roll")
        page.wait_for_timeout(2500)
        # The use is gone; a mark still on the Roll button would invite a second
        # spend of the same resource on the next roll.
        self.assertEqual(page.text_content("#dp-roll"), "Roll")
        self.assertFalse(page.evaluate(
            "() => document.querySelector('.dp-offer[data-offer=\"kenku_recall\"]')"
            ".classList.contains('active')"))

    def test_a_resolved_prescribed_roll_locks_the_pad(self):
        page = self.open_pad()
        page.route("**/player-input/dice", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"spec": "1d20", "modifier": 0,
                             "advantage": "advantage",
                             "kept": [17], "both": [17, 4], "subtotal": 17,
                             "total": 17, "spend_label": "Kenku Recall",
                             "text": "Kairos rolls 1d20: [17, 4] → keep 17 "
                                     "(advantage) = 17 — Stealth (Kenku Recall)"})))
        page.evaluate("req => window._onDiceRequest(req)",
                      _req(offers=[{**RECALL, "uses": 2}]))
        page.wait_for_timeout(150)
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(80)
        page.click("#dp-roll")
        page.wait_for_timeout(2500)
        self.assertTrue(page.evaluate(
            "() => document.getElementById('dp-roll').disabled"),
            "a resolved prescribed roll locks the pad afterwards")


if __name__ == "__main__":
    import unittest
    unittest.main()