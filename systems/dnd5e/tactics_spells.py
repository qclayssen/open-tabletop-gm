"""tactics_spells.py: what casting a 5e spell does on the grid, for one caster.

resolve(caster, name, level) turns a spell name into a spec the engine runs:
who it can target, the attack or save, the damage for this slot or character
level, the area, concentration, and effects the engine tracks (conditions on
a failed save, Mind Sliver's penalty, Mage Armor).

Numbers come from the SRD build (`mechanics` on each spell record, see
build_srd._spell_mechanics). BUILTIN adds what the SRD fields do not say
(Magic Missile's darts, conditions on a failed save) and the spells Kairos
needs that are not in the SRD (Mind Sliver, Silvery Barbs): numbers only, no
rules text. A spell the engine cannot run is still castable: the slot and the
action are spent and the GM narrates the effect ("narrate" mode).
"""

from __future__ import annotations

import pathlib
import re
import sys

MODES = ("attack", "save", "darts", "heal", "effect", "narrate")

BUILTIN = {
    # Not in the SRD: mechanics only (Tasha's Cauldron of Everything, Strixhaven).
    "mind sliver": {"name": "Mind Sliver", "level": 0, "casting": "action", "range": 60,
                    "origin": "point", "concentration": False,
                    "save": {"ability": "int", "on_success": "none"},
                    "damage": {"type": "psychic",
                               "character": {"1": "1d6", "5": "2d6", "11": "3d6", "17": "4d6"}},
                    "on_fail": {"name": "mind sliver", "save_penalty": "1d4", "ends": "end"}},
    "silvery barbs": {"name": "Silvery Barbs", "level": 1, "casting": "reaction", "range": 60,
                      "origin": "point", "concentration": False, "reaction": "silvery barbs"},
    # In the SRD, completed here.
    "shield": {"level": 1, "casting": "reaction", "reaction": "shield"},
    "magic missile": {"darts": 3, "dart": {"dice": "1d4+1", "type": "force"}},
    "mage armor": {"effect": "mage_armor"},
    "hold person": {"fail_conditions": ["paralyzed"], "repeat": "end"},
    "hold monster": {"fail_conditions": ["paralyzed"], "repeat": "end"},
    "blindness/deafness": {"fail_conditions": ["blinded"], "repeat": "end"},
    "hideous laughter": {"fail_conditions": ["prone", "incapacitated"], "repeat": "end"},
    "entangle": {"fail_conditions": ["restrained"]},
    "web": {"fail_conditions": ["restrained"]},
}

# SRD flags the engine can live with once BUILTIN fills the gap.
_COVERED = {"effect": ("darts", "effect", "fail_conditions", "reaction")}


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _srd(key: str):
    """The SRD record's mechanics for a spell, or None (dataset not built)."""
    here = str(pathlib.Path(__file__).parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import lookup                                       # systems/dnd5e/lookup.py
        rec = lookup.lookup_record(key, category="spell")
    except Exception:                                       # noqa: BLE001  no dataset
        return None
    if not rec or _key(rec.get("name")) != key or "mechanics" not in rec:
        return None
    return dict(rec["mechanics"], name=rec["name"], level=int(rec.get("level", 0)))


def known(caster) -> list:
    return [_key(s) for s in caster.extra.get("spells", [])]


def _match(caster, name: str) -> str:
    key = _key(name)
    spells = known(caster)
    if not spells or key in spells:
        return key
    starts = [s for s in spells if s.startswith(key)]
    if len(starts) == 1:
        return starts[0]
    raise ValueError(f"{caster.name} does not know {name!r}. Spells: "
                     f"{', '.join(s.title() for s in spells)}.")


def mechanics(caster, key: str) -> dict:
    """SRD mechanics merged with BUILTIN, cached on the caster (so the encounter
    file is self-contained and a later cast needs no SRD lookup)."""
    book = caster.extra.setdefault("spellbook", {})
    base = book.get(key)
    if base is None:
        base = _srd(key)
        if base is not None:
            book[key] = base
    extra = BUILTIN.get(key, {})
    if base is None and "name" not in extra:
        return {}
    out = dict(base or {})
    out.update(extra)
    out.setdefault("name", key.title())
    flags = set(out.get("flags", []))
    for flag, keys in _COVERED.items():
        if flag in flags and any(k in extra for k in keys):
            flags.discard(flag)
    out["flags"] = sorted(flags)
    return out


def _char_level_dice(table: dict, level: int) -> str:
    best = None
    for k in sorted(table, key=int):
        if int(k) <= level:
            best = table[k]
    return best or table[min(table, key=int)]


def _proficiency(level: int) -> int:
    return 2 + (max(1, level) - 1) // 4


def resolve(caster, name: str, level: int = None) -> dict:
    key = _match(caster, name)
    m = mechanics(caster, key)
    if not m:
        raise ValueError(f"No spell data for {name!r}: build the SRD "
                         "(python3 systems/dnd5e/build_srd.py --no-fvtt) or narrate it.")
    base = int(m.get("level", 0))
    if base == 0:
        if level not in (None, 0):
            raise ValueError(f"{m['name']} is a cantrip: it has no slot level.")
        slot = 0
    else:
        slot = base if level is None else int(level)
        if not base <= slot <= 9:
            raise ValueError(f"{m['name']} is level {base}: cast it with a slot of level {base} to 9.")
    char_level = int(caster.extra.get("level") or 1)
    dc = caster.extra.get("spell_dc")
    atk = caster.extra.get("spell_attack")
    mod = caster.extra.get("spell_mod")
    if mod is None and dc is not None:
        mod = int(dc) - 8 - _proficiency(char_level)
    spec = {"name": m["name"], "key": key, "level": base, "slot": slot,
            "casting": m.get("casting", "action"), "range": m.get("range"),
            "origin": m.get("origin", "point"), "concentration": bool(m.get("concentration")),
            "area": m.get("area"), "flags": list(m.get("flags", [])), "damage": [],
            "save": None, "attack": None, "on_fail": m.get("on_fail"),
            "fail_conditions": list(m.get("fail_conditions", [])), "repeat": m.get("repeat"),
            "reaction": m.get("reaction")}
    dmg = m.get("damage") or {}
    if dmg:
        table = dmg.get("slot") or {}
        dice = table.get(str(slot)) if table else None
        if not table and dmg.get("character"):
            dice = _char_level_dice(dmg["character"], char_level)
        if dice:
            spec["damage"] = [{"dice": dice, "type": dmg.get("type", "")}]
    if m.get("save"):
        if dc is None:
            raise ValueError(f"{caster.name} has no spell save DC on the sheet.")
        spec["save"] = {"ability": m["save"]["ability"], "dc": int(dc),
                        "on_success": m["save"].get("on_success", "none")}
    if m.get("reaction"):
        spec["mode"] = "reaction"
    elif m.get("darts"):
        spec["mode"] = "darts"
        spec["darts"] = int(m["darts"]) + max(0, slot - base)
        spec["dart"] = dict(m["dart"])
    elif m.get("attack"):
        if atk is None:
            raise ValueError(f"{caster.name} has no spell attack bonus on the sheet.")
        rng = int(m.get("range") or 5)
        spec["mode"] = "attack"
        spec["attack"] = {"name": m["name"], "type": m["attack"], "source": "spell",
                          "bonus": int(atk), "damage": spec["damage"], "flags": [],
                          "reach": 5 if m["attack"] == "melee" else None,
                          "range": [rng, rng] if m["attack"] == "ranged" else None}
    elif spec["save"]:
        spec["mode"] = "save"
    elif m.get("heal"):
        dice = (m["heal"].get("slot") or {}).get(str(slot))
        if not dice:
            spec["mode"] = "narrate"
        else:
            if "MOD" in dice:
                if mod is None:
                    raise ValueError(f"{caster.name} has no spellcasting modifier on the sheet.")
                dice = dice.replace("+MOD", f"{int(mod):+d}").replace("MOD", str(int(mod)))
            spec["mode"] = "heal"
            spec["heal"] = dice
    elif m.get("effect"):
        spec["mode"] = "effect"
        spec["effect"] = m["effect"]
    else:
        spec["mode"] = "narrate"
    blocking = {"range_unparsed", "area_placement", "damage_unparsed", "save_effect", "effect"}
    if spec["mode"] not in ("narrate", "reaction") and blocking & set(spec["flags"]):
        spec["mode"] = "narrate"
    if spec["mode"] == "save" and not (spec["damage"] or spec["fail_conditions"] or spec["on_fail"]):
        spec["mode"] = "narrate"                 # a save whose outcome the engine cannot apply
    if spec["mode"] == "narrate" and spec["range"] is None:
        spec["range"] = 0
    return spec
