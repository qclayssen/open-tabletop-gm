"""sight.py: what a creature can see, for the display's overlays and the GM.

Nothing here changes a rule: it reads the same line of sight and cover the
engine uses for attacks (grid.py), so the map shading and a real attack
always agree.

  fog(enc)          squares at least one living PC can see; the display
                    dims the rest and, in "hide" mode, leaves out creatures
                    standing there (the mode lives in enc.meta["fog"])
  sight(enc, ref)   cover from one creature to every square it can see, and
                    a short text for the GM (the same facts as the overlay)
"""

from __future__ import annotations

from .grid import label

FOG_MODES = ("hide", "dim", "off")
DEFAULT_FOG = "hide"
_LEVEL = {2: "half", 5: "three-quarters"}


def fog_mode(enc) -> str:
    mode = (enc.meta or {}).get("fog", DEFAULT_FOG)
    return mode if mode in FOG_MODES else DEFAULT_FOG


def fog(enc):
    """Squares some living PC sees, or None when fog is off or no PC is left
    to see (an empty map would help nobody)."""
    if fog_mode(enc) == "off":
        return None
    eyes = [t for t in enc.tokens.values() if t.side == "pc" and t.active]
    if not eyes:
        return None
    grid = enc.board()
    seen = set()
    for t in eyes:
        seen |= grid.visible_from(t.pos)
    return seen


def shown(enc, t, visible) -> bool:
    """Is token t drawn for the players? Hidden enemies never are; in "hide"
    fog, neither is a creature no PC can see. PCs and allies always are."""
    if t.side in ("pc", "ally"):
        return True
    if t.side == "enemy" and t.has("hidden"):
        return False
    return visible is None or fog_mode(enc) != "hide" or t.pos in visible


def redact_log(enc, entries: list, visible) -> list:
    """Log entries for the players: the names of creatures they cannot see
    become "an unseen creature" (and those entries lose their dice lines,
    whose labels name them too). The GM's own log is never changed."""
    names = sorted({t.name for t in enc.tokens.values() if not shown(enc, t, visible)},
                   key=len, reverse=True)
    if not names:
        return list(entries)
    out = []
    for e in entries:
        text = e.get("text", "")
        if any(n in text for n in names) or e.get("actor") in {t.id for t in enc.tokens.values()
                                                               if t.name in names}:
            for n in names:
                text = text.replace(n, "an unseen creature")
            text = text[:1].upper() + text[1:]
            e = dict(e, text=text, actor="", rolls=[])
        out.append(e)
    return out


def sight(enc, ref, players: bool = False) -> dict:
    """Cover from ref's square to every square it can see.

    cover: {label: "half" | "three-quarters"} for squares with cover (the rest
    of `visible` has none); squares missing from `visible` are total cover.
    Creatures give cover as in an attack. players=True (the display) counts
    and lists only the creatures the players can see, so the overlay never
    gives away an unseen enemy; the GM sees everything."""
    from .engine import _resolve
    me = _resolve(enc, ref)
    grid = enc.board()
    visible = fog(enc)
    known = [t for t in enc.tokens.values() if t.active and t.id != me.id
             and (not players or shown(enc, t, visible))]
    others = frozenset(t.pos for t in known)
    seen = grid.visible_from(me.pos)
    cover = {}
    for sq in seen:
        if sq == me.pos or not grid.passable(sq):   # cover on a wall square means nothing
            continue
        lvl = grid.cover(me.pos, sq, creatures=others)["cover"]
        if lvl in _LEVEL:
            cover[label(sq)] = _LEVEL[lvl]
    creatures = []
    for t in known:
        state = "no line of sight" if t.pos not in seen else cover.get(t.square, "no") + " cover"
        creatures.append({"id": t.id, "name": t.name, "square": t.square, "sight": state})
    return {"from": me.id, "square": me.square,
            "visible": sorted(label(s) for s in seen),
            "cover": cover, "creatures": creatures, "text": _text(me, creatures)}


def _text(me, creatures) -> str:
    groups = {}
    for c in creatures:
        groups.setdefault(c["sight"], []).append(f"{c['name']} {c['square']}")
    if not groups:
        return f"{me.name} ({me.square}) sees no other creature."
    order = ("no cover", "half cover", "three-quarters cover", "no line of sight")
    parts = [f"{k[0].upper() + k[1:]}: {', '.join(groups[k])}." for k in order if k in groups]
    return f"From {me.name} ({me.square}): " + " ".join(parts)
