#!/usr/bin/env python3
"""
build_srd.py — build the bundled dnd5e_srd.json from two upstream sources

Sources:
  • 5e-bits/5e-srd-api packages/5e-database (MIT + OGL) — spells, equipment, magic items, conditions, monsters
  • foundryvtt/dnd5e     (MIT + CC-BY-4.0) : class features, racial traits

2014 rules only, deliberately. The engine adjudicates 2014 (tactics_rules.py
cites the 2014 PHB and SRD 5.1), and the Foundry repo ships both editions side
by side: packs/_source/classes + classfeatures are 2014, classes24 + spells24 +
origins24 are 2024. Reading the 2024 packs here produced a dataset that was 2014
spells and monsters bolted to 2024 class features, so a lookup could answer a
2014 rules question with 2024 text. The packs below are named explicitly so the
edition is a one-line decision rather than a guess inside path matching.

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

# The 2014 packs in foundryvtt/dnd5e, named once. The 2024 equivalents are
# classes24, spells24, origins24, feats24 and equipment24; the two live side by
# side in the same repo, and the two editions nest their features differently:
#
#   2014  packs/_source/classfeatures/<class>/<class>-features/<feature>.yml
#   2024  packs/_source/classes24/<class>/class-features/<feature>.yml
#
# so the edition changes both which packs are read and how the class is pulled
# out of the path. Races are 2014-shaped in both cases (packs/_source/races).
FVTT_CLASS_PACK     = "classes"          # one YAML per class, flat
FVTT_CLASS_FEATURES = "classfeatures"    # <class>/<class>-features/<feature>.yml
FVTT_RACES          = "races"            # <race>/<race>-features/<feature>.yml

# The edition this build produces, written into `_meta` so the dataset says what
# it is instead of the reader having to infer it.
#
# It used to say nothing. `RAW_5EBITS` points at `/src/2014/en/` and the three
# packs above are the 2014 ones, but a consumer reading `dnd5e_srd.json` had no
# way to check any of that: `tests/test_srd_sources.py` proves the constants are
# right at the SOURCE, and a 2024 record served from a 2014 path would still
# pass every test in it. A dataset that does not record its edition cannot be
# refused for carrying the wrong one, which is the failure this constant exists
# to prevent.
#
# One value, named once, and asserted rather than re-derived at each use site.
EDITION = "2014"

BITS_FILES = {
    "spells":      "5e-SRD-Spells.json",
    "equipment":   "5e-SRD-Equipment.json",
    "magic_items": "5e-SRD-Magic-Items.json",
    "conditions":  "5e-SRD-Conditions.json",
    "monsters":    "5e-SRD-Monsters.json",
}


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _auth_headers() -> dict:
    """Headers for every fetch, including a bearer token when one is offered.

    `api.github.com` allows 60 requests/hour per IP address for an UNAUTHENTICATED
    caller, and the shared GitHub-hosted runner pool is exactly that case: several
    jobs share an egress IP, so a build can be refused with HTTP 403 part-way
    through. That is not hypothetical -- it is what happened on the first CI run
    that built this dataset (2026-10-04): the raw.githubusercontent.com fetches for
    spells, equipment, magic items, conditions and monsters all succeeded, every
    api.github.com call was rate-limited, and the build still exited 0 having
    written a dataset with ZERO class features. `cmd_build` only warns about a
    partly-empty dataset, so that failure is silent unless something checks.

    Authenticated, the same endpoint allows 5000/hour. So the token is used when
    one is in the environment and omitted when none is: a developer running
    `build_srd.py` locally, or `gh` supplying `GH_TOKEN` itself, keeps working
    unchanged, and a token is never required to build.

    Only `api.github.com` needs it, but sending it to `raw.githubusercontent.com`
    as well is harmless and keeps one header set rather than a per-host branch.
    """
    headers = {"User-Agent": "dnd-skill-build/1.0"}
    token = os.environ.get("SRD_BUILD_TOKEN") or os.environ.get("GITHUB_TOKEN") \
        or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def _fetch(url: str, as_json: bool = False):
    """Fetch URL, return parsed JSON or raw text. Returns None on error."""
    try:
        req = urllib.request.Request(url, headers=_auth_headers())
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
#    "damage": {"type", "slot": {level: dice}} | {"type", "character": {level: dice}}
#              | {"components": [{"type", "slot"}, ...], "combine": all|choose},
#    "heal": {"slot": {level: dice}}, "area": {"shape", "size", "width"},
#    "concentration": bool, "flags": [...]}

_CASTING = {"1 action": "action", "1 bonus action": "bonus", "1 reaction": "reaction"}
_LINE_WIDTH = re.compile(r"(?:(\d+) feet wide|(\d+)-foot-wide)")

#: A dice expression the engine can roll: `4d6`, `2d8+3`, or a flat number.
_ROLLABLE = re.compile(r"\d+d\d+([+-]\d+)?|\d+")
#: Upstream writes an either-or upcast value as one string: "4d6 OR 5d6". Flame
#: Strike carries one at every slot from 6th up. The "OR" is the caster's choice,
#: so it is KEPT VERBATIM rather than resolved -- see `PRESERVED_NOT_APPLIED`.
#: Note this is also why the normal whitespace strip below is not applied to a
#: value containing one: "4d6OR5d6" is neither rollable nor readable, and
#: "4d6 OR 5d6" is both a sentence and an honest record of a choice.
_ALTERNATIVE = re.compile(r"\s+OR\s+", re.IGNORECASE)


def _dice(value) -> str:
    """One damage value, whitespace normalised but never silently mangled."""
    text = str(value)
    if _ALTERNATIVE.search(text):
        return _ALTERNATIVE.sub(" OR ", text).strip()
    return text.replace(" ", "")


def _damage_component(part) -> dict | None:
    """One `{type, slot}` / `{type, character}` component, or None if unusable.

    Returns None rather than a partial dict so the caller can tell "upstream gave
    us nothing for this component" from "upstream gave us a component with no
    numbers in it" -- the first is a data problem, the second is a shape the
    parser does not know, and the two want different flags.
    """
    if not isinstance(part, dict):
        return None
    dtype = (part.get("damage_type") or {}).get("index", "")
    if part.get("damage_at_slot_level"):
        return {"type": dtype,
                "slot": {str(k): _dice(v)
                         for k, v in part["damage_at_slot_level"].items()}}
    if part.get("damage_at_character_level"):
        return {"type": dtype,
                "character": {str(k): _dice(v)
                              for k, v in part["damage_at_character_level"].items()}}
    return {"type": dtype}


def _damage_table(component: dict) -> dict:
    """The dice table of a component, slot or character keyed. Empty if neither."""
    return component.get("slot") or component.get("character") or {}


def _unrollable(table: dict) -> list:
    """Values in a dice table the engine cannot roll as written.

    An either-or value like "4d6 OR 5d6" is deliberately NOT counted here. It is
    unrollable, but it is unrollable for a reason that already has a flag and an
    explanation (`damage_choice_upcast`), and counting it again as
    `damage_unparsed` would say "this parser did not understand the shape" about a
    shape the parser understood perfectly and declined to resolve.
    """
    return [v for v in table.values()
            if not _ROLLABLE.fullmatch(str(v)) and not _ALTERNATIVE.search(str(v))]


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
    if isinstance(damage, list):
        # Three SRD 5.1 spells carry a LIST of damage components: Flame Strike
        # (fire + radiant), Ice Storm (bludgeoning + cold) and Meteor Swarm
        # (fire + bludgeoning). All three say "and" in the printed text, so every
        # component applies.
        #
        # This used to read `damage[0] if len(damage) == 1 else None`, which threw
        # the WHOLE table away for those three and wrote `damage_unparsed`. The
        # numbers were not gone from upstream; they were deleted here, and the
        # description still said "4d6 fire damage and 4d6 radiant damage". A record
        # whose prose and whose mechanics disagree is the defect: whoever reads the
        # mechanics gets a spell that does no damage.
        #
        # Every component is now kept, as `components`, with `combine` naming how
        # they join. A bare list carries no `choose` marker -- upstream spells that
        # are either-or use `choose` + `from.options`, the same shape monster
        # damage_choice uses -- so "all" is what the schema itself says, and the
        # printed "and" agrees. An `OR` inside a single component's value is a
        # different thing: that is the caster choosing between two numbers at
        # upcast, and it is left exactly as written.
        parts = [c for c in (_damage_component(p) for p in damage) if c is not None]
        if not parts:
            flags.append("damage_unparsed")
        elif len(parts) == 1:
            out["damage"] = parts[0]
        else:
            out["damage"] = {"components": parts, "combine": "all"}
            flags.append("damage_multi")
        if any(_ALTERNATIVE.search(str(v)) for c in parts for v in _damage_table(c).values()):
            flags.append("damage_choice_upcast")
    elif isinstance(damage, dict):
        component = _damage_component(damage)
        if component is None or not _damage_table(component):
            flags.append("damage_unparsed")          # a shape with no numbers in it
        else:
            out["damage"] = component
    if "damage" in out:
        tables = [_damage_table(c) for c in out["damage"].get("components", [out["damage"]])]
        if any(_unrollable(t) for t in tables):
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


# ─── Preserved, not applied ──────────────────────────────────────────────────
#
# Every flag the builders write says something is not mechanical. A flag on its
# own is a shrug: "damage_unparsed" tells a reader the parser gave up but not
# whether the numbers are gone, sitting in the record for a GM to discover, or
# simply not carried to the token at all. Those are very different situations and
# conflating them is how data loss survives a review.
#
# So each flag is registered here with WHERE the data is and WHY the engine does
# not apply it. Three states, deliberately:
#
#   preserved   the numbers are in the record AND on the token, structured, and
#               the engine reads them.
#   recorded    the numbers are in the record, structured. The engine does not
#               apply them; the GM resolves the choice or the extra target.
#   dropped     upstream carries something this parser cannot represent, and it
#               is not in the record. Only the printed `raw` text survives.
#
# `dropped` is a defect to be fixed and a count to watch. `recorded` is a design
# decision with a name. Nothing belongs in this table without one of the three,
# because "we parse it and hope" is not a state.
#
# 2014 rules only. Each line cites what the engine does, not what the 2024 books
# say, because the engine adjudicates 2014 (see the module docstring).

PRESERVED_NOT_APPLIED: dict[str, dict] = {
    # --- spell flags -------------------------------------------------------
    "damage_multi": {
        "state": "recorded",
        "reason": "two damage types, both apply; area effect the grid does not split",
        "where": "spell `mechanics.damage.components`, one entry per damage type",
        "why": "Flame Strike, Ice Storm and Meteor Swarm apply EVERY component "
               "(the SRD prints \"4d6 fire damage and 4d6 radiant damage\"), so "
               "combine is `all` and no choice is needed. The engine still "
               "narrates them: all three are area effects the tactical grid does "
               "not resolve to more than one target, and Meteor Swarm names four "
               "separate points. Making them mechanical is a rules decision with "
               "its own validation, not a parsing one.",
    },
    "damage_choice_upcast": {
        "state": "recorded",
        "reason": "the caster chooses at upcast; both numbers kept verbatim",
        "where": "the dice value itself, verbatim, e.g. \"4d6 OR 5d6\"",
        "why": "Flame Strike from 6th up increases the fire OR the radiant damage "
               "(your choice). Choosing is the caster's, so both numbers are kept "
               "as written and neither is picked here.",
    },
    "damage_unparsed": {
        "state": "recorded",
        "reason": "a dice value only resolvable at cast time (e.g. 1d6 + MOD)",
        "where": "spell `mechanics.damage` when it parsed, else the printed text",
        "why": "A dice value the engine cannot roll as written, e.g. "
               "\"1d6 + MOD\" (Spiritual Weapon), where the modifier is the "
               "caster's and only known at cast time.",
    },
    "width_assumed": {
        "state": "recorded",
        "reason": "width defaulted to 5 ft; this parser's assumption, the SRD "
                  "prints none for five line spells",
        "where": "`mechanics.area.width`, defaulted to 5",
        "why": "Five line spells (Blade Barrier, Prismatic Wall, Wall of Fire, "
               "Wall of Thorns, Wind Wall) print no width, so the width is this "
               "parser's assumption, not the SRD's. Each is already flagged "
               "`area_placement` because a wall is shaped by the caster rather "
               "than centred, which is why the engine never uses the width.",
    },
    "area_placement": {
        "state": "recorded",
        "reason": "a wall or line the caster arranges, not a blast",
        "where": "`mechanics.area`",
        "why": "A wall or a line the caster arranges, not a blast centred on a "
               "point. Resolving it needs a placement step the grid has no rule "
               "for; it is always the GM's.",
    },
    "range_unparsed": {
        "state": "recorded",
        "reason": "range printed in a form that is not a number of feet",
        "where": "printed `range` text",
        "why": "Seven spells print a range the parser cannot reduce to feet, "
               "e.g. Meteor Swarm's \"1 mile\" or \"Self (60-foot-radius "
               "diameter)\".",
    },
    "save_effect": {
        "state": "recorded",
        "reason": "the save resolves but its success outcome is not half or nothing",
        "where": "`mechanics.save.on_success`",
        "why": "The save resolves, but what happens on a SUCCESS is not half "
               "damage or nothing (10 of 319 spells). The engine narrates these "
               "rather than guess an outcome.",
    },
    "effect": {
        "state": "recorded",
        "reason": "no attack, save, damage or heal: the effect is not a number",
        "where": "printed description",
        "why": "No attack, no save, no damage, no heal: a spell whose effect is "
               "something other than a number (a wall, a summon, a new sense). "
               "194 of 319 spells. This is the biggest coverage gap in the "
               "engine and it is not a data-loss defect.",
    },
    # --- monster action flags ---------------------------------------------
    # NOTE: `rider` is the FLAG for three different keys on the same action --
    # `rider` (the sentence), `rider_effects` (what parsed) and `rider_damage`
    # (damage only the rider deals). They are one row because they are one flag,
    # and `diagnostics()` is keyed by flag.
    "rider": {
        "state": "recorded",
        "reason": "damage a rider deals; periodic ones stay the GM's, saved ones "
                  "the engine's",
        "where": "action `rider` / `rider_effects` / `rider_damage`, and "
                 "`rider_damage` on the token's attack spec",
        "why": "152 actions carry text after the damage, and it splits three "
               "ways. What parsed into a saving throw lives in `rider_effects` "
               "and the engine DOES apply it -- a Giant Spider's 2d8 poison "
               "lands on a failed CON 11 save. What did not parse stays in "
               "`rider_rest` as prose for the GM. And damage the rider alone "
               "deals lives in `rider_damage`: an Aboleth's Tentacle acid "
               "(1d12 every 10 minutes while diseased), an Assassin's Shortsword "
               "poison (7d6 on a failed DC 15 save). Those six are `rider_damage` "
               "rather than `rider_effects` because their rider is periodic or "
               "conditional rather than a save resolved on the hit, so the GM "
               "runs the clock. It is NOT hit damage, and putting it in `damage` "
               "would apply it on every hit.",
    },
    "damage_choice": {
        "state": "recorded",
        "reason": "one of several damage options; a creature's choice, not the parser's",
        "where": "action `damage_choice` ({choose, options}), and on the token's "
                 "attack spec",
        "why": "\"Melee Weapon Attack: ... one of the following options\" -- 16 "
               "SRD actions. Which option is a creature's choice (a Druid's "
               "Quarterstaff is also shillelagh; a Djinni's Scimitar adds "
               "lightning OR thunder). The engine keeps every option and picks "
               "none; an attack whose only damage is a choice resolves to no "
               "damage rather than to the first option, which is why those "
               "actions carry `damage_unparsed` too.",
    },
    "targeting": {
        "state": "dropped",
        "reason": "area not in a shape the parser knows; only the printed text survives",
        "where": "printed `raw`",
        "why": "A save action whose area the parser could not find (49 actions, "
               "almost all legendary actions and breath weapons that are not "
               "written as \"N-foot-radius cone\"). The SRD's shape does not "
               "cover them and guessing an area would move damage the printed "
               "text does not put there.",
    },
    "conditional_bonus": {
        "state": "recorded",
        "reason": "attack bonus depends on the wielder (shillelagh, a magic "
                  "weapon); the printed number is the bare-hand one",
        "where": "attack `attack.bonus`, plus the printed `raw`",
        "why": "Two SRD actions. The Druid's Quarterstaff reads \"+2 to hit\" and "
               "then offers +4 with shillelagh and +3 with a magic weapon; the "
               "engine cannot know which is in hand at build time, so the base "
               "number is kept and the GM adjusts. Emitting the best case would "
               "make every staff hit stronger than the rules allow.",
    },
    "success_conflict": {
        "state": "recorded",
        "reason": "upstream's field contradicts its own text; neither side picked",
        "where": "action `dc.on_success` set to None, with `raw`",
        "why": "Five records where upstream's `success_type` field contradicts "
               "its own printed text (an Adult Red Dragon's Fire Breath says "
               "\"none\" where the text says half). Refusing to pick a side is "
               "the only honest option; `None` means unresolved.",
    },
    "unparsed": {
        "state": "dropped",
        "reason": "no known shape; the printed sentence is kept verbatim in raw",
        "where": "printed `raw`",
        "why": "An action in no known shape (82 actions: prose-only abilities, "
               "multiattack variants, non-attack entries). Kept as the printed "
               "sentence and named in `raw`, because the alternative is inventing "
               "a structure.",
    },
}


def diagnostics(record: dict) -> list:
    """What this one record preserves but does not apply, one line per flag.

    Read off `flags`, which is where the builders already write the fact. The
    point is that "the parser gave up" and "the numbers were thrown away" are
    distinguishable without reading three files: a flag whose registry row says
    `dropped` has lost its numbers, and one that says `recorded` has not.

    Returns [] for a record with no flags, which is the normal case and means the
    engine can run this record's numbers without GM judgment.
    """
    return [f"{flag}: {PRESERVED_NOT_APPLIED[flag]['state']} -- "
            f"{PRESERVED_NOT_APPLIED[flag]['reason']}"
            for flag in record.get("flags", []) if flag in PRESERVED_NOT_APPLIED]


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
        # `maxsplit` by keyword. Passed positionally it is deprecated from 3.13
        # and warns once per call, which here is once per monster action in the
        # whole dataset, so it is noise in a developer's terminal and noise in
        # the CI log that now runs this build (outer #289).
        after = re.split(r"half as much damage on a successful one\.|damage on a failed save\.",
                         desc, maxsplit=1)
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

    # Derive class from path: packs/_source/classfeatures/<class>/<class>-features/...
    # or races: packs/_source/races/<race>/<variant>-features/...
    parts = path.replace("\\", "/").split("/")
    # Empty string, not None, for "belongs to no one class": lookup's formatter
    # does r.get("class", "") and prints the result, so a None here renders as
    # the word "None" in the header instead of falling through to the type.
    class_name = ""
    if FVTT_CLASS_FEATURES in parts:
        idx        = parts.index(FVTT_CLASS_FEATURES)
        class_name = parts[idx + 1] if idx + 1 < len(parts) else ""
        if class_name == "shared-features":
            # Extra Attack, the fighting styles, ASI: they belong to no one
            # class, and printing "[shared-features]" reads as a class name.
            class_name = ""
    elif FVTT_RACES in parts:
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

def _partition_fvtt_tree(tree: list) -> tuple:
    """Split a Foundry repo tree into the class documents and feature documents
    of the 2014 packs. (feature_paths, class_yml_paths)

    Pure, so the edition this build reads is a thing a test can assert rather
    than a path match buried in a network loop. The 2024 packs nest features
    one way and class documents another, and both editions sit in the same
    repository, so a well-meaning path edit would otherwise quietly swap the
    edition with nothing to notice.
    """
    feature_paths   = []
    class_yml_paths = []
    class_prefix    = f"packs/_source/{FVTT_CLASS_PACK}/"
    feature_prefix  = f"packs/_source/{FVTT_CLASS_FEATURES}/"
    race_prefix     = f"packs/_source/{FVTT_RACES}/"
    for t in tree:
        p = t["path"] if isinstance(t, dict) else str(t)
        if not p.endswith(".yml") or os.path.basename(p).startswith("_"):
            continue
        if p.startswith(class_prefix) and p.count("/") == 3:
            # e.g. packs/_source/classes/wizard.yml, the class document itself.
            # Flat in 2014; the 2024 packs nest it as classes24/<class>/<class>.yml.
            class_yml_paths.append(p)
        elif p.startswith(feature_prefix) and p.count("/") >= 4:
            # e.g. packs/_source/classfeatures/wizard/wizard-features/sculpt-spell.yml (6)
            # or one that belongs to no single class:
            # classfeatures/shared-features/extra-attack.yml (4), and
            # classfeatures/shared-features/fighting-styles/archery.yml (5).
            # The depth floor keeps out class documents that sit loose in the
            # pack root (classfeatures/grappler.yml, 3), which are not features.
            # Extra Attack and Ability Score Improvement live at depth 4, so the
            # floor cannot be higher or a core 2014 feature goes missing.
            feature_paths.append(p)
        elif p.startswith(race_prefix) and re.search(r"/-?\w+-features/", p):
            feature_paths.append(p)
    return feature_paths, class_yml_paths


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

    feature_paths, class_yml_paths = _partition_fvtt_tree(tree)

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
        if FVTT_CLASS_PACK not in parts:
            continue
        idx = parts.index(FVTT_CLASS_PACK)
        # The 2014 pack is flat (classes/wizard.yml), so the segment after the
        # pack is the file itself, extension and all. The scale tables are keyed
        # by class slug, and _norm_feature looks them up by the directory in the
        # feature path, so "wizard.yml" would silently never match.
        class_name = parts[idx + 1][:-4] if idx + 1 < len(parts) else None
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
    # Printed because the edition is the one field that decides whether the rest
    # of the file is usable at all: a 2024 dataset read by a 2014 engine answers
    # 2014 questions with 2024 text.
    print(f"Edition:    {meta.get('edition', 'NOT RECORDED (pre-' + EDITION + ' build)')}")
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
        # `_build_fvtt` returns the tree sha it read. It used to be discarded
        # here and the fetched_at/sha pair below carried an empty sha instead,
        # so the 260 features that come from this source had anonymous
        # provenance while the 5e-bits half named a commit. Recorded, and
        # recorded as "" when the tree could not be read, because a wrong sha
        # would be worse than a missing one.
        features, tree_sha = _build_fvtt()
        if tree_sha and not fvtt_sha:
            fvtt_sha = tree_sha
        elif tree_sha and fvtt_sha and tree_sha != fvtt_sha:
            # Not fatal: the commits API and the tree API can legitimately differ
            # by a commit or two of lag. Said out loud rather than silently kept,
            # because two shas for one source is the sort of thing nobody reads
            # until it matters.
            print(f"  ! foundryvtt tree sha {tree_sha[:12]} differs from commits "
                  f"sha {fvtt_sha[:12]}", file=sys.stderr)
    categories["features"] = features

    counts = {k: len(v) for k, v in categories.items()}
    total  = sum(counts.values())

    dataset = {
        "_meta": {
            "built_at":      now,
            "edition":       EDITION,
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
