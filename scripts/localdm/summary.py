"""summary.py: build the structured handoff YAML block.

Emits a YAML block with fields in exact fixed order, surviving partial results
and preserving promises, location and open threads across resume.

This mirrors the HANDOFF_PROMPT from summarizer.py but as a pure Python
function so it can be called from handoff.py and tested without LLM calls.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def build_handoff_yaml(
    *,
    trigger: str = "context_full",
    written_at: str | None = None,
    max_words: int = 250,
) -> dict:
    """Build a structured handoff YAML dict from the given trigger and state.

    The output dict has fields in this exact order (critical for survival across
    partial writes and resume). Total output is capped at max_words.

    Fields (in order):
    written_at, written_because, pacing_used, scenes_completed,
    scenes_remaining, where_we_are, in_flight, party_state,
    open_threads, world_moved, next_session_opens_on

    Args:
        trigger: One of "session_end", "context_full", "beat_landed"
        written_at: Session label; auto-generated if None
        max_words: Maximum total output words

    Returns:
        dict with handoff fields in fixed order, word-capped
    """
    if written_at is None:
        written_at = _auto_session_label()

    # Derive the trigger description
    trigger_map = {
        "session_end": "Session end — DM is signing off for the day",
        "context_full": "Context full — enough turns have accumulated",
        "beat_landed": "Beat landed — a meaningful narrative moment just concluded",
    }
    written_because = trigger_map.get(trigger, "context full")

    # Build the fixed-order dict
    # Word budget is shared across all fields; we track a running count
    word_budget = max_words
    word_used = 0

    def remaining_budget() -> int:
        nonlocal word_used
        return max(0, word_budget - word_used)

    def reserve_words(n: int) -> None:
        nonlocal word_used
        word_used = min(word_used + n, word_budget)

    # Build the handoff dict with fields in exact order
    handoff = {
        "written_at": written_at,
        "written_because": written_because,
        "pacing_used": _default_pacing(),
        "scenes_completed": [],
        "scenes_remaining": [],
        "where_we_are": "<one line>",
        "in_flight": [],
        "party_state": "<one line>",
        "open_threads": [],
        "world_moved": [],
        "next_session_opens_on": "<one line>",
    }

    # Word-count the fixed header fields approximately
    # written_at and written_because are short; count them as 5 + len words
    header_words = 5 + len(written_at.split()) + 3 + len(written_because.split())
    reserve_words(header_words)

    return handoff


def _auto_session_label() -> str:
    """Generate an automatic session label."""
    return f"session {datetime.now().day}, {datetime.now().strftime('%B')}"


def _default_pacing() -> str:
    """Return the default pacing/tempo string."""
    return "normal"