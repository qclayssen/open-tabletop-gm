"""triggers.py: moments where the small DM should hear from the advisor first.

Read from the combat snapshot only, so they cost nothing to check. Each has a
stable key (fight, creature) and fires once; the caller remembers seen keys.
A boss is an enemy with at least twice the highest PC max HP.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Trigger:
    key: str
    question: str
    advisors: tuple


def _names(tokens) -> str:
    return ", ".join(t["name"] for t in tokens) or "unknown foes"


def check(snap) -> list:
    if not snap or snap.get("status") != "active":
        return []
    fight, tokens = snap["key"], snap["tokens"]
    pcs = [t for t in tokens if t["side"] == "pc"]
    enemies = [t for t in tokens if t["side"] == "enemy" and not t["dead"]]
    found = [Trigger(f"start:{fight}",
                     f"A fight just started against {_names(enemies)}. What is at stake, "
                     "and how should these enemies fight?", ("tactician", "director"))]
    top = max((t["max_hp"] for t in pcs), default=0)
    for t in enemies:
        if top and t["max_hp"] >= 2 * top:
            found.append(Trigger(f"boss:{fight}:{t['id']}",
                                 f"{t['name']} is a boss-level foe here. How should it be "
                                 "staged and played?", ("director", "tactician")))
    for t in pcs:
        if t["dead"]:
            found.append(Trigger(f"dead:{fight}:{t['id']}",
                                 f"{t['name']} has died. How should this moment land, and "
                                 "which threads does it touch?", ("director", "continuity")))
        elif t["hp"] == 0:
            found.append(Trigger(f"down:{fight}:{t['id']}",
                                 f"{t['name']} just dropped to 0 HP. How should the scene "
                                 "react?", ("director",)))
    return found


def fresh(found, seen) -> list:
    return [t for t in found if t.key not in seen]


def advisors_for(found, limit: int = 3) -> list:
    names = []
    for t in found:
        names += [a for a in t.advisors if a not in names]
    return names[:limit]


def question(found) -> str:
    return " ".join(t.question for t in found)
