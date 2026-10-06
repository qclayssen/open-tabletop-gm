"""#228: the typewriter's "cursor" is a CSS class on a content element, not a
caret node -- so a block flush must undecorate it, never remove it.

THE DEFECT THIS PINS
====================
`flushNewBlock()` used to open with

    if (currentCursor && currentCursor.parentNode) {
      currentCursor.parentNode.removeChild(currentCursor);
      currentCursor = null;
    }

which reads correctly only if `currentCursor` is a throwaway caret span. It is
not. Since the initial release the comment above its assignment has said

    // Apply cursor class to current last paragraph via ::after pseudo-element
    currentCursor = currentEl.querySelector('p:last-child');

so it is the last `<p>` of the block -- the paragraph the DM's sentence was just
typed into. `typeNextChar()` and `instantFlush()` both assign
`_lastTextEl(currentEl)`, which is that same element, and the visual cursor is a
bar drawn by `.typing-cursor::after` in display.css. Removing the node
therefore deleted the last typed paragraph of the narration block.

`_flushForBlock()` -- written later, for the same field, on the other code path
-- already does the right thing: `classList.remove('typing-cursor')` and a null.
That is the contract these tests hold the flush path to.

REPRODUCED ON THE CURRENT BUILD
===============================
Measured in chromium against the real Flask app on an ephemeral port, by POSTing
to the real `/chunk` route and reading the story column:

    POST /chunk "The lantern gutters as the door swings shut behind them."
      t+2.7s   #text-content  "The lantern gutters as the door swings shut behind them."
      t+3.9s   #text-content  "The lantern gutters as the door swings shut behind them."
      t+6.9s   #text-content  "<voices sidebar text>x"   <-- paragraph gone

The 3.6s gap is `IDLE_GAP * 2` (display.js), the idle timer that closes a block
once the typewriter has drained. It is not a slow machine and not a typewriter
race: the text is there, correct and complete, and then it is deleted while
nobody is typing. The same removal is reachable from the other three callers of
`flushNewBlock()` -- the >1.8s chunk-gap branch in `handleIncomingText`,
`renderReplay()` and `renderReplayBatch()` -- so a phone that reconnects
mid-sentence loses that sentence too.

The one path that is safe is `charDelay === 0`, and only by accident:
`instantFlush()` calls `clearTimeout(idleTimer)`, which cancels the timer
`handleIncomingText` armed three lines earlier, so there is no deferred flush to
delete anything. That is why the defect is invisible with the typewriter off, and
why every test here leaves it on.

WHERE THE TEXT ACTUALLY GOES
============================
Nowhere. The paragraph is detached from the DOM, not emptied: no server call, no
buffer, no other sink. The characters survive only in the `text_log.json` replay,
so a player who was present loses text and a player who reconnects gets it back.
The `README.md` claim "no narration is lost on reconnect" is about the replay
path and was never wrong; what was wrong is that live narration is lost *on the
way to* the screen.

HOW THESE TESTS ARE ARRANGED
===========================
The three static tests pin the ownership contract, so the field cannot quietly
become a caret node again without a test failing. The six browser tests pin the
behaviour, the last two of them by watching every DOM removal in the story
column rather than by checking one call site's text.

A note on why there is no source scan for `removeChild(currentCursor)`: the
first draft had one, and it could not be written honestly. The bug's own comment
has to be able to *name* `removeChild(currentCursor)`, and any line-comment
stripper that survives that also has to survive apostrophes in prose ("the
block's last paragraph"), which is a hand-rolled JS lexer -- more fragile than
the bug. The MutationObserver tests below assert the same thing as behaviour and
survive any rewrite of the source.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from tests._browser import BrowserTestCase
from tests.display_settle import present

#: A sentence long enough that its paragraph is unmistakably the one that
#: disappears, and with no markup in it so a failure cannot be blamed on
#: _mdParse splitting it somewhere unexpected.
SENTENCE = "The lantern gutters as the door swings shut behind them."

#: The second sentence, for the paths that append to the same block.
SECOND = "Rain starts to tick against the shutter."

#: Installed on every page before anything is typed. Records every element the
#: story column loses, so a test can assert on what was REMOVED rather than only
#: on what is still there -- the difference between "the sentence is still
#: there" and "nothing that had text was ever taken away".
WATCH_REMOVALS = """
window.__removed = [];
new MutationObserver(records => {
  for (const rec of records) {
    for (const node of rec.removedNodes) {
      if (node.nodeType !== 1) continue;
      const text = (node.textContent || '').trim();
      window.__removed.push({tag: node.tagName, text: text.slice(0, 80)});
    }
  }
}).observe(document.getElementById('text-content'),
            {childList: true, subtree: true});
"""


def story(page) -> str:
    """What a reader would actually see in the story column."""
    return page.evaluate(
        "() => [...document.querySelectorAll('#text-content .dm-block')]"
        ".map(b => b.textContent).join('\\n')")


def text_bearing_removals(page) -> list:
    """Elements carrying narration text that the story column has lost.

    Empty `<p>` placeholders do not count: `getOrCreateBlock()` creates one
    eagerly and `_mdStartBlock()` legitimately takes it back when a block break
    follows, so a removal of one is layout housekeeping, not a lost sentence.
    """
    return page.evaluate(
        "() => (window.__removed || [])"
        ".filter(r => r.text && !/^[x\\u2726\\s]+$/.test(r.text))")


def feed(page, text: str) -> None:
    """Push narration the way the SSE dispatcher does, then wait it out.

    The typewriter is left ON. `charDelay = 0` makes every one of these tests
    pass for the wrong reason, because it also cancels the idle timer (see the
    module docstring) -- which is exactly the trap that let this defect reach a
    release.
    """
    page.evaluate("t => handleIncomingText(t)", text)
    drained(page)


# The typewriter and the idle timer are real JS timers in a real browser, so both
# legs scale with how fast the runner is. These are the multipliers applied to the
# page's OWN constants, read at run time -- not to numbers typed in here.
#
# WHY DERIVED AND NOT A BIGGER LITERAL (#252)
# ===========================================
# `timeout=20000` was a hand-picked number with no relationship to the work it
# was waiting for. Measured on a quiet machine the whole path takes
#
#     len(SENTENCE) * charDelay  +  IDLE_GAP * 2
#     = 56 * 36                   +  1800 * 2      =  ~5.6s
#
# so 20s looked like 3.5x headroom. It is not: on a loaded macOS arm64 runner
# every one of those timers stretches with it, and the typewriter leg stretches
# with the CHARACTER COUNT while the idle leg stretches on its own. The result is
# a test that passes on a developer machine and on a free CI runner and fails on a
# contended one -- which is what "times out in CI on 4 of 4 unrelated branches"
# was actually reporting. A literal bump would have moved the cliff, not removed it.
#
# The margin is 4x the derived budget, which is generous enough for a runner at
# several times normal speed and still bounded: a flush that genuinely never runs
# fails in well under a minute instead of hanging to a job timeout.
TIMER_MARGIN = 4
#: Never wait less than this, however short the derived budget computes. A budget
#: that collapsed to a few hundred ms would turn a slow runner into a flake again,
#: which is the bug rather than its cure.
MIN_TIMER_BUDGET_MS = 30000


def timer_budget(page, chars: int, *, margin: int = TIMER_MARGIN) -> int:
    """How long this page's OWN constants say `chars` of typing plus one idle
    flush should take, times `margin`.

    Read from the page rather than imported, because these are mutable in
    `display.js` -- `charDelay` is reassigned by the speed toggle at
    `display.js:2930` -- so a value copied into this file would be wrong the
    moment anyone moved the slider. `charDelay` is a top-level `let` and
    `IDLE_GAP` a top-level `const`, both of which live in the page's global
    lexical environment and are readable from `evaluate` (verified, not assumed).
    """
    # Index the returned dict rather than unpacking it. `delay, gap = {...}` on
    # a two-key dict yields the KEYS -- the strings "delay" and "gap" -- and
    # `chars * "delay"` is then silent string REPETITION in Python, so the budget
    # comes out as one enormous number and `int()` raises thousands of characters
    # later. Typed the values so a future return of the wrong shape fails here
    # rather than as arithmetic on strings.
    got = page.evaluate("() => ({delay: charDelay, gap: IDLE_GAP})")
    delay, gap = int(got["delay"]), int(got["gap"])
    needed = (chars * delay) + (2 * gap)
    return max(MIN_TIMER_BUDGET_MS, int(needed * margin))


def drained(page, timeout: int = None) -> None:
    """Block until the typewriter has nothing left to type."""
    if timeout is None:
        timeout = timer_budget(page, len(SENTENCE))
    present(page, "() => !isTyping && charQueue.length === 0",
            "the typewriter to drain", timeout=timeout)


def flushed(page, timeout: int = None) -> None:
    """Block until the block really has been flushed.

    Proof that the flush happened, so "the text is still there" cannot be
    satisfied by the flush simply never running. A divider is what
    `flushNewBlock()` appends for a block that had content, so its presence is
    the observable the flush is judged by.
    """
    if timeout is None:
        timeout = timer_budget(page, len(SENTENCE))
    present(page, "() => !!document.querySelector('#text-content .divider')",
            "flushNewBlock() to run and post its divider", timeout=timeout)


class CursorOwnership(unittest.TestCase):
    """The contract, asserted against the source without a browser.

    Cheap, and it is what names the bug: `currentCursor` is the block's last
    text element, and the cursor is a class on it. A reader who takes this on
    trust can see why removing that element is wrong without launching
    chromium, and a future caret-node assumption has to be argued for in a test
    instead of inherited from a comment.
    """
    @classmethod
    def setUpClass(cls) -> None:
        display = pathlib.Path(__file__).resolve().parent.parent / "display"
        cls.js = (display / "static" / "display.js").read_text(encoding="utf-8")
        cls.css = (display / "static" / "display.css").read_text(encoding="utf-8")

    def test_both_writers_of_the_cursor_assign_the_blocks_last_text_element(self):
        for line in ("currentCursor = _lastTextEl(currentEl);",):
            self.assertIn(line, self.js)
        # And nowhere assigns it a freshly-created node, which is the assumption
        # the removeChild() was written under. Both negatives are exact code
        # shapes, so a comment naming the old bug cannot satisfy them.
        self.assertNotIn("currentCursor = document.createElement", self.js)
        self.assertNotIn("currentCursor = el;", self.js)

    def test_the_cursor_is_drawn_by_a_pseudo_element_not_by_a_node(self):
        """A cursor that lives in the stylesheet has no node to remove.

        This is the other half of the ownership contract and the half that is
        easy to forget: the bar is `.typing-cursor::after`.
        """
        self.assertIn(".typing-cursor::after", self.css)

    def test_last_text_el_skips_the_badge_the_tts_bar_and_tables(self):
        """`_lastTextEl` is what makes the cursor safe to undecorate.

        It walks back over a block's children to the last P/H1-6/LI, skipping the
        badge image, the TTS bar and a table. Without those skips the cursor
        could land on something that is not narration at all. The three are
        named here because they are the reason the helper exists.
        """
        self.assertIn("function _lastTextEl(block)", self.js)
        self.assertIn("e.tagName === 'TABLE'", self.js)
        self.assertIn("^(P|H[1-6]|LI)$", self.js)


class Browser(BrowserTestCase):
    module_name = "gm_display_app_narration_228"

    def setUp(self) -> None:
        # This display's runtime state is a temp directory, so the narration
        # these tests POST does not land in display/text_log.json -- gitignored
        # state that is loaded at startup and replayed into every display that
        # connects afterwards. Without this, a chunk from here is another
        # display test's fixture.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = pathlib.Path(tmp.name)
        prev = {k: os.environ.get(k) for k in ("GM_TEXT_LOG_FILE", "GM_STATS_FILE")}
        os.environ["GM_TEXT_LOG_FILE"] = str(state / "text_log.json")
        os.environ["GM_STATS_FILE"] = str(state / "stats.json")

        def restore():
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)

        # A second server on its own port. The class server from
        # BrowserTestCase.setUpClass was loaded before these env vars existed,
        # so it points at the repo's own text_log.json; posting to it would
        # leave state that the next display test replays.
        self.server = type(self.server)(self.module_name)
        self.addCleanup(self.server.stop)
        self.port = self.server.port

    def page_in_story(self):
        page = self.open_page(size=(1280, 900), wait=600)
        page.on("pageerror", lambda e: self.fail(f"uncaught page error: {e}"))
        page.evaluate(WATCH_REMOVALS)
        return page

    def post_chunk(self, text: str) -> None:
        """POST the real `/chunk` route on the running app.

        In-process `test_client()` rather than urllib: the POST's own transport
        is not what is under test, the SSE broadcast that follows it is, and the
        broadcast reaches the page's queue either way. Driving `/chunk` rather
        than calling `handleIncomingText()` is what makes the idle-timer test
        below an end-to-end reproduction instead of a unit test.
        """
        r = self.server.app.test_client().post("/chunk", json={"text": text})
        self.assertEqual(r.status_code, 204, r.data)

    # 1. the defect, called directly ----------------------------------------
    def test_flush_keeps_the_last_typed_paragraph(self):
        page = self.page_in_story()
        feed(page, SENTENCE)
        # The whole defect in one line of evidence: before the flush, the cursor
        # IS the paragraph.
        self.assertEqual(
            page.evaluate("() => currentCursor && currentCursor.textContent"),
            SENTENCE)
        page.evaluate("flushNewBlock()")
        self.assertIn(SENTENCE, story(page),
                      "flushNewBlock() deleted the paragraph it was finalising")

    # 2. the defect, end to end ---------------------------------------------
    def test_the_idle_timer_keeps_the_last_typed_paragraph(self):
        """The real symptom, on the real timer.

        The 3.6s `IDLE_GAP * 2` timer that closes a block once the typewriter
        drains is what a table actually hits: the DM finishes a sentence, the
        screen is watched, and the paragraph vanishes with no further input.
        """
        page = self.page_in_story()
        self.post_chunk(SENTENCE)
        drained(page)
        self.assertIn(SENTENCE, story(page))

        flushed(page)          # the idle timer ran -- proven, not assumed
        self.assertIn(SENTENCE, story(page),
                      "the idle timer that closed the block deleted its last paragraph")

    # 3. the other callers ----------------------------------------------------
    def test_a_reconnect_replay_keeps_in_progress_narration(self):
        """`renderReplayBatch()` flushes before it draws, on every reconnect.

        A phone that drops off the Wi-Fi for two seconds and comes back loses
        the sentence it was reading at that moment -- the paragraph is detached
        before the replay is rendered, and the replay only carries what the
        *server* has, so the paragraph does not come back.
        """
        page = self.page_in_story()
        feed(page, SENTENCE)
        page.evaluate(
            "() => renderReplayBatch([{text: 'something the server kept',"
            " seq: 9001, _epoch: 'e'}])")
        flushed(page)
        self.assertIn(SENTENCE, story(page),
                      "the reconnect replay deleted in-progress narration")

    def test_render_replay_keeps_in_progress_narration(self):
        """`renderReplay()` flushes too, and is the single-chunk path."""
        page = self.page_in_story()
        feed(page, SENTENCE)
        page.evaluate("t => renderReplay(t)", "an older line from the log")
        self.assertIn(SENTENCE, story(page),
                      "renderReplay() deleted the paragraph being typed")

    def test_a_chunk_gap_starts_a_new_block_without_eating_the_old_one(self):
        """`handleIncomingText()` flushes when the last chunk is >IDLE_GAP old.

        This is the ordinary shape of a streamed turn: the DM's model pauses
        between sentences, so the gap branch is not an edge case, it is the
        common case. Two sentences, a real gap between them, both must survive.
        """
        page = self.page_in_story()
        feed(page, SENTENCE)
        # The gap, without a 1.8s sleep. `lastChunkTime` is a module-level
        # `let`, reached through the page's own top-level scope, which is what
        # an evaluated function body compiles into. A `function`, not an arrow,
        # because it needs an argument -- and `arguments` does not exist in an
        # arrow, which is how this test first measured nothing at all.
        page.evaluate("""function (second) {
            lastChunkTime = Date.now() - 5000;
            handleIncomingText(second);
        }""", SECOND)
        drained(page)
        flushed(page)
        self.assertIn(SENTENCE, story(page),
                      f"the earlier block was eaten: {story(page)!r}")
        self.assertIn(SECOND, story(page),
                      f"the new block is missing: {story(page)!r}")

    # 4. the whole lifecycle, watched for removals --------------------------
    def test_no_narration_is_ever_removed_from_the_story_column(self):
        """The strongest form: not one call site, but every path at once.

        Type a block, let the idle timer close it, reconnect (replay), then open
        a second block across a chunk gap -- and assert that across all of it,
        nothing carrying text was ever detached. This subsumes the four tests
        above and is what would catch a fifth call site nobody thought to test.
        """
        page = self.page_in_story()
        self.post_chunk(SENTENCE)
        drained(page)
        flushed(page)
        page.evaluate(
            "() => renderReplayBatch([{text: 'a line from the log',"
            " seq: 9002, _epoch: 'e'}])")
        page.evaluate("""function (second) {
            lastChunkTime = Date.now() - 5000;
            handleIncomingText(second);
        }""", SECOND)
        drained(page)
        flushed(page)

        lost = text_bearing_removals(page)
        self.assertEqual(lost, [],
                         "the story column lost text it had already shown: "
                         f"{lost}")

    # 5. the legitimate half of the removal ---------------------------------
    def test_an_empty_trailing_placeholder_is_still_cleaned_up(self):
        """The fix must not become "never remove anything".

        A block whose markdown ends on a block break leaves an empty `<p>`
        behind (`getOrCreateBlock()` creates one eagerly). Removing *that* is
        correct -- it is a layout artefact, not narration -- and the assertion
        is that a flush leaves no empty paragraph behind, so the fix cannot be
        made by deleting the cleanup.
        """
        page = self.page_in_story()
        page.evaluate(
            "t => { handleIncomingText(t); instantFlush(); }",
            "A paragraph that is done.\n\n")
        page.evaluate("flushNewBlock()")
        empties = page.evaluate(
            "() => [...document.querySelectorAll('#text-content .dm-block > p')]"
            ".filter(p => !p.textContent.trim()).length")
        self.assertEqual(empties, 0,
                         "flushNewBlock() left an empty placeholder paragraph behind")
        self.assertIn("A paragraph that is done.", story(page))


if __name__ == "__main__":
    unittest.main()

# ── why the budget is derived rather than typed in (#252) ─────────────────
#
# The defect is ENVIRONMENT-DEPENDENT: on a quiet machine every budget passes,
# and on a contended CI runner the old hardcoded 20s was not enough. That means a
# mutation on the timeout value CANNOT be caught here -- verified, not assumed:
# reverting to the literal 20000 passes locally, and so does collapsing the floor
# to 6s. Both pass. A test that cannot fail on the broken version is decoration,
# and pretending otherwise would be the same error as the original bug.
#
# What CAN be pinned here is the property that makes the fix more than a bigger
# number: the budget is DERIVED from the page's own constants, so it moves when
# they move. A typed-in literal cannot have that property, which is why the old
# value drifted out of relationship with the work it waited for.

class TimerBudget(unittest.TestCase):
    """`timer_budget` as arithmetic, against pages with known constants.

    No browser: the function's whole contract is "read two numbers, derive a
    budget", and a chromium launch to check multiplication would cost a second to
    prove nothing the arithmetic does not.
    """

    class _Page:
        def __init__(self, delay, gap):
            self._delay, self._gap = delay, gap

        def evaluate(self, _script):
            return {"delay": self._delay, "gap": self._gap}

    def test_a_slower_typewriter_buys_a_larger_budget(self):
        """The speed toggle reassigns `charDelay` (`display.js:2930`). A GM who
        slows the typewriter down, or a future default change, must not silently
        outrun a budget computed from the old number."""
        fast = timer_budget(self._Page(36, 1800), 56)
        slow = timer_budget(self._Page(120, 1800), 56)
        assert slow > fast, f"{slow} is not larger than {fast}"

    def test_a_longer_idle_gap_buys_a_larger_budget(self):
        fast = timer_budget(self._Page(36, 1800), 56)
        long_gap = timer_budget(self._Page(36, 4000), 56)
        assert long_gap > fast

    def test_more_characters_buy_a_larger_budget(self):
        short = timer_budget(self._Page(36, 1800), 10)
        long_sentence = timer_budget(self._Page(36, 1800), 500)
        assert long_sentence > short, (
            "the typewriter leg scales with character COUNT, which is the half "
            "of the old 20s that was least likely to be checked")

    def test_the_budget_exceeds_what_the_page_actually_needs(self):
        """The margin's whole job: the budget must be comfortably above the real
        work, or it is the same cliff with a different number."""
        delay, gap, chars = 36, 1800, 56
        needed = (chars * delay) + (2 * gap)
        budget = timer_budget(self._Page(delay, gap), chars)
        assert budget >= needed * 2, (
            f"budget {budget} is under 2x the {needed}ms the page actually needs")

    def test_the_budget_is_never_smaller_than_the_floor(self):
        """A budget that collapsed to a few hundred ms would turn a slow runner
        into a flake again, which is the bug rather than its cure."""
        instant = timer_budget(self._Page(0, 0), 1)
        assert instant >= MIN_TIMER_BUDGET_MS

    def test_a_dict_return_is_indexed_not_unpacked(self):
        """The bug I made while writing this.

        `delay, gap = page.evaluate(...)` on a two-key dict yields the KEYS --
        the strings "delay" and "gap" -- and `chars * "delay"` is then silent
        string repetition in Python, so the budget became one enormous number and
        `int()` raised thousands of characters away from the cause. Pinned
        because the failure looks like an arithmetic problem, not a type one.
        """
        got = self._Page(36, 1800).evaluate("ignored")
        assert isinstance(got, dict)
        delay, gap = int(got["delay"]), int(got["gap"])
        assert (delay, gap) == (36, 1800)
        assert not isinstance(56 * got["delay"], str)


class BoundedFailure(unittest.TestCase):
    """A wait that can never be satisfied must still FAIL, in bounded time.

    The counterweight to raising a budget: a timeout raised far enough to survive
    a loaded runner must not also survive a flush that genuinely never runs, or
    the test stops being a signal and becomes a stall.
    """

    def test_a_predicate_that_never_becomes_true_still_raises(self):
        """Uses the SHARED browser from tests/_browser.py, not a fresh
        `sync_playwright()`. Launching one inside the test body trips the sync
        API's own guard -- "you are using Playwright Sync API inside the asyncio
        loop" -- which is the harness telling me to reuse the session browser,
        and it is what every other display test here does."""
        from tests._browser import BrowserUnavailable, shared_browser
        try:
            browser = shared_browser()
        except BrowserUnavailable as exc:
            self.skipTest(str(exc))
        page = browser.new_context().new_page()
        self.addCleanup(page.context.close)
        page.set_content("<html><body><p>x</p></body></html>")
        with self.assertRaises(AssertionError) as caught:
            present(page, "() => false",
                    "something that never arrives", timeout=1500)
        assert "never arrived (1500ms)" in str(caught.exception)
