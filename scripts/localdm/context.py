"""context.py: the messages for one DM call.

The system message is the DM prompt plus a digest of state.md. It changes only
when state.md does, so Ollama can reuse its prompt cache and paid tiers can
use prompt caching. Everything that moves each turn goes in one user message,
oldest recent turns trimmed first to stay under a character budget.
"""
from __future__ import annotations

import os
import pathlib
import re

PROMPTS = pathlib.Path(__file__).resolve().parent / "prompts"
DIGEST_SECTIONS = ("Current Situation", "Pinned Facts", "Active Quests", "Open Threads & Rumours",
                   "Live State Flags", "GM Style Notes")
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


def state_digest(state_md: str, sections=DIGEST_SECTIONS, limit: int = 3000) -> str:
    heads = list(_HEADING.finditer(state_md or ""))
    parts = []
    for i, m in enumerate(heads):
        if m.group(1) not in sections:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(state_md)
        body = [line for line in state_md[m.end():end].splitlines()
                if line.strip() and not _is_helper(line)]
        if body:
            parts.append(f"### {m.group(1)}\n" + "\n".join(body))
    return "\n\n".join(parts)[:limit]


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


NOTE_FILES = ("world.md", "npcs.md")
_HEAD_LINE = re.compile(r"^#{1,6} ")
_TEMPLATE_DEFAULT = re.compile(r"Attitude toward party:\*\*\s*neutral|Current stage:\*\*\s*1\b", re.I)


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
        keep = [ln for ln in text.splitlines()
                if ln.strip() and not _is_helper(ln) and not re.search(r"<[^>\n]+>", ln)
                and not re.fullmatch(r"[|\s:-]*", ln)
                and not _TEMPLATE_DEFAULT.search(ln)
                and not re.fullmatch(r"\s*(?:[-*]\s+)?\*\*[^*]+:\*\*[\s|]*(?:\*\*[^*]+:\*\*[\s|]*)*", ln)]
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
        out.append(entry)
    return out


def skill_bonus(camp_dir, skill: str):
    """(name, bonus) for a skill in the first sheet's skills table, else None."""
    try:
        sheet = sorted((pathlib.Path(camp_dir) / "characters").glob("*.md"))[0]
        text = sheet.read_text(encoding="utf-8")
    except (OSError, IndexError):
        return None
    for m in re.finditer(r"^\|\s*([A-Za-z ]+?)\s*\|[^|]*\|\s*([+-]?\d+)\s*\|", text, re.M):
        if m.group(1).lower() == skill.strip().lower():
            return sheet.stem, m.group(1), int(m.group(2))
    return None


def council_setting(state_md: str) -> str:
    m = _COUNCIL.search(state_md or "")
    return "off" if m and m.group(1).lower() == "off" else "auto"


def build_messages(system: str, digest: str, summary: str, recent: list, *, engine: str = "",
                   notes: str = "", player: str = "", task: str = "",
                   budget: int = 12000) -> list:
    sys_msg = system + (f"\n\n## Campaign\n{digest}" if digest else "")
    head = [f"## Story so far\n{summary.strip()}"] if summary else []
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
    while lines and fixed + sum(len(line) + 1 for line in lines) > budget:
        lines.pop(0)
    body = head + (["## Recent turns\n" + "\n".join(lines)] if lines else []) + tail
    return [{"role": "system", "content": sys_msg},
            {"role": "user", "content": "\n\n".join(body)}]
