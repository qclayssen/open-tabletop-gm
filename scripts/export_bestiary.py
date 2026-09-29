"""
export_bestiary.py: write the SRD bestiary out as Fantasy Statblocks notes.

Run: python3 scripts/export_bestiary.py [--out VAULT] [--stats]

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
_SECTION_SPLIT = re.compile(r"\n\n(?=(?:Action|Bonus Action|Reaction|Legendary Action|Mythic Action|Lair Action)\s*\u2014\s*)")
_TRAIT_LINE = re.compile(r"^([A-Z][^:\n]{2,60}):\s+(.+)$", re.DOTALL)

# Some creatures pack several printed entries into one SRD action, separated by the
# same em-dash marker used between sections, an Ancient Red Dragon's Fire Breath
# carries its three legendary actions in the same `raw` string. These must be split
# out or they render as one unreadable action.
_INLINE_SPLIT = re.compile(r"\s*(?=(?:Legendary|Mythic)\s*\u2014\s*[A-Z])")


def load_monsters() -> list[dict]:
    with open(DATA_FILE, encoding="utf-8") as fh:
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
    """FSB's `stats` is positional: [str, dex, con, int, wis, cha]."""
    return [int(monster.get(a) or 10) for a in _ABILITY_ORDER]


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


def split_description(description: str) -> tuple[list[dict], list[dict]]:
    """(traits, actions) parsed out of the SRD's `description` prose.

    FSB has no body format, so the text has to arrive as structured frontmatter.
    The SRD prints traits as "Name: text" paragraphs and actions as
    "Action <em-dash> Name: text" paragraphs, which maps cleanly onto FSB's
    {name, desc} containers.
    """
    traits: list[dict] = []
    actions: list[dict] = []

    for block in _SECTION_SPLIT.split(description.strip()):
        block = block.strip()
        if not block:
            continue

        marked = re.match(
            r"^(Action|Bonus Action|Reaction|Legendary Action|Mythic Action|Lair Action)"
            r"\s*\u2014\s*([^:]+):\s*(.+)$",
            block, re.DOTALL,
        )
        if marked:
            # The section word ("Action", "Reaction", ...) only marks the block;
            # FSB supplies its own headings, so it is not carried over.
            _section, name, desc = marked.groups()
            desc = " ".join(desc.split())
            actions.append({"name": name.strip(), "desc": desc})
            continue

        trait = _TRAIT_LINE.match(block)
        if trait:
            name, desc = trait.groups()
            traits.append({"name": name.strip(), "desc": " ".join(desc.split())})
        else:
            # A paragraph with no "Name:" prefix is still worth showing; give it an
            # empty name, which FSB renders as description-only.
            traits.append({"name": "", "desc": " ".join(block.split())})

    return traits, actions


def build_fields(monster: dict) -> dict:
    """The FSB field dict for one creature. Order is the rendering order."""
    traits, prose_actions = split_description(monster.get("description", ""))

    # Only 287 of 841 SRD actions carry `raw` (the printed sentence); the builder
    # decomposed the rest into attack/damage/dc/area objects and left `raw` null.
    # So the two sources are complementary, not alternatives:
    #   - `raw` where present is the printed text, preferred
    #   - the description prose covers every action the builder captured
    #   - the SRD `actions[]` names are the authority on WHICH actions exist
    #
    # Order follows the description, which is the printed statblock order. Walking
    # the SRD list and looking each name up in the prose keeps an action that has
    # neither source out of the output rather than rendering a bare name.
    prose_by_name = {a["name"]: a for a in prose_actions}
    raw_by_name = {}
    for action in monster.get("actions") or []:
        raw = " ".join((action.get("raw") or "").split())
        if raw:
            raw_by_name[action.get("name", "")] = raw

    actions = []
    legendary: list[dict] = []
    mythic: list[dict] = []
    for action in monster.get("actions") or []:
        name = action.get("name", "")
        desc = raw_by_name.get(name) or prose_by_name.get(name, {}).get("desc", "")
        if not desc:
            continue
        # A packed body may hold printed legendary/mythic entries of its own; those
        # belong in FSB's separate containers, not appended to the action.
        body = [p for p in split_packed(desc) if p[2]]
        for kind, entry_name, entry_desc in body:
            if kind == "legendary":
                legendary.append({"name": entry_name, "desc": entry_desc})
            elif kind == "mythic":
                mythic.append({"name": entry_name, "desc": entry_desc})
            else:
                actions.append({"name": name, "desc": entry_desc})
    if not actions:
        actions = prose_actions

    fields: dict = {"name": monster["name"]}

    subheading = [monster.get("size"), monster.get("type"), monster.get("alignment")]
    fields["size"] = str(monster.get("size") or "")
    fields["type"] = str(monster.get("type") or "")
    fields["alignment"] = str(monster.get("alignment") or "")

    if monster.get("ac") is not None:
        fields["ac"] = monster["ac"]
    if monster.get("hp") is not None:
        fields["hp"] = monster["hp"]
    if monster.get("hp_dice"):
        fields["hit_dice"] = str(monster["hp_dice"])

    sp = speed(monster)
    if sp:
        fields["speed"] = sp

    fields["stats"] = abilities(monster)

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
    if actions:
        fields["actions"] = actions
    if legendary:
        fields["legendary_actions"] = legendary
    if mythic:
        fields["mythic_actions"] = mythic

    del subheading
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


def note_for(monster: dict) -> str:
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
        "*Derived from `dnd5e_srd.json` by `export_bestiary.py`. "
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
    args = parser.parse_args(argv)

    monsters = load_monsters()
    if args.only:
        wanted = {w.strip().lower() for w in args.only.split(",") if w.strip()}
        monsters = [m for m in monsters
                    if m["name"].lower() in wanted or str(m.get("index", "")).lower() in wanted]
        if not monsters:
            print(f"no monster matched {args.only!r}", file=sys.stderr)
            return 1

    if args.stats:
        coverage(monsters or load_monsters())
        return 0

    out = args.out / "Bestiary"
    out.mkdir(parents=True, exist_ok=True)

    for monster in monsters:
        path = out / f"{safe_name(monster['name'])}.md"
        path.write_text(note_for(monster), encoding="utf-8")

    print(f"wrote {len(monsters)} notes to {out}")
    print("\nTo view them, open that directory as an Obsidian vault and install")
    print("Fantasy Statblocks (community plugin), then set the Bestiary Folder")
    print("to Bestiary/ under settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
