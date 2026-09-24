"""tactics: deterministic grid combat engine.

The engine owns the rules. The LLM only narrates and chooses. Every position,
distance, hit point and die roll in a grid fight is decided here; the GM calls
a command, reads a short result and narrates it.

Modules:
    grid     5 ft squares, terrain, movement cost, pathing, line of sight, cover
    state    the encounter file (<campaign>/combat/encounter.json)
    roller   dice, reusing scripts/dice.py's notation parser; tags every roll's source
    rules    thin interface to a game system's combat rules (see SYSTEM-PORTING.md)
    engine   turn flow: initiative, movement, attacks, reactions, action economy

System-specific rules live in systems/<system>/tactics_rules.py.
"""

import pathlib
import sys

# scripts/ holds dice.py and paths.py, which the engine reuses.
_SCRIPTS = str(pathlib.Path(__file__).resolve().parent.parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
