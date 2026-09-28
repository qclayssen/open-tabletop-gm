"""encounter.py: dnd5e encounter design — what a fight is worth before it starts.

WHY THIS EXISTS
===============
`combat.py start` answers "what happens in this fight". Nothing answered "how hard
is this fight", which is the question a GM asks *before* committing a session to
one, and the question players ask after one goes wrong. So the numbers existed
(every DMG table is public knowledge) but the tool did not.

The tables have exactly one home, `systems/dnd5e/xp.py` — the 2014 thresholds, the
CR→XP values and the monster-count multiplier are imported from there rather than
copied, because a second copy of an XP table is a second thing to get wrong. The
2024 budget table has no such ancestor (it replaces the threshold table rather
than extending it) and lives here, in the system module, where a port to another
game system can leave it behind.

THE TWO RULESETS DIFFER IN MORE THAN NAMES
==========================================
2014 (DMG ch. 9): four thresholds per level, and a multiplier for the number of
monsters, because six goblins are worth more than the sum of their XP — the party
spends more attention per round. A party of mixed levels is measured against the
average level here, and each character is measured separately; the printed figure
is the per-character award.

2024 (DMG ch. 12): three tiers, no monster-count multiplier at all. The
monsters' XP is what it is, and the party's budget is the per-character figure
multiplied by the size of the party — so a big group of goblins is NOT worth
more than the same XP in one ogre, and the encounter is read as "the whole
party's share of a High-difficulty day". Deadly is gone: 2024 has no tier that
says "this should kill someone", only "this is a fight that will cost you".

Read the monster's own CR and XP out of the SRD record, the same way
`combat.py add --srd` does, so a rating and a fight can never disagree about how
much a goblin is worth.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).parent


def _try(fn):
    """A suggestion pass must never be the reason a rating fails.

    difflib over a dataset that is not built yet raises nothing useful, and the
    GM who gets a traceback here learns less than the GM who gets 'no monster'.
    """
    try:
        return list(fn)
    except Exception:                                             # noqa: BLE001
        return []

RULESETS = ("2014", "2024")
TIERS = {"2014": ("Easy", "Medium", "Hard", "Deadly"),
         "2024": ("Low", "Moderate", "High")}

# 2024 DMG, encounter XP budget per character per level: Low / Moderate / High.
# The 2024 rules dropped the monster-count multiplier and the Deadly tier, so
# this is a different table rather than a subset of xp.py's — the Moderate
# column is also not the 2014 Medium column, and copying one for the other is
# exactly the bug this file's existence is meant to prevent.
XP_BUDGET_2024: dict[int, tuple[int, int, int]] = {
    1:  (25,   50,   100),
    2:  (50,   75,   150),
    3:  (75,   150,  225),
    4:  (125,  250,  375),
    5:  (250,  400,  750),
    6:  (300,  500,  1100),
    7:  (350,  750,  1400),
    8:  (450,  1000, 1900),
    9:  (550,  1100, 2400),
    10: (600,  1400, 2800),
    11: (800,  1600, 3600),
    12: (1000, 2000, 4500),
    13: (1100, 2400, 5400),
    14: (1250, 2800, 6300),
    15: (1400, 3200, 7100),
    16: (1600, 3900, 8400),
    17: (2000, 4500, 10000),
    18: (2100, 5000, 11400),
    19: (2400, 5700, 12800),
    20: (2800, 6400, 14400),
}


# ── sibling modules ───────────────────────────────────────────────────────────

def _sibling(name: str):
    """Load another system module by file path, once.

    The system modules are scripts, not an installed package: tactics_rules.py
    loads tactics_spells.py the same way, and this keeps the XP tables in
    xp.py readable as xp.py rather than as a submodule of a rules engine.
    """
    key = f"dnd5e_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, _HERE / f"{name}.py")
    if spec is None or spec.loader is None:                    # pragma: no cover
        raise ImportError(f"cannot load {_HERE / (name + '.py')}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def xp():
    """systems/dnd5e/xp.py — the 2014 XP tables and the award ledger."""
    return _sibling("xp")


# ── table helpers ─────────────────────────────────────────────────────────────

def _clamp_level(level) -> int:
    try:
        n = int(level)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(20, n))


def _levels(levels) -> list:
    return [_clamp_level(l) for l in (levels or [])] or [1]


def _average_level(levels: list) -> int:
    """The 2014 rules measure a mixed party by its average level (DMG ch. 9);
    rounding to nearest, the same way `xp.py award` does, so a rating and the
    award that follows it cannot disagree."""
    return max(1, min(20, round(sum(levels) / len(levels))))


def _row(ruleset: str, level: int) -> list:
    level = _clamp_level(level)
    if ruleset == "2024":
        return list(XP_BUDGET_2024[level])
    return list(xp().XP_THRESHOLDS.get(level, xp().XP_THRESHOLDS[20]))


def _multiplier_table(ruleset: str) -> list:
    """[(monster count ceiling, multiplier)] — 2024 has no multiplier at all."""
    return [] if ruleset == "2024" else [list(m) for m in xp().MONSTER_MULTIPLIERS]


def multiplier_for(count: int, ruleset: str = "2014"):
    """The 2014 monster-count multiplier. None for 2024, which has no such thing."""
    if ruleset == "2024":
        return None
    return xp()._monster_multiplier(count)


def cr_label(cr) -> str:
    """"1/4" rather than 0.25 — a GM reads CR in fractions."""
    if cr in (None, ""):
        return "?"
    try:
        return xp()._normalise_cr(str(cr))
    except Exception:                                          # noqa: BLE001
        return str(cr)


def _xp_for_cr(cr: str) -> int:
    return xp().CR_XP.get(cr, 0)


# ── monster resolution ────────────────────────────────────────────────────────

def _default_lookup(name: str) -> dict:
    here = str(_HERE)
    if here not in sys.path:
        sys.path.insert(0, here)
    import lookup                                   # systems/dnd5e/lookup.py
    rec = lookup.lookup_record(name, category="monster")
    if not rec:
        raise ValueError(f"no SRD monster {name!r}")
    return rec


def _default_suggest(name: str) -> list:
    here = str(_HERE)
    if here not in sys.path:
        sys.path.insert(0, here)
    import lookup
    try:
        return [nm for nm, _cat in lookup.suggest(name, category="monster", n=3)]
    except Exception:                                          # noqa: BLE001
        return []


def monster(name: str, lookup=None, suggest=None, known: dict = None) -> dict:
    """{"name", "cr", "xp"} for an SRD monster, or a ValueError the GM can read.

    `known` is a record the caller already has (a token's own CR and XP); it is
    used as-is rather than looked up again, with only `name` as a fallback.

    A typo has to come back as a list of near matches, not a traceback: the GM
    is mid-design, writing "goblin x4" from memory, and the useful answer to
    "gobiln" is "goblin".
    """
    rec = known
    if rec is None or rec.get("xp") in (None, ""):
        try:
            rec = (lookup or _default_lookup)(name)
        except ValueError:
            near = _try((suggest or _default_suggest)(name))
            if near:
                raise ValueError(f"No SRD monster {name!r}. Did you mean: "
                                 f"{', '.join(near)}?") from None
            raise ValueError(f"No SRD monster {name!r}. Build the SRD dataset "
                             f"(python3 systems/dnd5e/build_srd.py), or use a name "
                             f"from the SRD monster list.") from None
    cr = rec.get("cr")
    value = rec.get("xp")
    if value in (None, ""):
        value = _xp_for_cr(cr_label(cr))
    return {"name": rec.get("name") or name, "cr": cr, "xp": int(value or 0)}


# ── budget ────────────────────────────────────────────────────────────────────

def budget(levels=None, ruleset: str = "2014") -> dict:
    """The thresholds this party is measured against, and the multiplier that
    turns a pile of monsters into an encounter."""
    if ruleset not in RULESETS:
        raise ValueError(f"unknown ruleset {ruleset!r} (2014 or 2024)")
    lv = _levels(levels)
    tiers = list(TIERS[ruleset])
    rows = [_row(ruleset, l) for l in lv]
    return {
        "ruleset": ruleset,
        "tiers": tiers,
        "levels": lv,
        "average_level": _average_level(lv),
        "mixed": len(set(lv)) > 1,
        "per_character": _row(ruleset, _average_level(lv)),
        "party_total": [sum(r[i] for r in rows) for i in range(len(tiers))],
        "multiplier": _multiplier_table(ruleset),
    }


# ── rating ────────────────────────────────────────────────────────────────────

def rate(groups=None, levels=None, ruleset: str = "2014", lookup=None, suggest=None,
         known: dict = None) -> dict:
    """Rate a monster list against a party. `groups` is [(name, count), ...].

    `known` maps a name to a record the caller already holds — the CR and XP the
    monsters on the grid were built with. A fight that is being costed after it
    ran must not look its own monsters up again: the tokens' display names are
    numbered for the GM ("Goblin 3") and a second lookup is a second chance to
    disagree with the fight that actually happened.

    Returns the arithmetic as well as the verdict: a rating the GM cannot check
    is a rating the GM has to take on faith, and "it felt deadly" has to be
    argued with numbers or it is just mood.
    """
    if ruleset not in RULESETS:
        raise ValueError(f"unknown ruleset {ruleset!r} (2014 or 2024)")
    groups = [(str(n).strip(), int(c)) for n, c in (groups or []) if str(n).strip()]
    if not groups:
        raise ValueError("No monsters to rate.")
    known = known or {}
    rows, raw, count = [], 0, 0
    for name, n in groups:
        if n < 1:
            raise ValueError(f"{name}: {n} monsters — the count has to be 1 or more.")
        m = monster(name, lookup=lookup, suggest=suggest, known=known.get(name.lower()))
        m["count"] = n
        m["total"] = m["xp"] * n
        m["cr_label"] = cr_label(m.get("cr"))          # the GM reads fractions
        rows.append(m)
        raw += m["total"]
        count += n
    mult = multiplier_for(count, ruleset)
    adjusted = int(raw * mult) if mult else raw
    lv = _levels(levels)
    party = len(lv)
    avg = _average_level(lv)
    thresholds = _row(ruleset, avg)
    per_character = adjusted // party if party else adjusted
    return {
        "ruleset": ruleset,
        "rows": rows,
        "count": count,
        "raw": raw,
        "multiplier": mult,
        "adjusted": adjusted,
        "party_size": party,
        "levels": lv,
        "average_level": avg,
        "thresholds": thresholds,
        "tiers": list(TIERS[ruleset]),
        "per_character": per_character,
        "difficulty": classify(per_character, thresholds, ruleset),
    }


def classify(xp_per_character: int, thresholds, ruleset: str = "2014") -> str:
    """The tier a per-character XP figure falls in.

    Below the first tier it is "trivial", not "Easy": telling a GM that two rats
    in a cellar is an Easy encounter is the kind of true-sounding line that
    convinces them to stop preparing, and 2014's own thresholds have no word for
    "a fight that is not a fight yet".
    """
    for name, value in zip(TIERS[ruleset][::-1], thresholds[::-1]):
        if xp_per_character >= value:
            return name.lower()
    return "trivial"


# ── XP award, through the ledger xp.py already keeps ──────────────────────────

def award_xp(sheet_path, amount: int) -> dict:
    """Add XP to one sheet. Returns {} when the sheet does not track XP.

    A sheet that records "**XP:** 0 (milestone levelling)" is a campaign playing
    by milestones, not a sheet with an XP total of zero. Writing 300 XP into it
    would put a number where the campaign has a rule, and the ledger would then
    claim an award that nothing reflects on the sheet — so it is reported as
    "not tracked" and the GM awards progression their own way.
    """
    return xp().award_xp(sheet_path, int(amount))


def record_awards(campaign_dir, entries: list, note: str = "") -> None:
    """Append the award to the campaign's existing xp-ledger.jsonl."""
    xp().record_awards(campaign_dir, entries, note)
