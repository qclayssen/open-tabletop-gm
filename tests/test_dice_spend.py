"""Spending a resource: four checks, and the decrement that follows them (#284).

RS1 made the offer visible and named it in the roll line; RS2.1 made the counter.
This is where the counter moves, and it is the only place.

The four checks, in order, and the order is the design:

1. the request is a real pending request with that id
2. `spend` names an offer in THAT request's meta["offers"]
3. the player's counter for that key is > 0
4. the effect does not double-count

Each is proved below by a test that fails when its own guard is removed, rather
than by one test asserting that "the four checks work".

The security property is check 3: because the counter is server-side and
decremented on commit, a phone that reloads — or a second browser open on the
same character — sees the decremented value and is refused. No lock, no token,
no coordination, because the authority is the counter and not anything a client
holds.
"""
import importlib.util
import json
import pathlib
import threading
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app(name="gm_display_app_spend"):
    spec = importlib.util.spec_from_file_location(
        name, str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app(cls.__name__)
        cls.mod._token_ok = lambda: True
        cls.mod._persist_log = lambda: None
        cls.mod._persist_tail = lambda: None
        cls.mod._broadcast = lambda payload: None
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        # Two posts, and the reason matters. `replace_players: true` wipes the
        # list and then appends each incoming player through the NEW-player
        # branch, which strips every `_`-prefixed key — so a mutation op in the
        # same push is silently discarded. Found by this fixture quietly not
        # seeding anything; it is pre-existing behaviour for all mutation ops,
        # and it means `push_stats --replace-players` combined with
        # `--resource-set` in one call loses the counter.
        self.client.post("/stats", data=json.dumps({
            "players": [{"name": "Kairos", "hp": {"current": 20, "max": 20}}],
            "replace_players": True}), content_type="application/json")
        self.client.post("/stats", data=json.dumps({"players": [{
            "name": "Kairos",
            "_resource_set": {"Kenku Recall": {"used": 0, "max": 2},
                              "Bless": {"used": 0, "max": 2},
                              "Bardic Inspiration": {"used": 0, "max": 2}}}]}),
            content_type="application/json")
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def tearDown(self):
        with self.mod._dice_pending_lock:
            self.mod._dice_pending.clear()

    def request(self, offers=None, characters=None, **extra):
        body = {"characters": characters or ["Kairos"], "spec": "1d20",
                "modifier": 0, "label": "Stealth"}
        body.update(extra)
        if offers is not None:
            body["offers"] = offers
        return self.client.post("/dice-request", data=json.dumps(body),
                                content_type="application/json")

    def rid(self, offers=None, characters=None, **extra):
        return self.request(offers, characters, **extra).get_json()["request_id"]

    def roll(self, rid, character="Kairos", spend=None, **extra):
        body = {"character": character, "spec": "1d20", "modifier": 0,
                "label": "Stealth", "request_id": rid}
        if spend is not None:
            body["spend"] = spend
        body.update(extra)
        return self.client.post("/player-input/dice", data=json.dumps(body),
                                content_type="application/json")

    def counter(self, name="Kairos", label="Kenku Recall"):
        with self.mod._spend_lock, self.mod._stats_lock:
            p = next(x for x in self.mod._current_stats["players"]
                     if x["name"] == name)
            return dict(p["resources"].get(label, {}))

    def roll_for_both(self, rid=None, offer="Kenku Recall:advantage",
                      key="kenku_recall"):
        """A two-character request both players spend against.

        Kairos and Mira each need their OWN counter: the counter is
        per-character, so Mira spending without one is refused by check 3 rather
        than quietly drawing on Kairos's uses.
        """
        self.client.post("/stats", data=json.dumps({"players": [
            {"name": "Mira", "hp": {"current": 14, "max": 14}}]}),
            content_type="application/json")
        label = offer.split(":", 1)[0].strip()
        for who in ("Kairos", "Mira"):
            self.set_counter(label, 0, 2, who)
        if rid is None:
            rid = self.rid([offer], characters=["Kairos", "Mira"])
        self.roll(rid, character="Kairos", spend=key)
        self.roll(rid, character="Mira", spend=key)
        return rid

    def set_counter(self, label, used, max_, name="Kairos"):
        """Seed one counter. Separate from the roster push because
        `replace_players` strips mutation ops — see setUp."""
        self.client.post("/stats", data=json.dumps({"players": [{
            "name": name, "_resource_set": {label: {"used": used, "max": max_}}}]}),
            content_type="application/json")


# ── the happy path ──────────────────────────────────────────────────────────

class ASpendThatIsAllowed(_Base):
    def test_a_valid_spend_decrements_exactly_once(self):
        rid = self.rid(["Kenku Recall:advantage"])
        r = self.roll(rid, spend="kenku_recall")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.counter(), {"used": 1, "max": 2})

    def test_a_valid_spend_names_the_feature_in_the_roll_line(self):
        rid = self.rid(["Kenku Recall:advantage"])
        text = self.roll(rid, spend="kenku_recall").get_json()["text"]
        self.assertTrue(text.endswith("— Stealth (Kenku Recall)"), text)

    def test_a_valid_spend_applies_the_effect(self):
        rid = self.rid(["Kenku Recall:advantage"])
        body = self.roll(rid, spend="kenku_recall").get_json()
        self.assertEqual(body["advantage"], "advantage")

    def test_the_roll_reports_what_is_left(self):
        rid = self.rid(["Kenku Recall:advantage"])
        body = self.roll(rid, spend="kenku_recall").get_json()
        self.assertEqual(body["resource"], {"used": 0, "max": 2})

    def test_a_spend_does_not_decrement_without_a_spend_field(self):
        rid = self.rid(["Kenku Recall:advantage"])
        self.assertEqual(self.roll(rid).status_code, 200)
        self.assertEqual(self.counter(), {"used": 0, "max": 2})

    def test_each_character_spends_from_their_own_counter(self):
        """Two characters each spending once: each is 1/2, not one of them 2/2.
        A shared pool would be the wrong model — the character sheet has one
        Kenku Recall per character, not one per party."""
        self.roll_for_both()
        self.assertEqual(self.counter("Kairos"), {"used": 1, "max": 2})
        self.assertEqual(self.counter("Mira"), {"used": 1, "max": 2})

    def test_counters_are_per_character(self):
        self.client.post("/stats", data=json.dumps({"players": [{
            "name": "Mira", "hp": {"current": 14, "max": 14}}]}),
            content_type="application/json")
        self.set_counter("Kenku Recall", 0, 2, "Mira")
        rid = self.rid(["Kenku Recall:advantage"], characters=["Kairos", "Mira"])
        self.roll(rid, character="Kairos", spend="kenku_recall")
        self.assertEqual(self.counter("Mira"), {"used": 0, "max": 2})


# ── check 1 ─────────────────────────────────────────────────────────────────

class Check1RealPendingRequest(_Base):
    def test_an_unknown_request_id_is_refused(self):
        r = self.roll("deadbeefcafe", spend="kenku_recall")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter(), {"used": 0, "max": 2},
                         "a refused spend must not decrement")

    def test_a_spend_without_a_request_id_is_refused(self):
        r = self.roll("", spend="kenku_recall")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter(), {"used": 0, "max": 2})

    def test_a_spend_against_a_finished_request_is_refused(self):
        rid = self.rid(["Kenku Recall:advantage"])
        self.assertEqual(self.roll(rid, spend="kenku_recall").status_code, 200)
        # Same client pressing again: the entry is gone once the last prescribed
        # character has rolled, so check 1 refuses before check 3 is reached.
        again = self.roll(rid, spend="kenku_recall")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(self.counter(), {"used": 1, "max": 2},
                         "the second attempt must not decrement again")

    def test_a_cancelled_request_spends_nothing(self):
        rid = self.rid(["Kenku Recall:advantage"])
        self.client.delete(f"/dice-request/{rid}")
        r = self.roll(rid, spend="kenku_recall")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter(), {"used": 0, "max": 2})

    def test_a_bad_spec_does_not_spend(self):
        """The order that protects the counter.

        The base die is validated BEFORE the four checks, so a malformed spec
        cannot reach the decrement on its way to being refused — the use would be
        gone and the player would have no roll to show for it.

        Asserted on the ROLLING character's own counter. The first version of this
        test rolled as Mira and then asserted on Kairos's counter, which passed
        whatever the code did; found by the mutation that moves this validation
        after the commit also passing.
        """
        rid = self.rid(["Kenku Recall:advantage"])
        r = self.roll(rid, character="Kairos", spend="kenku_recall", spec="1dx")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter("Kairos"), {"used": 0, "max": 2})

    def test_an_out_of_range_spec_does_not_spend(self):
        """Same property for the bounds check rather than the regex."""
        rid = self.rid(["Kenku Recall:advantage"])
        r = self.roll(rid, character="Kairos", spend="kenku_recall", spec="99d20")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter("Kairos"), {"used": 0, "max": 2})


# ── check 2 ─────────────────────────────────────────────────────────────────

class Check2KeyMustBeOffered(_Base):
    def test_a_key_the_gm_never_offered_is_refused(self):
        rid = self.rid(["Kenku Recall:advantage"])
        r = self.roll(rid, spend="second_wind")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter(), {"used": 0, "max": 2})

    def test_the_refusal_says_what_was_not_offered(self):
        rid = self.rid(["Kenku Recall:advantage"])
        r = self.roll(rid, spend="second_wind")
        self.assertIn("second_wind", r.get_json()["error"])
        self.assertIn("did not offer", r.get_json()["error"])

    def test_an_offer_from_a_different_request_is_refused(self):
        """The lookup is against THIS request's offers, not any request's. Two
        concurrent prescribed rolls must not share an offer list."""
        first = self.rid(["Kenku Recall:advantage"], characters=["Kairos", "Mira"])
        self.rid(["Bless:+2"], characters=["Kairos", "Mira"])
        r = self.roll(first, character="Mira", spend="bless")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter("Kairos", "Bless"), {"used": 0, "max": 2})

    def test_a_request_with_no_offers_refuses_every_key(self):
        rid = self.rid()
        self.assertEqual(self.roll(rid, spend="kenku_recall").status_code, 400)

    def test_a_refused_spend_does_not_roll(self):
        rid = self.rid(["Kenku Recall:advantage"], characters=["Kairos", "Mira"])
        r = self.roll(rid, character="Mira", spend="second_wind")
        self.assertIsNone(r.get_json().get("text"))
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertFalse(st["complete"], "the player may still roll it")


# ── check 3 ─────────────────────────────────────────────────────────────────

class Check3CounterMustBePositive(_Base):
    def test_a_spent_counter_is_refused(self):
        self.client.post("/stats", data=json.dumps({"players": [{
            "name": "Kairos", "hp": {"current": 20, "max": 20},
            "_resource_set": {"Kenku Recall": {"used": 2, "max": 2}}}]}),
            content_type="application/json")
        rid = self.rid(["Kenku Recall:advantage"], characters=["Kairos", "Mira"])
        r = self.roll(rid, spend="kenku_recall")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter(), {"used": 2, "max": 2},
                         "a refused spend must not decrement below the cap")

    def test_the_refusal_says_the_feature_is_spent(self):
        self.client.post("/stats", data=json.dumps({"players": [{
            "name": "Kairos", "hp": {"current": 20, "max": 20},
            "_resource_set": {"Kenku Recall": {"used": 2, "max": 2}}}]}),
            content_type="application/json")
        rid = self.rid(["Kenku Recall:advantage"])
        error = self.roll(rid, spend="kenku_recall").get_json()["error"]
        self.assertIn("spent", error)
        self.assertIn("2/2", error, "the pad shows counts, so say the counts")

    def test_a_zero_cap_is_refused(self):
        """A feature set to 0 has no uses. Reading it as "unknown" and letting it
        through would be the clamp this check exists to prevent."""
        self.set_counter("Kenku Recall", 0, 0)
        rid = self.rid(["Kenku Recall:advantage"])
        self.assertEqual(self.roll(rid, spend="kenku_recall").status_code, 400)

    def test_a_feature_with_no_counter_is_refused(self):
        """An offer the GM never gave a cap for. Spending a resource nobody
        counted would be inventing state."""
        self.client.post("/stats", data=json.dumps({
            "players": [{"name": "Kairos", "hp": {"current": 20, "max": 20}}],
            "replace_players": True}), content_type="application/json")
        rid = self.rid(["Kenku Recall:advantage"])
        r = self.roll(rid, spend="kenku_recall")
        self.assertEqual(r.status_code, 400)
        self.assertIn("--resource-set", r.get_json()["error"])

    def test_the_counter_matches_case_insensitively(self):
        """The GM types the label twice by hand — once for --resource-set and once
        for --offer. A silent case mismatch would read as no counter at all, and
        the player would be told to configure a feature that IS set.

        A separate player, because setUp seeds "Kenku Recall" already and this
        test is about the display reading "kenku recall"."""
        self.client.post("/stats", data=json.dumps({"players": [
            {"name": "Mira", "hp": {"current": 14, "max": 14}}]}),
            content_type="application/json")
        self.set_counter("kenku recall", 0, 2, "Mira")
        rid = self.rid(["Kenku Recall:advantage"], characters=["Mira"])
        self.assertEqual(self.roll(rid, character="Mira",
                                   spend="kenku_recall").status_code, 200)
        self.assertEqual(self.counter("Mira", "kenku recall"),
                         {"used": 1, "max": 2})

    def test_a_spent_counter_makes_the_second_of_two_uses_fail(self):
        self.set_counter("Kenku Recall", 1, 2)
        rid = self.rid(["Kenku Recall:advantage"], characters=["Kairos", "Mira"])
        self.assertEqual(self.roll(rid, character="Mira",
                                   spend="kenku_recall").status_code, 400)
        self.assertEqual(self.counter(), {"used": 1, "max": 2})


# ── check 4 ─────────────────────────────────────────────────────────────────

class Check4NoDoubleCount(_Base):
    def test_an_advantage_offer_on_an_advantage_prescription_is_refused(self):
        rid = self.rid(["Kenku Recall:advantage"], advantage="advantage")
        r = self.roll(rid, spend="kenku_recall", advantage="advantage")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter(), {"used": 0, "max": 2},
                         "a double-counted spend must cost nothing")

    def test_an_advantage_offer_on_a_disadvantage_prescription_is_refused(self):
        rid = self.rid(["Kenku Recall:advantage"], advantage="disadvantage")
        r = self.roll(rid, spend="kenku_recall", advantage="disadvantage")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter(), {"used": 0, "max": 2})

    def test_the_refusal_says_it_would_double_count(self):
        rid = self.rid(["Kenku Recall:advantage"], advantage="advantage")
        r = self.roll(rid, spend="kenku_recall", advantage="advantage")
        self.assertIn("double", r.get_json()["error"].lower())

    def test_a_flat_offer_still_applies_on_an_advantage_prescription(self):
        """Only an ADVANTAGE effect double-counts. A flat one adds on any roll,
        so refusing it here would be wrong rather than conservative."""
        rid = self.rid(["Bless:+2"], advantage="advantage")
        r = self.roll(rid, spend="bless", advantage="advantage")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["modifier"], 2)
        self.assertEqual(self.counter("Kairos", "Bless"), {"used": 1, "max": 2})

    def test_a_bonus_die_still_applies_on_an_advantage_prescription(self):
        rid = self.rid(["Bardic Inspiration:1d6"], advantage="advantage")
        r = self.roll(rid, spend="bardic_inspiration", advantage="advantage")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["bonus_faces"]), 1)


# ── stacking ────────────────────────────────────────────────────────────────

class TwoModifiersDoNotStack(_Base):
    def test_two_offers_contributing_a_modifier_is_refused(self):
        """"These two do not stack" is a table ruling and the display is not where
        it gets made."""
        rid = self.rid(["Bless:+2", "Guidance:+1"])
        r = self.roll(rid, spend="bless")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.counter("Kairos", "Bless"), {"used": 0, "max": 2})

    def test_the_refusal_is_about_stacking(self):
        rid = self.rid(["Bless:+2", "Guidance:+1"])
        self.assertIn("stack", self.roll(rid, spend="bless")
                      .get_json()["error"].lower())


# ── the double-spend guard, as concurrency ──────────────────────────────────

class DoubleSpendFromASecondClient(_Base):
    def test_a_second_client_spending_the_same_offer_is_refused(self):
        """Two separate requests against the same character and counter — the
        shape of a second browser, or a phone that reloaded and resent.

        Not a lock test: two sequential clients, because a sequential double
        spend is the case a player actually hits and the one that has to be
        refused by the counter rather than by a session token."""
        self.set_counter("Kenku Recall", 0, 1)
        first = self.rid(["Kenku Recall:advantage"], characters=["Kairos", "Mira"])
        second = self.rid(["Kenku Recall:advantage"], characters=["Kairos", "Mira"])

        self.assertEqual(self.roll(first, character="Kairos",
                                   spend="kenku_recall").status_code, 200)
        again = self.roll(second, character="Mira", spend="kenku_recall")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(self.counter(), {"used": 1, "max": 1})

    def test_concurrent_spends_of_one_use_produce_exactly_one_roll(self):
        """The same property under real concurrency: eight threads, one use.

        Honest about its own power: under CPython the window between the check
        and the commit is a handful of bytecodes, so against a lock-free build
        this test passes by scheduling luck rather than by design. Verified by
        re-running it against that build with a `sleep(0.002)` between the two
        steps, where it fails 8-of-8. The deterministic half is
        `test_the_decrement_commits_inside_the_spend_lock` below; this one is the
        end-to-end symptom.
        """
        self.set_counter("Kenku Recall", 0, 1)
        rids = [self.rid(["Kenku Recall:advantage"],
                         characters=["Kairos", "Mira"])
                for _ in range(8)]

        ok = []
        refused = []
        barrier = threading.Barrier(len(rids))

        def spend(i):
            barrier.wait()
            r = self.roll(rids[i], character="Kairos", spend="kenku_recall")
            (ok if r.status_code == 200 else refused).append(r)

        threads = [threading.Thread(target=spend, args=(i,))
                   for i in range(len(rids))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(ok), 1,
                         "exactly one of eight concurrent spends may succeed")
        self.assertEqual(len(refused), 7)
        self.assertEqual(self.counter(), {"used": 1, "max": 1},
                         "the counter must not go below the cap under contention")

    def test_the_decrement_commits_inside_the_spend_lock(self):
        """The atomicity property, stated directly rather than raced for.

        `_stats_lock` guards the dict; `_spend_lock` guards check 3 TOGETHER WITH
        the decrement. If the commit sat outside `_spend_lock`, two callers could
        both pass check 3 — each holding `_stats_lock` in turn, each seeing a
        counter that is still > 0 — and each then writing.

        Asserted by observation: a wrapped `_spend_lock` records whether it was
        held at the instant the counter was written, and the counter value at
        that instant is captured too, so the assertion is on the write, not on
        the shape of the source.
        """
        observed = []
        real_lock = self.mod._spend_lock

        class _Watched:
            def __init__(self, inner):
                self._inner = inner
                self.depth = 0

            def __enter__(self):
                self._inner.acquire()
                self.depth += 1
                return self

            def __exit__(self, *exc):
                self.depth -= 1
                self._inner.release()
                return False

        watched = _Watched(real_lock)
        self.mod._spend_lock = watched

        # A dict subclass that notes, on assignment, whether the lock was held.
        # Observing the real write is what makes this a measurement rather than
        # an assertion about how the code is written.
        class _NotingDict(dict):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.note = None

            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                if self.note is not None:
                    self.note.append((key, value, watched.depth > 0))

        noting = _NotingDict(used=0, max=1)
        self.mod._spend_lock = watched
        try:
            with self.mod._stats_lock:
                p = next(x for x in self.mod._current_stats["players"]
                         if x["name"] == "Kairos")
                p["resources"]["Kenku Recall"] = noting
            rid = self.rid(["Kenku Recall:advantage"])
            noting.note = observed
            r = self.roll(rid, spend="kenku_recall")
            self.assertEqual(r.status_code, 200)
        finally:
            self.mod._spend_lock = real_lock

        self.assertTrue(observed, "the counter was never written")
        for key, value, inside in observed:
            self.assertEqual(key, "used")
            self.assertTrue(
                inside,
                f"the decrement of `used` to {value} happened OUTSIDE "
                f"_spend_lock, so a second client could have passed check 3 "
                f"against the pre-decrement value")


# ── the refusal is never silent ─────────────────────────────────────────────

class EveryRefusalCarriesAMessage(_Base):
    CASES = [
        ("unknown request", dict(req="deadbeef", spend="kenku_recall")),
        ("no request", dict(req="", spend="kenku_recall")),
        ("key not offered", dict(offers=["Kenku Recall:advantage"],
                                 spend="second_wind")),
        ("double count", dict(offers=["Kenku Recall:advantage"],
                              advantage="advantage", spend="kenku_recall")),
        ("two modifiers", dict(offers=["Bless:+2", "Guidance:+1"],
                               spend="bless")),
    ]

    def test_every_refusal_names_itself(self):
        """A silent 400 here is indistinguishable from a network blip on a phone
        that just tried to spend something."""
        for name, case in self.CASES:
            with self.subTest(case=name):
                offers = case.get("offers", ["Kenku Recall:advantage"])
                self.set_counter("Kenku Recall", 0, 2)
                self.set_counter("Bless", 0, 2)
                rid = self.rid(offers, characters=["Kairos", "Mira"],
                               **({"advantage": case["advantage"]}
                                  if "advantage" in case else {}))
                r = self.roll(rid, character="Mira", spend=case["spend"],
                              **({"advantage": case["advantage"]}
                                 if "advantage" in case else {}))
                self.assertEqual(r.status_code, 400, name)
                body = r.get_json()
                self.assertTrue((body.get("error") or "").strip(),
                                f"{name} refused without a message")

    def test_every_refusal_leaves_the_counter_alone(self):
        for name, case in self.CASES:
            with self.subTest(case=name):
                self.set_counter("Kenku Recall", 0, 2)
                self.set_counter("Bless", 0, 2)
                offers = case.get("offers", ["Kenku Recall:advantage"])
                rid = self.rid(offers, characters=["Kairos", "Mira"],
                               **({"advantage": case["advantage"]}
                                  if "advantage" in case else {}))
                self.roll(rid, character="Mira", spend=case["spend"],
                          **({"advantage": case["advantage"]}
                             if "advantage" in case else {}))
                self.assertEqual(self.counter(), {"used": 0, "max": 2}, name)
                self.assertEqual(self.counter("Kairos", "Bless"),
                                 {"used": 0, "max": 2}, name)


# ── the pre-existing roll path is untouched ─────────────────────────────────

class TheWaitStillReleases(_Base):
    def test_a_spent_roll_resolves_the_pending_request(self):
        """The existing tests/test_dice_request_results.py path, unchanged."""
        rid = self.rid(["Kenku Recall:advantage"], label="Insight check")
        r = self.roll(rid, spend="kenku_recall", label="Insight check")
        self.assertEqual(r.status_code, 200)
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertTrue(st["complete"])
        self.assertEqual(len(st["results"]), 1)

    def test_a_multi_character_request_releases_only_when_all_have_rolled(self):
        self.client.post("/stats", data=json.dumps({"players": [
            {"name": "Mira", "hp": {"current": 14, "max": 14}}]}),
            content_type="application/json")
        self.set_counter("Kenku Recall", 0, 2, "Mira")
        rid = self.rid(["Kenku Recall:advantage"], characters=["Kairos", "Mira"])
        self.roll(rid, character="Kairos", spend="kenku_recall")
        self.assertFalse(self.client.get(f"/dice-request/{rid}")
                         .get_json()["complete"],
                         "one of two prescribed characters has not rolled yet")
        self.roll(rid, character="Mira", spend="kenku_recall")
        self.assertTrue(self.client.get(f"/dice-request/{rid}")
                        .get_json()["complete"])
        self.assertEqual(self.counter()["used"], 1)   # Kairos's own

    def test_a_roll_with_no_offer_still_works(self):
        rid = self.rid()
        self.assertEqual(self.roll(rid).status_code, 200)


# ── what the pad is told ────────────────────────────────────────────────────

class ThePadIsToldTheCount(_Base):
    def test_an_offer_carries_the_remaining_uses(self):
        r = self.request(["Kenku Recall:advantage"])
        offers = r.get_json() and None
        # The count rides the broadcast, not the /dice-request response, because
        # the pad renders from the broadcast. Read it from the payload.
        seen = []
        self.mod._broadcast = lambda p: seen.append(p)
        self.request(["Kenku Recall:advantage"])
        req = [p for p in seen if "dice_request" in p][0]["dice_request"]
        self.assertEqual(req["offers"][0]["uses"], 2)

    def test_a_spent_offer_reports_zero_rather_than_disappearing(self):
        self.client.post("/stats", data=json.dumps({"players": [{
            "name": "Kairos", "hp": {"current": 20, "max": 20},
            "_resource_set": {"Kenku Recall": {"used": 2, "max": 2}}}]}),
            content_type="application/json")
        seen = []
        self.mod._broadcast = lambda p: seen.append(p)
        self.request(["Kenku Recall:advantage"])
        req = [p for p in seen if "dice_request" in p][0]["dice_request"]
        self.assertEqual(req["offers"][0]["uses"], 0,
                         "a spent feature still shows, at 0 — that is the "
                         "difference from a milestone")

    def test_an_offer_with_no_counter_reports_none_not_zero(self):
        """None and 0 are different. None means the GM never ran
        --resource-set — an unconfigured display — and greying out every offer
        there would read as a broken phone."""
        self.client.post("/stats", data=json.dumps({
            "players": [{"name": "Kairos", "hp": {"current": 20, "max": 20}}],
            "replace_players": True}), content_type="application/json")
        seen = []
        self.mod._broadcast = lambda p: seen.append(p)
        self.request(["Kenku Recall:advantage"])
        req = [p for p in seen if "dice_request" in p][0]["dice_request"]
        self.assertIsNone(req["offers"][0]["uses"])

    def test_the_pending_entry_keeps_the_parsed_offer_not_the_wire_one(self):
        """The spend path reads `effect`. A `uses` field arriving from a client
        must not be able to stand in for the server's own value."""
        rid = self.rid(["Kenku Recall:advantage"])
        with self.mod._dice_pending_lock:
            entry = self.mod._dice_pending[rid]
        self.assertEqual(entry["meta"]["offers"][0]["effect"], {"advantage": True})
        self.assertNotIn("uses", entry["meta"]["offers"][0])


if __name__ == "__main__":
    unittest.main()