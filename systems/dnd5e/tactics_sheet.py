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

from tactics.state import Token  # noqa: E402

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
# Kept on the sheet after combat. Others (prone, grappled, restrained,
# frightened, unconscious, ...) end with the fight. Poisoned, cursed and the
# like are kept only while a duration remains (token.extra["durations"]).
ALWAYS_LASTING = {"exhaustion", "exhausted"}

_HP = re.compile(r"(\*\*HP:\*\*\s*)(\d+)\s*/\s*(\d+)")
_TEMP = re.compile(r"(\*\*Temp HP:\*\*\s*)(\d+)")
_DEATH = re.compile(r"(\*\*Death Saves:\*\*\s*Successes:\s*)(\d+)(\s*\|\s*Failures:\s*)(\d+)")
_HIT_DICE = re.compile(r"(\*\*Hit Dice:\*\*\s*)(\d+d\d+)\s*\(remaining:\s*(\d+)\)")
_CONDITIONS = re.compile(r"^- \*\*Conditions:\*\*.*$", re.M)
_SLOT_ROW = re.compile(r"^(\|\s*(\d+)\w*\s*\|\s*\d+\s*\|\s*)(\d+)(\s*\|)", re.M)


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
    damage = [{"dice": dice.replace(" ", ""), "type": dtype.lower()}]
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

    slots = {}
    for row in _table_rows(_section(text, "Spell Slots"))[1:]:
        if len(row) >= 3 and re.match(r"\d", row[0]):
            slots[str(_int(row[0]))] = {"total": _int(row[1]), "used": _int(row[2])}

    skills = {}
    for row in _table_rows(_section(text, "Skills"))[1:]:
        if len(row) >= 3 and re.match(r"[+-]?\d", row[2]):
            skills[re.sub(r"[^a-z]+", "-", row[0].lower()).strip("-")] = _int(row[2])
    spell_dc = re.search(r"spell save DC\s*(\d+)", text, re.I)
    spell_atk = re.search(r"spell attack\s*([+-]\d+)", text, re.I)
    passive = re.search(r"Passive Perception\s*(\d+)", text, re.I)
    level = _int(_field(text, "Level"), 1)
    spells = _spell_names(_section(text, "Known Spells"))
    for a in attacks + save_spells:                  # spells used as attacks are known too
        if a.get("source") == "spell" or "dc" in a:
            if a["name"].lower() not in (n.lower() for n in spells):
                spells.append(a["name"])

    temp = _TEMP.search(text)
    death = _DEATH.search(text)
    hd = _HIT_DICE.search(text)
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
               "hit_dice": {"die": hd.group(2), "remaining": int(hd.group(3))} if hd else None,
               "abilities": scores, "skills": skills, "level": level, "spells": spells,
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
        out = _HIT_DICE.sub(lambda m: f"{m.group(1)}{m.group(2)} (remaining: {hd['remaining']})",
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

