"""tactics_sheet.py: D&D 5e character sheet <-> combat token.

Reads the markdown sheet the skill keeps (templates/character-sheet.md shape)
into a Token, and writes combat results back into it: HP, temp HP, spent
spell slots, hit dice, death saves and lasting conditions. Everything else in
the sheet is left byte for byte as it was.

WHERE A NUMBER CAME FROM
========================

A parser that cannot find a field has three honest answers and one dishonest one,
and this module now tells them apart (issue #138).

  stated     the sheet says it. Authoritative.
  derived    computed from something the sheet does say (a saving throw from an
             ability score, a DEX modifier from a DEX score). Authoritative.
  default    the sheet is silent and a documented fallback was applied. The
             number is real, it is being used to compute hit chances and movement
             budgets, and nobody has been told. `DEFAULTS` below says which
             fallbacks are CONSEQUENTIAL and why, and `explain()` renders that for a
             GM.
  missing    the sheet does not say and there is no defensible number. `None`,
             and the consumers that matter already refuse with a message naming
             the character (`tactics_spells.resolve` on a missing spell DC).

The dishonest one was a fourth: a field that is PRESENT but unreadable -- "TBD",
"---", a stray dash in a blank template -- was parsed as its fallback, so a
half-filled sheet produced the same authoritative-looking token as a finished one.
`_required_int` now refuses that case with the field, the sheet and the text that
could not be read. Absent is not malformed, so an absent field still falls back; a
present-and-unreadable one does not.
"""

from __future__ import annotations

import pathlib
import re
import sys

_SCRIPTS = str(pathlib.Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from tactics.roller import split_crit  # noqa: E402
from tactics.state import Token  # noqa: E402

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
# Kept on the sheet after combat. Others (prone, grappled, restrained,
# frightened, unconscious, ...) end with the fight. Poisoned, cursed and the
# like are kept only while a duration remains (token.extra["durations"]).
ALWAYS_LASTING = {"exhaustion", "exhausted"}

_HP = re.compile(r"(\*\*HP:\*\*\s*)(\d+)\s*/\s*(\d+)")
_TEMP = re.compile(r"(\*\*Temp HP:\*\*\s*)(\d+)")
_DEATH = re.compile(r"(\*\*Death Saves:\*\*\s*Successes:\s*)(\d+)(\s*\|\s*Failures:\s*)(\d+)")
_HIT_DICE = re.compile(r"^(.*?\*\*Hit Dice:\*\*[ \t]*)(.*)$", re.M)
_HD_TERM = re.compile(r"(\d+)\s*d\s*(\d+)", re.I)
_HD_REMAINING = re.compile(r"remaining:\s*(\d+)", re.I)
_HD_FRACTION = re.compile(r"^(\d+)\s*/\s*(\d+)\s*d\s*(\d+)", re.I)
_CONDITIONS = re.compile(r"^- \*\*Conditions:\*\*.*$", re.M)
_SLOT_ROW = re.compile(r"^(\|\s*(\d+)\w*\s*\|\s*\d+\s*\|\s*)(\d+)(\s*\|)", re.M)
_FEATURES = re.compile(r"^###\s+(.+?)\s*$", re.M)


def _slots_module():
    """spell_slots.py, the SRD's caster tables, loaded the way tactics_rules
    loads its siblings (an explicit name, so two copies cannot diverge)."""
    import importlib.util
    import sys
    name = "spell_slots_dnd5e"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, pathlib.Path(__file__).with_name("spell_slots.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def _section(text: str, title: str) -> str:
    m = re.search(rf"^##\s+{re.escape(title)}.*?$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def _table_rows(block: str) -> list:
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def _int(text: str, default: int = 0) -> int:
    m = re.search(r"[+-]?\d+", text or "")
    return int(m.group(0)) if m else default


def _field(text: str, label: str) -> str:
    """The text after a `**Label:**`, up to the bar or the end of the line.

    `[ \t]*` and not `\s*`, deliberately: `\s*` eats a newline, so a `**Speed:**` at
    the end of a line with nothing after it read the NEXT line instead and returned
    "1d6 (remaining: 1)", which `_int` turned into a speed of 1. That is the same
    failure this module's header describes -- a number that was never on the sheet
    arriving as an authoritative one -- reached from the other direction, and it only
    showed up once the provenance work made the fields visible.
    """
    m = re.search(rf"\*\*{re.escape(label)}:\*\*[ \t]*([^|\n]*)", text)
    return m.group(1).strip() if m else ""


# ── the fallback table: which numbers change an outcome, and why ──────────────
#
# `consequential` is the whole question #138 asks. A default that only changes a
# label does not need explaining; one that changes a die roll does. The `why` is
# written to be read by a GM in `explain()` output, so it says what the number is
# used for rather than what it is.
#
# `None` in the fallback column means "no number is defensible", and every consumer
# that matters already refuses rather than substituting one.
DEFAULTS = {
    "ac": (10, True,
           "the sheet has no AC line; 10 is an unarmoured creature, and every attack roll "
           "against this creature is computed from it"),
    "speed": (30, True,
              "the sheet has no Speed line; 30 ft is a humanoid walk, and the whole "
              "movement budget for a turn comes from it"),
    "level": (1, True,
              "the sheet has no Level line; level 1 sets proficiency, the spell-slot table "
              "and the hit dice"),
    "dex_mod": (0, True,
                "there is no readable DEX score; +0 changes Dex saves and Mage Armor"),
    "saves": ({}, True,
              "neither a Saving Throws table nor an Ability Scores table was readable; "
              "every save is rolled at +0"),
    "attack_bonus": (0, True,
                     "the Attacks row's bonus column was empty; +0 is a real attack roll, "
                     "not a missing one"),
    "spell_dc": (None, False,
                 "no spell save DC on the sheet; tactics_spells.resolve already refuses "
                 "with the caster's name rather than guessing one"),
    "spell_attack": (None, False,
                     "no spell attack bonus on the sheet; tactics_spells.resolve refuses "
                     "the same way"),
    "passive_perception": (None, False,
                           "no passive score on the sheet; checks.decide only reads one "
                           "the sheet states"),
    "temp_hp": (0, False, "0 is the truth about a character with no temporary hit points"),
    "hit_dice": (None, False,
                 "the sheet has no Hit Dice line; a long rest asks rather than assuming"),
}

#: The three provenance values a number in `extra["derived"]` can carry.
STATED, DERIVED, DEFAULT = "stated", "derived", "default"


def _required_int(text: str, label: str, sheet: str, default, field: str) -> tuple:
    """`(value, provenance)` for one `**Label:**` field.

    Absent falls back and says so. Present-but-unreadable refuses, because the only
    alternative is a fallback number wearing the same authority as a stated one, and
    that is the failure #138 is about. The message names the sheet, the field and the
    text, so the fix is a keystroke rather than an investigation.
    """
    raw = _field(text, label)
    if not raw:
        return default, DEFAULT
    m = re.search(r"[+-]?\d+", raw)
    if not m:
        raise ValueError(
            f"{sheet}: the **{label}:** field reads {raw!r}, which has no number in it. "
            f"Fix the field, or remove the line so {DEFAULTS[field][2]}.")
    return int(m.group(0)), STATED


def _attack(row: list):
    """An Attacks table row -> attack spec, or ("save", spec) for save spells."""
    name, bonus, dice, dtype = (row + ["", "", "", ""])[:4]
    notes = row[4] if len(row) > 4 else ""
    if not name or not dice:
        return None
    # A sheet writes its crit the way the SRD does -- "1d6 (1d8 crit)" -- and
    # that is correct as written. The annotation used to be flattened into the
    # dice string by the same replace(" ", "") that tidies "2d6 + 1", giving
    # "1d6(1d8crit)", which no dice parser accepts: the engine raised
    # "Cannot parse dice notation" while picking an opportunity attack, and
    # that killed the session. Split it off into its own field instead.
    base, crit = split_crit(dice)
    part = {"dice": base.replace(" ", ""), "type": dtype.lower()}
    if crit:
        part["crit_dice"] = crit.replace(" ", "")
    damage = [part]
    low = notes.lower()
    source = "spell" if ("cantrip" in low or "spell" in low) else "weapon"
    if bonus.upper().startswith("DC"):
        m = re.match(r"DC\s*(\d+)\s*(\w+)", bonus, re.I)
        return ("save", {"name": name, "dc": int(m.group(1)) if m else None,
                         "ability": (m.group(2)[:3].lower() if m else ""),
                         "damage": damage, "notes": notes})
    spec = {"name": name, "bonus": _int(bonus), "damage": damage, "source": source, "flags": []}
    thrown = re.search(r"thrown\s*(\d+)\s*/\s*(\d+)", low)
    reach = re.search(r"reach\s*(\d+)", low)
    long_range = re.search(r"(\d+)\s*/\s*(\d+)", low)
    single = re.search(r"(\d+)\s*ft", low)
    if thrown:
        spec.update(type="melee_or_ranged", reach=5, range=[int(thrown.group(1)), int(thrown.group(2))])
    elif reach:
        spec.update(type="melee", reach=int(reach.group(1)))
    elif long_range:
        spec.update(type="ranged", range=[int(long_range.group(1)), int(long_range.group(2))])
    elif single:
        spec.update(type="ranged", range=[int(single.group(1))] * 2)
    else:
        spec.update(type="melee", reach=5)
    return spec


def _spell_names(block: str) -> list:
    """Spell names from a "Known Spells" section: every bullet's comma list,
    keeping only entries that look like names ("Fire Bolt", "Blindness/Deafness"),
    not notes ("one more Quandrix cantrip of your choice")."""
    names = []
    for line in block.splitlines():
        m = re.match(r"\s*-\s*\*\*[^*]+:\*\*\s*(.+)$", line)
        if not m:
            continue
        for part in re.split(r",|\s\+\s", m.group(1)):
            part = re.sub(r"\([^)]*\)", "", part).strip(" .*")
            words = part.split()
            if not words or len(words) > 4:
                continue
            if all(w[0].isupper() or w.lower() in ("of", "and", "the", "to", "from") for w in words) \
                    and words[0][0].isupper() and re.fullmatch(r"[A-Za-z' /-]+", part):
                if part.lower() not in (n.lower() for n in names):
                    names.append(part)
    return names


def _feature_names(text: str) -> list:
    """The bullets under each "### <Feature group>" heading in Features & Traits.

    Only the names, not the descriptions: the engine uses these to answer "does
    this creature have Arcane Recovery", and a sheet that spells the feature out
    in full should be believed over the SRD's level for it. A sheet with no
    Features section has none, which is different from one whose features were
    never written down, so the caller can tell the two apart.
    """
    block = _section(text, "Features & Traits")
    if not block.strip():
        return []
    out = []
    for line in block.splitlines():
        m = re.match(r"\s*(?:[-*]|\d+\.)\s+\*\*([^*]+)\*\*\s*:?", line) or \
            re.match(r"\s*(?:[-*]|\d+\.)\s+([^*\n][^\n:]{0,60}?)\s*:?\s*$", line)
        if m:
            out.append(m.group(1).strip(" .*:-"))
    return out


def parse_hit_dice(value: str):
    """`{"die", "total", "remaining"}` from the text after `**Hit Dice:**`, or None.

    Handles `5d6 (remaining: 2)`, `5d6`, `2/5 d6` (remaining/total) and a
    multiclass list such as `3d8 + 2d10 (remaining: 4)` (total is the sum, the
    die is the first one listed). The unfilled template `Xd[Y]` gives None.
    """
    value = value.strip()
    frac = _HD_FRACTION.match(value)
    if frac:
        return {"die": f"d{frac.group(3)}", "total": int(frac.group(2)),
                "remaining": min(int(frac.group(1)), int(frac.group(2)))}
    terms = _HD_TERM.findall(value)
    if not terms:
        return None
    total = sum(int(n) for n, _ in terms)
    rem = _HD_REMAINING.search(value)
    remaining = min(int(rem.group(1)), total) if rem else total
    return {"die": f"d{terms[0][1]}", "total": total, "remaining": remaining}


def _rewrite_hit_dice(value: str, remaining: int) -> str:
    """The Hit Dice text with only the remaining count changed."""
    if _HD_REMAINING.search(value):
        return _HD_REMAINING.sub(f"remaining: {remaining}", value, count=1)
    if _HD_FRACTION.match(value.strip()):
        return re.sub(r"\d+", str(remaining), value, count=1)
    return f"{value.rstrip()} (remaining: {remaining})"


def read_sheet(text: str, token_id: str, pos: tuple, path: str = "") -> Token:
    title = re.search(r"^#\s+(.+)$", text, re.M)
    if not title:
        raise ValueError("sheet has no '# Name' title line")
    name = title.group(1).strip()
    hp = _HP.search(text)
    if not hp:
        raise ValueError(f"{name}: no '**HP:** current / max' line on the sheet")
    ac_text = _field(text, "AC")

    scores = {}
    for row in _table_rows(_section(text, "Ability Scores")):
        if len(row) == 6 and all(re.match(r"\d+", c) for c in row):
            scores = {ab: _int(c) for ab, c in zip(ABILITIES, row)}
    # A saving throw with no table is derived from the ability score; with neither
    # table it is the documented +0 fallback, and either way the token carries which.
    saves = {ab: (s - 10) // 2 for ab, s in scores.items()}
    saves_from = DERIVED if scores else DEFAULT
    for row in _table_rows(_section(text, "Saving Throws")):
        if len(row) == 6 and all(re.match(r"[+-]?\d", c) for c in row):
            saves = {ab: _int(c) for ab, c in zip(ABILITIES, row)}
            saves_from = STATED

    attacks, save_spells = [], []
    # Only labelled when there is an attack to have a bonus on. A sheet with no
    # Attacks table has not stated a bonus and has not defaulted one either, and
    # reporting a source for a field that does not exist is the blur this issue is
    # about.
    bonuses_from = None
    for row in _table_rows(_section(text, "Attacks"))[1:]:          # skip the header row
        a = _attack(row)
        if isinstance(a, tuple):
            save_spells.append(a[1])
        elif a:
            attacks.append(a)
            if bonuses_from is None:
                bonuses_from = STATED
            if not re.search(r"[+-]?\d+", row[1] if len(row) > 1 else ""):
                bonuses_from = DEFAULT

    ac, ac_from = _required_int(text, "AC", name, DEFAULTS["ac"][0], "ac")
    speed, speed_from = _required_int(text, "Speed", name, DEFAULTS["speed"][0], "speed")
    level, level_from = _required_int(text, "Level", name, DEFAULTS["level"][0], "level")
    dex_mod, dex_from = ((scores["dex"] - 10) // 2, DERIVED) if "dex" in scores \
        else (DEFAULTS["dex_mod"][0], DEFAULT)
    slots = {}
    for row in _table_rows(_section(text, "Spell Slots"))[1:]:
        if len(row) >= 3 and re.match(r"\d", row[0]) and _int(row[1]):
            slots[str(_int(row[0]))] = {"total": _int(row[1]), "used": _int(row[2])}
    # A caster whose sheet has no filled-in Spell Slots table still casts. The
    # class and level are on the sheet, so the table is filled from the SRD
    # rather than the caster being treated as having no slots at all — the
    # engine cannot tell those two apart, and guessing "none" would leave a
    # level 5 wizard who never got round to the table unable to cast anything.
    # A row of zeroes counts as no table, which is what the template's blank
    # "## Spell Slots (if applicable)" section parses to. A table the sheet
    # *does* have is never overwritten, and a class with no SRD table is left
    # exactly as the sheet left it.
    klass = _field(text, "Class")
    if not slots and _slots_module().is_caster(klass):
        slots = _slots_module().spent_table(klass, level)

    skills = {}
    for row in _table_rows(_section(text, "Skills"))[1:]:
        if len(row) >= 3 and re.match(r"[+-]?\d", row[2]):
            skills[re.sub(r"[^a-z]+", "-", row[0].lower()).strip("-")] = _int(row[2])
    spell_dc = re.search(r"spell save DC\s*(\d+)", text, re.I)
    spell_atk = re.search(r"spell attack\s*([+-]\d+)", text, re.I)
    passive = re.search(r"Passive Perception\s*(\d+)", text, re.I)
    spells = _spell_names(_section(text, "Known Spells"))
    for a in attacks + save_spells:                  # spells used as attacks are known too
        if a.get("source") == "spell" or "dc" in a:
            if a["name"].lower() not in (n.lower() for n in spells):
                spells.append(a["name"])

    temp = _TEMP.search(text)
    death = _DEATH.search(text)
    hd = _HIT_DICE.search(text)
    hit_dice = parse_hit_dice(hd.group(2)) if hd else None
    conds = []
    cm = _CONDITIONS.search(text)
    if cm:
        conds = [c.strip().lower() for c in cm.group(0).split(":**", 1)[1].split(",")
                 if c.strip() and c.strip().lower() not in ("none", "-")]
    derived = {"ac": ac_from, "speed": speed_from, "level": level_from,
               "dex_mod": dex_from, "saves": saves_from,
               # A sheet with a Passive Perception line states one; one without is
               # absent rather than unknown-to-the-model, and `checks` reads only a
               # stated value, so this is STATED by construction when present.
               "passive_perception": STATED if passive else "missing",
               "hit_dice": STATED if hd else "missing",
               "spell_dc": STATED if spell_dc else "missing",
               "spell_attack": STATED if spell_atk else "missing"}
    if bonuses_from is not None:
        derived["attack_bonus"] = bonuses_from
    return Token(
        id=token_id, name=name, side="pc", x=pos[0], y=pos[1],
        hp=int(hp.group(2)), max_hp=int(hp.group(3)), ac=ac,
        temp_hp=int(temp.group(2)) if temp else 0,
        speed=speed,
        dex_mod=dex_mod,
        controller="player", conditions=conds, attacks=attacks, saves=saves,
        death_saves={"successes": int(death.group(2)) if death else 0,
                     "failures": int(death.group(4)) if death else 0},
        source={"kind": "sheet", "path": path},
        extra={"ac_note": ac_text, "slots": slots, "save_spells": save_spells,
               "hit_dice": hit_dice, "derived": derived, "ac_parts": ac_parts(text),
               "abilities": scores, "skills": skills, "level": level, "spells": spells,
               "features": _feature_names(text),
               # What a short rest can give back is a class question (Pact Magic,
               # Arcane Recovery), and the class is a line on the sheet that
               # nothing else read.
               "spellcasting": {"class": klass, "level": level} if slots else None,
               "spell_dc": int(spell_dc.group(1)) if spell_dc else None,
               "spell_attack": int(spell_atk.group(1)) if spell_atk else None,
               "passive_perception": int(passive.group(1)) if passive else None},
    )


# ── the AC decomposition (RI10), as labels, not as a new model ───────────────
#
# RI10 asks for `ac_base` / `ac_dex_bonus` / `ac_max_bonus` rather than one
# integer, so an AC can change and the reason can be named. What it does NOT ask
# for is adopting the external runtime that motivated it, and nothing here does:
# `Token.ac` is still the integer every attack roll is computed from, and
# `token_from_sheet` still sets it the way it always has. This returns the three
# parts beside it, each with where it came from, which is the half of RI10 that is
# a provenance question rather than an engine rewrite.
_AC_NOTE = re.compile(r"\(\s*([+-]?\d+)\s*([^)]*)\)")


def ac_parts(text: str) -> dict:
    """The AC a sheet states, decomposed, with provenance.

        "**AC:** 12 (13 with Mage Armor)"  ->
            base 12, stated_with 13, dex_bonus +2, max_bonus -2

    `max_bonus` is what the parenthetical adds over "13 + DEX", which is the number
    Mage Armor or a shield contributes on its own. On the repository's own fixture
    sheet that is **-2**: the sheet says 13 with Mage Armor where PHB p.144 says
    13 + DEX, and with DEX 14 the rule gives 15. Reporting the discrepancy as a
    number with a provenance is the point. Correcting it is not: the sheet is
    campaign data, and this function only says what it says.

    A sheet with no parenthetical has no `stated_with`, and `max_bonus` is then 0
    rather than None, because "the sheet claims no bonus" is a fact rather than an
    absence.
    """
    raw = _field(text, "AC")
    out = {"base": None, "stated_with": None, "dex_bonus": 0, "max_bonus": 0,
           "provenance": "missing", "note": ""}
    base = re.match(r"\s*([+-]?\d+)", raw)
    if not base:
        out["note"] = ("the sheet has no readable AC line; the engine's 10 is the "
                       "documented unarmoured fallback")
        return out
    out["base"] = int(base.group(1))
    out["provenance"] = STATED
    scores = {}
    for row in _table_rows(_section(text, "Ability Scores")):
        if len(row) == 6 and all(re.match(r"\d+", c) for c in row):
            scores = {ab: _int(c) for ab, c in zip(ABILITIES, row)}
    if "dex" in scores:
        out["dex_bonus"] = (scores["dex"] - 10) // 2
    note = _AC_NOTE.search(raw)
    if note:
        out["stated_with"] = int(note.group(1))
        out["max_bonus"] = out["stated_with"] - 13 - out["dex_bonus"]
        out["note"] = f"the sheet states {out['stated_with']} {note.group(2).strip()}".strip()
        if out["max_bonus"] != 0 and "mage armor" in note.group(2).lower():
            out["note"] += (f", and PHB p.144 gives 13 + DEX = "
                            f"{13 + out['dex_bonus']}")
    return out


def explain(token) -> list:
    """Every consequential fallback on this token, as a line a GM can act on.

    Empty for a sheet that states everything, which is the common case and the reason
    this is a function rather than a warning: a parser that always warns teaches a GM
    to ignore it.
    """
    derived = (token.extra or {}).get("derived") or {}
    out = []
    for field, source in derived.items():
        if source != DEFAULT or not DEFAULTS.get(field, (None, False, ""))[1]:
            continue
        out.append(f"{token.name}: {field} is {token_value(token, field)}; "
                   f"{DEFAULTS[field][2]}.")
    return out


def token_value(token, field: str):
    """The number `explain()` is talking about, for the field named."""
    return {"ac": token.ac, "speed": token.speed, "level": token.extra.get("level"),
            "dex_mod": token.dex_mod, "saves": token.saves,
            "attack_bonus": [a.get("bonus") for a in token.attacks],
            }.get(field)


def lasting_conditions(token) -> list:
    if token.dead:
        return ["dead"]
    durations = token.extra.get("durations", {})
    return [c for c in token.conditions if c in ALWAYS_LASTING or durations.get(c)]


def write_back(text: str, token) -> str:
    """Sheet text with this token's combat results written into it."""
    out = _HP.sub(lambda m: f"{m.group(1)}{token.hp} / {m.group(3)}", text, count=1)
    out = _TEMP.sub(lambda m: f"{m.group(1)}{token.temp_hp}", out, count=1)
    ds = token.death_saves
    out = _DEATH.sub(lambda m: f"{m.group(1)}{ds['successes']}{m.group(3)}{ds['failures']}",
                     out, count=1)
    hd = token.extra.get("hit_dice")
    if hd:
        out = _HIT_DICE.sub(
            lambda m: (m.group(1) + _rewrite_hit_dice(m.group(2), int(hd["remaining"]))
                       if parse_hit_dice(m.group(2)) else m.group(0)),
            out, count=1)
    slots = token.extra.get("slots") or {}

    def _slot(m):
        s = slots.get(m.group(2))
        return f"{m.group(1)}{s['used']}{m.group(4)}" if s else m.group(0)

    if slots:
        out = _SLOT_ROW.sub(_slot, out)
    lasting = lasting_conditions(token)
    line = f"- **Conditions:** {', '.join(lasting) if lasting else 'none'}"
    if _CONDITIONS.search(out):
        out = _CONDITIONS.sub(line, out, count=1)
    elif lasting:
        out = re.sub(r"^(- \*\*Death Saves:\*\*.*)$", lambda m: m.group(1) + "\n" + line,
                     out, count=1, flags=re.M)
    return out

