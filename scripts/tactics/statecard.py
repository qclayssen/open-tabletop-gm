"""statecard.py: a compact, engine-computed state card for one creature.

The card is what a model (or a GM running without the display) should read
instead of inventing where things are: every creature with its square, HP and
distance in feet from the actor, the map's named landmarks with their squares,
and cover and line of sight from the actor. Every number comes from the
engine (Grid.distance for feet, sight.sight for cover and line of sight), so
the card and a real attack always agree. Nothing here is written by a model.

North is fixed: row 1 is the top edge and columns run A, B, C from the left,
so "north" is toward smaller row numbers and "east" toward later letters.

players=True applies the display's fog filter: a hidden enemy, or one no PC can
see in "hide" fog, is left out of both the card and the text, exactly as in
sight.sight(players=True). The default (the GM's view) lists everyone.
"""

from __future__ import annotations

from . import sight as sight_mod
from .grid import parse_square

NORTH = "North is up: row 1 is the north edge, columns run A, B, C... west to east."
MAX_LANDMARKS = 10          # nearest first; the card stays a few lines
_SQUARES_SHOWN = 4
# Terrain types that are scenery, not things to point at, unless the map names them.
_PLAIN = {"floor", "wall", "difficult", "void", "water"}


def _landmarks(enc) -> list:
    marks = (enc.meta or {}).get("landmarks")
    if marks is None:                    # an encounter saved before landmarks existed
        slug = (enc.meta or {}).get("slug")
        if slug:
            try:
                from . import maps
                marks = maps.load(slug)["meta"].get("landmarks")
            except (OSError, ValueError, KeyError):
                marks = None
    return [m for m in marks or [] if m.get("named") or m.get("type") not in _PLAIN]


def _span(squares: list) -> str:
    if len(squares) <= _SQUARES_SHOWN:
        return ",".join(squares)
    return f"{squares[0]}..{squares[-1]} ({len(squares)} squares)"


def statecard(enc, ref, players: bool = False) -> dict:
    """Structured state card for creature `ref` (id or name)."""
    from .engine import _resolve
    me = _resolve(enc, ref)
    grid = enc.board()
    s = sight_mod.sight(enc, me.id, players=players)
    seen = set(s["visible"])
    by_id = {c["id"]: c for c in s["creatures"]}

    creatures = []
    for t in enc.tokens.values():
        if t.id != me.id and t.id not in by_id:
            continue                                   # dead, or unseen by the players
        c = {"id": t.id, "name": t.name, "side": t.side, "square": t.square,
             "hp": t.hp, "max_hp": t.max_hp, "conditions": list(t.conditions),
             "distance_ft": grid.distance(me.pos, t.pos)}
        if t.id == me.id:
            c["sight"] = "self"
        else:
            c["sight"] = by_id[t.id]["sight"]
        if t.dead:
            continue
        creatures.append(c)
    creatures.sort(key=lambda c: (c["id"] != me.id, c["distance_ft"], c["id"]))

    marks = []
    for m in _landmarks(enc):
        pts = [(sq, parse_square(sq)) for sq in m["squares"]]
        d, near = min((grid.distance(me.pos, p), sq) for sq, p in pts)
        marks.append({"name": m["name"], "type": m["type"], "label": m.get("label", ""),
                      "squares": list(m["squares"]), "nearest": near, "distance_ft": d,
                      "in_sight": any(sq in seen for sq in m["squares"]),
                      "cover_from_actor": _best_cover(s, m["squares"])})
    marks.sort(key=lambda m: (m["distance_ft"], m["name"]))
    marks = marks[:MAX_LANDMARKS]

    card = {"actor": me.id, "square": me.square, "map": (enc.meta or {}).get("name", ""),
            "orientation": NORTH, "creatures": creatures, "landmarks": marks}
    card["text"] = render(card)
    return card


def _best_cover(s: dict, squares: list) -> str:
    """Cover the actor has against the landmark's most open visible square."""
    levels = [s["cover"].get(sq, "no") for sq in squares if sq in s["visible"]]
    if not levels:
        return "no line of sight"
    order = {"no": 0, "half": 1, "three-quarters": 2}
    return min(levels, key=order.__getitem__) + " cover"


def render(card: dict) -> str:
    """Compact text: a header, one line per creature, one for landmarks."""
    lines = [f"State card, {card['actor']} at {card['square']}"
             + (f" on {card['map']}" if card["map"] else "") + ". " + card["orientation"]]
    for c in card["creatures"]:
        if c["sight"] == "self":
            lines.append(f"- {c['id']} ({c['name']}) {c['square']} HP {c['hp']}/{c['max_hp']} [you]")
        else:
            lines.append(f"- {c['id']} ({c['name']}, {c['side']}) {c['square']} "
                         f"HP {c['hp']}/{c['max_hp']} {c['distance_ft']} ft, {c['sight']}")
    if card["landmarks"]:
        lines.append("Landmarks:")
        for m in card["landmarks"]:
            lines.append(f"- {m['name']} {_span(m['squares'])} {m['distance_ft']} ft "
                         f"(nearest {m['nearest']}), {m['cover_from_actor']}")
    return "\n".join(lines)
