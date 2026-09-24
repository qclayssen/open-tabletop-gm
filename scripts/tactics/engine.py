"""engine.py: turn flow for grid combat.

System-neutral. Geometry comes from grid.py, rules from a Rules object
(rules.load(enc.system)). Every function mutates the Encounter it is given and
returns a result dict with a short `text` for the GM.

Two exceptions pause an action instead of half-applying it:

  roller.PendingRoll   a player must roll (roll_mode "players")
  DecisionNeeded       a player must decide (take an opportunity attack?)

Both can be raised part-way through, after some state has changed. Callers
therefore work on a copy (the CLI reloads encounter.json for every command and
only saves on success) and retry with the roll or decision supplied.

Previews come before commitment, in the spirit of Baldur's Gate 3's turn-based
mode: reachable squares with dash range, the path and feet a move costs, who
gets an opportunity attack and how likely it is to hit, and hit chance plus
expected damage for every target. Movement can be undone until an action,
reaction or roll locks it in.
"""

from __future__ import annotations

from . import rules as rules_mod
from .grid import MoveOptions, label, parse_square
from .roller import PendingRoll, Roller, average
from .rules import AttackContext
from .state import Encounter, TurnState

_RULES_CACHE = {}


class CombatError(Exception):
    """An illegal command. The message is safe to show the GM as-is."""


class DecisionNeeded(Exception):
    """A player-controlled creature must choose before the action can resolve."""

    def __init__(self, who: str, kind: str, prompt: str, options: list):
        self.who, self.kind, self.prompt, self.options = who, kind, prompt, options
        super().__init__(prompt)

    def to_dict(self) -> dict:
        return {"who": self.who, "kind": self.kind, "prompt": self.prompt, "options": self.options}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def rules_for(enc: Encounter):
    if enc.system not in _RULES_CACHE:
        _RULES_CACHE[enc.system] = rules_mod.load(enc.system)
    return _RULES_CACHE[enc.system]


def hostile(a, b) -> bool:
    friends = {"pc", "ally"}
    if a.side == "neutral" or b.side == "neutral" or a.id == b.id:
        return False
    return (a.side in friends) != (b.side in friends)


def player_rolls(enc: Encounter, token, roller: Roller) -> bool:
    """Does a human roll this token's dice right now?"""
    return (token.controller == "player" and enc.roll_mode == "players"
            and not roller.for_me)


def average_damage(attack: dict) -> float:
    return sum(average(p["dice"]) for p in attack.get("damage", []))


def _log(enc: Encounter, kind: str, actor: str, text: str, roller: Roller = None, mark: int = 0) -> None:
    rolls = [r.to_dict() for r in roller.log[mark:]] if roller else []
    enc.log.append({"round": enc.round, "actor": actor, "kind": kind, "text": text, "rolls": rolls})


def _resolve(enc: Encounter, ref):
    try:
        return enc.token(ref) if isinstance(ref, str) else ref
    except KeyError as e:
        raise CombatError(str(e.args[0])) from None


def _require_turn(enc: Encounter, token) -> None:
    if enc.status != "active":
        raise CombatError("Combat has ended.")
    cur = enc.current
    if cur is None or cur.id != token.id:
        raise CombatError(f"It is {cur.name if cur else 'nobody'}'s turn, not {token.name}'s.")
    if enc.turn.pending == "death_save":
        raise CombatError(f"{token.name} must make a death save first.")
    if not rules_for(enc).can_act(token):
        raise CombatError(f"{token.name} cannot act ({', '.join(token.conditions) or 'down'}).")


def _require_action(enc: Encounter, token) -> None:
    _require_turn(enc, token)
    if enc.turn.action_used:
        raise CombatError(f"{token.name} has already used their action this turn.")


def remaining_movement(enc: Encounter) -> int:
    return max(0, enc.turn.movement_budget - enc.turn.movement_used)


def move_options(enc: Encounter, token) -> MoveOptions:
    R = rules_for(enc)
    blocked, occupied = set(), set()
    for t in enc.tokens.values():
        if t.id == token.id or not t.active:
            continue
        (blocked if hostile(token, t) else occupied).add(t.pos)
    return MoveOptions(blocked=frozenset(blocked), occupied=frozenset(occupied),
                       crawling=R.crawling(token), swim=token.swim_speed > 0)


def resources(enc: Encounter) -> dict:
    """What the current actor has left: the BG3 action bar, as data."""
    t, turn = enc.current, enc.turn
    return {"actor": t.id if t else None, "movement": remaining_movement(enc),
            "action": not turn.action_used, "bonus": not turn.bonus_used,
            "reaction": bool(t and not t.reaction_used), "can_undo": can_undo(enc)}


# ─── Setup and turn order ─────────────────────────────────────────────────────

def begin(enc: Encounter, roller: Roller) -> dict:
    """Roll initiative for every active token and start round 1."""
    R = rules_for(enc)
    mark = len(roller.log)
    rolled = []
    for t in enc.tokens.values():
        if not t.active:
            continue
        r = R.initiative(t, roller)
        t.initiative, t.init_roll = r.total, r.natural
        rolled.append(t)
    # Ties: higher Dex modifier, then PCs first, then name, so order is deterministic.
    rolled.sort(key=lambda t: (-t.initiative, -t.dex_mod, t.side != "pc", t.name))
    enc.order = [t.id for t in rolled]
    enc.round, enc.turn_index, enc.status = 1, 0, "active"
    order = ", ".join(f"{t.name} {t.initiative}" for t in rolled)
    _log(enc, "initiative", "", f"Initiative: {order}.", roller, mark)
    start = _start_turn(enc, roller)
    return {"order": enc.order, "text": f"Initiative: {order}. Round 1. {start['text']}"}


def _start_turn(enc: Encounter, roller: Roller) -> dict:
    R = rules_for(enc)
    t = enc.current
    t.reaction_used = False
    t.dodging = False                      # Dodge lasts until the start of your next turn
    enc.turn = TurnState(actor=t.id, movement_budget=R.speed(t))
    text = f"{t.name}'s turn."
    if t.hp == 0 and not t.dead and not t.stable and not R.can_act(t):
        enc.turn.pending = "death_save"
        try:
            text += " " + death_save(enc, roller)["text"]
        except PendingRoll:
            text += f" {t.name} must roll a death save."
    return {"actor": t.id, "text": text}


def death_save(enc: Encounter, roller: Roller) -> dict:
    t = enc.current
    if enc.turn.pending != "death_save":
        raise CombatError(f"{t.name} has no death save to make.")
    mark = len(roller.log)
    res = rules_for(enc).death_save(t, roller, player_rolls(enc, t, roller))
    enc.turn.pending = ""
    _log(enc, "death_save", t.id, res["text"], roller, mark)
    return res


def end_turn(enc: Encounter, roller: Roller) -> dict:
    if enc.status != "active":
        raise CombatError("Combat has ended.")
    if enc.turn.pending == "death_save":
        raise CombatError(f"{enc.current.name} must make a death save before the turn ends.")
    ender = enc.current
    n = len(enc.order)
    for _ in range(n):
        enc.turn_index += 1
        if enc.turn_index >= n:
            enc.turn_index = 0
            enc.round += 1
        if enc.current.active:
            break
    else:
        raise CombatError("Nobody is left to act.")
    _log(enc, "end_turn", ender.id, f"{ender.name} ends their turn.")
    start = _start_turn(enc, roller)
    text = f"Round {enc.round}. {start['text']}"
    if not any(t.active and t.side == "enemy" for t in enc.tokens.values()):
        text += " All enemies are down."
    return {"actor": enc.current.id, "round": enc.round, "text": text}


# ─── Movement ─────────────────────────────────────────────────────────────────

def reachable(enc: Encounter, token_ref) -> dict:
    """{"walk": {square: feet}, "dash": {square: feet}} from the current position.
    dash holds squares only reachable by also taking the Dash action."""
    t = _resolve(enc, token_ref)
    R, grid, opts = rules_for(enc), enc.board(), move_options(enc, t)
    mine = enc.current is not None and enc.current.id == t.id
    left = remaining_movement(enc) if mine else R.speed(t)
    can_dash = mine and not enc.turn.action_used
    parity = enc.turn.diag_parity if mine else 0
    every = grid.reachable(t.pos, left + (R.speed(t) if can_dash else 0), opts, parity=parity)
    return {"walk": {label(p): c for p, c in every.items() if c <= left},
            "dash": {label(p): c for p, c in every.items() if c > left}}


def _provokers(enc: Encounter, mover, path) -> list:
    """[(step_index, hostile)] for hostiles whose reach the path leaves, in order.
    A creature gets one opportunity attack (one reaction) per move."""
    if enc.turn.disengaged:
        return []
    R, grid = rules_for(enc), enc.board()
    out = []
    for h in enc.tokens.values():
        if not (h.active and hostile(mover, h) and R.can_react(h) and R.opportunity_attack(h)):
            continue
        reach = R.reach(h)
        for i in range(len(path) - 1):
            if grid.distance(path[i], h.pos) <= reach < grid.distance(path[i + 1], h.pos):
                out.append((i, h))
                break
    return sorted(out, key=lambda x: x[0])


def _plan(enc: Encounter, t, square):
    grid, opts = enc.board(), move_options(enc, t)
    dest = parse_square(square) if isinstance(square, str) else tuple(square)
    if not grid.in_bounds(dest):
        raise CombatError(f"{label(dest)} is off the map.")
    if not grid.passable(dest):
        raise CombatError(f"{label(dest)} is a {grid.terrain_name(dest)}.")
    if dest != t.pos and (dest in opts.blocked or dest in opts.occupied):
        raise CombatError(f"{label(dest)} is occupied.")
    parity = enc.turn.diag_parity if enc.current and enc.current.id == t.id else 0
    found = grid.path(t.pos, dest, opts=opts, parity=parity)
    if found is None:
        raise CombatError(f"No path from {t.square} to {label(dest)}.")
    path, feet = found
    return grid, opts, dest, path, feet


def preview_move(enc: Encounter, token_ref, square) -> dict:
    """What a move would cost and risk, without doing it."""
    t = _resolve(enc, token_ref)
    grid, _opts, dest, path, feet = _plan(enc, t, square)
    R = rules_for(enc)
    left = remaining_movement(enc)
    warnings = []
    for i, h in _provokers(enc, t, path):
        oa = R.opportunity_attack(h)
        ctx = AttackContext(distance=grid.distance(path[i], h.pos), melee=True, opportunity=True)
        warnings.append({"id": h.id, "name": h.name, "attack": oa["name"],
                         "hit_percent": R.hit_chance(h, t, oa, ctx)["percent"]})
    hazards = [label(p) for p in path[1:] if grid.terrain(p).get("hazard")]
    legal = feet <= left
    if legal:
        text = f"{t.name} to {label(dest)}: {feet} ft ({left - feet} ft left)."
    else:
        dash_ok = not enc.turn.action_used and feet <= left + R.speed(t)
        text = f"{t.name} to {label(dest)} needs {feet} ft, has {left}." + (
            " Reachable with Dash." if dash_ok else "")
    for w in warnings:
        text += f" Provokes {w['name']} ({w['attack']}, {w['hit_percent']}% to hit)."
    if hazards:
        text += f" Crosses hazard at {', '.join(hazards)}."
    return {"path": [label(p) for p in path], "feet": feet, "legal": legal,
            "opportunity_attacks": warnings, "hazards": hazards, "text": text}


def move(enc: Encounter, roller: Roller, token_ref, square, reactions: dict = None) -> dict:
    """Move along the cheapest path. Opportunity attacks resolve just before the
    mover leaves reach; if the mover drops, it stops where it was hit.

    reactions: {hostile_id: bool} decisions for player-controlled hostiles.
    GM-controlled creatures always take the opportunity attack."""
    t = _resolve(enc, token_ref)
    _require_turn(enc, t)
    R = rules_for(enc)
    grid, opts, dest, path, feet = _plan(enc, t, square)
    left = remaining_movement(enc)
    if feet > left:
        raise CombatError(f"{t.name} needs {feet} ft to reach {label(dest)} but has {left} ft left.")
    reactions = reactions or {}
    provoked = _provokers(enc, t, path)
    for _i, h in provoked:                  # ask before rolling anything
        if h.controller == "player" and h.id not in reactions:
            raise DecisionNeeded(h.id, "opportunity_attack",
                                 f"{t.name} is leaving {h.name}'s reach. Opportunity attack?",
                                 ["yes", "no"])
    mark = len(roller.log)
    parity0 = enc.turn.diag_parity
    costs, _ = grid.step_costs(path, opts, parity0)
    start, stop_at, lines = t.pos, len(path) - 1, []
    by_step = {}
    for i, h in provoked:
        by_step.setdefault(i, []).append(h)
    for i in range(len(path) - 1):
        for h in by_step.get(i, []):
            if not reactions.get(h.id, True):
                lines.append(f"{h.name} lets {t.name} go.")
                continue
            if not R.can_react(h):
                continue
            h.reaction_used = True
            ctx = AttackContext(distance=grid.distance(path[i], h.pos), melee=True, opportunity=True)
            res = R.attack(h, t, R.opportunity_attack(h), ctx, roller, player_rolls(enc, h, roller))
            lines.append("Opportunity attack: " + res["text"])
        if not R.can_act(t) or R.speed(t) == 0:
            t.x, t.y = path[i]          # stopped where the attack landed
            stop_at = i
            break
    t.x, t.y = path[stop_at]
    used = costs[stop_at]
    enc.turn.movement_used += used
    enc.turn.diag_parity = grid.step_costs(path[:stop_at + 1], opts, parity0)[1]
    enc.turn.moves.append({"from": list(start), "to": list(t.pos), "feet": used,
                           "parity": parity0})
    if lines:
        enc.turn.undo_locked = True
    if stop_at == 0 and len(path) > 1:
        text = f"{t.name} is stopped at {t.square} before taking a step."
    else:
        text = (f"{t.name} moves {label(start)} to {t.square} "
                f"({used} ft, {remaining_movement(enc)} ft left).")
        if stop_at < len(path) - 1:
            text += " Movement stops."
    hazards = [label(p) for p in path[1:stop_at + 1] if grid.terrain(p).get("hazard")]
    if hazards:
        text += f" Enters hazard at {', '.join(hazards)} (GM decides the effect)."
    text = " ".join([text] + lines)
    _log(enc, "move", t.id, text, roller, mark)
    return {"path": [label(p) for p in path[:stop_at + 1]], "feet": used,
            "stopped": stop_at < len(path) - 1, "opportunity_attacks": len(lines), "text": text}


def can_undo(enc: Encounter) -> bool:
    return bool(enc.turn.moves) and not enc.turn.undo_locked


def undo_move(enc: Encounter) -> dict:
    if not enc.turn.moves:
        raise CombatError("No movement to undo this turn.")
    if enc.turn.undo_locked:
        raise CombatError("Movement is locked in: an action, reaction or roll happened since.")
    t = enc.current
    last = enc.turn.moves.pop()
    if last.get("stood_up"):
        t.add_condition("prone")
    else:
        t.x, t.y = last["from"]
        enc.turn.diag_parity = last.get("parity", 0)
    enc.turn.movement_used -= last["feet"]
    text = f"{t.name} undoes the last move: back at {t.square}, {remaining_movement(enc)} ft left."
    _log(enc, "undo", t.id, text)
    return {"text": text}


def stand_up(enc: Encounter, token_ref) -> dict:
    t = _resolve(enc, token_ref)
    _require_turn(enc, t)
    if not t.has("prone"):
        raise CombatError(f"{t.name} is not prone.")
    cost = rules_for(enc).stand_up_cost(t)
    if cost > remaining_movement(enc):
        raise CombatError(f"Standing up costs {cost} ft; {t.name} has {remaining_movement(enc)} ft.")
    t.remove_condition("prone")
    enc.turn.movement_used += cost
    enc.turn.moves.append({"from": list(t.pos), "to": list(t.pos), "feet": cost, "stood_up": True})
    text = f"{t.name} stands up ({cost} ft, {remaining_movement(enc)} ft left)."
    _log(enc, "stand", t.id, text)
    return {"text": text}


# ─── Actions ──────────────────────────────────────────────────────────────────

def _attack_context(enc: Encounter, attacker, target, attack: dict):
    """(AttackContext, None) or (None, reason it cannot be made)."""
    R, grid = rules_for(enc), enc.board()
    dist = grid.distance(attacker.pos, target.pos)
    kind = attack.get("type", "melee")
    reach = attack.get("reach", 5)
    rng = attack.get("range")
    if kind == "melee" or (kind == "melee_or_ranged" and dist <= reach):
        if dist > reach:
            return None, f"{target.name} is {dist} ft away; {attack['name']} reaches {reach} ft."
        melee, long_range = True, False
    else:
        if not rng:
            return None, f"{attack['name']} has no range."
        if dist > rng[1]:
            return None, f"{target.name} is {dist} ft away; {attack['name']} range is {rng[0]}/{rng[1]} ft."
        melee, long_range = False, dist > rng[0]
    others = {t.pos for t in enc.tokens.values() if t.active and t.id not in (attacker.id, target.id)}
    cov = grid.cover(attacker.pos, target.pos, creatures=others)
    if not cov["los"]:
        return None, f"{attacker.name} has no line of sight to {target.name}."
    adjacent = any(hostile(attacker, h) and h.active and R.can_act(h)
                   and grid.distance(attacker.pos, h.pos) <= 5 for h in enc.tokens.values())
    return AttackContext(distance=dist, melee=melee, cover=cov["cover"],
                         long_range=long_range, hostile_adjacent=adjacent), None


def _find_attack(attacker, name):
    if not attacker.attacks:
        raise CombatError(f"{attacker.name} has no attacks.")
    if not name:
        return None
    low = name.strip().lower()
    for a in attacker.attacks:
        if a["name"].lower() == low:
            return a
    for a in attacker.attacks:
        if a["name"].lower().startswith(low):
            return a
    raise CombatError(f"{attacker.name} has no attack {name!r}. "
                      f"Attacks: {', '.join(a['name'] for a in attacker.attacks)}.")


def attack_options(enc: Encounter, attacker_ref) -> list:
    """Every (attack, hostile target) pair with legality, hit chance and expected
    damage, best first. Drives target highlighting and the enemy option list."""
    a = _resolve(enc, attacker_ref)
    R = rules_for(enc)
    out = []
    for atk in a.attacks:
        if "unparsed" in atk.get("flags", []):
            continue
        for t in enc.tokens.values():
            if not (t.active and hostile(a, t)):
                continue
            ctx, why = _attack_context(enc, a, t, atk)
            row = {"attack": atk["name"], "target": t.id, "target_name": t.name,
                   "square": t.square, "legal": ctx is not None, "reason": why}
            if ctx:
                hc = R.hit_chance(a, t, atk, ctx)
                row.update(hit_percent=hc["percent"], advantage=hc["advantage"],
                           reasons=hc["reasons"], cover=ctx.cover,
                           expected_damage=round(hc["chance"] * average_damage(atk), 1))
            out.append(row)
    out.sort(key=lambda r: (not r["legal"], -r.get("expected_damage", 0)))
    return out


def attack(enc: Encounter, roller: Roller, attacker_ref, target_ref, attack_name: str = None) -> dict:
    a = _resolve(enc, attacker_ref)
    t = _resolve(enc, target_ref)
    _require_action(enc, a)
    if not t.active:
        raise CombatError(f"{t.name} is dead.")
    if t.id == a.id:
        raise CombatError(f"{a.name} cannot attack themselves.")
    chosen = _find_attack(a, attack_name)
    candidates = [chosen] if chosen else [x for x in a.attacks if "unparsed" not in x.get("flags", [])]
    reasons = []
    for atk in candidates:
        ctx, why = _attack_context(enc, a, t, atk)
        if ctx:
            break
        reasons.append(why)
    else:
        raise CombatError(reasons[0] if reasons else f"{a.name} cannot attack {t.name}.")
    mark = len(roller.log)
    res = rules_for(enc).attack(a, t, atk, ctx, roller, player_rolls(enc, a, roller))
    enc.turn.action_used = True
    enc.turn.undo_locked = True
    _log(enc, "attack", a.id, res["text"], roller, mark)
    return res


def _simple_action(enc: Encounter, token_ref, kind: str) -> dict:
    t = _resolve(enc, token_ref)
    _require_action(enc, t)
    if kind == "dash":
        enc.turn.movement_budget += rules_for(enc).speed(t)
        text = f"{t.name} dashes ({remaining_movement(enc)} ft left)."
    elif kind == "disengage":
        enc.turn.disengaged = True
        text = f"{t.name} disengages: no opportunity attacks this turn."
    else:
        t.dodging = True
        text = f"{t.name} dodges: attacks against them have disadvantage until their next turn."
    enc.turn.action_used = True
    enc.turn.undo_locked = True
    _log(enc, kind, t.id, text)
    return {"text": text}


def dash(enc: Encounter, token_ref) -> dict:
    return _simple_action(enc, token_ref, "dash")


def disengage(enc: Encounter, token_ref) -> dict:
    return _simple_action(enc, token_ref, "disengage")


def dodge(enc: Encounter, token_ref) -> dict:
    return _simple_action(enc, token_ref, "dodge")
