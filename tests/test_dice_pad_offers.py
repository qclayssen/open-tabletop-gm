"""The pad's lock must mean three different things when offers exist (RS1.1).

Server-side tests cannot reach this. `_setLocked` lives in the browser, and its
whole job is which buttons are disabled. Asserting on the text of display.js
would be decoration: it would pass with the mask deleted.

The regression that matters most is the one with **no** offers. A prescribed
roll locks adv/dis and the modifier today, and that is correct — the server
rolls authoritatively, so letting the phone choose would be a claim the display
cannot honour. That path must not change, and it is the common case.

Driven through `window._onDiceRequest`, which is the real entry point: it is
exactly what the `/dice-request` broadcast calls, so a test that went through
any other door would be testing a door nobody uses.
"""
import json

from tests._browser import BrowserTestCase


class PadLocks(BrowserTestCase):
    module_name = "gm_display_app_pad_offers"
    # `harness_path` is only consulted for a static server; a flask one is always
    # loaded at "/". The pad is phone-only markup, so the view has to be asked
    # for explicitly or there is nothing to measure.
    PAD = "/?view=input&character=Kairos"

    def open_pad(self, **kw):
        return self.open_page(path=self.PAD, **kw)

    def pad_state(self, page):
        """Which pad controls are disabled, as the page actually has them."""
        return page.evaluate("""() => {
          const dis = sel => Array.from(document.querySelectorAll(sel))
            .filter(b => b.hasAttribute('disabled'))
            .map(b => b.dataset.spec || b.dataset.adv || b.dataset.delta);
          const row = document.getElementById('dp-offer-row');
          return {
            die:      dis('.dp-die'),
            adv:      dis('.dp-adv'),
            mod:      dis('.dp-mod'),
            offers:   row ? Array.from(row.querySelectorAll('.dp-offer'))
                            .map(b => b.dataset.offer) : [],
            offerRowHidden: row ? row.hidden : null,
            rollLabel: (document.getElementById('dp-roll') || {}).textContent,
          };
        }""")

    def prescribe(self, page, **req):
        req.setdefault("request_id", "req0001")
        req.setdefault("characters", ["Kairos"])
        req.setdefault("spec", "1d20")
        req.setdefault("modifier", 0)
        req.setdefault("advantage", "normal")
        page.evaluate("req => window._onDiceRequest(req)", req)
        page.wait_for_timeout(120)

    # ── the common case: no offers, byte-identical to before ──────────────

    def test_a_request_with_no_offers_locks_everything_as_it_did(self):
        """die: only the prescribed one. adv: only the prescribed one.
        mod: all of them. This is the path that was already correct."""
        page = self.open_pad()
        self.prescribe(page, spec="1d20", advantage="normal")
        s = self.pad_state(page)
        self.assertEqual(s["die"], ["1d4", "1d6", "1d8", "1d10", "1d12",
                                    "1d100", "2d6"])
        self.assertEqual(s["adv"], ["advantage", "disadvantage"])
        self.assertEqual(s["mod"], ["-1", "1"])
        self.assertEqual(s["offers"], [])
        self.assertTrue(s["offerRowHidden"])

    def test_a_prescribed_advantage_locks_the_other_two_adv_buttons(self):
        page = self.open_pad()
        self.prescribe(page, spec="1d20", advantage="advantage")
        s = self.pad_state(page)
        self.assertEqual(s["adv"], ["normal", "disadvantage"])

    # ── the new case: offers exist ────────────────────────────────────────

    def test_an_offer_renders_one_button_per_offer(self):
        page = self.open_pad()
        self.prescribe(page, offers=[
            {"key": "kenku_recall", "label": "Kenku Recall",
             "effect": {"advantage": True}},
            {"key": "bless", "label": "Bless", "effect": {"modifier": 2}},
        ])
        s = self.pad_state(page)
        self.assertEqual(s["offers"], ["kenku_recall", "bless"])
        self.assertFalse(s["offerRowHidden"])

    def test_an_offer_unlocks_adv_and_the_modifier_but_not_the_die(self):
        """The mask. The die stays locked because a Stealth check is 1d20 and
        letting the phone pick 1d8 is not a choice, it is a different check."""
        page = self.open_pad()
        self.prescribe(page, offers=[
            {"key": "kenku_recall", "label": "Kenku Recall",
             "effect": {"advantage": True}}])
        s = self.pad_state(page)
        self.assertEqual(s["adv"], [])
        self.assertEqual(s["mod"], [])
        self.assertEqual(s["die"], ["1d4", "1d6", "1d8", "1d10", "1d12",
                                    "1d100", "2d6"])

    def test_the_label_stays_readonly_even_with_offers(self):
        """The label is a fourth claim and it is not relaxed. A phone renaming
        the check the GM asked for would corrupt the transcript."""
        page = self.open_pad()
        self.prescribe(page, offers=[
            {"key": "kenku_recall", "label": "Kenku Recall",
             "effect": {"advantage": True}}])
        self.assertTrue(page.evaluate(
            "() => document.getElementById('dp-label').hasAttribute('readonly')"))

    def test_tapping_an_offer_marks_it_and_tapping_again_unmarks_it(self):
        page = self.open_pad()
        self.prescribe(page, offers=[
            {"key": "kenku_recall", "label": "Kenku Recall",
             "effect": {"advantage": True}}])
        self.assertEqual(self.pad_state(page)["rollLabel"], "Roll")
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(60)
        self.assertTrue(page.evaluate(
            "() => document.querySelector('.dp-offer[data-offer=\"kenku_recall\"]')"
            ".classList.contains('active')"))
        # The spend is visible on the Roll button BEFORE it is committed, so a
        # mis-tap is caught before the dice are thrown.
        self.assertEqual(self.pad_state(page)["rollLabel"], "Roll (Kenku Recall)")
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(60)
        self.assertEqual(self.pad_state(page)["rollLabel"], "Roll")

    def test_a_free_roll_offers_nothing(self):
        """A free roll has no prescribed check for an offer to apply to, so the
        row must be empty rather than left offering a button nobody would check."""
        page = self.open_pad()
        self.prescribe(page, offers=[
            {"key": "kenku_recall", "label": "Kenku Recall",
             "effect": {"advantage": True}}])
        self.assertTrue(self.pad_state(page)["offers"])
        # A cancelled request is the client's own reset path.
        page.evaluate("() => window._onDiceRequestCancelled('req0001')")
        page.wait_for_timeout(120)
        s = self.pad_state(page)
        self.assertEqual(s["offers"], [])
        self.assertEqual(s["rollLabel"], "Roll")

    def test_a_new_request_drops_a_marked_offer_it_does_not_offer(self):
        """A stale key must not ride a new request. The server would refuse it,
        but a button that used to work and now silently does not reads as a bug
        on the player's phone."""
        page = self.open_pad()
        self.prescribe(page, request_id="req0001", offers=[
            {"key": "kenku_recall", "label": "Kenku Recall",
             "effect": {"advantage": True}}])
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(60)
        self.prescribe(page, request_id="req0002", offers=[
            {"key": "bless", "label": "Bless", "effect": {"modifier": 2}}])
        s = self.pad_state(page)
        self.assertEqual(s["offers"], ["bless"])
        self.assertEqual(s["rollLabel"], "Roll")


class PadPostsTheSpend(BrowserTestCase):
    """The spend reaches the server on the Roll press — one commit point."""

    module_name = "gm_display_app_pad_spend"

    PAD = "/?view=input&character=Kairos"

    def open_pad(self, **kw):
        return self.open_page(path=self.PAD, **kw)

    def test_the_roll_body_carries_the_marked_key(self):
        page = self.open_pad()
        seen = []
        page.route("**/player-input/dice", lambda route: (
            seen.append(route.request.post_data) or route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"spec": "1d20", "modifier": 0,
                                 "kept": [11], "subtotal": 11, "total": 11,
                                 "text": "Kairos rolls 1d20: [11] = 11"}))))
        page.evaluate("""req => window._onDiceRequest(req)""", {
            "request_id": "req0001", "characters": ["Kairos"], "spec": "1d20",
            "modifier": 0, "advantage": "normal", "label": "Stealth",
            "offers": [{"key": "kenku_recall", "label": "Kenku Recall",
                        "effect": {"advantage": True}}]})
        page.wait_for_timeout(150)
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(60)
        page.click("#dp-roll")
        page.wait_for_timeout(2500)          # the reel animation runs ~1s
        self.assertEqual(len(seen), 1)
        body = json.loads(seen[0])
        self.assertEqual(body.get("spend"), "kenku_recall")
        self.assertEqual(body.get("request_id"), "req0001")

    def test_no_offer_means_no_spend_field(self):
        page = self.open_pad()
        seen = []
        page.route("**/player-input/dice", lambda route: (
            seen.append(route.request.post_data) or route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"spec": "1d20", "modifier": 0,
                                 "kept": [11], "subtotal": 11, "total": 11,
                                 "text": "Kairos rolls 1d20: [11] = 11"}))))
        # A free roll: no prescribed request, so no offer row, no `spend` key.
        # The pad opens on the Move tab, so switch to Roll the way a player does.
        page.click('.dp-tab[data-tab="roll"]')
        page.wait_for_timeout(120)
        self.assertEqual(page.evaluate(
            "() => Array.from(document.querySelectorAll('.dp-offer')).length"), 0)
        page.click("#dp-roll")
        page.wait_for_timeout(2500)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("spend", json.loads(seen[0]))

    def test_a_refusal_shows_the_server_message_and_keeps_the_mark(self):
        """A refused spend spent nothing, so the mark stays — the player can
        drop it and roll straight — and the refusal is the server's sentence,
        because a generic 'roll failed' on a phone that just cost a use reads
        as a broken app."""
        page = self.open_pad()
        page.route("**/player-input/dice", lambda route: route.fulfill(
            status=400, content_type="application/json",
            body=json.dumps({"error": "the GM did not offer 'kenku_recall' on "
                                      "this roll — nothing was spent"})))
        page.evaluate("""req => window._onDiceRequest(req)""", {
            "request_id": "req0001", "characters": ["Kairos"], "spec": "1d20",
            "modifier": 0, "advantage": "normal", "label": "Stealth",
            "offers": [{"key": "kenku_recall", "label": "Kenku Recall",
                        "effect": {"advantage": True}}]})
        page.wait_for_timeout(150)
        page.click('.dp-offer[data-offer="kenku_recall"]')
        page.wait_for_timeout(60)
        page.click("#dp-roll")
        page.wait_for_timeout(2500)
        line = page.text_content("#dp-result-line")
        self.assertIn("did not offer", line)
        self.assertNotEqual(line.strip(), "roll failed")
        self.assertEqual(page.text_content("#dp-roll"), "Roll (Kenku Recall)")


if __name__ == "__main__":
    import unittest
    unittest.main()