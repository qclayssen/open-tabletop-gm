"""The two sidebar counters must differ, and differ deliberately (RS2.1).

`badge_set` filters `count > 0`: a milestone the player no longer holds should
leave no row. `resource_count` does not filter: a per-rest feature at 0/2 must
still show, because the player who just failed a check they had a resource for
needs to see the feature exists and is spent, not have it vanish.

That difference is the whole point of this slice, and it is invisible to a
server-side test. So it is measured in a real browser, on the **same input**,
with the two widgets rendered by the same manifest.

Measured rather than grepped on purpose: an assertion about display.js's text
would pass with `_wResourceCount` deleted entirely, which is decoration by
definition.
"""
import json
import pathlib

from tests._browser import BrowserTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent


class SidebarCountersDiffer(BrowserTestCase):
    module_name = "gm_display_app_resource_widget"

    # Pushed over the real /stats route, which is the only way stats enter the
    # display. No test-only hook: a hook that exists only for a test is a second
    # way in, and this measurement is about what the display does with a stats
    # payload, not about how the payload got there.
    PLAYERS = [{
        "name": "Kairos", "hp": {"current": 20, "max": 20},
        # The same numbers in both fields: a per-rest feature at 0/3 and a
        # milestone at 0, so the two policies are visible on one sidebar.
        "resources": {"Kenku Recall": {"used": 2, "max": 2},
                      "Bardic Inspiration": {"used": 0, "max": 3}},
        "milestones": {"Inspiration": 2, "Bennie": 0},
    }]

    def sidebar(self, players=None):
        self.clear_display_state()
        self.server.app.test_client().post("/stats", data=json.dumps(
            {"players": players if players is not None else self.PLAYERS,
             "replace_players": True}), content_type="application/json")
        page = self.open_page(path="/?view=dm")
        return page

    def sidebar_with_counters(self):
        """The page, waited until the counters are actually rendered.

        Separate from `sidebar()` because the self-hiding case has nothing to
        wait FOR: a player with no resources correctly renders no
        `[data-role="resources"]`, so waiting on it would time out on the
        behaviour under test. That distinction is the reason these are two
        helpers rather than one with a flag.
        """
        page = self.sidebar()
        page.wait_for_function(
            "() => document.querySelector('[data-role=\"resources\"]') !== null",
            timeout=10000)
        page.wait_for_timeout(150)
        return page

    def rows(self, page):
        return page.evaluate("""() => {
            const read = role => {
              const out = {};
              document.querySelectorAll(
                `[data-role="${role}"] .sb-milestone-row`).forEach(r => {
                out[r.querySelector('.sb-milestone-label').textContent] =
                  r.querySelector('.sb-milestone-count').textContent;
              });
              return out;
            };
            return { resources: read('resources'), milestones: read('milestones') };
        }""")

    def test_a_spent_feature_still_renders_at_zero(self):
        """The difference, stated as a failure. Kenku Recall at used=2/max=2
        reads 2/2; Bardic at 0/3 reads 0/3 and must be VISIBLE."""
        page = self.sidebar_with_counters()
        rows = self.rows(page)["resources"]
        self.assertEqual(rows.get("Bardic Inspiration"), "0/3",
                         "a spent per-rest feature must still render")
        self.assertEqual(rows.get("Kenku Recall"), "2/2")

    def test_a_spent_milestone_does_not_render(self):
        """The other half. Same widget classes, opposite policy. If this ever
        starts rendering "0", the two widgets have become one and the difference
        the handoff names has been lost."""
        page = self.sidebar_with_counters()
        rows = self.rows(page)["milestones"]
        self.assertEqual(rows.get("Inspiration"), "2")
        self.assertNotIn("Bennie", rows,
                         "badge_set filters count > 0, and must keep doing so")

    def test_both_widgets_share_the_same_classes(self):
        """Styling is inherited, not duplicated: a parallel class set would be a
        parallel set of light-theme rules to keep in step."""
        page = self.sidebar_with_counters()
        classes = page.evaluate("""() => {
            const c = sel => {
              const el = document.querySelector(sel);
              return el ? [el.className,
                           el.querySelector('.sb-milestone-row') &&
                             el.querySelector('.sb-milestone-row').className,
                           el.querySelector('.sb-milestone-count') &&
                             el.querySelector('.sb-milestone-count').className] : null;
            };
            return { resources: c('[data-role="resources"]'),
                     milestones: c('[data-role="milestones"]') };
        }""")
        self.assertIsNotNone(classes["resources"])
        self.assertEqual(classes["resources"], classes["milestones"],
                         "resource_count must reuse badge_set's classes exactly")

    def test_a_player_with_no_resources_shows_no_row(self):
        """The manifest's self-hiding contract: a campaign with no per-rest
        features must not grow an empty block."""
        page = self.sidebar([{"name": "Mira", "hp": {"current": 14, "max": 14}}])
        self.assertEqual(page.evaluate(
            "() => document.querySelectorAll('[data-role=\"resources\"]')"
            ".length"), 0)


class ManifestCarriesTheWidget(BrowserTestCase):
    """`ui.json` and the built-in default must stay byte-identical.

    The reference manifest's own `_comment` requires it, and a display that
    renders differently depending on whether a campaign shipped a ui.json is a
    display nobody can debug from a screenshot."""

    module_name = "gm_display_app_resource_manifest"

    def ui_json(self):
        return json.loads((REPO / "systems" / "dnd5e" / "ui.json")
                          .read_text(encoding="utf-8"))

    def default_manifest_block(self):
        js = (REPO / "display" / "static" / "display.js").read_text(
            encoding="utf-8")
        start = js.index("const DEFAULT_UI_MANIFEST")
        return js[start:js.index("};", start)]

    def test_the_reference_manifest_has_the_widget(self):
        types = [w.get("type") for w in self.ui_json()["sidebar"]]
        self.assertIn("resource_count", types)

    def test_the_widget_binds_to_resources(self):
        entry = next(w for w in self.ui_json()["sidebar"]
                     if w.get("type") == "resource_count")
        self.assertEqual(entry["bind"], "resources")

    def test_the_two_manifests_agree_on_the_widget_list(self):
        block = self.default_manifest_block()
        for widget_type in (w.get("type") for w in self.ui_json()["sidebar"]):
            self.assertIn(f"type: '{widget_type}'", block,
                          f"{widget_type} is in ui.json but not in the built-in "
                          f"default, so the display would differ by campaign")


if __name__ == "__main__":
    import unittest
    unittest.main()