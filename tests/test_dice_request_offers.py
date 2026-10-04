"""A prescribed roll must carry the features the GM put on the table (RS1.1).

A DM prescribed a roll; the player held a feature that would improve it and the
phone gave him nothing to press. The cause was not a missing dialog: the request
had no field for "the player might have something", and the pad actively
forbade deviating — `_setLocked` disabled every adv/dis button that was not the
prescribed one.

Two halves, and the second is the one that can lie:

- the DOOR (`/dice-request`). A malformed offer is a 400 and registers nothing.
  An offer that would do nothing is worse than no offer, because it spends a
  resource for nothing.
- the TRUST (`/player-input/dice`). `spend` names one of the server's own
  offers and is never taken from the phone's word for it. A phone cannot spend a
  feature the GM never offered, and cannot make the roll line name one.

Harness copied from tests/test_dice_request_results.py:17-33.
"""
import importlib.util
import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_offers", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OfferDoor(unittest.TestCase):
    """`POST /dice-request` — what the GM may put on the table."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._persist_log = lambda: None
        cls.mod._persist_tail = lambda: None
        cls.mod._broadcast = lambda payload: None
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def tearDown(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def ask(self, offers, **extra):
        body = {"characters": ["Kairos"], "spec": "1d20", "modifier": 0}
        body.update(extra)
        if offers is not None:
            body["offers"] = offers
        return self.client.post("/dice-request", data=json.dumps(body),
                                content_type="application/json")

    def entry(self, request_id):
        with self.mod._dice_pending_lock:
            return self.mod._dice_pending.get(request_id)

    # ── The door refuses ──────────────────────────────────────────────────

    def test_an_unknown_effect_key_is_refused_and_registers_nothing(self):
        r = self.ask(["Kenku Recall:granted"])
        self.assertEqual(r.status_code, 400)
        self.assertIn("granted", r.get_json()["error"])
        # The whole point: not merely a bad status. A half-issued request would
        # render buttons against an offer list the server never accepted.
        with self.mod._dice_pending_lock:
            self.assertEqual(self.mod._dice_pending, {})

    def test_an_empty_effect_is_refused_and_registers_nothing(self):
        r = self.ask(["Kenku Recall:"])
        self.assertEqual(r.status_code, 400)
        with self.mod._dice_pending_lock:
            self.assertEqual(self.mod._dice_pending, {})

    def test_an_offer_with_no_colon_is_refused(self):
        """No ':' means no effect at all, which is the same lie in another shape."""
        r = self.ask(["Kenku Recall"])
        self.assertEqual(r.status_code, 400)
        self.assertIn("effect", r.get_json()["error"])

    def test_two_offers_with_the_same_slug_are_refused(self):
        r = self.ask(["Kenku Recall:advantage", "kenku recall:+2"])
        self.assertEqual(r.status_code, 400)
        self.assertIn("kenku_recall", r.get_json()["error"])
        with self.mod._dice_pending_lock:
            self.assertEqual(self.mod._dice_pending, {})

    def test_an_offer_saying_both_advantage_and_disadvantage_is_refused(self):
        r = self.ask(["Recall:advantage,disadvantage"])
        self.assertEqual(r.status_code, 400)

    def test_a_bonus_die_is_accepted_now_that_the_slice_has_landed(self):
        """The inverse of the refusal this file asserted before RS1.2: an `NdM`
        offer is now the real thing rather than a button that does nothing.
        What must still hold is the door's other refusals, asserted below —
        an accepted effect did not make the door permissive."""
        r = self.ask(["Bardic Inspiration:1d6"])
        self.assertEqual(r.status_code, 200)
        entry = self.entry(r.get_json()["request_id"])
        self.assertEqual(entry["meta"]["offers"][0]["effect"], {"bonus": "1d6"})
        # A die that is not a die is still refused.
        self.assertEqual(self.ask(["X:1d"]).status_code, 400)
        self.assertEqual(self.ask(["X:99d6"]).status_code, 400)
        self.assertEqual(self.ask(["X:0d6"]).status_code, 400)

    def test_every_refusal_carries_a_message(self):
        """A silent 400 at the door is indistinguishable from a network blip."""
        for bad in (["X:granted"], ["X:"], ["X"], ["X:+999999"]):
            with self.subTest(bad=bad):
                body = self.ask(bad).get_json()
                self.assertTrue(body.get("error", "").strip())

    # ── The door accepts ──────────────────────────────────────────────────

    def test_a_valid_offer_is_stored_with_a_derived_key(self):
        rid = self.ask(["Kenku Recall:advantage"]).get_json()["request_id"]
        entry = self.entry(rid)
        self.assertEqual(entry["meta"]["offers"], [
            {"key": "kenku_recall", "label": "Kenku Recall",
             "effect": {"advantage": True}}])

    def test_disadvantage_and_a_flat_modifier_are_both_offer_shapes(self):
        rid = self.ask(["Illusion:disadvantage", "Bless:+2"]).get_json()["request_id"]
        offers = self.entry(rid)["meta"]["offers"]
        self.assertEqual(offers[0]["effect"], {"advantage": False})
        self.assertEqual(offers[1]["effect"], {"modifier": 2})

    def test_a_request_with_no_offers_field_is_unchanged(self):
        """The common case must not move. No `offers` key at all is not an error."""
        rid = self.ask(None).get_json()["request_id"]
        self.assertEqual(self.entry(rid)["meta"]["offers"], [])

    def test_offers_reach_the_pending_snapshot(self):
        """The reload / second-window path. The snapshot is the server's own copy,
        so carrying offers widens what a window can SEE, never what a phone may
        spend — the spend is still checked against the pending entry."""
        rid = self.ask(["Kenku Recall:advantage"]).get_json()["request_id"]
        snap = {s["request_id"]: s for s in self.mod._dice_pending_snapshot()}
        self.assertEqual(snap[rid]["offers"][0]["key"], "kenku_recall")

    def test_the_broadcast_carries_the_offers(self):
        """The phone renders buttons from the broadcast, not from the snapshot."""
        seen = []
        self.mod._broadcast = lambda p: seen.append(p)
        self.ask(["Kenku Recall:advantage"])
        req = [p for p in seen if "dice_request" in p][0]["dice_request"]
        self.assertEqual(req["offers"][0]["label"], "Kenku Recall")

    def test_an_empty_offer_list_is_not_an_error(self):
        self.assertEqual(self.ask([]).status_code, 200)


def _set_resources(mod, features, character="Kairos"):
    """Declare how many uses are left for each named feature.

    The roster entry is pushed FIRST, in its own request, because `/stats`
    applies a mutation op only to a player it already knows: a push naming
    someone new appends an entry built from the non-mutation keys alone, with
    `_resource_set` stripped (`test_a_resource_set_on_an_unknown_player_creates_
    the_name_but_no_counter` in tests/test_resources_counter.py pins that). One
    request carrying both would therefore register the character and silently
    drop the counter, and the spend would be refused at check 3 with a message
    about a counter nobody set.

    That made this helper order-dependent: it worked from the second call on,
    because the first had created the name. So the file passed on a developer
    machine, where `display/stats.json` is gitignored runtime state that a
    previous run left holding Kairos, and failed in CI on a clean checkout,
    where the roster starts empty and the very first seed registered nothing.
    The seeding must not depend on what an earlier test or an earlier run left
    behind.
    """
    client = mod.app.test_client()
    client.post("/stats", data=json.dumps(
        {"players": [{"name": character, "hp": {"current": 1, "max": 1}}]}),
        content_type="application/json")
    client.post("/stats", data=json.dumps(
        {"players": [{"name": character, "_resource_set": features}]}),
        content_type="application/json")


class _SpendsNeedCounters:
    """Mixin: since RS2.2 an offer alone is no longer spendable.

    A spend is REFUSED without a counter (check 3), so every test that spends has
    to declare how many uses are left. That is the point of the counter, so this
    is not incidental setup — it is what makes an offer usable at all.

    Seeded automatically from the offer list so a new test cannot forget it and
    fail for the wrong reason. `set_resources` is the explicit form for a test
    that wants a specific count.
    """

    def _seed_counters_for(self, offers):
        labels = [o.split(":", 1)[0].strip() for o in (offers or [])]
        if labels:
            self.set_resources({f: {"used": 0, "max": 2} for f in labels})

    def set_resources(self, features, character="Kairos"):
        _set_resources(self.mod, features, character)


class SpendChecks(_SpendsNeedCounters, unittest.TestCase):
    """`POST /player-input/dice` — checks 1, 2, 3 and 4.

    Check 3 arrived with the counter (RS2.2). Every test here seeds a counter
    for the feature it spends, because a spend without one is refused.
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._persist_log = lambda: None
        cls.mod._persist_tail = lambda: None
        cls.mod._broadcast = lambda payload: None
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def tearDown(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()


    def request(self, offers=None, **extra):
        body = {"characters": ["Kairos"], "spec": "1d20", "modifier": 0,
                "label": "Stealth"}
        body.update(extra)
        if offers is not None:
            body["offers"] = offers
            self._seed_counters_for(offers)
        return self.client.post("/dice-request", data=json.dumps(body),
                                content_type="application/json").get_json()["request_id"]

    def roll(self, rid, spend=None, **extra):
        body = {"character": "Kairos", "spec": "1d20", "modifier": 0,
                "label": "Stealth", "request_id": rid}
        if spend is not None:
            body["spend"] = spend
        body.update(extra)
        return self.client.post("/player-input/dice", data=json.dumps(body),
                                content_type="application/json")

    # ── Check 1: a real pending request ───────────────────────────────────

    def test_a_spend_without_a_request_id_is_refused(self):
        r = self.roll("", spend="kenku_recall")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json()["error"])

    def test_a_spend_against_an_unknown_request_is_refused(self):
        r = self.roll("deadbeefcafe", spend="kenku_recall")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json()["error"])

    def test_a_spend_against_a_finished_request_is_refused(self):
        """The entry is popped the moment the last prescribed character rolls, so
        a second press finds nothing. This is the single-client half of the
        double-spend guard; the counter is the other half and is not here yet."""
        rid = self.request(["Kenku Recall:advantage"])
        first = self.roll(rid, spend="kenku_recall")
        self.assertEqual(first.status_code, 200)
        again = self.roll(rid, spend="kenku_recall")
        self.assertEqual(again.status_code, 400)

    # ── Check 2: the key is one of the server's own offers ────────────────

    def test_an_unknown_key_is_refused(self):
        """The phone must not be able to spend a feature the GM never offered.
        It is a 400, not a silent no-op: a no-op would leave the player believing
        they spent something."""
        rid = self.request(["Kenku Recall:advantage"])
        r = self.roll(rid, spend="second_wind")
        self.assertEqual(r.status_code, 400)
        self.assertIn("second_wind", r.get_json()["error"])

    def test_an_unknown_key_does_not_roll(self):
        rid = self.request(["Kenku Recall:advantage"])
        r = self.roll(rid, spend="second_wind")
        self.assertIsNone(r.get_json().get("text"))
        # …and the pending request is untouched, so the player may still roll it.
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertFalse(st["complete"])

    def test_a_request_with_no_offers_refuses_every_key(self):
        rid = self.request()
        self.assertEqual(self.roll(rid, spend="kenku_recall").status_code, 400)

    # ── Check 4: the effect does not double-count ─────────────────────────

    def test_an_advantage_offer_on_an_advantage_prescription_is_refused(self):
        """The GM already made this roll advantageous. Spending Recall on it
        would burn a per-rest resource for nothing, and naming it in the roll
        line would be a false claim.

        The roll body carries the prescribed `advantage` because that is what
        the pad posts: `_applyDiceRequest` clicks the prescribed adv button, so
        this is what a real phone sends, not a contrived shape."""
        rid = self.request(["Kenku Recall:advantage"], advantage="advantage")
        r = self.roll(rid, spend="kenku_recall", advantage="advantage")
        self.assertEqual(r.status_code, 400)
        self.assertIn("double", r.get_json()["error"].lower())

    def test_an_advantage_offer_on_a_disadvantage_prescription_is_refused(self):
        rid = self.request(["Kenku Recall:advantage"], advantage="disadvantage")
        r = self.roll(rid, spend="kenku_recall", advantage="disadvantage")
        self.assertEqual(r.status_code, 400)
        self.assertIn("double", r.get_json()["error"].lower())

    def test_a_flat_offer_still_applies_on_an_advantage_prescription(self):
        """Only an ADVANTAGE effect double-counts. A flat one adds to the total
        on any roll, so refusing it here would be wrong, not conservative."""
        rid = self.request(["Bless:+2"], advantage="advantage")
        r = self.roll(rid, spend="bless", advantage="advantage")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["modifier"], 2)


class RollLineNamesTheSpend(_SpendsNeedCounters, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._persist_log = lambda: None
        cls.mod._persist_tail = lambda: None
        cls.mod._broadcast = lambda payload: None
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def tearDown(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def request(self, offers=None, **extra):
        body = {"characters": ["Kairos"], "spec": "1d20", "modifier": 6,
                "label": "Stealth"}
        body.update(extra)
        if offers is not None:
            body["offers"] = offers
            self._seed_counters_for(offers)
        return self.client.post("/dice-request", data=json.dumps(body),
                                content_type="application/json").get_json()["request_id"]

    def roll(self, rid, **extra):
        body = {"character": "Kairos", "spec": "1d20", "modifier": 6,
                "label": "Stealth", "request_id": rid}
        body.update(extra)
        return self.client.post("/player-input/dice", data=json.dumps(body),
                                content_type="application/json")

    def test_an_advantage_offer_actually_makes_the_roll_advantage(self):
        rid = self.request(["Kenku Recall:advantage"])
        r = self.roll(rid, spend="kenku_recall")
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["advantage"], "advantage")
        self.assertEqual(len(body["both"]), 2)
        self.assertEqual(body["kept"][0], max(body["both"]))

    def test_the_roll_line_names_the_feature_after_the_check(self):
        rid = self.request(["Kenku Recall:advantage"])
        text = self.roll(rid, spend="kenku_recall").get_json()["text"]
        self.assertTrue(text.endswith("— Stealth (Kenku Recall)"), text)
        self.assertIn("(advantage)", text)

    def test_a_spend_is_named_even_with_no_label(self):
        rid = self.request(["Kenku Recall:advantage"], label="")
        text = self.roll(rid, spend="kenku_recall", label="").get_json()["text"]
        self.assertTrue(text.endswith("(Kenku Recall)"), text)

    def test_a_roll_with_no_spend_is_byte_identical_to_before(self):
        """The common case. Any change here would change every roll, so this
        pins the exact pre-existing string shape: modifier repeated once in the
        header and once in the breakdown, and no parentheses anywhere."""
        rid = self.request(["Kenku Recall:advantage"])
        text = self.roll(rid).get_json()["text"]
        self.assertRegex(text, r"^Kairos rolls 1d20\+6: \[\d+\] \+6 = \d+ — Stealth$")
        self.assertNotIn("(", text)

    def test_the_response_carries_the_spend_for_the_pad(self):
        rid = self.request(["Kenku Recall:advantage"])
        body = self.roll(rid, spend="kenku_recall").get_json()
        self.assertEqual(body["spend"], "kenku_recall")
        self.assertEqual(body["spend_label"], "Kenku Recall")

    def test_a_flat_offer_adds_to_the_modifier(self):
        rid = self.request(["Bless:+2"])
        r = self.roll(rid, spend="bless")
        self.assertEqual(r.get_json()["modifier"], 8)   # 6 prescribed + 2 offered
        self.assertIn("+8", r.get_json()["text"])

    def test_the_effect_comes_from_the_offer_not_from_the_phone(self):
        """The offer's amount is read from the server's own offer list. A phone
        cannot inflate it by asking twice or by claiming a bigger modifier: the
        same +2 lands on each of two rolls against the same request.

        Both players need their own counter: the counter is per-character, and a
        second character with none is refused by check 3 rather than spending
        the first player's uses."""
        # Mira has to exist before she can hold a counter: `_resource_set` is a
        # mutation, not a player upsert.
        self.mod.app.test_client().post("/stats", data=json.dumps({"players": [
            {"name": "Mira", "hp": {"current": 14, "max": 14}}]}),
            content_type="application/json")
        self.set_resources({"Bless": {"used": 0, "max": 2}}, "Kairos")
        self.set_resources({"Bless": {"used": 0, "max": 2}}, "Mira")
        rid = self.request(["Bless:+2"], characters=["Kairos", "Mira"])
        first = self.roll(rid, character="Kairos", spend="bless").get_json()
        second = self.roll(rid, character="Mira", spend="bless").get_json()
        self.assertEqual(first["modifier"], 8)    # 6 prescribed + 2 offered
        self.assertEqual(second["modifier"], 8)

    def test_a_phone_asking_for_a_bigger_modifier_just_gets_its_own_clamped(self):
        """Nothing here trusts the phone's modifier, but nothing here changes it
        either: this test records that the offer adds to whatever the pad sent
        rather than replacing it, so an offer cannot be used to rewrite the
        check's own modifier."""
        rid = self.request(["Bless:+2"])
        r = self.roll(rid, spend="bless", modifier=3)
        self.assertEqual(r.get_json()["modifier"], 5)    # 3 sent + 2 offered


class TheRollStillResolvesTheRequest(unittest.TestCase):
    """The pre-existing `--wait` path must be untouched by any of this."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._persist_log = lambda: None
        cls.mod._persist_tail = lambda: None
        cls.mod._broadcast = lambda payload: None
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def tearDown(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def test_a_spent_roll_still_releases_the_wait(self):
        rid = self.client.post("/dice-request", data=json.dumps(
            {"characters": ["Kairos"], "spec": "1d20", "modifier": 0,
             "label": "Insight check",
             "offers": ["Kenku Recall:advantage"]}),
            content_type="application/json").get_json()["request_id"]
        r = self.client.post("/player-input/dice", data=json.dumps(
            {"character": "Kairos", "spec": "1d20", "modifier": 0,
             "label": "Insight check", "request_id": rid,
             "spend": "kenku_recall"}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertTrue(st["complete"])
        self.assertEqual(len(st["results"]), 1)


if __name__ == "__main__":
    unittest.main()