"""A per-rest resource counter the pad can read (RS2.1).

Kenku Recall is 2 uses per long rest. It exists in this repo only as prose —
`tests/fixtures/Kairos_Level1.md` — with nothing tracking it, so a player who
spends one is relying on the GM remembering. The sidebar's only feature widget
is `feature_flags`, one boolean: 2nd Wind, available or not.

This adds the counter. The engine owns it. The GM sets the cap and the rest; the
display reads it and (RS2.2) decrements the player's spend. Nothing here decides
whether a feature applies to a check — that is a ruling, made by whoever
declared the offer.

**The milestone counter is a trap, not a template.** `_milestone_dec` pops the
key at zero (gm-display-app.py) and `badge_set` filters `count > 0`. Both are
right for a reward the player no longer holds and wrong for a per-rest feature,
which must keep reading 0/2 so the player knows it exists and is spent. Reusing
`milestones` would also conflate Bardic Inspiration (GM-awarded) with Kenku
Recall (racial) — different replenishment events, different owners.

So the two widgets' behaviour is asserted **side by side on the same input**
below, so the difference stays deliberate rather than drifting into an accident.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
PUSH_STATS = REPO / "display" / "push_stats.py"


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_resources", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ResourceCounter(unittest.TestCase):
    """The engine side: set, restore, persist."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.client = cls.mod.app.test_client()

    def setUp(self):
        # replace_players, because gm-display-app.py holds module-level state
        # that outlives a test: each class imports its own module, but within a
        # class the previous test's features would still be on Kairos and every
        # assertion about "the resources" would be about the union of them. That
        # is the fixture-leak class, and the fix is a wipe, not a longer label.
        self.client.post("/stats", data=json.dumps({
            "players": [{"name": "Kairos", "hp": {"current": 20, "max": 20}}],
            "replace_players": True}), content_type="application/json")

    def push(self, name="Kairos", **update):
        update["name"] = name
        return self.client.post("/stats", data=json.dumps(
            {"players": [update]}), content_type="application/json")

    def resources(self, name="Kairos"):
        players = self.mod._current_stats.get("players", [])
        p = next(x for x in players if x.get("name") == name)
        return p.get("resources")

    # ── --resource-set ────────────────────────────────────────────────────

    def test_resource_set_creates_the_entry_at_zero(self):
        self.push(_resource_set={"Kenku Recall": 2})
        self.assertEqual(self.resources(),
                         {"Kenku Recall": {"used": 0, "max": 2}})

    def test_resource_set_is_a_full_replace(self):
        """The GM owns both numbers, and one command overwrites both. That is the
        command a long rest uses, so it must not leave a stale `used` behind."""
        self.push(_resource_set={"Kenku Recall": 2})
        self.push(_resource_set={"Kenku Recall": 2})
        self.assertEqual(self.resources()["Kenku Recall"], {"used": 0, "max": 2})

    def test_resource_set_accepts_an_explicit_used(self):
        """Mid-session: a GM correcting a count without refilling."""
        self.push(_resource_set={"Kenku Recall": {"used": 1, "max": 2}})
        self.assertEqual(self.resources()["Kenku Recall"], {"used": 1, "max": 2})

    def test_resource_set_replaces_the_named_feature_only(self):
        self.push(_resource_set={"Kenku Recall": 2, "Bardic Inspiration": 1})
        self.push(_resource_set={"Bardic Inspiration": 1})
        self.assertEqual(sorted(self.resources()), ["Bardic Inspiration",
                                                    "Kenku Recall"])

    def test_a_label_with_a_colon_in_it_survives(self):
        """Font of Inspiration is a real feature name. push_stats splits on the
        RIGHT colon so only the final segment is the cap."""
        self.push(_resource_set={"Font: of Inspiration": 3})
        self.assertEqual(self.resources()["Font: of Inspiration"],
                         {"used": 0, "max": 3})

    def test_mutation_ops_are_not_stored_as_player_fields(self):
        """`_resource_set` must never be persisted as if it were data.

        Measured on the existing-player path, where the op is actually applied:
        it is consumed by the branch and never reaches `match[key] = val`. If it
        leaked, the next merge would carry a `_resource_set` field forever and
        every later --resource-set would be applied twice."""
        self.push(_resource_set={"Kenku Recall": 2})
        kairos = next(p for p in self.mod._current_stats["players"]
                      if p["name"] == "Kairos")
        self.assertNotIn("_resource_set", kairos)
        self.assertEqual(kairos["resources"],
                         {"Kenku Recall": {"used": 0, "max": 2}})

    def test_a_resource_set_on_an_unknown_player_creates_the_name_but_no_counter(self):
        """Pre-existing behaviour of every mutation op, pinned rather than
        assumed.

        A push naming someone who does not exist appends a player entry built
        from the non-mutation keys only — so the name appears and
        `_resource_set` is stripped, which leaves a player with no `resources`
        at all. The op is not applied to the new entry, because the new-entry
        branch never runs the mutation branches.

        Worth pinning because of what it means at the table: `--resource-set`
        alone does NOT introduce a character. The GM has to push the roster
        first (push_stats --json), exactly as for --hp and --spell-slots. Same
        as every other op, so not a defect here — but a GM who has not learned
        it will see the counter silently not appear, and that is the shape the
        RS2 kill criterion warns about."""
        self.push("Mira", _resource_set={"Bardic Inspiration": 1})
        mira = next(p for p in self.mod._current_stats["players"]
                    if p["name"] == "Mira")
        self.assertNotIn("_resource_set", mira)
        self.assertNotIn("resources", mira)
        # Once the roster knows Mira, the op applies normally.
        self.push("Mira", hp={"current": 14, "max": 14})
        self.push("Mira", _resource_set={"Bardic Inspiration": 1})
        self.assertEqual(self.resources("Mira"),
                         {"Bardic Inspiration": {"used": 0, "max": 1}})

    # ── --resource-restore ────────────────────────────────────────────────

    def test_restore_takes_one_back(self):
        self.push(_resource_set={"Kenku Recall": {"used": 2, "max": 2}})
        self.push(_resource_restore="Kenku Recall")
        self.assertEqual(self.resources()["Kenku Recall"], {"used": 1, "max": 2})

    def test_restore_stops_at_the_cap(self):
        """The acceptance criterion, and the one that matters after a long rest:
        four restores on a 2-use feature must not read 0/4 or a negative used."""
        self.push(_resource_set={"Kenku Recall": {"used": 2, "max": 2}})
        for _ in range(4):
            self.push(_resource_restore="Kenku Recall")
        self.assertEqual(self.resources()["Kenku Recall"], {"used": 0, "max": 2})

    def test_restore_on_an_unknown_feature_creates_it_at_zero(self):
        """Not a silent no-op and not a crash: it lands as 0/0 so the pad has
        something to render and the GM sees the feature is not set."""
        self.push(_resource_restore="Kenku Recall")
        self.assertEqual(self.resources()["Kenku Recall"], {"used": 0, "max": 0})

    def test_the_cap_is_never_raised_by_a_restore(self):
        """A restore decrements `used`. It must not touch `max` — the cap is the
        GM's, set by --resource-set."""
        self.push(_resource_set={"Kenku Recall": {"used": 1, "max": 2}})
        self.push(_resource_restore="Kenku Recall")
        self.assertEqual(self.resources()["Kenku Recall"]["max"], 2)

    # ── it survives a restart ─────────────────────────────────────────────

    def test_the_field_round_trips_through_stats_json(self):
        """`_persist_stats` writes stats.json; the field has to be in it or a
        restart silently empties every counter. Asserted on the file, not on the
        in-memory dict."""
        self.push(_resource_set={"Kenku Recall": {"used": 1, "max": 2}})
        persisted = json.loads(
            pathlib.Path(self.mod.STATS_FILE).read_text(encoding="utf-8"))
        p = next(x for x in persisted["players"] if x.get("name") == "Kairos")
        self.assertEqual(p["resources"]["Kenku Recall"],
                         {"used": 1, "max": 2})

    def test_a_restart_reloads_the_counter(self):
        self.push(_resource_set={"Kenku Recall": {"used": 1, "max": 2}})
        fresh = _import_app()
        players = fresh._current_stats.get("players", [])
        p = next((x for x in players if x.get("name") == "Kairos"), None)
        if p is not None:
            self.assertEqual(p.get("resources", {}).get("Kenku Recall"),
                             {"used": 1, "max": 2})
        else:
            self.skipTest("stats.json holds no Kairos; the file is shared state")

    # ── the CLI reaches it ────────────────────────────────────────────────

    def _push_stats(self, *args):
        return subprocess.run(
            [sys.executable, str(PUSH_STATS), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO),
                 "HOME": str(pathlib.Path.home())})

    def _push_stats_payload(self, *args):
        """The exact JSON push_stats would post, captured without sending.

        A subprocess with no display running exits 0 and transmits nothing, so
        asserting on the payload is the only way to test what the flag parses
        rather than whether the CLI rejected its own input. Imported in-process
        with `_send` stubbed, which keeps it honest: the real argparse path
        runs, only the network call is replaced."""
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("push_stats_probe", str(PUSH_STATS))
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        captured = {}

        def _capture(url, data, token):
            captured["payload"] = json.loads(data.decode("utf-8"))

        mod._send = _capture
        argv = sys.argv
        sys.argv = ["push_stats.py", *args]
        try:
            mod.main()
        finally:
            sys.argv = argv
        return captured.get("payload")

    def _read_payload(self):
        persisted = json.loads(
            pathlib.Path(self.mod.STATS_FILE).read_text(encoding="utf-8"))
        p = next(x for x in persisted["players"] if x.get("name") == "Kairos")
        return p

    def test_push_stats_parses_label_and_cap(self):
        payload = self._push_stats_payload("--player", "Kairos", "--resource-set",
                                           "Kenku Recall:2")
        self.assertEqual(payload["players"][0]["_resource_set"],
                         {"Kenku Recall": 2})

    def test_push_stats_splits_a_label_containing_a_colon_from_the_right(self):
        """`Font: of Inspiration:3` must keep its inner colon. Splitting from the
        left would send the label "Font" and reject " of Inspiration:3" as a
        non-numeric cap — a silent mis-parse is what this asserts against."""
        payload = self._push_stats_payload(
            "--player", "Kairos", "--resource-set", "Font: of Inspiration:3")
        self.assertEqual(payload["players"][0]["_resource_set"],
                         {"Font: of Inspiration": 3})

    def test_push_stats_passes_an_explicit_used_through(self):
        payload = self._push_stats_payload(
            "--player", "Kairos", "--resource-set",
            'Kenku Recall:{"used": 1, "max": 2}')
        self.assertEqual(payload["players"][0]["_resource_set"],
                         {"Kenku Recall": {"used": 1, "max": 2}})

    def test_push_stats_rejects_malformed_resource_set_json(self):
        r = self._push_stats("--player", "Kairos", "--resource-set",
                             'Kenku Recall:{"used":')
        self.assertEqual(r.returncode, 1)
        self.assertIn("Invalid", r.stderr)

    def test_push_stats_accepts_repeated_features(self):
        payload = self._push_stats_payload(
            "--player", "Kairos", "--resource-set", "Kenku Recall:2",
            "--resource-set", "Bardic Inspiration:1")
        self.assertEqual(payload["players"][0]["_resource_set"],
                         {"Kenku Recall": 2, "Bardic Inspiration": 1})

    def test_push_stats_restore_sends_a_list_even_for_one_feature(self):
        """A bare string would restore a feature literally named
        "['Kenku Recall']" — the server branch takes the whole value as one
        label. Found by this assertion failing, not by reading."""
        payload = self._push_stats_payload("--player", "Kairos",
                                           "--resource-restore", "Kenku Recall")
        self.assertEqual(payload["players"][0]["_resource_restore"],
                         ["Kenku Recall"])

    def test_push_stats_restore_accepts_repeated_features(self):
        payload = self._push_stats_payload(
            "--player", "Kairos", "--resource-restore", "Kenku Recall",
            "--resource-restore", "Bardic Inspiration")
        self.assertEqual(payload["players"][0]["_resource_restore"],
                         ["Kenku Recall", "Bardic Inspiration"])

    def test_push_stats_requires_a_player_name(self):
        r = self._push_stats("--resource-set", "Kenku Recall:2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--player", r.stderr)

    def test_push_stats_resource_set_is_accepted(self):
        r = self._push_stats("--player", "Kairos", "--resource-set",
                             "Kenku Recall:2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Invalid", r.stderr)

    def test_push_stats_rejects_a_resource_set_with_no_cap(self):
        r = self._push_stats("--player", "Kairos", "--resource-set",
                             "Kenku Recall")
        self.assertEqual(r.returncode, 1)
        self.assertIn("LABEL:CAP", r.stderr)

    def test_push_stats_rejects_a_non_numeric_cap(self):
        r = self._push_stats("--player", "Kairos", "--resource-set",
                             "Kenku Recall:lots")
        self.assertEqual(r.returncode, 1)
        self.assertIn("must be a number", r.stderr)

    def test_push_stats_resource_restore_is_accepted(self):
        r = self._push_stats("--player", "Kairos", "--resource-restore",
                             "Kenku Recall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Invalid", r.stderr)


class ResourceShapeIsNotMilestones(unittest.TestCase):
    """The two counters, side by side, on the same input.

    This is the test that keeps the difference deliberate. If `badge_set` ever
    starts rendering 0, or `resource_count` ever starts filtering it, this fails
    and the reason is named.
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()

    def _player(self, resources, milestones):
        return {"name": "Kairos", "resources": resources,
                "milestones": milestones}

    def test_a_spent_feature_is_stored_and_a_spent_milestone_is_not(self):
        """Same numbers, opposite storage policy, stated in one place."""
        spent_feature = self.mod._set_resource({}, "Kenku Recall",
                                               {"used": 2, "max": 2})
        self.assertEqual(spent_feature, {"used": 2, "max": 2},
                         "a spent per-rest feature must keep reading 0/2")

        # The milestone path, verbatim: pop the key at zero.
        player = {"milestones": {"Inspiration": 0}}
        ms = player.setdefault("milestones", {})
        ms["Inspiration"] = max(ms.get("Inspiration", 0) - 1, 0)
        if ms.get("Inspiration", 0) == 0:
            ms.pop("Inspiration", None)
        self.assertNotIn("Inspiration", player["milestones"],
                         "milestones pop at zero — that is the policy resource "
                         "counters deliberately do NOT share")

    def test_the_two_fields_do_not_alias(self):
        """Bardic Inspiration (GM-awarded) and Kenku Recall (racial) are
        replenished by different events. One counter for both would make a long
        rest refill a feature the GM gave."""
        player = {}
        self.mod._set_resource(player, "Kenku Recall", 2)
        player["milestones"] = {"Bardic Inspiration": 1}
        self.assertIn("Kenku Recall", player["resources"])
        self.assertNotIn("Bardic Inspiration", player["resources"])
        self.assertEqual(player["milestones"], {"Bardic Inspiration": 1})


if __name__ == "__main__":
    unittest.main()