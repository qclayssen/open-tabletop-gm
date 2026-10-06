"""What a play session may propose for `graph.json`, and what it must not.

WHY A SESSION MAY WRITE NOTHING BY ITSELF
=========================================
#289 asked for a play session to update the graph. It did not ask for one to
auto-apply, and the difference is the whole design: a session writes
*proposals*, `gm_graph.apply_proposals` is the only thing that writes a graph,
and the GM reviews before anything lands. `Extractor.extract()` already runs
mid-session on a background thread and verifies each turn against itself, so the
signal exists and is pinned; this module turns that signal into proposals and
nothing else. It never opens `graph.json`.

THE THREE THINGS THAT HAD TO BE SETTLED FIRST
=============================================
Measured against `origin/main` before any of this was written, because each one
would otherwise have been a silent no-op:

1. `summary` is the only per-node field the turn path renders. `play.py:522-526`
   prints `name` and `summary` and ignores `type`, `tags` and every edge. So
   this proposes summaries. An edge would have been invisible.

2. `since_session` is inert on this read path. `play.py` never passes
   `at_session` (`grep -c at_session` is 0), and `_edge_active_at` ignores
   `since_session` when the session is `None`. So proposals carry provenance
   without pretending to be time-scoped. Making them time-scoped is a behaviour
   change to T2.5's pinned wiring and is filed separately.

3. The graph is GM-only. `player-view.js` never reads it and no HTTP route
   serves it. That reframes the redaction criterion, below.

REDACTION, WHICH #289 CALLED THE LOAD-BEARING CONSTRAINT
========================================================
The stated worry was that an edge written from live play "must not make an
off-screen creature's state public retroactively". Measured, that path does not
exist: a canon record's text is a verbatim span of the DM's own narration, and
`Extractor.extract()` reads only turns with `role == "dm"` (`canon.py:313`) --
narration the players were shown. Canon is public text by construction, so a
graph built from it inherits that. `sight.redact_log` is not the relevant
mechanism here at all: it protects the combat log, and a graph node is not a
combat-log entry.

The risk that IS real is a laundering risk rather than a visibility one. The
graph feeds the DM's context and the DM narrates what it is given, so a number
in a node summary arrives next turn as something the DM did not observe, while
its real provenance is narration -- and narration numbers are the DM's own
invention. `play.py` already refuses exactly this in `_backing()`: "a number the
DM invented last turn is in them, and backing a draft with them would launder
last turn's fabrication into this turn's fact."

NO REGEX, AND WHY -- THE THREE COUNTEREXAMPLES
==============================================
The obvious implementation of that is a pattern that strips numerals next to
game-state words. It was written, measured, and deleted. Against the pattern
`NUMERAL + state word`:

    "Velkyn has 3 / 9 hp left and AC 17."
        -> kept "Velkyn has 3 / left and"    dangling "3 /", and 9 hp removed
    "He rolled a 20 to hit for 8 damage."
        -> kept "a 20 to hit for"            the roll itself survived
    "Twelve of the garrison lay dead by dawn."
        -> KEPT ENTIRELY                    the claim was missed outright

The third is the disqualifying one. Requiring the numeral to sit next to a state
word misses any claim with a noun phrase between them, and loosening it to catch
those starts eating "the third sigil" and "nine days". There is no threshold
that separates them, because the distinction is not lexical -- it is whether the
number was produced by the engine, which the text does not record.

So the safeguard is structural instead of lexical, and none of it pretends to
detect a number:

  * the GM reviews every proposal before anything is written, and sees the span
    verbatim -- so the judgement is made by the one party who can make it;
  * a session-written summary always carries `summary_source`, which marks it
    provisional in the file itself rather than in a reader's memory;
  * `_apply_node_summary` never overwrites a summary the GM wrote, so the graph
    cannot be rewritten under them;
  * the summary is one bounded sentence, because the thing being protected is a
    prompt budget as much as a fact.

What a reader must not conclude from the absence of a scrubber: numbers are NOT
filtered out of session-written summaries. They are surfaced to the GM, which is
the only gate that works on this input.
"""
from __future__ import annotations

import re

#: A node summary is one sentence and this many characters at most. Bounded
#: because the graph feeds the DM's context on every turn: a paragraph of
#: narration quoted back as graph state is a prompt-budget bug that looks like a
#: feature, and a graph node is not the place for a retelling.
MAX_SUMMARY_CHARS = 240

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def summarize(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """One bounded sentence of `text`, whitespace-collapsed.

    First sentence rather than a "most informative" span, because picking the
    informative part is a judgement that needs the model's help, and this runs
    with no model and no review of its own. Truncation is marked with an
    ellipsis so a summary that was cut is visibly cut rather than looking
    complete.
    """
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    parts = _SENTENCE_END.split(flat, 1)
    first = parts[0].strip()
    if len(first) > limit:
        return first[:limit].rstrip() + "..."
    return first


def _summary_worthy(text: str) -> bool:
    """Is there a sentence here worth putting in a graph?

    A node whose summary is empty renders as a bare name in the DM's digest,
    which reads as the graph asserting this entity has nothing to say. Better to
    have proposed nothing.
    """
    return len(text.split()) >= 4


def proposals_from_canon(records: list) -> list:
    """Canon records -> `node_summary` proposals for `gm_graph.apply_proposals`.

    Only `reveal` records are proposed. The other kinds name nothing the graph
    can hold: `dialogue` and `interaction` are attributed to a speaker and say
    the speaker was present, which is a fact about a scene rather than about an
    entity, and `death` is state the engine already owns. A `reveal` carries a
    `key` -- a slug of what was revealed -- which is exactly a node.

    One proposal per key, keeping the FIRST span. The introduction is what a
    graph is for, and a later restatement would silently overwrite it.

    Returns `[]` rather than raising when nothing survives. A session that
    discovers nothing graph-shaped is the normal case, not an error, and this
    runs on a background thread.
    """
    proposals = []
    seen = set()
    for record in records:
        if record.get("kind") != "reveal":
            continue
        key = (record.get("key") or "").strip()
        if not key or key in seen:
            continue
        summary = summarize(record.get("text") or "")
        if not _summary_worthy(summary):
            continue
        seen.add(key)
        proposals.append({
            "kind": "node_summary",
            "to": key,
            "summary": summary,
            "confidence": "medium",
            "source": {
                "origin": "canon",
                "turn": record.get("turn"),
                "kind": record.get("kind"),
                "speaker": record.get("speaker") or "",
                # The verbatim span, not the summary derived from it. The
                # summary is bounded and possibly truncated; this is not, so it
                # is what a GM reviewing the proposal is actually checking.
                "anchor": (record.get("text") or "").strip(),
            },
        })
    return proposals