"""Waiting for the display to stop moving, in one place (not a test module).

WHY THIS EXISTS
===============
Fourteen of the display's browser tests used to answer "has it finished yet?" by
sleeping. A sleep is not an answer: it is a bet that the thing arrived within N
milliseconds, and it is a bet that gets worse the more of the suite has run
first. `test_display_retest_findings.py` documents the whole shape of that at
length and ships a real predicate for it; this module is that predicate, plus
the second one the other files need, promoted so the reasoning is written once.

`wait_for_timeout` is not forbidden outright. It is right for a test that is
measuring a RATE, where the answer really is "how many frames happened in the
time you allowed" and the negative case is the absence of the thing. Everything
here replaces the other kind: a sleep standing in for "the panel has been
measured and the inset has caught up with it".

THE PREDICATES
==============

`settle` is the layout one. `#text-scroll` transitions its padding over 0.4s and
the value it is heading for is published by the scripts as `--tx-bottom` (the
combat panel's measured bottom edge) and `--dpb-bottom` (the dice-pending
badge's), so "arrived" has a real lower bound to be measured against rather than
a guess about easing curves. `calm` removes the transition first, so that
arriving is the only thing left to observe.

`present` is the other one, and it exists for a different reason. Every call to
`page.wait_for_function` that times out raises playwright's own
`TimeoutError: Timeout 5000ms exceeded`, whose message names nothing: not the
predicate, not the element, not the state of the page. The usual cause of that
timeout is that the thing never rendered, and read as written it says the
machine was slow, so the next thing anybody does is re-run it and hope. Every
call goes through here instead, and the failure names what was being waited for.
"""
from __future__ import annotations

#: Take the padding transition off, so "the value has stopped changing" and "the
#: value has arrived" are the same observation. Without this, three identical
#: samples in a row is also what an animation looks like in its first 50ms.
NO_PADDING_TRANSITION = "#text-scroll { transition: none !important; }"

#: The inset has caught up with everything that is overlaying the story, and has
#: stopped moving.
#:
#: Poll padding-top, NOT the published variables. Both are written
#: synchronously by a ResizeObserver, so they are already final on the first
#: read while padding is still short of them; polling the variable returns
#: immediately and measures a half-built layout. That version failed five tests
#: that had been passing.
#:
#: `pad >= need` rather than equality, because the rules that write padding-top
#: add a gap of their own (28px under the combat panel, 12px under the badge) and
#: take a max() with the resting inset, so the settled value is legitimately
#: larger than the edge it is clearing. It is a lower bound and nothing more,
#: which is what keeps this from pinning a number the stylesheet owns.
SETTLE = """() => {
  const pad = parseFloat(getComputedStyle(
    document.getElementById('text-scroll')).paddingTop) || 0;
  const cs = getComputedStyle(document.body);
  const px = n => parseFloat(cs.getPropertyValue(n)) || 0;
  const need = Math.max(px('--tx-bottom'), px('--dpb-bottom'));
  // `need` is zero when nothing is overlaying the story, which is not a failure
  // to wait out: the base inset is a constant in the stylesheet and there is
  // nothing to catch up with.
  if (need && pad < need) return false;
  const w = window;
  if (w.__mqLastPad === undefined || pad !== w.__mqLastPad) {
    w.__mqLastPad = pad; w.__mqStable = 0; return false;
  }
  // Three identical samples, not one: a single match can be two samples inside
  // the same easing step. Each poll is a separate task, or a synchronous loop
  // would never yield and style recalc would never run.
  return ++w.__mqStable >= 3;
}"""

#: How long a settle is given before it is called a failure. Long enough for a
#: loaded machine running the suite serially, short enough that a hang is a
#: failure rather than a wait.
SETTLE_TIMEOUT = 8000


def _timeout_error():
    """playwright's TimeoutError, or a stand-in if playwright is absent.

    The test modules that call this are already skipped without playwright, so
    this is only about importing the helper at all.
    """
    try:
        from playwright.sync_api import TimeoutError
        return TimeoutError
    except ImportError:                                        # pragma: no cover
        return None


def calm(page):
    """Take the padding transition off. Idempotent, and a no-op without playwright."""
    page.add_style_tag(content=NO_PADDING_TRANSITION)
    # One frame, so the style is live before the first read. A style tag applies
    # on the next style recalc, and the measurement that follows would otherwise
    # race it.
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('text-scroll'))"
        ".transitionDuration.split(',').every(d => parseFloat(d) === 0)",
        timeout=SETTLE_TIMEOUT)


def settle(page, timeout=SETTLE_TIMEOUT):
    """Block until the story's inset has caught up with what is overlaying it.

    Call `calm(page)` first, or "three samples in a row and none of them moved"
    can be true of an animation in its first 50ms. That is not a theoretical
    concern: the predicate could then return while the layout was half built,
    pass when the file ran alone, and fail at the fourth viewport when it ran
    after the rest of the suite, which is the worst shape a timing bug can take.
    """
    page.wait_for_function(SETTLE, polling="raf", timeout=timeout)
    # A frame past the last sample, so the reading the caller takes is of the
    # settled layout and not of the one before it.
    page.wait_for_timeout(50)


def present(page, predicate, what, timeout=SETTLE_TIMEOUT, arg=None):
    """Block until `predicate` is true, or fail saying that `what` never arrived.

    `what` is prose, not a selector: it is what the failure message reads as
    "the spell list did not open", which is the difference between knowing what
    to look at and re-running the test to see whether it passes this time.
    `arg` is passed through to the predicate, for a wait that is about a
    particular value: a width that had to change, a count that had to grow.
    """
    try:
        page.wait_for_function(predicate, arg=arg, timeout=timeout)
    except Exception as exc:
        if _timeout_error() is None or not isinstance(exc, _timeout_error()):
            raise
        raise AssertionError(
            f"{what} never arrived ({timeout}ms).\n"
            f"  predicate: {predicate}\n"
            f"  This is normally the thing never having rendered, not a slow "
            f"machine. Playwright's own message for it says only "
            f"'Timeout {timeout}ms exceeded', which is why it is wrapped here."
        ) from exc
