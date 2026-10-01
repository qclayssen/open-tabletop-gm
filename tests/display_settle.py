"""display_settle.py: the one place a display browser test says "wait until".

WHY THIS IS ITS OWN MODULE
==========================
Three test files drive a real display page in a real browser, and all three were
doing the same thing badly in three slightly different ways: `wait_for_timeout(200)`
after something that had already happened, or before something that had not. The
number is a guess about how long a machine takes, so it is too short on a loaded one
and wasted on a fast one, and the failure when it is too short reads as a layout bug
rather than as a race. That is the shape of defect `fix-n2-settle-predicate` came
from: green when the test was run, red when the suite was.

So the primitive is here rather than copied three times, and the three files differ
only in WHICH thing they are waiting for:

  present()      the element is in the DOM with a non-zero box
  box_settled()  that element's rectangle has stopped changing
  gone()         the element is not there (the one a timed thing needs)

WHAT IS DELIBERATELY NOT HERE
=============================
The padding-top settle predicate from `test_display_retest_findings.py` is NOT
promoted, and the reason is that it is not the same wait. It is `pad >= txb`, which
is only meaningful on a page that has a combat panel publishing `--tx-bottom` and a
28px transition that has to be taken off first; the other two files measure a page
that has neither, and importing a predicate that returns false forever there would
turn every one of their waits into a timeout. A shared module for waits that do not
apply to the file importing them is worse than a duplicated block that is obviously
about this page.

`NO_PADDING_TRANSITION` IS shared, because taking the transition off is a property
of the product being measured and not of the assertion, and it is already the
convention the predicate's docstring reasons from.

THE FLOATS ARE THE CASE THAT PROVES THE POINT
=============================================
The odds float in tactics.js is removed on a `setTimeout(..., 1400)`. A test that
has to read it DURING its life cannot wait for it to settle, and cannot wait for it
to be gone either. `present(".tx-odds-float", timeout=1000)` is the third thing:
wait for it to EXIST, with a bound short enough that the read still lands inside
the 1400ms. That is both faster than a fixed 200ms sleep and far more robust
against it, because a slow machine reaches the read later rather than later than the
float's own lifetime. Measured: the float is drawn in the same task as the push, so
this resolves in single-digit milliseconds.
"""
from __future__ import annotations

# ── the product's own padding transition ────────────────────────────────────
#
# `#text-scroll` transitions `padding` over 0.4s, and `--tx-bottom` feeds it. Any
# test measuring the inset wants the settled layout rather than a frame of an
# animation, and "the value stopped changing" cannot tell a finished transition from
# one that has not started, so the transition is taken off rather than waited out.
NO_PADDING_TRANSITION = "#text-scroll { transition: none !important; }"


# ── present: in the DOM, with a box ─────────────────────────────────────────
#
# `offsetParent` is null for display:none AND for position:fixed elements, so it is
# not usable on its own. getBoundingClientRect is: a display:none element has a
# zero rect, a laid-out one does not.
_PRESENT = """(sel) => {
  const els = document.querySelectorAll(sel);
  if (!els.length) return false;
  return [...els].some(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
}"""


def present(page, selector: str, timeout: int = 5000, why: str = ""):
    """Block until `selector` matches something that is laid out.

    Raises AssertionError if it never does, chaining playwright's TimeoutError. A
    caller that is asserting the element is ABSENT must use `gone` or catch this,
    since a timeout here means "it is there" for every assertion that follows.

    `why` names what was being waited for, and is worth passing wherever the wait
    stands in for a fixture.
    """
    try:
        page.wait_for_function(_PRESENT, arg=selector, timeout=timeout)
    except Exception as exc:                                # noqa: BLE001
        detail = why or selector
        raise AssertionError(
            f"{detail}: {selector!r} never appeared within {timeout}ms. Nothing "
            f"was laid out matching it, so this is not a slow machine -- check "
            f"that whatever should have produced it actually ran."
        ) from exc
    return page


def gone(page, selector: str, timeout: int = 8000):
    """Block until `selector` matches nothing laid out.

    For a thing that leaves on its own (a float on a 1400ms timer) rather than for
    a thing that should never have appeared.
    """
    page.wait_for_function(
        "(sel) => ![...document.querySelectorAll(sel)].some(e => {"
        "  const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })",
        arg=selector, timeout=timeout)
    return page


# ── box_settled: the rectangle has stopped changing ─────────────────────────
#
# Three identical samples, not one, and each poll is a separate task. Two reasons,
# both learned the hard way in test_display_retest_findings.py:
#
#   - A single match can be two samples inside the same easing step.
#   - A synchronous loop inside the predicate never yields, style recalc never runs,
#     and the value sits at its starting value for every iteration, so "stable" is
#     reached on the third one having measured nothing.
#
# Unlike that file's predicate this one cannot use `pad >= txb`, so it is a pure
# stability check and it is only correct for a box that ends up somewhere. The three
# samples are what keep it from reporting a value the first frame of a transition
# has not left yet, and the callers below are all cases where the final box is what
# the test then asserts against.
def box_settled(page, selector: str, timeout: int = 5000, before=None):
    """Block until `selector`'s rectangle has been identical for three samples.

    The replacement for a fixed sleep after something reflowed the page: a panel
    opening, a spell list landing, a rail expanding. Each of those finishes at a
    time that depends on the machine, and each of them was being waited for with a
    number chosen on a fast one.

    `before` is an optional extra condition, evaluated on the same sample. It is for
    the case where the box is already where it is going to be and the caller is
    waiting for something else to catch up with it (a scroll landing, a list
    populating), where "the box stopped moving" is true before the thing happened.
    """
    page.wait_for_function(
        """a => {
          const el = document.querySelector(a.sel);
          if (!el) return false;
          const r = el.getBoundingClientRect();
          const key = [Math.round(r.top), Math.round(r.left),
                       Math.round(r.width), Math.round(r.height)].join(',');
          const w = window;
          w.__boxKey = w.__boxKey || {};
          if (w.__boxKey[a.sel] !== key) {
            w.__boxKey[a.sel] = key; w.__boxStable = 0; return false;
          }
          w.__boxStable = (w.__boxStable || 0) + 1;
          if (w.__boxStable < 3) return false;
          return a.before ? !!a.fn() : true;
        }""",
        arg={"sel": selector, "before": bool(before), "fn": before},
        polling="raf", timeout=timeout)
    return page


# ── page_ready: the parts a display test needs before it measures ───────────
#
# `wait_until="load"` already covers the scripts, so this is not that. It is the
# two things that are true a beat later and that a font measurement reads wrong
# until they are: `document.fonts` has finished, and the SSE handler the tests
# drive by hand is installed. Measured: on a warm page both are already true when
# `load` fires, so this normally costs nothing, which is the point of a predicate
# over a 500ms sleep.
_PAGE_READY = """() => {
  const ok = typeof window.handleIncomingText === 'function'
          && typeof window.instantFlush === 'function';
  return ok && (document.fonts ? document.fonts.status === 'loaded' : true);
}"""


def page_ready(page, timeout: int = 8000):
    """Block until the display's own globals exist and the fonts have settled."""
    page.wait_for_function(_PAGE_READY, timeout=timeout)
    return page
