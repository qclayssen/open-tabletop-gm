# Testing the narration display

The working note for the browser tests that cover the story column: how a
narration block is supposed to close, the idle-timer defect that stopped it
closing, the two tests that pin the fix, and what to do when one of these tests
times out. For anyone changing `display/static/display.js`, its stylesheet, or
the tests under `tests/` that drive it.

## The block lifecycle

A chunk of narration arrives at `handleIncomingText()` (`display/static/display.js`).
If more than `IDLE_GAP` (1800ms) has passed since the last chunk, the open block
is finalised first; the text is then queued and typed one character at a time by
the typewriter. A block is closed by `flushNewBlock()`, which appends a divider
(`#text-content .divider`) and undecorates the cursor.

Two things close a block:

- the chunk-gap branch in `handleIncomingText()`, when the DM pauses for more
  than `IDLE_GAP`; and
- the idle timer, armed to fire `IDLE_GAP * 2` (3600ms) after the last chunk,
  which closes the block once the typewriter has stopped.

The browser tests observe a closed block by waiting for the divider
(`flushed()` in `tests/test_display_narration_retention.py`), not by waiting for
the text to appear. Waiting for the text cannot tell "the flush ran and kept the
text" from "the flush never ran".

## The defect: a one-shot idle deadline that expires mid-reveal

Before the fix, `handleIncomingText()` armed the idle timer once per chunk:

```js
clearTimeout(idleTimer);
idleTimer = setTimeout(() => {
  if (!isTyping && charQueue.length === 0) flushNewBlock();
}, IDLE_GAP * 2);
```

The callback fired 3600ms after the chunk armed it. If the typewriter was still
running at that moment the guard was false, the callback returned, and nothing
re-armed it. A reveal longer than 3600ms therefore never closed its block: no
divider appeared and `flushNewBlock()` never ran. Raising the constant does not
fix it; any reveal longer than the new value re-enters the same state.

Measured in chromium against the real Flask app (POSTing to `/chunk`), on the
56-character sentence at `charDelay = 100`, pre-fix:

    flushCount=0  dividers=0  blocks=1  liveIdleTimers=0

The text was typed and present; the block was simply never finalised.

## The fix

The callback re-arms itself while the typewriter is active, and flushes only once
it has drained:

```js
clearTimeout(idleTimer);
const closeWhenDrained = () => {
  if (isTyping || charQueue.length > 0) {
    idleTimer = setTimeout(closeWhenDrained, IDLE_GAP * 2);
    return;
  }
  flushNewBlock();
};
idleTimer = setTimeout(closeWhenDrained, IDLE_GAP * 2);
```

Each call still clears the pending timer first, so a later chunk resets the
deadline instead of leaving a second timer behind. Same probe, post-fix:

    flushCount=1  dividers=1  blocks=1  liveIdleTimers=0

## The two tests that pin it

Both live in `tests/test_display_narration_retention.py` and slow the typewriter
(`charDelay = 100`) so a reveal deterministically outlasts the 3600ms deadline
without touching `IDLE_GAP`. Both **fail before the fix** at the `flushed()`
divider wait (`flushNewBlock()` never ran), and pass after it.

- `test_a_slow_reveal_still_closes_its_block_once` - one slow reveal; asserts the
  block closed exactly once (`divider_count == 1`) and the sentence is still in
  the story column.
- `test_a_second_chunk_resets_the_deadline_without_splitting_the_block` - a
  second chunk arrives mid-reveal (inside `IDLE_GAP`, so the same block); asserts
  one divider and one block. This checks visible flush behavior; it does not
  independently prove that the prior timer was cancelled.

Run them from the `open-tabletop-gm` checkout (or its worktree):

```bash
python3 -m pytest tests/test_display_narration_retention.py -v
python3 -m pytest tests/test_display_narration_retention.py -n4   # four workers
```

The whole suite, run in the worktree after this change:

    3944 passed, 51 skipped, 117 subtests passed, 0 failed

## When a display test times out

A timeout means only that the predicate `present()` (`tests/display_settle.py`)
was waiting on was not observed true within its budget. On its own it cannot tell
a rendering or timer defect from contention on a loaded machine, so read the
failure before changing anything:

1. Read the predicate in the failure. It names the exact condition that stayed
   false. For a reveal that never drained, check `isTyping`, `charQueue.length`
   and whether any idle timer is still live (the callback re-arms, so a stuck
   timer is a different bug from a dropped one).
2. Reproduce the test alone, then under `-n4`. A parallel-only failure can
   indicate contention, a race, or shared fixture state; an isolated failure
   can still reflect machine load. Compare predicate and timer observations
   before attributing the cause. Neither result alone diagnoses the defect.
3. Only after that, decide whether the budget or the code is wrong.

`SETTLE_TIMEOUT` (8000ms in `tests/display_settle.py`) exists so a hang is a
failure rather than a wait, not as the first lever to reach for.

## CI and the merge queue

Grounded in the outer `qclayssen/dnd-gm` repo's `docs/guides/ci.md` and
`scripts/merge_queue.py`:

- Both repos' workflows trigger on `pull_request` only, and **neither is a
  required status check**. A green check is information to GitHub, not an
  enforcement; `main` is not protected in either repo, so nothing server-side
  stops a merge.
- Since #262 the queue gate reads the PR's `statusCheckRollup` (`merge_queue.py
  gate` -> `blockers()` -> `_ci_blockers()`). A **failing or absent** rollup is a
  hard blocker: `gate` refuses. The only way past it is a recorded waiver,
  `merge_queue.py advisory <PR> --why "<reason>"`, which writes a reason onto the
  entry rather than a silent bypass; the failing checks keep printing either way.
- `merge_queue.py merge` runs from the owning worktree and, after `gate`, runs
  the **full local integrated suite** in that worktree before merging; a failure
  aborts. After the merge, `merge_queue.py landed <PR>` must prove the merge
  commit is on `main` and measure the full merged-main suite against its
  recorded baseline. CI never runs on `main`, so that measurement closes the gap.
- `merge_queue.py next` skips blocked entries and names them (non-zero when
  nothing is eligible). Skipping is visible, but it is not an authorisation to
  merge the blocked PR: `gate` validates the next eligible entry, and the
  locked merge command must still run its local suite and landing checks.

### Flaky is a claim, not an observation

There is **no automated known-flaky registry** in this repo, so nothing here can
auto-retry or auto-quarantine a failure. Do not call a failure flaky without all
of: the test node ID, the commit SHA, the dataset or fixture it ran against, a
repeat reproduction, and a linked tracking issue. A single red run is a red run;
absent that evidence, treat it as a defect and read the predicate.

## Status

This note documents the idle-drain fix for
[open-tabletop-gm#252](https://github.com/qclayssen/open-tabletop-gm/issues/252).
That issue stays open: its broader CI-reliability evidence and policy follow-up
are not addressed here, and the global merge-queue redesign is unresolved and
outside this scope. Nothing here should be read as closing them.
