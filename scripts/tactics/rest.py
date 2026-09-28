"""rest.py: short and long rest — the pause in a fight that costs the party resources.

    short_rest(enc, token_ids, roller)   1 hour: Hit Dice, short-rest features, Pact Magic
    long_rest(enc, token_ids)            8 hours: HP, half the Hit Dice, every spell slot
    cmd_rest(args, enc, roller)          the `rest` command, on the caller's encounter
    advance_calendar(campaign, kind)     move the in-world clock on by the rest

Why this module exists next to slots.py: slots.py is the one place that knows
what a spell slot is, and it can only be read on the way back up if something
decides a rest happened. A slot that is only ever spent is a resource the party
runs out of mid-campaign, so the two are not separable.

Two rules the 5e rests turn on, and the second one is the caster's, not the
engine's:

  * A long rest restores every slot, full stop (PHB p201). There is nothing to
    decide, so the engine does it and says so.
  * A short rest restores nothing for most classes (PHB p201). A Warlock's Pact
    Magic refills by itself because the feature says it does; a Wizard's Arcane
    Recovery buys slots back with a once-per-long-rest resource, so the engine
    spends the cheapest slots it can and reports exactly which ones, because
    "up to half your level in slot levels" is a ceiling the caster chooses
    within. Everything else the GM rules.

Hit Dice are the same shape of decision and are handled the same way: a long
rest restores half, rounded up, whatever the caster did with the rest of them.
Short-rest healing spends Hit Dice and rolls for them, which is the one place
this module rolls a die — and only with the roller's own dice, and only when
the GM says `--for-me`, because a player's hit points are the player's to roll.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from . import effects, slots
from .core import CombatError
from .roller import Roller
from .state import Encounter, Token

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]

# Conditions a long rest ends (PHB Appendix A). "exhaustion" is reduced by one
# rather than cleared, so it is handled apart in _clear_long_rest_conditions.
LONG_REST_CLEAR = {"frightened", "charmed", "poisoned"}


# ─── hit dice ─────────────────────────────────────────────────────────────────

def _hit_die_type(token: Token) -> str:
    """The hit die notation for a token, whatever shape it was stored in.

    build_srd.py writes "d10"; the engine writes {"die": "d10", "remaining": n}.
    """
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return str(hd.get("die") or "1d10")
    if isinstance(hd, str) and hd.strip():
        return hd.strip()
    return "1d10"


def _hit_dice_remaining(token: Token) -> int:
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return int(hd.get("remaining") or 0)
    if isinstance(hd, int) and not isinstance(hd, bool):
        return hd
    return 0


def _set_hit_dice_remaining(token: Token, n: int) -> None:
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        hd["remaining"] = n
    else:
        token.extra["hit_dice"] = {"die": _hit_die_type(token), "remaining": n}


def _hit_dice_total(token: Token) -> int:
    """How many Hit Dice the character has in total."""
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return int(hd.get("total") or 1)
    return 1


def _con_mod(token: Token) -> int:
    scores = token.extra.get("abilities") or {}
    try:
        return (int(str(scores["con"]).strip()) - 10) // 2
    except (KeyError, TypeError, ValueError):
        return int(token.saves.get("con") or 0)


# ─── features ─────────────────────────────────────────────────────────────────

def _recharge(token: Token, kinds: tuple) -> list:
    """Reset the features in `extra["usage"]` that recharge on one of `kinds`."""
    out = []
    for name, data in (token.extra.get("usage") or {}).items():
        if not isinstance(data, dict):
            continue
        if data.get("type") in kinds:
            data["left"] = data.get("max", 1)
            out.append(name)
        elif data.get("charged") is False:
            data["charged"] = True
            out.append(name)
    return out


SHORT_REST_RECHARGE = ("short_rest", "per short rest")
LONG_REST_RECHARGE = ("long_rest", "per day", "per long rest", "recharge after rest")


# ─── the rests ────────────────────────────────────────────────────────────────

def _targets(enc: Encounter, token_ids: list = None) -> list:
    """The named tokens, or every PC — a monster does not benefit from the party's rest."""
    if token_ids is None:
        return [t for t in enc.tokens.values() if t.side == "pc"]
    return [enc.token(tid) for tid in token_ids]


def short_rest(enc: Encounter, token_ids: list = None, roller: Roller = None) -> list:
    """An hour's rest. Returns GM-facing lines describing what happened."""
    lines = []
    for token in _targets(enc, token_ids):
        if token.dead:
            lines.append(f"{token.name} is dead; short rest has no effect.")
            continue
        token.reaction_used = False
        token.dodging = False
        lines += _spend_hit_dice(token, roller)
        recharged = _recharge(token, SHORT_REST_RECHARGE)
        if recharged:
            lines.append(f"{token.name}: recharged {', '.join(recharged)}.")
        lines += slots.short_rest(token)
    return lines or ["Short rest: nothing to recover."]


def _spend_hit_dice(token: Token, roller: Roller) -> list:
    """Spend Hit Dice to heal. In auto mode all of them, in players mode none.

    A player rolls their own hit points, so the engine will not spend them on
    the GM's say-so: it reports what is available and waits for `--for-me`, the
    same rule every other player die in this engine already follows.
    """
    remaining, total = _hit_dice_remaining(token), _hit_dice_total(token)
    if remaining <= 0 or token.hp >= token.max_hp:
        return []
    die, con = _hit_die_type(token), _con_mod(token)
    if roller is None:
        roller = Roller()
    if not roller.for_me:
        return [f"{token.name}: {remaining}/{total} Hit Dice ({die} {con:+d} each) available. "
                f"Use `rest short --for-me` to spend them."]
    healed = spent = 0
    while remaining > 0 and token.hp < token.max_hp:
        gained = min(roller.roll(die, token.name, "Hit Die").total + con, token.max_hp - token.hp)
        token.hp += gained
        healed += gained
        spent += 1
        remaining -= 1
    _set_hit_dice_remaining(token, remaining)
    noun = "Hit Die" if spent == 1 else "Hit Dice"
    return [f"{token.name}: spent {spent} {noun} ({die} {con:+d}), healed {healed} HP "
            f"(now {token.hp}/{token.max_hp})."]


def long_rest(enc: Encounter, token_ids: list = None) -> list:
    """Eight hours. Returns GM-facing lines describing what happened."""
    lines = []
    for token in _targets(enc, token_ids):
        if token.dead:
            lines.append(f"{token.name} is dead; long rest has no effect.")
            continue
        if token.hp < token.max_hp:
            healed = token.max_hp - token.hp
            token.hp = token.max_hp
            lines.append(f"{token.name}: healed {healed} HP (now {token.max_hp}/{token.max_hp}).")
        if token.temp_hp > 0:
            token.temp_hp = 0
            lines.append(f"{token.name}: temporary HP cleared.")
        total = _hit_dice_total(token)
        restored = (total + 1) // 2
        _set_hit_dice_remaining(token, min(_hit_dice_remaining(token) + restored, total))
        lines.append(f"{token.name}: {restored} Hit Dice restored "
                     f"(now {_hit_dice_remaining(token)}/{total}).")
        lines += slots.long_rest(token)
        recharged = _recharge(token, LONG_REST_RECHARGE)
        if recharged:
            lines.append(f"{token.name}: recharged {', '.join(recharged)}.")
        cleared = _clear_long_rest_conditions(token)
        if cleared:
            lines.append(f"{token.name}: cleared {', '.join(cleared)}.")
        if token.hp > 0:
            token.death_saves.update(successes=0, failures=0)
            token.stable = False
        token.reaction_used = False
        token.dodging = False
        if token.concentration:
            lines.append(effects.end_concentration(enc, token, "long rest"))
            token.concentration = None
    return lines or ["Long rest: nothing to recover."]


def _clear_long_rest_conditions(token: Token) -> list:
    """Conditions a long rest ends. Exhaustion goes down by one, not to zero."""
    cleared = []
    for cond in list(token.conditions):
        low = cond.lower()
        if low not in LONG_REST_CLEAR and low != "exhaustion":
            continue
        if low == "exhaustion":
            level = int(token.extra.get("exhaustion_level") or 0) - 1
            token.extra["exhaustion_level"] = max(0, level)
            if level <= 0:
                token.remove_condition("exhaustion")
                cleared.append("exhaustion")
            else:
                cleared.append(f"exhaustion (down to level {level})")
        else:
            token.remove_condition(low)
            cleared.append(low)
    return cleared


# ─── the command ──────────────────────────────────────────────────────────────

def cmd_rest(args, enc: Encounter, roller: Roller = None) -> tuple:
    """The `rest short|long [--token NAME]` command, on the caller's encounter.

    Takes the encounter `cli.run` has already loaded rather than loading its
    own: the caller saves the copy it holds after every command, so a second
    load here would mean a second copy, and whichever was written last would be
    the one that did not rest.
    """
    if enc.status != "active":
        raise CombatError("No combat is running. Rest between fights, or start one first.")
    token_ids = [args.token] if getattr(args, "token", None) else None
    if args.type == "short":
        lines = short_rest(enc, token_ids, roller)
    else:
        lines = long_rest(enc, token_ids)
    return "\n".join(lines), {"rest_type": args.type,
                              "targets": [t.id for t in _targets(enc, token_ids)],
                              "slots": {t.id: slots.read(t) for t in _targets(enc, token_ids)}}


def advance_calendar(campaign: str, rest_type: str) -> str:
    """Move the in-world clock on by the rest. '' if the campaign has no calendar.

    A subprocess because calendar.py is a command-line tool with its own
    argparse, and a rest that is eight hours of the party's time is not
    something the engine should be guessing at twice. A campaign without a
    calendar — every test, and every table that has not run `calendar.py init` —
    is not an error: the rest happened, only the clock did not move.
    """
    try:
        done = subprocess.run([sys.executable, str(_SCRIPTS / "calendar.py"),
                               "-c", campaign, "rest", rest_type],
                              capture_output=True, encoding="utf-8", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "").strip() if done.returncode == 0 else ""
