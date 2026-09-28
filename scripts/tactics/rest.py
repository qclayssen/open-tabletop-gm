"""rest.py: short and long rest mechanics for the tactical combat engine.

Handles Hit Dice spending, HP recovery, feature recharge, spell slot restoration,
and condition clearing per 5e rules.
"""

from __future__ import annotations

from .state import Encounter, Token
from .roller import Roller, average
from . import effects


# Conditions that end on a long rest (PHB Appendix A)
LONG_REST_CLEAR = {
    "exhaustion",  # Reduced by 1, not cleared entirely
    "frightened",
    "charmed",
    "poisoned",
    # Note: prone, grappled, restrained, stunned, etc. are from effects/combat, not long rest
}


def _hit_die_type(token: Token) -> str:
    """Return the hit die type for this token (e.g., '1d10')."""
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return hd.get("die", "1d10")
    elif isinstance(hd, str):
        return hd
    return "1d10"


def _hit_dice_remaining(token: Token) -> int:
    """Return the number of Hit Dice remaining for this token."""
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return hd.get("remaining", 0)
    elif isinstance(hd, int):
        return hd
    return 0


def _set_hit_dice_remaining(token: Token, n: int) -> None:
    """Set the number of Hit Dice remaining for this token."""
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        hd["remaining"] = n
    else:
        token.extra["hit_dice"] = {"die": _hit_die_type(token), "remaining": n}


def _hit_dice_total(token: Token) -> int:
    """Return the total Hit Dice for this token (based on level)."""
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return hd.get("total", 1)
    return 1


def _con_mod(token: Token) -> int:
    """Return the Constitution modifier for this token."""
    scores = token.extra.get("abilities", {})
    if "con" in scores:
        return (int(scores["con"]) - 10) // 2
    # Fallback to saves
    return token.saves.get("con", 0)


def _has_feature(token: Token, feature_name: str) -> bool:
    """Check if token has a feature by name (case-insensitive)."""
    traits = token.extra.get("traits", [])
    if isinstance(traits, list):
        return any(feature_name.lower() in str(t).lower() for t in traits)
    return False


def short_rest(enc: Encounter, token_ids: list[str] = None, roller: Roller = None) -> list[str]:
    """Apply short rest benefits to specified tokens (or all PCs if none specified).

    Returns list of text lines describing what happened.
    """
    if token_ids is None:
        targets = [t for t in enc.tokens.values() if t.side == "pc"]
    else:
        targets = [enc.token(tid) for tid in token_ids]

    lines = []
    for token in targets:
        if token.dead:
            lines.append(f"{token.name} is dead; short rest has no effect.")
            continue

        # Reset action economy
        token.reaction_used = False
        token.dodging = False

        # Spend Hit Dice to heal (auto-spend all remaining in auto mode, or show available in players mode)
        remaining = _hit_dice_remaining(token)
        max_hd = _hit_dice_total(token)
        if remaining > 0 and token.hp < token.max_hp:
            if roller is None:
                roller = Roller()
            die = _hit_die_type(token)
            con_mod = _con_mod(token)
            
            # In auto mode (--for-me), spend all Hit Dice to heal
            if roller.for_me:
                total_heal = 0
                spent = 0
                while remaining > 0 and token.hp < token.max_hp:
                    d = roller.roll(die, token.name, "Hit Die")
                    heal = d.total + con_mod
                    actual_heal = min(heal, token.max_hp - token.hp)
                    token.hp += actual_heal
                    total_heal += actual_heal
                    remaining -= 1
                    spent += 1
                _set_hit_dice_remaining(token, remaining)
                lines.append(f"{token.name}: spent {spent} Hit Die ({die} + {con_mod:+d}), healed {total_heal} HP (now {token.hp}/{token.max_hp}).")
            else:
                # Players mode - show available
                lines.append(f"{token.name}: {remaining}/{max_hd} Hit Dice ({die} + {con_mod:+d} each) available. Use 'rest short --for-me' to auto-spend.")

        # Recharge features that recharge on short rest
        recharged = _recharge_short_rest_features(token)
        if recharged:
            lines.append(f"{token.name}: recharged {', '.join(recharged)}.")

    if not lines:
        lines.append("Short rest completed.")
    return lines


def _recharge_short_rest_features(token: Token) -> list[str]:
    """Recharge features that recharge on short rest. Returns list of recharged feature names."""
    recharged = []
    usage = token.extra.get("usage", {})
    for name, data in usage.items():
        if isinstance(data, dict) and data.get("type") == "short_rest":
            data["left"] = data.get("max", 1)
            recharged.append(name)
        elif isinstance(data, dict) and data.get("charged") is False:
            # Some features marked as recharging on short rest
            data["charged"] = True
            recharged.append(name)
    return recharged


def _recharge_long_rest_features(token: Token) -> list[str]:
    """Recharge features that recharge on long rest. Returns list of recharged feature names."""
    recharged = []
    usage = token.extra.get("usage", {})
    for name, data in usage.items():
        if isinstance(data, dict):
            if data.get("type") in ("long_rest", "per day", "recharge after rest"):
                data["left"] = data.get("max", 1)
                recharged.append(name)
            elif data.get("charged") is False:
                data["charged"] = True
                recharged.append(name)
    return recharged


def _restore_spell_slots(token: Token, short_rest: bool = False) -> list[str]:
    """Restore spell slots. Returns list of text lines."""
    lines = []
    slots = token.extra.get("slots")
    if not slots:
        return lines

    class_name = token.extra.get("spellcasting", {}).get("class", "").lower()

    if short_rest:
        # Wizard Arcane Recovery: once per day, recover up to half level (rounded up) spell slot levels
        if class_name == "wizard" and _has_feature(token, "arcane recovery"):
            if not token.extra.get("arcane_recovery_used"):
                level = token.extra.get("level", 1)
                max_levels = (level + 1) // 2
                lines.append(f"{token.name}: Arcane Recovery available (up to {max_levels} slot levels).")
        # Warlock Pact Magic: all slots restored on short rest
        elif class_name == "warlock":
            for lv in slots:
                if slots[lv].get("used", 0) > 0:
                    slots[lv]["used"] = 0
            lines.append(f"{token.name}: all Pact Magic slots restored.")
        # Sorcerer Sorcery Points: can convert to slots (handled separately)
        elif class_name == "sorcerer":
            pass  # Sorcery points handled elsewhere
    else:
        # Long rest: restore all spell slots
        for lv in slots:
            if slots[lv].get("used", 0) > 0:
                slots[lv]["used"] = 0
        lines.append(f"{token.name}: all spell slots restored.")

    return lines


def _clear_long_rest_conditions(token: Token) -> list[str]:
    """Clear conditions that end on a long rest. Returns list of cleared condition names."""
    cleared = []
    for cond in list(token.conditions):
        if cond.lower() in LONG_REST_CLEAR:
            if cond.lower() == "exhaustion":
                # Exhaustion reduced by 1, not cleared
                level = token.extra.get("exhaustion_level", 0)
                if level > 0:
                    token.extra["exhaustion_level"] = level - 1
                    if token.extra["exhaustion_level"] == 0:
                        token.remove_condition("exhaustion")
                        cleared.append("exhaustion (reduced to 0)")
                    else:
                        cleared.append(f"exhaustion (reduced to level {token.extra['exhaustion_level']})")
            else:
                token.remove_condition(cond)
                cleared.append(cond)
    return cleared


def _reset_death_saves(token: Token) -> None:
    """Reset death saves for a token that has HP > 0."""
    if token.hp > 0:
        token.death_saves.update(successes=0, failures=0)
        token.stable = False


def long_rest(enc: Encounter, token_ids: list[str] = None) -> list[str]:
    """Apply long rest benefits to specified tokens (or all PCs if none specified).

    Returns list of text lines describing what happened.
    """
    if token_ids is None:
        targets = [t for t in enc.tokens.values() if t.side == "pc"]
    else:
        targets = [enc.token(tid) for tid in token_ids]

    lines = []
    for token in targets:
        if token.dead:
            lines.append(f"{token.name} is dead; long rest has no effect.")
            continue

        # Full HP
        if token.hp < token.max_hp:
            healed = token.max_hp - token.hp
            token.hp = token.max_hp
            lines.append(f"{token.name}: healed {healed} HP (now {token.max_hp}/{token.max_hp}).")

        # Temp HP cleared
        if token.temp_hp > 0:
            token.temp_hp = 0
            lines.append(f"{token.name}: temporary HP cleared.")

        # Half Hit Dice (rounded up) restored
        max_hd = _hit_dice_total(token)
        restored = (max_hd + 1) // 2
        _set_hit_dice_remaining(token, min(_hit_dice_remaining(token) + restored, max_hd))
        lines.append(f"{token.name}: {restored} Hit Dice restored (now {_hit_dice_remaining(token)}/{max_hd}).")

        # All spell slots restored
        lines.extend(_restore_spell_slots(token, short_rest=False))

        # All features recharged
        recharged = _recharge_long_rest_features(token)
        if recharged:
            lines.append(f"{token.name}: recharged {', '.join(recharged)}.")

        # Exhaustion reduced by 1
        cleared = _clear_long_rest_conditions(token)
        if cleared:
            lines.append(f"{token.name}: cleared {', '.join(cleared)}.")

        # Reset death saves if HP > 0
        _reset_death_saves(token)

        # Reset action economy
        token.reaction_used = False
        token.dodging = False

        # Clear concentration if it was held
        if token.concentration:
            lines.append(effects.end_concentration(enc, token, "long rest"))
            token.concentration = None

    if not lines:
        lines.append("Long rest completed.")
    return lines


def cmd_rest(args, camp_dir) -> tuple[str, dict]:
    """CLI command handler for rest command."""
    from .cli import _load, _campaign, _roller
    from . import state, sync

    enc = _load(camp_dir)
    if enc.status != "active":
        raise Stop("No active combat. Rest is only available during combat.")

    roller = _roller(args)

    # Determine targets
    if getattr(args, "token", None):
        token_ids = [args.token]
    else:
        token_ids = None  # All PCs

    if args.type == "short":
        lines = short_rest(enc, token_ids, roller)
    else:
        lines = long_rest(enc, token_ids)

    # Advance calendar time
    try:
        import subprocess
        import sys
        _SCRIPTS = str(pathlib.Path(__file__).resolve().parents[1])
        subprocess.run([
            sys.executable, f"{_SCRIPTS}/calendar.py",
            "-c", _campaign(args), "rest", args.type
        ], capture_output=True, timeout=5)
    except Exception:
        pass  # Calendar not initialized is OK

    state.save(enc, state.encounter_path(camp_dir))
    sync.sync_tracker(camp_dir, enc, drop_monsters=False)
    sync.push_display(enc, enc.meta)

    text = "\n".join(lines)
    return text, {"rest_type": args.type, "targets": token_ids}


# Need to import pathlib for the calendar call
import pathlib

# Avoid circular import with cli.py - define Stop here
class Stop(Exception):
    """Print a message and exit with a code; nothing is saved."""

    def __init__(self, text: str, code: int = 1):
        super().__init__(text)
        self.text, self.code = text, code