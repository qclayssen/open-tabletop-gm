"""actions.py: Help, Hide, Escape a grapple, Ready and releasing a readied action.

    help_action(enc, helper, ally, target)       the ally's next attack on target has advantage
    hide(enc, roller, token)                     Stealth vs every hostile's passive Perception
    escape(enc, roller, token)                   Athletics or Acrobatics vs the escape DC
    ready(enc, roller, token, kind, ...)         store an attack, a spell or a move and a trigger
    trigger(enc, roller, token, target)          the GM says the trigger happened: spend the reaction

Triggers are free text ("when a frog comes out of the water"): the engine
cannot read them, so the GM calls `trigger` when one happens. The readied
action is lost at the start of the creature's next turn, and a readied spell
is lost if concentration breaks (PHB p193).
"""

from __future__ import annotations

from . import effects as fx
from . import spells
from .core import CombatError, hostile, log, player_rolls, resolve, rules_for
from .engine import (_attack_context, _find_attack, _require_action, _resolve_attack, move)
from .grid import label, parse_square
from .roller import Roller


def help_action(enc, helper_ref, ally_ref, target_ref) -> dict:
    """PHB p192, attack version: the target must be within 5 ft of the helper;
    the ally's first attack roll against it before the helper's next turn has
    advantage."""
    h, ally, t = resolve(enc, helper_ref), resolve(enc, ally_ref), resolve(enc, target_ref)
    _require_action(enc, h)
    if ally.id == h.id or hostile(h, ally) or not ally.active:
        raise CombatError(f"{h.name} can only help an ally.")
    if not (t.active and hostile(h, t)):
        raise CombatError(f"{t.name} is not an enemy.")
    dist = enc.board().distance(h.pos, t.pos)
    if dist > 5:
        raise CombatError(f"{t.name} is {dist} ft from {h.name}; Help needs them within 5 ft.")
    fx.add(ally, {"name": "help", "source": h.id, "ends": "start", "advantage_vs": t.id})
    enc.turn.action_used = enc.turn.undo_locked = True
    text = (f"{h.name} helps {ally.name}: advantage on their next attack against {t.name} "
            f"before {h.name}'s next turn.")
    log(enc, "help", h.id, text)
    return {"text": text}


def hide(enc, roller: Roller, token_ref) -> dict:
    """PHB p177 and p192. Hiding needs every hostile unable to see you (total
    cover: no line of sight). Stealth equal to or above a hostile's passive
    Perception beats it; a hostile with a higher passive Perception notices."""
    t = resolve(enc, token_ref)
    _require_action(enc, t)
    R, grid = rules_for(enc), enc.board()
    watchers = [h for h in enc.tokens.values() if h.active and hostile(t, h) and R.can_act(h)]
    seen_by = [h for h in watchers if grid.line_of_sight(h.pos, t.pos)]
    if seen_by:
        who = ", ".join(f"{h.name} ({h.square})" for h in seen_by)
        raise CombatError(f"{t.name} cannot hide in plain sight: seen by {who}. "
                          "Get out of their line of sight first.")
    mark = len(roller.log)
    bonus = R.skill_bonus(t, "stealth")
    r = roller.roll(f"1d20{bonus:+d}", t.name, "Stealth (hide)", player=player_rolls(enc, t))
    noticed = [h for h in watchers if R.passive_perception(h) > r.total]
    enc.turn.action_used = enc.turn.undo_locked = True
    if noticed:
        text = (f"{t.name} tries to hide: Stealth {r.total}, but "
                + ", ".join(f"{h.name} (passive {R.passive_perception(h)})" for h in noticed)
                + " still hears them. Not hidden.")
    else:
        t.add_condition("hidden")
        t.extra["stealth"] = r.total
        text = (f"{t.name} hides: Stealth {r.total}. Hidden until they attack, cast a spell "
                "or are seen in the open.")
    log(enc, "hide", t.id, text, roller, mark)
    return {"hidden": not noticed, "stealth": r.total, "text": text}


def escape(enc, roller: Roller, token_ref) -> dict:
    """PHB p195: an action, Athletics or Acrobatics (the better one) against
    the escape DC."""
    t = resolve(enc, token_ref)
    _require_action(enc, t)
    grip = next((e for e in t.effects if e.get("grapple")), None)
    if grip is None:
        raise CombatError(f"{t.name} is not grappled.")
    R = rules_for(enc)
    skill = max(("athletics", "acrobatics"), key=lambda s: R.skill_bonus(t, s))
    bonus = R.skill_bonus(t, skill)
    dc = grip["grapple"]["escape_dc"]
    mark = len(roller.log)
    r = roller.roll(f"1d20{bonus:+d}", t.name, f"{skill.title()} (escape DC {dc})",
                    player=player_rolls(enc, t))
    enc.turn.action_used = enc.turn.undo_locked = True
    src = enc.tokens.get(grip.get("source"))
    if r.total >= dc:
        fx.remove(t, grip)
        text = f"{t.name} escapes {src.name if src else 'the grapple'}: {skill.title()} {r.total} vs DC {dc}."
    else:
        text = f"{t.name} fails to escape: {skill.title()} {r.total} vs DC {dc}."
    log(enc, "escape", t.id, text, roller, mark)
    return {"escaped": r.total >= dc, "text": text}


def ready(enc, roller: Roller, token_ref, kind: str, what: str = None, target: str = None,
          trigger_text: str = "", level: int = None) -> dict:
    """Ready an attack (what = attack name, target), a spell (what = spell,
    target) or a move (target = square). A readied spell spends its slot now
    and is held with concentration."""
    t = resolve(enc, token_ref)
    _require_action(enc, t)
    kind = (kind or "").lower()
    entry = {"kind": kind, "target": target, "trigger": trigger_text or "the trigger"}
    lines = []
    if kind == "attack":
        atk = _find_attack(t, what)
        entry["attack"] = atk["name"] if atk else None
        entry["label"] = f"{atk['name'] if atk else 'attack'}" + (f" at {target}" if target else "")
    elif kind == "cast":
        if not what:
            raise CombatError("Name the spell to ready.")
        try:
            spec = rules_for(enc).spell(t, what, level)
        except ValueError as e:
            raise CombatError(str(e)) from None
        if spec["casting"] != "action" or spec["mode"] == "reaction":
            raise CombatError(f"Only a spell with a casting time of 1 action can be readied, "
                              f"not {spec['name']}.")
        lv = spells._check_slot(t, spec)
        if lv:
            t.extra["slots"][lv]["used"] += 1
        name = f"{spec['name']} (readied)"
        lines += fx.start_concentration(enc, t, name)
        entry.update(spell=spec["name"], level=spec["slot"] or None, concentration=True,
                     concentration_name=name,
                     label=f"{spec['name']}" + (f" at {target}" if target else ""))
    elif kind == "move":
        if not target:
            raise CombatError("Name the square to move to.")
        parse_square(target)
        entry["label"] = f"move to {target.upper()}"
    else:
        raise CombatError("Ready an attack, a spell (cast) or a move.")
    t.extra["readied"] = entry
    enc.turn.action_used = enc.turn.undo_locked = True
    text = " ".join(lines + [f"{t.name} readies {entry['label']}, when {entry['trigger']}. "
                             f"The GM runs: trigger {t.id}"])
    log(enc, "ready", t.id, text)
    return {"readied": entry, "text": text}


def trigger(enc, roller: Roller, token_ref, target: str = None, reactions: dict = None) -> dict:
    """The readied action happens now, as a reaction. If it cannot (target out
    of range), nothing changes and the action stays readied."""
    t = resolve(enc, token_ref)
    r = t.extra.get("readied")
    if not r:
        raise CombatError(f"{t.name} has nothing readied.")
    R = rules_for(enc)
    if not R.can_react(t):
        raise CombatError(f"{t.name} cannot take a reaction now.")
    goal = target or r.get("target")
    mark = len(roller.log)
    if r["kind"] == "attack":
        if not goal:
            raise CombatError("Name the target: trigger <token> <target>.")
        victim = resolve(enc, goal)
        atk = _find_attack(t, r.get("attack")) or next(
            (a for a in t.attacks if "unparsed" not in a.get("flags", [])), None)
        ctx, why = _attack_context(enc, t, victim, atk)
        if ctx is None:
            raise CombatError(why + " The readied attack stays ready.")
        t.extra.pop("readied")
        t.reaction_used = True
        text = "Readied: " + _resolve_attack(enc, roller, t, victim, atk, ctx, reactions)["text"]
    elif r["kind"] == "cast":
        if not t.concentration or t.concentration != r.get("concentration_name"):
            t.extra.pop("readied")
            raise CombatError(f"{t.name} lost the readied {r['spell']} (concentration broke).")
        res = spells.cast(enc, roller, t, r["spell"], [goal] if goal else [], reactions=reactions,
                          readied=r)
        t.extra.pop("readied", None)
        t.reaction_used = True
        text = "Readied: " + res["text"]
    else:
        dest = goal or r["target"]
        res = move(enc, roller, t, dest, reactions, as_reaction=True)
        t.extra.pop("readied", None)
        t.reaction_used = True
        text = "Readied: " + res["text"]
    log(enc, "trigger", t.id, text, roller, mark)
    return {"text": text}


def readied_lines(enc) -> list:
    """One reminder per creature holding a readied action (for status and the GM hint)."""
    out = []
    for t in enc.tokens.values():
        r = t.extra.get("readied")
        if r and t.active:
            out.append(f"{t.name} readied {r['label']} when {r['trigger']} (trigger {t.id}).")
    return out


__all__ = ["help_action", "hide", "escape", "ready", "trigger", "readied_lines", "label"]
