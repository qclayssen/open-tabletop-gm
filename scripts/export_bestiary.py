"""
export_bestiary.py: write the SRD bestiary out as Fantasy Statblocks notes.

Run: python3 scripts/export_bestiary.py [--out VAULT] [--data FILE] [--stats]

Experiment A of docs/research/atlas-vtt/OBSIDIAN-INTEGRATION-DECISION.md: dump the
334 monsters in dnd5e_srd.json into a vault as FSB notes, open one mid-session, and
see whether the GM ever reaches for it. The question is not "can we render", it is
"will the GM ever look at it during play", when /srd-lookup is one click from the
token on the device already in hand.

This writes DERIVED OUTPUT. It is regenerated wholesale and never read back by the
engine. That direction is the whole safety property: `combat.py adjust TOKEN ac=16`
exists because the SRD value is wrong for a given fight, so a note that is edited
in place becomes a stale snapshot with a plausible number that reads as true.

Output: <out>/Bestiary/<Name>.md, one note per monster.

Usage:
    python3 export_bestiary.py                      # write to ./export/bestiary
    python3 export_bestiary.py --out ~/vault/AtlasTest
    python3 export_bestiary.py --only goblin,owlbear
    python3 export_bestiary.py --stats             # coverage report, writes nothing
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

DATA_FILE = pathlib.Path(__file__).resolve().parents[1] / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"
DEFAULT_OUT = pathlib.Path(__file__).resolve().parents[1] / "export" / "bestiary"

# The SRD stores an action's whole sentence in `actions[].raw` when the parser could
# not decompose it, and decomposes it into attack/damage/dc/area when it could. Both
# are usable; `raw` is preferred because it is the printed text, verbatim.
#
# The trait text lives only in `description`, already formatted as the SRD prints it
# ("Legendary Resistance: If the dragon fails..."). That prose is exactly the shape
# FSB renders, so it is passed through rather than reconstructed.

# `saves` and `skills` are {ability: modifier}. FSB wants a list of single-key maps.
_ABILITY_ORDER = ("str", "dex", "con", "int", "wis", "cha")
_ABILITY_LONG = {
    "str": "strength", "dex": "dexterity", "con": "constitution",
    "int": "intelligence", "wis": "wisdom", "cha": "charisma",
}

# `speed` is "walk 30 ft." in the SRD; FSB's canonical example is "30 ft.".
_SPEED_PREFIX = re.compile(r"^walk\s+", re.IGNORECASE)

# The em-dash (U+2014) section marker the SRD builder writes: "Action <em-dash> Bite: ...".
# Every printed entry in the SRD's `description` is its own paragraph; the builder
# joins them with "\n\n". A blank line IS the entry boundary, and splitting there is
# what the source already means.
#
# Splitting only before "Action <em-dash>" instead -- which is what this did at
# first -- leaves every trait paragraph welded to the one above it, and since the
# trait regex matches "Name: text" with DOTALL, the first name wins and swallows
# the rest. All six of Aurora Luna Wynterstarr's traits rendered as one. A spell
# list, having no "Name:" at all, was swallowed the same way.
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")

# The em-dash (U+2014) section marker the SRD builder writes: "Action <em-dash> Bite: ...".
# The longer names come first on purpose: "Legendary Action" must win over the
# bare "Legendary" alternative, or every legendary action is filed as a generic one.
#
# The bare "Legendary"/"Mythic" markers are the same em-dash form `split_packed`
# already recognises, and they are how the SRD prints a legendary action INSIDE a
# statblock: an Ancient Red Dragon's three legendary actions each head their own
# paragraph as "Legendary <em-dash> Tail Attack: ...", not "Legendary Action <em-dash>".
_SECTION_LINE = re.compile(
    r"^(Action|Bonus Action|Reaction|Lair Action|Legendary Action|Mythic Action"
    r"|Legendary|Mythic)"
    r"\s*\u2014\s*([^:]+):\s*(.+)$",
    re.DOTALL,
)
_TRAIT_LINE = re.compile(r"^([A-Z][^:\n]{2,60}):\s+(.+)$", re.DOTALL)

# The SRD prints a trait name and a colon: "Pack Tactics: The goblin has advantage".
# Strixhaven prints a period instead: "Gravity Shift (Recharge 5-6). The archaic
# reverses gravity". The colon form does not match that at all, so every trait of
# every Strixhaven creature rendered nameless -- a wall of bold-less text with the
# ability name, which is the whole point of the entry, gone.
#
# The name is split off in code rather than by regex because the delimiter is
# ambiguous: "Gravity Shift (Recharge 5-6)" contains both a period and a closing
# paren, and a regex that stops at the first period yields "Gravity Shift (Recharge
# 5-6" -- a name with an unbalanced paren, which is worse than no name at all.
_PERIOD_TRAIT = re.compile(r"\.\s+(?=[A-Z\u201c\"'])")


def _period_trait(block: str) -> tuple[str, str] | None:
    """(name, desc) for a "Name. text" paragraph, or None.

    Split at the EARLIEST period that is followed by a capitalised word and whose
    prefix has balanced parentheses, so a name carrying a "(Recharge 5-6)" or
    "(Costs 2 Actions)" qualifier survives intact.
    """
    for match in _PERIOD_TRAIT.finditer(block):
        head, tail = block[:match.start()], block[match.end():]
        if not 2 <= len(head) <= 70:
            continue
        if head.count("(") != head.count(")"):
            continue
        if not head[0].isupper():
            continue
        return head.strip(), " ".join(tail.split())
    return None

# Some creatures pack several printed entries into one SRD action, separated by the
# same em-dash marker used between sections, an Ancient Red Dragon's Fire Breath
# carries its three legendary actions in the same `raw` string. These must be split
# out or they render as one unreadable action.
_INLINE_SPLIT = re.compile(r"\s*(?=(?:Legendary|Mythic)\s*\u2014\s*[A-Z])")


def load_monsters(data_file: pathlib.Path | None = None) -> list[dict]:
    """Load monsters from a bestiary data file.

    Defaults to the SRD. `--data` points at another file using the same
    `{"monsters": [...]}` shape — that is how non-SRD content (Strixhaven
    students, college-role templates) gets exported without being written
    into `dnd5e_srd.json`, which stays exactly as the SRD published it.
    """
    path = data_file or DATA_FILE
    if not path.exists():
        raise SystemExit(f"bestiary data file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["monsters"]


def _modifier(value) -> int | None:
    """A save/skill modifier, or None when the SRD has nothing to say.

    FSB drops an entry whose value is 0 or empty (Saves.svelte), which is the
    behaviour we want: a creature with no notable saves should show no Saves line.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n or None


def abilities(monster: dict) -> list[int]:
    """FSB's `stats` is positional: [str, dex, con, int, wis, cha].

    A missing ability becomes 10, which is the SRD's default for a stat a
    creature never had. It is also what this printed for every monster until
    Ruin Grinder arrived with three genuinely absent fields -- and a statblock
    claiming INT 10 that the sourcebook simply did not print is a value
    invented at the table, which is the one thing an extraction must not do.

    So an absent ability is refused, naming the monster. Fill the field in the
    data file, or accept the refusal; there is no third option that is honest.
    """
    # Two input shapes reach here: the SRD's named keys (str/dex/...), and a
    # positional `stats` list some callers already hold. A list is explicit, so
    # it is never "missing" -- there is no way to read intent from a gap in it.
    positional = monster.get("stats")
    if isinstance(positional, list):
        try:
            return [int(v) for v in positional]
        except (TypeError, ValueError):
            raise ValueError(
                f"{monster.get('name', '?')}: stats list holds a non-number: "
                f"{positional!r}") from None

    out = []
    missing = [a for a in _ABILITY_ORDER if monster.get(a) is None]
    if missing:
        raise ValueError(
            f"{monster.get('name', '?')}: no value for "
            f"{', '.join(a.upper() for a in missing)}. The source did not print it, and "
            "guessing one is inventing a stat the table will then use.")
    for a in _ABILITY_ORDER:
        try:
            out.append(int(monster[a]))
        except (TypeError, ValueError):
            raise ValueError(
                f"{monster.get('name', '?')}: {a.upper()} is "
                f"{monster[a]!r}, which is not a score.") from None
    return out


def saves(monster: dict) -> list[dict]:
    out = []
    for short, mod in (monster.get("saves") or {}).items():
        n = _modifier(mod)
        if n is not None:
            out.append({_ABILITY_LONG.get(short, short): n})
    return out


def skills(monster: dict) -> list[dict]:
    out = []
    for name, mod in (monster.get("skills") or {}).items():
        n = _modifier(mod)
        if n is not None:
            out.append({name.replace("_", " ").title(): n})
    return out


def speed(monster: dict) -> str:
    raw = str(monster.get("speed") or "").strip()
    return _SPEED_PREFIX.sub("", raw) if raw else ""


def senses(monster: dict) -> str:
    passive = monster.get("passive_perception")
    if not passive:
        return ""
    return f"passive Perception {passive}"


def split_packed(text: str) -> list[tuple[str, str, str]]:
    """Split a packed action body into (kind, name, desc) triples.

    `text` is one SRD action's `raw` or one description paragraph. Most hold a
    single entry, but some concatenate several printed ones after a
    "Legendary <em-dash> " or "Mythic <em-dash> " marker. Those belong in FSB's own section
    containers, so `kind` says which; the first piece is always the ordinary
    action and carries an empty kind.
    """
    pieces = [p.strip() for p in _INLINE_SPLIT.split(text) if p.strip()]
    if len(pieces) == 1:
        return [("", "", text.strip())]
    out: list[tuple[str, str, str]] = []
    for piece in pieces:
        head = re.match(r"^(Legendary|Mythic)\s*\u2014\s*([^:]+):\s*(.+)$", piece, re.DOTALL)
        if head:
            out.append((head.group(1).lower(), head.group(2).strip(),
                        " ".join(head.group(3).split())))
        else:
            out.append(("", "", " ".join(piece.split())))
    return out


# The section word a description paragraph opens with, mapped to the FSB container
# that holds it. FSB supplies its own headings per container, so the word itself is
# dropped -- but WHICH container an entry lands in is a fidelity question, and
# putting a Reaction in the Actions list misrepresents the printed statblock.
#
# The SRD only ever writes "Action", so this mapping is inert for it. Homebrew
# records do use the other sections: the Strixhaven students print "Reaction <em-dash>
# Beginner's Luck (2/Day)", and that is a reaction, not an action.
_SECTION_CONTAINER = {
    "legendary": "legendary_actions",
    "mythic": "mythic_actions",
    "action": "actions",
    "bonus action": "bonus_actions",
    "reaction": "reactions",
    "lair action": "lair_actions",
    "legendary action": "legendary_actions",
    "mythic action": "mythic_actions",
}


def parse_description(description: str) -> tuple[list[dict], list[tuple[str, dict]]]:
    """(traits, sections) parsed out of a record's `description` prose.

    FSB has no body format, so the text has to arrive as structured frontmatter.
    Traits are printed as "Name: text" paragraphs and the rest as
    "Section <em-dash> Name: text" paragraphs, which maps cleanly onto FSB's
    {name, desc} containers.

    `sections` keeps the section word attached to each entry so the caller can route
    it; `split_description` is the 2-tuple view that discards it.
    """
    traits: list[dict] = []
    sections: list[tuple[str, dict]] = []

    for block in _PARAGRAPH_SPLIT.split(description.strip()):
        block = block.strip()
        if not block:
            continue

        marked = _SECTION_LINE.match(block)
        if marked:
            section, name, desc = marked.groups()
            sections.append((section.strip().lower(),
                             {"name": name.strip(), "desc": " ".join(desc.split())}))
            continue

        trait = _TRAIT_LINE.match(block)
        if trait:
            name, desc = trait.groups()
            traits.append({"name": name.strip(), "desc": " ".join(desc.split())})
            continue

        # No colon form, so try the "Name. text" form before giving up on it.
        dotted = _period_trait(block)
        if dotted:
            name, desc = dotted
            traits.append({"name": name, "desc": desc})
            continue

        # A paragraph with no "Name:" prefix is still worth showing; give it an
        # empty name, which FSB renders as description-only. Spell lists land here,
        # and used to be swallowed whole by the trait above them.
        traits.append({"name": "", "desc": " ".join(block.split())})

    return traits, sections


def split_description(description: str) -> tuple[list[dict], list[dict]]:
    """(traits, actions) parsed out of a record's `description` prose.

    The section word is dropped and every entry is treated as an ordinary action,
    which is correct for the SRD (it prints no other section) and is the historical
    behaviour kept for callers that only want the flat view.
    """
    traits, sections = parse_description(description)
    return traits, [entry for _section, entry in sections]


def build_fields(monster: dict) -> dict:
    """The FSB field dict for one creature. Order is the rendering order."""
    traits, sections = parse_description(monster.get("description", ""))

    # An action's text lives in one of three places, and which one depends on who
    # wrote the record:
    #   - `actions[].raw` -- the SRD's printed sentence, for 287 of 841 actions; the
    #     builder decomposed the rest into attack/damage/dc/area objects and left
    #     `raw` null, so this is a PARTIAL source, never the whole story
    #   - `actions[].desc` -- the key homebrew records use, holding the same printed
    #     sentence. Reading only `raw` silently dropped every homebrew attack: all
    #     four of Aurora Luna Wynterstarr's, including her Vampiric Bite.
    #   - the description prose -- covers entries the `actions[]` list never names
    #
    # The `actions[]` NAMES are the authority on which entries exist; the text is
    # the best source available for each. Order follows `actions[]`, which is the
    # printed order, and an entry with neither source is left out rather than
    # rendered as a bare name.
    prose_by_name: dict[str, tuple[str, dict]] = {}
    for section, entry in sections:
        prose_by_name.setdefault(entry["name"], (section, entry))

    text_by_name: dict[str, str] = {}
    for action in monster.get("actions") or []:
        text = " ".join((action.get("raw") or action.get("desc") or "").split())
        if text:
            text_by_name[action.get("name", "")] = text

    routed: dict[str, list[dict]] = {}
    covered: set[str] = set()
    for action in monster.get("actions") or []:
        name = action.get("name", "")
        # The record's own text when it has any, else the printed prose. Only 287 of
        # 841 SRD actions carry `raw`, so for the other two thirds the prose IS the
        # only source -- and a record naming an action with no text anywhere is left
        # out rather than rendered as a bare name.
        section, entry = prose_by_name.get(name, ("action", {}))
        desc = text_by_name.get(name) or entry.get("desc", "")
        if not desc:
            continue
        # A packed body may hold printed legendary/mythic entries of its own; those
        # belong in FSB's separate containers, not appended to the action.
        for kind, entry_name, entry_desc in [p for p in split_packed(desc) if p[2]]:
            if kind:
                section = kind
                entry_name = entry_name or name
            else:
                entry_name = name
            routed.setdefault(
                _SECTION_CONTAINER.get(section, "actions"), []
            ).append({"name": entry_name, "desc": entry_desc})
        covered.add(name)

    # `actions[]` is NOT the authority on which entries EXIST. It never lists
    # legendary actions at all -- an Ancient Red Dragon's `actions[]` has six names,
    # none of them Detect / Tail Attack / Wing Attack -- so those three live only in
    # the prose. Anything the prose prints that `actions[]` did not already cover
    # is emitted here, or the legendary actions vanish.
    #
    # The two lists are reconciled by name, so this cannot double up: an entry in
    # both is emitted once, from the better text source, in the `actions[]` pass.
    for section, entry in sections:
        if entry["name"] in covered:
            continue
        body = [p for p in split_packed(entry["desc"]) if p[2]]
        for kind, entry_name, entry_desc in body:
            routed.setdefault(
                _SECTION_CONTAINER.get(kind or section, "actions"), []
            ).append({"name": entry_name or entry["name"], "desc": entry_desc})

    fields: dict = {"name": monster["name"]}

    # The portrait, when the record knows where one lives upstream. FSB reads
    # `image` in the fence and renders it above the statblock, so this is what
    # puts a face in the note WITHOUT touching the note frontmatter -- which
    # `statblock_art.stamp_image` owns, and which strips an `image:` key written
    # here. Writing it in the fence is the only place both tools can coexist.
    #
    # This is an address, not a file. Nothing downloads it and nothing promises
    # it is on this machine: see build_srd.IMAGE_BASE for why the art cannot be
    # committed, and why "the record knows a portrait exists" has to stay a
    # different claim from "a portrait is installed".
    image = str(monster.get("image") or "").strip()
    if image.startswith(("http://", "https://")):
        fields["image"] = image

    # Only emitted when the record actually carries the value. `size: ""` is not
    # rendered by FSB (falsy properties are hidden) but it IS a line in the note,
    # and a stat-LESS record -- a campaign civilian, a corpse -- has no size to
    # print. Emitting the key because the field exists in the record is the wrong
    # reason to emit it.
    for key in ("size", "type", "alignment"):
        value = str(monster.get(key) or "").strip()
        if value:
            fields[key] = value

    if monster.get("ac") is not None:
        fields["ac"] = monster["ac"]
    if monster.get("hp") is not None:
        fields["hp"] = monster["hp"]
    if monster.get("hp_dice"):
        fields["hit_dice"] = str(monster["hp_dice"])

    sp = speed(monster)
    if sp:
        fields["speed"] = sp

    # A record with no ability scores at all is a stat-LESS creature -- a civilian,
    # a corpse, an NPC the table never fights -- and FSB renders `stats` as a
    # fixed-width table, so there is nothing to put in it. Emitting six 10s would
    # be inventing the scores, which is the one thing this exporter must not do.
    #
    # A record that is *partly* filled is a different case and still fails below,
    # naming the missing abilities: an absent one is refused, not defaulted.
    present = [a for a in _ABILITY_ORDER if monster.get(a) is not None]
    if isinstance(monster.get("stats"), list) or len(present) == len(_ABILITY_ORDER):
        fields["stats"] = abilities(monster)
    elif present:
        # Half-filled is not stat-less, and this is the case worth being loud about:
        # the GM wrote five real scores and the sixth went missing, so rendering a
        # stat-less civilian hides a data-entry error behind a block that looks
        # deliberate. `abilities()` names what is absent instead.
        abilities(monster)

    sv = saves(monster)
    if sv:
        fields["saves"] = sv
    sk = skills(monster)
    if sk:
        fields["skillsaves"] = sk

    for key, src in (
        ("damage_vulnerabilities", "vulnerabilities"),
        ("damage_resistances", "resistances"),
        ("damage_immunities", "immunities"),
        ("condition_immunities", "condition_immunities"),
    ):
        value = str(monster.get(src) or "").strip()
        if value:
            fields[key] = value

    se = senses(monster)
    if se:
        fields["senses"] = se
    if monster.get("languages"):
        fields["languages"] = str(monster["languages"])

    if monster.get("cr") is not None:
        fields["cr"] = monster["cr"]

    if traits:
        fields["traits"] = traits
    # `actions` is emitted first because it is the first section of a printed
    # statblock; the rest follow the same order the book prints them in.
    for container in ("actions", "bonus_actions", "reactions",
                      "legendary_actions", "mythic_actions", "lair_actions"):
        if routed.get(container):
            fields[container] = routed[container]

    return fields


# --- YAML emission -----------------------------------------------------------
#
# Hand-rolled rather than PyYAML: FSB reads this with Obsidian's own YAML parser,
# and the traps that matter (values containing ": ", leading "*" or "[") are easier
# to get right explicitly than to configure around. Every string is quoted unless it
# is unambiguously safe.

_SAFE_PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,'()/+\-]*$")


def yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text and _SAFE_PLAIN.match(text) and ": " not in text and not text.endswith(":"):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'


def emit_yaml(fields: dict, indent: int = 0) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for key, value in fields.items():
        # `stats` must be an INLINE flow sequence. FSB binds it to a fixed-width
        # table and a block-style list renders as six rows of one number each.
        if key == "stats" and isinstance(value, list):
            lines.append(f"{pad}stats: [{', '.join(str(v) for v in value)}]")
        elif isinstance(value, list):
            if not value:
                continue
            lines.append(f"{pad}{key}:")
            lines.extend(emit_yaml_list(value, indent + 2))
        else:
            lines.append(f"{pad}{key}: {yaml_scalar(value)}")
    return lines


def emit_yaml_list(items: list, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            if len(item) == 1:
                # The FSB single-key form for saves and skills.
                (only_key, only_value), = item.items()
                lines.append(f"{pad}- {yaml_scalar(only_key)}: {yaml_scalar(only_value)}")
            else:
                first = True
                for key, value in item.items():
                    prefix = f"{pad}- " if first else f"{pad}  "
                    lines.append(f"{prefix}{key}: {yaml_scalar(value)}")
                    first = False
        else:
            lines.append(f"{pad}- {yaml_scalar(item)}")
    return lines


def note_for(monster: dict, source_name: str = DATA_FILE.name) -> str:
    body = "\n".join(emit_yaml(build_fields(monster)))
    # `inline` mode: the body fence carries the data, frontmatter only triggers the
    # watcher and provides a block-ref target. Nothing is duplicated.
    return (
        "---\n"
        "statblock: inline\n"
        'statblock-link: "#^statblock"\n'
        "---\n"
        "\n"
        f"# {monster['name']}\n"
        "\n"
        f"*Derived from `{source_name}` by `export_bestiary.py`. "
        "Regenerated on export, do not edit; changes here are lost.*\n"
        "\n"
        "```statblock\n"
        f"{body}\n"
        "```\n"
        "^statblock\n"
    )


def safe_name(name: str) -> str:
    return re.sub(r"[^\w\s-]", "", name).strip() or "unnamed"


def coverage(monsters: list[dict]) -> None:
    print(f"{len(monsters)} monsters\n")
    stats = {
        "traits parsed": 0, "actions parsed": 0, "with saves": 0,
        "with skills": 0, "with resistances": 0, "with immunities": 0,
        "with vulnerabilities": 0, "with condition immunities": 0,
        "with passive perception": 0, "multiattack": 0, "unparsed description": 0,
    }
    for monster in monsters:
        traits, actions = split_description(monster.get("description", ""))
        if traits:
            stats["traits parsed"] += 1
        if actions:
            stats["actions parsed"] += 1
        if monster.get("saves"):
            stats["with saves"] += 1
        if monster.get("skills"):
            stats["with skills"] += 1
        for key, label in (
            ("resistances", "with resistances"), ("immunities", "with immunities"),
            ("vulnerabilities", "with vulnerabilities"),
            ("condition_immunities", "with condition immunities"),
        ):
            if monster.get(key):
                stats[label] += 1
        if monster.get("passive_perception"):
            stats["with passive perception"] += 1
        if any(a.get("multiattack") for a in monster.get("actions") or []):
            stats["multiattack"] += 1
        if not monster.get("description", "").strip():
            stats["unparsed description"] += 1
    width = max(len(k) for k in stats)
    for key, count in stats.items():
        print(f"  {key.ljust(width)}  {count:>4}  ({count / len(monsters):.0%})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT,
                        help="output directory (default: %(default)s)")
    parser.add_argument("--only", help="comma-separated monster names or indexes")
    parser.add_argument("--stats", action="store_true", help="coverage report only")
    parser.add_argument("--data", type=pathlib.Path, default=None,
                        help="bestiary data file (default: the SRD). Any file shaped "
                             "{\"monsters\": [...]}. Non-SRD content belongs in its own "
                             "file, never in dnd5e_srd.json.")
    args = parser.parse_args(argv)

    monsters = load_monsters(args.data)
    if args.only:
        wanted = {w.strip().lower() for w in args.only.split(",") if w.strip()}
        monsters = [m for m in monsters
                    if m["name"].lower() in wanted or str(m.get("index", "")).lower() in wanted]
        if not monsters:
            print(f"no monster matched {args.only!r}", file=sys.stderr)
            return 1

    if args.stats:
        coverage(monsters or load_monsters(args.data))
        return 0

    out = args.out / "Bestiary"
    out.mkdir(parents=True, exist_ok=True)

    source_name = (args.data or DATA_FILE).name
    written, refused = 0, []
    for monster in monsters:
        try:
            note = note_for(monster, source_name)
        except ValueError as e:
            # One creature with an unprinted field must not cost the other
            # twelve. Report it and leave it out; a note that invents the value
            # is worse than no note.
            refused.append(str(e))
            continue
        path = out / f"{safe_name(monster['name'])}.md"
        path.write_text(note, encoding="utf-8")
        written += 1

    print(f"wrote {written} notes to {out}")
    if refused:
        print(f"\n{len(refused)} creature(s) not written, for want of a value:")
        for r in refused:
            print(f"  - {r}")
    print("\nTo view them, open that directory as an Obsidian vault and install")
    print("Fantasy Statblocks (community plugin), then set the Bestiary Folder")
    print("to Bestiary/ under settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
