#!/usr/bin/env python3
"""combat.py: the GM's one-call commands for grid combat.

    python3 scripts/tactics/combat.py -c <campaign> <command> [args] [--roll N] [--json]

Setup and flow
    start <map> --pc NAME@SQ [--pc ...] --monster "SRD NAME@SQ" [...] [--ally "SRD NAME@SQ"]
    status                         whose turn, positions, HP
    options <token>                numbered choices for a GM-controlled creature
    choose <token> <n>             run option n
    end-turn                       next creature in initiative
    end                            finish: write sheets, tracker, session log

Actions (the current creature)
    move <token> <square>          e.g. move kairos D5   (preview <token> <square> checks first)
    attack <token> <target> [attack name]
    dash | disengage | dodge | stand <token>
    death-save <token>
    undo-move                      take back the last move this turn
    condition <token> add|remove <condition>     GM ruling (e.g. a grapple rider)
    adjust <token> hp=N temp_hp=N ac=N           GM correction
    log [n]                        last n combat log lines
    reachable <token>              squares reachable walking and with Dash (for the display)

Dice. Under roll_mode "players" a player's roll is asked for, never invented:
the command stops (exit code 2, nothing changed) and says what to roll.
Re-run the same command with --roll <natural result> (the die face, or the
dice total, without modifiers); repeat --roll for several rolls. --for-me lets
the engine roll this time. --react yes|no answers "take the opportunity attack?".

Output is 1 to 4 plain lines for the GM; --json prints the full result.
"""

import pathlib
import sys

# Import the package, not this folder: scripts/combat.py (the initiative
# tracker) would otherwise shadow a module named "combat".
sys.path[:] = [p for p in sys.path if pathlib.Path(p or ".").resolve() != pathlib.Path(__file__).resolve().parent]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tactics.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
