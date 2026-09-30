"""stall.py: in-fiction wait phrasing for long operations, subagent calls, and compaction.

Provides diegetic 1-sentence stall lines for a wait the player is watching.

The rule every line here obeys: assert nothing the scene has not established.
No named place, no named NPC, no weather, no furniture, no specific terrain. A
line may name the character ("you"), the thing in front of them, and the mood;
nothing else. A stall line lands in a library as often as in a tavern, and the
one thing it must not do is move the character somewhere the DM did not put them.

That leaves one honest problem: the call sites know almost nothing. Both of them
compute "combat is running" or it is not, so anything the player sees is keyed on
that alone. NEUTRAL_LINES is the pool for a scene the caller cannot describe, and
it is deliberately the smallest one, because a line with fewer claims is a line
that cannot contradict the scene.
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional

# The scene is unknown: the character waits, and that is all the line claims.
# These are the default for every non-combat call, which is most of them.
NEUTRAL_LINES: List[str] = [
    "A moment passes, and the moment is not empty: something close by is unfinished.",
    "The weight of the next moment settles on you, unhurried and unavoidable.",
    "You hold still, and the silence has room in it.",
    "Nothing hurries you. The moment stays open, and what you do with it is yours.",
    "You take the time to breathe, and let it be that and no more.",
]

STALL_LINES: Dict[str, List[str]] = {
    # "social" is what the callers actually have: combat is not running, which
    # says nothing about where the character is. It is the neutral pool, not a
    # tavern, so "social" stays a safe key for an unknown scene.
    "social": NEUTRAL_LINES,
    "neutral": NEUTRAL_LINES,
    "combat": [
        "Steel finds its distance, and no one in the fight spends the gap for nothing.",
        "A heavy silence settles over the field as your adversaries catch their breath.",
        "The moment hardens: every one of them is measuring you the same way you are measuring them.",
        "Something snarls low and thoughtful, a creature deciding what it is willing to risk.",
        "You hold your ground, and the ground holds with you.",
    ],
    "exploration": [
        "A draft moves through, carrying whatever this place has carried all along.",
        "Somewhere out of sight, something makes its small sound and falls quiet again.",
        "The air shifts, and the light with it, and nothing yet demands a decision.",
        "You look, and looking takes longer than you expected, and longer is fine.",
        "The quiet of the place settles over you, unasked and unanswering.",
    ],
}

# What a caller that has no scene information passes. The default argument of
# get_stall_line() stays "social" for backward compatibility, and both keys hold
# the same neutral pool, so neither one can reintroduce a place the DM never
# said was there.
NEUTRAL_CONTEXT = "neutral"


def get_stall_line(context: str = "social", seed: Optional[int] = None) -> str:
    """Return a single 1-sentence in-fiction stall line for the wait.

    `context` is best-effort: "combat" only when the Engine reports a fight, and
    NEUTRAL_CONTEXT (or anything unrecognized) when the caller knows nothing. An
    unknown context is never treated as a social one in the prose sense, only as
    a key into the neutral pool.
    """
    lines = STALL_LINES.get(context) or NEUTRAL_LINES
    if seed is not None:
        rng = random.Random(seed)
        return rng.choice(lines)
    return random.choice(lines)
