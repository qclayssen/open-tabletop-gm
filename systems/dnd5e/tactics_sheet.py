"""tactics_sheet.py: D&D 5e character sheet <-> combat token.

Reads the markdown sheet the skill keeps (templates/character-sheet.md shape)
into a Token, and writes combat results back into it: HP, temp HP, spent
spell slots, hit dice, death saves and lasting conditions. Everything else in
the sheet is left byte for byte as it was.
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
    m = re.search(rf"\*\*{re.escape(label)}:\*\*\s*([^|\n]*)", text)
    return m.group(1).strip() if m else ""


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
    saves = {ab: (s - 10) // 2 for ab, s in scores.items()}
    for row in _table_rows(_section(text, "Saving Throws")):
        if len(row) == 6 and all(re.match(r"[+-]?\d", c) for c in row):
            saves = {ab: _int(c) for ab, c in zip(ABILITIES, row)}

    attacks, save_spells = [], []
    for row in _table_rows(_section(text, "Attacks"))[1:]:          # skip the header row
        a = _attack(row)
        if isinstance(a, tuple):
            save_spells.append(a[1])
        elif a:
            attacks.append(a)

    level = _int(_field(text, "Level"), 1)
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
    return Token(
        id=token_id, name=name, side="pc", x=pos[0], y=pos[1],
        hp=int(hp.group(2)), max_hp=int(hp.group(3)), ac=_int(ac_text, 10),
        temp_hp=int(temp.group(2)) if temp else 0,
        speed=_int(_field(text, "Speed"), 30),
        dex_mod=(scores["dex"] - 10) // 2 if "dex" in scores else 0,
        controller="player", conditions=conds, attacks=attacks, saves=saves,
        death_saves={"successes": int(death.group(2)) if death else 0,
                     "failures": int(death.group(4)) if death else 0},
        source={"kind": "sheet", "path": path},
        extra={"ac_note": ac_text, "slots": slots, "save_spells": save_spells,
               "hit_dice": hit_dice,
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

