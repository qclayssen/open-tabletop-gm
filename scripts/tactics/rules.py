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
  design        encounter_budget(levels, ruleset) -> dict: what a party can be handed
                rate_encounter(groups, levels, ruleset) -> dict: what a monster list
                costs ; award_xp(sheet_path, amount) -> dict ; record_awards(campaign_dir,
                entries, note) — so a finished fight feeds the campaign's own XP ledger

Result dicts carry a short `text` the CLI prints as-is. Rolls go through the
Roller, so their source (engine, player, verbal) is always recorded.

Odds travel with the roll, not beside it. A preview is the wrong moment to
show a chance: a player doubts a roll after seeing it. So a system that has a
number for a check sets `roll.odds` on the `Roll` its attack() or
saving_throw() just made, reusing hit_chance()/save_chance() rather than
recomputing (one function, so the pre-action badge and the number printed next
to the result cannot disagree). `Roll.odds` is system-neutral and optional: the
engine and the display only read it. It lives on the roll rather than on the
log entry beside it because sight.redact_log drops `rolls` wholesale for a
creature the players cannot see, and a field alongside would have survived that
and given its armor class away.
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
    source_in_sight: bool = True  # whatever is frightening or charming one of them
                                  # can currently be seen by the other
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
    def condition_modifiers(self, token) -> dict:
        """Everything this token's conditions change, merged into one dict.

        The keys are always present, so a caller never has to tell "this condition
        grants nothing" from "nobody asked": `attack_roll`, `ability_check`, `save`,
        `attack_against`, `movement`, `action_economy`. A value is "adv"/"dis", a
        gate dict ({"ranged": "dis"} — only applied when the gate holds for this
        roll), a per-ability dict, a speed, or None.

        Systems are expected to keep one table for this, as
        systems/dnd5e/tactics_rules.py does, and derive their condition sets from
        it: three hand-kept lists of conditions is how a condition ends up
        helping the creature it was meant to be hurting.
        """
        raise NotImplementedError

    def condition_notes(self, token) -> list:
        """What the token's conditions are currently doing, one line each, for
        the GM to read after applying one."""
        raise NotImplementedError

    def set_condition(self, token, condition: str) -> list:
        """Add a condition and apply what the rules make immediate rather than
        per-roll (a halved hit point maximum, a death). Returns GM-facing lines."""
        raise NotImplementedError

    def clear_condition(self, token, condition: str) -> list:
        """Remove a condition and undo what setting it applied."""
        raise NotImplementedError

    def can_act(self, token) -> bool:
        raise NotImplementedError

    def can_react(self, token) -> bool:
        raise NotImplementedError

    def death_save(self, token, roller, player: bool) -> dict:
        raise NotImplementedError

    # attack, save, damage
    def attack(self, attacker, target, attack: dict, ctx: AttackContext, roller,
               player: bool, explicit: str = "normal") -> dict:
        """`explicit` is "advantage" or "disadvantage" when the GM ruled one for
        this attack. It outranks every condition, in both directions."""
        raise NotImplementedError

    def hit_chance(self, attacker, target, attack: dict, ctx: AttackContext,
                   explicit: str = "normal") -> dict:
        """{"percent": int, "advantage": str, "reasons": [...]} without rolling.
        Shown on every option and target before the player commits, and the
        source of the odds attack() puts on the roll it makes."""
        raise NotImplementedError

    def saving_throw(self, token, ability: str, dc: int, roller, player: bool,
                     cover: int = 0, explicit: str = "normal") -> dict:
        raise NotImplementedError

    def save_chance(self, token, ability: str, dc: int, cover: int = 0,
                    explicit: str = "normal") -> dict:
        """{"fail": 0..1, "percent_fail": int, "advantage": str} without rolling.
        The source of the odds saving_throw() puts on the roll it makes."""
        raise NotImplementedError

    def ability_check(self, token, name: str, dc: int, roller, player: bool,
                      explicit: str = "normal", sense: str = "", other=None,
                      source_in_sight: bool = True) -> dict:
        """A skill or ability check with the conditions that touch it. `name` is a
        skill or an ability; `sense` says what the check depends on, which is what
        blind and deaf turn on; `other` is the creature it is being made about,
        which is what a charm turns on."""
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

    # encounter design: what a party can be handed, and what a fight just cost
    def encounter_budget(self, levels: list, ruleset: str = "") -> dict:
        """Thresholds for a party, from their sheets' levels.

        Returns the tiers, the per-character and party figures, and whatever
        modifiers the system applies for the size of the opposition. No monsters:
        this is what to spend, not what has been spent.
        """
        raise NotImplementedError

    def rate_encounter(self, groups: list, levels: list, ruleset: str = "",
                       known: dict = None) -> dict:
        """Cost a monster list to a party. `groups` is [(name, count), ...].

        `known` maps a lowercased name to a record the caller already holds (the
        CR and XP a token was built with), so costing a fight that has already
        been fought does not go looking for its monsters a second time.

        Returns the per-monster rows, the arithmetic (raw, any multiplier, the
        adjusted total, the per-character share) and the difficulty tier, so the
        GM can check the number instead of trusting it. An unknown monster name
        raises ValueError with a message the GM can act on.
        """
        raise NotImplementedError

    def award_xp(self, sheet_path, amount: int) -> dict:
        """Add XP to one character sheet, in place.

        Returns {"awarded", "total_after", "level", "leveled", "next"}.
        `total_after` is None when the sheet does not track XP at all (a
        campaign levelling by milestone) — which is a different situation from a
        character at zero, and the caller has to be able to tell them apart.
        """
        raise NotImplementedError

    def record_awards(self, campaign_dir, entries: list, note: str = "") -> None:
        """Append awards to the campaign's XP ledger, if it keeps one.

        Only for awards that landed. A ledger that claims an award no sheet
        reflects is worse than no ledger, because `check` then reports drift the
        GM did not cause.
        """
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
