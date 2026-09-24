"""roller.py: dice for the engine, with every roll's source on record.

Notation is parsed by scripts/dice.py (the same parser /gm roll uses); the
engine only adds a seedable random source and the bookkeeping it needs:

  source "engine"   rolled here (NPCs, initiative, roll_mode auto, "Roll for me")
  source "player"   a value the player rolled, supplied through the display
  source "verbal"   a value supplied on the command line (--roll N)

Supplied values are NATURAL dice totals, before modifiers: the d20 face kept
after advantage or disadvantage, or the sum of the damage dice. The engine adds
the modifier, so crits and nat 1s are always detected from the die itself.

When a player-controlled roll is needed under roll_mode "players" and no value
was supplied, PendingRoll is raised. Callers work on a copy of the encounter
and discard it, so nothing is half-applied; the CLI then asks for the roll.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import dice as _dice   # scripts/dice.py (put on sys.path by tactics/__init__)


class PendingRoll(Exception):
    """A player must roll before the action can resolve."""

    def __init__(self, who: str, label: str, notation: str, advantage: str = "normal"):
        self.who, self.label, self.notation, self.advantage = who, label, notation, advantage
        super().__init__(f"{who} must roll {notation} ({label})")

    def to_dict(self) -> dict:
        return {"who": self.who, "label": self.label, "notation": self.notation,
                "advantage": self.advantage}


def parse(notation: str) -> tuple:
    """(count, sides, modifier). Plain integers ("1") are flat damage."""
    text = str(notation).replace(" ", "").lower()
    if text.lstrip("+-").isdigit():
        return 0, 0, int(text)
    count, sides, mod, keep_mode, _keep, adv, dis = _dice.parse_notation(text)
    if keep_mode or adv or dis:
        raise ValueError(f"engine notation must be plain NdS+M: {notation!r}")
    return count, sides, mod


def average(notation: str) -> float:
    """Mean result of plain NdS+M notation (or a flat number)."""
    n, sides, mod = parse(notation)
    return n * (sides + 1) / 2 + mod


@dataclass
class Roll:
    who: str
    label: str
    notation: str
    dice: list
    natural: int         # sum of kept dice, no modifier
    total: int
    source: str
    advantage: str = "normal"

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Roller:
    """rng: random.Random for engine rolls. supplied: natural values for player
    rolls, consumed in order. supplied_source: "player" or "verbal"."""
    rng: random.Random = field(default_factory=random.Random)
    supplied: list = field(default_factory=list)
    supplied_source: str = "verbal"
    for_me: bool = False      # "Roll for me": the engine rolls a player's dice this time
    log: list = field(default_factory=list)

    def roll(self, notation: str, who: str, label: str, player: bool = False,
             advantage: str = "normal", crit: bool = False) -> Roll:
        """Roll notation. player=True means the player rolls this one (roll_mode
        players, not "Roll for me"). advantage applies to a single d20.
        crit doubles the dice, not the modifier."""
        count, sides, mod = parse(notation)
        if crit:
            count *= 2
        shown = f"{count}d{sides}{mod:+d}" if count else str(mod)
        if shown.endswith("+0"):
            shown = shown[:-2]
        if not count:                        # flat damage: nothing to roll
            rec = Roll(who, label, shown, [], 0, mod, "fixed", advantage)
        elif player:
            if not self.supplied:
                raise PendingRoll(who, label, shown, advantage)
            natural = int(self.supplied.pop(0))
            lo, hi = count, count * sides
            if count and not lo <= natural <= hi:
                raise ValueError(f"{natural} is not a possible {count}d{sides} roll")
            rec = Roll(who, label, shown, [natural], natural, natural + mod,
                       self.supplied_source, advantage)
        else:
            faces = [self.rng.randint(1, sides) for _ in range(count)]
            natural = sum(faces)
            if advantage != "normal" and count == 1 and sides == 20:
                other = self.rng.randint(1, 20)
                faces = [faces[0], other]
                natural = max(faces) if advantage == "advantage" else min(faces)
            rec = Roll(who, label, shown, faces, natural, natural + mod, "engine", advantage)
        self.log.append(rec)
        return rec
