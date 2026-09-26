"""ai.py: numbered options for a GM-controlled creature's turn.

The GM model reads a short menu and answers with a number; the engine does
the rest. Options are deterministic (no dice), so `choose N` recomputes the
same list and runs option N. Each option has a short tactical tag so a small
model can pick well:

  1. Move to J6 and Multiattack Kairos: Beak, Claws (55%/60% to hit, ~9.1 dmg)
  2. Shortbow Kairos from here, then fall back to M1 (60% to hit, ~2.1 dmg) [cover +2]
  3. Disengage, then retreat to N2 [flee: below 25% HP]
  4. Hold position and Dodge [defensive]

Heuristics, borrowed from tactics games and 5e monster-tactics writing:

- Multiattack is one action with every attack in the routine (the stat block).
- Expected damage (hit chance x average) ranks plans; "can finish them" and
  the lowest AC break ties toward focus fire (BG3, Dofus).
- Never walk through an opportunity attack when a safe square works.
- Ranged creatures stay out of melee, prefer squares with cover from their
  target, and use leftover movement to fall back after shooting (XCOM, BG3;
  5e lets a creature split its movement around its action, PHB p190).
- Melee creatures avoid squares next to several enemies at once.
- A downed PC is a legal target, but attacking one is listed below Dodge (or
  Wait, once no one is fighting back) and tagged with what it does (death save
  failures), so the GM chooses it knowingly.
- Morale: flee below 25% HP, or below 50% once half their side is down
  (The Monsters Know What They're Doing, OSR morale). Undead and constructs
  never flee.

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
BROKEN_FLEE_BELOW = 0.5
FEARLESS_TYPES = {"undead", "construct"}


def _hostiles(enc, t):
    """Hostiles this creature knows where to find (hidden ones are not targets)."""
    return [h for h in enc.tokens.values()
            if h.active and engine.hostile(t, h) and not h.has("hidden")]


def _kites(t) -> bool:
    """Has something to do from range, so backing off at full health makes sense."""
    return (any(a.get("type") in ("ranged", "melee_or_ranged") or a.get("range")
                for a in t.attacks if "unparsed" not in a.get("flags", []))
            or bool(spells.area_actions(t)))


def _held(enc, t):
    """The creature t is grappling, or None."""
    held = fx.grappling(enc, t)
    return held[0][0] if held else None


def _grapple_attack(atk) -> bool:
    """A grappling attack that cannot be used on anyone else while it holds ("the
    frog can't bite another target"); a crab's other claw can still grab."""
    return (any(e.get("kind") == "grapple" for e in atk.get("rider_effects", []))
            and "another target" in atk.get("rider_rest", ""))


def _conc_break(enc, R, target, hit_chance: float, exp: float) -> float:
    """Chance this attack breaks the target's concentration."""
    if not target.concentration or hit_chance <= 0:
        return 0.0
    dc = max(10, int(exp / max(hit_chance, 0.01)) // 2)
    return hit_chance * R.save_chance(target, "con", dc)["fail"]


def _threats(enc, t, R):
    """Hostiles that can still fight back (not downed or incapacitated)."""
    return [h for h in _hostiles(enc, t) if R.can_act(h)]


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


def _morale(enc, t):
    """(flee threshold as a share of max HP, tag)."""
    if t.extra.get("type") in FEARLESS_TYPES:
        return 0.0, None
    side = [x for x in enc.tokens.values() if x.side == t.side]
    down = sum(1 for x in side if engine._down(x))
    if len(side) >= 2 and 2 * down >= len(side):
        return BROKEN_FLEE_BELOW, "morale broken: half their side is down"
    return FLEE_BELOW, "flee: below 25% HP"


def _weapons(t) -> list:
    """What the creature can do with its action: (kind, name, attacks, routine)
    for each Multiattack option (routine is 1-based) and each single attack."""
    out = []
    for i, r in enumerate(engine.multiattack_routines(t), 1):
        attacks, _other = engine.expand_routine(t, r)
        if attacks:
            out.append(("multiattack", engine.routine_name(r), attacks, i))
    for atk in t.attacks:
        if "unparsed" not in atk.get("flags", []):
            out.append(("attack", atk["name"], [atk], None))
    return out


def _cover_from(enc, t, h, pos) -> int:
    """Cover t would have at pos against an attack from h."""
    others = {x.pos for x in enc.tokens.values() if x.active and x.id not in (t.id, h.id)}
    return enc.board().cover(h.pos, pos, creatures=others)["cover"]


def _fallback(enc, t, pos, cost, threats):
    """After attacking from pos, the square the leftover movement reaches that
    is furthest from every threat (then in the most cover), without provoking.
    None when nothing is safer than pos."""
    left = engine.remaining_movement(enc) - cost
    if left < 5 or not threats:
        return None
    grid, opts = enc.board(), engine.move_options(enc, t)
    parity = enc.turn.diag_parity
    if pos != t.pos:
        found = grid.path(t.pos, pos, opts=opts, parity=parity)
        if found is None:
            return None
        parity = grid.step_costs(found[0], opts, parity)[1]
    reach = grid.reachable(pos, left, opts, parity=parity)

    def nearest(p):
        return min(grid.distance(p, h.pos) for h in threats)

    def cover(p):
        return min(_cover_from(enc, t, h, p) for h in threats)

    here = (nearest(pos), cover(pos))
    ranked = sorted((p for p in reach if p != pos and p not in opts.occupied),
                    key=lambda p: (-nearest(p), -cover(p), reach[p]))
    for p in ranked[:8]:
        if (nearest(p), cover(p)) <= here:
            return None
        found = grid.path(pos, p, opts=opts, parity=parity)
        if found is not None and not _at(t, pos, lambda: engine._provokers(enc, t, found[0])):
            return p
    return None


def _attack_plans(enc, t, R, squares) -> list:
    hostiles = _hostiles(enc, t)
    if not hostiles:
        return []
    grid = enc.board()
    threats = _threats(enc, t, R)
    up = [h for h in hostiles if not engine._down(h)]
    low_ac = min((h.ac for h in up), default=None)
    held = _held(enc, t)
    oa_cache = {}
    plans = []
    for kind, name, attacks, routine in _weapons(t):
        avg = sum(engine.average_damage(a) for a in attacks)
        for h in hostiles:
            if held is not None and h.id != held.id and any(_grapple_attack(a) for a in attacks):
                continue                             # "can't bite another target"
            downed = engine._down(h)
            best = None
            for pos, cost in squares.items():
                ctxs = [_at(t, pos, lambda a=a: engine._attack_context(enc, t, h, a)[0])
                        for a in attacks]
                if not any(ctxs):
                    continue
                hits, exp, adv, ranged = [], 0.0, "normal", True
                for a, ctx in zip(attacks, ctxs):
                    if ctx is None:
                        hits.append(0)
                        continue
                    hc = _at(t, pos, lambda: R.hit_chance(t, h, a, ctx))
                    hits.append(hc["percent"])
                    exp += hc["chance"] * engine.average_damage(a)
                    adv = hc["advantage"] if adv == "normal" else adv
                    ranged = ranged and not ctx.melee
                if pos not in oa_cache:
                    oa_cache[pos] = _provokes(enc, t, pos)
                ooa = oa_cache[pos]
                adjacent = sum(1 for x in threats if grid.distance(pos, x.pos) <= 5)
                cov = _at(t, pos, lambda: _cover_from(enc, t, h, pos)) if ranged else 0
                score = exp - 4 * len(ooa) - cost / 100      # safe and short beats risky
                if ranged and adjacent:
                    score -= 2                               # stay out of melee
                if not ranged and adjacent > 1:
                    score -= 0.5 * (adjacent - 1)            # do not get surrounded
                score += 0.4 * cov                           # shoot from cover
                if best is None or score > best["score"]:
                    best = {"pos": pos, "cost": cost, "hits": hits, "exp": exp, "adv": adv,
                            "oa": ooa, "score": score, "cover": cov, "ranged": ranged}
            if best is None:
                continue
            tags = []
            if downed:
                tags.append("downed: a hit is a death save failure, two within 5 ft")
                best["score"] = -0.5 - best["cost"] / 100    # listed below Dodge
            else:
                if h.hp + h.temp_hp <= avg:
                    tags.append("can finish them")
                    best["score"] += 2
                if h.ac == low_ac and len(up) > 1:
                    tags.append("lowest AC")
                    best["score"] += 0.5
            if held is not None and h.id == held.id:
                tags.append("keeps grapple")
                best["score"] += 1
            brk = _conc_break(enc, R, h, best["hits"][0] / 100, best["exp"])
            if brk:
                tags.append(f"{h.name} concentrating ({round(brk * 100)}% to break)")
                best["score"] += 1.5 * brk
            if best["cover"]:
                tags.append(f"cover +{best['cover']}")
            if best["oa"]:
                tags.append("provokes " + ", ".join(o["name"] for o in best["oa"]))
            if best["adv"] != "normal":
                tags.append(best["adv"])
            then_to = None
            if best["ranged"] and not downed:
                back = _fallback(enc, t, best["pos"], best["cost"], threats)
                if back is not None:
                    then_to = label(back)
                    best["score"] += 0.5
            plans.append({"kind": kind, "attack": name, "routine": routine, "target": h.id,
                          "target_name": h.name,
                          "move_to": None if best["pos"] == t.pos else label(best["pos"]),
                          "then_to": then_to,
                          "hit_percent": best["hits"][0], "hits": best["hits"],
                          "expected": round(best["exp"], 1),
                          "score": best["score"], "tags": tags})
    # A single attack that is part of a Multiattack plan on the same target is noise.
    routines = engine.multiattack_routines(t)
    in_multi = {(p["target"], a["name"]) for p in plans if p["kind"] == "multiattack"
                for a in engine.expand_routine(t, routines[p["routine"] - 1])[0]}
    return [p for p in plans if p["kind"] != "attack" or (p["target"], p["attack"]) not in in_multi]


ALL = 99          # options(limit=ALL): every plan, for policy.pick


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
    threats = _threats(enc, t, R)
    out = []

    if action_free:
        out += _attack_plans(enc, t, R, squares)
        out += _area_plans(enc, t, R, squares)

    flee_below, flee_tag = _morale(enc, t)
    low_hp = t.hp <= t.max_hp * flee_below
    held = _held(enc, t)
    if threats and left > 0 and (held is None or low_hp) and (
            low_hp or 2 * t.hp <= t.max_hp or _kites(t)):   # a healthy melee-only creature closes in
        def nearest(pos):
            return min(grid.distance(pos, h.pos) for h in threats)
        far = max(squares, key=lambda p: (nearest(p), -squares[p]))
        if nearest(far) > nearest(t.pos):
            ooa = _provokes(enc, t, far)
            disengage = bool(ooa) and action_free
            risk = 0 if disengage else 4 * len(ooa)
            tags = [flee_tag] if low_hp else ["keep distance"]
            if held is not None:
                tags.append(f"releases {held.name}")
            out.append({"kind": "retreat", "move_to": label(far), "disengage": disengage,
                        "score": (20 if low_hp else -1) - risk, "tags": tags})

    live = [o for o in out if o["kind"] in ("attack", "multiattack", "area") and o["score"] > -0.5]
    if action_free and threats and held is None and not live:
        dash = {parse_square(s): c for s, c in reach["dash"].items()}
        if dash:
            best = min(dash, key=lambda p: (min(grid.distance(p, h.pos) for h in threats), dash[p]))
            out.append({"kind": "dash", "move_to": label(best), "score": 1,
                        "tags": ["close the distance"]})

    if hostiles and not threats:
        # Everyone against them is down: Dodge means nothing. The story decides.
        out.append({"kind": "wait", "score": 0,
                    "tags": ["no one is fighting back: GM decides to finish, capture or leave"]})
    elif action_free:
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
    multi = bool(engine.multiattack_routines(t))
    runnable = {a["name"] for a in spells.area_actions(t)}
    return [a["name"] for a in t.extra.get("actions", [])
            if not (a.get("kind") == "multiattack" and multi) and a["name"] not in runnable]


def _label(o) -> str:
    tags = f" [{'; '.join(o['tags'])}]" if o.get("tags") else ""
    if o["kind"] == "area":
        what = (f"{o['action']}: {o['area_text']} at {o['aim']} ({o['dc_text']}, {o['fail_text']}, "
                f"~{o['expected']} dmg)")
        return (f"Move to {o['move_to']}, then {what}" if o["move_to"] else what) + tags
    if o["kind"] in ("attack", "multiattack"):
        if o["kind"] == "multiattack":
            what = f"Multiattack {o['target_name']}: {o['attack']}"
            odds = "/".join(f"{h}%" for h in o["hits"])
        else:
            what, odds = f"{o['attack']} {o['target_name']}", f"{o['hit_percent']}%"
        odds = f"({odds} to hit, ~{o['expected']} dmg)"
        where = f"Move to {o['move_to']} and " if o["move_to"] else ""
        here = "" if o["move_to"] else " from here"
        then = f", then fall back to {o['then_to']}" if o.get("then_to") else ""
        return f"{where}{what}{here}{then} {odds}{tags}"
    if o["kind"] == "retreat":
        first = "Disengage, then retreat" if o.get("disengage") else "Retreat"
        return f"{first} to {o['move_to']}{tags}"
    if o["kind"] == "dash":
        return f"Dash to {o['move_to']}{tags}"
    if o["kind"] == "wait":
        return f"Wait{tags}"
    return f"Hold position and Dodge{tags}"


def choose(enc, roller, token_ref, n: int, reactions: dict = None, limit: int = 5) -> dict:
    """Run option n for this creature. Returns {"option", "text"}."""
    t = engine._resolve(enc, token_ref)
    opts = options(enc, t, limit)
    pick = next((o for o in opts if o["n"] == n), None)
    if pick is None:
        raise engine.CombatError(f"{t.name} has no option {n} (options 1 to {len(opts)}).")
    lines = []
    kind = pick["kind"]
    R = engine.rules_for(enc)
    if kind in ("attack", "multiattack"):
        if pick["move_to"]:
            lines.append(engine.move(enc, roller, t, pick["move_to"], reactions)["text"])
            if not R.can_act(t):
                return {"option": pick, "text": " ".join(lines)}
        if kind == "multiattack":
            lines.append(engine.multiattack(enc, roller, t, pick["target"], pick["routine"],
                                            reactions)["text"])
        else:
            lines.append(engine.attack(enc, roller, t, pick["target"], pick["attack"], reactions)["text"])
        if pick.get("then_to") and R.can_act(t):
            try:
                lines.append(engine.move(enc, roller, t, pick["then_to"], reactions)["text"])
            except engine.CombatError as e:
                lines.append(f"{t.name} holds position ({e}).")
    elif kind == "area":
        if pick["move_to"]:
            lines.append(engine.move(enc, roller, t, pick["move_to"], reactions)["text"])
            if not R.can_act(t):
                return {"option": pick, "text": " ".join(lines)}
        lines.append(spells.use_action(enc, roller, t, pick["action"], pick["aim"], reactions)["text"])
    elif kind == "retreat":
        if pick.get("disengage"):
            engine.disengage(enc, t)
        lines.append(engine.move(enc, roller, t, pick["move_to"], reactions)["text"])
    elif kind == "dash":
        engine.dash(enc, t)
        lines.append(engine.move(enc, roller, t, pick["move_to"], reactions)["text"])
    elif kind == "wait":
        lines.append(f"{t.name} waits.")
    else:
        lines.append(engine.dodge(enc, t)["text"])
    return {"option": pick, "text": " ".join(lines)}
