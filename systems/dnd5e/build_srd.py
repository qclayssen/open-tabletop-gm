#!/usr/bin/env python3
"""
build_srd.py — build the bundled dnd5e_srd.json from two upstream sources

Sources:
  • 5e-bits/5e-srd-api packages/5e-database (MIT + OGL) — spells, equipment, magic items, conditions, monsters
  • foundryvtt/dnd5e     (MIT + CC-BY-4.0) — class features, racial traits (2024 SRD)

Output: systems/dnd5e/data/dnd5e_srd.json

Usage:
    python3 build_srd.py             # build/rebuild the dataset
    python3 build_srd.py --status    # show current dataset metadata
    python3 build_srd.py --no-fvtt   # skip FoundryVTT features (faster, spells/items only)
"""

import json
import os
import pathlib
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    print("PyYAML required for FoundryVTT data. Install: pip3 install pyyaml")
    print("Run with --no-fvtt to skip class features and build spells/items only.")
    yaml = None  # type: ignore

DATA_DIR  = str(pathlib.Path(__file__).parent / "data")
OUT_FILE  = os.path.join(DATA_DIR, "dnd5e_srd.json")

# The standalone 5e-bits/5e-database repo is archived. The data now lives in the
# 5e-srd-api monorepo under packages/5e-database, and only there gets updates.
#
# 5e-bits added a language directory (src/<ruleset>/<lang>/) — the files are
# NOT at src/2014/ any more, and every one of them 404s there. A fetch
# failure here used to be soft, so the old path produced an empty dataset
# and a build that still reported success.
RAW_5EBITS   = "https://raw.githubusercontent.com/5e-bits/5e-srd-api/main/packages/5e-database/src/2014/en"
RAW_FVTT     = "https://raw.githubusercontent.com/foundryvtt/dnd5e/master"
FVTT_TREE    = "https://api.github.com/repos/foundryvtt/dnd5e/git/trees/master?recursive=1"
BITS_COMMITS = "https://api.github.com/repos/5e-bits/5e-srd-api/commits?sha=main&path=packages/5e-database&per_page=1"
FVTT_COMMITS = "https://api.github.com/repos/foundryvtt/dnd5e/commits/master?per_page=1"

BITS_FILES = {
    "spells":      "5e-SRD-Spells.json",
    "equipment":   "5e-SRD-Equipment.json",
    "magic_items": "5e-SRD-Magic-Items.json",
    "conditions":  "5e-SRD-Conditions.json",
    "monsters":    "5e-SRD-Monsters.json",
}


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _fetch(url: str, as_json: bool = False):
    """Fetch URL, return parsed JSON or raw text. Returns None on error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dnd-skill-build/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        return json.loads(data) if as_json else data.decode("utf-8")
    except Exception as e:
        print(f"    ✗ {url}: {e}", file=sys.stderr)
        return None


def _fetch_json(url: str):
    return _fetch(url, as_json=True)


# ─── Text normalisation ───────────────────────────────────────────────────────

def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _strip_html(html: str) -> str:
    """Convert FoundryVTT HTML description to clean plain text."""
    if not html:
        return ""
    # @UUID[...]{label} → label
    html = re.sub(r"@UUID\[[^\]]*\]\{([^}]+)\}", r"\1", html)
    # [[lookup @scale.class.feature]] — resolved before _strip_html via _resolve_scale_tokens;
    # this fallback catches any that slip through (e.g. no scale_tables loaded)
    html = re.sub(r"\[\[lookup\s+@scale\.[^\]]+\]\]", "(scales with level)", html)
    # [[/r ...]] inline roll expressions → strip entirely
    html = re.sub(r"\[\[/r\s+[^\]]+\]\]", "", html)
    # Any remaining [[ ... ]] FoundryVTT tokens → strip
    html = re.sub(r"\[\[[^\]]*\]\]", "", html)
    # @Damage[...]{label} → label
    html = re.sub(r"@Damage\[[^\]]*\]\{([^}]+)\}", r"\1", html)
    # @Check[...]{label} → label
    html = re.sub(r"@Check\[[^\]]*\]\{([^}]+)\}", r"\1", html)
    # Any remaining @Token[...]{label} → label
    html = re.sub(r"@\w+\[[^\]]*\]\{([^}]+)\}", r"\1", html)
    # Any remaining bare @Token[...] → strip
    html = re.sub(r"@\w+\[[^\]]*\]", "", html)
    # &amp;Reference[Dash] → Dash
    html = re.sub(r"&amp;Reference\[([^\]]+)\]", r"\1", html)
    # &Reference[Dash] → Dash (in case already decoded)
    html = re.sub(r"&Reference\[([^\]]+)\]", r"\1", html)
    # List items
    html = re.sub(r"<li[^>]*>", "• ", html)
    html = re.sub(r"</li>", "\n", html)
    # Paragraphs/divs as line breaks
    html = re.sub(r"</p>|</div>|<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    # Table cells (crude: separate with spaces)
    html = re.sub(r"<td[^>]*>|<th[^>]*>", "  ", html, flags=re.IGNORECASE)
    html = re.sub(r"</tr>", "\n", html, flags=re.IGNORECASE)
    # Strip all remaining tags
    html = re.sub(r"<[^>]+>", "", html)
    # HTML entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    # Collapse whitespace
    lines = [ln.strip() for ln in html.splitlines()]
    text  = "\n".join(ln for ln in lines if ln)
    text  = re.sub(r"\n{3,}", "\n\n", text).strip()
    # Strip FoundryVTT-specific "Foundry Note" sections (everything from that header onward)
    text = re.sub(r"\n?Foundry Note\b.*", "", text, flags=re.DOTALL).strip()
    return text


def _fmt_scale_table(table: dict) -> str:
    """Format {level_str: value_str} into a compact range string.
    e.g. {"1":"1d6","3":"2d6",...} → "1d6 (lvl 1–2), 2d6 (lvl 3–4), ..., 10d6 (lvl 19–20)"
    """
    levels = sorted(table.keys(), key=lambda x: int(x))
    parts  = []
    for i, lvl in enumerate(levels):
        val     = table[lvl]
        lvl_int = int(lvl)
        if i + 1 < len(levels):
            end = int(levels[i + 1]) - 1
            parts.append(f"{val} (lvl {lvl_int}–{end})" if end > lvl_int else f"{val} (lvl {lvl_int})")
        else:
            parts.append(f"{val} (lvl {lvl_int}–20)" if lvl_int < 20 else f"{val} (lvl 20)")
    return ", ".join(parts)


def _resolve_scale_tokens(html: str, scale_tables: dict) -> str:
    """Replace [[lookup @scale.class.identifier]] with formatted progression strings.
    Called before _strip_html so that the real data is embedded in the description.
    If a table is not found, substitutes '(scales with level)' as fallback.
    """
    if "[[" not in html:
        return html

    def _replacer(m):
        inner = re.search(r'@scale\.(\w+)\.([^\]\s]+)', m.group(0))
        if not inner:
            return "(scales with level)"
        cls_name   = inner.group(1)
        identifier = inner.group(2)
        table = (scale_tables.get(cls_name) or {}).get(identifier)
        return _fmt_scale_table(table) if table else "(scales with level)"

    return re.sub(r'\[\[lookup\s+@scale\.[^\]]+\]\]', _replacer, html)


def _join_desc(desc) -> str:
    """Normalise 5e-bits desc field (list or string) to a single string."""
    if isinstance(desc, list):
        return "\n\n".join(str(d) for d in desc)
    return str(desc) if desc else ""


# ─── 5e-bits normalisers ──────────────────────────────────────────────────────

def _norm_spell(r: dict) -> dict:
    school = r.get("school", {})
    return {
        "name":         r.get("name", ""),
        "index":        r.get("index", _slugify(r.get("name", ""))),
        "description":  _join_desc(r.get("desc", [])),
        "higher_level": _join_desc(r.get("higher_level", [])),
        "level":        r.get("level", 0),
        "school":       school.get("name", school) if isinstance(school, dict) else str(school),
        "casting_time": r.get("casting_time", ""),
        "range":        r.get("range", ""),
        "components":   r.get("components", []),
        "material":     r.get("material", ""),
        "duration":     r.get("duration", ""),
        "concentration":r.get("concentration", False),
        "ritual":       r.get("ritual", False),
        "classes":      [c.get("name", c) if isinstance(c, dict) else str(c)
                         for c in r.get("classes", [])],
        "mechanics":    _spell_mechanics(r),
    }


# ─── Structured spell mechanics ───────────────────────────────────────────────
#
# What the tactical engine needs to cast a spell on the grid, read from
# upstream's fields (never from the prose, except a line's width, which only
# the text gives). `flags` empty means the engine can run the spell's numbers
# without GM judgment; effects beyond damage and healing (conditions, walls,
# summons) are always the GM's.
#
#   {"casting": action|bonus|reaction|other, "range": feet, "origin": self|touch|point,
#    "attack": melee|ranged, "save": {"ability", "on_success": half|none|other},
#    "damage": {"type", "slot": {level: dice}} | {"type", "character": {level: dice}},
#    "heal": {"slot": {level: dice}}, "area": {"shape", "size", "width"},
#    "concentration": bool, "flags": [...]}

_CASTING = {"1 action": "action", "1 bonus action": "bonus", "1 reaction": "reaction"}
_LINE_WIDTH = re.compile(r"(?:(\d+) feet wide|(\d+)-foot-wide)")


def _spell_mechanics(r: dict) -> dict:
    flags = []
    rng = str(r.get("range", "")).strip()
    out = {"casting": _CASTING.get(str(r.get("casting_time", "")).strip(), "other"),
           "concentration": bool(r.get("concentration"))}
    m = re.match(r"^(\d+) feet$", rng)
    if rng.lower().startswith("self"):
        out["range"], out["origin"] = 0, "self"
    elif rng.lower() == "touch":
        out["range"], out["origin"] = 5, "touch"
    elif m:
        out["range"], out["origin"] = int(m.group(1)), "point"
    else:
        flags.append("range_unparsed")
    if r.get("attack_type") in ("melee", "ranged"):
        out["attack"] = r["attack_type"]
    dc = r.get("dc")
    if isinstance(dc, dict):
        ability = (dc.get("dc_type") or {}).get("index", "")
        success = dc.get("dc_success", "none")
        out["save"] = {"ability": ability[:3], "on_success": success}
        if success not in ("half", "none"):
            flags.append("save_effect")
    damage = r.get("damage")
    if isinstance(damage, list):                     # a few records carry a list
        damage = damage[0] if len(damage) == 1 else None
        if damage is None:
            flags.append("damage_unparsed")
    if isinstance(damage, dict):
        dtype = (damage.get("damage_type") or {}).get("index", "")
        if damage.get("damage_at_slot_level"):
            out["damage"] = {"type": dtype, "slot": {str(k): str(v).replace(" ", "")
                             for k, v in damage["damage_at_slot_level"].items()}}
        elif damage.get("damage_at_character_level"):
            out["damage"] = {"type": dtype, "character": {str(k): str(v).replace(" ", "")
                             for k, v in damage["damage_at_character_level"].items()}}
        else:
            flags.append("damage_unparsed")
        if "damage" in out and any(not re.fullmatch(r"\d+d\d+([+-]\d+)?|\d+", v)
                                   for v in (out["damage"].get("slot")
                                             or out["damage"].get("character")).values()):
            flags.append("damage_unparsed")          # "1d6 + MOD" and the like
    heal = r.get("heal_at_slot_level")
    if isinstance(heal, dict):
        out["heal"] = {"slot": {str(k): str(v).replace(" ", "") for k, v in heal.items()}}
    area = r.get("area_of_effect")
    if isinstance(area, dict) and area.get("type") in ("sphere", "cube", "cone", "line", "cylinder"):
        out["area"] = {"shape": area["type"], "size": int(area.get("size") or 0)}
        if area["type"] == "line":
            w = _LINE_WIDTH.search(_join_desc(r.get("desc", [])))
            out["area"]["width"] = int(w.group(1) or w.group(2)) if w else 5
            if not w:
                flags.append("width_assumed")
            if out.get("origin") != "self":
                flags.append("area_placement")       # walls: shaped by the caster, not a blast
    if not any(k in out for k in ("attack", "save", "damage", "heal")):
        flags.append("effect")                       # nothing the engine can resolve by itself
    out["flags"] = sorted(set(flags))
    return out


def _norm_equipment(r: dict) -> dict:
    cat  = r.get("equipment_category", {})
    cost = r.get("cost", {})
    dmg  = r.get("damage", {})
    dmg2 = r.get("two_handed_damage", {})
    rng  = r.get("range", {})
    trng = r.get("throw_range", {})
    ac   = r.get("armor_class", {})
    props = [p.get("name", p) if isinstance(p, dict) else str(p)
             for p in r.get("properties", [])]
    return {
        "name":          r.get("name", ""),
        "index":         r.get("index", _slugify(r.get("name", ""))),
        "description":   _join_desc(r.get("desc", [])),
        "category":      cat.get("name", "") if isinstance(cat, dict) else str(cat),
        "cost":          f"{cost.get('quantity','?')} {cost.get('unit','?')}"
                         if isinstance(cost, dict) else "",
        "weight":        r.get("weight"),
        "damage":        f"{dmg.get('damage_dice','')} {dmg.get('damage_type',{}).get('name','')}"
                         .strip() if dmg else "",
        "damage_2h":     f"{dmg2.get('damage_dice','')} {dmg2.get('damage_type',{}).get('name','')}"
                         .strip() if dmg2 else "",
        "ac":            f"AC {ac.get('base','')} + DEX" if ac else "",
        "properties":    props,
        "range":         f"{rng.get('normal','?')}/{rng.get('long','?')} ft"
                         if rng and rng.get("normal") else "",
        "throw_range":   f"{trng.get('normal','?')}/{trng.get('long','?')} ft" if trng else "",
        "stealth_disadv":r.get("stealth_disadvantage", False),
        "str_minimum":   r.get("str_minimum"),
    }


def _norm_magic_item(r: dict) -> dict:
    rar = r.get("rarity", {})
    cat = r.get("equipment_category", {})
    return {
        "name":        r.get("name", ""),
        "index":       r.get("index", _slugify(r.get("name", ""))),
        "description": _join_desc(r.get("desc", [])),
        "rarity":      rar.get("name", rar) if isinstance(rar, dict) else str(rar),
        "category":    cat.get("name", "") if isinstance(cat, dict) else str(cat),
        "attunement":  "attunement" in _join_desc(r.get("desc", [])).lower(),
    }


def _norm_condition(r: dict) -> dict:
    return {
        "name":        r.get("name", ""),
        "index":       r.get("index", _slugify(r.get("name", ""))),
        "description": _join_desc(r.get("desc", [])),
    }


def _norm_monster(r: dict) -> dict:
    ac_list = r.get("armor_class", [])
    ac_val  = (ac_list[0].get("value") if isinstance(ac_list, list) and ac_list
               and isinstance(ac_list[0], dict) else
               ac_list[0] if isinstance(ac_list, list) and ac_list else
               ac_list if isinstance(ac_list, (int, float)) else "?")
    speed   = r.get("speed", {})
    speed_s = ", ".join(f"{k} {v}" for k, v in speed.items() if v) if isinstance(speed, dict) else ""

    # Defenses. Upstream gives damage_* as lists of plain strings and
    # condition_immunities as a list of {index,name,url} records, so they need
    # different flattening. Dropping these was not a cosmetic omission: halving
    # or zeroing damage changes what a fight IS, and a GM with no record to
    # consult will apply a half-remembered resistance inconsistently.
    def _flat(key: str) -> str:
        vals = r.get(key) or []
        if not isinstance(vals, list):
            return str(vals or "")
        out = []
        for v in vals:
            if isinstance(v, dict):
                v = v.get("name", "")
            if v:
                out.append(str(v))
        return ", ".join(out)
    # Flatten special abilities + actions into description
    parts = []
    for sa in r.get("special_abilities", []):
        parts.append(f"{sa.get('name','')}: {sa.get('desc','')}")
    for a in r.get("actions", []):
        parts.append(f"Action — {a.get('name','')}: {a.get('desc','')}")
    for a in r.get("legendary_actions", []):
        parts.append(f"Legendary — {a.get('name','')}: {a.get('desc','')}")
    return {
        "name":  r.get("name", ""),
        "index": r.get("index", _slugify(r.get("name", ""))),
        "description": "\n\n".join(parts),
        "cr":    r.get("challenge_rating", "?"),
        "xp":    r.get("xp", "?"),
        "size":  r.get("size", ""),
        "type":  r.get("type", ""),
        "hp":    r.get("hit_points", "?"),
        "hp_dice": r.get("hit_dice", ""),
        "ac":    ac_val,
        "speed": speed_s,
        "str":   r.get("strength", 10),
        "dex":   r.get("dexterity", 10),
        "con":   r.get("constitution", 10),
        "int":   r.get("intelligence", 10),
        "wis":   r.get("wisdom", 10),
        "cha":   r.get("charisma", 10),
        "alignment": r.get("alignment", ""),
        "languages": r.get("languages", ""),
        "resistances":   _flat("damage_resistances"),
        "immunities":    _flat("damage_immunities"),
        "vulnerabilities": _flat("damage_vulnerabilities"),
        "condition_immunities": _flat("condition_immunities"),
        "actions": [_norm_monster_action(a) for a in r.get("actions", [])
                    if isinstance(a, dict)],
        **_proficiencies(r),
    }


def _proficiencies(r: dict) -> dict:
    """Save proficiencies ("saving-throw-dex": 6) and skills ("skill-stealth": 3)
    as {"saves": {"dex": 6}, "skills": {"stealth": 3}}, plus passive Perception
    from the senses block. Abilities without a proficiency are left out: the
    engine falls back to the ability modifier."""
    saves, skills = {}, {}
    for p in r.get("proficiencies") or []:
        idx = ((p or {}).get("proficiency") or {}).get("index", "")
        if not isinstance(p.get("value"), int):
            continue
        if idx.startswith("saving-throw-"):
            saves[idx[len("saving-throw-"):][:3]] = p["value"]
        elif idx.startswith("skill-"):
            skills[idx[len("skill-"):]] = p["value"]
    out = {"saves": saves, "skills": skills}
    senses = r.get("senses") or {}
    if isinstance(senses, dict) and isinstance(senses.get("passive_perception"), int):
        out["passive_perception"] = senses["passive_perception"]
    return out


# ─── Structured monster actions ───────────────────────────────────────────────
#
# The tactical combat engine needs numbers, not prose: attack bonus, reach,
# range, damage dice, save DC. Upstream carries most of these as fields; reach
# and range only exist in the text, in a fixed SRD phrasing. Anything that does
# not fit a known shape is kept as raw text with a flag rather than guessed at.
# `flags` empty means the engine can run the action without GM judgment.
#
#   {"name", "kind": attack|save|multiattack|other, "flags": [...],
#    "attack": {"type": melee|ranged|melee_or_ranged, "source": weapon|spell,
#               "bonus", "reach", "range": [normal, long]},
#    "damage": [{"dice", "type"}], "damage_choice": {"choose", "options"},
#    "dc": {"ability", "value", "on_success"}, "area": {"shape", "size", "width"},
#    "multiattack": [[{"action", "count", "type"}], ...],   # one list per option
#    "usage": {...}, "rider": "<text after the damage>", "raw": "<desc>"}

_ATTACK_HDR = re.compile(r"^(Melee or Ranged|Melee|Ranged) (Weapon|Spell) Attack:")
_REACH      = re.compile(r"\breach (\d+) ft\.")
_RANGE      = re.compile(r"\brange (\d+)(?:/(\d+))? ft\.")
_HIT_DAMAGE = re.compile(
    r"^\d+(?: \([^)]*\))? [a-z]+ damage"
    r"(?: plus \d+(?: \([^)]*\))? [a-z]+ damage)*\.?")
_LEAD_PART  = re.compile(r"(\d+)(?: \(([^)]*)\))? ([a-z]+) damage")
_AREA       = re.compile(
    r"(\d+)-foot(?:-radius)? (cone|line|cube|sphere)(?: that is (\d+) feet wide)?")


def _damage_part(d: dict):
    if not isinstance(d, dict) or "damage_dice" not in d:
        return None
    return {"dice": str(d["damage_dice"]).replace(" ", ""),
            "type": (d.get("damage_type") or {}).get("index", "")}


# Common rider shapes the engine applies itself. Anything else in a rider stays
# text for the GM ("rider_rest"). Size conditions ("a Medium or smaller
# creature") are never structured: tokens carry no size yet.
_ABILITY = {"strength": "str", "dexterity": "dex", "constitution": "con",
            "intelligence": "int", "wisdom": "wis", "charisma": "cha"}
_SAVE_HEAD = (r"(?:if the target is a creature, )?(?:the target|it|each creature in that area) "
              r"must (?:succeed on|make) a DC (\d+) (\w+) saving throw")
_RIDER_SHAPES = [
    ("grapple", re.compile(r"(?:and |if the target is a creature, )?(?:the target|it) is grappled \(escape DC (\d+)\)", re.I)),
    ("restrained", re.compile(r"until this grapple ends, the (?:target|creature) is restrained", re.I)),
    ("save_damage", re.compile(r"(?:and )?" + _SAVE_HEAD + r"(?:, taking| or take) \d+ \(([^)]*)\) ([a-z]+) damage"
                               r"(?: on a failed save)?(, or half as much damage on a successful one)?", re.I)),
    ("save_condition", re.compile(r"(?:and )?" + _SAVE_HEAD + r" or (?:be|become) (knocked prone|poisoned|frightened|"
                                  r"blinded|deafened|paralyzed|restrained|stunned|charmed|incapacitated)"
                                  r"(?: for (\d+ (?:round|minute|hour)s?|\d+ (?:round|minute|hour)))?", re.I)),
    ("repeat", re.compile(r"(?:the (?:\w+ )?target|the creature|a creature) can repeat the saving throw "
                          r"at the end of each of its turns, "
                          r"ending the effect on itself on a success", re.I)),
]


def _rider_effects(rider: str) -> tuple:
    """(effects, leftover text) for a rider. Effects:
    {"kind": "grapple", "escape_dc", "restrained"} and
    {"kind": "save", "ability", "dc", "condition" | "damage", "on_success", "duration", "repeat"}."""
    effects, leftover = [], []
    for sentence in re.split(r"(?<=\.)\s+", rider.strip()):
        rest = sentence
        plain = re.sub(r"\bif the target is a creature,", "", sentence, flags=re.I)
        if re.search(r"\b(if|unless)\b", plain, re.I):
            leftover.append(sentence)              # "other than an elf", "isn't already grappling"
            continue
        for kind, rx in _RIDER_SHAPES:
            m = rx.search(rest)
            if not m:
                continue
            if kind == "grapple":
                effects.append({"kind": "grapple", "escape_dc": int(m.group(1)), "restrained": False})
            elif kind == "restrained":
                grapple = next((e for e in effects if e["kind"] == "grapple"), None)
                if grapple is None:
                    continue
                grapple["restrained"] = True
            elif kind == "save_damage":
                dice = m.group(3).replace(" ", "")
                if not re.fullmatch(r"\d+d\d+([+-]\d+)?", dice) or m.group(2).lower() not in _ABILITY:
                    continue
                effects.append({"kind": "save", "ability": _ABILITY[m.group(2).lower()],
                                "dc": int(m.group(1)),
                                "damage": [{"dice": dice, "type": m.group(4).lower()}],
                                "on_success": "half" if m.group(5) else "none"})
            elif kind == "save_condition":
                if m.group(2).lower() not in _ABILITY:
                    continue
                cond = m.group(3).lower().replace("knocked prone", "prone")
                eff = {"kind": "save", "ability": _ABILITY[m.group(2).lower()],
                       "dc": int(m.group(1)), "condition": cond}
                if m.group(4):
                    eff["duration"] = m.group(4)
                effects.append(eff)
            elif kind == "repeat":
                last = next((e for e in reversed(effects) if e.get("condition")), None)
                if last is None:
                    continue
                last["repeat"] = "end"
            rest = rest[:m.start()] + rest[m.end():]
        rest = re.sub(r"^[\s,.]*(?:and|or)?[\s,.]*", "", rest).strip(" ,.")
        if re.search(r"[a-z]{3}", rest, re.I):
            leftover.append(rest[0].upper() + rest[1:] + ".")
    return effects, " ".join(leftover)


def _norm_monster_action(a: dict) -> dict:
    desc  = (a.get("desc") or "").strip()
    out   = {"name": a.get("name", ""), "kind": "other"}
    flags = []

    if isinstance(a.get("usage"), dict):
        out["usage"] = a["usage"]

    damage, choices = [], []
    for d in a.get("damage") or []:
        part = _damage_part(d)
        if part:
            damage.append(part)
        elif isinstance(d, dict) and d.get("choose"):
            opts = [_damage_part(o) for o in (d.get("from") or {}).get("options", [])]
            if opts and all(opts):
                choices.append({"choose": d["choose"], "options": opts})
            else:
                flags.append("damage_unparsed")
        else:
            flags.append("damage_unparsed")
    if damage:
        out["damage"] = damage
    if choices:
        out["damage_choice"] = choices[0] if len(choices) == 1 else choices
        flags.append("damage_choice")

    if a.get("multiattack_type"):
        out["kind"] = "multiattack"
        if a["multiattack_type"] == "actions":
            sets = [a.get("actions") or []]
        else:
            opts = ((a.get("action_options") or {}).get("from") or {}).get("options", [])
            sets = [o.get("items", []) if o.get("option_type") == "multiple" else [o]
                    for o in opts]
        parsed = [[{"action": i.get("action_name", ""), "count": i.get("count", 1),
                    "type": i.get("type", "")} for i in s] for s in sets]
        items = [i for s in parsed for i in s]
        if (parsed and all(parsed) and all(i["action"] for i in items)
                and all(str(i["count"]).isdigit() for i in items)):   # hydra: "Number of Heads"
            for i in items:
                i["count"] = int(i["count"])
            out["multiattack"] = parsed
        else:
            flags.append("unparsed")

    elif "attack_bonus" in a and _ATTACK_HDR.match(desc):
        hdr   = _ATTACK_HDR.match(desc)
        atype = {"Melee": "melee", "Ranged": "ranged",
                 "Melee or Ranged": "melee_or_ranged"}[hdr.group(1)]
        out["kind"] = "attack"
        attack = {"type": atype, "source": hdr.group(2).lower(),
                  "bonus": int(a["attack_bonus"])}
        head, _, hit = desc.partition("Hit:")
        reach, rng = _REACH.search(head), _RANGE.search(head)
        if atype != "ranged":
            if reach:
                attack["reach"] = int(reach.group(1))
            else:
                flags.append("reach_unparsed")
        if atype != "melee":
            if rng:
                attack["range"] = [int(rng.group(1)), int(rng.group(2) or rng.group(1))]
            else:
                flags.append("range_unparsed")
        if "(+" in head.split(",")[0]:
            flags.append("conditional_bonus")          # e.g. "+4 to hit with shillelagh"
        out["attack"] = attack
        if not damage and not choices:
            flags.append("damage_unparsed")
        hit  = hit.strip()
        lead = _HIT_DAMAGE.match(hit)
        # Upstream's damage list also carries damage that only a rider deals
        # (a bite's poison on a failed save). Only the leading "Hit:" phrase
        # lands on every hit; the rest belongs to the rider.
        on_hit = {(dice.replace(" ", "") or flat, dtype) for flat, dice, dtype in
                  _LEAD_PART.findall(lead.group(0) if lead else "")}
        if damage and lead:
            rider_dmg = [d for d in damage if (d["dice"], d["type"]) not in on_hit]
            if rider_dmg:
                out["damage"] = [d for d in damage if d not in rider_dmg]
                out["rider_damage"] = rider_dmg
                if not out["damage"]:
                    del out["damage"]
                    flags.append("damage_unparsed")
        rest = hit[lead.end():].strip(" ,.") if lead else hit.strip(" ,.")
        if rest:
            out["rider"] = rest
            flags.append("rider")
            effects, leftover = _rider_effects(rest)
            if effects:
                out["rider_effects"] = effects
                if leftover:
                    out["rider_rest"] = leftover
        if a.get("dc"):
            flags.append("rider")                      # save attached to an attack

    elif isinstance(a.get("dc"), dict):
        dc = a["dc"]
        out["kind"] = "save"
        out["dc"] = {"ability": (dc.get("dc_type") or {}).get("index", ""),
                     "value": dc.get("dc_value"),
                     "on_success": dc.get("success_type", "none")}
        # Upstream's success_type contradicts its own text on a few records
        # (adult red dragon Fire Breath says "none"). Refuse to pick a side.
        text_half = bool(re.search(r"half as much damage on a successful", desc))
        if text_half != (out["dc"]["on_success"] == "half"):
            out["dc"]["on_success"] = None
            flags.append("success_conflict")
        # What happens besides the damage: a condition on a failed save is
        # structured like an attack rider; anything else is text for the GM.
        after = re.split(r"half as much damage on a successful one\.|damage on a failed save\.", desc, 1)
        tail = after[1].strip() if len(after) > 1 else ""
        if not out.get("damage") or tail:
            effects, leftover = _rider_effects(desc if not out.get("damage") else tail)
            effects = [e for e in effects if e.get("condition")]
            if effects:
                out["rider_effects"] = effects
            rest = leftover if not out.get("damage") else (leftover if effects else tail)
            if out.get("damage") and rest:
                out["rider"] = rest
                flags.append("rider")
        area = _AREA.search(desc)
        if area:
            out["area"] = {"shape": area.group(2), "size": int(area.group(1))}
            if area.group(3):
                out["area"]["width"] = int(area.group(3))
        else:
            flags.append("targeting")

    else:
        flags.append("unparsed")

    out["flags"] = sorted(set(flags))
    if out["flags"]:
        out["raw"] = desc
    return out


# ─── FoundryVTT normaliser ────────────────────────────────────────────────────

def _parse_scale_tables(class_doc: dict) -> dict:
    """Extract ScaleValue advancements from a class YAML document.
    Returns {identifier: {level_str: value_str}}
    Indexed by both config.identifier and _slugify(title) so either lookup hits.
    """
    tables = {}
    system = class_doc.get("system", {}) if "system" in class_doc else class_doc
    advancement = system.get("advancement") or []
    if isinstance(advancement, dict):  # newer Foundry data keys advancements by id
        advancement = advancement.values()
    for adv in advancement:
        if not isinstance(adv, dict) or adv.get("type") != "ScaleValue":
            continue
        title  = adv.get("title", "").strip()
        config = adv.get("configuration", {}) or {}
        scale  = config.get("scale", {})
        vtype  = config.get("type", "dice")
        ident  = (config.get("identifier") or "").strip() or _slugify(title)
        if not scale or not title:
            continue

        table = {}
        for lvl, val in scale.items():
            if not isinstance(val, dict):
                continue
            if vtype == "dice":
                n, f = val.get("number", 0), val.get("faces", 0)
                if n and f:
                    table[str(lvl)] = f"{n}d{f}"
            elif vtype == "number":
                v = val.get("value")
                if v is not None:
                    table[str(lvl)] = f"+{v}" if isinstance(v, (int, float)) and v > 0 else str(v)
            else:
                v = val.get("value") or val.get("number")
                if v is not None:
                    table[str(lvl)] = str(v)

        if not table:
            continue
        tables[ident] = table
        slug = _slugify(title)
        if slug != ident:
            tables[slug] = table

    return tables


def _norm_feature(doc: dict, path: str, scale_tables=None):
    name = doc.get("name", "").strip()
    if not name:
        return None
    system    = doc.get("system", {})
    desc_html = system.get("description", {}).get("value", "") if isinstance(system.get("description"), dict) else ""
    prereq    = system.get("prerequisites", {}) or {}
    feat_type = system.get("type", {}).get("value", "class") if isinstance(system.get("type"), dict) else "class"

    # Derive class from path: packs/_source/classes24/<class>/class-features/...
    # or races: packs/_source/races/<race>/<variant>-features/...
    parts      = path.replace("\\", "/").split("/")
    class_name = None
    if "classes24" in parts:
        idx        = parts.index("classes24")
        class_name = parts[idx + 1] if idx + 1 < len(parts) else None
    elif "races" in parts:
        feat_type = "race"

    # Resolve [[lookup @scale.class.identifier]] tokens before HTML stripping
    desc_html = _resolve_scale_tokens(desc_html, scale_tables or {})

    return {
        "name":        name,
        "index":       _slugify(name),
        "description": _strip_html(desc_html),
        "class":       class_name,
        "level_req":   prereq.get("level"),
        "type":        feat_type,
    }


# ─── Fetch 5e-bits datasets ───────────────────────────────────────────────────

class SourceUnavailable(RuntimeError):
    """A configured upstream source could not be fetched.

    Distinct from "fetched, and it was empty". Conflating the two is what let
    an upstream path change write an empty dataset over a good one and exit 0.
    """


def _load_bits_records(filename: str) -> list:
    body = _fetch(f"{RAW_5EBITS}/{filename}")
    if body is None:
        raise SourceUnavailable(f"{RAW_5EBITS}/{filename}")
    raw = json.loads(body or "null")
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("results", list(raw.values())[0] if raw else [])
    return []


def _build_5ebits() -> dict:
    categories = {}
    for key, filename in BITS_FILES.items():
        print(f"  5e-bits  {key} …", end="", flush=True)
        try:
            records = _load_bits_records(filename)
        except SourceUnavailable as e:
            # Exit rather than continue with []. The old behaviour was to carry
            # on, and the result was a dataset that looked built and held
            # nothing.
            sys.exit(
                f"\n✗ SRD source unavailable: {e}\n"
                "  Nothing was written. If this is a 404 rather than a network\n"
                "  failure, the upstream layout has changed and RAW_5EBITS needs\n"
                "  updating."
            )
        NORM = {
            "spells":      _norm_spell,
            "equipment":   _norm_equipment,
            "magic_items": _norm_magic_item,
            "conditions":  _norm_condition,
            "monsters":    _norm_monster,
        }
        normed = [NORM[key](r) for r in records if isinstance(r, dict)]
        normed = [r for r in normed if r.get("name")]
        categories[key] = normed
        print(f" {len(normed)} records")
    return categories


# ─── Fetch FoundryVTT features ────────────────────────────────────────────────

def _build_fvtt():
    if yaml is None:
        print("  foundryvtt  skipped (PyYAML not installed)")
        return [], ""

    print("  foundryvtt  fetching repo tree …", end="", flush=True)
    data = _fetch_json(FVTT_TREE)
    if not data:
        print(" failed")
        return [], ""
    tree = data.get("tree", [])
    sha  = data.get("sha", "")

    # Partition tree into feature YAMLs and class-level YAMLs (scale tables)
    feature_paths  = []
    class_yml_paths = []
    for t in tree:
        p = t["path"]
        if not p.endswith(".yml") or os.path.basename(p).startswith("_"):
            continue
        if p.startswith("packs/_source/classes24/"):
            depth = p.count("/")
            if "/class-features/" in p:
                # e.g. packs/_source/classes24/rogue/class-features/SneakAttack.yml (5 slashes)
                feature_paths.append(p)
            elif depth == 4:
                # e.g. packs/_source/classes24/rogue/Rogue.yml — class document itself (4 slashes)
                class_yml_paths.append(p)
        elif p.startswith("packs/_source/races/") and re.search(r"/-?\w+-features/", p):
            feature_paths.append(p)

    print(f" {len(feature_paths)} feature files, {len(class_yml_paths)} class files")

    # Fetch class YAMLs and extract scale tables
    # scale_tables: {class_name: {identifier: {level_str: value_str}}}
    scale_tables = {}
    for path in class_yml_paths:
        raw = _fetch(f"{RAW_FVTT}/{path}")
        if not raw:
            continue
        try:
            doc = yaml.safe_load(raw)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        parts = path.replace("\\", "/").split("/")
        if "classes24" not in parts:
            continue
        idx        = parts.index("classes24")
        class_name = parts[idx + 1] if idx + 1 < len(parts) else None
        if not class_name:
            continue
        tables = _parse_scale_tables(doc)
        if tables:
            scale_tables[class_name] = tables

    if scale_tables:
        resolved = sum(len(v) for v in scale_tables.values())
        print(f"  foundryvtt  {len(scale_tables)} classes, {resolved} scale tables loaded")

    # Fetch and normalise feature files
    features = []
    failed   = 0
    for i, path in enumerate(feature_paths, 1):
        raw = _fetch(f"{RAW_FVTT}/{path}")
        if raw is None:
            failed += 1
            continue
        try:
            doc = yaml.safe_load(raw)
        except Exception:
            failed += 1
            continue
        if not isinstance(doc, dict):
            continue
        feat = _norm_feature(doc, path, scale_tables)
        if feat and feat["description"]:
            features.append(feat)
        if i % 50 == 0:
            time.sleep(0.5)
            print(f"    … {i}/{len(feature_paths)}")

    print(f"  foundryvtt  {len(features)} features  ({failed} failed/empty)")
    return features, sha


# ─── Latest commit SHAs (for sync_srd.py) ────────────────────────────────────

def _latest_sha(url: str) -> str:
    data = _fetch_json(url)
    if data and isinstance(data, list) and data:
        return data[0].get("sha", "")
    return ""


# ─── Main ─────────────────────────────────────────────────────────────────────

def cmd_status() -> None:
    if not os.path.exists(OUT_FILE):
        print(f"Dataset not built. Run build_srd.py to create it.")
        return
    with open(OUT_FILE, encoding="utf-8") as f:
        data = json.load(f)
    meta   = data.get("_meta", {})
    counts = meta.get("record_counts", {})
    sources = meta.get("sources", {})
    print(f"Dataset:    {OUT_FILE}")
    print(f"Built at:   {meta.get('built_at','?')}")
    print()
    for cat, n in counts.items():
        print(f"  {cat:<12}  {n} records")
    print()
    for src, info in sources.items():
        print(f"  {src}:  {info.get('fetched_at','?')}  sha={info.get('sha','?')[:12]}…")


def cmd_build(skip_fvtt: bool = False) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("── 5e-bits/5e-srd-api (packages/5e-database) ──────────────────")
    bits_sha = _latest_sha(BITS_COMMITS)
    categories = _build_5ebits()

    print()
    print("── foundryvtt/dnd5e ────────────────────────────────────────────")
    fvtt_sha = _latest_sha(FVTT_COMMITS)
    if skip_fvtt:
        print("  skipped (--no-fvtt)")
        features = []
    else:
        features, _ = _build_fvtt()
    categories["features"] = features

    counts = {k: len(v) for k, v in categories.items()}
    total  = sum(counts.values())

    dataset = {
        "_meta": {
            "built_at":      now,
            "total_records": total,
            "record_counts": counts,
            "sources": {
                "5e-bits": {
                    "repo":       "5e-bits/5e-srd-api",
                    "path":       "packages/5e-database",
                    "branch":     "main",
                    "sha":        bits_sha,
                    "fetched_at": now,
                },
                "foundryvtt": {
                    "repo":       "foundryvtt/dnd5e",
                    "branch":     "master",
                    "sha":        fvtt_sha,
                    "fetched_at": now,
                },
            },
        },
        **categories,
    }

    # A category that fetched successfully and is empty is a real answer. Every
    # category empty means the sources moved or the network is gone, and
    # writing that over a good dataset is how a working install silently loses
    # its SRD. Refuse, loudly, and leave whatever is on disk alone.
    empty = [k for k, n in counts.items() if n == 0]
    if len(empty) == len(counts):
        sys.exit(
            "✗ every category came back empty — refusing to overwrite "
            f"{OUT_FILE}.\n"
            "  Nothing was written. Check the source URLs above for 404s."
        )
    if empty:
        print(f"  ! empty categories: {', '.join(empty)}", file=sys.stderr)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dataset, f, separators=(",", ":"))  # compact

    size_kb = os.path.getsize(OUT_FILE) // 1024
    print()
    print(f"── Complete ────────────────────────────────────────────────────")
    print(f"  {total} records  →  {OUT_FILE}  ({size_kb} KB)")
    for cat, n in counts.items():
        print(f"    {cat:<12}  {n}")


def main() -> None:
    args = sys.argv[1:]
    if "--status" in args:
        cmd_status()
    else:
        cmd_build(skip_fvtt="--no-fvtt" in args)


if __name__ == "__main__":
    main()
