#!/usr/bin/env python3
"""
rules_coverage.py -- what fraction of 2014 5e the engine actually runs.

WHY THIS IS SEPARATE FROM export_bestiary.py
============================================
`export_bestiary.py --stats` answers a *rendering* question: of these 334
monsters, how many parsed into a complete Fantasy Statblocks note. Its input is
one JSON file and its imports are stdlib. This instrument answers a *rules*
question -- of these 319 spells, how many can the engine resolve without a GM --
and it has to reach into `tactics.rules`, `tactics_spells` and `localdm` to
answer honestly.

Two questions, two tools, one report each. They are not merged because merging
them would put the rules engine's import surface on a script that runs fine
without it, and would leave `--stats` looking like a coverage number when it is
a parse-success rate. `ROADMAP-ideas.md` RI5 proposed extending `--stats`; this
is the same instrument in the place that can carry it.

THE THREE STATES, AND WHY TWO ARE NOT ENOUGH
============================================
Every record gets exactly one of:

  mechanical   the engine runs this record's numbers with no GM judgment
  reference    the engine can show it (lookup prints it) but not apply it
  unmeasured   there is no data to measure against

`unmeasured` exists because the dataset is gitignored (`.gitignore:50`). On a
fresh clone `dnd5e_srd.json` does not exist, and an instrument that reported 0%
would be indistinguishable from an instrument reporting that the engine
implements nothing. Both are wrong, and the second is worse, because a 0% reads
as a measurement. So with no dataset every category reports `unmeasured`, every
count is `null` rather than `0`, and `dataset_absent` names the reason. A
missing dataset never silently becomes zero and never silently becomes one.

This is the "runnable in two modes" requirement from RI5, and it is why
`--data` exists: the same code path produces a measured report from a fixture
and an unmeasured report from an absent file.

THE DEFECT COUNTER IS A SEPARATE NUMBER WITH A SEPARATE TARGET
==============================================================
Implementation coverage is a percentage and can only be argued about. The defect
counter is a count whose target is **zero**, and it is only allowed to contain
things that are unambiguously wrong:

  assumptions_without_consumers  a flag or field the build writes that nothing
                                 reads, so a value nobody checks
  discarded_metadata             data parsed out of upstream that reaches
                                 neither the record nor the token
  ignored_clauses                a 2014 rule clause present in the SRD text that
                                 the engine resolves nowhere
  unsupported_declarations        a feature the engine will not apply and does
                                 not tell the player about

Both counters are reported together and never merged: a record can be fully
mechanical and still contribute a defect (petrified resistance to all damage is
mechanically applied AND has an unenforced clause).

Usage:
    python3 scripts/rules_coverage.py                 # measured report, text
    python3 scripts/rules_coverage.py --json          # machine-readable
    python3 scripts/rules_coverage.py --data FILE    # another dataset
    python3 scripts/rules_coverage.py --category spells --json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"

SCHEMA_VERSION = 1

#: The only three states a record may carry. Ordered worst-to-best for counting.
STATES = ("mechanical", "reference", "unmeasured")

#: Why a record is not mechanical. One of these on every non-mechanical row;
#: a row with `state: mechanical` must have `reason: null`. This is the part
#: that makes the report falsifiable: a boolean "implemented" cannot be argued
#: with, an enum can be.
REASONS = {
    # --- spells ------------------------------------------------------------
    "no_anchor": (
        "no attack roll, no save, no damage and no healing: the spell's effect "
        "is something other than a number (a wall, a summon, a new sense)"
    ),
    "builtin_forced_narrate": (
        "tactics_spells.BUILTIN overrides the SRD fields and says narrate, "
        "because the SRD record is shaped wrong for what the spell does"
    ),
    "builtin_level_narrate": (
        "tactics_spells.BUILTIN sets narrate_from_level: the SRD record holds one "
        "number and the spell needs a different number per cast"
    ),
    "blocking_flag": (
        "a build_srd flag the resolver treats as blocking; the flag names which"
    ),
    "save_nothing_to_apply": (
        "the save resolves but the engine cannot apply either outcome, so "
        "rolling would produce a number with nothing behind it"
    ),
    # --- monsters ----------------------------------------------------------
    "no_structured_action": (
        "no action on the creature is a shape the engine can run"
    ),
    "action_unparsed": (
        "at least one action is prose the parser could not decompose"
    ),
    "action_area_unknown": (
        "at least one action is a save with no parseable area"
    ),
    "action_damage_unresolved": (
        "at least one action's damage is a choice or an unparseable value"
    ),
    # --- conditions and exhaustion -----------------------------------------
    "no_table_entry": (
        "the dataset names a condition the engine's condition table does not"
    ),
    "clause_not_enforced": (
        "the condition is applied, but a clause of the SRD text is not"
    ),
    "documented_deviation": (
        "the engine deliberately differs from the SRD and says so in the code; "
        "the clause is unenforced on purpose and the call is a user's, not a bug"
    ),
    # --- features and items ------------------------------------------------
    "unsupported_listed": (
        "the engine will not apply it and says so to the player via "
        "localdm.autopilot.UNSUPPORTED_FEATURES"
    ),
    "unsupported_unlisted": (
        "the engine will not apply it and does NOT tell the player: an ordinary "
        "action with no note. This is a defect, not a design choice."
    ),
    "reference_only": (
        "lookup prints it; no engine rule consumes it"
    ),
    # --- everything, when there is nothing to measure ------------------------
    "dataset_absent": "no dataset on disk, so nothing was measured",
}

#: Which states a reason may accompany. `unmeasured` only ever pairs with
#: `dataset_absent`; a bug that reports 40% coverage with no data is the exact
#: failure this table exists to make impossible to write by accident.
_REASON_STATES = {
    "no_anchor": ("reference",),
    "builtin_forced_narrate": ("reference",),
    "builtin_level_narrate": ("reference",),
    "blocking_flag": ("reference",),
    "save_nothing_to_apply": ("reference",),
    "no_structured_action": ("reference",),
    "action_unparsed": ("reference",),
    "action_area_unknown": ("reference",),
    "action_damage_unresolved": ("reference",),
    "no_table_entry": ("reference", "unmeasured"),
    "clause_not_enforced": ("reference",),
    "documented_deviation": ("reference",),
    "unsupported_listed": ("reference",),
    "unsupported_unlisted": ("reference",),
    "reference_only": ("reference",),
    "dataset_absent": ("unmeasured",),
}


class CoverageError(ValueError):
    """The report would be wrong rather than merely incomplete."""


# ─── loading ──────────────────────────────────────────────────────────────────


def _load_module(name: str, path: pathlib.Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine():
    """The 2014 rules modules, loaded the way the CLI loads them.

    `tactics.rules.load("dnd5e")` is the only supported way in, and it puts the
    system module in `sys.modules` under a stable name; going around it would
    measure a different copy of the rules than the one the game runs.
    """
    for extra in (ROOT / "scripts", ROOT / "systems" / "dnd5e"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    import tactics.rules as rules_mod

    RULES = rules_mod.load("dnd5e")
    system = sys.modules[type(RULES).__module__]
    return system, system._spells_module()


def _autopilot():
    """`localdm.autopilot`, for UNSUPPORTED_FEATURES. Optional: a tree without
    it can still measure everything else, and the features axis then reports
    which way it went."""
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from localdm import autopilot
    except Exception:                                            # noqa: BLE001
        return None
    return autopilot


def load_dataset(path: pathlib.Path) -> tuple:
    """(records, provenance) or (None, reason) when the file is not there.

    Never raises for a missing file: a missing dataset is a reportable state,
    not an error, and the difference between "0 of 319" and "319 unmeasured" is
    the whole reason this function returns instead of asserting.
    """
    if not path.exists():
        return None, f"dataset absent at {path}"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as error:
        return None, f"dataset unreadable at {path}: {error}"
    if not isinstance(data, dict):
        return None, f"dataset at {path} is not an object"
    meta = data.get("_meta") or {}
    provenance = {
        "path": str(path),
        "built_at": meta.get("built_at"),
        "total_records": meta.get("total_records"),
        "record_counts": meta.get("record_counts") or {},
        "sources": meta.get("sources") or {},
    }
    # The edition is a fact about the dataset, not an inference from a URL. The
    # build reads the 2014 packs (build_srd.FVTT_CLASS_PACK and friends) and
    # /src/2014/ from 5e-bits; a dataset that recorded its own edition would be
    # believed over this, which is why the key is read first.
    provenance["edition"] = meta.get("edition", "2014")
    return data, provenance


# ─── per-record measurement ───────────────────────────────────────────────────


def _row(category, index, name, state, reason=None, detail=None):
    if state not in STATES:
        raise CoverageError(f"{category}/{index}: unknown state {state!r}")
    if state == "mechanical" and reason is not None:
        raise CoverageError(f"{category}/{index}: mechanical rows carry no reason")
    if state != "mechanical":
        if reason not in REASONS:
            raise CoverageError(f"{category}/{index}: reason {reason!r} is not in REASONS")
        if state not in _REASON_STATES[reason]:
            raise CoverageError(
                f"{category}/{index}: reason {reason!r} may not pair with {state!r}")
    return {"category": category, "index": index, "name": name,
            "state": state, "reason": reason, "detail": detail or {}}


def _unmeasured_rows(category, records, names):
    return [_row(category, r, names.get(r, r), "unmeasured", "dataset_absent")
            for r in records]


# --- spells -------------------------------------------------------------------

#: build_srd flags that `tactics_spells.resolve` treats as blocking. Kept here
#: as data so the report and the resolver cannot drift: if a flag is added to
#: the resolver's set and not to this, the report would call a spell mechanical
#: that the engine narrates.
_BLOCKING = ("range_unparsed", "area_placement", "damage_unparsed",
             "save_effect", "effect")


#: The caster every spell is measured against. Spell coverage is a FUNCTION OF
#: THE CASTER and the report has to say so, or "54 of 319" is a number with a
#: hidden assumption in it. Eldritch Blast is the proof: `BUILTIN` sets
#: `narrate_from_level: 5` because the SRD record holds one number and the spell
#: deals more beams above 4th, so it is mechanical at level 1 and not at 5.
#: Both numbers are real; which one is "the" coverage figure is a decision, and
#: hiding the decision is how the roadmap's 58 became unfalsifiable.
MEASUREMENT_CONDITIONS = {
    "caster_level": 5,
    "spell_save_dc": 14,
    "spell_attack": 6,
    "slot_levels": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    "note": "a spell counts as mechanical if `tactics_spells.resolve` returns "
            "any mode other than `narrate` for this caster. Raise "
            "--caster-level to measure at another level; eldritch blast is "
            "mechanical at 1 and reference at 5.",
}


def _production_srd(spells_mod):
    """The real `tactics_spells._srd`, whatever is installed right now.

    Read out of the module's source rather than from `spells_mod._srd`, because
    `spells_mod._srd` is a mutable module global and the test tree replaces it:
    `tests/tactics_fixtures.py` assigns `spells_rules._srd = srd_spell` at import
    and nothing puts it back (that is issue #231). When this ran inside the full
    suite it silently measured the six-spell test fixture and reported it as the
    319-spell dataset -- 1,008 spell rows were fiction and the headline coverage
    number was wrong. Reached by loading the module source again under a
    throwaway name, which no fixture has patched.
    """
    import importlib.util

    path = pathlib.Path(spells_mod.__file__)
    spec = importlib.util.spec_from_file_location("tactics_spells_pristine", path)
    pristine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pristine)
    return pristine._srd


def _spell_rows(spells, system, spells_mod, measured=None):
    """One row per spell, driven through the real resolver.

    The production `_srd` is installed for the duration and restored afterwards,
    so the report describes the dataset it was pointed at rather than whatever
    SRD lookup happens to be installed when it runs.
    """
    from tactics.state import Token

    measured = measured or MEASUREMENT_CONDITIONS
    builtin = spells_mod.BUILTIN
    slots = {str(i): {"total": 1, "used": 0} for i in measured["slot_levels"]}
    production = _production_srd(spells_mod)
    installed = spells_mod._srd
    spells_mod._srd = production
    rows = []
    try:
        for record in spells:
            index = record.get("index") or record.get("name", "")
            name = record.get("name", "")
            key = re.sub(r"\s+", " ", name.strip().lower())
            caster = Token(id="cov", name="Coverage", side="pc", x=0, y=0,
                           hp=1, max_hp=1, ac=10,
                           extra={"spells": [key],
                                  "spell_dc": measured["spell_save_dc"],
                                  "spell_attack": measured["spell_attack"],
                                  "level": measured["caster_level"],
                                  "slots": slots})
            try:
                spec = spells_mod.resolve(caster, key)
            except ValueError as error:
                # A spell the engine cannot even resolve is `reference`, and the
                # error is kept so the row explains itself. It is NOT unmeasured:
                # we measured it and the answer was no.
                rows.append(_row("spell", index, name, "reference",
                                 "no_anchor", {"error": str(error)}))
                continue
            mode = spec.get("mode")
            if mode != "narrate":
                rows.append(_row("spell", index, name, "mechanical",
                                 detail={"mode": mode}))
                continue
            reason, detail = _narrate_reason(record, builtin, key, spec)
            rows.append(_row("spell", index, name, "reference", reason, detail))
    finally:
        spells_mod._srd = installed
    return rows


def _narrate_reason(record, builtin, key, spec):
    """Which of the documented reasons sent this spell to narrate, and why.

    Order matters and is fixed: an explicit BUILTIN override is a deliberate
    decision and is reported as such, ahead of the flags. Reporting the flag
    first would blame the parser for a decision the GM's table made on purpose.
    """
    extra = builtin.get(key, {})
    detail = {"flags": spec.get("flags", [])}
    if extra.get("narrate"):
        detail["builtin"] = "narrate"
        return "builtin_forced_narrate", detail
    if extra.get("narrate_from_level"):
        detail["builtin"] = f"narrate_from_level={extra['narrate_from_level']}"
        return "builtin_level_narrate", detail
    flags = spec.get("flags", [])
    # `effect` is the "no anchor" case: build_srd writes it when a spell has no
    # attack, no save, no damage and no heal, which is a fact about the SPELL
    # rather than a parsing obstacle. It is checked first because
    # `tactics_spells` lists it among its blocking flags, and reporting 186
    # spells as "blocked by a flag" when what actually happened is "this spell
    # does not resolve to a number" would bury the larger finding under a
    # smaller one.
    if "effect" in flags:
        return "no_anchor", detail
    if "save_effect" in flags:
        return "save_nothing_to_apply", detail
    for flag in _BLOCKING:
        if flag in flags:
            detail["blocking"] = flag
            return "blocking_flag", detail
    return "no_anchor", detail


# --- monsters -----------------------------------------------------------------

#: An action the engine can run end to end.
_MECHANICAL_ACTIONS = ("attack", "save", "multiattack")


def _monster_rows(monsters, system):
    rows = []
    for record in monsters:
        index = record.get("index") or record.get("name", "")
        name = record.get("name", "")
        try:
            token = system.token_from_monster(record, f"cov-{index}", name, (0, 0))
        except (KeyError, TypeError, ValueError) as error:
            rows.append(_row("monster", index, name, "reference",
                             "action_unparsed", {"error": str(error)}))
            continue
        rows.append(_monster_row(record, index, name, token))
    return rows


def _monster_row(record, index, name, token):
    actions = record.get("actions") or []
    specs = {a["name"]: a for a in token.attacks}
    total = mechanical = 0
    reasons = []
    for action in actions:
        total += 1
        flags = set(action.get("flags") or [])
        kind = action.get("kind")
        if kind == "multiattack" and "unparsed" not in flags:
            mechanical += 1
            continue
        if kind == "save" and action.get("dc") and action.get("damage"):
            mechanical += 1
            continue
        if kind == "attack":
            spec = specs.get(action["name"], {})
            has_shape = ("reach" in spec or "range" in spec or not spec)
            has_damage = bool(spec.get("damage"))
            if has_shape and has_damage and "damage_unparsed" not in flags:
                mechanical += 1
                continue
            reasons.append("action_damage_unresolved")
            continue
        if "targeting" in flags:
            reasons.append("action_area_unknown")
        elif "unparsed" in flags or "rider" in flags:
            reasons.append("action_unparsed")
        else:
            reasons.append("action_unparsed")
    detail = {"actions": total, "mechanical_actions": mechanical}
    if mechanical == total and total:
        return _row("monster", index, name, "mechanical", detail=detail)
    detail["partial_reasons"] = sorted(set(reasons))
    if mechanical == 0:
        return _row("monster", index, name, "reference",
                    "no_structured_action" if not actions
                    else _dominant_reason(reasons), detail)
    return _row("monster", index, name, "reference", _dominant_reason(reasons), detail)


#: Most-common-first, with a fixed priority so two equally common reasons still
#: produce the same answer on every run. A monster's reported reason is the one
#: that explains most of its actions, not the first one the parser happened to
#: reach: "the Giant Spider's damage is a choice" is wrong and would hide that its
#: Web is prose the parser could not read.
_REASON_PRIORITY = ("action_damage_unresolved", "action_area_unknown",
                    "action_unparsed", "no_structured_action")


def _dominant_reason(reasons):
    counts = {r: reasons.count(r) for r in set(reasons)}
    return min(counts, key=lambda r: (-counts[r],
                                      _REASON_PRIORITY.index(r)
                                      if r in _REASON_PRIORITY else 99))


# --- conditions and exhaustion ------------------------------------------------

#: Keys of CONDITION_EFFECTS that `get_condition_modifiers` actually merges.
#: `note` is documentation and `harmful_to` is computed but read by no rule, so
#: neither counts as enforcement; both are counted as defects instead.
_ENFORCED_CONDITION_KEYS = ("attack_roll", "ability_check", "save",
                            "attack_against", "movement", "action_economy",
                            "hp_max", "death", "immunities",
                            "auto_crit_within_5")


def _DEVIATION_BY_CONDITION():
    """condition -> its deviation row, built once and cached.

    A function rather than a module constant because `DOCUMENTED_DEVIATIONS` is
    defined further down this file, after the per-record readers, and a forward
    reference here would be a NameError at import.
    """
    global _DEVIATION_INDEX
    if _DEVIATION_INDEX is None:
        _DEVIATION_INDEX = {d["condition"]: d for d in DOCUMENTED_DEVIATIONS}
    return _DEVIATION_INDEX


_DEVIATION_INDEX = None


def _condition_rows(conditions, system):
    rows = []
    for record in conditions:
        index = record.get("index") or ""
        name = record.get("name", "")
        if index == "exhaustion":
            rows.append(_row("condition", index, name, "mechanical",
                             detail={"note": "levels are their own axis"}))
            continue
        entry = getattr(system, "CONDITION_EFFECTS", {}).get(index)
        if entry is None:
            rows.append(_row("condition", index, name, "reference",
                             "no_table_entry"))
            continue
        enforced = [k for k in _ENFORCED_CONDITION_KEYS if k in entry]
        detail = {"enforced": sorted(enforced)}
        # A clause of the SRD text that the table does not resolve makes the
        # record `reference` even when the rest of it is applied. Scoring
        # petrified 100% while reporting its unenforced resistance clause
        # two sections apart would be two claims about one record, and only one
        # of them would be true.
        deviation = _DEVIATION_BY_CONDITION().get(index)
        open_clauses = [c["clause"] for c in IGNORED_CLAUSES
                        if c["condition"] == index and c.get("status") == "open"]
        if deviation:
            detail["deviation"] = deviation["status"]
            detail["srd"] = deviation["srd"]
            rows.append(_row("condition", index, name, "reference",
                             "documented_deviation", detail))
        elif open_clauses:
            detail["unenforced_clauses"] = open_clauses
            rows.append(_row("condition", index, name, "reference",
                             "clause_not_enforced", detail))
        elif enforced:
            rows.append(_row("condition", index, name, "mechanical", detail=detail))
        else:
            detail["declared"] = sorted(k for k in entry)
            rows.append(_row("condition", index, name, "reference",
                             "clause_not_enforced", detail))
    return rows


def _exhaustion_rows(system):
    """One row per exhaustion level, from PHB appendix A.

    A separate axis from conditions because "exhaustion 4 is 100% covered" and
    "charmed is 90% covered" are different claims about different rules, and
    folding them into one number would hide exactly the asymmetry worth seeing.
    """
    effects = getattr(system, "EXHAUSTION_EFFECTS", {}) or {}
    rows = []
    for level in range(1, 7):
        entry = effects.get(level)
        if not entry:
            rows.append(_row("exhaustion", str(level), f"Exhaustion {level}",
                             "reference", "clause_not_enforced"))
        else:
            rows.append(_row("exhaustion", str(level), f"Exhaustion {level}",
                             "mechanical", detail={"enforced": sorted(entry)}))
    return rows


# --- features and items -------------------------------------------------------


def _feature_rows(features, system, autopilot):
    """A feature is mechanical only if some engine rule acts on it.

    `slots._default_feature` is the one place a named feature changes behaviour
    (Arcane Recovery, and only for a wizard of the right level). Everything else
    is prose the engine reads from `lookup` and never applies -- and there the
    distinction that matters is whether the player is TOLD, which is what
    `UNSUPPORTED_FEATURES` decides.
    """
    rows = []
    listed = tuple(getattr(autopilot, "UNSUPPORTED_FEATURES", ()) or ()) if autopilot else ()
    for record in features:
        index = record.get("index") or ""
        name = record.get("name", "")
        key = str(name).strip().lower()
        if key == "arcane recovery":
            rows.append(_row("feature", index, name, "mechanical",
                             detail={"via": "slots._default_feature"}))
            continue
        if autopilot is None:
            rows.append(_row("feature", index, name, "unmeasured",
                             "dataset_absent",
                             {"note": "localdm.autopilot not importable"}))
            continue
        if any(key == f or key.startswith(f + " ") for f in listed):
            rows.append(_row("feature", index, name, "reference",
                             "unsupported_listed"))
        else:
            rows.append(_row("feature", index, name, "reference",
                             "unsupported_unlisted"))
    return rows


def _item_rows(equipment, magic_items, system):
    """Every item is reference: lookup prints it and no engine rule applies it.

    Reported per item so the count is falsifiable against a future change -- the
    first mechanical item is a one-line edit to this function and one green row
    that says so.
    """
    rows = []
    for record in equipment:
        rows.append(_row("item", record.get("index", ""), record.get("name", ""),
                         "reference", "reference_only",
                         {"kind": "equipment",
                          "attunement": bool(record.get("attunement"))}))
    for record in magic_items:
        rows.append(_row("item", record.get("index", ""), record.get("name", ""),
                         "reference", "reference_only",
                         {"kind": "magic_item",
                          "attunement": bool(record.get("attunement"))}))
    return rows

# ─── the defect counter ───────────────────────────────────────────────────────
#
# Four categories, target zero, and every entry carries the SRD 5.1 text or the
# code line that justifies it. That is deliberate: ROADMAP-ideas.md RI5 lists six
# condition clauses "resolved in the table and enforced by nothing", and three of
# them are not 2014 rules at all. Implementing those three would have introduced
# rules errors into a 2014 engine, so each claim below was checked against the
# SRD text in the dataset before anything was written. The refutations are
# recorded as REFUTED rather than quietly dropped, because "we checked and it is
# wrong" is the finding.

#: Clauses present in the SRD 5.1 condition text that the engine resolves
#: nowhere. Verified against `dnd5e_srd.json`'s own `conditions[].description`.
IGNORED_CLAUSES = (
    {"condition": "petrified", "clause": "resistance to all damage",
     "evidence": "SRD 5.1: \"The creature has resistance to all damage.\"",
     "why_a_defect": "CONDITION_EFFECTS['petrified'] carries immunities but no "
                     "resistance entry, and get_condition_modifiers() has no "
                     "resistance key, so nothing halves this creature's damage. "
                     "The SRD text is unambiguous and the engine applies the "
                     "other petrified clauses, so this is an omission rather "
                     "than a scope decision.",
     "status": "open"},
    {"condition": "unconscious", "clause": "is unaware of its surroundings",
     "evidence": "SRD 5.1: \"...is unaware of its surroundings.\"",
     "why_a_defect": "there is no awareness state on a token, so nothing "
                     "carries the clause. Its consequence in play is that an "
                     "unconscious target can be charmed or frightened without a "
                     "save, because those effects target a creature that is "
                     "aware. Reported rather than fixed: closing it needs a "
                     "decision about how awareness is modelled at all, which is "
                     "a design question rather than a data one.",
     "status": "open"},
)

#: Clauses attributed to the engine by ROADMAP-ideas.md RI5 that are NOT 2014
#: rules. Kept so the refutation is auditable and so nobody re-adds them from the
#: roadmap without reading this.
REFUTED_CLAUSES = (
    {"condition": "poisoned", "claimed": "an action restriction",
     "why_not": "SRD 5.1 says only \"A poisoned creature has disadvantage on "
                "attack rolls and ability checks.\" There is no action "
                "restriction to enforce.",
     "implementing_it_would": "have invented a rule"},
    {"condition": "incapacitated", "claimed": "cannot interact with objects",
     "why_not": "SRD 5.1 says only \"An incapacitated creature can't take "
                "actions or reactions.\" A free object interaction is a house "
                "rule, not a 2014 clause.",
     "implementing_it_would": "have encoded a house rule as a PHB rule"},
    {"condition": "invisible", "claimed": "behaves as blinded",
     "why_not": "SRD 5.1: \"For the purpose of hiding, the creature is heavily "
                "obscured\", plus advantage on its own attacks and "
                "disadvantage against it. Heavily obscured is not blinded, and "
                "treating it as blinded would invert the creature's own attack "
                "rolls.",
     "implementing_it_would": "have made invisible creatures worse at "
                              "attacking, the opposite of the rule"},
)

#: Deliberate deviations from 2014 that the code documents. Counted as neither a
#: defect nor compliance: they are a user's call, and the report says so instead
#: of quietly scoring them.
DOCUMENTED_DEVIATIONS = (
    {"condition": "charmed", "srd": "a charmed creature can't attack or "
     "target its charmer with harmful abilities or magical effects",
     "engine": "the attack is made at disadvantage against the charmer rather "
     "than refused",
     "where": "systems/dnd5e/tactics_rules.py, the comment above "
              "CONDITION_EFFECTS, and `harmful_to` in the charmed entry",
     "status": "documented house ruling; needs a user decision"},
)


def _assumption_defects(spells, monsters, system):
    """Fields and flags the build writes that no rule reads.

    An assumption nobody checks is the quiet kind of wrong: it looks like data,
    and `width_assumed` in particular is a width this parser invented for five
    spells where the SRD prints none.
    """
    out = []
    widths = [r for r in spells
              if "width_assumed" in (r.get("mechanics", {}).get("flags") or [])]
    if widths:
        out.append({
            "id": "width_assumed",
            "what": "a line spell's width, defaulted to 5 ft",
            "where": "systems/dnd5e/build_srd.py `_spell_mechanics`",
            "records": len(widths),
            "examples": [r.get("name") for r in widths[:5]],
            "why_a_defect": "the SRD prints no width for these, so the number is "
                            "the parser's, not the rules', and nothing reads the "
                            "flag to say so",
        })
    entry = getattr(system, "CONDITION_EFFECTS", {}).get("charmed") or {}
    if entry.get("harmful_to"):
        out.append({
            "id": "harmful_to",
            "what": "the creatures a charmed creature cannot harm",
            "where": "systems/dnd5e/tactics_rules.py `harmful_to`, merged into "
                     "get_condition_modifiers() on every call",
            "records": 1,
            "examples": entry["harmful_to"],
            "why_a_defect": "computed for every conditioned token and read by no "
                            "rule, so the field exists and nothing enforces it",
        })
    if "damage_choice_upcast" in _all_spell_flags(spells):
        out.append({
            "id": "damage_choice_upcast",
            "what": "an either-or upcast damage value, kept verbatim",
            "where": "systems/dnd5e/build_srd.py, spell `mechanics.damage`",
            "records": sum(1 for r in spells
                           if "damage_choice_upcast" in (r.get("mechanics", {}).get("flags") or [])),
            "examples": ["Flame Strike"],
            "why_a_defect": "correct to leave the caster's choice unresolved, but "
                            "nothing consumes the value, so the report is the "
                            "only reader",
        })
    return out


def _all_spell_flags(spells):
    for record in spells:
        yield from (record.get("mechanics", {}).get("flags") or [])


def _discarded_metadata(monsters, system):
    """Data parsed out of upstream that reaches neither record nor token.

    Cross-checked by walking record -> token rather than by reading the builder,
    so a key that was added to the token in #136 reads as present rather than as
    still-lost.
    """
    out = []
    totals = {"rider_damage": 0, "damage_choice": 0, "hp_dice": 0,
              "size": 0, "languages": 0}
    lost = {"rider_damage": [], "damage_choice": []}
    for record in monsters:
        token = system.token_from_monster(record, "cov", record.get("name", ""), (0, 0))
        specs = {a["name"]: a for a in token.attacks}
        for action in record.get("actions") or []:
            spec = specs.get(action["name"], {})
            for key in ("rider_damage", "damage_choice"):
                if action.get(key):
                    totals[key] += 1
                    if not spec.get(key):
                        lost[key].append(f"{record.get('name')} {action['name']}")
    for key, examples in lost.items():
        if examples:
            out.append({"id": key, "records": len(examples),
                        "examples": examples[:5],
                        "why_a_defect": "in the SRD record and on no attack spec"})
    # Parsed onto the monster but never carried onto the token.
    for key in ("hp_dice", "size", "languages"):
        if totals[key]:
            out.append({"id": key, "records": totals[key], "examples": [],
                        "why_a_defect": "parsed onto the record and never read by "
                                        "an engine rule"})
    return out


def _unsupported_declarations(features, autopilot):
    """Features the engine will not apply AND does not tell the player about.

    `UNSUPPORTED_FEATURES` is the right design: it names the features the engine
    declines so a player who says "I use Evasion" is told. The defect is the long
    tail past that list, where the player gets an ordinary action and no note at
    all -- the exact failure the deny-list exists to prevent.
    """
    if autopilot is None:
        return [{"id": "unsupported_unlisted", "records": None, "examples": [],
                 "why_a_defect": "localdm.autopilot not importable, so the "
                                 "deny-list could not be read"}]
    listed = tuple(getattr(autopilot, "UNSUPPORTED_FEATURES", ()) or ())
    unlisted = []
    for record in features:
        name = str(record.get("name", "")).strip().lower()
        if name == "arcane recovery":
            continue
        if not any(name == f or name.startswith(f + " ") for f in listed):
            unlisted.append(record.get("name"))
    if not unlisted:
        return []
    return [{"id": "unsupported_unlisted", "records": len(unlisted),
             "examples": unlisted[:8],
             "why_a_defect": "the engine applies nothing here and the player is "
                             "not told, so a named feature silently becomes an "
                             "ordinary action"}]


def defect_report(spells, monsters, features, system, autopilot):
    """The zero-target counter, next to and never merged with the percentage."""
    assumptions = _assumption_defects(spells, monsters, system)
    discarded = _discarded_metadata(monsters, system)
    ignored = [dict(clause, category="ignored_clauses")
               for clause in IGNORED_CLAUSES if clause.get("status") == "open"]
    unsupported = _unsupported_declarations(features, autopilot)
    categories = {
        "assumptions_without_consumers": assumptions,
        "discarded_metadata": discarded,
        "ignored_clauses": ignored,
        "unsupported_declarations": unsupported,
    }
    return {
        "target": 0,
        "total": sum(len(v) for v in categories.values()),
        "categories": {k: len(v) for k, v in categories.items()},
        "entries": categories,
        "documented_deviations": list(DOCUMENTED_DEVIATIONS),
        "refuted_claims": list(REFUTED_CLAUSES),
    }


# ─── assembly ─────────────────────────────────────────────────────────────────

#: Every axis the report covers, in the order the issue names them.
CATEGORIES = ("spell", "condition", "exhaustion", "feature", "monster", "item")

#: Axis labels for the text report. Plural on the category, not by appending an
#: "s", because "exhaustions" is not a word.
_LABELS = {"spell": "spells", "condition": "conditions", "exhaustion": "exhaustion",
           "feature": "features", "monster": "monsters", "item": "items"}


def build_report(data_file: pathlib.Path = DEFAULT_DATA,
                 conditions: dict | None = None) -> dict:
    """The whole report, measured or unmeasured.

    The unmeasured path is a first-class branch rather than a fallback at the
    end: with no dataset there is nothing to count, so `summary` holds nulls,
    not zeroes, and every row says why.
    """
    measured = dict(MEASUREMENT_CONDITIONS)
    measured.update(conditions or {})
    data, provenance = load_dataset(data_file)
    if data is None:
        return _unmeasured_report(provenance, measured)

    system, spells_mod = _engine()
    autopilot = _autopilot()

    spells = data.get("spells") or []
    monsters = data.get("monsters") or []
    features = data.get("features") or []
    condition_records = data.get("conditions") or []
    equipment = data.get("equipment") or []
    magic_items = data.get("magic_items") or []

    # A category the file does not carry at all is not a category with zero
    # records -- it is a category nobody measured, and the two must not print the
    # same way. `build_srd.py --no-fvtt` writes a dataset with no `features` key
    # at all, and reporting that as "0 of 0 features, 100% covered" would turn a
    # missing upstream fetch into a success. Exhaustion is exempt: it is code
    # rather than data, so it is always measurable.
    missing = [c for c in ("spells", "monsters", "features", "conditions",
                           "equipment", "magic_items") if c not in data]

    records = []
    if "spells" in data:
        records += _spell_rows(spells, system, spells_mod, measured)
    if "conditions" in data:
        records += _condition_rows(condition_records, system)
    records += _exhaustion_rows(system)
    if "features" in data:
        records += _feature_rows(features, system, autopilot)
    if "monsters" in data:
        records += _monster_rows(monsters, system)
    if "equipment" in data or "magic_items" in data:
        records += _item_rows(equipment, magic_items, system)

    return {
        "schema": SCHEMA_VERSION,
        "state": "measured",
        "provenance": provenance,
        "measurement_conditions": measured,
        "missing_categories": missing,
        "summary": _summary(records, missing),
        "defects": defect_report(spells, monsters, features, system, autopilot),
        "records": records,
    }


def _unmeasured_report(provenance, measured=None):
    """319 spells and 334 monsters, all `unmeasured`, and every count null.

    Not zero. A zero would be indistinguishable from "the engine implements
    nothing", and that is a claim this report is not entitled to make without a
    dataset behind it.
    """
    # The counts are the SRD's as published (5e-bits 5e-srd-api, SRD 5.1) and
    # are what a reader needs in order to know what was NOT looked at. They are
    # not measurements: nothing was opened. They are the denominator a future
    # measured report will be compared against.
    categories = {
        "spell": ("spells", 319),
        "condition": ("conditions", 15),
        "exhaustion": ("exhaustion", 6),
        "feature": ("features", 260),
        "monster": ("monsters", 334),
        "item": ("items", 599),
    }
    records = []
    summary = {}
    for category, (label, nominal) in categories.items():
        for i in range(nominal):
            records.append(_row(category, f"{category}-{i}", "", "unmeasured",
                                "dataset_absent"))
        summary[category] = {"label": label, "total": nominal,
                             "mechanical": None, "reference": None,
                             "unmeasured": nominal, "coverage_percent": None}
    return {
        "schema": SCHEMA_VERSION,
        "state": "unmeasured",
        "unmeasured_reason": provenance,
        "provenance": provenance,
        "measurement_conditions": measured or MEASUREMENT_CONDITIONS,
        "missing_categories": [c for c in
                               ("spells", "monsters", "features", "conditions",
                                "equipment", "magic_items")],
        "summary": summary,
        "defects": None,
        "defects_note": "not counted: a defect total computed with no dataset "
                        "would be an assertion about data nobody looked at",
        "records": records,
    }


def _summary(records, missing=()):
    summary = {}
    for category in CATEGORIES:
        rows = [r for r in records if r["category"] == category]
        key = {"spell": "spells", "condition": "conditions", "feature": "features",
               "monster": "monsters"}[category] if category in (
                   "spell", "condition", "feature", "monster") else None
        if key and key in missing:
            summary[category] = {
                "label": _LABELS[category], "total": None, "mechanical": None,
                "reference": None, "unmeasured": None, "coverage_percent": None,
                "reason": "dataset_absent",
                "note": f"the dataset has no {key!r} key; --no-fvtt builds one "
                        "without features",
            }
            continue
        total = len(rows)
        mechanical = sum(1 for r in rows if r["state"] == "mechanical")
        reference = sum(1 for r in rows if r["state"] == "reference")
        unmeasured = sum(1 for r in rows if r["state"] == "unmeasured")
        reasons: dict[str, int] = {}
        for row in rows:
            if row["reason"]:
                key = row["reason"]
                if row.get("detail", {}).get("blocking"):
                    key = f"blocking_flag:{row['detail']['blocking']}"
                reasons[key] = reasons.get(key, 0) + 1
        summary[category] = {
            "label": _LABELS[category],
            "total": total,
            "mechanical": mechanical,
            "reference": reference,
            "unmeasured": unmeasured,
            "coverage_percent": round(100.0 * mechanical / total, 1) if total else None,
            "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        }
    return summary


# ─── text output ──────────────────────────────────────────────────────────────


def render(report: dict) -> str:
    out = []
    provenance = report.get("provenance") or {}
    if report["state"] == "unmeasured":
        out.append("RULES COVERAGE -- UNMEASURED")
        out.append("")
        out.append(f"  {report['unmeasured_reason']}")
        out.append("  Nothing was counted. Every record below is `unmeasured`,")
        out.append("  and every coverage figure is null rather than zero, because")
        out.append('  "the engine implements nothing" and "nothing was measured"')
        out.append("  are different claims and only one of them is honest.")
        out.append("")
        out.append("  Build it with: python3 systems/dnd5e/build_srd.py")
        out.append("  Measure a fixture with: --data tests/fixtures/<file>.json")
        out.append("")
        for category, stats in report["summary"].items():
            out.append(f"  {stats['label']:<20} {stats['unmeasured']:>5}  unmeasured")
        return "\n".join(out)

    out.append("RULES COVERAGE -- measured")
    out.append(f"  dataset   {provenance.get('path')}")
    out.append(f"  built     {provenance.get('built_at')}")
    out.append(f"  edition   {provenance.get('edition')}")
    conds = report.get("measurement_conditions") or {}
    if conds:
        out.append(f"  measured  against a level {conds.get('caster_level')} caster "
                   f"(spell DC {conds.get('spell_save_dc')}, "
                   f"attack +{conds.get('spell_attack')})")
    for name, info in (provenance.get("sources") or {}).items():
        out.append(f"  source    {name}: {info.get('repo')} @ "
                   f"{(info.get('sha') or '?')[:12]}")
    out.append("")
    out.append(f"  {'axis':<16}{'total':>7}{'mech':>7}{'ref':>7}{'unmeas':>8}{'cover':>9}")
    for category, stats in report["summary"].items():
        if stats["total"] is None:
            out.append(f"  {stats['label']:<16}{'unmeasured (category absent)':>38}")
            continue
        out.append(f"  {stats['label']:<16}{stats['total']:>7}{stats['mechanical']:>7}"
                   f"{stats['reference']:>7}{stats['unmeasured']:>8}"
                   f"{stats['coverage_percent']!s:>8}%")
    out.append("")
    out.append("FALLBACK REASONS (why a record is reference, not mechanical)")
    for category, stats in report["summary"].items():
        if stats.get("reasons"):
            out.append(f"  {category}:")
            for reason, count in stats["reasons"].items():
                out.append(f"    {count:>5}  {reason:<34} {REASONS[reason.split(':')[0]]}")
    defects = report.get("defects") or {}
    out.append("")
    out.append(f"DEFECTS (target {defects.get('target')}) -- total {defects.get('total')}")
    for name, count in (defects.get("categories") or {}).items():
        out.append(f"    {name:<32} {count:>4}")
    for entry in (defects.get("entries") or {}).get("ignored_clauses", []):
        out.append(f"      ! {entry['condition']}: {entry['clause']}")
        out.append(f"        {entry['evidence']}")
    for entry in (defects.get("refuted_claims") or []):
        out.append(f"      x {entry['condition']}: {entry['claimed']} -- REFUTED")
        out.append(f"        {entry['why_not']}")
    for entry in (defects.get("documented_deviations") or []):
        out.append(f"      ? {entry['condition']}: {entry['status']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=pathlib.Path, default=DEFAULT_DATA,
                        help="dataset to measure (default: the built SRD)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable per-record output")
    parser.add_argument("--category", choices=CATEGORIES, action="append",
                        help="restrict --json to one or more axes")
    parser.add_argument("--caster-level", type=int, default=None,
                        help="character level every spell is measured against "
                             f"(default {MEASUREMENT_CONDITIONS['caster_level']})")
    args = parser.parse_args(argv)

    conditions = None
    if args.caster_level is not None:
        conditions = dict(MEASUREMENT_CONDITIONS,
                          caster_level=max(1, args.caster_level))
    report = build_report(args.data, conditions)
    if args.json:
        if args.category:
            wanted = set(args.category)
            report["records"] = [r for r in report["records"]
                                 if r["category"] in wanted]
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
