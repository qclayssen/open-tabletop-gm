"""ai.py: numbered options for a GM-controlled creature's turn.

The GM model reads a short menu and answers with a number; the engine does
the rest. Options are deterministic (no dice), so `choose N` recomputes the
same list and runs option N. Each option has a short tactical tag so a small
model can pick well:

  1. Move to J6 and Bite Kairos (60% to hit, ~2.7 dmg) [can finish them]
  2. Bite Kairos from here (60% to hit, ~2.7 dmg)
  3. Disengage, then retreat to N2 [flee: below 25% HP]
  4. Hold position and Dodge [defensive]

Heuristics: prefer targets that can be finished or have the lowest AC, never
walk through an opportunity attack when a safe square works, ranged
creatures stay out of melee, and anything below 25% HP flees first.
"""

from __future__ import annotations

from . import engine
from .grid import label, parse_square

FLEE_BELOW = 0.25


def _hostiles(enc, t):
    return [h for h in enc.tokens.values() if h.active and engine.hostile(t, h)]


def _at(t, pos, fn):
    """Evaluate fn() with t standing on pos, then put it back."""
    old = t.x, t.y
    t.x, t.y = pos
    try:
        return fn()
    finally:
        t.x, t.y = old


def _provokes(enc, t, dest) -> list:
    if tuple(dest) == t.pos:
        return []
    try:
        return engine.preview_move(enc, t, dest)["opportunity_attacks"]
    except engine.CombatError:
        return []


def _attack_plans(enc, t, R, squares) -> list:
    hostiles = _hostiles(enc, t)
    if not hostiles:
        return []
    grid = enc.board()
    low_ac = min(h.ac for h in hostiles)
    plans = []
    for atk in t.attacks:
        if "unparsed" in atk.get("flags", []):
            continue
        ranged_only = atk.get("type") == "ranged"
        avg = engine.average_damage(atk)
        for h in hostiles:
            best = None
            for pos, cost in squares.items():
                ctx = _at(t, pos, lambda: engine._attack_context(enc, t, h, atk)[0])
                if ctx is None:
                    continue
                hc = _at(t, pos, lambda: R.hit_chance(t, h, atk, ctx))
                exp = hc["chance"] * avg
                ooa = _provokes(enc, t, pos)
                adjacent = sum(1 for x in hostiles if grid.distance(pos, x.pos) <= 5)
                score = exp - 4 * len(ooa) - cost / 100      # safe and short beats risky
                if ranged_only and adjacent:
                    score -= 2                               # stay out of melee
                if best is None or score > best["score"]:
                    best = {"pos": pos, "hit": hc["percent"], "exp": exp,
                            "adv": hc["advantage"], "oa": ooa, "score": score}
            if best is None:
                continue
            tags = []
            if h.hp <= avg:
                tags.append("can finish them")
                best["score"] += 2
            if h.ac == low_ac and len(hostiles) > 1:
                tags.append("lowest AC")
                best["score"] += 0.5
            if best["oa"]:
                tags.append("provokes " + ", ".join(o["name"] for o in best["oa"]))
            if best["adv"] != "normal":
                tags.append(best["adv"])
            plans.append({"kind": "attack", "attack": atk["name"], "target": h.id,
                          "target_name": h.name,
                          "move_to": None if best["pos"] == t.pos else label(best["pos"]),
                          "hit_percent": best["hit"], "expected": round(best["exp"], 1),
                          "score": best["score"], "tags": tags})
    return plans


def options(enc, token_ref, limit: int = 5) -> list:
    t = engine._resolve(enc, token_ref)
    R = engine.rules_for(enc)
    if not R.can_act(t):
        return []
    grid = enc.board()
    mine = enc.current is not None and enc.current.id == t.id
    left = engine.remaining_movement(enc) if mine else R.speed(t)
    action_free = not (mine and enc.turn.action_used)
    reach = engine.reachable(enc, t)
    squares = {t.pos: 0}
    squares.update({parse_square(s): c for s, c in reach["walk"].items()})
    hostiles = _hostiles(enc, t)
    out = []

    if action_free:
        out += _attack_plans(enc, t, R, squares)

    low_hp = t.hp <= t.max_hp * FLEE_BELOW
    if hostiles and left > 0:
        def nearest(pos):
            return min(grid.distance(pos, h.pos) for h in hostiles)
        far = max(squares, key=lambda p: (nearest(p), -squares[p]))
        if nearest(far) > nearest(t.pos):
            ooa = _provokes(enc, t, far)
            disengage = bool(ooa) and action_free
            risk = 0 if disengage else 4 * len(ooa)
            out.append({"kind": "retreat", "move_to": label(far), "disengage": disengage,
                        "score": (20 if low_hp else -1) - risk,
                        "tags": ["flee: below 25% HP"] if low_hp else ["keep distance"]})

    if action_free and hostiles and not any(o["kind"] == "attack" for o in out):
        dash = {parse_square(s): c for s, c in reach["dash"].items()}
        if dash:
            best = min(dash, key=lambda p: (min(grid.distance(p, h.pos) for h in hostiles), dash[p]))
            out.append({"kind": "dash", "move_to": label(best), "score": 1,
                        "tags": ["close the distance"]})

    if action_free:
        out.append({"kind": "dodge", "score": 0 if hostiles else -5, "tags": ["defensive"]})

    out.sort(key=lambda o: -o["score"])
    out = out[:limit]
    for i, o in enumerate(out, 1):
        o["n"] = i
        o["label"] = _label(o)
    return out


def specials(enc, token_ref) -> list:
    """Names of actions the engine will not run by itself (flagged in the SRD
    data); the GM may narrate one instead of picking a number."""
    t = engine._resolve(enc, token_ref)
    return [a["name"] for a in t.extra.get("actions", []) if a.get("kind") != "multiattack"]


def _label(o) -> str:
    tags = f" [{'; '.join(o['tags'])}]" if o.get("tags") else ""
    if o["kind"] == "attack":
        odds = f"({o['hit_percent']}% to hit, ~{o['expected']} dmg)"
        if o["move_to"]:
            return f"Move to {o['move_to']} and {o['attack']} {o['target_name']} {odds}{tags}"
        return f"{o['attack']} {o['target_name']} from here {odds}{tags}"
    if o["kind"] == "retreat":
        first = "Disengage, then retreat" if o.get("disengage") else "Retreat"
        return f"{first} to {o['move_to']}{tags}"
    if o["kind"] == "dash":
        return f"Dash to {o['move_to']}{tags}"
    return f"Hold position and Dodge{tags}"


def choose(enc, roller, token_ref, n: int, reactions: dict = None) -> dict:
    """Run option n for this creature. Returns {"option", "text"}."""
    t = engine._resolve(enc, token_ref)
    opts = options(enc, t)
    pick = next((o for o in opts if o["n"] == n), None)
    if pick is None:
        raise engine.CombatError(f"{t.name} has no option {n} (options 1 to {len(opts)}).")
    lines = []
    kind = pick["kind"]
    if kind == "attack":
        if pick["move_to"]:
            lines.append(engine.move(enc, roller, t, pick["move_to"], reactions)["text"])
            if not engine.rules_for(enc).can_act(t):
                return {"option": pick, "text": " ".join(lines)}
        lines.append(engine.attack(enc, roller, t, pick["target"], pick["attack"])["text"])
    elif kind == "retreat":
        if pick.get("disengage"):
            engine.disengage(enc, t)
        lines.append(engine.move(enc, roller, t, pick["move_to"], reactions)["text"])
    elif kind == "dash":
        engine.dash(enc, t)
        lines.append(engine.move(enc, roller, t, pick["move_to"], reactions)["text"])
    else:
        lines.append(engine.dodge(enc, t)["text"])
    return {"option": pick, "text": " ".join(lines)}
