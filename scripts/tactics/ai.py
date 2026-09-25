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

Milestone 4: area save actions (breath weapons) are options too, aimed at the
best spot from here or a short move away and scored by expected damage (each
target's chance to fail its save, half on a success) minus damage to allies.
A spent recharge action is listed as recharging. A grappler keeps biting the
creature it holds and does not wander off; hidden creatures cannot be targeted;
a concentrating caster is tagged.
"""

from __future__ import annotations

from . import effects as fx
from . import engine, spells
from .grid import label, parse_square

FLEE_BELOW = 0.25


def _hostiles(enc, t):
    """Hostiles this creature knows where to find (hidden ones are not targets)."""
    return [h for h in enc.tokens.values()
            if h.active and engine.hostile(t, h) and not h.has("hidden")]


def _held(enc, t):
    """The creature t is grappling, or None."""
    held = fx.grappling(enc, t)
    return held[0][0] if held else None


def _grapple_attack(atk) -> bool:
    return any(e.get("kind") == "grapple" for e in atk.get("rider_effects", []))


def _conc_break(enc, R, target, hit_chance: float, exp: float) -> float:
    """Chance this attack breaks the target's concentration."""
    if not target.concentration or hit_chance <= 0:
        return 0.0
    dc = max(10, int(exp / max(hit_chance, 0.01)) // 2)
    return hit_chance * R.save_chance(target, "con", dc)["fail"]


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
    held = _held(enc, t)
    plans = []
    for atk in t.attacks:
        if "unparsed" in atk.get("flags", []):
            continue
        ranged_only = atk.get("type") == "ranged"
        avg = engine.average_damage(atk)
        for h in hostiles:
            if held is not None and _grapple_attack(atk) and h.id != held.id:
                continue                             # "can't bite another target"
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
            if held is not None and h.id == held.id:
                tags.append("keeps grapple")
                best["score"] += 1
            brk = _conc_break(enc, R, h, best["hit"] / 100, best["exp"])
            if brk:
                tags.append(f"{h.name} concentrating ({round(brk * 100)}% to break)")
                best["score"] += 1.5 * brk
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
        out += _area_plans(enc, t, R, squares)

    low_hp = t.hp <= t.max_hp * FLEE_BELOW
    held = _held(enc, t)
    if hostiles and left > 0 and (held is None or low_hp):
        def nearest(pos):
            return min(grid.distance(pos, h.pos) for h in hostiles)
        far = max(squares, key=lambda p: (nearest(p), -squares[p]))
        if nearest(far) > nearest(t.pos):
            ooa = _provokes(enc, t, far)
            disengage = bool(ooa) and action_free
            risk = 0 if disengage else 4 * len(ooa)
            tags = ["flee: below 25% HP"] if low_hp else ["keep distance"]
            if held is not None:
                tags.append(f"releases {held.name}")
            out.append({"kind": "retreat", "move_to": label(far), "disengage": disengage,
                        "score": (20 if low_hp else -1) - risk, "tags": tags})

    if action_free and hostiles and held is None and not any(o["kind"] in ("attack", "area") for o in out):
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


def _area_plans(enc, t, R, squares) -> list:
    """The best aim for each usable area save action, from here or from one of
    the few cheapest squares that put a hostile in range."""
    hostiles = _hostiles(enc, t)
    if not hostiles:
        return []
    grid = enc.board()
    plans = []
    for a in spells.area_actions(t):
        if not spells.usable(t, a):
            continue
        spec = spells._action_spec(a)
        size = spec["area"]["size"]
        near = sorted((c, p) for p, c in squares.items()
                      if min(grid.distance(p, h.pos) for h in hostiles) <= size + 5)
        origins = [t.pos] + [p for _c, p in near if p != t.pos][:6]
        avg = sum(engine.average(p["dice"]) for p in spec["damage"])
        dtype = spec["damage"][0]["type"] if spec["damage"] else ""
        k = 0.5 if spec["save"]["on_success"] == "half" else 0.0
        cond = next((e.get("condition") for e in spec["rider_effects"] if e.get("condition")), None)
        best = None
        for origin in origins:
            for h in hostiles:
                if h.pos == origin:
                    continue
                try:
                    caught_sq = set(_at(t, origin, lambda: spells.area_squares(
                        enc, origin, spec["area"], "self", h.pos)))
                except engine.CombatError:
                    continue
                caught = [x for x in enc.tokens.values()
                          if x.active and x.pos in caught_sq and x.id != t.id]
                foes = [x for x in caught if engine.hostile(t, x)]
                if not foes:
                    continue
                total_foe = total_ally = 0.0
                fails = []
                for x in caught:
                    cover = 0
                    if spec["save"]["ability"] == "dex":
                        cover = _at(t, origin, lambda: spells._cover_from(enc, origin, x))
                    fail = R.save_chance(x, spec["save"]["ability"], spec["save"]["dc"], cover)["fail"]
                    e = min(x.hp, avg * R.damage_multiplier(x, dtype) * (fail + (1 - fail) * k))
                    e += (2.0 if cond else 0.0) * fail
                    if engine.hostile(t, x):
                        total_foe += e
                        fails.append((x, fail))
                    else:
                        total_ally += e
                ooa = _provokes(enc, t, origin)
                cost = squares.get(origin, 0)
                score = (total_foe - 1.5 * total_ally - 4 * len(ooa) - cost / 100
                         + 1.5 * (len(foes) - 1))
                if best is None or score > best["score"]:
                    best = {"score": score, "origin": origin, "aim": h.pos, "foes": foes,
                            "allies": [x for x in caught if not engine.hostile(t, x)],
                            "fails": fails, "exp": total_foe, "oa": ooa}
        if best is None:
            continue
        u = (t.extra.get("usage") or {}).get(a["name"], {})
        tags = []
        if "charged" in u:
            tags.append(f"recharge {u.get('min', 6)}" + ("-6" if u.get("min", 6) < 6 else ""))
        elif "left" in u:
            tags.append(f"{u['left']} left")
        if len(best["foes"]) > 1:
            tags.append(f"catches {len(best['foes'])} hostiles")
        for x in best["allies"]:
            tags.append(f"hits ally {x.name}")
        if best["oa"]:
            tags.append("provokes " + ", ".join(o["name"] for o in best["oa"]))
        fail_txt = ", ".join(f"{x.name} {round(f * 100)}% to fail" for x, f in best["fails"])
        plans.append({"kind": "area", "action": a["name"], "aim": label(best["aim"]),
                      "move_to": None if best["origin"] == t.pos else label(best["origin"]),
                      "expected": round(best["exp"], 1), "fail_text": fail_txt,
                      "area_text": f"{spec['area']['size']} ft {spec['area']['shape']}",
                      "dc_text": f"DC {spec['save']['dc']} {spec['save']['ability'].upper()}",
                      "score": best["score"] + (0.5 if len(best["foes"]) > 1 else 0), "tags": tags})
    return plans


def recharging(enc, token_ref) -> list:
    t = engine._resolve(enc, token_ref)
    return [name for name, u in (t.extra.get("usage") or {}).items()
            if u.get("charged") is False or u.get("left") == 0]


def specials(enc, token_ref) -> list:
    """Names of actions the engine will not run by itself (flagged in the SRD
    data); the GM may narrate one instead of picking a number."""
    t = engine._resolve(enc, token_ref)
    runnable = {a["name"] for a in spells.area_actions(t)}
    return [a["name"] for a in t.extra.get("actions", [])
            if a.get("kind") != "multiattack" and a["name"] not in runnable]


def _label(o) -> str:
    tags = f" [{'; '.join(o['tags'])}]" if o.get("tags") else ""
    if o["kind"] == "attack":
        odds = f"({o['hit_percent']}% to hit, ~{o['expected']} dmg)"
        if o["move_to"]:
            return f"Move to {o['move_to']} and {o['attack']} {o['target_name']} {odds}{tags}"
        return f"{o['attack']} {o['target_name']} from here {odds}{tags}"
    if o["kind"] == "area":
        what = (f"{o['action']}: {o['area_text']} at {o['aim']} ({o['dc_text']}, {o['fail_text']}, "
                f"~{o['expected']} dmg)")
        return (f"Move to {o['move_to']}, then {what}" if o["move_to"] else what) + tags
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
        lines.append(engine.attack(enc, roller, t, pick["target"], pick["attack"], reactions)["text"])
    elif kind == "area":
        if pick["move_to"]:
            lines.append(engine.move(enc, roller, t, pick["move_to"], reactions)["text"])
            if not engine.rules_for(enc).can_act(t):
                return {"option": pick, "text": " ".join(lines)}
        lines.append(spells.use_action(enc, roller, t, pick["action"], pick["aim"], reactions)["text"])
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
