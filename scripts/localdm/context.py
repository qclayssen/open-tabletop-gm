"""context.py: the messages for one DM call.

The system message is the static DM prompt: it does not change turn to turn, so
Ollama can reuse its prompt cache and paid tiers can use prompt caching.
Everything that moves each turn goes in one user message, oldest recent turns
trimmed first to stay under a character budget. That budget covers the dynamic
content only; the static prompt is not charged against it (see build_messages).
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
    if _PLACEHOLDER.search(line):
        return "unfilled <placeholder>"
    if _TEMPLATE_DEFAULT.search(line):
        return "template default"
    if _EMPTY_FIELD.fullmatch(line):
        return "empty **Field:**"
    if re.fullmatch(r"[|\s:-]*", line):
        return "empty table"
    return ""


def _tidy_yaml_blocks(lines: list[str]) -> list[str]:
    """Drop a fenced block emptied by the template-line pass, and strip trailing
    `# comments` from the values under `## Campaign Rhythm` (only the resolved
    values reach the prompt). An unfilled rhythm block therefore costs nothing."""
    out, in_rhythm = [], False
    for i, ln in enumerate(lines):
        if _HEAD_LINE.match(ln):
            in_rhythm = ln.strip() == "## Campaign Rhythm"
        elif in_rhythm and not ln.lstrip().startswith("```"):
            ln = re.sub(r"\s+#.*$", "", ln)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if ln.rstrip().endswith(":") and len(nxt) - len(nxt.lstrip()) <= len(ln) - len(ln.lstrip()):
                continue                           # a parent key whose children were all blank
        out.append(ln)
    text = re.sub(r"^\s*```\w*\n\s*```\s*$\n?", "", "\n".join(out), flags=re.M)
    return [ln for ln in text.split("\n") if ln.strip()]


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
        keep = _tidy_yaml_blocks(keep)
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


def _inventory_line(text: str) -> str:
    """The "Equipment & Inventory" section's filled-in entries as one line, else ""."""
    m = re.search(r"^##\s*Equipment[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return ""
    items = []
    for ln in m.group(1).splitlines():
        ln = re.sub(r"\*\*([^*]+):\*\*", "", ln).strip().lstrip("-*").strip()
        if ln and not re.fullmatch(r"0gp 0sp 0cp|[-_ ]*", ln):
            items.append(ln.rstrip("."))
    return "; ".join(items)


def _sheet_hp(text: str):
    """The sheet's own "**HP:** 6/8" as (current, max), or None when it states no HP.

    Deliberately not party_stats: that fills 1/1 in for a sheet with no HP line, and a
    question the player asked deserves "the sheet says nothing" over a confident 1/1."""
    m = re.search(r"\*\*HP:\*\*\s*(\d+)\s*/\s*(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _sheet_ac(camp_dir, name: str, text: str):
    """The sheet's own "**AC:** 12", or a still-active AC override (Mage Armor), else None.

    Same reason as the HP: party_stats defaults a missing AC to 10, which is a bare-
    skinned guess wearing the sheet's authority."""
    override = _active_ac(camp_dir, name)
    if override is not None:
        return override
    m = re.search(r"\*\*AC:\*\*\s*(\d+)", text)
    return int(m.group(1)) if m else None


def sheet_facts(camp_dir) -> dict | None:
    """Sheet numbers for an out-of-character question, or None with no sheet.

    Plain reads of what the sheet says (HP, AC, both None when the sheet states neither,
    with a still-active AC override winning so an active Mage Armor shows) plus the passive
    scores the check policy already uses (10 + skill bonus)."""
    from localdm import checks
    sheet = first_sheet_path(camp_dir)
    if sheet is None:
        return None
    try:
        text = sheet.read_text(encoding="utf-8")
    except OSError:
        return None
    passive = {}
    for skill in ("perception", "insight", "investigation"):
        found = skill_bonus(camp_dir, skill)
        if found:
            passive[skill] = checks.passive_score(found[2])
    return {"name": sheet.stem, "hp": _sheet_hp(text), "ac": _sheet_ac(camp_dir, sheet.stem, text),
            "passive": passive, "inventory": _inventory_line(text)}


def council_setting(state_md: str) -> str:
    m = _COUNCIL.search(state_md or "")
    return "off" if m and m.group(1).lower() == "off" else "auto"


def build_messages(system: str, digest: str, summary: str, recent: list, *, engine: str = "",
                   notes: str = "", player: str = "", task: str = "",
                   canon: list = (), budget: int = 12000,
                   report: dict | None = None) -> list:
    """Two messages: a static system head, and one ordered user message.

    WHY THE DIGEST IS NOT IN THE SYSTEM MESSAGE
    ============================================
    The system message used to carry `## Campaign` appended to the prompt. That
    made it the cache head's tail, and the digest changes every turn, so every
    turn invalidated the whole prefix: with a paid endpoint paying full input
    price on ~2.3k static tokens that never change. The system message is now
    static only, and the digest moved to the head of the user message, which is
    the smallest change that makes the `llm.CACHE_ENV` opt-in worth turning on.

    The heading is still literally `## Campaign`, and it is still the first thing
    read, because `prompts/dm.md` refers to it by name in at least six places
    ("Use the sheet in the Campaign section", "An NPC's wants come from their
    entry in the Campaign section", ...). Renaming the block, or burying it under
    a generic heading, would leave every one of those instructions pointing at
    nothing. A cache optimisation that breaks six prompt contracts is not one.

    It is in `head`, not in `lines`, so the budget loop below trims conversation
    and never trims the campaign facts.

    WHY THE STATIC PROMPT IS NOT CHARGED AGAINST THE BUDGET
    =======================================================
    `fixed` used to be `len(sys_msg) + ...`, so the 9030-char `prompts/dm.md`
    came out of the same 12000-char allowance as the conversation. That left
    ~2070 chars for the campaign digest *and* the recent turns together, while
    the digest's own per-file caps allow 8500 (`state_digest` 3000 +
    `sheet_digest` 3000 + `notes_digest` 2500). Measured on the harness in the
    outer repo (`scripts/measure_turn_tokens.py`): at 4797 chars of digest the
    `## Recent turns` section is evicted entirely, so a campaign whose files are
    filled in got a full load of lore and **zero conversation history**.

    The static prompt does not compete for context with the conversation, it
    competes for cache, and those are different resources. So the budget covers
    the dynamic content only. Two things are deliberately unchanged: the unit
    is still characters (`--budget`'s help says so), because converting it to
    tokens would silently move every existing session's effective context, and
    the digest is still never trimmed, because `dm.md` refers to it by name.

    `report`, when given, is filled in place with `system`, `dynamic`, `budget`,
    `turns` and `offered`, so the split is observable instead of inferred. The
    two sizes are the real message lengths. `dynamic` is NOT bounded by
    `budget`: the loop trims canon and then turns, but never `head`, so a digest
    larger than the budget on its own puts `dynamic` over the line with nothing
    left to give. That is deliberate here (dm.md refers to `## Campaign` by
    name, so a truncated digest is a broken contract rather than a smaller
    prompt) and it is why the split is now reported: a `dynamic` figure past
    `budget` is the operator's signal that the digest, not the conversation, is
    what needs the cap. Bounding the combined digest is tracked in
    `ROADMAP-ideas.md` -> `## Session/context management`.
    """
    sys_msg = system
    head = ([f"## Campaign\n{digest.strip()}"] if digest and digest.strip() else [])
    if summary:
        head.append(f"## Story so far\n{summary.strip()}")
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
    offered = len(lines)
    fixed = sum(len(p) + 2 for p in head + tail) + len("## Recent turns\n")
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
    user = "\n\n".join(body)
    if report is not None:
        report.update(system=len(sys_msg), dynamic=len(user), budget=budget,
                      turns=len(lines), offered=offered)
    return [{"role": "system", "content": sys_msg},
            {"role": "user", "content": user}]
