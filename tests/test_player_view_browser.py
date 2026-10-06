"""#118 — browser coverage for `?view=player`.

The static tests in this file's sibling (`test_player_view.py`) prove the policy
is complete and internally consistent. They cannot prove the policy is
*applied*. This half measures the real page in a real browser, and it reads the
policy out of the running module (`window.GMPlayerView`) rather than restating
it in Python, so a surface added to OPERATOR_SURFACES is checked here the
moment it is added — no second list to forget.

#118's criterion: "Browser coverage verifies both presentations and absence of
interactive command controls in player mode."
"""
from __future__ import annotations

import sys
import unittest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from _browser import BrowserTestCase  # noqa: E402

# #118's words, transcribed independently of the policy module.
AC_NAMED = ("sent-log", "tx-log", "tx-actions", "tx-cover",
            "device-approvals", "cp-status", "controls-toggle")


class PlayerViewBrowserTest(BrowserTestCase):
    """Both presentations, measured against the real Flask app."""

    server_kind = "flask"
    module_name = "gm_display_app_player_view"

    # ── helper: ask the page what the policy decided ───────────────────────

    def _state(self, page) -> dict:
        """Snapshot the page's view state.

        Everything crosses the evaluate boundary as JSON, so visibility is
        resolved to a plain {id: state} map HERE rather than returned as a
        function — a JS closure serialises to None and every assertion against
        it then fails with a TypeError that reads like a policy bug.
        """
        return page.evaluate("""() => {
            const P = window.GMPlayerView;
            const vis = {};
            const ids = P
                ? Array.from(P.OPERATOR_SURFACES).concat(Array.from(P.PLAYER_SURFACES))
                : [];
            for (const id of ids) {
                const el = document.getElementById(id);
                vis[id] = !el ? 'absent'
                    : (el.offsetParent !== null || getComputedStyle(el).display !== 'none')
                      ? 'visible' : 'hidden';
            }
            return {
                policyLoaded: !!P,
                playerClass: document.body.classList.contains('player-view'),
                operator: P ? Array.from(P.OPERATOR_SURFACES) : [],
                player: P ? Array.from(P.PLAYER_SURFACES) : [],
                vis,
            };
        }""")

    # ── both presentations ─────────────────────────────────────────────────

    def test_ordinary_operator_display_is_unaffected(self):
        """"ordinary operator behavior is retained" (#118).

        The mode is inert without the query parameter: no class, and — the part
        that actually matters — no `display:none` planted on anything, so a GM
        who never types ?view=player gets the same screen they had.
        """
        page = self.open_page(path="/")
        st = self._state(page)
        self.assertTrue(st["policyLoaded"], "the policy module never loaded")
        self.assertFalse(st["playerClass"],
                         "?view absent but the page is in player mode")
        hidden = page.evaluate(
            """() => Array.from(document.querySelectorAll('[data-pv="hidden"]'))
                       .map(e => e.id)""")
        self.assertEqual(hidden, [],
                         "player-view suppressed surfaces on the ORDINARY display")

    def test_player_view_activates_on_the_query_parameter(self):
        page = self.open_page(path="/?view=player")
        st = self._state(page)
        self.assertTrue(st["policyLoaded"])
        self.assertTrue(st["playerClass"], "?view=player did not activate the mode")

    # ── the AC: no interactive command controls in player mode ──────────────

    def test_every_live_dom_surface_is_classified_by_the_policy(self):
        """Cross-check the LIVE DOM against the policy, in the browser.

        This is the check that finds a policy OMISSION, and it had to be built
        deliberately. Two earlier versions of the companion test iterated
        `window.GMPlayerView.OPERATOR_SURFACES` — and both passed against a
        policy with `world-clock` and the `wc-*` ids deleted from it, because
        deleting an entry from the set also removes it from the loop. A test
        that reads the thing-under-test's own list cannot detect that list
        losing an entry. So the iteration starts from the DOM instead: every
        element with an id must be classified by one list or the other.

        (The static `test_no_surface_is_unclassified` catches omissions from
        the source. This one catches them from the rendered page, which also
        covers ids injected at runtime.)
        """
        page = self.open_page(path="/?view=player")
        unclassified = page.evaluate("""() => {
            const P = window.GMPlayerView;
            if (!P) return ['<policy module never loaded>'];
            return Array.from(document.querySelectorAll('[id]'))
                .map(e => e.id)
                .filter(id => !P.PLAYER_SURFACES.has(id) && !P.OPERATOR_SURFACES.has(id));
        }""")
        self.assertEqual(
            unclassified, [],
            "live DOM contains surfaces the policy does not classify: "
            f"{unclassified}")

    def test_ac_named_controls_are_suppressed_by_an_independent_list(self):
        """#118's own wording, asserted from a list written here rather than
        read from the module.

        Two independent lists for the same fact is not duplication for its own
        sake: the policy-driven tests can only catch surfaces the policy knows
        about, so the criterion #118 actually wrote has to be pinned by
        something that does not consult the policy at all.
        """
        page = self.open_page(path="/?view=player")
        survivors = page.evaluate("""(ids) => ids.filter(id => {
            const el = document.getElementById(id);
            if (!el) return false;
            const shown = el.offsetParent !== null
                || getComputedStyle(el).display !== 'none';
            return shown;
        })""", list(AC_NAMED))
        self.assertEqual(survivors, [],
                         "#118 names these as suppressed; they are visible: "
                         f"{survivors}")

    def test_no_operator_surface_is_visible_in_player_mode(self):
        """The criterion, checked against every entry the policy denies.

        Iterating the runtime policy rather than a Python copy is deliberate: a
        surface added to OPERATOR_SURFACES is asserted here immediately, so the
        deny list cannot grow without the browser agreeing it works.
        """
        page = self.open_page(path="/?view=player")
        self.assertTrue(self._state(page)["policyLoaded"])
        # Evaluated inside the page: one round trip, and the visibility check
        # runs against the live layout rather than a Python re-implementation
        # of "is this element shown", which is the part worth not re-inventing.
        # Assert on the POLICY'S DECISION (data-pv), not on final visibility.
        #
        # This distinction was measured, not reasoned about. A visibility-only
        # version of this test was run against a policy with `world-clock` and
        # the `wc-*` ids deleted from OPERATOR_SURFACES, and it PASSED — because
        # display.css's `body.player-view` rules hid those elements anyway. The
        # CSS is a second, redundant line of defence, and it made a broken
        # policy look like a working one. data-pv is what the policy itself
        # decided; if that is wrong, no amount of CSS should hide the bug.
        undecided = page.evaluate("""() => {
            const P = window.GMPlayerView;
            const bad = [];
            for (const id of P.OPERATOR_SURFACES) {
                const el = document.getElementById(id);
                if (!el) continue;                 // not built in this state
                if (el.dataset.pv !== 'hidden') bad.push(id);
            }
            return bad;
        }""")
        self.assertEqual(
            undecided, [],
            "the policy did not suppress these operator surfaces: "
            f"{undecided}")

        # And, separately, the end-to-end property #118 actually asks for: none
        # of them is visible. Belt and braces, deliberately a second assertion.
        leaked = page.evaluate("""() => {
            const P = window.GMPlayerView;
            const bad = [];
            for (const id of P.OPERATOR_SURFACES) {
                const el = document.getElementById(id);
                if (!el) continue;
                const shown = el.offsetParent !== null
                    || getComputedStyle(el).display !== 'none';
                if (shown) bad.push(id);
            }
            return bad;
        }""")
        self.assertEqual(
            leaked, [],
            "GM command controls visible on the player screen: "
            f"{leaked}. #118 requires their absence.")

    def test_the_player_screen_still_shows_the_table(self):
        """A screen with no GM tools is only correct if it still shows the
        table. Narration is the reason the mode exists, and #118 asks for "the
        map and intended player-facing state".
        """
        page = self.open_page(path="/?view=player")
        st = self._state(page)
        self.assertEqual(st["vis"]["text-scroll"], "visible",
                         "narration column is hidden on the player screen")
        # The battle map only exists once a fight is loaded, so it cannot be
        # asserted on a default page. Assert the policy reserves it instead:
        # #118 asks for "the map and intended player-facing state", and a map
        # classified as operator furniture would be hidden for every player.
        self.assertIn("tx-board", st["player"],
                      "the battle map is not player-facing in the policy")
        self.assertIn("tx-panel", st["player"])
        self.assertIn("tx-info", st["player"])

    def test_tx_info_is_not_suppressed_in_player_mode(self):
        """#tx-info carries the death-save prompt.

        If the policy ever classified it as operator furniture, a player at the
        table could not see that their character was dying — and the test that
        notices would be this one, not a playtest.
        """
        page = self.open_page(path="/?view=player")
        marked = page.evaluate(
            """() => {
                const el = document.getElementById('tx-info');
                return el ? el.dataset.pv || 'unmarked' : 'absent';
            }""")
        self.assertNotEqual(marked, "hidden",
                            "tx-info is suppressed; it carries the death-save prompt")

    def test_the_tactical_toolbar_is_suppressed_once_tactics_builds_it(self):
        """The regression that a load-time-only sweep would miss.

        tactics.js is `defer`red, so #tx-panel and its controls do not exist
        when player-view.js boots. This asserts on the live module by building
        the panel the way tactics.js does — after the policy has already swept.
        """
        page = self.open_page(path="/?view=player")
        result = page.evaluate("""() => {
            // Stand in for the deferred tactics.js build: same ids, same
            // container, appended the same way (document.body).
            const p = document.createElement('section');
            p.id = 'tx-panel';
            p.innerHTML =
                '<button class="tx-btn" id="tx-cover" type="button">Cover</button>' +
                '<div id="tx-actions" role="toolbar"></div>' +
                '<ol id="tx-log"></ol>' +
                '<div id="tx-info" class="tx-info"></div>';
            document.body.appendChild(p);
            const seen = {};
            for (const id of ['tx-cover', 'tx-actions', 'tx-log', 'tx-info']) {
                const el = document.getElementById(id);
                seen[id] = el.dataset.pv || 'unmarked';
            }
            return seen;
        }""")
        self.assertEqual(result["tx-cover"], "hidden",
                         "the cover toggle survived a late DOM build")
        self.assertEqual(result["tx-actions"], "hidden",
                         "tactical action controls survived a late DOM build")
        self.assertEqual(result["tx-log"], "hidden",
                         "the combat log survived a late DOM build")
        self.assertEqual(result["tx-info"], "unmarked",
                         "tx-info was suppressed by a late build it should not be caught by")

    def test_deactivating_restores_the_operator_display(self):
        """Leaving player view must undo exactly what it did — not blanket-clear
        `display`, which would destroy whatever a stylesheet had legitimately
        set on a surface."""
        page = self.open_page(path="/?view=player")
        page.evaluate("() => window.GMPlayerView.deactivate()")
        leftover = page.evaluate(
            """() => ({
                 cls: document.body.classList.contains('player-view'),
                 marked: document.querySelectorAll('[data-pv="hidden"]').length,
             })""")
        self.assertFalse(leftover["cls"], "player-view class survived deactivate()")
        self.assertEqual(leftover["marked"], 0,
                         "data-pv markers survived deactivate()")


if __name__ == "__main__":
    unittest.main()