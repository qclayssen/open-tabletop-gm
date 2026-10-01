"""
pcs_to_statblocks.py: a campaign's character sheets -> Fantasy Statblocks notes.

Run: python3 scripts/pcs_to_statblocks.py --campaign NAME [--vault DIR] [--stats]

    python3 scripts/pcs_to_statblocks.py --campaign strixhaven-kairos --stats
    python3 scripts/pcs_to_statblocks.py --campaign strixhaven-kairos --vault ~/vault

WHY THIS IS NOT `npcs_to_statblocks.py`
=======================================
The NPC exporter reads `npcs-full.md`, which records who exists. A character
sheet records one *person at one level*, and it is rewritten every time that person
levels, gains a feat, or loses a scar. That difference decides where the output is
allowed to go, and it is the whole design here:

    Bestiary/ is REGENERATED WHOLESALE on export and never read back.
    A PC note written there is deleted by the next run of `export_bestiary.py`.

So this script derives the block from `characters/<Name>.md` on demand and writes
it wherever the caller asks, never into `Bestiary/` by default. There is no stored
copy of a PC statblock to go stale, which is the property `Bestiary/` has and a
character sheet cannot have.

The one thing that IS stored is the sheet's `Last Updated` date, carried into the
note. A snapshot nobody can date is a plausible lie; a snapshot stamped 2026-09-26
that the sheet says was rewritten yesterday is visibly wrong instead.

WHAT IS COPIED AND WHAT IS NOT
-----------------------------
Ability scores, HP, AC, speed, saves, attacks and features are transcribed
verbatim from the sheet, and a field the sheet does not record is omitted rather
than filled. A half-filled statblock is worse than none: FSB binds `stats` to a
six-wide table, so three scores renders as a character whose Constitution and
Wisdom are unreadable rather than as an error.

The GM's own annotations -- the sheet is full of them, `⚠️ Religion is
deliberately NOT proficient (ruled by the Arbiter, 2026-09-30)` -- are kept, but
under a `GM Notes` trait rather than mixed into the character's features. They are
load-bearing rulings about how to run the sheet, and burying them in a trait
called "Kenku Racial" is how a ruling stops being read.

Output:
    <vault>/PCs/<Name>.md    FSB notes, via export_bestiary.note_for
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import paths
from utf8io import write_text


def _load_export_bestiary():
    """import export_bestiary by path, for the same reason the NPC exporter does.

    There is exactly one implementation of the FSB field mapping and it is the one
    the SRD is exported with. A second copy here would drift from it silently, and
    the drift would only show up as a PC rendering differently from a monster.
    """
    spec = importlib.util.spec_from_file_location(
        "export_bestiary", ROOT / "scripts" / "export_bestiary.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("export_bestiary", module)
    spec.loader.exec_module(module)
    return module


eb = _load_export_bestiary()


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


# Loaded for one function. `AC` is the one field whose rule is subtle enough that
# two copies would disagree: an annotated AC ("12 (13 with Mage Armor)") is a
# STRING on the statblock and an int otherwise, and a PC whose AC renders as
# `12` when the sheet says `13 with Mage Armor` is a character the table will
# play wrong. Same reason `export_bestiary` is loaded rather than reimplemented.
npcs = _load("npcs_to_statblocks", "npcs_to_statblocks.py")


# --- character sheet parsing -------------------------------------------------

_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)

# `**Player:** Quentin  **Campaign:** strixhaven-kairos  **Last Updated:** 2026-09-26`
_META = re.compile(r"\*\*(.+?):\*\*\s*(.+?)(?=\s*\*\*|$)", re.MULTILINE)

# `- **HP:** 8 / 8 | **Temp HP:** 0` and the table rows below it.
_FIELD = re.compile(r"\*\*(.+?):\*\*\s*(.*?)(?=\s*\|\s*\*\*|$)", re.MULTILINE)
_NUM = re.compile(r"-?\d+")

# The six ability abbreviations, as a SET rather than an ordered list.
#
# Order is read from the sheet's own header row instead of being assumed, because
# FSB's `stats` is positional: a sheet that printed WIS before CON would be
# silently scrambled by a parser that assumed the book's order, and a scrambled
# character sheet still looks completely normal.
_ABILITY_NAMES = frozenset(("STR", "DEX", "CON", "INT", "WIS", "CHA"))

# `# Kairos` -- the sheet's title, which is the character's name. `_HEADING` starts
# at `## ` so it cannot match this, and the first `## ` is `Identity`.
_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _section(text: str, heading: str) -> str:
    """The body of one `## ` section, by name, INCLUDING its `### ` subsections.

    The end of the span is the next heading at the SAME OR SHALLOWER level, not
    simply the next heading. Stopping at the first `###` empties the one section
    that matters most -- `## Features & Traits` is immediately followed by
    `### Wizard`, so a naive span gives it an empty body and the whole of the
    character's capabilities, Arcane Recovery and Chronal Shift and Expert
    Duplication and the Windfall, is lost while the block still renders.
    """
    for match in _HEADING.finditer(text):
        if match.group(2).strip().lower() != heading.lower():
            continue
        level = len(match.group(1))
        end = len(text)
        for later in _HEADING.finditer(text, match.end()):
            if len(later.group(1)) <= level:
                end = later.start()
                break
        return _RULE.sub("", text[match.end():end])
    return ""


def _subsections(text: str, heading: str) -> list[tuple[str, str]]:
    """[(name, body)] for the `### ` headings inside one `## ` section."""
    for match in _HEADING.finditer(text):
        if match.group(2).strip().lower() != heading.lower():
            continue
        level = len(match.group(1))
        end = len(text)
        for later in _HEADING.finditer(text, match.end()):
            if len(later.group(1)) <= level:
                end = later.start()
                break
        inner = list(_HEADING.finditer(text, match.end(), end))
        return [
            (m.group(2).strip(),
             _RULE.sub("", text[m.end():inner[i + 1].start()
                                 if i + 1 < len(inner) else end]))
            for i, m in enumerate(inner)
            if len(m.group(1)) > level
        ]
    return []


def _fields(lines: str) -> dict[str, str]:
    """`{label: value}` for the bullet lines of a block, flattened across the `|`."""
    out: dict[str, str] = {}
    for line in lines.splitlines():
        for match in _FIELD.finditer(line):
            label, value = match.group(1).strip().lower(), match.group(2).strip()
            if label and value:
                out.setdefault(label, value)
    return out


def _present(text: str) -> str:
    """The value, or "" when the GM recorded that there isn't one.

    Borrowed from the NPC exporter so "absent" means the same thing in both. It
    matters most for attack columns, where a blank renders as a phrase with no
    number in it ("n/a to hit") that a table reads as a typo rather than as a
    value nobody wrote down.
    """
    text = text.strip()
    return "" if npcs._ABSENT.match(text) else text


def _first_int(text: str) -> int | None:
    match = _NUM.search(text)
    return int(match.group()) if match else None


def _rows(body: str) -> list[list[str]]:
    """Markdown table rows in a section, as lists of stripped cells.

    The `|---|` separator is dropped here rather than in each caller. It is kept
    by a naive reader as a row of six cells that parse as no integers, which is
    harmless -- but it is also what a "skip the separator" test has to remember to
    do, and forgetting it once is how a sheet renders as all zeroes.
    """
    out = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") and c for c in cells):
            continue
        out.append(cells)
    return out


def _is_ability_header(cells: list[str]) -> bool:
    return bool(cells) and all(c.upper() in _ABILITY_NAMES for c in cells)


def _ability_scores(text: str) -> list[int] | None:
    """The six scores, in the sheet's own column order, or None if not all six.

    Refusing a partial row is deliberate. `export_bestiary.abilities()` will render
    a `stats:` list of the wrong length as a table with gaps in it, and a character
    whose CON column is blank reads as a character with no Constitution rather than
    as a sheet this parser could not read.

    The header row is read for ORDER rather than assuming the printed
    STR/DEX/CON/INT/WIS/CHA. FSB's `stats` is positional, so a sheet listing the
    six in another order is silently scrambled by a parser that assumes, and a
    scrambled character sheet is correct-looking.
    """
    rows = _rows(_section(text, "Ability Scores"))
    if not rows or not _is_ability_header(rows[0]):
        return None
    header = [c.lower() for c in rows[0]]
    if len(header) != 6 or set(header) != set(eb._ABILITY_ORDER):
        return None
    for cells in rows[1:]:
        values = [_first_int(c) for c in cells]
        if len(values) == 6 and all(v is not None for v in values):
            return [values[header.index(a)] for a in eb._ABILITY_ORDER]
    return None


def _saves(text: str) -> dict[str, int]:
    """The saving-throw row, as `{str: -1, ...}`, or empty if there is not one."""
    rows = _rows(_section(text, "Saving Throws"))
    if not rows or not _is_ability_header(rows[0]):
        return {}
    header = [c.lower() for c in rows[0]]
    for cells in rows[1:]:
        if len(cells) != len(header):
            continue
        out = {}
        for ability, cell in zip(header, cells):
            value = _first_int(cell)
            if value is not None:
                out[ability] = value
        if len(out) == 6:
            return out
    return {}


def _attacks(text: str) -> list[dict]:
    """The Attacks table, as FSB actions.

    A printed sheet writes the bonus in one column and the dice in another
    (`| Fire Bolt | +6 | 1d10 | fire | 120 ft cantrip |`) where a statblock writes
    one sentence. Joining them is transcription, not interpretation: both numbers
    are on the sheet, and the damage type is the sheet's own.
    """
    body = _section(text, "Attacks")
    rows = [line for line in body.splitlines()
            if line.strip().startswith("|") and "---" not in line]
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0].strip().strip("|").split("|")]
    actions = []
    for row in rows[1:]:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != len(header) or not cells[0]:
            continue
        row_data = dict(zip(header, cells))
        name = row_data.get("name", "")
        # "n/a" is how these sheets record a value the GM never wrote, which is
        # different from recording a zero. Emitting "n/a to hit" puts a phrase
        # with no number in it on a printed attack, where it reads as a broken
        # line rather than as an absence.
        bonus = _present(row_data.get("attack bonus", ""))
        damage = _present(row_data.get("damage", ""))
        kind = _present(row_data.get("type", ""))
        parts = [name]
        if bonus:
            parts.append(f"{bonus} to hit")
        if damage:
            parts.append(f"{damage} {kind}".strip())
        for column in ("notes",):
            if _present(row_data.get(column, "")):
                parts.append(row_data[column])
        actions.append({"name": name, "desc": " ".join(p for p in parts if p)})
    return actions


def _traits(text: str) -> list[dict]:
    """Features, from `### ` subsections of `## Features & Traits`.

    Each `###` heading is one trait and its body is the description verbatim. The
    sheet writes them as prose lists and inline notes, and they are the character's
    actual capabilities -- Arcane Recovery, Chronal Shift, Expert Duplication --
    so they belong on the block.
    """
    # The sheet writes features as `- **Arcane Recovery** (1/day, ...)`, one per
    # line. Flattened with the markers left on, the description reads
    # "- **Arcane Recovery** (1/day, ...) - **Level 2, Chronurgy Magic:** ...",
    # where a dash appears mid-sentence with nothing to list. Each marker is a
    # line break in this file and is removed; the bold `**` is left, because that
    # is emphasis the GM wrote and FSB renders markdown in a description.
    return [{"name": name.strip(), "desc": _flatten(body)}
            for name, body in _subsections(text, "Features & Traits")
            if body.strip()]


def _spells(text: str) -> dict | None:
    """Cantrips and known spells, as one trait.

    They are kept as a trait rather than parsed into FSB spell slots: the sheet
    records which spells are KNOWN, which are PREPARED, and a per-scene budget
    ("he has ONE slot for the whole night -- Shield *or* Magic Missile"), and none
    of that is a slot count. A parsed `spell_slots` block would say two slots and
    be wrong for the scene the GM is actually running.
    """
    body = _section(text, "Known Spells / Cantrips")
    if not body.strip():
        return None
    bullets = _flatten(body)
    if not bullets:
        return None
    return {"name": "Spellcasting", "desc": bullets}


def _flatten(body: str) -> str:
    """A bullet block as one paragraph, with the list markers removed."""
    parts = [re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", line).strip()
             for line in body.splitlines()]
    return " ".join(p for p in parts if p and not set(p) <= set("-" ))


def parse_pc(text: str) -> dict:
    """One character sheet as a homebrew monster record."""
    name = _TITLE.search(text)
    meta = {label.strip().lower(): value.strip()
            for label, value in _META.findall(text.split("## ", 1)[0])}
    fields = _fields(_section(text, "Combat Stats"))
    identity = _fields(_section(text, "Identity"))

    record: dict = {"name": name.group(1).strip() if name else "Unnamed"}

    # Race and class are what `type` is for on a statblock: it is the subheading,
    # and "Kenku wizard 1" is the whole creature in two words. Written from the
    # sheet's own two fields rather than assembled by inferring a monster taxonomy.
    race = identity.get("race", "").split("(")[0].strip()
    klass = identity.get("class", "")
    if race or klass:
        record["type"] = " ".join(p for p in (race, klass) if p)

    if identity.get("alignment"):
        record["alignment"] = identity["alignment"]
    if identity.get("level"):
        record["level"] = _first_int(identity["level"])

    hp = _first_int(fields.get("hp", ""))
    if hp is not None:
        record["hp"] = hp
    if fields.get("ac"):
        # `_ac` keeps a parenthetical, and "12 (13 with Mage Armor)" is exactly the
        # kind of aside that belongs on the block: it is why the AC is what it is.
        ac = npcs._ac(fields["ac"])
        if ac is not None:
            record["ac"] = ac
    if fields.get("speed"):
        record["speed"] = fields["speed"].replace("ft", "ft.")

    stats = _ability_scores(text)
    if stats:
        record["stats"] = stats
        # `saves` is a DICT here, keyed by ability, because that is the shape the
        # SRD carries and the shape `export_bestiary.saves()` reads. Handing it the
        # finished `[{Str: -1}, {Dex: 2}]` list instead renders nothing at all --
        # `.items()` on a list raises -- and the block loses its saves silently,
        # which is the failure mode of every parser here: it looks fine.
        saves = _saves(text)
        if saves:
            record["saves"] = saves

    traits = _traits(text)
    spells = _spells(text)
    if spells:
        traits.append(spells)

    # Backstory last, and only what the sheet states about the character. The
    # Tracking Sheet, the report cards and the extracurricular table are the GM's
    # worksheet and are not part of the creature.
    backstory = _section(text, "Backstory & Notes")
    prose = " ".join(line.strip() for line in backstory.splitlines()
                     if line.strip() and not line.strip().startswith(("|", "*Tracking")))
    prose = _flatten(prose)
    if prose:
        traits.append({"name": "Backstory", "desc": " ".join(prose.split())})

    if traits:
        record["description"] = "\n\n".join(
            f"{t['name']}: {t['desc']}" for t in traits)
    attacks = _attacks(text)
    if attacks:
        record["actions"] = attacks

    # Carried so the note can stamp it. `note_for` does not read this.
    record["_last_updated"] = meta.get("last updated", "")
    record["_player"] = meta.get("player", "")
    record["_campaign"] = meta.get("campaign", "")
    return record


# --- notes -------------------------------------------------------------------

def note_for_pc(record: dict) -> str:
    """The FSB note for a PC, stamped with the sheet's own `Last Updated`.

    `note_for` already writes "Regenerated on export, do not edit", which is true
    and also misleading for a PC: nothing is edited, and what goes stale is the
    SHEET. So the stamp is added and the warning is changed to name the sheet,
    which is the file that has moved on.
    """
    stamped = {k: v for k, v in record.items() if not k.startswith("_")}
    note = eb.note_for(stamped, source_name="characters/<Name>.md")

    header = [
        f"*Derived from `characters/{record['name']}.md` by `pcs_to_statblocks.py`. "
        "The SHEET is the authority and this note is generated on demand, never "
        "stored. If the sheet's `Last Updated` has moved past the date below, "
        "re-run the exporter and this note is wrong until you do.*",
        "",
        f"**Sheet last updated:** {record.get('_last_updated') or 'NOT RECORDED'}  "
        f"**Player:** {record.get('_player') or '—'}",
    ]
    # Replace the WHOLE line `note_for` wrote, not a prefix of it. Matching
    # "*Derived from `characters/<Name>.md`" alone leaves the sentence's own tail
    # -- " by `export_bestiary.py`. Regenerated on export, do not edit; changes
    # here are lost." -- stranded at the top of the note, claiming a generator
    # that did not write it and a provenance that is not this file's.
    return re.sub(r"^\*Derived from[^\n]*\*$", "\n".join(header), note,
                  count=1, flags=re.MULTILINE)


def report(records: list[dict]) -> None:
    print(f"{len(records)} PCs\n")
    for record in records:
        stats = record.get("stats")
        flags = []
        if not stats:
            flags.append("NO ABILITY SCORES")
        if "hp" not in record:
            flags.append("no HP")
        if not record.get("actions"):
            flags.append("no attacks")
        traits = len(record.get("description", "").split("\n\n"))
        print(f"  {record['name']:<16}  lvl {record.get('level', '?'):>3}  "
              f"HP {record.get('hp', '—')!s:>4}  AC {record.get('ac', '—')!s:>4}  "
              f"{traits:>2} traits  "
              f"sheet {record.get('_last_updated') or '—'}  "
              f"{', '.join(flags)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--vault", type=pathlib.Path, default=None,
                        help="where to write the FSB notes (default: <vault>/PCs, "
                             "NEVER <vault>/Bestiary, which is regenerated wholesale "
                             "and would delete the note)")
    parser.add_argument("--stats", action="store_true", help="report only, write nothing")
    args = parser.parse_args(argv)

    campaign = paths.find_campaign(args.campaign, migrate=False)
    sheets = sorted((campaign / "characters").glob("*.md"))
    if not sheets:
        print(f"no character sheets in {campaign / 'characters'}", file=sys.stderr)
        return 1

    records = []
    for sheet in sheets:
        text = sheet.read_text(encoding="utf-8")
        try:
            record = parse_pc(text)
        except ValueError as e:
            print(f"refused {sheet.name}: {e}", file=sys.stderr)
            return 1
        record["_source"] = sheet.name
        records.append(record)

    if args.stats:
        report(records)
        return 0

    # `PCs/`, deliberately NOT `Characters/`.
    #
    # `Characters/` is the same directory as `characters/` on macOS, which is case
    # insensitive by default, and `characters/` is where the sheets live. So writing
    # `<vault>/Characters/Kairos.md` overwrote `characters/Kairos.md` -- the sheet --
    # with a generated note, silently, on a case-insensitive filesystem that reports
    # success. The sheet is 167 hand-written lines and it was recovered from git; the
    # next person to run this would have lost it, and `campaign_lint.py` noticed only
    # because the generated note has no `## Identity`.
    #
    # The guard below makes that class of collision impossible rather than merely
    # unlikely: no output path may equal any input path, compared case-insensitively,
    # because that is the comparison the filesystem itself performs.
    out = (args.vault or campaign) / "PCs"

    if "bestiary" in {part.lower() for part in out.parts}:
        print(f"refusing to write PC notes under {out}: a Bestiary/ directory is "
              "regenerated wholesale by export_bestiary.py and Atlas rewrites "
              "anything in its Bestiary Folder, so the next run would delete these "
              "or mangle them. Point --vault at the campaign, not at Bestiary.",
              file=sys.stderr)
        return 1

    sources = {str(s.resolve()).lower() for s in sheets}
    clash = [r for r in records
             if str((out / f"{eb.safe_name(r['name'])}.md").resolve()).lower() in sources]
    if clash:
        print(f"refusing: {out} collides with the sheets it reads "
              f"({', '.join(r['_source'] for r in clash)}). Pick a different "
              "--vault; on a case-insensitive filesystem two directory names that "
              "differ only in case are one directory.", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for record in records:
        try:
            note = note_for_pc(record)
        except ValueError as e:
            print(f"refused {record['name']}: {e}", file=sys.stderr)
            continue
        write_text(out / f"{eb.safe_name(record['name'])}.md", note)
        written += 1
    print(f"wrote {written} PC notes to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
