"""agency.py: the GM-only ledger of agency violations, and how each was corrected.

Every guardrail in `Session._dm` and `Session._player_turn` buys a corrective retry and
then keeps the first draft unless the retry came back clean. That is a deliberate
bargain, and it is invisible from the outside: nothing anywhere says how many drafts a
session tripped a guardrail on, how many retries cleaned them, and how many violations
reached the player anyway. A GM reviewing a session has the transcript and no count.

So this is the count. One JSON line per occurrence, append-only, in
`<campaign>/localdm/agency.jsonl`:

    {"turn": 7, "scene": 2, "form": "agency", "attempts": 2,
     "outcome": "caught", "evidence": "you say ...", "at": "2026-10-03 21:14 +0200"}

WHAT IS RECORDED, AND WHY EACH FIELD IS THERE
==============================================

  turn     which turn, so a GM can find the line in transcript.jsonl
  scene    which scene, so a run of trips can be read as one beat rather than as seven
  form     which guardrail tripped, named after the boundary it protects rather than
           after the regex that found it
  attempts how many drafts tripped it on this turn. This is the dedup: a retry that
           trips the same guard again is the SAME violation being corrected twice, not
           two violations, and a log that cannot tell those apart overstates the rate
           by however many retries the model needed
  outcome  which of three, and the distinction is the whole point:

             caught     the guardrail fired and the text the player was shown does
                        NOT trip it. The correction worked: the retry was clean and
                        was adopted.
             narrated   the guardrail fired and the text the player was shown STILL
                        trips it. The retry was dirty, so the first draft was kept
                        (see `Session._dm`: a rewrite is adopted only when it comes
                        back clean), and the player saw the violation.
             refused    the engine refused outright, with no correction step at all.
                        The two mid-fight fields are always this: a check or a cast
                        asked for while a fight is running is refused in one visible
                        engine line, and nothing is rolled or spent.

  unknown is a fourth, and it is not an outcome anybody chose. A draft tripped a guard
  and the region never settled -- a model error, say -- so what the player was shown is
  not knowable. Recorded rather than dropped, because a silently dropped trip is
  indistinguishable from no trip, and the missing record is exactly what a GM cannot
  tell is missing.

  evidence the quoted span, so the GM can judge the guardrail rather than trust it. A
           ledger that says "violation" without saying what was written is a metric,
           not evidence.

WHAT THIS IS DELIBERATELY NOT
============================

**No model call.** Every trip is already the result of a regex the loop ran anyway, and
the caught/narrated decision is a second pass of that same regex over the final text.
A log that needed a summariser would be a cost on every turn for a number nobody reads.

**Never fed to the DM.** GM-only, like `notes.md`. A DM briefed on its own guardrail
report learns to write to the detector, which is the same failure `notes.md` documents
for advisor notes and the same one `reply.sanitize_turns` prevents for a captured
injection. The file lives beside the transcript and is read by `/agency`, never by
`context.build_messages`.

**Not a trip-rate estimate.** It counts what the loop's own guards saw. The corpus of
shapes those guards cannot see is a separate question, measured (with denominators) in
`docs/DM-BOUNDARY-BASELINE.md`; this ledger is not evidence about those.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import threading

NAME = "agency.jsonl"

#: The forms, one per guardrail in the DM loop, named after the boundary it protects.
#:
#: `detector` is `None` for the two the engine refuses rather than corrects: there is
#: no draft to re-read, so their outcome is fixed at "refused" and nothing is measured.
FORMS = {
    "agency": "reply.speaks_for_player",
    "injection": "reply.grants_injection",
    "name-reuse": "Session.names.suspect",
    "unbacked-number": "reply.unbacked_numbers",
    "check-outcome": "reply.reveals_check_outcome",
    "unbacked-cast": "reply.states_an_unbacked_cast_result",
    "fail-forward": "reply.is_costless_failure",
    "mid-fight-check": "engine, no correction step",
    "mid-fight-cast": "engine, no correction step",
}

OUTCOMES = ("caught", "narrated", "refused", "unknown")
EVIDENCE_CHARS = 160


def _clip(text: str) -> str:
    return " ".join((text or "").split())[:EVIDENCE_CHARS]


class Ledger:
    """Append-only log of guardrail trips for one campaign.

    `turn` and `scene` are callables rather than arguments, because every call site is
    inside `Session` and already knows both; passing them nine times is how a field
    starts disagreeing with itself. They are read at trip time, not at write time, so a
    record says which turn it was on even though it is written a moment later.

    Thread-safe on the same bargain as `Memory` and `Notes`: nothing here runs on a
    background thread today, and a ledger that is not safe when the shadow advisor
    lands on it is a ledger that will be rewritten.
    """

    def __init__(self, dir_or_memory, *, turn=None, scene=None):
        self.dir = getattr(dir_or_memory, "dir", dir_or_memory)
        self._turn = turn or (lambda: 0)
        self._scene = scene or (lambda: 0)
        self._lock = threading.RLock()
        self._pending: list = []

    @property
    def path(self) -> pathlib.Path:
        return self.dir / NAME

    # ── writing ───────────────────────────────────────────────────────────────

    def trip(self, form: str, evidence: str = "", *, detector=None, arg=None) -> None:
        """A guardrail fired on the draft in hand.

        `detector` is what `settle` re-runs over the final text to decide caught from
        narrated, and `arg` is that detector's second argument when it takes one
        (`unbacked_numbers` needs the numbers the engine produced). `None` means the
        engine refused rather than corrected, so the outcome is fixed.
        """
        if form not in FORMS:
            return
        with self._lock:
            key = (self._turn(), self._scene(), form)
            self._expire((self._turn(), self._scene()))
            for rec in self._pending:
                if (rec["turn"], rec["scene"], rec["form"]) == key:
                    rec["attempts"] += 1        # the retry, not a second violation
                    if evidence:
                        rec["evidence"] = _clip(evidence)
                    return
            self._pending.append({"turn": key[0], "scene": key[1], "form": form,
                                  "attempts": 1, "detector": detector, "arg": arg,
                                  "evidence": _clip(evidence)})

    def _expire(self, where: tuple) -> None:
        """Pending trips from an EARLIER turn or scene never reached a settle.

        Scoped to the same turn and scene on purpose: the retries of one violation all
        land on the same key and must merge into one record, so expiring on every trip
        would turn one violation into one line per retry, which is the inflation the
        dedup exists to prevent.
        """
        stale = [r for r in self._pending
                 if (r["turn"], r["scene"]) != (where[0], where[1])]
        if not stale:
            return
        self._pending = [r for r in self._pending
                         if (r["turn"], r["scene"]) == (where[0], where[1])]
        self._flush_pending("unknown", pending=stale)

    def settle(self, shown: str) -> None:
        """The guarded region is over; `shown` is the text the player was shown.

        Nothing pending means nothing tripped, which is the common case and costs one
        truthiness check.

        A shown text that STILL trips its detector is one more flagged draft, and is
        counted as one: the guard ran on the retry too, and because that retry was
        dirty `Session._dm` kept the first one. So `attempts` is the number of drafts
        the guardrail flagged, which is what a GM reading "the same violation twice"
        needs to know.
        """
        with self._lock:
            self._flush_pending(None, shown)

    def _flush_pending(self, forced, shown: str = "", pending=None) -> None:
        batch = self._pending if pending is None else pending
        if not batch:
            return
        stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
        rows = []
        for rec in batch:
            detector, arg = rec.get("detector"), rec.get("arg")
            outcome = forced
            if outcome is None:
                if detector is None:
                    outcome = "refused"
                else:
                    fires = detector(shown) if arg is None else detector(shown, arg)
                    outcome = "narrated" if fires else "caught"
                    if fires:
                        rec["attempts"] = int(rec["attempts"]) + 1   # the retry too
            rows.append({"turn": rec["turn"], "scene": rec["scene"], "form": rec["form"],
                         "attempts": rec["attempts"], "outcome": outcome,
                         "evidence": rec["evidence"], "at": stamp})
        if pending is None:
            self._pending = []
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            # A ledger that cannot be written is not a reason to lose the turn. The
            # caller already reported the trip on stderr if it wanted to; this is a
            # GM-facing convenience, never a correctness path.
            pass

    # ── reading (GM only) ─────────────────────────────────────────────────────

    def entries(self) -> list:
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []
        out = []
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue                    # a torn append: skip it, keep the rest
            if isinstance(data, dict) and data.get("form") in FORMS:
                out.append(data)
        return out

    def summary(self) -> dict:
        """Counts by outcome, so a GM can see the rate without reading the log."""
        rows = self.entries()
        by = {o: 0 for o in OUTCOMES}
        for r in rows:
            by[r.get("outcome", "unknown")] = by.get(r.get("outcome", "unknown"), 0) + 1
        by["violations"] = len(rows)
        by["attempts"] = sum(int(r.get("attempts", 1)) for r in rows)
        return by

    def recent(self, limit: int = 10) -> str:
        """The last `limit` trips as markdown, oldest first. "" when there are none."""
        rows = self.entries()[-max(0, limit):] if limit > 0 else self.entries()
        if not rows:
            return ""
        out = []
        for r in rows:
            tries = (f", {r['attempts']} drafts" if int(r.get("attempts", 1)) > 1 else "")
            out.append(f"- turn {r['turn']} scene {r['scene']}: **{r['form']}** "
                       f"({r['outcome']}{tries}) [{FORMS[r['form']]}]"
                       + (f"\n    > {r['evidence']}" if r.get("evidence") else ""))
        return "\n".join(out)


def render(ledger: Ledger, limit: int = 10) -> str:
    """`/agency`: the GM-only read-back, or a line saying there is nothing."""
    s = ledger.summary()
    if not s.get("violations"):
        return (f"(No agency violations logged for this campaign yet. They are kept in "
                f"{ledger.path}.)")
    return (f"[GM agency log - last {limit} of {s['violations']} in {ledger.path}]\n"
            f"{s['caught']} caught, {s['narrated']} narrated, {s['refused']} refused by "
            f"the engine, over {s['attempts']} flagged drafts\n\n{ledger.recent(limit)}")


__all__ = ["FORMS", "OUTCOMES", "Ledger", "render"]