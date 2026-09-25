"""rules.py: the thin interface between the engine and a game system.

The engine (engine.py) owns geometry and turn flow: who is where, whose turn
it is, what the path costs, who is in reach, what blocks sight. Everything
that depends on a game's rules goes through a Rules object loaded from
systems/<system>/tactics_rules.py, which must define `RULES = <Rules subclass>()`.

The contract, grouped the way SYSTEM-PORTING.md documents it:

  attack        attack(attacker, target, attack, ctx, roller, player) -> dict
                hit_chance(attacker, target, attack, ctx) -> dict (no roll; for previews)
  save          saving_throw(token, ability, dc, roller, player, cover=0) -> dict
                save_chance(token, ability, dc, cover=0) -> dict (no roll; for previews)
  spells        spell(caster, name, level=None) -> spec dict (see systems/dnd5e/tactics_spells.py)
                ac(token) -> AC with effects (Shield); skill_bonus(token, skill) -> int
  damage        damage(target, parts, crit, ctx) -> dict ; heal(token, amount) -> dict
  conditions    can_act(token), can_react(token), death_save(token, roller, player) -> dict
  movement      speed(token), crawling(token), stand_up_cost(token), reach(token)
  economy       turn_budget(token) -> dict ; initiative(token, roller) -> Roll
                opportunity_attack(token) -> attack spec or None
  characters    token_from_sheet(path, token_id, pos), token_from_monster(name, token_id,
                display_name, pos), write_back(sheet_text, token) -> text,
                lasting_conditions(token) -> list

Result dicts carry a short `text` the CLI prints as-is. Rolls go through the
Roller, so their source (engine, player, verbal) is always recorded.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from dataclasses import dataclass

SYSTEMS_DIR = pathlib.Path(__file__).resolve().parents[2] / "systems"


@dataclass
class AttackContext:
    """Geometry the engine measured for one attack. System-neutral facts only;
    the rules decide what they mean (advantage, cover bonus, auto-crit)."""
    distance: int                 # feet between attacker and target
    melee: bool                   # made as a melee attack (vs ranged)
    cover: int = 0                # AC bonus from cover: 0, 2 or 5
    long_range: bool = False      # beyond normal range, within long range
    hostile_adjacent: bool = False  # a hostile that can act is within 5 ft of the attacker
    opportunity: bool = False     # made as a reaction to leaving reach
    react: object = None          # engine hook for reactions to a hit (Shield, Silvery Barbs):
                                  # react(natural, total, ac) -> {"natural", "total", "ac", "lines"}


class Rules:
    """Base class. Subclasses implement every method; the engine never reaches
    around this interface into system details."""
    name = "base"

    # economy
    def initiative(self, token, roller):
        raise NotImplementedError

    def turn_budget(self, token) -> dict:
        """{"movement": feet, "action": 1, "bonus": 1, "reaction": 1}"""
        raise NotImplementedError

    def opportunity_attack(self, token):
        raise NotImplementedError

    # movement
    def speed(self, token) -> int:
        raise NotImplementedError

    def crawling(self, token) -> bool:
        raise NotImplementedError

    def stand_up_cost(self, token) -> int:
        raise NotImplementedError

    def reach(self, token) -> int:
        raise NotImplementedError

    # conditions
    def can_act(self, token) -> bool:
        raise NotImplementedError

    def can_react(self, token) -> bool:
        raise NotImplementedError

    def death_save(self, token, roller, player: bool) -> dict:
        raise NotImplementedError

    # attack, save, damage
    def attack(self, attacker, target, attack: dict, ctx: AttackContext, roller,
               player: bool) -> dict:
        raise NotImplementedError

    def hit_chance(self, attacker, target, attack: dict, ctx: AttackContext) -> dict:
        """{"percent": int, "advantage": str, "reasons": [...]} without rolling.
        Shown on every option and target before the player commits."""
        raise NotImplementedError

    def saving_throw(self, token, ability: str, dc: int, roller, player: bool,
                     cover: int = 0) -> dict:
        raise NotImplementedError

    def save_chance(self, token, ability: str, dc: int, cover: int = 0) -> dict:
        """{"fail": 0..1, "percent_fail": int, "advantage": str} without rolling."""
        raise NotImplementedError

    def spell(self, caster, name: str, level: int = None) -> dict:
        """What casting `name` does, resolved for this caster and slot level.
        Raises ValueError with a message for the GM when it cannot be cast."""
        raise NotImplementedError

    def ac(self, token) -> int:
        raise NotImplementedError

    def known_spells(self, caster) -> list:
        return []

    def damage_multiplier(self, token, dtype: str) -> float:
        return 1.0

    def passive_perception(self, token) -> int:
        raise NotImplementedError

    def skill_bonus(self, token, skill: str) -> int:
        raise NotImplementedError

    def damage(self, target, parts: list, crit: bool = False, ctx: AttackContext = None) -> dict:
        """parts: [{"amount": int, "type": str}], already rolled."""
        raise NotImplementedError

    def heal(self, token, amount: int) -> dict:
        raise NotImplementedError

    # characters: where tokens come from and where results go back to
    def token_from_sheet(self, path, token_id: str, pos: tuple):
        raise NotImplementedError

    def token_from_monster(self, name: str, token_id: str, display_name: str, pos: tuple):
        raise NotImplementedError

    def write_back(self, sheet_text: str, token) -> str:
        """The sheet with combat results (HP, resources, lasting conditions) written in."""
        raise NotImplementedError

    def lasting_conditions(self, token) -> list:
        """Conditions that outlast the fight; the rest are dropped when combat ends."""
        raise NotImplementedError


def load(system: str) -> Rules:
    path = SYSTEMS_DIR / system / "tactics_rules.py"
    if not path.exists():
        raise FileNotFoundError(f"system {system!r} has no tactics_rules.py ({path})")
    name = f"tactics_rules_{system}"
    if name in sys.modules:
        mod = sys.modules[name]
    else:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    rules = getattr(mod, "RULES", None)
    if not isinstance(rules, Rules):
        raise TypeError(f"{path} must define RULES = <Rules subclass>()")
    return rules
