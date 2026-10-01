"""
npcs_to_statblocks.py: a campaign's npcs.md -> Fantasy Statblocks notes.

Run: python3 scripts/npcs_to_statblocks.py --campaign NAME [--vault DIR] [--stats]

    python3 scripts/npcs_to_statblocks.py --campaign emberfall-wake --vault ~/vault
    python3 scripts/npcs_to_statblocks.py --campaign emberfall-wake --stats

`npcs.md` is a GM's working document and is the campaign's authority on who exists.
It is not a stat block: it records HP, AC and one attack line per NPC, in prose,
and says nothing about ability scores. This reads it and produces FSB notes so an
NPC can be put on the table mid-session, which is the moment `/srd-lookup` is one
click away but only covers SRD creatures.

WHY THE BLOCKS ARE MOSTLY STAT-LESS
-----------------------------------
An NPC here is a person in the story, not a monster. The convention is CR 0 (or
"n/a") and no ability scores, which is why these notes carry a name, a subheading,
HP/AC where the file gives them, and traits -- and no `stats` table.

That restraint is the whole design. `npcs.md` gives an attack bonus, never the
ability scores behind it, so printing `stats: [10, 10, 10, 10, 10, 10]` would invent
six numbers the GM never wrote and then hand the table a plausible-looking block
where a transcribed value should be. `export_bestiary.abilities()` refuses that
case for the same reason, and the `has_stats` guard added there is what lets a
stat-less record through while a half-filled one is still refused by name.

An NPC whose file DOES carry ability scores passes them straight through and gets a
full block, with no special-casing here.

Every field is copied verbatim. Nothing is normalised, completed, or inferred.

Output:
    <campaign>/statblocks.json          the homebrew monster records (derived)
    <vault>/Bestiary/<Name>.md          FSB notes, via export_bestiary.note_for
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import paths
from localdm import context


def _load_export_bestiary():
    """import export_bestiary as a module.

    It is a script with a hyphen-free name but a hyphen-free path is not enough:
    `export_bestiary` is importable by name, so a plain `import` would work. It is
    loaded by path anyway because tests load it the same way and there is exactly
    one implementation of the FSB field mapping to keep honest -- a second copy
    would drift from the one the SRD is exported with.
    """
    spec = importlib.util.spec_from_file_location(
        "export_bestiary", ROOT / "scripts" / "export_bestiary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eb = _load_export_bestiary()


# --- npcs.md parsing --------------------------------------------------------
#
# A section is `### <Name>` followed by `- **Label:** value` bullet lines, where
# several labelled fields share one line separated by `|`, plus `### Personality`,
# `### Relationships` and `### Notes` subsections. The two heading shapes are told
# apart by what follows them, not by a list of names, so a campaign that adds a
# subsection does not need this updated.

_HEADING = re.compile(r"^(#{2,6})\s+(.+?)\s*$", re.MULTILINE)

# The people are the SHALLOWEST heading level in the file, and that has to be
# derived rather than hardcoded to `###`.
#
# In `npcs-full.md` the 26 `## ` headings are the people and the 19 `### ` headings
# are subsections (`Personality`, `Relationships`, `Notes`). A `^###` matcher takes
# the subsections and misses every person -- which is why this script shipped,
# passed 25 tests and produced nothing at all for the only campaign that has one.
# Pointed at the index it found two entries, both of them document sections.
#
# Levels 1 are excluded: every one of these files opens with `# NPCs ...`, and
# counting it would make the document's own title the shallowest heading and hence
# the level at which the people live. So the search is over `## ` and deeper, and
# the shallowest level found there is the NPC level.
def npc_level(text: str) -> int:
    levels = {len(m.group(1)) for m in _HEADING.finditer(text)}
    return min(levels) if levels else 3

# Sections are separated by a `---` rule that is NOT part of the last section's
# content: the file puts one between every NPC. A body runs to the next `###`, so
# the rule lands inside the Notes text and the note ends up with a trait reading
# "...has not revisited it. ---", an Obsidian frontmatter delimiter quoted into the
# middle of a statblock. It happens to parse, which is exactly why it survived.
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_SUBSECTIONS = {"personality", "relationships", "notes"}
# `re.M` matters: `_fields` walks the file a line at a time, and without it `$`
# anchors to end-of-STRING, so the last field on every bullet line silently failed
# to match and the NPC lost half its record. The `\|` lookahead is what separates
# the several labelled fields the GM packs onto one line.
#
# The label charset is deliberately wide -- "Notable abilities", "CR/Level",
# "Attitude toward party" are all labels, and a narrow one would drop whichever
# label happened to be spelled differently.
_FIELD = re.compile(r"\*\*(.+?):\*\*\s*(.*?)(?=\s*\|\s*\*\*|$)", re.MULTILINE)

# The Personality and Relationships bullets label with the axis itself:
# "- **Trustworthy <-> Deceptive:** Deceptive, but only about the water". The
# axis text is not [A-Za-z/ ], so this matcher takes any non-empty label.
_BULLET = re.compile(r"^\s*-\s*\*\*(.+?):\*\*\s*(.*)$", re.MULTILINE)

# "n/a", "tbd", "unknown", and an em-dash standing in for a blank. These mean the
# GM did not record the value, which is different from recording a zero, and the
# difference decides whether the field is emitted at all.
_ABSENT = re.compile(r"^(n/?a|tbd|unknown|—|-)$", re.IGNORECASE)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _number(text: str) -> int | None:
    """The first integer in a prose field, or None.

    HP is "27" and AC is "15 (coat over a breastplate, keeps the breastplate secret)",
    so the number comes first and the parenthetical is the GM's aside, which the
    FSB `ac` field takes as a free string. A field of "n/a" yields None, and None
    means the field is omitted -- never coerced to 0, because 0 HP is a claim.
    """
    if _ABSENT.match(text.strip()):
        return None
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else None


def _ac(text: str) -> int | str | None:
    """AC as an int, or as "15 (coat over a breastplate)" when annotated.

    FSB takes `ac` as a number OR a string (FANTASY-STATBLOCKS-FORMAT.md §4), and
    "the reeve keeps the breastplate secret" is exactly the kind of note that
    belongs on the block -- it is the reason the AC is what it is.
    """
    value = _number(text)
    if value is None:
        return None
    detail = re.match(r"^\s*-?\d+\s*\((.+)\)\s*$", text.strip())
    return f"{value} ({detail.group(1)})" if detail else value


# A CR is a bare number, optionally annotated: "2", "0 (dead)", "**11**".
#
# Anything looser is a bug, and the looser version of this shipped. Taking the first
# integer anywhere in the field turned real prose into numbers the table would roll:
# "base **Apprentice Wizard**-style (use **Mage** at reduced HP 25 from level 5)"
# gave Mabli Quenn CR 25, "from level 6 **Mage** reskin" gave Theodric Vane CR 6,
# "book p.189" gave Vess CR 189, and "Scene **0B**" gave Hesper CR 0. Four
# characters with a challenge rating that appears nowhere in the source, which is
# the failure `export_bestiary.py` calls the Ruin Grinder: a plausible number
# transcribed from something that was not a number.
_CLEAN_NUMBER = re.compile(r"^(-?\d+)\s*(?:\(([^)]*)\))?$")


def _cr(text: str) -> int | str:
    """CR from the "CR/Level" field.

    A bare number passes through, and a number with a trailing gloss ("0 (dead)")
    passes through as the number. Prose -- "**Archmage**-tier", "**Daemogoth**
    (book p.189, Huge fiend, CR 10)" -- stays the string it was written as, which
    is what FSB accepts and what the GM wrote. Stripping markdown emphasis first
    matters: the field is usually bolded.
    """
    plain = text.strip().replace("**", "").replace("*", "").strip()
    match = _CLEAN_NUMBER.match(plain)
    if match:
        return int(match.group(1))
    return plain


def split_sections(text: str, level: int | None = None) -> list[tuple[str, str, int]]:
    """(heading, body, level) for every heading at `level`, in file order.

    `level` defaults to the shallowest heading in the file, which is where the
    people live (see `npc_level`).
    """
    level = npc_level(text) if level is None else level
    marks = [m for m in _HEADING.finditer(text) if len(m.group(1)) == level]
    out = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((context.unlink(mark.group(2).strip()),
                    text[mark.end():end], level))
    return out


def subsections(text: str, level: int, start: int, end: int) -> list[tuple[str, str]]:
    """(heading, body) for the deeper headings inside one NPC's span.

    `start`/`end` are offsets into `text`, so a subsection is read only from the
    region belonging to the NPC above it. Ysolde's `Stat block (finale)` is the
    case that matters: it is the only block of numbers the GM wrote for the most
    important NPC in the campaign, and a parser that treats every heading as a
    name either drops it or turns it into a character called "Stat block".
    """
    inner = [m for m in _HEADING.finditer(text, start, end)
             if len(m.group(1)) > level]
    out = []
    for i, mark in enumerate(inner):
        stop = inner[i + 1].start() if i + 1 < len(inner) else end
        out.append((context.unlink(mark.group(2).strip()), text[mark.end():stop]))
    return out


def is_section_heading(heading: str, body: str) -> bool:
    """True when this heading is a document section rather than a person.

    The test is that the body defines no fields at all. A person is a list of
    `- **Label:** value` bullets -- role, location, HP, attitude -- and a document
    section is prose or a table ABOUT people. `Web of Wants (every student NPC:
    desire, fear/secret, problem with someone)` is a markdown table and trips this;
    all 25 person entries have field bullets and none of them do.

    The earlier version of this rule also demanded a parenthetical in the heading,
    which was the wrong way round: it refused `Esteemed Professor Ysolde Marrow
    (the Stopped Hand)`, the campaign's most important NPC, because her heading is
    qualified and her HP is written as prose in a `### Stat block (finale)`
    subsection rather than as an `**HP:**` field. Guessing at "no HP here means
    this is a section" is how the first version of this parser shipped with tests,
    found zero people, and called the campaign's villain a document.

    A refusal is reported rather than silent -- see `skipped_sections` -- because
    the cost of a wrong refusal is a character who is not at the table.
    """
    if roster(body, heading):
        return False
    # Read the span BEFORE the first subsection. Personality axes are `- **Label:**
    # value` bullets too, and a `### Personality` four bullets long would otherwise
    # make a heading with no fields of its own look like a character.
    return not _FIELD.search(body.split("\n###", 1)[0])


# The vocabulary of FIELD labels, as opposed to the names of people.
#
# `npcs-full.md` labels two different kinds of bullet. `- **Role:** ...`,
# `- **Demeanor:** ...`, `- **Knows:** ...` define a character; `- **Tilana
# Kapule** (Quandrix, outgoing Reader): ...`, `- **Saffi Tarn:** ...` introduce one.
# The parser has to tell them apart, because reading a roster bullet as a field is
# what turns `Quandrix Squad and Campus Faces` into a single token named after the
# heading instead of eight people the GM can put on the table.
#
# This is a closed list on purpose, and it is the same shape as `_SUBSECTIONS`
# above: these files define a small fixed vocabulary and reuse it, so enumerating
# it is reading the format rather than hardcoding one campaign's cast. It was built
# by listing every label that occurs in `npcs-full.md` -- 21 `Role`, 14
# `Demeanor`, the four Personality axes, `Knows`/`Owes`/`Fears`/`Hates`/`Allied
# with`, and the rest. A campaign that adds a label gets one refused below rather
# than one guessed at, and `roster()` needs two unrecognised labels before it will
# do anything, so a single unfamiliar field label is inert.
_FIELD_LABELS = frozenset({
    # identity and mechanics
    "role", "cr/level", "hp", "ac", "attack", "level", "class", "race",
    "str", "dex", "con", "int", "wis", "cha",
    "alignment", "languages", "size", "speed", "saves", "initiative",
    # the descriptive set every entry opens with
    "location", "appearance", "faction", "demeanor", "motivation", "secret",
    "speech quirk", "attitude toward party", "current goal", "schedule",
    "notable abilities", "traits", "testimony", "counselor",
    # relationships, written either as a subsection or as a bullet
    "relationships", "knows", "owes", "fears", "hates", "allied with", "wants",
    # voice
    "line", "lines", "lines by chapter", "voice", "speech",
    # free prose blocks
    "notes", "note", "appearance note", "canon note", "dm note",
    # the Personality axes, written with either arrow
    "trustworthy ↔ deceptive", "trustworthy <-> deceptive",
    "ambitious ↔ content", "ambitious <-> content",
    "loyal ↔ opportunistic", "loyal <-> opportunistic",
    "brave ↔ cowardly", "brave <-> cowardly",
})


# A roster bullet, in the two shapes `npcs-full.md` writes them:
#   - **Saffi Tarn:** first-year, Prismari-bound...      (colon inside the bold)
#   - **Tilana Kapule** (Quandrix, outgoing Reader): ... (colon outside, after a
#     parenthetical gloss)
# `_BULLET` only matches the first, which is why the Quandrix Squad heading came
# through as one block named after the heading instead of eight people.
_ROSTER_BULLET = re.compile(
    r"^\s*-\s+\*\*(.+?)\*\*\s*(?:\([^)]*\)\s*)?:?\s*(.+)$", re.MULTILINE)


def roster(text: str, heading: str = "") -> list[tuple[str, str]]:
    """[(name, description)] when a heading introduces several people at once.

    Three headings in `npcs-full.md` do this -- `Deans Adrix and Nev`, `Saffi
    Tarn and Rennick Moss`, `Quandrix Squad and Campus Faces` -- and their bodies
    are bullets keyed by a NAME (`- **Saffi Tarn:** first-year, ...`) rather than by
    a field (`- **Demeanor:** sardonic`). A parser that reads those labels as
    fields produces one statblock called "Saffi Tarn and Rennick Moss", which is
    the heading: a token the GM cannot put on the table, for a person who is in
    it.

    The discriminator is structural: a label that is not in the `_FIELD_LABELS`
    vocabulary is a person's name, and two or more of them in one heading make it a
    roster.

    The first version of this tested the label against the heading text instead,
    which looked stricter and was wrong twice over. `## Quandrix Squad and Campus
    Faces (minor, no secrets, no suspects)` names none of the eight people under
    it -- Tilana Kapule, Drazhomir Yarnask, Larine Arneza, Aurora Luna
    Wynterstarr, Greta Gorunn, Ellina Tanglewood -- so all eight were read as
    fields and the heading became one token with a roster's worth of people
    missing from it. Vocabulary is the honest test; a heading is free prose and
    does not have to name anybody.
    """
    labels = [(context.unlink(m.group(1).strip().rstrip(":").strip()),
               context.unlink(m.group(2).strip()))
              for m in _ROSTER_BULLET.finditer(text)]
    members = [(label, value) for label, value in labels
               if label and value and label.lower() not in _FIELD_LABELS]
    # Two or more, and never fewer. One unrecognised label is a field this
    # vocabulary has not met, and treating it as a person would rename the entry
    # after it; the count is what makes an unfamiliar label inert rather than
    # destructive.
    return members if len(members) > 1 else []


def parse_npcs(text: str) -> list[dict]:
    """The NPCs in an npcs.md, as homebrew monster records.

    Only the first heading per NPC is a name; the rest are subsections of it. An
    NPC with no name heading (a file that opens straight into `### Personality`) is
    skipped rather than inventing a name for it.
    """
    records: list[dict] = []
    level = npc_level(text)
    marks = [m for m in _HEADING.finditer(text) if len(m.group(1)) == level]

    def clean(body: str) -> str:
        return _RULE.sub("", body)

    def flush(name: str | None, head: list[str], sub: dict[str, list[str]],
              intro: str):
        if name is None:
            return
        people = roster(intro, name or "")
        if not people:
            records.append(_build(name, head, sub))
            return
        # A roster heading is not a person, so it produces one record per person
        # named in its own heading. The shared prose stays with every one of them:
        # it is the only description of them that exists, and dropping it would
        # leave a token with a name and nothing to say.
        for person, description in people:
            merged = dict(sub)
            merged["notes"] = [description, *sub.get("notes", [])]
            records.append(_build(person, head, merged))

    name: str | None = None
    head: list[str] = []
    sub: dict[str, list[str]] = {}
    intro = ""

    for i, mark in enumerate(marks):
        heading = mark.group(2).strip()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[mark.end():end]
        if heading.lower() in _SUBSECTIONS:
            # A section heading at NPC level belongs to the NPC above it. This is
            # the flat shape: a file whose people AND their subsections are all
            # `### `, which is what the tests use and what `npcs.md` was before it
            # was split off into `npcs-full.md`. Only the heading LEVEL cannot tell
            # `### Personality` from `### Reeve Aldis Kett` here; the vocabulary
            # can, and it is the same test the level test falls back to.
            if name is None:
                # A file that opens straight into "### Personality" has no NPC to
                # own it. Falling through would make the next heading's person be
                # built out of nobody's inner life.
                continue
            sub.setdefault(heading.lower(), []).append(clean(body))
            continue
        if is_section_heading(heading, body):
            # A document section, not a person. It neither starts an NPC nor
            # interrupts the one above it: `Web of Wants` follows the last entry
            # and would otherwise truncate that entry's Notes.
            continue
        flush(name, head, sub, intro)
        # `intro` is the prose BEFORE the first subsection, and it is the only
        # span `roster()` may read. Reading the whole body instead is how eight
        # Personality axes came back as eight people named "Trustworthy <->
        # Deceptive": those bullets are in the body, they are not field labels,
        # and they are in the heading text too.
        inner = subsections(text, level, mark.end(), end)
        stop = _first_subheading_offset(text, level, mark.end(), end)
        name, head, sub = heading, [], {}
        intro = text[mark.end():stop]
        head.append(clean(intro))
        for sub_head, sub_body in inner:
            sub.setdefault(sub_head.lower(), []).append(clean(sub_body))
    flush(name, head, sub, intro)
    return records


def _first_subheading_offset(text: str, level: int, start: int, end: int) -> int:
    """Offset of the first heading deeper than `level` inside the span."""
    for match in _HEADING.finditer(text, start, end):
        if len(match.group(1)) > level:
            return match.start()
    return end


def _fields(lines: list[str]) -> dict[str, str]:
    """`{label: value}` for the bullet lines of one block, flattened across the `|`.

    Values are unlinked as they are read. A linked value is the same fact as an
    unlinked one -- "as [[characters/Kairos.md|Kairos]]'s level" says exactly what
    "as Kairos's level" says -- and it reaches the statblock as literal text, where
    `[[characters/Kairos.md|Kairos]]` reads as corruption rather than as a name.
    """
    out: dict[str, str] = {}
    for line in lines:
        for match in _FIELD.finditer(line):
            label = match.group(1).strip().lower()
            value = context.unlink(match.group(2).strip())
            if label and value:
                out.setdefault(label, value)
    return out


def _bullets(lines: list[str]) -> list[tuple[str, str]]:
    """(label, value) for the `- **Label:** value` bullets of a subsection.

    The label is split from the value on the first `:**`, and a value that runs to
    the end of the line is kept whole -- these run to several clauses and joining
    them back together is the only lossless option.
    """
    out = []
    for label, value in _BULLET.findall("\n".join(lines)):
        label, value = context.unlink(label.strip(" -*")), value.strip()
        if label and value:
            out.append((label, " ".join(context.unlink(value).split())))
    return out


def _trait(name: str, value: str) -> dict:
    # Linked names are unlinked here rather than at each call site. `npcs-full.md`
    # is an Obsidian document and Obsidian links what it can: nine of the campaign's
    # own entries are written `- **[[Bestiary/Adrix.md|Adrix]]:** the theorist`, and
    # a statblock named "[[Bestiary/Adrix.md|Adrix]]" is a character whose name is a
    # file path. It also appears inside values -- "as
    # [[characters/Kairos.md|Kairos]]'s level" -- which reach the block as trait
    # text, where a link reads as corruption rather than as a name.
    return {"name": context.unlink(name).strip(),
            "desc": " ".join(context.unlink(value).split())}


def _build(name: str, head: list[str], sub: dict[str, list[str]]) -> dict:
    fields = _fields(head)
    record: dict = {"name": name, "index": _slug(name)}

    # The "Role" line carries the subheading the GM already wrote, verbatim:
    # "Elected reeve of Wickmoor, one of five Council seats". FSB joins size, type
    # and alignment into one subheading, and `type` is the slot that is meant for
    # this. Nothing is split out of it -- "Medium" and "Neutral" are not in this
    # file, and inferring them would be the invention this script exists to avoid.
    if fields.get("role"):
        record["type"] = fields["role"]

    if "cr/level" in fields:
        record["cr"] = _cr(fields["cr/level"])
    # A faction is not an alignment. "Reeve's Council" in the Alignment slot of a
    # printed statblock reads as a moral alignment, which is a different claim, and
    # npcs.md has no alignment field for these people at all. It is a trait.
    if "alignment" in fields:
        record["alignment"] = fields["alignment"]
    if fields.get("languages"):
        record["languages"] = fields["languages"]

    hp = _number(fields.get("hp", ""))
    if hp is not None:
        record["hp"] = hp
    ac = _ac(fields.get("ac", ""))
    if ac is not None:
        record["ac"] = ac

    # Ability scores: passed through if and only if the file recorded all six. The
    # `has_stats` guard in export_bestiary then lets a stat-less record render and
    # still refuses a half-filled one, naming what is missing.
    # Each score is passed through as it is found rather than all six or nothing.
    # Dropping five transcribed scores because the sixth is missing loses real data,
    # and hides the gap; keeping them lets `export_bestiary.abilities()` refuse the
    # note and NAME the ability, which is the only way the GM learns about it.
    for ability in eb._ABILITY_ORDER:
        score = _number(fields.get(ability, ""))
        if score is not None:
            record[ability] = score

    # The one attack line becomes one action, verbatim. It is not expanded into
    # "Melee Weapon Attack: +5 to hit, reach 5 ft., one target. Hit: 6 (1d8 + 2)
    # bludgeoning damage." -- the file does not say melee, does not give a reach,
    # and does not give the average. Writing that sentence would be a rule
    # interpretation presented as a transcription.
    attack = fields.get("attack")
    if attack and not _ABSENT.match(attack.strip()):
        weapon = re.search(r"\(([^()]*)\)\s*$", attack)
        label = weapon.group(1).split(",")[0].strip() if weapon else "Attack"
        record["actions"] = [{
            "name": label.title(),
            "desc": " ".join(attack.split()),
        }]

    traits: list[dict] = []
    # Order is the order the GM wrote them in, which is the order a reader wants.
    for label in ("notable abilities", "faction", "location", "demeanor",
                  "motivation", "secret", "speech quirk", "attitude toward party",
                  "current goal", "schedule"):
        if fields.get(label):
            traits.append(_trait(label.title(), fields[label]))

    # FSB keys traits by name in a Map, so two traits sharing a name silently
    # collapse to the last one (FANTASY-STATBLOCKS-FORMAT.md 4.3). Four "Personality"
    # traits and five "Relationships" traits is nine entries lost from every block,
    # rendering as a complete-looking statblock quietly missing its cast. The label
    # is therefore part of the NAME, which is also what makes the axis legible:
    # "Personality: Trustworthy <-> Deceptive" says what is being measured.
    #
    # The separator is an em-dash, NOT a colon, and that is load-bearing. Traits
    # reach FSB by round-tripping through `description`, where `parse_description`
    # reads the name as everything up to the first colon. A name like
    # "Personality: Trustworthy <-> Deceptive" therefore came back as "Personality",
    # and four of them collapsed to one -- the exact loss this comment is warning
    # about, reintroduced through the back door. A colon inside a trait NAME is
    # unrepresentable; an em-dash is not, because the section patterns all require a
    # colon AFTER the marker to match.
    for section in ("personality", "relationships"):
        for label, value in _bullets(sub.get(section, [])):
            traits.append(_trait(f"{section.title()} \u2014 {label}", value))

    if sub.get("notes"):
        traits.append(_trait("Notes", " ".join(" ".join(sub["notes"]).split())))

    # A subsection the schema does not know is kept verbatim rather than dropped.
    # `### Stat block (finale)` under Ysolde is the only place the GM wrote her
    # numbers in full -- AC 15 lattice ward, HP 130, spell save DC 17, the Hinge
    # Point and the legendary actions -- and they are prose, so none of the field
    # matchers above can reach them. A parser that keeps only Personality,
    # Relationships and Notes silently deletes the most complete statblock in the
    # campaign and emits a block with no HP in its place.
    for section, bodies in sub.items():
        if section in _SUBSECTIONS:
            continue
        body = " ".join(" ".join(bodies).split())
        if body:
            traits.append(_trait(section.title(), body))

    if traits:
        record["description"] = "\n\n".join(
            f"{t['name']}: {t['desc']}" for t in traits)
    return record


# --- reporting --------------------------------------------------------------

def _stat_state(record: dict) -> str:
    """"stat-less", "full", or "partial" -- how complete the ability scores are.

    The three cases behave differently and are worth telling apart. "stat-less" is
    the normal campaign civilian and renders fine. "full" renders fine. "partial"
    REFUSES: `abilities()` raises rather than inventing the missing score, so the
    note is not written and the GM is told which ability is absent.
    """
    if isinstance(record.get("stats"), list):
        return "full"
    present = sum(1 for a in eb._ABILITY_ORDER if record.get(a) is not None)
    if present == 0:
        return "stat-less"
    return "full" if present == len(eb._ABILITY_ORDER) else "partial"


def _is_statless(record: dict) -> bool:
    return _stat_state(record) == "stat-less"


# The file that HOLDS THE ENTRIES, in preference order.
#
# `npcs.md` is not this. In `strixhaven-kairos` it is a 33-row markdown table with
# zero headings in it, and it says so in its own second line: "Index only. Full
# entries (secrets, relationships, stat notes) in `npcs-full.md`". Both this script
# and `campaign_lint.py` read `npcs.md` and expect headings, so for this campaign
# the miss is structural rather than a parser bug, and it is why
# "no ### NPC entries found" fired before any of the other 32 lint warnings.
#
# The entries file is therefore tried first. A campaign with no separate file --
# which is what the tests use, and what `npcs.md` looked like before it was split
# -- still resolves, so nothing that worked stops working.
_ENTRY_FILES = ("npcs-full.md", "npcs.md")


def entries_file(campaign: pathlib.Path) -> pathlib.Path | None:
    """The campaign's NPC entries file: `npcs-full.md` if it has one, else `npcs.md`."""
    for name in _ENTRY_FILES:
        candidate = campaign / name
        if candidate.is_file():
            return candidate
    return None


_PROVENANCE = re.compile(r"Derived from `([^`]+)`")

# The folders that hold curated statblocks, in precedence order.
#
# A person can appear in both `npcs-full.md` and a curated data file. Five students
# do: Greta Gorunn, Aurora Luna Wynterstarr, Drazhomir Yarnask, Larine Arneza and
# Tilana Kapule are in `dnd5e_strixhaven_students.json` AND in the roster section of
# `npcs-full.md`. Both notes render, so Atlas and any scraper see two statblocks for
# one creature with different content, and an edit to one silently does not reach the
# other.
#
# The curated one wins, and that is not a formality: the data-file note has discrete
# traits, real actions and a proper stat block, while the roster's version is one
# prose line with no HP and no attack. The GM's decision, recorded here so the next
# run does not have to be asked.
CURATED_FOLDERS = ("Bestiary", "bestiary")
_NPC_FOLDER = "NPCs"


def curated_owner(vault: pathlib.Path, stem: str) -> str | None:
    """The curated note that already provides this creature, if one does.

    Matched on the FOLDED stem -- the same `_fold` the Atlas exporter uses -- so
    "Greta Gorunn" and "greta  gorunn" are one creature and not two.
    """
    key = _fold(stem)
    for folder in CURATED_FOLDERS:
        directory = vault / folder
        if not directory.is_dir():
            continue
        for note in sorted(directory.glob("*.md")):
            if note.stem.lower() == "readme":
                continue
            if _fold(note.stem) == key:
                return f"{folder}/{note.name}"
    return None


def _fold(name: str) -> str:
    """Case- and punctuation-insensitive key for a creature name.

    Duplicated from `map_to_atlas._fold` rather than imported, because importing it
    would make this exporter depend on the Atlas exporter for one string function,
    and the dependency is the wrong way round: this is the older, smaller tool.
    The behaviour is asserted identical in the tests rather than trusted.
    """
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def owner_of(note: pathlib.Path) -> str | None:
    """Which generator wrote this note, or None if it is not a derived note.

    Read from the note's own "Derived from" line rather than from a sidecar,
    because the line is the only thing that travels with the file: a note copied
    into another vault, or restored from a backup, keeps its provenance and keeps
    the protection.
    """
    try:
        match = _PROVENANCE.search(note.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


# The notes this exporter is allowed to overwrite: its own, and ones with no
# provenance at all (a note the GM wrote by hand, which nothing claims).
_MINE = "statblocks.json"


def write_note(bestiary: pathlib.Path, record: dict, source_name: str,
               vault: pathlib.Path | None = None) -> tuple[bool, str]:
    """(written, reason) for one note, refusing to clobber another generator's.

    Two refusals, both silent-corruption guards:

    * it will not overwrite a note another generator owns; and
    * it will not write a SECOND note for a creature a curated folder already
      provides, because two renderable statblocks for one person is a state the
      table cannot resolve and a later edit cannot reach.


    `Bestiary/` is written by at least four exporters in this tree -- the SRD, the
    faculty data file, the students data file, and this one -- and they all name
    their output the same way: `safe_name(record["name"]) + ".md"`. Nothing used to
    check who owned a note, and the first run of this exporter overwrote five of
    them.

    That is not a cosmetic collision. `dnd5e_strixhaven_students.json` carries real
    numbers for Greta Gorunn, Aurora Luna Wynterstarr, Drazhomir Yarnask, Larine
    Arneza and Tilana Kapule, and this exporter's record for each of them has no HP
    and no attack, because `npcs-full.md` describes them in one prose line. So the
    overwrite replaced a complete statblock with a stat-less one, under a name that
    still looked right, and the GM opened a five-character file and saw nothing
    wrong with it. The provenance line is in the note, so it is the one cheap test
    that catches this, and a refusal names the file that owns the note.
    """
    note = eb.note_for(record, source_name)
    path = bestiary / f"{eb.safe_name(record['name'])}.md"
    existing = owner_of(path)
    if existing and existing != _MINE and existing != source_name:
        return False, (f"{path.name} belongs to {existing!r}, not this exporter; "
                       f"left it alone. Use --force to overwrite.")

    # A curated note for this same creature wins, and this one is not written.
    # Checked against the note's own NAME rather than its filename, so a curated
    # "Greta Gorunn" is recognised even where the file was written under a
    # different spelling.
    if vault is not None and bestiary.name == _NPC_FOLDER:
        curated = curated_owner(vault, path.stem)
        if curated and existing != _MINE:
            return False, (f"{curated} already provides this creature with a "
                           f"curated statblock; not writing a second one to "
                           f"{bestiary.name}/. The curated note wins.")

    path.write_text(note, encoding="utf-8")
    return True, ""


def report(records: list[dict]) -> None:
    print(f"{len(records)} NPCs\n")
    width = max((len(r["name"]) for r in records), default=10)
    for record in records:
        flags = []
        state = _stat_state(record)
        if state != "stat-less":
            flags.append(state if state == "full" else "PARTIAL SCORES")
        if not record.get("actions"):
            flags.append("no attack")
        if "hp" not in record:
            flags.append("no HP")
        print(f"  {record['name'].ljust(width)}  CR {record.get('cr', '?')!s:>5}  "
              f"HP {record.get('hp', '—')!s:>4}  "
              f"{len(record.get('description', '').split(chr(10) + chr(10)))} traits  "
              f"{', '.join(flags)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", required=True,
                        help="campaign name, as passed to the other scripts. "
                             "Resolved read-only: this never migrates or copies a "
                             "campaign between roots, since running a report should "
                             "not move the GM's data.")
    parser.add_argument("--npcs", type=pathlib.Path, default=None,
                        help="path to the NPC entries file (default: "
                             "<campaign>/npcs-full.md, falling back to npcs.md)")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="where to write the derived JSON "
                             "(default: <campaign>/statblocks.json)")
    parser.add_argument("--vault", type=pathlib.Path, default=None,
                        help="also write FSB notes to <vault>/NPCs. NOT Bestiary/: that "
                             "folder is Atlas's Bestiary Folder and is regenerated "
                             "wholesale by export_bestiary.py")
    parser.add_argument("--stats", action="store_true",
                        help="report only, write nothing")
    args = parser.parse_args(argv)

    campaign = paths.find_campaign(args.campaign, migrate=False)
    npcs = args.npcs or entries_file(campaign)
    if not npcs or not npcs.exists():
        print(f"no NPC entries file for campaign {args.campaign!r}: "
              f"looked for {entries_file(campaign)}", file=sys.stderr)
        return 1

    records = parse_npcs(npcs.read_text(encoding="utf-8"))
    if not records:
        print(f"no NPC sections found in {npcs}", file=sys.stderr)
        return 1

    if args.stats:
        report(records)
        return 0

    out = args.out or (campaign / "statblocks.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "_meta": {
            "derived_from": npcs.name,
            "note": "Regenerated by npcs_to_statblocks.py. Do not edit; "
                    "npcs.md is the authority. Every value is copied verbatim.",
        },
        "monsters": records,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(records)} records to {out}")

    if args.vault:
        # `NPCs/`, not `Bestiary/`. That is not a preference, it is a measured
        # failure: with the notes in `Bestiary/`, Atlas -- which has the vault open
        # and watches it -- rewrote nine of them into mangled duplicates named
        # `BestiaryAdrixmdAdrix.md` on every run, with `name:` set to a
        # self-referential `[[Bestiary/Adrix.md|Adrix]]`. It does that to any note in
        # its Bestiary Folder that a token already references, and this vault has
        # tokens for Adrix, Saffi, Rennick, Tilana, Drazhomir, Larine, Aurora,
        # Greta and Ellina. `Bestiary/` is the regenerated SRD set and Atlas owns
        # that folder; `NPCs/` is derived from `npcs-full.md` and nothing else
        # writes it.
        #
        # `map_to_atlas.bestiary_index()` scans `NPCs/` too, so tokens still resolve
        # a `statblockPath`. Set Atlas's Bestiary Folder to `Bestiary/` and leave it.
        notes_dir = args.vault / "NPCs"
        notes_dir.mkdir(parents=True, exist_ok=True)
        written, skipped, refused = 0, [], []
        for record in records:
            try:
                ok, reason = write_note(notes_dir, record, out.name,
                                         vault=args.vault or campaign)
            except ValueError as e:
                refused.append(str(e))
                continue
            if ok:
                written += 1
            else:
                skipped.append(reason)
        print(f"wrote {written} notes to {notes_dir}")
        for reason in skipped:
            print(f"  skipped: {reason}", file=sys.stderr)
        for r in refused:
            print(f"  refused: {r}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
