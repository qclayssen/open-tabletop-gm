#!/usr/bin/env python3
"""
spell_slots.py — how many spell slots a caster has, from class and level

The Spell Slots table on a character sheet is the authoritative answer when the
sheet has one. This module is what fills that table in for the sheets that do
not: a half-finished sheet, a caster added to a campaign mid-session, an NPC the
GM invented at the table. Without it a caster with no table is a caster the
engine will refuse to let cast anything, because "no slots" and "no slots left"
are the same thing to `spells._check_slot` and only one of them is true.

Three tables cover the SRD's casters, because the SRD only has three shapes of
caster (PHB p112-118, p143):

    FULL    bard, cleric, druid, sorcerer, wizard — cast at every level
    HALF    paladin, ranger — start casting at 2nd level, and half as fast
    PACT    warlock — a different thing entirely (see below)

A Warlock's Pact Magic slots are not "one slot per level from a table". Their
number is the warlock's level and their level is the warlock's level, capped at
5th — that is the whole of the feature. Modelling it as an ordinary table would
be a table that lies at every level, and the two places that care (short-rest
restoration, and the display's pips) would read the lie.

Artificer is absent on purpose: it is not in the 5.1 SRD, so there is no
house-published table to copy. A campaign that uses one supplies it on the
sheet, which is the same path a homebrew class takes.

    >>> for_level("wizard", 3)
    {'1': 3, '2': 3}
    >>> for_level("paladin", 1)
    {}
    >>> for_level("fighter", 3)
    {}
"""

from __future__ import annotations

__all__ = [
    "CASTERS",
    "FULL_CASTERS",
    "HALF_CASTERS",
    "PACT_CLASSES",
    "is_caster",
    "pact_slot_level",
    "for_level",
]

MAX_LEVEL = 20

FULL_CASTERS = ("bard", "cleric", "druid", "sorcerer", "wizard")
HALF_CASTERS = ("paladin", "ranger")
PACT_CLASSES = ("warlock",)
CASTERS = FULL_CASTERS + HALF_CASTERS + PACT_CLASSES

# Level -> how many slots of each level, first number is 1st level.
_FULL = {
    1: (2,),
    2: (3,),
    3: (4, 2),
    4: (4, 3),
    5: (4, 3, 2),
    6: (4, 3, 3),
    7: (4, 3, 3, 1),
    8: (4, 3, 3, 2),
    9: (4, 3, 3, 3, 1),
    10: (4, 3, 3, 3, 2),
    11: (4, 3, 3, 3, 2, 1),
    12: (4, 3, 3, 3, 2, 1),
    13: (4, 3, 3, 3, 2, 1, 1),
    14: (4, 3, 3, 3, 2, 1, 1),
    15: (4, 3, 3, 3, 2, 1, 1, 1),
    16: (4, 3, 3, 3, 2, 1, 1, 1),
    17: (4, 3, 3, 3, 2, 1, 1, 1, 1),
    18: (4, 3, 3, 3, 3, 1, 1, 1, 1),
    19: (4, 3, 3, 3, 3, 2, 1, 1, 1),
    20: (4, 3, 3, 3, 3, 2, 2, 1, 1),
}

_HALF = {
    2: (2,),
    3: (3,),
    4: (3,),
    5: (4, 2),
    6: (4, 2),
    7: (4, 3),
    8: (4, 3),
    9: (4, 3, 2),
    10: (4, 3, 2),
    11: (4, 3, 3),
    12: (4, 3, 3),
    13: (4, 3, 3, 1),
    14: (4, 3, 3, 1),
    15: (4, 3, 3, 2),
    16: (4, 3, 3, 2),
    17: (4, 3, 3, 3, 1),
    18: (4, 3, 3, 3, 1),
    19: (4, 3, 3, 3, 2),
    20: (4, 3, 3, 3, 2),
}


def normalise_class(name) -> str:
    """The class key, from anything a sheet might carry.

    Sheets write the class the way the player says it at the table: "**Class:**
    Wizard 1 (Chronurgy at 2)", "Rogue 3 / Scout", "warlock". Only the first
    word that names a known class is the class; the rest is level and
    multiclass, which the sheet's own Level line already has.
    """
    text = str(name or "").lower()
    for word in text.replace("/", " ").replace("(", " ").split():
        word = word.strip(".,:;")
        if word in CASTERS:
            return word
    return ""


def is_caster(name) -> bool:
    return normalise_class(name) in CASTERS


def pact_slot_level(level: int) -> int:
    """Which level a Warlock's Pact Magic slots are, capped at 5 (PHB p107)."""
    return min(max(int(level or 1), 1), 5)


def for_level(class_name, level) -> dict:
    """{"1": 2, "2": 1} — how many slots of each level this caster has, unspent.

    {} for anyone who is not a spellcaster, for a level outside 1..20, and for
    a class this SRD has no table for: a caller that gets {} must leave the
    sheet's own table alone rather than overwrite it with nothing.
    """
    cls = normalise_class(class_name)
    try:
        lv = int(str(level).strip())
    except (TypeError, ValueError):
        return {}
    if not 1 <= lv <= MAX_LEVEL:
        return {}
    if cls in PACT_CLASSES:
        return {str(pact_slot_level(lv)): lv}
    table = _HALF if cls in HALF_CASTERS else _FULL if cls in FULL_CASTERS else None
    if table is None or lv not in table:
        return {}
    return {str(i + 1): n for i, n in enumerate(table[lv])}


def spent_table(class_name, level) -> dict:
    """for_level, in the engine's stored shape: {"1": {"total": 2, "used": 0}}.

    The engine counts slots spent, not slots left, so a freshly built caster
    starts at used 0 — the same thing a freshly written sheet says.
    """
    return {lv: {"total": n, "used": 0} for lv, n in for_level(class_name, level).items()}


if __name__ == "__main__":  # a quick look, and a self-check of the tables
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in CASTERS:
        print(json.dumps(spent_table(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1), indent=1))
    else:
        for cls in CASTERS:
            print(f"{cls:9} 1:{for_level(cls, 1)} 5:{for_level(cls, 5)} 20:{for_level(cls, 20)}")
