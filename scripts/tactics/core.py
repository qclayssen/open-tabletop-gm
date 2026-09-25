"""core.py: helpers shared by every engine module (engine, effects, spells, actions).

Kept apart so the modules that build on the engine can use them without
importing each other in a circle. engine.py re-exports all of them, so
`engine.CombatError` and friends keep working.
"""

from __future__ import annotations

from . import rules as rules_mod
from .roller import Roller
from .state import Encounter

_RULES_CACHE = {}


class CombatError(Exception):
    """An illegal command. The message is safe to show the GM as-is."""


class DecisionNeeded(Exception):
    """A player-controlled creature must choose before the action can resolve.

    key names the decision in the `reactions` dict the caller passes back:
    the reacting token's id for an opportunity attack, "<id>:shield" or
    "<id>:silvery barbs" for a spell reaction."""

    def __init__(self, who: str, kind: str, prompt: str, options: list, key: str = None):
        self.who, self.kind, self.prompt, self.options = who, kind, prompt, options
        self.key = key or who
        super().__init__(prompt)

    def to_dict(self) -> dict:
        return {"who": self.who, "kind": self.kind, "prompt": self.prompt,
                "options": self.options, "key": self.key}


def rules_for(enc: Encounter):
    if enc.system not in _RULES_CACHE:
        _RULES_CACHE[enc.system] = rules_mod.load(enc.system)
    return _RULES_CACHE[enc.system]


def hostile(a, b) -> bool:
    friends = {"pc", "ally"}
    if a.side == "neutral" or b.side == "neutral" or a.id == b.id:
        return False
    return (a.side in friends) != (b.side in friends)


def player_rolls(enc: Encounter, token, roller: Roller = None) -> bool:
    """Does a human roll this token's dice right now?"""
    return token.controller == "player" and enc.roll_mode == "players"


def log(enc: Encounter, kind: str, actor: str, text: str, roller: Roller = None, mark: int = 0) -> None:
    rolls = [r.to_dict() for r in roller.log[mark:]] if roller else []
    enc.log.append({"round": enc.round, "actor": actor, "kind": kind, "text": text, "rolls": rolls})


def resolve(enc: Encounter, ref):
    try:
        return enc.token(ref) if isinstance(ref, str) else ref
    except KeyError as e:
        raise CombatError(str(e.args[0])) from None


def decide(token, key: str, kind: str, prompt: str, reactions: dict) -> bool:
    """A reaction choice for `token`. Its `reactions` setting decides first:
    "off" never, "auto" always; "ask" (the default) asks a player-controlled
    creature through DecisionNeeded unless the answer is already in
    `reactions`. GM creatures always take a useful reaction."""
    setting = getattr(token, "reactions", "ask")
    if setting == "off":
        return False
    if key in (reactions or {}):
        return bool(reactions[key])
    if token.controller != "player" or setting == "auto":
        return True
    raise DecisionNeeded(token.id, kind, prompt, ["yes", "no"], key=key)
