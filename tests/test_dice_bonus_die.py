"""A bonus die is a separate roll, not a modifier (RS1.2).

The shortcut here is to let an offer carry a flat modifier, which works for
Bless and is wrong for Bardic Inspiration. 2014 SRD 5.1, Bardic Inspiration:
"Once within the next 10 minutes, the creature can roll the die and add the
number rolled to one ability check, attack roll, or saving throw it makes. The
creature can wait until after it rolls the d20 before deciding to use the
Bardic Inspiration die."

That sentence is the whole design. It is a die, it is added to the d20, and the
d20 is already rolled when the decision is made. So:

- folding it into `modifier` renders the pad as 1d20+4, claiming a flat 4
  rather than a die that can come up 1, and the table reads the pad — the error
  is displayed, not hidden by the server;
- rolling it before advantage resolves would let a d4 change which d20 is kept,
  which is not a bonus die but a third d20 with no rules behind it.

The die sizes are the 2014 ones: d6 at Bard 1, d8 at 5, d10 at 10, d12 at 15.
"""
import contextlib
import importlib.util
import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_bonus", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def seeded_faces(mod, *faces):
    """Pin the rolled faces to `faces` (1-based, as the table sees them), then
    put the real RNG back.

    `secrets.randbelow(n)` returns 0..n-1 and the server adds 1, so the values
    are decremented here. Writing them 1-based means a failing assertion reads
    as the face the table saw rather than an off-by-one to be decoded.

    A context manager rather than an assignment in the test body: the first
    version of this file assigned `secrets.randbelow` directly and never
    restored it, which leaked a three-element iterator into every test that ran
    afterwards and turned the whole class into StopIteration. Restoring is not
    optional — `secrets` is a shared module object, so the leak escapes this
    class too.
    """
    it = iter(faces)
    real = mod.secrets.randbelow
    mod.secrets.randbelow = lambda n: (next(it) - 1) % n
    try:
        yield
    finally:
        mod.secrets.randbelow = real


class BonusDie(unittest.TestCase):
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

    def set_resources(self, features, character="Kairos"):
        """Declare how many uses are left.

        Since RS2.2 a spend is refused without a counter (check 3), so every
        test that spends a bonus die has to declare the uses first. Seeded from
        the offer list automatically so a new test cannot forget it."""
        self.mod.app.test_client().post("/stats", data=json.dumps(
            {"players": [{"name": character, "_resource_set": features}]}),
            content_type="application/json")

    def request(self, offers=None, **extra):
        body = {"characters": ["Kairos"], "spec": "1d20", "modifier": 0,
                "label": "Persuasion"}
        body.update(extra)
        if offers is not None:
            body["offers"] = offers
            labels = [o.split(":", 1)[0].strip() for o in offers]
            # The counter is per-character, and a two-character request spends
            # from each in turn, so seed everyone the request names.
            for name in body["characters"]:
                self.set_resources({f: {"used": 0, "max": 2} for f in labels}, name)
        return self.client.post("/dice-request", data=json.dumps(body),
                                content_type="application/json").get_json()["request_id"]

    def roll(self, rid, spend=None, **extra):
        body = {"character": "Kairos", "spec": "1d20", "modifier": 0,
                "label": "Persuasion", "request_id": rid}
        if spend is not None:
            body["spend"] = spend
        body.update(extra)
        return self.client.post("/player-input/dice", data=json.dumps(body),
                                content_type="application/json")

    # ── it is accepted at the door now ─────────────────────────────────────

    def test_a_bonus_die_offer_is_accepted_and_carried(self):
        rid = self.request(["Bardic Inspiration:1d6"])
        with self.mod._dice_pending_lock:
            entry = self.mod._dice_pending.get(rid)
        self.assertEqual(entry["meta"]["offers"][0]["effect"], {"bonus": "1d6"})

    def test_the_door_still_refuses_a_malformed_bonus(self):
        r = self.client.post("/dice-request", data=json.dumps(
            {"characters": ["Kairos"], "offers": ["X:1d"]}),
            content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json()["error"])

    def test_the_door_still_refuses_an_out_of_range_bonus(self):
        r = self.client.post("/dice-request", data=json.dumps(
            {"characters": ["Kairos"], "offers": ["X:99d6"]}),
            content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_the_door_still_refuses_an_empty_effect(self):
        self.assertEqual(self.client.post("/dice-request", data=json.dumps(
            {"characters": ["Kairos"], "offers": ["X:"]}),
            content_type="application/json").status_code, 400)

    # ── it is a separate roll ──────────────────────────────────────────────

    def test_a_bonus_die_produces_a_second_set_of_dice(self):
        """Two distinct rolls, and the total is kept + bonus + modifier."""
        rid = self.request(["Bardic Inspiration:1d6"])
        body = self.roll(rid, spend="bardic_inspiration").get_json()
        self.assertEqual(len(body["kept"]), 1)
        self.assertEqual(len(body["bonus_faces"]), 1)
        self.assertTrue(1 <= body["bonus_faces"][0] <= 6)
        self.assertEqual(body["total"],
                         body["kept"][0] + body["bonus_faces"][0] + body["modifier"])

    def test_the_bonus_is_not_part_of_the_kept_die(self):
        rid = self.request(["Bardic Inspiration:1d6"])
        body = self.roll(rid, spend="bardic_inspiration").get_json()
        # `kept` is the d20 only. A pad that locks the reel to `kept` must not
        # be handed a sum, or it would show a face nobody rolled.
        self.assertEqual(len(body["kept"]), 1)
        self.assertNotEqual(body["kept"][0], body["total"])

    def test_the_bonus_is_a_die_and_not_a_fixed_number(self):
        """The flat-modifier shortcut's symptom: every total lands on the same
        offset from the d20. A d6 that contributes a spread of values is the
        observable difference, and it needs no seeding."""
        deltas = set()
        for _ in range(60):
            rid = self.request(["Bardic Inspiration:1d6"])
            body = self.roll(rid, spend="bardic_inspiration").get_json()
            deltas.add(body["total"] - body["kept"][0])
        self.assertEqual(deltas, set(range(1, 7)),
                         "the bonus die collapsed to a fixed offset")

    def test_the_bonus_face_is_never_zero(self):
        """A d6 rolls 1..6. An off-by-one that allowed 0 would let the display
        claim a die that came up below its minimum."""
        seen = set()
        for _ in range(60):
            rid = self.request(["Bardic Inspiration:1d6"])
            seen.update(self.roll(rid, spend="bardic_inspiration")
                        .get_json()["bonus_faces"])
        self.assertTrue(seen)
        self.assertTrue(min(seen) >= 1)
        self.assertTrue(max(seen) <= 6)

    # ── ordering: bonus is rolled AFTER advantage ──────────────────────────

    def test_the_bonus_never_participates_in_advantage(self):
        """The kept d20 is decided before the die exists.

        Driven off a fixed RNG: with the same seeded values the kept face is
        identical with and without the bonus. If the bonus were drawn first, or
        folded into the comparison, these would differ."""
        rid = self.request(offers=["Bardic Inspiration:1d6"],
                           characters=["Kairos", "Mira"], advantage="advantage")
        # The pad pre-fills the prescribed advantage and posts it back; the
        # server reads the body's `advantage`, not the request's.
        with seeded_faces(self.mod, 8, 4, 5):
            with_bonus = self.roll(rid, character="Kairos",
                                   spend="bardic_inspiration",
                                   advantage="advantage").get_json()
        with seeded_faces(self.mod, 8, 4):
            without = self.roll(rid, character="Mira",
                                advantage="advantage").get_json()

        self.assertEqual(with_bonus["both"], [8, 4])
        self.assertEqual(with_bonus["kept"], [8])       # the higher of the pair
        self.assertEqual(without["both"], [8, 4])
        self.assertEqual(without["kept"], [8])          # unchanged by the bonus
        self.assertEqual(with_bonus["bonus_faces"], [5])
        self.assertEqual(with_bonus["total"], 13)       # 8 + 5 + 0

    def test_a_bonus_die_on_a_disadvantage_roll_is_added_to_the_kept_face(self):
        rid = self.request(offers=["Bardic Inspiration:1d6"],
                           characters=["Kairos", "Mira"], advantage="disadvantage")
        with seeded_faces(self.mod, 20, 4, 3):
            body = self.roll(rid, character="Kairos", spend="bardic_inspiration",
                             advantage="disadvantage").get_json()
        self.assertEqual(body["kept"], [4])             # the lower of the pair
        self.assertEqual(body["bonus_faces"], [3])
        self.assertEqual(body["total"], 7)

    # ── the render, which is where a lie would be visible ─────────────────

    def test_the_roll_line_shows_the_die_and_its_faces(self):
        rid = self.request(["Bardic Inspiration:1d6"])
        with seeded_faces(self.mod, 12, 6):
            text = self.roll(rid, spend="bardic_inspiration").get_json()["text"]
        self.assertIn("+ 1d6 [6]", text)

    def test_the_roll_line_never_shows_the_bonus_as_a_flat_number(self):
        """The exact failure this slice exists to prevent. A +6 on a d6 is the
        one value that would read as legitimate, so it is asserted directly."""
        rid = self.request(["Bardic Inspiration:1d6"])
        with seeded_faces(self.mod, 12, 6):
            text = self.roll(rid, spend="bardic_inspiration").get_json()["text"]
        self.assertRegex(text, r"\[12\] \+ 1d6 \[6\] = 18")
        # No bare "+6" adjacent to the kept face: the die must be in the way.
        self.assertNotRegex(text, r"\[12\] \+6 ")

    def test_the_header_names_the_both_dice(self):
        rid = self.request(["Bardic Inspiration:1d6"])
        text = self.roll(rid, spend="bardic_inspiration").get_json()["text"]
        self.assertTrue(text.startswith("Kairos rolls 1d201d6"), text)

    def test_the_response_carries_the_bonus_for_the_pad(self):
        rid = self.request(["Bardic Inspiration:1d6"])
        body = self.roll(rid, spend="bardic_inspiration").get_json()
        self.assertEqual(body["bonus"], "1d6")
        self.assertEqual(len(body["bonus_faces"]), 1)

    # ── the phone may not name its own die ────────────────────────────────

    def test_a_phone_supplied_bonus_is_refused(self):
        """The phone asking for a die the GM never offered is the same forgery
        as naming a feature. Refused, and told so, not silently ignored."""
        rid = self.request()
        r = self.roll(rid, bonus="1d20")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no bonus die", r.get_json()["error"])

    def test_a_phone_cannot_swap_the_offered_die(self):
        rid = self.request(["Bardic Inspiration:1d6"])
        r = self.roll(rid, spend="bardic_inspiration", bonus="1d20")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(r.get_json().get("bonus_faces"))

    # ── nothing changed for the common case ───────────────────────────────

    def test_a_roll_with_no_bonus_is_byte_identical(self):
        """Bonus absent must not add a field, a space, or a null to the line.
        The `bonus` and `bonus_faces` keys exist and are None/[] — that is what
        "carried for the pad" means — but the text is unchanged."""
        rid = self.request(["Bardic Inspiration:1d6"])
        text = self.roll(rid).get_json()["text"]
        self.assertRegex(text, r"^Kairos rolls 1d20: \[\d+\] = \d+ — Persuasion$")
        self.assertNotIn("1d6", text)

    def test_a_roll_with_no_bonus_carries_an_empty_bonus(self):
        rid = self.request(["Bardic Inspiration:1d6"])
        body = self.roll(rid).get_json()
        self.assertIsNone(body["bonus"])
        self.assertEqual(body["bonus_faces"], [])


class BonusOnANonD20(unittest.TestCase):
    """Advantage only ever applies to a d20. A bonus die has no such gate."""

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

    def test_a_bonus_die_works_on_a_2d6(self):
        self.client.post("/stats", data=json.dumps({"players": [{
            "name": "Kairos", "hp": {"current": 20, "max": 20},
            "_resource_set": {"Bless": {"used": 0, "max": 2}}}]}),
            content_type="application/json")
        rid = self.client.post("/dice-request", data=json.dumps(
            {"characters": ["Kairos"], "spec": "2d6", "modifier": 1,
             "offers": ["Bless:1d4"]}), content_type="application/json"
        ).get_json()["request_id"]
        body = self.client.post("/player-input/dice", data=json.dumps(
            {"character": "Kairos", "spec": "2d6", "modifier": 1,
             "request_id": rid, "spend": "bless"}),
            content_type="application/json").get_json()
        self.assertEqual(len(body["kept"]), 2)
        self.assertEqual(len(body["bonus_faces"]), 1)
        self.assertEqual(body["total"],
                         sum(body["kept"]) + body["bonus_faces"][0] + 1)


if __name__ == "__main__":
    unittest.main()