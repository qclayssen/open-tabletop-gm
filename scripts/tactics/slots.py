"""slots.py: spell slots — what is left, what a cast costs, what a rest gives back.

    read(token)            {1: {"total": 2, "used": 0}, ...}, normalised
    check(token, level)    raise CombatError unless a slot of that level is left
    spend(token, level)    spend one, or raise
    lowest_with(token, 1)  the cheapest slot level with one left (a reaction)
    restore_all(token)     every slot back to full (a long rest)
    summary(token)         "Spell slots: 1st: 2/4, 2nd: 1/3"
    short_rest(token)      the class's short-rest recovery, as GM-facing lines
    long_rest(token)       the same for a long rest

Where the data lives, and why it is not a Token field. A caster's slots are in
``token.extra["slots"]``::

    {"1": {"total": 2, "used": 0}, "2": {"total": 3, "used": 1}}

That shape is not this engine's to change. The character sheet has a Spell Slots
table, ``tactics_sheet`` parses it and writes it back, ``sync`` hands it to the
display, and the display's sidebar draws it as pips that drain and refill. A
first-class ``Token.spell_slots`` field would rename the same value in five
modules and migrate every live ``encounter.json`` without making any of them
more correct, and two names for one number is how a sheet and a sidebar end up
disagreeing. So the contract is kept and made explicit instead: this module is
the only place that knows what a slot is, ``schemas.SPELL_SLOTS`` is the type it
has to satisfy, and ``Token.spell_slots`` is the typed way to reach it.

``used`` and ``total``, not ``remaining`` and ``max``, because that is the
column order the character sheet's own table is in and the GM who edits it
mid-session should not have to learn a second one. Older files that used the
display's ``{"remaining": n, "max": n}`` are normalised by the v2 -> v3
migration in ``state.py`` on load.

Levels are string keys. JSON has no integer keys, and the sheet's table and the
display's payload both name a level as a string, so the file is written once in
the shape it is read in. Every helper here takes and returns ints, and the one
place that writes the file converts.

Rules here are the SRD's, and only the ones the engine can decide on its own.
Warlock Pact Magic all refills on a short rest and Wizard Arcane Recovery gives
back up to half the caster's level in slot levels once per long rest (PHB p107,
p112). Sorcery Points are not spell slots — they are spent to *make* slots — so
a Sorcerer's short rest is reported to the GM and the conversion is left to
them, the same as any other ruling the engine cannot make.
"""

from __future__ import annotations

from .core import CombatError

__all__ = [
    "cast_class",
    "cast_level",
    "check",
    "has_feature",
    "left_levels",
    "long_rest",
    "lowest_with",
    "ordinal",
    "read",
    "remaining",
    "restore_all",
    "short_rest",
    "spend",
    "summary",
    "write",
]

# Slots of every class the 5.1 SRD has a table for. A token whose class is not
# in here gets the engine's default behaviour (a long rest refills everything,
# a short rest does nothing), which is the right answer for a class this SRD
# does not cover rather than a wrong one.
FULL_CASTERS = ("bard", "cleric", "druid", "sorcerer", "wizard")
PACT_CLASSES = ("warlock",)
ARCANE_RECOVERY_LEVEL = 2       # a Wizard feature, so 2nd level and up (PHB p112)


# ─── shape ────────────────────────────────────────────────────────────────────

def _one(value, level) -> dict:
    """One slot entry, whatever loose shape it arrived in, as {"total","used"}.

    Three shapes are in the wild: the engine's own, the display's legacy
    ``{"remaining", "max"}`` (see CHANGELOG 0.7.0), and a bare number that a
    hand-edited file reduced the level to. A slot count below zero, or more
    spent than exist, is a file that was edited while the engine was not looking;
    it is clamped here rather than refused, because refusing would cost the GM a
    running fight over a typo.
    """
    total, used = 0, 0
    if isinstance(value, dict):
        if "total" in value or "used" in value:
            total, used = value.get("total"), value.get("used")
        elif "max" in value or "remaining" in value:
            total, remaining = value.get("max"), value.get("remaining")
            used = (total - remaining) if _num(total) is not None and _num(remaining) is not None else 0
    elif _num(value) is not None:
        total = value
    total = max(0, _num(total) or 0)
    used = max(0, _num(used) or 0)
    return {"total": total, "used": min(used, total)}


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read(token) -> dict:
    """The token's slots as {int level: {"total": n, "used": n}}, ascending.

    Always returns a fresh dict of fresh dicts: callers mutate what they get
    back (a spend is a mutation), and handing out the stored dicts would let a
    caller change a slot count without going through spend().
    """
    raw = (token.extra or {}).get("slots")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for level, value in raw.items():
        n = _num(level)
        if n is None or n < 1:
            continue
        entry = _one(value, n)
        if entry["total"] or entry["used"]:
            out[n] = entry
    return {lv: out[lv] for lv in sorted(out)}


def write(token, slots: dict) -> None:
    """Store {int level: {"total","used"}} back onto the token, canonically."""
    token.extra.setdefault("slots", {})
    clean = {str(lv): _one(entry, lv) for lv, entry in slots.items() if _num(lv) and int(lv) > 0}
    token.extra["slots"] = {k: clean[k] for k in sorted(clean, key=int)}


def left_levels(token) -> list:
    """The slot levels with one left, ascending. "What can this creature still cast."""
    return [lv for lv, s in read(token).items() if s["used"] < s["total"]]


def remaining(token, level) -> int:
    """Slots of `level` still unspent."""
    entry = read(token).get(_num(level) or 0, {"total": 0, "used": 0})
    return entry["total"] - entry["used"]


# ─── spending ─────────────────────────────────────────────────────────────────

def check(token, level) -> None:
    """Raise CombatError unless a slot of exactly `level` is left.

    A cantrip (level 0) never reaches here, and neither does a monster's innate
    spell: the caller only asks about a slot when the spell has one. The
    message names the levels that are left, because "no 3rd level slot" with
    nothing else is a question the GM then has to go and answer by hand.
    """
    slots = read(token)
    entry = slots.get(_num(level) or 0)
    if entry and entry["used"] < entry["total"]:
        return
    if not slots:
        raise CombatError(f"{token.name} has no spell slots (no casting class or level "
                          f"on the sheet: `adjust` cannot invent them).")
    left = left_levels(token)
    raise CombatError(f"{token.name} has no level {_num(level) or level} slot left"
                      + (f" (left: level {', '.join(str(l) for l in left)})." if left else "."))


def spend(token, level) -> None:
    """Spend one slot of `level`, or raise. Every cast goes through here."""
    check(token, level)
    stored = token.extra["slots"][str(_num(level))]
    stored["used"] = stored.get("used", 0) + 1


def lowest_with(token, level: int = 1):
    """The cheapest slot level >= `level` with one left, or None.

    A reaction (Shield, Silvery Barbs) spends the least valuable slot that will
    do, which is the Sage Advice reading and the reason a wizard with a full
    3rd-level slot left does not burn it to block a crossbow bolt.
    """
    for lv, entry in read(token).items():
        if lv >= level and entry["used"] < entry["total"]:
            return str(lv)
    return None


def restore_all(token) -> int:
    """Every slot back to full. Returns how many slots were restored."""
    slots = read(token)
    spent = sum(s["used"] for s in slots.values())
    write(token, {lv: {"total": s["total"], "used": 0} for lv, s in slots.items()})
    return spent


# ─── the caster ───────────────────────────────────────────────────────────────

def cast_class(token) -> str:
    """The token's casting class, lowercased, or ''.

    Read from ``extra["spellcasting"]["class"]``, which is where the sheet
    reader puts it, falling back to a bare ``extra["class"]`` for a token a GM
    built by hand. The first word that names a caster is the class: sheets
    write "**Class:** Wizard 1 (Chronurgy at 2)" and "Rogue 3 / Sorcerer 2", and
    the level lives on its own line.
    """
    raw = (token.extra or {}).get("spellcasting")
    if not isinstance(raw, dict):
        raw = {"class": (token.extra or {}).get("class")}
    return _first_class(raw.get("class"))


def _first_class(name) -> str:
    text = str(name or "").lower()
    for word in text.replace("/", " ").replace("(", " ").split():
        word = word.strip(".,:;")
        if word in FULL_CASTERS + PACT_CLASSES or word in ("paladin", "ranger"):
            return word
    return ""


def cast_level(token) -> int:
    """The caster's character level, or 0."""
    level = (token.extra or {}).get("level")
    n = _num(level)
    return n if n and n > 0 else 0


def has_feature(token, name: str) -> bool:
    """Is a named class feature on the token? Reads extra["features"].

    The sheet's "## Features & Traits" section is read into that list as feature
    names, so a homegame wizard who moved Arcane Recovery is believed over the
    table. A sheet that lists no features at all falls back to the SRD level
    for the features the engine acts on, so an unfinished sheet still works.
    """
    features = (token.extra or {}).get("features")
    if not isinstance(features, list):
        return False
    return any(name.lower() in str(f).lower() for f in features)


def _default_feature(token, name: str) -> bool:
    """The SRD reading, used when the sheet does not list its features."""
    if name == "arcane recovery":
        return cast_class(token) == "wizard" and cast_level(token) >= ARCANE_RECOVERY_LEVEL
    return False


# ─── display ──────────────────────────────────────────────────────────────────

_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def ordinal(n) -> str:
    """1 -> "1st", 2 -> "2nd", 11 -> "11th", 21 -> "21st"."""
    n = _num(n) or 0
    if 10 <= abs(n) % 100 <= 20:
        return f"{n}th"
    return f"{n}{_SUFFIX.get(abs(n) % 10, 'th')}"


def summary(token) -> str:
    """``Spell slots: 1st: 2/4, 2nd: 1/3`` — the levels remaining, of the total.

    Remaining, not spent, because that is how a player reads "2/4" at the table
    and it is what the pips on the display are showing them. The GM who wants
    the spent count has the sheet.
    """
    slots = read(token)
    if not slots:
        return ""
    return "Spell slots: " + ", ".join(
        f"{ordinal(lv)}: {s['total'] - s['used']}/{s['total']}" for lv, s in slots.items())


def spent_summary(token) -> str:
    """The same levels counted as spent — what `end` writes into the session log."""
    slots = read(token)
    used = ", ".join(f"level {lv} {s['used']}/{s['total']}"
                     for lv, s in slots.items() if s["used"])
    return used


# ─── rest ─────────────────────────────────────────────────────────────────────

def _levels_word(numbers) -> str:
    """[1, 1, 3] -> "2x 1st, 1x 3rd" — how many slots of each level, not their total.

    The total is the number that matters for the PHB p112 ceiling, and that is
    already in the sentence, so what the GM needs here is the split: "recovered
    2x 1st" says which slots to put back on the sheet, "recovered 3 slot levels"
    does not.
    """
    counts = {}
    for n in numbers:
        counts[n] = counts.get(n, 0) + 1
    return ", ".join(f"{n}x {ordinal(lv)}" for lv, n in sorted(counts.items()))


def _restore(token, budget: int) -> list:
    """Spend `budget` slot levels on the cheapest spent slots. Returns them.

    Cheapest first is the reading a GM expects and the one that costs the caster
    least: a level 5 wizard who has burned a 1st and a 3rd gets the 1st back,
    because the 3rd is worth more and the rule only sets a ceiling.
    """
    slots = read(token)
    restored, budget_left = [], budget
    for lv in sorted(slots):
        entry = slots[lv]
        while entry["used"] > 0 and budget_left >= lv:
            entry["used"] -= 1
            budget_left -= lv
            restored.append(lv)
    if restored:
        write(token, slots)
    return restored


def long_rest(token) -> list[str]:
    """Everything a long rest gives back that is a spell slot (PHB p201)."""
    lines = []
    spent = restore_all(token)
    if spent:
        lines.append(f"{token.name}: all spell slots restored.")
    if cast_class(token) == "wizard" and token.extra.get("arcane_recovery_used"):
        token.extra["arcane_recovery_used"] = False
        lines.append(f"{token.name}: Arcane Recovery is available again.")
    return lines


def short_rest(token) -> list[str]:
    """The class's short-rest recovery, as lines for the GM. [] if there is none.

    Only two classes have one in the SRD, and they are different kinds of thing:
    Pact Magic refills by itself because the feature says so (PHB p107), while
    Arcane Recovery spends a once-per-long-rest resource to buy slots back
    (PHB p112). Everything else is told what it could do rather than having it
    done, because the decision of *which* slots to recover is the caster's and
    the engine does not make that call for a player.
    """
    if not read(token):
        return []
    cls, level = cast_class(token), cast_level(token)
    if cls in PACT_CLASSES:
        spent = restore_all(token)
        if spent:
            return [f"{token.name}: Pact Magic slots restored ({spent} back)."]
        return []
    if cls == "wizard" and _has(token, "arcane recovery"):
        budget = (level + 1) // 2
        if token.extra.get("arcane_recovery_used"):
            return [f"{token.name}: Arcane Recovery is already used today."]
        if budget <= 0:
            return [f"{token.name}: no Arcane Recovery slots to recover."]
        if not spent_summary(token):
            return [f"{token.name}: Arcane Recovery available, but no slots are spent."]
        restored = _restore(token, budget)
        if restored:
            token.extra["arcane_recovery_used"] = True
            return [f"{token.name}: Arcane Recovery recovered {_levels_word(restored)} "
                    f"(up to {budget} slot levels, PHB p112)."]
        return [f"{token.name}: Arcane Recovery could not recover a slot "
                f"(up to {budget} slot levels)."]
    if cls == "sorcerer":
        points = token.extra.get("sorcery_points")
        if isinstance(points, dict):
            return [f"{token.name}: {points.get('available', 0)} Sorcery Points available. "
                    f"They are spent to create slots, not to restore them: the GM rules "
                    f"the conversion (1 point buys a slot of the caster's level, more for "
                    f"higher ones)."]
    return []


def _has(token, name: str) -> bool:
    return has_feature(token, name) or _default_feature(token, name)
