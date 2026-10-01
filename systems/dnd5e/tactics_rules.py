"""tactics_rules.py: D&D 5e (2014) rules for the tactical combat engine.

Implements the Rules interface in scripts/tactics/rules.py. Everything here is
5e-specific: advantage and disadvantage from conditions, crits and nat 1s,
resistances, temp HP, dropping to 0 HP, instant death, death saves, cover as
an AC bonus, hit chance for previews, and the monster adapter that turns an
SRD record into a token.

Rule references are to the 2014 Player's Handbook (PHB) and SRD 5.1.
"""

from __future__ import annotations

import pathlib
import re
import sys

_SCRIPTS = str(pathlib.Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from tactics import effects as fx               # noqa: E402
from tactics.roller import average             # noqa: E402
from tactics.rules import AttackContext, Rules  # noqa: E402
from tactics.state import Token                   # noqa: E402

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
SKILL_ABILITY = {"athletics": "str", "acrobatics": "dex", "sleight-of-hand": "dex", "stealth": "dex",
                 "arcana": "int", "history": "int", "investigation": "int", "nature": "int",
                 "religion": "int", "animal-handling": "wis", "insight": "wis", "medicine": "wis",
                 "perception": "wis", "survival": "wis", "deception": "cha", "intimidation": "cha",
                 "performance": "cha", "persuasion": "cha"}

# ── conditions ───────────────────────────────────────────────────────────────
# One table for all fourteen 5e conditions (PHB p290-292) plus exhaustion
# (appendix A), so the attack, save and check paths read the same rules instead
# of each keeping its own list — which is how a condition ends up helping the
# creature it is meant to be hurting.
#
# A value is one of:
#   "adv" / "dis"        advantage or disadvantage, always
#   {"gate": "adv"}      only when the gate holds for this roll, and simply
#                        absent otherwise, so an unmet gate never cancels
#                        another condition's advantage out
#   "auto_fail"          (saves) the save fails without a die being rolled
#   0 / "half"           (movement) speed, as a multiplier of the token's own
#
# Gates: melee, melee_within_5, ranged, source_in_sight, charmer, charmer_social,
# hearing, sight. A "*" key in `save` means every ability.
#
# Each entry also carries a `note`: what the condition is doing, in the words the
# GM reads it in. Fourteen conditions nobody can hold in their head is how a
# condition quietly stops being applied halfway through a fight.
#
# One entry is a house ruling, marked as such. 2014 charmed says the charmed
# creature cannot willingly attack or target its charmer; the engine does not
# refuse the attack (a charm is a suggestion in play, and the GM's ruling is
# better than the table's), it makes the creature's attacks on its charmer
# disadvantage, which is what "will not fight the friend" looks like on a die.
CONDITION_EFFECTS = {
    "blinded": {
        "attack_roll": "dis",                     # PHB p290: attacks have disadvantage
        "ability_check": "dis",                   # checks that need sight
        "attack_against": "adv",                  # attacks against it have advantage
        "note": "disadvantage on attack rolls and on checks that need sight; "
                "attacks against them have advantage",
    },
    "charmed": {
        "attack_roll": {"charmer": "dis"},        # house ruling, see above
        "ability_check": {"charmer_social": "adv"},   # PHB p290: the charmer's social checks
        "harmful_to": ["charmer"],                # PHB p290: it cannot harm its charmer
        "note": "cannot willingly attack its charmer; the charmer has advantage "
                "on social checks with them",
    },
    "deafened": {
        "ability_check": {"hearing": "auto_fail"},    # PHB p291: checks needing hearing
        "note": "automatically fails any check that requires hearing",
    },
    "exhaustion": {},                            # by level, see EXHAUSTION_EFFECTS
    "frightened": {
        "attack_roll": {"source_in_sight": "dis"},
        "ability_check": {"source_in_sight": "dis"},
        "note": "disadvantage on attack rolls and ability checks while the source "
                "of its fear is in sight",
    },
    "grappled": {
        "movement": 0,                            # PHB p291
        "note": "speed 0",
    },
    "incapacitated": {
        "action_economy": "none",                 # PHB p292
        "note": "cannot take actions or reactions",
    },
    "invisible": {
        "attack_roll": "adv",                     # PHB p292
        "attack_against": "dis",
        "note": "attacks with advantage, attacks against it with disadvantage",
    },
    "paralyzed": {
        "attack_roll": "dis", "ability_check": "dis",
        "save": {"str": "auto_fail", "dex": "auto_fail"},
        "attack_against": "adv", "action_economy": "none", "movement": 0,
        "auto_crit_within_5": True,               # PHB appendix A
        "note": "speed 0, no actions or reactions, STR and DEX saves fail "
                "automatically, attacks against it have advantage",
    },
    "petrified": {
        "attack_roll": "dis", "ability_check": "dis",
        "save": {"str": "auto_fail", "dex": "auto_fail"},
        "attack_against": "adv", "action_economy": "none", "movement": 0,
        "auto_crit_within_5": True,               # PHB appendix A
        "immunities": ["poison", "disease"],     # PHB p291
        "note": "as paralyzed, and immune to poison and disease",
    },
    "poisoned": {
        "attack_roll": "dis", "ability_check": "dis",   # PHB p292
        "note": "disadvantage on attack rolls and ability checks",
    },
    "prone": {
        "attack_roll": {"ranged": "dis"},         # PHB p292: ranged attacks only
        "attack_against": {"melee_within_5": "adv", "ranged": "dis"},
        "note": "attackers within 5 ft have advantage, ranged attacks against it "
                "have disadvantage, and its own ranged attacks have disadvantage",
    },
    "restrained": {
        "attack_roll": "dis", "attack_against": "adv",
        "save": {"dex": "dis"}, "movement": 0,    # PHB p292
        "note": "speed 0, DEX saves have disadvantage, attacks against it have "
                "advantage",
    },
    "stunned": {
        "attack_roll": "dis", "ability_check": "dis",
        "save": {"str": "auto_fail", "dex": "auto_fail"},
        "attack_against": "adv", "action_economy": "none", "movement": 0,
        "note": "speed 0, no actions or reactions, STR and DEX saves fail "
                "automatically, attacks against it have advantage",
    },
    "unconscious": {
        "attack_roll": "dis", "ability_check": "dis",
        "save": {"str": "auto_fail", "dex": "auto_fail"},
        "attack_against": "adv", "action_economy": "none", "movement": 0,
        "auto_crit_within_5": True,               # PHB appendix A
        "note": "as paralyzed, and unaware",
    },
}

# PHB appendix A. Level 4 and 6 are not per-roll modifiers: the hit point maximum
# is halved and the creature dies once, when the level is set (see set_condition).
EXHAUSTION_EFFECTS = {
    1: {"ability_check": "dis"},
    2: {"movement": "half"},
    3: {"attack_roll": "dis", "save": {"*": "dis"}},
    4: {"hp_max": "half"},
    5: {"movement": 0},
    6: {"death": True},
}
EXHAUSTION_TEXT = {
    1: "disadvantage on ability checks",
    2: "speed halved",
    3: "disadvantage on attack rolls and saving throws",
    4: "hit point maximum halved",
    5: "speed 0",
    6: "death",
}

# SRD defense text that makes a resistance conditional; never applied blindly.
_CONDITIONAL = re.compile(r"nonmagical|\bfrom\b|that aren|while|except", re.I)

# Every key get_condition_modifiers() always returns, so a caller never has to
# ask whether a condition that grants nothing is the same as a condition that was
# never looked up.
MODIFIER_KEYS = ("attack_roll", "ability_check", "save", "attack_against",
                 "movement", "action_economy")


def exhaustion_level(token) -> int:
    """0 to 6. The level lives in `extra`, so the condition on the token stays
    the plain word "exhaustion" and the display, the sheet write-back and every
    set membership keep working; "exhaustion 3" is accepted as an input form."""
    level = (token.extra or {}).get("exhaustion_level")
    if isinstance(level, bool) or not isinstance(level, (int, float, str)):
        level = None
    if level is not None:
        try:
            return max(0, min(6, int(str(level).strip())))
        except (TypeError, ValueError):
            pass
    for cond in token.conditions:
        m = re.fullmatch(r"(?:exhausted|exhaustion)\s*(\d)", str(cond).strip().lower())
        if m:
            return max(0, min(6, int(m.group(1))))
    return 0


def get_condition_modifiers(token) -> dict:
    """Every modifier this token's conditions impose, merged into one dict.

    The keys are always present: `attack_roll`, `ability_check`, `save`,
    `attack_against`, `movement`, `action_economy`, plus `auto_crit_within_5`,
    `immunities`, `harmful_to`, `exhaustion` and the `sources` that produced
    them. A value is `"adv"` / `"dis"`, a gate dict, a per-ability dict (with
    "*" for every ability), a speed, or None when nothing applies.

    Advantage and disadvantage from different conditions cancel, because that is
    what the rules say happens; every other kind of effect stacks.
    """
    mods = {k: None for k in MODIFIER_KEYS}
    mods.update({"save": {}, "auto_crit_within_5": False, "immunities": [],
                 "harmful_to": [], "hp_max": None, "death": False,
                 "exhaustion": 0, "sources": []})
    level = exhaustion_level(token)
    if level:
        mods["exhaustion"] = level
        mods["sources"].append(f"exhaustion {level}")
        _merge_effects(mods, EXHAUSTION_EFFECTS[level])
    for cond in sorted(token.conditions):
        cond = str(cond).strip().lower()
        if not cond or re.fullmatch(r"(?:exhausted|exhaustion)\s*\d", cond):
            continue
        effects = CONDITION_EFFECTS.get(cond)
        if not effects:
            continue
        mods["sources"].append(cond)
        _merge_effects(mods, effects)
    return mods


def _merge_effects(mods: dict, effects: dict) -> None:
    for key, value in effects.items():
        if key == "note":                      # prose, not a modifier
            continue
        if key in ("save", "attack_against", "attack_roll", "ability_check"):
            mods[key] = _merge_modes(mods[key], value)
        elif key == "movement":
            mods[key] = _merge_movement(mods[key], value)
        elif key == "action_economy":
            if value == "none":
                mods[key] = "none"
        elif key in ("auto_crit_within_5", "death"):
            mods[key] = bool(mods[key]) or bool(value)
        elif key in ("immunities", "harmful_to"):
            mods[key] = mods[key] + [v for v in value if v not in mods[key]]
        elif key == "hp_max":
            mods[key] = value
        else:                     # an effect this version does not know about
            mods.setdefault(key, value)


def _merge_modes(old, new):
    """adv and dis cancel; a gate dict merges gate by gate, and an ungated value
    is treated as a "*" gate so the two can be combined without either winning."""
    if old is None:
        return new
    out = dict(_as_gates(old))
    for gate, mode in _as_gates(new).items():
        out[gate] = _cancel(out[gate], mode) if gate in out else mode
    if list(out) == ["*"]:
        return out["*"]
    return out


def _as_gates(value) -> dict:
    if value is None:
        return {}
    return {"*": value} if isinstance(value, str) else dict(value)


def _cancel(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a == b:
        return a
    return None if {a, b} == {"adv", "dis"} else a


def _merge_movement(old, new):
    if old == 0 or new == 0:
        return 0
    if old == "half" or new == "half":
        return "half"
    return old


def _gate_holds(token, gate: str, ctx=None, other=None) -> bool:
    """Whether a conditional effect applies to this one situation.

    The engine measures geometry and hands it over in `ctx`; the rules decide
    what it means. A gate nobody measured (no `ctx` at all) does not apply: an
    unmet gate has to be silent, because a condition that guesses is worse than
    a condition that waits to be asked.
    """
    if gate in ("melee", "ranged"):
        if ctx is None:
            return False
        return bool(ctx.melee) == (gate == "melee")
    if gate == "melee_within_5":
        return bool(ctx and ctx.melee and ctx.distance <= 5)
    if gate == "source_in_sight":
        if ctx is None:
            return True            # nobody measured it: a fear the GM applied is happening
        if isinstance(ctx, dict):
            return bool(ctx.get("source_in_sight", True))
        return bool(getattr(ctx, "source_in_sight", True))
    if gate in ("charmer", "charmer_social"):
        return other is not None and _source_of(token, "charmed") == other.id
    if gate == "hearing":
        return _needs(ctx, "hearing")
    if gate == "sight":
        return _needs(ctx, "sight")
    return True


def _needs(ctx, sense: str) -> bool:
    """A check's stated sense, when the caller gave one."""
    if ctx is None:
        return False
    needed = ctx.get("sense") if isinstance(ctx, dict) else getattr(ctx, "sense", None)
    return needed == sense or (sense == "hearing" and needed == "hearing_or_sight")


def _source_of(token, condition: str):
    """The id of whoever applied `condition` to this token, when it is known.

    A charm or a fear applied by a spell or a rider records its source; one the GM
    typed in by hand does not, and then the gate has nobody to single out and stays
    silent rather than picking the nearest hostile.
    """
    for effect in token.effects or []:
        if condition in (effect.get("conditions") or []) and effect.get("source"):
            return effect["source"]
    return None


def _side(mods: dict, key: str, token, ctx=None, other=None) -> tuple:
    """("adv" | "dis" | None, [the conditions that actually say so]).

    The second half is what goes in the reason list, so the GM is told the
    condition that did it and not the four that were also on the creature.
    """
    value = mods.get(key)
    if not isinstance(value, dict):
        return value, list(mods["sources"]) if value else []
    mode, conds = None, []
    for gate, gate_mode in value.items():
        if not _gate_holds(token, gate, ctx, other):
            continue
        mode = _merge_modes(mode, gate_mode)
        conds += [c for c in mods["sources"] if gate in _gates_of(c, key)]
    return mode, conds


def _gates_of(cond: str, key: str) -> dict:
    """The gates the named condition contributes for `key`."""
    return _as_gates(CONDITION_EFFECTS.get(cond, {}).get(key))


def _mode_name(mode: str) -> str:
    """"adv"/"dis" as the words everything else in the engine says. A mode that is
    already one of those words (a GM's ruling) is left alone."""
    return {"adv": "advantage", "dis": "disadvantage",
            "advantage": "advantage", "disadvantage": "disadvantage"}.get(mode, "normal")


def _merge_modes_with_reasons(side: tuple, explicit: str) -> tuple:
    """(mode, conditions) from _side, with the GM's ruling winning if there is one"""
    mode, conds = side
    if explicit in ("advantage", "disadvantage"):
        return explicit, ["the GM's ruling"]
    return mode, conds


def _mod_for(mods: dict, key: str, token, ctx=None, other=None) -> str:
    """The mode for one situation, resolving the gates: "adv", "dis" or None.

    A gated effect that does not apply contributes nothing, rather than cancelling
    the other conditions out: a prone creature is not blinded to their own arrows.
    """
    value = mods.get(key)
    if not isinstance(value, dict):
        return value
    out = None
    for gate, mode in value.items():
        if _gate_holds(token, gate, ctx, other):
            out = _merge_modes(out, mode)
    return out


def _save_for(mods: dict, ability: str) -> str:
    """A save's mode: the condition names one ability, or "*" for all of them."""
    saves = mods.get("save") or {}
    if isinstance(saves, str):
        return saves
    return saves.get(ability) or saves.get("*") or "normal"


def _failing(token, ability: str = "") -> str:
    """The condition that is making this save worse, for the prose.

    One creature can be grappled, restrained and exhausted at once; the line the
    GM reads should name the condition that is actually doing the work, and a
    grapple costs a DEX save nothing on its own.
    """
    mods = get_condition_modifiers(token)
    for cond in mods["sources"]:
        effects = CONDITION_EFFECTS.get(cond) or EXHAUSTION_EFFECTS.get(mods["exhaustion"], {})
        saves = _as_gates(effects.get("save"))
        if saves.get(ability) or saves.get("*"):
            return cond
    return "a condition"


def _parse_exhaustion(condition: str) -> tuple:
    """"exhaustion 3" -> ("exhaustion", 3); "exhaustion" -> ("exhaustion", None);
    anything else -> (condition, None).

    None means the GM typed no number, and the two callers read that differently:
    adding exhaustion with no number is level 1 (nobody is exhausted at 0), and
    removing it with no number is one level off, which is what a long rest does.

    The number is stored in `extra`; the condition itself stays the plain word,
    because that is what the display, the sheet write-back and every set
    membership expect.
    """
    text = str(condition or "").strip().lower()
    m = re.fullmatch(r"(exhausted|exhaustion)(?:\s+(\d))?", text)
    if not m:
        return text, None
    return "exhaustion", int(m.group(2)) if m.group(2) else None


def _mod(score: int) -> int:
    return (int(score) - 10) // 2


def _why(mode: str, reasons: list) -> str:
    """The reason a roll was harder or easier than its numbers, in the line itself.

    A player watching a poisoned archer roll well deserves to know why, and a GM
    who cannot see the modifier in the output will assume the engine is wrong.
    """
    if mode == "normal" or not reasons:
        return ""
    return f" ({mode}: {', '.join(reasons)})"


def _uses_death_saves(token) -> bool:
    """PCs roll death saves at 0 HP; monsters and NPCs die (PHB p197, DMG p272 default)."""
    return bool(token.extra.get("death_saves", token.side == "pc"))


class DnD5e(Rules):
    name = "dnd5e"

    # ── economy ──────────────────────────────────────────────────────────────
    def initiative(self, token, roller):
        bonus = token.dex_mod + int(token.extra.get("initiative_bonus", 0))
        # SKILL.md: initiative is always GM-rolled, whatever roll_mode says.
        return roller.roll(f"1d20{bonus:+d}", token.name, "initiative")

    def turn_budget(self, token) -> dict:
        return {"movement": self.speed(token), "action": 1, "bonus": 1, "reaction": 1}

    def opportunity_attack(self, token):
        """The melee attack a creature makes as a reaction: its best parsed one."""
        melee = [a for a in token.attacks
                 if a.get("type") in ("melee", "melee_or_ranged")
                 and "unparsed" not in a.get("flags", [])]
        return max(melee, key=average_damage, default=None)

    # ── movement ─────────────────────────────────────────────────────────────
    def speed(self, token) -> int:
        if token.dead:
            return 0
        speed = self.condition_modifiers(token)["movement"]
        if speed == 0:
            return 0
        return token.speed // 2 if speed == "half" else token.speed

    def crawling(self, token) -> bool:
        return token.has("prone")

    def stand_up_cost(self, token) -> int:
        return token.speed // 2          # PHB p190: half your speed

    def reach(self, token) -> int:
        reaches = [a.get("reach", 5) for a in token.attacks
                   if a.get("type") in ("melee", "melee_or_ranged")
                   and "unparsed" not in a.get("flags", [])]
        return max(reaches, default=5)

    # ── conditions ───────────────────────────────────────────────────────────
    def condition_modifiers(self, token) -> dict:
        """Everything this token's conditions change, merged. See
        get_condition_modifiers(); the GM-facing prose is condition_notes()."""
        return get_condition_modifiers(token)

    def condition_notes(self, token) -> list:
        """What the token's conditions are currently doing, in one line each.

        A player who suddenly rolls badly deserves to know why, and the GM
        deserves not to have to remember which of fourteen conditions it was.
        """
        mods = self.condition_modifiers(token)
        lines = []
        for cond in mods["sources"]:
            if cond.startswith("exhaustion"):
                said = EXHAUSTION_TEXT.get(mods["exhaustion"], "")
            else:
                said = CONDITION_EFFECTS.get(cond, {}).get("note", "")
            lines.append(f"{cond}: {said}" if said else f"{cond}.")
        return lines

    def set_condition(self, token, condition: str) -> list:
        """Add a condition, and apply what the rules make immediate rather than
        per-roll: exhaustion level 4's halved hit point maximum, level 6's death.

        Everything else in the table is a modifier the attack, save and check
        paths read on their own, so nothing has to be applied here for those —
        a condition that is only half-applied by the time it matters is worse
        than one that is never applied at all.
        """
        cond, level = _parse_exhaustion(condition)
        token.add_condition(cond)
        lines = []
        if cond != "exhaustion":
            return lines
        level = level or 1                      # nobody is exhausted at level 0
        token.extra["exhaustion_level"] = level
        if level >= 4 and not token.extra.get("exhaustion_hp_halved"):
            token.extra["exhaustion_hp_halved"] = True
            before = token.max_hp
            token.max_hp = max(1, before // 2)
            token.hp = min(token.hp, token.max_hp)
            lines.append(f"{token.name}'s hit point maximum is halved: {before} -> {token.max_hp}.")
        if level >= 6 and not token.dead:
            token.dead = True
            lines.append(f"{token.name} dies.")
        return lines

    def clear_condition(self, token, condition: str) -> list:
        """Remove a condition, and undo what setting it applied.

        "exhaustion" with no number is one level off, which is what a long rest
        does; "exhaustion 2" sets the level outright, for a GM ruling one.
        """
        cond, asked = _parse_exhaustion(condition)
        if cond != "exhaustion":
            token.remove_condition(cond)
            return []
        was = exhaustion_level(token)         # before the condition goes, not after
        level = asked if asked is not None else max(0, was - 1)
        token.remove_condition("exhaustion")
        if level:
            token.extra["exhaustion_level"] = level
        else:
            token.extra.pop("exhaustion_level", None)
        lines = []
        if level < 4 and token.extra.pop("exhaustion_hp_halved", None):
            token.max_hp = max(1, token.max_hp * 2)
            token.hp = min(token.hp, token.max_hp)
            lines.append(f"{token.name}'s hit point maximum is back to {token.max_hp}.")
        return lines

    def can_act(self, token) -> bool:
        mods = self.condition_modifiers(token)
        if token.dead or (token.hp <= 0 and _uses_death_saves(token)):
            return False
        if mods["death"]:                 # exhaustion 6, set by set_condition
            return False
        return mods["action_economy"] != "none"

    def can_react(self, token) -> bool:
        return self.can_act(token) and not token.reaction_used

    def death_save(self, token, roller, player: bool) -> dict:
        """PHB p197. 10+ succeeds, nat 20 regains 1 HP, nat 1 is two failures."""
        r = roller.roll("1d20", token.name, "death save", player=player)
        ds = token.death_saves
        value, note = r.natural, ""
        penalty = fx.take(token, lambda e: e.get("save_penalty"))
        if penalty:                 # a death save is a saving throw: it spends the penalty
            p = roller.roll(penalty["save_penalty"], token.name, f"{penalty['name']} penalty")
            value -= p.total
            note = f" (-{p.total} {penalty['name']})"
        if r.natural == 20:
            token.hp = 1
            ds.update(successes=0, failures=0)
            token.stable = False
            token.remove_condition("unconscious")
            text = f"{token.name} death save: nat 20, regains 1 HP and wakes!"
        elif r.natural == 1:
            ds["failures"] += 2
            text = f"{token.name} death save: nat 1, two failures ({ds['failures']}/3)."
        elif value >= 10:
            ds["successes"] += 1
            text = f"{token.name} death save: {value}{note}, success ({ds['successes']}/3)."
        else:
            ds["failures"] += 1
            text = f"{token.name} death save: {value}{note}, failure ({ds['failures']}/3)."
        if ds["failures"] >= 3:
            token.dead = True
            text += f" {token.name} dies."
        elif ds["successes"] >= 3:
            token.stable = True
            ds.update(successes=0, failures=0)
            text += f" {token.name} is stable."
        return {"natural": r.natural, "stable": token.stable, "dead": token.dead,
                "revived": token.hp > 0, "text": text}

    # ── attack ───────────────────────────────────────────────────────────────
    def advantage(self, attacker, target, ctx: AttackContext,
                  explicit: str = "normal") -> tuple:
        """("advantage" | "disadvantage" | "normal", [reasons]).

        Every reason is named, because the line the GM reads afterwards has to
        say *why* the roll was harder than the numbers suggest. An `explicit`
        advantage is the GM's own ruling for this one attack and outranks every
        condition, both ways: a --adv on a poisoned attacker is advantage, and a
        --dis on a stunned defender is disadvantage.
        """
        adv, dis = [], []
        amode, aconds = _side(self.condition_modifiers(attacker), "attack_roll", attacker, ctx,
                              other=target)
        tmode, tconds = _side(self.condition_modifiers(target), "attack_against", target, ctx,
                              other=attacker)
        for mode, conds, who in ((amode, aconds, attacker), (tmode, tconds, target)):
            if mode == "dis":
                dis += [f"{who.name} is {c}" for c in conds]
            elif mode == "adv":
                adv += [f"{who.name} is {c}" for c in conds]
        if attacker.has("hidden"):
            adv.append(f"{attacker.name} is hidden")
        if any(e.get("advantage_vs") == target.id for e in attacker.effects):
            adv.append(f"helped against {target.name}")
        if any(e.get("advantage_next") for e in attacker.effects):
            adv.append("Silvery Barbs")
        if target.has("hidden"):
            dis.append(f"{target.name} is hidden")
        if self._dodging(target):
            dis.append(f"{target.name} is dodging")
        if not ctx.melee:
            if ctx.long_range:
                dis.append("long range")
            if ctx.hostile_adjacent:
                dis.append("an enemy is within 5 ft")
        if explicit in ("advantage", "disadvantage"):
            mode = explicit
            return mode, ["the GM's ruling"]
        if adv and not dis:
            return "advantage", adv
        if dis and not adv:
            return "disadvantage", dis
        return "normal", adv + dis

    def _dodging(self, token) -> bool:
        """PHB Dodge: the benefit ends if you are incapacitated or your speed drops to 0."""
        return token.dodging and self.can_act(token) and self.speed(token) > 0

    def hit_chance(self, attacker, target, attack: dict, ctx: AttackContext,
                   explicit: str = "normal") -> dict:
        """Exact chance to hit, for previews (BG3-style percentages on every
        option). Uses the same advantage and cover logic as attack()."""
        mode, reasons = self.advantage(attacker, target, ctx, explicit)
        need = self.ac(target) + ctx.cover - int(attack.get("bonus", 0))   # natural roll needed
        need = min(max(need, 2), 20)                                  # nat 1 misses, nat 20 hits
        p = (21 - need) / 20
        if mode == "advantage":
            p = 1 - (1 - p) ** 2
        elif mode == "disadvantage":
            p = p ** 2
        return {"chance": p, "percent": round(p * 100), "advantage": mode, "reasons": reasons}

    def attack(self, attacker, target, attack: dict, ctx: AttackContext, roller,
               player: bool, explicit: str = "normal") -> dict:
        mode, reasons = self.advantage(attacker, target, ctx, explicit)
        # The odds are the same call the pre-action preview makes, deliberately:
        # one function, so the badge the player chose on and the number printed
        # beside the resolved roll can never disagree. Computed before the roll
        # and not revised after it, because a reaction (Shield, Silvery Barbs)
        # changes the AC the roll is compared against, not the chance the player
        # acted on.
        odds = self.hit_chance(attacker, target, attack, ctx, explicit)
        bonus = int(attack.get("bonus", 0))
        ac = self.ac(target) + ctx.cover
        r = roller.roll(f"1d20{bonus:+d}", attacker.name, f"{attack['name']} vs {target.name}",
                        player=player, advantage=mode)
        r.odds = {"percent": odds["percent"], "label": "to hit", "about": target.id,
                  "advantage": mode}
        # One-shot advantages are spent by the roll they helped.
        fx.take(attacker, lambda e: e.get("advantage_vs") == target.id)
        fx.take(attacker, lambda e: e.get("advantage_next"))
        natural, total = r.natural, r.total
        reaction_lines = []
        if ctx.react and (natural == 20 or (natural != 1 and total >= ac)):
            res = ctx.react(natural, total, ac)
            natural, total, ac, reaction_lines = res["natural"], res["total"], res["ac"], res["lines"]
        crit = natural == 20
        hit = crit or (natural != 1 and total >= ac)
        # PHB appendix A: any hit by an attacker within 5 ft, melee or ranged.
        if hit and ctx.distance <= 5 and self.condition_modifiers(target)["auto_crit_within_5"]:
            crit = True
        out = {"hit": hit, "crit": crit, "natural": natural, "total": total, "ac": ac,
               "advantage": mode, "reasons": reasons, "damage": None,
               "odds": dict(r.odds)}
        if hit:
            parts = []
            for p in attack.get("damage", []):
                d = roller.roll(p["dice"], attacker.name, f"{attack['name']} damage",
                                player=player, crit=crit)
                parts.append({"amount": d.total, "type": p.get("type", "")})
            out["damage"] = self.damage(target, parts, crit=crit, ctx=ctx)
        tag = " (CRIT)" if crit else " (nat 1)" if natural == 1 else ""
        adv = f", {mode}" if mode != "normal" else ""
        cover = f", +{ctx.cover} cover" if ctx.cover else ""
        text = " ".join(reaction_lines + [
            f"{attacker.name} {attack['name']} -> {target.name}: {total} vs AC {ac}"
            f"{cover}{adv}, {'hit' if hit else 'miss'}{tag}{_why(mode, reasons)}."])
        if out["damage"]:
            text += " " + out["damage"]["text"]
        if hit and attack.get("rider"):
            out["rider"] = attack["rider"]
            if attack.get("rider_effects"):         # the engine applies these; the rest is the GM's
                if attack.get("rider_rest"):
                    out["gm_note"] = f"GM decides: {attack['rider_rest'].rstrip('.')}."
            else:
                first = re.sub(r"^(and|or)\s+", "", attack["rider"].split(". ")[0].rstrip("."))
                text += f" GM decides the rider: {first}."
        out["text"] = text
        return out

    # ── save ─────────────────────────────────────────────────────────────────
    def _save_mode(self, token, ability: str, explicit: str = "normal") -> tuple:
        mods = self.condition_modifiers(token)
        mode = _save_for(mods, ability)
        if mode == "auto_fail":
            return "auto fail", [f"{token.name} is {_failing(token, ability)}"]
        adv, dis = [], []
        if mode == "dis":
            dis.append(f"{token.name} is {_failing(token, ability)}")
        elif mode == "adv":
            adv.append(f"{token.name} is {_failing(token, ability)}")
        elif ability == "dex" and self._dodging(token):
            adv.append("dodging")       # a condition's own penalty is the worse of it
        if any(e.get("advantage_next") for e in token.effects):
            adv.append("Silvery Barbs")
        if explicit in ("advantage", "disadvantage"):
            return explicit, ["the GM's ruling"]
        if adv and not dis:
            return "advantage", adv
        if dis and not adv:
            return "disadvantage", dis
        return "normal", adv + dis

    def save_bonus(self, token, ability: str, cover: int = 0) -> int:
        return int(token.saves.get(ability, 0)) + (cover if ability == "dex" else 0)

    def saving_throw(self, token, ability: str, dc: int, roller, player: bool,
                     cover: int = 0, explicit: str = "normal") -> dict:
        """PHB p179. Cover adds to DEX saves (PHB p196). A Mind Sliver penalty is
        spent by the next save of any kind; Silvery Barbs' advantage likewise.

        `explicit` is the GM's own ruling for this save and outranks the
        conditions, so a stunned creature can be given a save to make.
        """
        ability = ability.lower()[:3]
        mods = self.condition_modifiers(token)
        if ability in ("str", "dex") and _save_for(mods, ability) == "auto_fail":
            why = f"{token.name} is {_failing(token, ability)}"
            return {"success": False, "auto_fail": True, "natural": None, "total": None,
                    "dc": dc, "advantage": "auto fail", "reasons": [why],
                    "text": f"{token.name} automatically fails the {ability.upper()} save "
                            f"({_failing(token, ability)})."}
        mode, reasons = self._save_mode(token, ability, explicit)
        bonus = self.save_bonus(token, ability, cover)
        r = roller.roll(f"1d20{bonus:+d}", token.name, f"{ability.upper()} save DC {dc}",
                        player=player, advantage=mode)
        # The same reasoning as attack(): save_chance is the preview's own
        # function, so the preview and the resolved roll quote one number.
        r.odds = {"percent": self.save_chance(token, ability, dc, cover,
                                              explicit)["percent_fail"],
                  "label": "to fail the save", "about": token.id, "advantage": mode}
        fx.take(token, lambda e: e.get("advantage_next"))
        total, notes = r.total, []
        if cover and ability == "dex":
            notes.append(f"+{cover} cover")
        penalty = fx.take(token, lambda e: e.get("save_penalty"))
        if penalty:
            p = roller.roll(penalty["save_penalty"], token.name, f"{penalty['name']} penalty")
            total -= p.total
            notes.append(f"-{p.total} {penalty['name']}")
        ok = total >= dc
        note = f" ({', '.join(notes)})" if notes else ""
        adv = f", {mode}" if mode != "normal" else ""
        return {"success": ok, "auto_fail": False, "natural": r.natural, "total": total,
                "dc": dc, "advantage": mode, "reasons": reasons,
                "text": f"{token.name} {ability.upper()} save: {total} vs DC {dc}{note}{adv}, "
                        f"{'success' if ok else 'failure'}{_why(mode, reasons)}."}

    def save_chance(self, token, ability: str, dc: int, cover: int = 0,
                    explicit: str = "normal") -> dict:
        """Chance to FAIL a save, for previews. A natural 1 or 20 is not special
        on saves (PHB p179). A pending Mind Sliver penalty counts as its average."""
        ability = ability.lower()[:3]
        mods = self.condition_modifiers(token)
        if ability in ("str", "dex") and _save_for(mods, ability) == "auto_fail":
            return {"fail": 1.0, "percent_fail": 100, "advantage": "auto fail"}
        mode, _ = self._save_mode(token, ability, explicit)
        bonus = self.save_bonus(token, ability, cover)
        pen = next((e for e in token.effects if e.get("save_penalty")), None)
        if pen:
            bonus -= average(pen["save_penalty"])
        need = dc - bonus                      # natural roll needed to succeed
        succeed = min(max((21 - need) / 20, 0.0), 1.0)
        if mode == "advantage":
            succeed = 1 - (1 - succeed) ** 2
        elif mode == "disadvantage":
            succeed = succeed ** 2
        fail = 1 - succeed
        return {"fail": fail, "percent_fail": round(fail * 100), "advantage": mode}

    def ability_check(self, token, name: str, dc: int, roller, player: bool,
                      explicit: str = "normal", sense: str = "", other=None,
                      source_in_sight: bool = True) -> dict:
        """A skill or ability check, with the conditions that touch it.

        `name` is a skill ("perception") or an ability ("dex"). `sense` says what
        the check depends on, which is what deafened and blinded are about: a
        deafened creature fails a check that needs hearing, and cannot be talked
        out of it by a condition nobody stated the sense for.
        """
        skill = SKILL_ABILITY.get(name.lower().strip())
        bonus = self.skill_bonus(token, name) if skill else int(token.saves.get(name[:3], 0))
        mods = self.condition_modifiers(token)
        ctx = {"sense": sense, "source_in_sight": source_in_sight}
        if _mod_for(mods, "ability_check", token, ctx, other) == "auto_fail":
            cause = ("blind" if sense == "sight" else
                     "deaf" if sense == "hearing" else "the condition")
            return {"total": None, "success": False, "auto_fail": True, "dc": dc,
                    "advantage": "auto fail", "reasons": [cause],
                    "text": f"{token.name} automatically fails the {name} check ({cause})."}
        mode, reasons = _merge_modes_with_reasons(
            _side(mods, "ability_check", token, ctx, other), explicit)
        mode = _mode_name(mode)
        label = f"{token.name} {name.title()} check"
        if dc:
            r = roller.roll(f"1d20{bonus:+d}", token.name, f"{name} check DC {dc}",
                            player=player, advantage=mode)
            ok = r.total >= dc
            adv = f", {mode}" if mode != "normal" else ""
            return {"total": r.total, "natural": r.natural, "success": ok, "auto_fail": False,
                    "dc": dc, "advantage": mode, "reasons": reasons,
                    "text": f"{label}: {r.total} vs DC {dc}{adv}, "
                            f"{'success' if ok else 'failure'}{_why(mode, reasons)}."}
        r = roller.roll(f"1d20{bonus:+d}", token.name, f"{name} check",
                        player=player, advantage=mode)
        return {"total": r.total, "natural": r.natural, "success": None, "auto_fail": False,
                "dc": None, "advantage": mode, "reasons": reasons,
                "text": f"{label}: {r.total}{_why(mode, reasons)}."}

    def ac(self, token) -> int:
        return token.ac + fx.ac_bonus(token)

    def skill_bonus(self, token, skill: str) -> int:
        skill = skill.lower()
        skills = token.extra.get("skills") or {}
        if skill in skills:
            return int(skills[skill])
        ability = SKILL_ABILITY.get(skill, "dex")
        scores = token.extra.get("abilities") or {}
        if ability in scores:
            return _mod(scores[ability])
        return token.dex_mod if ability == "dex" else int(token.saves.get(ability, 0))

    def passive_perception(self, token) -> int:
        if token.extra.get("passive_perception"):
            return int(token.extra["passive_perception"])
        return 10 + self.skill_bonus(token, "perception")

    def spell(self, caster, name: str, level: int = None) -> dict:
        return _spells_module().resolve(caster, name, level)

    def known_spells(self, caster) -> list:
        return list(caster.extra.get("spells", []))

    def damage_multiplier(self, token, dtype: str) -> float:
        """For previews: 0 immune, 0.5 resistant, 2 vulnerable (unconditional entries only)."""
        dtype = (dtype or "").lower()
        if not dtype:
            return 1.0
        if dtype in _plain(token.immunities):
            return 0.0
        mult = 0.5 if dtype in _plain(token.resistances) else 1.0
        return mult * (2 if dtype in _plain(token.vulnerabilities) else 1)

    # ── damage ───────────────────────────────────────────────────────────────
    def damage(self, target, parts: list, crit: bool = False, ctx: AttackContext = None) -> dict:
        total, notes = 0, []
        # Petrified makes a creature immune to poison and disease (PHB p291); the
        # resistance that goes with it is a nonmagical-attack clause, which the
        # conditional-defence pass below leaves to the GM rather than guessing.
        immune = _plain(target.immunities) | set(self.condition_modifiers(target)["immunities"])
        for p in parts:
            amt, typ = max(0, int(p["amount"])), (p.get("type") or "").lower()
            if typ and typ in immune:
                notes.append(f"immune to {typ}")
                amt = 0
            else:
                if typ and typ in _plain(target.resistances):
                    amt //= 2
                    notes.append(f"resists {typ}")
                if typ and typ in _plain(target.vulnerabilities):
                    amt *= 2
                    notes.append(f"vulnerable to {typ}")
                for cond in _conditional(target.resistances + target.immunities):
                    if typ and typ in cond:
                        notes.append(f"GM check: {cond}")
            total += amt
        types = "/".join(sorted({p.get("type", "") for p in parts if p.get("type")}))
        hp_before = target.hp
        absorbed = min(target.temp_hp, total)
        target.temp_hp -= absorbed
        remaining = total - absorbed
        out = {"total": total, "absorbed": absorbed, "hp_before": hp_before,
               "dropped": False, "dead": False, "instant_death": False,
               "death_failures": 0, "concentration_dc": None}

        if remaining and target.concentration:
            out["concentration_dc"] = max(10, remaining // 2)   # PHB p203

        if hp_before == 0 and _uses_death_saves(target) and remaining and not target.dead:
            # PHB p197: damage at 0 HP is a death save failure, two on a crit;
            # damage of at least max HP kills outright.
            if remaining >= target.max_hp:
                target.dead = out["dead"] = out["instant_death"] = True
            else:
                n = 2 if crit else 1
                target.death_saves["failures"] += n
                target.stable = False
                out["death_failures"] = n
                if target.death_saves["failures"] >= 3:
                    target.dead = out["dead"] = True
        elif remaining:
            target.hp -= remaining
            if target.hp <= 0:
                overflow = -target.hp
                target.hp = 0
                out["dropped"] = True
                if not _uses_death_saves(target):
                    target.dead = out["dead"] = True
                elif overflow >= target.max_hp:
                    target.dead = out["dead"] = out["instant_death"] = True   # PHB p197
                else:
                    target.add_condition("unconscious")
                    target.add_condition("prone")       # falling unconscious drops you prone
                    target.death_saves.update(successes=0, failures=0)
                    target.stable = False

        out["hp_after"] = target.hp
        text = f"{total} {types} damage" if types else f"{total} damage"
        if notes:
            text += f" ({', '.join(notes)})"
        if absorbed:
            text += f" ({absorbed} to temp HP)"
        if out["instant_death"]:
            text += f"; {target.name} is killed outright."
        elif out["dead"]:
            text += f"; {target.name} dies."
        elif out["dropped"]:
            text += f"; {target.name} drops to 0 HP and falls unconscious."
        elif out["death_failures"]:
            text += f"; {target.name} suffers {out['death_failures']} death save failure(s)."
        else:
            text += f"; {target.name} {target.hp}/{target.max_hp} HP."
        out["text"] = text
        return out

    def heal(self, token, amount: int) -> dict:
        if token.dead:
            return {"healed": 0, "text": f"{token.name} is dead; healing does nothing."}
        before = token.hp
        token.hp = min(token.max_hp, token.hp + max(0, int(amount)))
        if before == 0 and token.hp > 0:
            token.remove_condition("unconscious")
            token.death_saves.update(successes=0, failures=0)
            token.stable = False
        return {"healed": token.hp - before,
                "text": f"{token.name} regains {token.hp - before} HP ({token.hp}/{token.max_hp})."}

    # ── characters ───────────────────────────────────────────────────────────
    def token_from_sheet(self, path, token_id, pos):
        text = pathlib.Path(path).read_text(encoding="utf-8")
        return _sheet_module().read_sheet(text, token_id, pos, path=str(path))

    def token_from_monster(self, name, token_id, display_name, pos):
        return token_from_monster(_lookup_monster(name), token_id, display_name, pos)

    def write_back(self, sheet_text, token):
        return _sheet_module().write_back(sheet_text, token)

    def lasting_conditions(self, token):
        return _sheet_module().lasting_conditions(token)

    # ── encounter design ─────────────────────────────────────────────────────
    def encounter_budget(self, levels, ruleset=""):
        return _encounter_module().budget(levels, ruleset or "2014")

    def rate_encounter(self, groups, levels, ruleset="", known=None):
        # The SRD lookup is passed in rather than reached for again so that a
        # test (or a system with its own bestiary) can answer for the monsters
        # here exactly as it does for a token on the grid.
        return _encounter_module().rate(groups, levels, ruleset or "2014",
                                        lookup=_lookup_monster, suggest=_srd_suggest,
                                        known=known)

    def adventuring_day(self, levels, ruleset="", plan=None):
        # Each planned fight is rated through the same path `rate_encounter` uses,
        # so a day's worth of fights is costed by the code that costs one fight
        # and the two cannot drift apart.
        return _encounter_module().adventuring_day(levels, ruleset or "2014", plan=plan,
                                                   lookup=_lookup_monster,
                                                   suggest=_srd_suggest)

    def award_xp(self, sheet_path, amount):
        return _encounter_module().award_xp(sheet_path, amount)

    def record_awards(self, campaign_dir, entries, note=""):
        _encounter_module().record_awards(campaign_dir, entries, note)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _plain(entries) -> set:
    """Unconditional damage types from a defense list."""
    return {e for e in entries or [] if not _CONDITIONAL.search(e)}


def _conditional(entries) -> list:
    return [e for e in entries or [] if _CONDITIONAL.search(e)]


def average_damage(attack: dict) -> float:
    return sum(average(p["dice"]) for p in attack.get("damage", []))


def _defenses(value) -> list:
    """SRD defense string -> entries. A conditional clause such as "bludgeoning,
    piercing, and slashing from nonmagical attacks" stays one entry, so its
    types are never applied without the condition being checked."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).lower().strip() for v in value]
    out = []
    for p in (s.strip() for s in str(value).lower().split(";")):
        if _CONDITIONAL.search(p):
            out.append(p)
        else:
            out.extend(v.strip() for v in p.split(",") if v.strip())
    return out


# ─── SRD monster adapter ──────────────────────────────────────────────────────

def _speeds(speed: str) -> dict:
    """"30 ft., swim 30 ft." / "walk 30 ft., swim 30 ft." -> {"walk": 30, "swim": 30}"""
    out = {}
    for part in (speed or "").split(","):
        m = re.search(r"(?:([a-z]+)\s+)?(\d+)\s*ft", part.strip().lower())
        if m:
            out[m.group(1) or "walk"] = int(m.group(2))
    return out


# Upstream SRD data errors in parsed Multiattack routines, by monster index.
# veteran: "two longsword attacks. If it has a shortsword drawn, it can also
# make a shortsword attack" is three attacks, not the four the API lists.
_MULTIATTACK_ERRATA = {
    "veteran": [[{"action": "Longsword", "count": 2, "type": "melee"},
                 {"action": "Shortsword", "count": 1, "type": "melee"}]],
}


def _other_actions(record: dict) -> list:
    out = []
    for a in record.get("actions", []):
        if a.get("kind") == "attack":
            continue
        if a.get("kind") == "multiattack" and record.get("index") in _MULTIATTACK_ERRATA:
            a = dict(a, multiattack=_MULTIATTACK_ERRATA[record["index"]])
        out.append(a)
    return out


def token_from_monster(record: dict, token_id: str, name: str, pos: tuple,
                       side: str = "enemy") -> Token:
    """Build a token from an SRD monster record (lookup.lookup_record(..., "monster")).
    Saving throw bonuses are ability modifiers; the SRD build does not carry
    monster save proficiencies yet."""
    speeds = _speeds(record.get("speed", ""))
    attacks = []
    for a in record.get("actions", []):
        if a.get("kind") != "attack":
            continue
        spec = {"name": a["name"], "type": a["attack"]["type"],
                "source": a["attack"].get("source", "weapon"),
                "bonus": a["attack"]["bonus"], "damage": a.get("damage", []),
                "flags": a.get("flags", [])}
        for k in ("reach", "range"):
            if k in a["attack"]:
                spec[k] = a["attack"][k]
        for k in ("rider", "rider_effects", "rider_rest"):
            if a.get(k):
                spec[k] = a[k]
        attacks.append(spec)
    return Token(
        id=token_id, name=name, side=side, x=pos[0], y=pos[1],
        hp=int(record["hp"]), max_hp=int(record["hp"]), ac=int(record["ac"]),
        speed=speeds.get("walk", 30), swim_speed=speeds.get("swim", 0),
        dex_mod=_mod(record.get("dex", 10)),
        attacks=attacks,
        saves={ab: int((record.get("saves") or {}).get(ab, _mod(record.get(ab, 10))))
               for ab in ABILITIES},
        resistances=_defenses(record.get("resistances")),
        immunities=_defenses(record.get("immunities")),
        vulnerabilities=_defenses(record.get("vulnerabilities")),
        condition_immunities=_defenses(record.get("condition_immunities")),
        source={"kind": "srd", "ref": record.get("index", "")},
        extra={"cr": record.get("cr"), "xp": record.get("xp"),
               "type": (record.get("type") or "").lower(), "int": record.get("int", 10),
               "traits": _traits(record), "alignment": record.get("alignment", ""),
               "actions": _other_actions(record),
               "abilities": {ab: int(record.get(ab, 10)) for ab in ABILITIES},
               "skills": dict(record.get("skills") or {}),
               "passive_perception": record.get("passive_perception"),
               "usage": _usage(record.get("actions", []))},
    )


def _traits(record: dict) -> list:
    """Trait names ("Pack Tactics") from the description paragraphs before the actions."""
    names = []
    for para in (record.get("description") or "").split("\n\n"):
        if para.startswith("Action"):
            break
        m = re.match(r"([A-Z][\w' /-]{2,40}?)(?: \([^)]*\))?: ", para)
        if m:
            names.append(m.group(1).strip())
    return names


def _usage(actions: list) -> dict:
    """Limited-use actions: {"Fire Breath": {"charged": True}} for "recharge on
    roll", {"Name": {"left": n}} for "per day" (the whole fight counts as one day)."""
    out = {}
    for a in actions:
        u = a.get("usage") or {}
        if u.get("type") == "recharge on roll":
            out[a["name"]] = {"charged": True, "dice": u.get("dice", "1d6"),
                              "min": int(u.get("min_value", 6))}
        elif u.get("type") == "per day":
            out[a["name"]] = {"left": int(u.get("times", 1))}
        elif u.get("type") in ("recharge after rest",):
            out[a["name"]] = {"left": 1}
    return out


def _spells_module():
    import importlib.util
    name = "tactics_spells_dnd5e"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, pathlib.Path(__file__).with_name("tactics_spells.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def _sheet_module():
    import importlib.util
    name = "tactics_sheet_dnd5e"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, pathlib.Path(__file__).with_name("tactics_sheet.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def _encounter_module():
    import importlib.util
    name = "encounter_design_dnd5e"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, pathlib.Path(__file__).with_name("encounter.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def _srd_suggest(name: str) -> list:
    """Near-miss monster names, for a typo the GM typed from memory.

    Best effort by design: a missing or half-built dataset is a reason to say
    "no such monster", never a traceback in the middle of designing a fight.
    """
    here = str(pathlib.Path(__file__).parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import lookup
    try:
        return [nm for nm, _cat in lookup.suggest(name, category="monster", n=3)]
    except Exception:                                             # noqa: BLE001
        return []


def _lookup_monster(name: str) -> dict:
    here = str(pathlib.Path(__file__).parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import lookup                                   # systems/dnd5e/lookup.py
    rec = lookup.lookup_record(name, category="monster")
    if not rec:
        raise ValueError(f"no SRD monster {name!r} (build the SRD: python3 systems/dnd5e/build_srd.py)")
    if "actions" not in rec:
        raise ValueError("the SRD dataset predates structured actions; rebuild it: "
                         "python3 systems/dnd5e/build_srd.py --no-fvtt")
    return rec


RULES = DnD5e()
