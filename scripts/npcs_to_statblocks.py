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

_NPC_HEADING = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)

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


def _cr(text: str) -> int | str:
    """CR from the "CR/Level" field.

    A numeric CR passes through. A level ("3") is the same number. Anything else --
    "n/a", which is how a dead NPC is recorded -- stays the string, and FSB's `cr`
    is documented as accepting a string (it only drives the Challenge line, not a
    calculation, for a non-numeric value).
    """
    value = _number(text)
    return value if value is not None else text.strip()


def split_sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) for every `###` section, in file order."""
    marks = list(_NPC_HEADING.finditer(text))
    out = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((mark.group(1).strip(), text[mark.end():end]))
    return out


def parse_npcs(text: str) -> list[dict]:
    """The NPCs in an npcs.md, as homebrew monster records.

    Only the first heading per NPC is a name; the rest are subsections of it. An
    NPC with no name heading (a file that opens straight into `### Personality`) is
    skipped rather than inventing a name for it.
    """
    records: list[dict] = []
    name: str | None = None
    head: list[str] = []
    sub: dict[str, list[str]] = {}
    current: str | None = None

    def _clean(body: str) -> str:
        return _RULE.sub("", body)

    def flush():
        if name is not None:
            records.append(_build(name, head, sub))

    for heading, body in split_sections(text):
        if heading.lower() in _SUBSECTIONS:
            # A subsection heading with no NPC above it yet -- a file that opens
            # straight into "### Personality" -- belongs to nothing. Falling through
            # would make it the next NPC's name, producing a statblock called
            # "Personality" built from another character's inner life.
            if name is None:
                continue
            current = heading.lower()
            sub.setdefault(current, [])
            sub[current].append(_clean(body))
            continue
        flush()
        name, head, sub, current = heading, [], {}, None
        head.append(_clean(body))
    flush()
    return records


def _fields(lines: list[str]) -> dict[str, str]:
    """`{label: value}` for the bullet lines of one block, flattened across the `|`."""
    out: dict[str, str] = {}
    for line in lines:
        for match in _FIELD.finditer(line):
            label = match.group(1).strip().lower()
            value = match.group(2).strip()
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
        label, value = label.strip(" -*"), value.strip()
        if label and value:
            out.append((label, " ".join(value.split())))
    return out


def _trait(name: str, value: str) -> dict:
    return {"name": name, "desc": " ".join(value.split())}


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
                        help="path to npcs.md (default: <campaign>/npcs.md)")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="where to write the derived JSON "
                             "(default: <campaign>/statblocks.json)")
    parser.add_argument("--vault", type=pathlib.Path, default=None,
                        help="also write FSB notes to <vault>/Bestiary")
    parser.add_argument("--stats", action="store_true",
                        help="report only, write nothing")
    args = parser.parse_args(argv)

    campaign = paths.find_campaign(args.campaign, migrate=False)
    npcs = args.npcs or (campaign / "npcs.md")
    if not npcs.exists():
        print(f"no npcs.md for campaign {args.campaign!r}: {npcs}", file=sys.stderr)
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
        bestiary = args.vault / "Bestiary"
        bestiary.mkdir(parents=True, exist_ok=True)
        written, refused = 0, []
        for record in records:
            try:
                note = eb.note_for(record, out.name)
            except ValueError as e:
                refused.append(str(e))
                continue
            (bestiary / f"{eb.safe_name(record['name'])}.md").write_text(
                note, encoding="utf-8")
            written += 1
        print(f"wrote {written} notes to {bestiary}")
        for r in refused:
            print(f"  refused: {r}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
