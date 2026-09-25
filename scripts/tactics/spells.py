"""spells.py: casting spells and area actions on the grid.

    cast(enc, roller, caster, spell, targets, level)   one call per spell
    preview(enc, caster, spell, target, level)         what it would hit, no dice
    castable(enc, caster)                              the caster's spells and why not
    use_action(enc, roller, creature, action, target)  a monster's area save action
                                                       (breath weapons), recharge included

The rules system says what a spell does (Rules.spell -> spec, see
systems/dnd5e/tactics_spells.py); this module does the rest: action economy
and the bonus-action spell rule, slots, range and line of sight, the area on
the grid, one damage roll for everyone in an area, a save per creature (cover
helps DEX saves), Silvery Barbs against a successful save, concentration, and
effects the engine tracks afterwards. Everything is checked before anything
is spent, so a refused cast costs nothing.
"""

from __future__ import annotations

from . import effects as fx
from .core import CombatError, hostile, log, player_rolls, resolve, rules_for
from .engine import _attack_context, _require_action, _require_turn, _resolve_attack, average_damage
from .grid import area as grid_area
from .grid import label, parse_square
from .roller import Roller, average

ABILITY_NAMES = {"str": "STR", "dex": "DEX", "con": "CON", "int": "INT", "wis": "WIS", "cha": "CHA"}


# ─── lookups ──────────────────────────────────────────────────────────────────

def _spec(enc, caster, name: str, level=None) -> dict:
    try:
        return rules_for(enc).spell(caster, name, level)
    except ValueError as e:
        raise CombatError(str(e)) from None


def _point(enc, ref):
    """(square, token or None) for a token name/id or a square label."""
    if not isinstance(ref, str):
        return tuple(ref), None
    try:
        t = enc.token(ref)
        return t.pos, t
    except KeyError:
        pass
    try:
        return parse_square(ref), None
    except ValueError:
        raise CombatError(f"{ref!r} is neither a creature nor a square.") from None


def _in_range(enc, caster, pos, spec) -> str:
    """'' if pos is a legal point for this spell, else the reason."""
    grid = enc.board()
    if not grid.in_bounds(pos):
        return f"{label(pos)} is off the map."
    rng = spec.get("range") or 0
    dist = grid.distance(caster.pos, pos)
    if spec["origin"] == "self" and not spec.get("area"):
        return "" if pos == caster.pos else f"{spec['name']} targets only {caster.name}."
    if spec["origin"] != "self" and dist > rng:
        return f"{label(pos)} is {dist} ft away; {spec['name']} reaches {rng} ft."
    if pos != caster.pos and not grid.line_of_sight(caster.pos, pos):
        return f"{caster.name} has no line of sight to {label(pos)}."
    return ""


def area_squares(enc, caster_pos, shape: dict, origin: str, pos) -> list:
    grid = enc.board()
    try:
        return grid_area(grid, shape["shape"], shape["size"], caster_pos, pos,
                         width=shape.get("width", 5), from_self=(origin == "self"))["squares"]
    except ValueError as e:
        raise CombatError(str(e)) from None


def _cover_from(enc, origin_sq, t) -> int:
    if t.pos == tuple(origin_sq):
        return 0
    others = {x.pos for x in enc.tokens.values() if x.active and x.id != t.id}
    return enc.board().cover(tuple(origin_sq), t.pos, creatures=others)["cover"]


# ─── economy ──────────────────────────────────────────────────────────────────

def _check_economy(enc, c, spec) -> None:
    if spec["mode"] == "reaction":
        raise CombatError(f"{spec['name']} is a reaction: the engine offers it when its trigger happens.")
    if spec["casting"] == "reaction":
        raise CombatError(f"{spec['name']} is a reaction the engine does not run: "
                          "the GM narrates it when its trigger happens.")
    if spec["casting"] not in ("action", "bonus"):
        raise CombatError(f"{spec['name']} takes longer than an action to cast.")
    if spec["casting"] == "bonus":
        _require_turn(enc, c)
        if enc.turn.bonus_used:
            raise CombatError(f"{c.name} has already used their bonus action this turn.")
    else:
        _require_action(enc, c)
    # PHB p202: on a turn with a bonus-action spell, the only other spell is a
    # cantrip with a casting time of 1 action.
    prior = enc.turn.spells
    action_cantrip = spec["casting"] == "action" and spec["level"] == 0
    if spec["casting"] == "bonus" and any(not (p["casting"] == "action" and p["level"] == 0)
                                          for p in prior):
        raise CombatError("After casting a spell this turn, a bonus-action spell is allowed only "
                          "if that spell was a 1-action cantrip (PHB p202).")
    if any(p["casting"] == "bonus" for p in prior) and not action_cantrip:
        raise CombatError("After a bonus-action spell, only a 1-action cantrip can be cast this turn "
                          "(PHB p202).")


def _check_slot(c, spec) -> str:
    if not spec["slot"]:
        return ""
    lv = str(spec["slot"])
    s = (c.extra.get("slots") or {}).get(lv)
    if not s or s.get("used", 0) >= s.get("total", 0):
        left = [k for k, v in sorted((c.extra.get("slots") or {}).items())
                if v.get("used", 0) < v.get("total", 0)]
        raise CombatError(f"{c.name} has no level {lv} slot left"
                          + (f" (left: level {', '.join(left)})." if left else "."))
    return lv


# ─── targets ──────────────────────────────────────────────────────────────────

def _targets(enc, c, spec, refs: list) -> dict:
    """Validate targets for a spec. Returns {"affected": [tokens], "squares": [...],
    "origin_sq", "darts": [(token, n)], "target": token}. Raises CombatError."""
    refs = [r for r in (refs or []) if r]
    mode = spec["mode"]
    out = {"affected": [], "squares": [], "origin_sq": c.pos, "darts": [], "target": None}
    if mode == "narrate":
        return out
    if spec.get("area"):
        if len(refs) != 1:
            raise CombatError(f"{spec['name']} needs one square or creature to aim at.")
        pos, _ = _point(enc, refs[0])
        why = _in_range(enc, c, pos, spec)
        if why:
            raise CombatError(why)
        squares = area_squares(enc, c.pos, spec["area"], spec["origin"], pos)
        out["squares"] = squares
        out["origin_sq"] = c.pos if spec["origin"] == "self" else pos
        on = set(squares)
        out["affected"] = [t for t in enc.tokens.values() if t.active and t.pos in on]
        return out
    if mode == "darts":
        if not refs:
            raise CombatError(f"{spec['name']} needs a target.")
        n = spec["darts"]
        if len(refs) not in (1, n):
            raise CombatError(f"{spec['name']} has {n} darts: name one target, or {n} (repeats allowed).")
        counts = {}
        for r in (refs * n if len(refs) == 1 else refs):
            t = resolve(enc, r)
            if not t.active or t.id == c.id:
                raise CombatError(f"{t.name} is not a valid target.")
            why = _in_range(enc, c, t.pos, spec)
            if why:
                raise CombatError(why)
            counts[t.id] = counts.get(t.id, 0) + 1
        out["darts"] = [(enc.tokens[k], v) for k, v in counts.items()]
        return out
    if not refs and mode in ("effect", "heal") and spec["origin"] in ("self", "touch"):
        refs = [c.id]
    if len(refs) != 1:
        raise CombatError(f"{spec['name']} needs one target.")
    t = resolve(enc, refs[0])
    if not t.active:
        raise CombatError(f"{t.name} is dead.")
    if mode == "attack":
        if t.id == c.id:
            raise CombatError(f"{c.name} cannot target themselves.")
        ctx, why = _attack_context(enc, c, t, spec["attack"])
        if ctx is None:
            raise CombatError(why)
        out["ctx"] = ctx
    else:
        why = _in_range(enc, c, t.pos, spec)
        if why:
            raise CombatError(why)
    out["target"] = t
    out["affected"] = [t]
    return out


# ─── casting ──────────────────────────────────────────────────────────────────

def cast(enc, roller: Roller, caster_ref, spell: str, targets: list = None, level: int = None,
         reactions: dict = None, readied: dict = None) -> dict:
    """Cast a spell. readied: the Ready action's stored spell, released as a
    reaction (the slot was spent and the economy paid when it was readied)."""
    c = resolve(enc, caster_ref)
    spec = _spec(enc, c, spell, level if not readied else readied.get("level"))
    reactions = reactions or {}
    if readied is None:
        _check_economy(enc, c, spec)
        lv = _check_slot(c, spec)
    else:
        lv = ""
    tg = _targets(enc, c, spec, targets)

    # Everything is legal: spend, then resolve.
    mark = len(roller.log)
    lines = []
    if lv:
        c.extra["slots"][lv]["used"] += 1
    if readied is None:
        if spec["casting"] == "bonus":
            enc.turn.bonus_used = True
        else:
            enc.turn.action_used = True
        enc.turn.spells.append({"level": spec["level"], "casting": spec["casting"]})
        enc.turn.undo_locked = True
    head = f"{c.name} casts {spec['name']}" + (f" (level {lv} slot)" if lv else "")
    if readied and c.concentration == readied.get("concentration_name"):
        c.concentration = None                   # holding the spell ends as it is released
    if spec["concentration"]:
        lines += fx.start_concentration(enc, c, spec["name"])
    mode = spec["mode"]
    if c.has("hidden") and mode != "attack":     # a spell attack keeps its advantage, then reveals
        c.remove_condition("hidden")
        lines.append(f"{c.name} is no longer hidden.")
    if mode == "attack":
        res = _resolve_attack(enc, roller, c, tg["target"], spec["attack"], tg["ctx"], reactions)
        lines.append(res["text"])
    elif mode == "save":
        where = ""
        if tg["squares"]:
            names = ", ".join(t.name for t in tg["affected"]) or "nobody"
            where = f" ({spec['area']['size']} ft {spec['area']['shape']}: {names})"
        head += where + "."
        lines += _resolve_saves(enc, roller, c, spec["name"], tg["affected"], spec["save"],
                                spec["damage"], tg["origin_sq"], reactions,
                                conditions=spec["fail_conditions"], repeat=spec["repeat"],
                                concentration=spec["concentration"], on_fail=spec["on_fail"])
    elif mode == "darts":
        lines += _darts(enc, roller, c, spec, tg["darts"], reactions)
    elif mode == "heal":
        # An area heal (Mass Cure Wounds) heals the caster's side in the area.
        who = [tg["target"]] if tg["target"] else [t for t in tg["affected"] if not hostile(c, t)]
        for t in who:
            r = roller.roll(spec["heal"], c.name, f"{spec['name']} healing", player=player_rolls(enc, c))
            lines.append(rules_for(enc).heal(t, r.total)["text"])
        if not who:
            lines.append("Nobody on your side is in the area.")
    elif mode == "effect":
        lines.append(_effect(c, tg["target"], spec))
    else:
        lines.append("GM decides the effect.")
    text = " ".join([head if head.endswith(".") else head + "."] + lines)
    log(enc, "cast", c.id, text, roller, mark)
    return {"spell": spec["name"], "mode": mode, "slot": lv or None,
            "squares": [label(q) for q in tg["squares"]],
            "affected": [t.id for t in tg["affected"]], "text": text}


def _resolve_saves(enc, roller, c, name, affected, save, damage, origin_sq, reactions,
                   conditions=(), repeat=None, concentration=False, on_fail=None,
                   rider_effects=()) -> list:
    """One damage roll for everyone, then a save per creature; half or none on
    a success; conditions and tracked effects on a failure."""
    R = rules_for(enc)
    lines = []
    rolled = []
    if damage and affected:
        for p in damage:
            d = roller.roll(p["dice"], c.name, f"{name} damage", player=player_rolls(enc, c))
            rolled.append({"amount": d.total, "type": p.get("type", "")})
    ability = save["ability"]
    for t in affected:
        if t.dead:
            continue
        cover = _cover_from(enc, origin_sq, t) if ability == "dex" else 0
        res = R.saving_throw(t, ability, save["dc"], roller, player_rolls(enc, t), cover=cover)
        ok, text = res["success"], res["text"]
        if ok and res.get("natural") is not None:
            for bc in fx.barbs_casters(enc, t):
                rb = fx.silvery_barbs(enc, roller, bc, t, f"its {ABILITY_NAMES.get(ability, ability)} save",
                                      res["natural"], res["total"], bc, reactions)
                if rb:
                    _nat, total, more = rb
                    ok = total >= save["dc"]
                    text += " " + " ".join(more) + (" Now a failure." if not ok else " Still a success.")
                    break
        lines.append(text)
        if rolled:
            half = save.get("on_success") == "half"
            if not ok or half:
                parts = [{"amount": p["amount"] // 2 if ok else p["amount"], "type": p["type"]}
                         for p in rolled]
                dmg = R.damage(t, parts)
                lines.append(dmg["text"])
                lines += fx.after_damage(enc, roller, t, dmg)
        if not ok and not t.dead:
            if conditions:
                blocked = fx.add(t, {"name": name.lower(), "source": c.id, "conditions": list(conditions),
                                     "concentration": concentration,
                                     "save": {"ability": ability, "dc": save["dc"]} if repeat else None,
                                     "repeat": repeat})
                got = [x for x in conditions if x not in blocked]
                if got:
                    lines.append(f"{t.name} is {' and '.join(got)}"
                                 + (" (saves again at the end of each turn)." if repeat else "."))
                if blocked:
                    lines.append(f"{t.name} is immune to {', '.join(blocked)}.")
                lines += fx.check_incapacitated(enc, t)
            if on_fail:
                fx.add(t, dict(on_fail, source=c.id))
                if on_fail.get("save_penalty"):
                    lines.append(f"{t.name} subtracts {on_fail['save_penalty']} from their next save "
                                 f"before the end of {c.name}'s next turn.")
            for eff in rider_effects:
                cond = eff.get("condition")
                if cond and cond not in {x.lower() for x in t.condition_immunities}:
                    fx.add(t, {"name": name.lower(), "source": c.id, "conditions": [cond],
                               "save": {"ability": eff["ability"], "dc": eff["dc"]},
                               "repeat": eff.get("repeat")})
                    dur = f" ({eff['duration']})" if eff.get("duration") else ""
                    lines.append(f"{t.name} is {cond}{dur}.")
                    lines += fx.check_incapacitated(enc, t)
    return lines


def _darts(enc, roller, c, spec, darts, reactions) -> list:
    """Magic Missile: every dart hits; one roll of the dart's dice for all of them
    (the Sage Advice reading). Each dart is its own damage instance, so a
    concentrating target saves per dart. Shield blocks the darts at its caster."""
    R = rules_for(enc)
    lines = []
    blocked = set()
    for t, _n in darts:
        more = fx.shield_vs_missiles(enc, c, t, reactions)
        if more:
            blocked.add(t.id)
            lines += more
    live = [(t, n) for t, n in darts if t.id not in blocked]
    if not live:
        return lines
    dart = spec["dart"]
    r = roller.roll(dart["dice"], c.name, f"{spec['name']} dart", player=player_rolls(enc, c))
    for t, n in live:
        extra = []
        for _ in range(n):
            if t.dead:
                break
            dmg = R.damage(t, [{"amount": r.total, "type": dart.get("type", "")}])
            extra += fx.after_damage(enc, roller, t, dmg)
        state = ("dies" if t.dead else "drops to 0 HP" if t.hp == 0 else f"{t.hp}/{t.max_hp} HP")
        lines.append(f"{n} dart{'s' if n > 1 else ''} hit {t.name} for {r.total} "
                     f"{dart.get('type', '')} each; {t.name} {state}.")
        lines += extra
    return lines


def _effect(c, t, spec) -> str:
    if spec.get("effect") == "mage_armor":
        base = 13 + t.dex_mod
        if t.ac >= base:
            return f"{t.name}'s AC stays {t.ac} (already {t.ac} or better)."
        t.extra["ac_before_mage_armor"] = t.ac
        t.ac = base
        return f"{t.name}'s AC is now {base} for 8 hours (no armor)."
    return "GM decides the effect."


# ─── previews and lists ───────────────────────────────────────────────────────

def preview(enc, caster_ref, spell: str, target=None, level: int = None) -> dict:
    """What a cast would do, without rolling or spending: the squares, every
    creature caught with its chance to fail the save and expected damage (allies
    flagged), or the hit chance for a spell attack."""
    c = resolve(enc, caster_ref)
    spec = _spec(enc, c, spell, level)
    R = rules_for(enc)
    out = {"spell": spec["name"], "mode": spec["mode"], "squares": [], "affected": [],
           "legal": True, "reason": ""}
    try:
        tg = _targets(enc, c, spec, [target] if isinstance(target, str) else (target or []))
    except CombatError as e:
        out.update(legal=False, reason=str(e), text=str(e))
        if spec.get("area") and isinstance(target, str):
            try:                                   # still show the shape, greyed out
                pos, _ = _point(enc, target)
                out["squares"] = [label(q) for q in area_squares(enc, c.pos, spec["area"],
                                                                  spec["origin"], pos)]
            except CombatError:
                pass
        return out
    out["squares"] = [label(q) for q in tg["squares"]]
    avg = sum(average(p["dice"]) for p in spec["damage"])
    dtype = spec["damage"][0]["type"] if spec["damage"] else ""
    rows = []
    if spec["mode"] == "save":
        for t in tg["affected"]:
            cover = _cover_from(enc, tg["origin_sq"], t) if spec["save"]["ability"] == "dex" else 0
            ch = R.save_chance(t, spec["save"]["ability"], spec["save"]["dc"], cover)
            k = 0.5 if spec["save"]["on_success"] == "half" else 0
            exp = avg * R.damage_multiplier(t, dtype) * (ch["fail"] + (1 - ch["fail"]) * k)
            rows.append({"id": t.id, "name": t.name, "side": t.side, "square": t.square,
                         "ally": not hostile(c, t), "fail_percent": ch["percent_fail"],
                         "cover": cover, "expected": round(exp, 1)})
    elif spec["mode"] == "attack":
        t = tg["target"]
        hc = R.hit_chance(c, t, spec["attack"], tg["ctx"])
        rows.append({"id": t.id, "name": t.name, "side": t.side, "square": t.square,
                     "ally": not hostile(c, t), "hit_percent": hc["percent"],
                     "expected": round(hc["chance"] * avg * R.damage_multiplier(t, dtype), 1)})
    elif spec["mode"] == "darts":
        for t, n in tg["darts"]:
            rows.append({"id": t.id, "name": t.name, "side": t.side, "square": t.square,
                         "ally": not hostile(c, t), "darts": n,
                         "expected": round(n * average(spec["dart"]["dice"])
                                           * R.damage_multiplier(t, spec["dart"].get("type")), 1)})
    out["affected"] = rows
    out["text"] = _preview_text(spec, rows, out["squares"])
    return out


def _preview_text(spec, rows, squares) -> str:
    name = spec["name"]
    if not rows:
        return f"{name}: {len(squares)} squares, nobody caught." if squares else f"{name}: no target."
    parts = []
    for r in rows:
        if "fail_percent" in r:
            parts.append(f"{r['name']} {r['fail_percent']}% to fail, ~{r['expected']} dmg")
        elif "hit_percent" in r:
            parts.append(f"{r['name']} {r['hit_percent']}% to hit, ~{r['expected']} dmg")
        else:
            parts.append(f"{r['name']} {r['darts']} dart(s), ~{r['expected']} dmg")
        if r["ally"]:
            parts[-1] += " (ALLY)"
    return f"{name}: " + "; ".join(parts) + "."


def castable(enc, caster_ref) -> list:
    """Every known spell with how it targets and whether it can be cast now."""
    c = resolve(enc, caster_ref)
    R = rules_for(enc)
    out = []
    for name in R.known_spells(c):
        try:
            spec = R.spell(c, name)
        except ValueError as e:
            out.append({"name": name, "ok": False, "reason": str(e), "mode": "unknown",
                        "targeting": "none", "level": None, "casting": None, "area": None})
            continue
        row = {"name": spec["name"], "level": spec["level"], "casting": spec["casting"],
               "mode": spec["mode"], "range": spec["range"], "area": spec["area"],
               "concentration": spec["concentration"],
               "targeting": ("area" if spec["area"] else "darts" if spec["mode"] == "darts"
                             else "self" if spec["origin"] == "self" or spec["mode"] == "effect"
                             else "none" if spec["mode"] in ("narrate", "reaction") else "single"),
               "ok": True, "reason": ""}
        try:
            _check_economy(enc, c, spec)
            _check_slot(c, spec)
        except CombatError as e:
            row.update(ok=False, reason=str(e))
        out.append(row)
    return out


# ─── monster area actions ─────────────────────────────────────────────────────

def area_actions(token) -> list:
    """A creature's save actions the engine can run: an area and a DC, no flags."""
    return [a for a in token.extra.get("actions", [])
            if a.get("kind") == "save" and a.get("area") and not a.get("flags")
            and a.get("dc", {}).get("on_success") in ("half", "none")
            and (a.get("damage") or a.get("rider_effects"))]


def usable(token, action: dict) -> bool:
    u = (token.extra.get("usage") or {}).get(action["name"])
    if not u:
        return True
    return u.get("charged", True) and u.get("left", 1) > 0


def _action_spec(a: dict) -> dict:
    shape = dict(a["area"])
    return {"name": a["name"], "area": shape, "origin": "self", "range": 0,
            "save": {"ability": a["dc"]["ability"], "dc": int(a["dc"]["value"]),
                     "on_success": a["dc"]["on_success"]},
            "damage": a.get("damage", []), "rider_effects": a.get("rider_effects", [])}


def preview_action(enc, token_ref, action_name: str, target) -> dict:
    t = resolve(enc, token_ref)
    a = _find_action(t, action_name)
    spec = _action_spec(a)
    pos, _ = _point(enc, target)
    squares = area_squares(enc, t.pos, spec["area"], "self", pos)
    on = set(squares)
    return {"squares": squares, "affected": [x for x in enc.tokens.values() if x.active and x.pos in on]}


def _find_action(t, name: str) -> dict:
    low = (name or "").strip().lower()
    for a in area_actions(t):
        if a["name"].lower() == low or a["name"].lower().startswith(low):
            return a
    raise CombatError(f"{t.name} has no area action {name!r}"
                      + (f" (has: {', '.join(a['name'] for a in area_actions(t))})."
                         if area_actions(t) else "."))


def use_action(enc, roller: Roller, token_ref, action_name: str, target, reactions: dict = None) -> dict:
    """A monster's area save action (a breath weapon), aimed from where it stands."""
    t = resolve(enc, token_ref)
    _require_action(enc, t)
    a = _find_action(t, action_name)
    if not usable(t, a):
        raise CombatError(f"{t.name}'s {a['name']} is not recharged.")
    spec = _action_spec(a)
    pos, _ = _point(enc, target)
    squares = area_squares(enc, t.pos, spec["area"], "self", pos)
    on = set(squares)
    affected = [x for x in enc.tokens.values() if x.active and x.pos in on]
    mark = len(roller.log)
    u = (t.extra.get("usage") or {}).get(a["name"])
    if u is not None:
        if "charged" in u:
            u["charged"] = False
        if "left" in u:
            u["left"] -= 1
    enc.turn.action_used = True
    enc.turn.undo_locked = True
    names = ", ".join(x.name for x in affected) or "nobody"
    head = f"{t.name} uses {a['name']} ({spec['area']['size']} ft {spec['area']['shape']}: {names})."
    conds = [e for e in spec["rider_effects"] if e.get("condition")]
    lines = _resolve_saves(enc, roller, t, a["name"], affected, spec["save"], spec["damage"], t.pos,
                           reactions or {}, rider_effects=conds)
    if a.get("rider"):
        lines.append(f"GM decides: {a['rider'].rstrip('.')}.")
    text = " ".join([head] + lines)
    log(enc, "action", t.id, text, roller, mark)
    return {"action": a["name"], "squares": [label(q) for q in squares],
            "affected": [x.id for x in affected], "text": text}


__all__ = ["cast", "preview", "castable", "use_action", "preview_action", "area_actions",
           "usable", "area_squares", "average_damage"]
