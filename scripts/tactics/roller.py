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


class BadFace(ValueError):
    """A supplied value is not a face the dice can show (15 for a 1d10).

    Its own class, not a bare ValueError, so cli.main can refuse the command
    without also swallowing the ValueErrors that mean a real bug somewhere
    below (a bad monster record, an unparseable square).
    """

    def __init__(self, natural: int, count: int, sides: int):
        self.natural, self.count, self.sides = natural, count, sides
        super().__init__(f"{natural} is not a possible {count}d{sides} roll")

    @property
    def legal(self) -> str:
        """The range the player should have answered in."""
        return f"{self.count}-{self.count * self.sides}"


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
    # The odds this roll was made under, in the system's own words. A player
    # doubts a roll after seeing it, not before choosing it, so the number the
    # system already computed for the preview is carried onto the resolved roll
    # here, where the display can put it next to the total.
    #
    # The system fills this in at the moment it rolls (dnd5e puts hit_chance's
    # and save_chance's answer on the roll); the engine and the display only
    # read it. Expected keys: `percent` (int), `label` (what the number is
    # about, e.g. "to hit"), `about` (the id of the token it is about, for
    # drawing it) and `advantage`. None means the system has no odds for this
    # kind of roll, which is a normal answer, not an error.
    #
    # It lives ON the roll rather than beside it in the log entry on purpose:
    # sight.redact_log drops `rolls` wholesale for an entry that names a
    # creature the players cannot see, and a field alongside would survive the
    # redaction and give that creature's AC away. See sight.py.
    odds: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Roller:
    """rng: random.Random for engine rolls. supplied: natural values for player
    rolls, consumed in order. supplied_source: "player" or "verbal"."""
    rng: random.Random = field(default_factory=random.Random)
    supplied: list = field(default_factory=list)
    supplied_source: str = "verbal"
    for_me: bool = False      # "Roll for me": the engine rolls whatever the player has not supplied
    log: list = field(default_factory=list)
    # state_fn: optional callable returning a fingerprint of the fight as it
    # stands. Set by the CLI, which has the encounter loaded before the dice
    # are rolled, so receipts.py can say which state each roll was rolled
    # against. `states` collects one entry per entry of `log`, in order.
    state_fn: object = None
    states: list = field(default_factory=list)

    def _fingerprint(self) -> str:
        """state_fn's answer, or "" when there is nothing to hash. Never raises:
        a receipt is a witness, not a precondition for a fight."""
        if self.state_fn is None:
            return ""
        try:
            return str(self.state_fn() or "")
        except Exception:                               # noqa: BLE001
            return ""

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
        if player and not self.supplied and self.for_me:
            player = False                   # "Roll for me": the engine rolls what is still missing
        if not count:                        # flat damage: nothing to roll
            rec = Roll(who, label, shown, [], 0, mod, "fixed", advantage)
        elif player:
            if not self.supplied:
                raise PendingRoll(who, label, shown, advantage)
            natural = int(self.supplied.pop(0))
            lo, hi = count, count * sides
            if count and not lo <= natural <= hi:
                raise BadFace(natural, count, sides)
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
        self.states.append(self._fingerprint())
        return rec
