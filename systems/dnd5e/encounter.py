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


# ── the day, not the fight ────────────────────────────────────────────────────

def _day_xp(level: int) -> int:
    """One character's adventuring-day budget. xp.py owns the table."""
    level = _clamp_level(level)
    return xp().ADVENTURING_DAY_XP.get(level, xp().ADVENTURING_DAY_XP[20])


def _encounters_affordable(day_per_character: int, per_character: list) -> dict:
    """How many fights of each difficulty fit inside one character's day.

    Integer division, and floored at zero. A day budget smaller than a single
    Easy encounter is a real answer — a level-1 party on a trivial day — and
    rounding it up to "1 encounter" would tell a GM their day holds a fight that
    is a tenth of it, which is the kind of confident wrong number this module
    exists to avoid.
    """
    return {tier: day_per_character // value
            for tier, value in zip(TIERS["2014"], per_character)}


def adventuring_day(levels=None, ruleset: str = "2014", plan=None, lookup=None,
                     suggest=None) -> dict:
    """What a party can be handed in a whole day, as opposed to one fight.

    2014 ONLY, and deliberately so. The day XP budget is a 2014 DMG table with no
    2024 counterpart in this project, and the temptation to answer a `--ruleset
    2024` request by dividing the 2024 High tier by three is exactly the failure
    this module is written against: 2024's three tiers are read as "the party's
    share of a High-difficulty *day*" (see the module docstring), so treating one
    tier as an encounter cost would produce a number with the right shape and no
    meaning. A 2024 GM is told so, rather than given a derived fiction.

    `plan` is the optional part that makes this worth having: a list of planned
    encounters, each a `groups` list like `rate()` takes, so the GM can ask
    "I have these four fights, is that a day?" Without a plan the budget alone
    is nearly useless, and the reason is worth writing down. The table is
    *calibrated* so that three to five fights ARE a day: dividing the day budget
    by any difficulty threshold returns 3 to 4 encounters at every level from 1 to
    20, because the two tables were built from the same encounter counts. So
    "a day holds 4 Hard fights" is true at level 1 and level 20 and tells a GM
    planning a level 20 day nothing they did not already assume. Only a plan can
    come out over or under.
    """
    if ruleset not in RULESETS:
        raise ValueError(f"unknown ruleset {ruleset!r} (2014 or 2024)")
    if ruleset != "2014":
        raise ValueError("The adventuring day budget is a 2014 DMG table. 2024 has no "
                         "day budget here: its three tiers are a whole day's share, not "
                         "an encounter cost, so there is nothing to divide. Rate fights "
                         "with `rate` instead.")
    lv = _levels(levels)
    rows = [_day_xp(l) for l in lv]
    per_character_budget = _day_xp(_average_level(lv))
    thresholds = _row("2014", _average_level(lv))
    affordable = _encounters_affordable(per_character_budget, thresholds)
    low, high = xp().ENCOUNTERS_PER_DAY
    total = sum(rows)
    out = {
        "ruleset": ruleset,
        "levels": lv,
        "average_level": _average_level(lv),
        "mixed": len(set(lv)) > 1,
        "day_per_character": rows,
        "party_total": total,
        "thresholds": thresholds,
        "tiers": list(TIERS["2014"]),
        "encounters": affordable,
        "per_day": (low, high),
        "planned": [],
        "planned_xp": 0,
        "headroom": _headroom(0, total, 0),
    }
    for index, groups in enumerate(plan or [], start=1):
        if not groups:
            continue
        rated = rate(groups, lv, "2014", lookup=lookup, suggest=suggest)
        out["planned"].append({
            "index": index,
            "groups": [(r["name"], r["count"], r["cr_label"], r["xp"], r["total"])
                       for r in rated["rows"]],
            "count": rated["count"],
            "raw": rated["raw"],
            "multiplier": rated["multiplier"],
            "adjusted": rated["adjusted"],
            "per_character": rated["per_character"],
            "difficulty": rated["difficulty"],
        })
        out["planned_xp"] += rated["adjusted"]
    if out["planned"]:
        out["headroom"] = _headroom(out["planned_xp"], total, len(out["planned"]))
    return out


def _headroom(spent: int, total: int, fights: int) -> str:
    """How much of the day a planned set of fights uses up, in words.

    This is the number a GM needs and it is the reason the day budget exists at
    all: the table is calibrated so that three to five fights ARE a day, so the
    budget on its own can never come out over or under. Only a plan can.

    The share is reported as a share, and the fight count as the count the GM
    actually wrote — never converted into an invented "fights' worth". A GM who
    planned four fights does not learn anything from being told those four fights
    are worth one, and the conversion would be the exact confident-wrong-number
    failure this module exists to prevent.

    The bands are the DMG's own encounter counts expressed as fractions of a day
    (3 and 5 fights is the range), because a GM thinks in fights, not in
    percentages of a budget.
    """
    if fights <= 0:
        return "no fights planned yet"
    share = spent / total if total else 1.0
    plural = "fight" if fights == 1 else "fights"
    lead = f"{fights} planned {plural}, about {share:.0%} of the day"
    if share <= 0.5:
        return f"{lead}. Room for more, or a rest between them"
    if share <= 1.0:
        return (f"{lead}. That is a full adventuring day and not an over budget "
                f"one, which is what the table is calibrated for")
    if share <= 2.0:
        return (f"{lead}. Over: two days, or a long rest in the middle of it, and "
                f"the party will feel the second half")
    return (f"{lead}. Far over: this is a multi-day march, not a day. The party "
            f"will be spent before the last fight, and exhaustion 5 (speed 0) is "
            f"likely by the end of it")


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
