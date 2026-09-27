"""stall.py: in-fiction wait phrasing for long operations, subagent calls, and compaction.

Provides diegetic 1-sentence stall lines matching scene context (social, combat, exploration)
to maintain player immersion during background work.
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional

STALL_LINES: Dict[str, List[str]] = {
    "social": [
        "The innkeeper pauses, rubbing his thumb along the rim of a tarnished goblet while considering your words.",
        "The commander leans over the parchment, her eyes narrowing as she weighs her response.",
        "An awkward silence falls over the tavern table as eyes shift back and forth.",
        "The merchant strokes his beard thoughtfully, tapping one coin against another.",
        "The scholar pauses with pen suspended over the parchment, deep in thought.",
    ],
    "combat": [
        "Steel glints in the torchlight as the goblin chief circles slowly, searching for an opening.",
        "Shadows lengthen across the bloodstained stone while rain rattles against the iron portcullis.",
        "A heavy silence settles briefly over the battlefield as adversaries catch their breath.",
        "Dust swirls around your feet as cold wind cuts through the courtyard between clashes.",
        "The creature snarls softly, muscles tensing as it re-evaluates the distance between you.",
    ],
    "exploration": [
        "A sudden draft flickers the torch flame, casting long shadows across the ancient runes.",
        "The distant drip of water echoes through the damp corridor as you study the locked doorway.",
        "Old timbers groan overhead in the cavernous room while you inspect your surroundings.",
        "Cold subterranean air sweeps past, carrying the faint smell of ozone and damp earth.",
        "Leaves rustle softly in the canopy above as a cool evening breeze filters through the trees.",
    ],
}


def get_stall_line(context: str = "social", seed: Optional[int] = None) -> str:
    """Return a single 1-sentence scene-appropriate in-fiction stall line."""
    lines = STALL_LINES.get(context, STALL_LINES["social"])
    if seed is not None:
        rng = random.Random(seed)
        return rng.choice(lines)
    return random.choice(lines)
