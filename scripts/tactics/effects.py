"""effects.py: timed and linked effects, concentration, grapples and spell reactions.

An effect is a small dict kept on the creature it affects (Token.effects):

    {"name": "shield", "source": "kairos",       who made it
     "ends": "start" | "end" | None,             the start or end of the source's next turn
     "armed": False,                             ("end" only) the source's next turn has begun
     "concentration": True,                      ends when the source's concentration ends
     "conditions": ["paralyzed"],                conditions it grants (removed with it)
     "ac": 5,                                    AC bonus while it lasts
     "save_penalty": "1d4",                      subtract from the next saving throw, then gone
     "advantage_next": True,                     advantage on the next attack or save, then gone
     "advantage_vs": "frog-1",                   advantage on the next attack against that target
     "grapple": {"escape_dc": 11},               held by the source
     "save": {"ability": "wis", "dc": 13}, "repeat": "end"}   save again at the end of each turn

The engine owns the lifecycle: start and end of turns, concentration, the
grappler letting go. The rules read the numbers (ac, save_penalty, advantage)
through the helpers below, so nothing is remembered by the GM.
"""

from __future__ import annotations

from .core import decide, hostile, player_rolls, rules_for
from .roller import Roller


# ─── adding and removing ──────────────────────────────────────────────────────

# Conditions that outlast the effect that caused them: a creature knocked prone
# by Hideous Laughter is still prone when the laughter ends.
PERSISTS = {"prone"}


def add(token, effect: dict) -> list:
    """Attach an effect; returns the conditions it could not grant (immunity).
    effect["granted"] records the conditions this effect added, so removing it
    never strips a condition the creature already had for another reason."""
    immune = {c.lower() for c in token.condition_immunities}
    wanted = effect.get("conditions", [])
    blocked = [c for c in wanted if c in immune]
    kept = [c for c in wanted if c not in immune]
    for c in kept:
        if c in PERSISTS:
            token.add_condition(c)
    tracked = [c for c in kept if c not in PERSISTS]
    effect = dict(effect, conditions=tracked,
                  granted=[c for c in tracked if not token.has(c)])
    effect.setdefault("armed", False)
    token.effects.append(effect)
    for c in tracked:
        token.add_condition(c)
    return blocked


def remove(token, effect: dict) -> None:
    if effect in token.effects:
        token.effects.remove(effect)
    still = {c for e in token.effects for c in e.get("conditions", [])}
    for c in effect.get("granted", effect.get("conditions", [])):
        if c not in still:
            token.remove_condition(c)


def remove_granting(token, condition: str) -> list:
    """Remove every effect that grants `condition` (a GM ruling). Returns their names."""
    gone = [e for e in token.effects if condition in e.get("conditions", [])]
    for e in gone:
        remove(token, e)
    return [e.get("name", "effect") for e in gone]


def check_incapacitated(enc, token) -> list:
    """A creature that can no longer act (paralyzed, stunned, dropped) loses
    concentration and lets go of what it grapples."""
    R = rules_for(enc)
    if R.can_act(token) and not token.dead:
        return []
    lines = []
    if token.concentration:
        lines.append(end_concentration(enc, token, "incapacitated"))
    lines += release(enc, token, "incapacitated")
    return lines


def ac_bonus(token) -> int:
    return sum(int(e.get("ac", 0)) for e in token.effects)


def find(token, **match):
    return [e for e in token.effects if all(e.get(k) == v for k, v in match.items())]


def take(token, pred):
    """Remove and return the first effect for which pred(effect) is true (a one-shot bonus)."""
    for e in list(token.effects):
        if pred(e):
            remove(token, e)
            return e
    return None


# ─── turn boundaries ──────────────────────────────────────────────────────────

def start_of_turn(enc, token) -> list:
    """Effects that end when `token`'s turn starts; arm the ones that end when it
    finishes. Returns text lines."""
    lines = []
    for t in enc.tokens.values():
        for e in list(t.effects):
            if e.get("expires_round") is not None and e["expires_round"] <= enc.round \
                    and e.get("source") == token.id:
                remove(t, e)                       # "within 1 minute" (10 rounds)
                continue
            if e.get("source") != token.id:
                continue
            if e.get("ends") == "start":
                remove(t, e)
                if e.get("name") == "shield":
                    lines.append(f"{t.name}'s Shield fades.")
            elif e.get("ends") == "end":
                e["armed"] = True
    readied = token.extra.pop("readied", None)
    if readied:
        lines.append(f"{token.name}'s readied {readied.get('label', 'action')} is lost.")
        if readied.get("concentration") and token.concentration:
            lines.append(end_concentration(enc, token, "the readied spell was never released"))
    return lines


def end_of_turn(enc, token, roller: Roller) -> list:
    """Repeat saves for `token`'s own effects ("save ends"), then drop effects
    that last until the end of the source's next turn."""
    R = rules_for(enc)
    lines = []
    for e in list(token.effects):
        if e.get("repeat") == "end" and e.get("save") and not token.dead:
            res = R.saving_throw(token, e["save"]["ability"], e["save"]["dc"], roller,
                                 player_rolls(enc, token, roller))
            if res["success"]:
                remove(token, e)
                lines.append(res["text"] + f" {e['name']} ends on {token.name}.")
            else:
                lines.append(res["text"] + f" Still {', '.join(e.get('conditions', [])) or e['name']}.")
    for t in enc.tokens.values():
        for e in list(t.effects):
            if e.get("source") == token.id and e.get("ends") == "end" and e.get("armed"):
                remove(t, e)
    return lines


# ─── concentration ────────────────────────────────────────────────────────────

def end_concentration(enc, caster, reason: str = "") -> str:
    spell = caster.concentration
    caster.concentration = None
    ended = []
    for t in enc.tokens.values():
        for e in list(t.effects):
            if e.get("source") == caster.id and e.get("concentration"):
                remove(t, e)
                ended.append(t.name)
    r = caster.extra.get("readied")
    if r and r.get("concentration"):
        caster.extra.pop("readied", None)
    text = f"{caster.name} loses concentration on {spell}" + (f" ({reason})" if reason else "") + "."
    if ended:
        text += f" It ends on {', '.join(sorted(set(ended)))}."
    return text


def start_concentration(enc, caster, spell: str) -> list:
    lines = []
    if caster.concentration:
        lines.append(end_concentration(enc, caster, f"now concentrating on {spell}"))
    caster.concentration = spell
    return lines


# ─── grapples ─────────────────────────────────────────────────────────────────

def grappling(enc, grappler) -> list:
    """[(target, effect)] for creatures `grappler` is holding."""
    return [(t, e) for t in enc.tokens.values() for e in t.effects
            if e.get("grapple") and e.get("source") == grappler.id]


def release(enc, grappler, reason: str) -> list:
    lines = []
    for t, e in grappling(enc, grappler):
        remove(t, e)
        lines.append(f"{t.name} is no longer grappled by {grappler.name} ({reason}).")
    return lines


def after_move(enc, mover) -> list:
    """A grappler that moves out of reach lets go (dragging is not supported)."""
    grid, R = enc.board(), rules_for(enc)
    lines = []
    for t, e in grappling(enc, mover):
        if grid.distance(mover.pos, t.pos) > R.reach(mover):
            remove(t, e)
            lines.append(f"{mover.name} moves away and releases {t.name}.")
    for e in list(mover.effects):
        g = e.get("grapple")
        src = enc.tokens.get(e.get("source"))
        if g and src and grid.distance(mover.pos, src.pos) > R.reach(src):
            remove(mover, e)
            lines.append(f"{mover.name} is out of {src.name}'s reach: the grapple ends.")
    return lines


# ─── after damage ─────────────────────────────────────────────────────────────

def after_damage(enc, roller: Roller, target, dmg: dict) -> list:
    """Concentration saves (one per damage instance) and what dropping ends."""
    if not dmg:
        return []
    R = rules_for(enc)
    lines = []
    if target.concentration and R.can_act(target) and not target.dead and dmg.get("concentration_dc"):
        res = R.saving_throw(target, "con", dmg["concentration_dc"], roller,
                             player_rolls(enc, target, roller))
        lines.append("Concentration: " + res["text"])
        if not res["success"]:
            lines.append(end_concentration(enc, target))
    lines += check_incapacitated(enc, target)
    if target.dead:
        for e in list(target.effects):             # nothing holds or affects a corpse
            remove(target, e)
    return lines


# ─── spell reactions ──────────────────────────────────────────────────────────

def knows(token, spell: str) -> bool:
    return spell in [s.lower() for s in token.extra.get("spells", [])]


def slot_for(token, level: int = 1):
    """The lowest slot level >= level with a slot left, or None."""
    slots = token.extra.get("slots") or {}
    for lv in sorted(slots, key=int):
        s = slots[lv]
        if int(lv) >= level and s.get("used", 0) < s.get("total", 0):
            return lv
    return None


def spend_slot(token, lv: str) -> None:
    token.extra["slots"][lv]["used"] += 1


def _can_cast_reaction(enc, token, spell: str) -> bool:
    return (token.active and knows(token, spell) and rules_for(enc).can_react(token)
            and slot_for(token) is not None)


def barbs_casters(enc, rolled_by, near=None) -> list:
    """Creatures that can answer `rolled_by`'s success with Silvery Barbs: hostile
    to it, within 60 ft, able to see it, a reaction and a slot left."""
    grid = enc.board()
    out = []
    for t in enc.tokens.values():
        if (hostile(t, rolled_by) and _can_cast_reaction(enc, t, "silvery barbs")
                and grid.distance(t.pos, rolled_by.pos) <= 60
                and grid.line_of_sight(t.pos, rolled_by.pos)):
            out.append(t)
    return out


def silvery_barbs(enc, roller: Roller, caster, rolled_by, what: str, natural: int,
                  total: int, beneficiary, reactions: dict):
    """Offer Silvery Barbs against a success. Returns (natural, total, lines) with
    the lower d20 kept, or None if not cast."""
    key = f"{caster.id}:silvery barbs"
    prompt = (f"{rolled_by.name} succeeds on {what} ({total}). "
              f"Cast Silvery Barbs to force a reroll?")
    if not decide(caster, key, "silvery_barbs", prompt, reactions):
        return None
    lv = slot_for(caster)
    spend_slot(caster, lv)
    caster.reaction_used = True
    r = roller.roll("1d20", rolled_by.name, f"{what} (Silvery Barbs reroll)")
    keep = min(natural, r.natural)
    new_total = total - natural + keep
    who = beneficiary if beneficiary is not None else caster
    add(who, {"name": "silvery barbs", "source": caster.id, "advantage_next": True,
              "expires_round": enc.round + 10})
    lines = [f"{caster.name} casts Silvery Barbs (level {lv} slot): {rolled_by.name} rerolls "
             f"{r.natural}, keeps {keep} ({new_total}). {who.name} gains advantage on the next roll."]
    return keep, new_total, lines


def on_hit(enc, roller: Roller, attacker, target, natural: int, total: int, ac: int,
           reactions: dict) -> dict:
    """Reactions to an attack that hits: Silvery Barbs first (by any hostile of
    the attacker), then Shield (by the target) if it still hits and +5 AC would
    make it miss. Returns {"natural", "total", "ac", "lines"}."""
    lines = []
    for caster in barbs_casters(enc, attacker):
        beneficiary = target if not hostile(caster, target) else caster
        res = silvery_barbs(enc, roller, caster, attacker, f"an attack on {target.name}",
                            natural, total, beneficiary, reactions)
        if res:
            natural, total, more = res
            lines += more
            break
    hit = natural == 20 or (natural != 1 and total >= ac)
    if (hit and natural != 20 and total < ac + 5 and hostile(attacker, target)
            and _can_cast_reaction(enc, target, "shield")):
        key = f"{target.id}:shield"
        prompt = (f"{attacker.name} hits {target.name} ({total} vs AC {ac}). "
                  f"Cast Shield (+5 AC, the attack misses)?")
        if decide(target, key, "shield", prompt, reactions):
            lv = slot_for(target)
            spend_slot(target, lv)
            target.reaction_used = True
            add(target, {"name": "shield", "source": target.id, "ends": "start", "ac": 5})
            ac += 5
            lines.append(f"{target.name} casts Shield (level {lv} slot): AC {ac} until their next turn.")
    return {"natural": natural, "total": total, "ac": ac, "lines": lines}


def shield_vs_missiles(enc, attacker, target, reactions: dict) -> list:
    """Magic Missile: Shield blocks every dart aimed at the caster. Returns lines
    ([] if Shield is not cast)."""
    if target.effects and find(target, name="shield"):
        return [f"{target.name}'s Shield blocks the darts."]
    if not (hostile(attacker, target) and _can_cast_reaction(enc, target, "shield")):
        return []
    key = f"{target.id}:shield"
    if not decide(target, key, "shield", f"Magic Missile darts fly at {target.name}. "
                  "Cast Shield to block them?", reactions):
        return []
    lv = slot_for(target)
    spend_slot(target, lv)
    target.reaction_used = True
    add(target, {"name": "shield", "source": target.id, "ends": "start", "ac": 5})
    return [f"{target.name} casts Shield (level {lv} slot) and the darts vanish."]
