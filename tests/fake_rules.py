"""FakeRules: a second game system, in about a hundred lines, to prove the interface.

Support module for `tests/test_rules_conformance.py`. Not product code and not a port:
this is the toy system SPEC-combat-phases-6-7.md 7.1 asks for, "a 30-line FakeRules:
d20-flat, 10 HP, no conditions", and its whole job is to be a `systems/*/tactics_rules.py`
that is not 5e, so the engine has to survive meeting it.

WHY IT EXISTS, AND WHAT IT IS NOT
=================================

The audit that produced this brief noted 5e leakage in the engine by inspection: Dash,
Disengage and Dodge in `engine.py`; Shield and Silvery Barbs in `effects.py`; spell slots
in `slots.py`. Static review names suspects; it does not prove them. A second system
does, cheaply, and the coupling it finds is a fact about the engine rather than an
opinion about its source.

So the rule this module follows is: **be a real second system, not a mock.** It answers
every method the contract requires with the simplest thing that is a defensible answer,
and nothing more. There is no condition system, no spellcasting, no death save, no
reactions. If the engine cannot run a fight without any of those, that is the coupling,
and the honest response is to record it rather than to grow the fake until it passes.

WHAT "D20-FLAT" MEANS HERE
==========================

  - one die, always d20, always against the target's AC
  - 10 HP, AC 12, 5 ft reach, 30 ft of movement
  - every attack does 1d6, no critical rule, no damage type
  - no conditions at all: `condition_modifiers` returns the keys and nothing else
  - a save is a d20 against the DC, and `can_act` / `can_react` are always true

The one thing it is not allowed to do is lie. `hit_chance` and `attack` must agree, or
`preview equals deterministic resolution` cannot be checked: that invariant is the most
valuable thing a second system buys, and a fake that cheated it would prove nothing.

THE ONE THING IT HAD TO BE TOLD TWICE
====================================

An early version of `attack` here rolled the damage and returned the parts without
applying them, because `Rules.damage` is documented as `damage(target, parts, crit, ctx)`
over "parts: already rolled" and that reads like the engine applies it. The fight then ran
and dealt no damage at all: `engine._resolve_attack` hands `res["damage"]` straight to
`fx.after_damage` and never calls `R.damage` on the attack path, so a ruleset written to
the letter of the interface docstring loses every hit. SYSTEM-PORTING.md's own method table
has always said "applies damage on a hit"; `tactics/rules.py` did not. That gap is the
conformance suite's first finding, and it is now closed in the interface docstring, so the
rule below is not folklore:

  `Rules.attack` applies the damage itself, by calling its own `damage`, and returns that
  result dict under `damage`. `fx.after_damage` reads `concentration_dc` off it, so the
  return value has to be the dict `damage` returned and not the rolled parts.

INSTALLING IT
=============

`rules.load` reads `systems/<system>/tactics_rules.py` from disk, so the fake is
registered in `core._RULES_CACHE` under the system name `fake` and an encounter with
`system="fake"` resolves to it. That is the same path a real port takes, which is the
point: nothing in the engine knows the difference.
"""
from __future__ import annotations

import sys as _sys

from tactics import rules as rules_mod
from tactics.core import _RULES_CACHE
from tactics.roller import Roll
from tactics.rules import AttackContext, Rules

SYSTEM = "fake"

#: The four bonuses an attack can carry, and the modifier each implies. Kept as data
#: rather than arithmetic so the arithmetic stays the engine's job, not the fake's.
_FLAT = 3          # every FakeRules attack rolls +3: a fair fight, no optimisation


def _mod(score: int) -> int:
    return (score - 10) // 2


class FakeRules(Rules):
    """A ruleset with no spells, no conditions and no crit rule."""

    name = SYSTEM

    # ── economy ────────────────────────────────────────────────────────────────

    def initiative(self, token, roller):
        """d20, high to first. No tiebreak rule, because there is nothing to break."""
        d = roller.roll("1d20", token.name, "initiative")
        return Roll(token.name, "initiative", "1d20", [d.natural], d.natural, d.total,
                    roller.supplied_source)

    def turn_budget(self, token) -> dict:
        return {"movement": self.speed(token), "action": 1, "bonus": 0, "reaction": 1}

    def opportunity_attack(self, token):
        return {"name": "Club", "type": "melee", "source": "weapon", "bonus": _FLAT,
                "damage": [{"dice": "1d6", "type": "blunt"}], "reach": 5, "flags": []}

    # ── movement ───────────────────────────────────────────────────────────────

    def speed(self, token) -> int:
        return 30

    def crawling(self, token) -> bool:
        return False

    def stand_up_cost(self, token) -> int:
        return 5

    def reach(self, token) -> int:
        return 5

    # ── conditions: none, and the keys are always present ──────────────────────

    def condition_modifiers(self, token) -> dict:
        return {"attack_roll": None, "ability_check": None, "save": None,
                "attack_against": None, "movement": None, "action_economy": None}

    def condition_notes(self, token) -> list:
        return []

    def set_condition(self, token, condition: str) -> list:
        return [f"{token.name} ignores {condition!r}: FakeRules has no conditions."]

    def clear_condition(self, token, condition: str) -> list:
        return []

    def can_act(self, token) -> bool:
        return True

    def can_react(self, token) -> bool:
        return True

    def death_save(self, token, roller, player: bool) -> dict:
        """0 HP is simply out. No death saves, no stabilising, no three turns."""
        d = roller.roll("1d20", token.name, "death save")
        return {"success": True, "text": f"{token.name} has no death saves: they are out.",
                "roll": d}

    # ── attack, save, damage ───────────────────────────────────────────────────

    def hit_chance(self, attacker, target, attack: dict, ctx: AttackContext,
                   explicit: str = "normal") -> dict:
        """Natural 1 misses, natural 20 hits, otherwise roll +3 against the AC.

        Deliberately hand-rolled rather than built from a table, so it cannot agree with
        `attack` by accident. Two independent implementations of one rule is the only
        way the "preview equals resolution" invariant can be checked rather than
        asserted.
        """
        ac = target.ac
        hits = sum(1 for n in range(1, 21) if n != 1 and n + _FLAT >= ac and not (n == 20 and n + _FLAT < ac))
        if explicit == "advantage":
            pct = round((1 - ((20 - hits) / 20) ** 2) * 100)
        elif explicit == "disadvantage":
            pct = round(((hits / 20) ** 2) * 100)
        else:
            pct = round(hits / 20 * 100)
        return {"percent": max(0, min(100, pct)), "chance": max(0.0, min(1.0, pct / 100)),
                "advantage": explicit, "reasons": [], "ac": ac, "cover": ctx.cover,
                "bonus": _FLAT}

    def attack(self, attacker, target, attack: dict, ctx: AttackContext, roller,
               player: bool, explicit: str = "normal") -> dict:
        """Roll, apply, and report. Applying the damage here is the contract; see the
        module docstring, which records what the interface docstring used to leave out.
        """
        dice = [roll for roll in ([1, 20] if explicit == "advantage" else
                                  [20, 1] if explicit == "disadvantage" else [None])
                if roll is not None] or [None]
        if dice == [None]:
            roll = roller.roll("1d20", attacker.name, f"{attack['name']} attack", player=player)
        else:
            roll = max((roller.roll("1d20", attacker.name, f"{attack['name']} attack",
                                    player=player) for _ in dice),
                       key=lambda r: r.total if explicit == "advantage" else -r.total)
        total = roll.total + _FLAT
        ac = self.ac(target)
        hit = roll.natural == 20 or (roll.natural != 1 and total >= ac)
        damage = None
        if hit:
            parts = [{"amount": self._damage_roll(roller, attacker, attack, player),
                      "type": "blunt"}]
            damage = self.damage(target, parts, crit=False, ctx=ctx)
        tag = " (nat 20)" if roll.natural == 20 else ""
        text = (f"{attacker.name} {attack['name']} -> {target.name}: {total} vs AC {ac}"
                f"{tag}, {'hit' if hit else 'miss'}.")
        if damage:
            text += " " + damage["text"]
        odds = self.hit_chance(attacker, target, attack, ctx, explicit)
        roll.odds = {"percent": odds["percent"], "label": "to hit", "about": target.id,
                     "advantage": explicit}
        # The same two fields 5e puts on its d20, after damage() has a total.
        # Odds alone would leave this toy green in the conformance suite while
        # the roll the display reads no longer matched.
        roll.hit = hit
        if hit:
            roll.damage = damage["total"]
        return {"hit": hit, "crit": False, "natural": roll.natural, "total": total,
                "ac": ac, "advantage": explicit, "reasons": odds["reasons"],
                "damage": damage, "odds": dict(roll.odds), "verdict": "hit" if hit else "miss",
                "text": text}

    def _damage_roll(self, roller, attacker, attack, player) -> int:
        spec = (attack.get("damage") or [{}])[0]
        return roller.roll(spec.get("dice", "1d6"), attacker.name,
                           f"{attack['name']} damage", player=player).total

    def saving_throw(self, token, ability: str, dc: int, roller, player: bool,
                     cover: int = 0, explicit: str = "normal") -> dict:
        cover_note = f" with cover (+{cover})" if cover else ""
        d = roller.roll("1d20", token.name, f"{ability.upper()} save", player=player)
        success = d.total >= dc
        text = (f"{token.name} makes a {ability.upper()} save{cover_note}: "
                f"{d.total} against DC {dc}: {'success' if success else 'failure'}.")
        return {"success": success, "natural": d.natural, "total": d.total, "dc": dc,
                "text": text, "roll": d}

    def save_chance(self, token, ability: str, dc: int, cover: int = 0,
                    explicit: str = "normal") -> dict:
        # Half the d20 succeeds at a DC the token can just reach, and the table is
        # counted rather than interpolated so it cannot drift from `saving_throw`.
        if explicit == "advantage":
            fail = sum(1 for a in range(1, 21) for b in range(1, 21) if max(a, b) < dc)
            percent = round(100 * (1 - fail / 400))
        elif explicit == "disadvantage":
            fail = sum(1 for a in range(1, 21) for b in range(1, 21) if min(a, b) < dc)
            percent = round(100 * (1 - fail / 400))
        else:
            percent = round(100 * (dc - 1) / 20)
        return {"fail": max(0.0, min(1.0, 1 - percent / 100)),
                "percent_fail": max(0, min(100, 100 - percent)),
                "advantage": explicit, "dc": dc, "bonus": 0, "reasons": []}

    def ability_check(self, token, name: str, dc: int, roller, player: bool,
                      explicit: str = "normal", sense: str = "", other=None,
                      source_in_sight: bool = True) -> dict:
        d = roller.roll("1d20", token.name, f"{name} check", player=player)
        success = d.total >= dc
        return {"success": success, "natural": d.natural, "total": d.total, "dc": dc,
                "roll": d, "modifier": 0,
                "text": f"{token.name} makes a {name} check: {d.total} against DC {dc}: "
                        f"{'success' if success else 'failure'}."}

    def damage_multiplier(self, token, dtype: str) -> float:
        return 1.0

    def passive_perception(self, token) -> int:
        return 10

    def skill_bonus(self, token, skill: str) -> int:
        return 0

    def ac(self, token) -> int:
        return token.ac

    def known_spells(self, caster) -> list:
        return []

    # ── damage ─────────────────────────────────────────────────────────────────

    def damage(self, target, parts: list, crit: bool = False, ctx=None) -> dict:
        """Apply, never take a creature below 0, and retire it at 0.

        Two contract invariants, neither of them a 5e one, and neither of them written
        down in the interface before this suite found them:

          - a change is never reported as negative and the token never goes below 0, so
            the engine can print a hit point total to a player without checking it;
          - a creature at 0 HP is `dead`, which is how the engine knows the fight is
            over (`end_turn` asks whether any hostile is still `active`). The first
            version of this method wrote `stable = True` instead, which is the 5e word
            for a 5e ritual, and the fight simply never ended.
        """
        total = sum(int(p.get("amount", 0)) for p in parts or [])
        types = ", ".join(sorted({p.get("type", "") for p in parts or [] if p.get("type")}))
        before = target.hp
        target.hp = max(0, target.hp - total)
        dealt = before - target.hp
        # `total` is the figure the attack roll's `damage` field reads, the
        # amount before the HP floor. `dealt` stays the hit points actually
        # lost. This system has no resistances to change the first number.
        out = {"hp": target.hp, "damage": dealt, "dealt": dealt, "total": total,
               "types": types, "dead": False, "concentration_dc": None}
        if target.hp == 0:
            target.dead = out["dead"] = True
            out["text"] = f"{target.name} takes {dealt} damage and is out of the fight."
        else:
            out["text"] = f"{target.name} takes {dealt} damage ({target.hp} left)."
        return out

    def heal(self, token, amount: int) -> dict:
        before = token.hp
        token.hp = min(token.max_hp, token.hp + int(amount))
        return {"hp": token.hp, "healed": token.hp - before,
                "text": f"{token.name} regains {token.hp - before}."}

    # ── characters ─────────────────────────────────────────────────────────────

    def token_from_sheet(self, path, token_id: str, pos: tuple):
        from tactics.state import Token
        text = open(path, encoding="utf-8").read()
        for line in text.splitlines():
            if line.startswith("# "):
                name = line[2:].strip()
                break
        else:
            name = token_id
        return Token(id=token_id, name=name, side="pc", x=pos[0], y=pos[1],
                     hp=10, max_hp=10, ac=12, speed=30, dex_mod=0, controller="player",
                     attacks=[{"name": "Club", "type": "melee", "source": "weapon",
                               "bonus": _FLAT, "reach": 5,
                               "damage": [{"dice": "1d6", "type": "blunt"}],
                               "flags": []}],
                     source={"kind": "sheet", "path": str(path)})

    def token_from_monster(self, name: str, token_id: str, display_name: str, pos: tuple):
        from tactics.state import Token
        return Token(id=token_id, name=display_name or name, side="enemy", x=pos[0],
                     y=pos[1], hp=10, max_hp=10, ac=12, speed=30, dex_mod=0,
                     controller="gm",
                     attacks=[{"name": "Club", "type": "melee", "source": "weapon",
                               "bonus": _FLAT, "reach": 5,
                               "damage": [{"dice": "1d6", "type": "blunt"}],
                               "flags": []}],
                     source={"kind": "fake", "ref": name})

    def write_back(self, sheet_text: str, token) -> str:
        out = []
        for line in sheet_text.splitlines(True):
            if line.startswith("**HP:**"):
                out.append(f"**HP:** {token.hp} / {token.max_hp}\n")
            else:
                out.append(line)
        return "".join(out)

    def lasting_conditions(self, token) -> list:
        return []

    # ── design: not implemented, and says so ───────────────────────────────────

    def encounter_budget(self, levels: list, ruleset: str = "") -> dict:
        raise NotImplementedError("FakeRules has no encounter design tables.")

    def rate_encounter(self, groups: list, levels: list, ruleset: str = "",
                       known: dict = None) -> dict:
        raise NotImplementedError("FakeRules has no encounter design tables.")

    def adventuring_day(self, levels: list, ruleset: str = "",
                        plan: list | None = None) -> dict:
        raise NotImplementedError("FakeRules has no day concept.")

    def award_xp(self, sheet_path, amount: int) -> dict:
        raise NotImplementedError("FakeRules does not track XP.")

    def record_awards(self, campaign_dir, entries: list, note: str = "") -> None:
        return None


RULES = FakeRules()


def install() -> str:
    """Put FakeRules in the engine's rules cache under `fake`, and return the name.

    `rules.load` reads `systems/<name>/tactics_rules.py`, so a second system has to be
    registered rather than assigned somewhere the engine reads. Going through the cache
    is deliberate: it is the same seam a real port uses, so a test that passes here
    passes for a port too.
    """
    _RULES_CACHE[SYSTEM] = RULES
    return SYSTEM


__all__ = ["SYSTEM", "RULES", "FakeRules", "install"]