"""N1/P4 and N3: stale display state must not leak from one campaign to the next.

- POST /clear used to wipe only the text log and stats, leaving sent actions and
  the queued-input files behind, so one campaign's input surfaced in the next.
- Registering a *different* campaign through POST /chunk {"campaign": ...} must
  trigger the same wipe automatically, with no manual /clear call.
- Re-registering the *same* campaign name must NOT wipe anything: that is a
  play.py restart re-announcing itself, not a fresh load.
- _current_scene_name was never reset, so a clear kept the previous campaign's
  scene ("dungeon") and the title/background were wrong until enough new
  narration re-triggered detection.

Ported from the stranded fix-report-b4-n-p4 branch, rewritten for the Send flow
that replaced Stage/Ready: the branch cleared _staged, which no longer exists.
"""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

from tests.display_sources import read_display_sources

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app(tmp_dir: pathlib.Path):
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_clear", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Redirect every on-disk path this test touches into a throwaway directory,
    # so it never reads or writes the real display/ files.
    mod.LOG_FILE = str(tmp_dir / "text_log.json")
    mod.STATS_FILE = str(tmp_dir / "stats.json")
    mod.CAMP_FILE = str(tmp_dir / ".campaign")
    mod.QUEUE_FILE = str(tmp_dir / ".input_queue")
    mod.TRIGGER_FILE = str(tmp_dir / ".input_trigger")
    return mod


class ClearBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mod = _import_app(pathlib.Path(self._tmp.name))
        self.mod._token_ok = lambda: True
        self.sent = []
        self.mod._broadcast = self.sent.append
        self.client = self.mod.app.test_client()

    def tearDown(self):
        self._tmp.cleanup()

    def _dirty_everything(self):
        """Put state in every place a campaign switch has to wipe."""
        with self.mod._sent_lock:
            self.mod._sent["Piper"] = {"text": "I search the room", "timestamp": 0}
        with self.mod._input_lock:
            self.mod._input_queue.append({"character": "Piper", "text": "queued"})
        with self.mod._stats_lock:
            self.mod._current_stats["turn_order"] = ["Piper", "Goblin"]
        with self.mod._queue_status_lock:
            self.mod._queue_status.append("Piper")
        self.mod._current_scene_name = "dungeon"
        for path in (self.mod.LOG_FILE, self.mod.STATS_FILE,
                     self.mod.QUEUE_FILE, self.mod.TRIGGER_FILE):
            pathlib.Path(path).write_text("{}", encoding="utf-8")

    def _assert_wiped(self, *, stats=True):
        self.assertEqual(self.mod._sent, {})
        self.assertEqual(self.mod._input_queue, [])
        self.assertEqual(self.mod._queue_status, [])
        # No stale campaign data. system_version is exempt on the /chunk path: the
        # route resolves the new campaign's system version and writes it straight
        # back after the clear, which is correct — that is current, not stale.
        self.assertEqual({k: v for k, v in self.mod._current_stats.items()
                          if k != "system_version"}, {})
        self.assertEqual(self.mod._current_scene_name, self.mod._DEFAULT_SCENE)
        paths = (self.mod.LOG_FILE, self.mod.QUEUE_FILE, self.mod.TRIGGER_FILE)
        if stats:
            paths += (self.mod._get_stats_file(),)
        for path in paths:
            self.assertFalse(pathlib.Path(path).exists(), f"{path} survived the clear")


class ClearWipesSentAndQueuedInput(ClearBase):
    def test_clear_wipes_sent_actions_and_the_queue_files(self):
        self._dirty_everything()
        r = self.client.post("/clear")
        self.assertEqual(r.status_code, 204)
        self._assert_wiped()

    def test_clear_broadcasts_the_reset_so_open_browsers_see_it(self):
        """A new connection reads the globals fresh on /stream, but a phone already
        holding the page only learns about the reset from the broadcast."""
        self.client.post("/clear")
        payload = self.sent[-1]
        self.assertTrue(payload["clear"])
        self.assertEqual(payload["sent_log"], {})
        self.assertEqual(payload["queue_status"], [])
        self.assertEqual(payload["pending_input"], [])

    def test_clear_resets_a_stale_scene_name(self):
        """N3: 'dungeon' from the last campaign must not survive into the next."""
        self._dirty_everything()
        self.assertEqual(self.mod._current_scene_name, "dungeon")
        self.client.post("/clear")
        self.assertEqual(self.mod._current_scene_name, self.mod._DEFAULT_SCENE)
        self.assertEqual(self.sent[-1]["scene"]["name"], self.mod._DEFAULT_SCENE)

    def test_clear_still_requires_the_token(self):
        self.mod._token_ok = lambda: False
        self.assertEqual(self.client.post("/clear").status_code, 403)


class CampaignSwitchClearsAutomatically(ClearBase):
    def _chunk_campaign(self, name):
        return self.client.post("/chunk", json={"campaign": name})

    def test_switching_campaigns_wipes_state_with_no_manual_clear(self):
        pathlib.Path(self.mod.CAMP_FILE).write_text("ember-hollow", encoding="utf-8")
        self._dirty_everything()
        legacy_stats = pathlib.Path(self.mod.STATS_FILE).read_text(encoding="utf-8")
        r = self._chunk_campaign("strixhaven-kairos")
        self.assertEqual(r.status_code, 204)
        self._assert_wiped(stats=False)
        self.assertEqual(pathlib.Path(self.mod.STATS_FILE).read_text(encoding="utf-8"),
                         legacy_stats, "legacy shared stats must remain as a safety copy")
        self.assertEqual(pathlib.Path(self.mod.CAMP_FILE).read_text(encoding="utf-8").strip(),
                         "strixhaven-kairos")

    def test_reregistering_the_same_campaign_keeps_state(self):
        """A play.py restart re-announces the same campaign. That is a resume, not a
        fresh load — wiping here would throw away a live session's input."""
        pathlib.Path(self.mod.CAMP_FILE).write_text("ember-hollow", encoding="utf-8")
        self._dirty_everything()
        self._chunk_campaign("ember-hollow")
        self.assertIn("Piper", self.mod._sent)
        self.assertTrue(pathlib.Path(self.mod.QUEUE_FILE).exists())

    def test_a_first_registration_counts_as_a_switch(self):
        """No .campaign on file yet: prev is '' and any name differs, so the initial
        registration clears leftovers from a previous process."""
        self._dirty_everything()
        self._chunk_campaign("strixhaven-kairos")
        self._assert_wiped()

    def test_a_blank_campaign_name_never_triggers_a_wipe(self):
        """A malformed /chunk must not be read as 'switch to no campaign' and wipe
        a live session."""
        pathlib.Path(self.mod.CAMP_FILE).write_text("ember-hollow", encoding="utf-8")
        self._dirty_everything()
        self.client.post("/chunk", json={"campaign": "   "})
        self.assertIn("Piper", self.mod._sent)


class DicePadIsReachableOnTheMainView(ClearBase):
    """N2: the pad lives in the Party Input panel, which ships collapsed on the main
    (TV) view — so a player watching the big screen had no way to roll at all."""

    @classmethod
    def setUpClass(cls):
        # The markup stays in the template; the pad's rules moved to
        # display/static/display.css and its wiring to display/static/display.js
        # (W2). The panel's collapsed class is the one thing still in the markup.
        src = read_display_sources()
        cls.html = src.all
        cls.css = src.css
        cls.js = src.js

    def test_the_waiting_badge_is_clickable_outside_input_only_view(self):
        self.assertIn("body:not(.input-only) #dice-pending-badge.visible {", self.css)
        self.assertRegex(self.css,
                         r"body:not\(\.input-only\) #dice-pending-badge\.visible\s*\{[^}]*"
                         r"pointer-events:\s*auto")

    def test_opening_the_pad_unhides_the_panel_body_it_lives_in(self):
        """The panel ships class="collapsed", which is display:none on #input-body.
        A position:fixed child renders nothing inside a display:none ancestor, so
        the collapse has to be overridden for as long as the pad is floated."""
        self.assertIn('id="input-panel" class="collapsed"', self.html)
        self.assertIn("#input-panel.collapsed #input-body { display: none; }", self.css)
        self.assertIn("body.dice-pad-open #input-panel.collapsed #input-body {", self.css)

    def test_the_pad_hides_everything_else_in_the_panel_when_floated(self):
        """Otherwise un-hiding the body dumps the character tabs and the action
        textarea over the narration as well."""
        self.assertRegex(self.css,
                         r"body\.dice-pad-open #input-panel\.collapsed #input-body > "
                         r"\*:not\(#dice-pad\)\s*\{\s*display:\s*none\s*!important")

    def test_the_badge_click_handler_is_bound_at_startup(self):
        self.assertIn("function _initDiceBadgeClick()", self.js)
        self.assertIn("_initDiceBadgeClick();", self.js)

    def test_the_pad_is_initialised_outside_the_input_only_guard(self):
        """The one that matters, and the one the first version of this test missed.

        N2 makes the pad reachable from the main view, so _initDicePad() has to run
        there. It used to sit inside `if (_inputMode)`, which meant the main view
        floated a pad whose Roll button had no listener: visible, and dead.

        A test asserting only that the *badge handler* was bound passed happily
        against that, because the badge handler genuinely was bound — it was the pad
        underneath it that was inert. This asserts on the pad's own initialisation,
        and specifically that the call site is not inside the input-only guard.
        """
        import re
        # The guard and the call site are both in display/static/display.js (W2);
        # reading the template here would find neither.
        js = self.js
        # Match only a real statement line, never a `//` comment: the fix's own
        # comment names the function with a semicolon, so a bare substring search
        # matches prose as well as code.
        calls = []
        for m in re.finditer(r"^([ \t]*)_initDicePad\(\);[ \t]*$", js, re.M):
            line_start = js.rfind("\n", 0, m.start()) + 1
            if js[line_start:m.start()].lstrip().startswith("//"):
                continue                      # a comment, not a call
            calls.append(m)
        self.assertEqual(len(calls), 1,
                         f"expected exactly one _initDicePad() call site, found {len(calls)}")
        call = calls[0]
        line_no = js[:call.start()].count("\n") + 1

        # Find the `if (_inputMode) {` block and the offset at which it closes, by
        # brace counting. A substring search for "is it between the guard and the
        # next call" is not the question — the question is whether the call's own
        # indentation puts it inside that block.
        guard_at = js.rfind("if (_inputMode) {", 0, call.start())
        self.assertNotEqual(guard_at, -1, "the _inputMode guard is gone; re-check this test")
        depth, i = 0, js.index("{", guard_at)
        start = i
        while i < len(js):
            if js[i] == "{":
                depth += 1
            elif js[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block_end = i
        # The call must be AFTER the input-only block closes. Being before that
        # offset means it is still inside the guard.
        self.assertGreater(call.start(), block_end,
                           f"_initDicePad() at line {line_no} is inside the "
                           "`if (_inputMode)` block, so the main-view pad shows but "
                           "cannot roll: the button has no listener")

    def test_the_pad_closes_when_nothing_is_waiting_any_more(self):
        """Otherwise it keeps covering the narration with a roll nobody is asked for."""
        self.assertIn("new MutationObserver", self.js)
        self.assertIn("document.body.classList.remove('dice-pad-open')", self.js)

    def test_escape_closes_the_pad(self):
        self.assertRegex(self.js, r"e\.key === 'Escape'[^\n]*remove\('dice-pad-open'\)")


if __name__ == "__main__":
    unittest.main()
