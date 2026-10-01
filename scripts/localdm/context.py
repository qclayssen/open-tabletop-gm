"""context.py: the messages for one DM call.

The system message is the DM prompt plus a digest of state.md. It changes only
when state.md does, so Ollama can reuse its prompt cache and paid tiers can
use prompt caching. Everything that moves each turn goes in one user message,
oldest recent turns trimmed first to stay under a character budget.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time

import world_queue                # scripts/ is on sys.path via localdm/__init__

from . import canon as canon_mod

PROMPTS = pathlib.Path(__file__).resolve().parent / "prompts"
# What the DM is told, in trim order: state_digest truncates at `limit`, so the
# sections a turn most depends on come first.
#
# World State and Faction Moves used to be absent, so the off-screen faction clocks
# in world.py were computed and then thrown away: the GM wrote "the Ninefold moved
# against Frog Pond" and the DM never saw it, so it could not put the consequence in
# front of the player. Faction Moves is ranked high for that reason: it is the
# section world.py:241 tells the GM to record into, and putting it last is what
# would have lost it on exactly the campaigns with the richest world state.
# Recent Events is history where Live State Flags is current state, so both fit.
#
# World Queue sits in state.md between Faction Moves and Recent Events, and the digest
# follows state.md order, so it is trimmed before Active Quests and after Live State
# Flags. It is optional (a campaign without the section is unchanged), and it is
# rendered by world_queue.digest_lines rather than dumped as raw YAML: fired entries
# in full, pending ones as unsurfaced hooks the DM must not reveal.
#
# Campaign Arc is deliberately still absent: templates/state.md's steering_notes
# and outstanding_beats are GM-only, and handing the DM the whole arc is how NPCs
# end up voicing the mystery before the player has earned it. Active Combat is safe
# to include because tactics/cli.py:349 writes only a pointer ("read
# combat/encounter.json"), never HP or positions, so it cannot contradict the
# Engine section.
DIGEST_SECTIONS = ("Current Situation", "Pinned Facts", "World State", "Faction Moves",
                   "Live State Flags", "Active Quests", "Open Threads & Rumours",
                   "Recent Events", "Active Combat", "GM Style Notes", "World Queue")
# Sections in DIGEST_SECTIONS that older campaigns legitimately lack; the linter
# does not call their absence an error.
OPTIONAL_DIGEST_SECTIONS = ("World Queue",)
LABEL = {"player": "Player", "dm": "GM", "engine": "Engine"}

_HEADING = re.compile(r"^## +(.+?)\s*$", re.M)
_COUNCIL = re.compile(r"^\W*council:\s*(\w+)", re.M | re.I)


def dm_prompt(no_think: bool | None = None) -> str:
    """The DM prompt; ends with /no_think (Qwen3's switch) unless GM_NO_THINK=0."""
    text = (PROMPTS / "dm.md").read_text(encoding="utf-8").strip()
    if no_think is None:
        no_think = os.environ.get("GM_NO_THINK", "1") != "0"
    return text + "\n/no_think" if no_think else text


def _is_helper(line: str) -> bool:
    s = line.strip()
    return s.startswith("*") and s.endswith("*") and not s.startswith("**")


def _truncate(text: str, limit: int) -> str:
    """Trim to `limit` on a line boundary, and always say when anything was dropped.

    A bare [:limit] cut mid-line and said nothing, so a campaign whose state.md
    outgrew the budget lost its tail invisibly. With the digest now carrying the
    faction and threat state, silently losing that is worse than a wasted token, so
    the marker is reserved out of the budget rather than appended only when it
    happens to fit.
    """
    if len(text) <= limit:
        return text
    marker = "\n\n[truncated: more campaign state exists; ask the GM to keep state.md tighter]"
    room = limit - len(marker)
    if room <= 0:                     # too small for a line and a marker alike
        return marker[:limit]
    kept = text[:room].rsplit("\n", 1)[0]
    return kept + marker


def state_digest(state_md: str, sections=DIGEST_SECTIONS, limit: int = 3000) -> str:
    """The state.md sections the DM reads, trimmed to what is actually filled in.

    Uses the same unfilled-line test as notes_digest, so a GM who has not filled in
    World State yet costs the prompt nothing rather than briefing the DM that the
    in-world date is "<Day, Month, Year - canonical source>".
    """
    heads = list(_HEADING.finditer(state_md or ""))
    parts = []
    for i, m in enumerate(heads):
        if m.group(1) not in sections:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(state_md)
        if m.group(1) == "World Queue":
            body = world_queue.digest_lines(state_md[m.end():end])
        else:
            body = [line for line in state_md[m.end():end].splitlines()
                    if line.strip() and not is_template_line(line)]
        if body:
            parts.append(f"### {m.group(1)}\n" + "\n".join(body))
    return _truncate("\n\n".join(parts), limit)


SHEET_SECTIONS = ("Identity", "Combat Stats", "Features & Traits", "Equipment & Inventory",
                  "Attacks", "Spell Slots (if applicable)", "Known Spells / Cantrips")


def sheet_digest(camp_dir, sections=SHEET_SECTIONS, limit: int = 3000) -> str:
    """The player character's sheet, trimmed: who they are, what they carry.

    Keeps the DM from inventing gear or abilities. Reads characters/*.md; the
    first sheet found is the player's (solo table).
    """
    try:
        sheet = sorted((pathlib.Path(camp_dir) / "characters").glob("*.md"))[0]
        text = sheet.read_text(encoding="utf-8")
    except (OSError, IndexError):
        return ""
    heads = list(re.finditer(r"^#{2,3} +(.+?)\s*$", text, re.M))
    parts = []
    for i, m in enumerate(heads):
        if m.group(1) not in sections:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = [ln for ln in text[m.end():end].splitlines() if ln.strip()]
        if body:
            parts.append(f"### Player character: {m.group(1)}\n" + "\n".join(body))
    return "\n\n".join(parts)[:limit]


# world.md and npcs.md are the campaign's authored notes; faction_log.md is what
# world.py actually writes the faction clock results to, and leaving it out meant
# the DM was told about a faction move only if the GM transcribed it into state.md
# by hand. The GM-only marker lines world.py writes are stripped by is_template_line.
NOTE_FILES = ("world.md", "npcs.md", "faction_log.md")
_HEAD_LINE = re.compile(r"^#{1,6} ")
_TEMPLATE_DEFAULT = re.compile(r"Attitude toward party:\*\*\s*neutral|Current stage:\*\*\s*1\b", re.I)
_PLACEHOLDER = re.compile(r"<[^>\n]+>")
_EMPTY_FIELD = re.compile(r"\s*(?:[-*]\s+)?\*\*[^*]+:\*\*[\s|]*(?:\*\*[^*]+:\*\*[\s|]*)*")
# A campaign vault is an Obsidian vault, so a cross-reference is written
# [[tournament/00-framework.md|Convergence Trials]]. Those angle brackets are
# link syntax, not a template placeholder: unmasked, _PLACEHOLDER matches
# "<tournament/00-framework.md|Convergence Trials>" and the line is classified
# unfilled, which both warned in the linter and dropped the line from the DM's
# prompt entirely. A linked line is content.
_WIKILINK = re.compile(r"\[\[[^\]\n]*\]\](?!\[)")


def unlink(text: str) -> str:
    """`[[target|Shown]]` -> `Shown`, `[[target]]` -> `target`.

    Used wherever a name is read back out of the file: a link and the word it
    stands for are the same name, and the linter compares index rows to `###`
    headings that are usually not themselves linked.
    """
    return _WIKILINK.sub(
        lambda m: (m.group(0)[2:-2].split("|")[-1].strip() or m.group(0)[2:-2]), text)


def mask_links(text: str) -> str:
    """Whole links replaced by a neutral token, brackets and all.

    Distinct from unlink, which has to keep the readable name. The template
    tests must not see a link's *target* either: a vault note can legitimately
    be called `npc-files/<name>.md`, and `_PLACEHOLDER` would read that as an
    unfilled `<name>` and drop a finished line from the DM's prompt. Blanking
    the link entirely leaves the sentence around it to be judged.
    """
    return _WIKILINK.sub(" link ", text)


def is_template_line(line: str) -> str:
    """Why this line is still blank template, or "" if it carries real content.

    Shared with campaign_lint.py on purpose. The digest used to drop these lines
    silently, which made a half-filled world.md and a finished one
    indistinguishable; a GM had no way to see what the DM was not being told.
    Naming the reason here means the prompt and the linter cannot disagree about
    what counts as unfilled.
    """
    if _is_helper(line):
        return "helper text (italic instructions to the GM)"
    # Every template test below runs on the line with its links blanked, so link
    # syntax can never be mistaken for an empty field or a placeholder.
    flat = mask_links(line)
    if _PLACEHOLDER.search(flat):
        return "unfilled <placeholder>"
    if _TEMPLATE_DEFAULT.search(flat):
        return "template default"
    if _EMPTY_FIELD.fullmatch(flat):
        return "empty **Field:**"
    if re.fullmatch(r"[|\s:-]*", line):
        return "empty table"
    return ""


def notes_digest(camp_dir, files=NOTE_FILES, limit: int = 2500) -> str:
    """world.md and npcs.md, trimmed: what the DM and advisors check facts against.

    A fresh campaign's notes are a blank template; drops italic helper lines, <placeholders>,
    empty "**Field:**" lines, table rows with no data and headings with nothing under them,
    so an unfilled file costs nothing.
    """
    parts = []
    for name in files:
        try:
            text = (pathlib.Path(camp_dir) / name).read_text(encoding="utf-8")
        except OSError:
            continue
        keep = [ln for ln in text.splitlines() if ln.strip() and not is_template_line(ln)]
        rows = [i for i, ln in enumerate(keep) if ln.lstrip().startswith("|")]
        if len(rows) == 1:                         # a table header and no data rows
            keep.pop(rows[0])
        body = [ln for i, ln in enumerate(keep)
                if not (_HEAD_LINE.match(ln)
                        and (i + 1 == len(keep) or _HEAD_LINE.match(keep[i + 1])))]
        while body != keep:                        # a heading emptied by the pass above
            keep = body
            body = [ln for i, ln in enumerate(keep)
                    if not (_HEAD_LINE.match(ln)
                            and (i + 1 == len(keep) or _HEAD_LINE.match(keep[i + 1])))]
        if body:
            parts.append(f"### {name}\n" + "\n".join(body))
    return "\n\n".join(parts)[:limit]


def _active_ac(camp_dir, name: str):
    """B4: the still-active AC override tracker.json has for `name` (e.g. from an
    out-of-combat Mage Armor cast), or None. This is a plain read of a number a
    caller already computed (play.py's _cast_spell) and expires with the effect
    like any other tracker.json entry; no 5e rule is decided here."""
    try:
        state = json.loads((pathlib.Path(camp_dir) / "tracker.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ent = state.get(name.lower()) if isinstance(state, dict) else None
    if not ent:
        return None
    now, best = time.time(), None
    for eff in ent.get("effects", []):
        if "ac" not in eff:
            continue
        dt = eff.get("duration_type", "indefinite")
        if dt in ("minutes", "hours") and now - eff.get("started_at", now) >= eff.get(
                "duration_seconds", 0):
            continue                        # expired
        if dt == "rounds" and eff.get("duration_remaining", 0) <= 0:
            continue                        # expired
        best = eff["ac"]
    return best
def party_stats(camp_dir) -> list:
    """Display sidebar entries (name, race, class, level, hp, ac, ...) from characters/*.md."""
    out = []
    for sheet in sorted((pathlib.Path(camp_dir) / "characters").glob("*.md")):
        try:
            text = sheet.read_text(encoding="utf-8")
        except OSError:
            continue

        def field(label):
            m = re.search(rf"\*\*{label}:\*\*\s*([^|\n]+)", text)
            return m.group(1).strip() if m else ""

        def num(label, default):
            m = re.match(r"[-+]?\d+", field(label))
            return int(m.group()) if m else default

        hp = re.search(r"\*\*HP:\*\*\s*(\d+)\s*/\s*(\d+)", text)
        cls = re.sub(r"\s*\d+.*$", "", field("Class")).strip()
        entry = {"name": sheet.stem, "race": re.sub(r"\s*\(.*$", "", field("Race")).strip(),
                 "class": cls, "level": num("Level", 1),
                 "hp": {"current": int(hp.group(1)) if hp else 1,
                        "max": int(hp.group(2)) if hp else 1, "temp": num("Temp HP", 0)},
                 "ac": num("AC", 10), "initiative": field("Initiative") or "+0",
                 "speed": num("Speed", 30)}
        override = _active_ac(camp_dir, entry["name"])
        if override is not None:
            entry["ac"] = override
        out.append(entry)
    return out


def first_sheet_path(camp_dir):
    """Path to the first character sheet (alphabetical), or None. The local-DM loop
    has no per-character routing, so a single active PC is assumed here too."""
    try:
        return sorted((pathlib.Path(camp_dir) / "characters").glob("*.md"))[0]
    except IndexError:
        return None
def skill_bonus(camp_dir, skill: str):
    """(name, bonus) for a skill in the first sheet's skills table, else None."""
    sheet = first_sheet_path(camp_dir)
    if sheet is None:
        return None
    try:
        text = sheet.read_text(encoding="utf-8")
    except OSError:
        return None
    for m in re.finditer(r"^\|\s*([A-Za-z ]+?)\s*\|[^|]*\|\s*([+-]?\d+)\s*\|", text, re.M):
        if m.group(1).lower() == skill.strip().lower():
            return sheet.stem, m.group(1), int(m.group(2))
    return None


def sheet_skills(camp_dir):
    """The skill names on the first sheet's skills table.

    Only used to name a fabricated skill back to the DM, so order does not matter and
    a sheet with no skills table simply yields nothing.
    """
    sheet = first_sheet_path(camp_dir)
    if sheet is None:
        return []
    try:
        text = sheet.read_text(encoding="utf-8")
    except OSError:
        return []
    return [m.group(1).strip() for m in
            re.finditer(r"^\|\s*([A-Za-z][A-Za-z ]*?)\s*\|[^|]*\|\s*[+-]?\d+\s*\|", text, re.M)]


def council_setting(state_md: str) -> str:
    m = _COUNCIL.search(state_md or "")
    return "off" if m and m.group(1).lower() == "off" else "auto"


def build_messages(system: str, digest: str, summary: str, recent: list, *, engine: str = "",
                   notes: str = "", player: str = "", task: str = "",
                   canon: list = (), budget: int = 12000) -> list:
    sys_msg = system + (f"\n\n## Campaign\n{digest}" if digest else "")
    head = [f"## Story so far\n{summary.strip()}"] if summary else []
    # Canon outranks old turns when the budget bites: a verbatim line the player
    # already heard is worth more than a stale turn that is about to be
    # summarized away. Trimmed from the least relevant end, then the turns go.
    can = [canon_mod.render_one(r) for r in canon] if canon else []
    tail = []
    if engine:
        tail.append(f"## Engine (facts, do not change them)\n{engine.strip()}")
    if notes:
        tail.append(f"## Advisor notes (GM only, never read aloud)\n{notes.strip()}")
    if player:
        tail.append(f"## Player now\n{player.strip()}")
    if task:
        tail.append(f"## Your task\n{task.strip()}")
    lines = [f"{LABEL[t['role']]}: {t['text'].strip()}" for t in recent if t["role"] in LABEL]
    fixed = len(sys_msg) + sum(len(p) + 2 for p in head + tail) + len("## Recent turns\n")
    header = len(canon_mod.HEADER) + 1 if can else 0   # charged only when there is canon

    def spent():
        return (fixed + header + sum(len(p) + 1 for p in can)
                + sum(len(p) + 1 for p in lines))

    while can and spent() > budget:
        can.pop()                           # canon.relevant sorts best-first
    while lines and spent() > budget:
        lines.pop(0)
    body = head + ([canon_mod.HEADER + "\n" + "\n".join(can)] if can else []) \
        + (["## Recent turns\n" + "\n".join(lines)] if lines else []) + tail
    return [{"role": "system", "content": sys_msg},
            {"role": "user", "content": "\n\n".join(body)}]
