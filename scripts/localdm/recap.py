"""recap.py: a "previously on..." recap and a pre-session prep checklist.

Both are assembled deterministically from stored state, with no model call, so
they cannot invent anything and cannot fail. Sources:

    recap    localdm/summary.md (the latest fold), state.md `## Recent Events`,
             localdm/canon.jsonl (verbatim lines the player was already shown)
    prep     characters/*.md (prepared spells, consumables) and state.md
             (marching / watch order, `## Open Threads & Rumours`, `## Active Quests`)

Nothing here reads the GM-only sections of state.md, and any line that names
itself a secret, spoiler or GM/DM-only note is dropped. Canon is player-shown by
construction (canon.verify keeps only spans the narration actually contained).

The gap is measured from the newest mtime of localdm/transcript.jsonl and
usage.jsonl: the transcript carries no timestamps of its own.
"""
from __future__ import annotations

import os
import pathlib
import re
import time

from . import canon as canon_mod
from . import context

DEFAULT_GAP_HOURS = 6.0
EVENT_LINES = 5
CANON_LINES = 4
SUMMARY_CHARS = 700

_PRIVATE = re.compile(r"\b(?:gm|dm)[- ]?(?:only|note|notes)\b|\bsecret\b|\bspoilers?\b"
                      r"|\bhidden\b|\bsealed\b", re.I)
# Credential-shape filter: catches API keys, bearer tokens, secrets in assignment form
# This is a shape-based filter, not a word list, so it catches OMNIROUTE_API_KEY=... etc.
_CREDENTIAL = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|secret[_-]?key|private[_-]?key|bearer[_-]?token)\s*[:=]\s*\S+"
    r"|(?:authorization|x-api-key)\s*:\s*\S+"
    r"|\bbearer\s+[A-Za-z0-9._-]+\b"
)
_CONSUMABLE = re.compile(r"ration|potion|scroll|torch|oil|arrow|bolt|waterskin|water|food|"
                         r"antitoxin|healer|elixir|bomb|flask|rope|lantern|candle", re.I)
_ORDER = re.compile(r"marching[ _]order|watch[ _]order|first[ _]watch|night watch|watches", re.I)


def gap_hours(camp_dir, now=None):
    """Hours since the campaign was last played, or None if it never was."""
    d = pathlib.Path(camp_dir) / "localdm"
    times = []
    for name in ("transcript.jsonl", "usage.jsonl"):
        try:
            times.append((d / name).stat().st_mtime)
        except OSError:
            pass
    if not times:
        return None
    return max(0.0, ((time.time() if now is None else now) - max(times)) / 3600.0)


def gap_setting(env=None) -> float:
    """GM_RECAP_GAP_HOURS, else the default. Unparseable values fall back."""
    raw = (os.environ if env is None else env).get("GM_RECAP_GAP_HOURS", "").strip()
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_GAP_HOURS
    except ValueError:
        return DEFAULT_GAP_HOURS


def _public(line: str) -> bool:
    return (bool(line.strip())
            and not context.is_template_line(line)
            and not _PRIVATE.search(line)
            and not _CREDENTIAL.search(line))


def section_lines(state_md: str, name: str) -> list:
    """Filled, non-private lines of one `## name` section of state.md."""
    heads = list(context._HEADING.finditer(state_md or ""))
    for i, m in enumerate(heads):
        if m.group(1).strip().lower().startswith(name.lower()):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(state_md)
            return [ln.rstrip() for ln in state_md[m.end():end].splitlines() if _public(ln)]
    return []


def _read(path) -> str:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _clip(text: str, limit: int) -> str:
    """The last `limit` characters, cut at a sentence or line start, never mid-word."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    m = re.search(r"(?<=[.!?]) +", tail)
    return ("..." + tail[m.end():]) if m else "..." + tail.split(" ", 1)[-1]


def build_recap(camp_dir) -> str:
    """The "previously on..." text, or "" when nothing is stored to recap."""
    camp = pathlib.Path(camp_dir)
    parts = []
    summary = " ".join(l for l in _read(camp / "localdm" / "summary.md").splitlines()
                       if _public(l) and not l.startswith("#"))
    if summary:
        parts.append(_clip(summary, SUMMARY_CHARS))
    events = section_lines(_read(camp / "state.md"), "Recent Events")
    if events:
        parts.append("Recently:\n" + "\n".join(_bullet(e) for e in events[-EVENT_LINES:]))
    records = [r for r in canon_mod.Canon(camp / "localdm").records()
               if not _PRIVATE.search(r.get("text", ""))]
    if records:
        parts.append("Words and moments you were there for:\n"
                     + "\n".join(canon_mod.render_one(r) for r in records[-CANON_LINES:]))
    if not parts:
        return ""
    return "Previously on this campaign...\n\n" + "\n\n".join(parts)


def _bullet(line: str) -> str:
    return "- " + line.strip().lstrip("-*").strip()


def _sheets(camp_dir) -> list:
    return sorted((pathlib.Path(camp_dir) / "characters").glob("*.md"))


def _sheet_section(text: str, name: str) -> list:
    heads = list(re.finditer(r"^#{2,3} +(.+?)\s*$", text, re.M))
    for i, m in enumerate(heads):
        if m.group(1).lower().startswith(name.lower()):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            return [ln.strip() for ln in text[m.end():end].splitlines() if ln.strip()]
    return []


def build_prep(camp_dir) -> str:
    """A short checklist of what to settle before play. Always returns text."""
    camp = pathlib.Path(camp_dir)
    state = _read(camp / "state.md")
    lines = []
    for sheet in _sheets(camp):
        text = _read(sheet)
        name = sheet.stem
        prepared = [l for l in text.splitlines() if re.search(r"prepared spells", l, re.I)]
        if prepared:
            lines.append(f"[ ] {name}: spells prepared. Change them now if you have rested. "
                         + _short(prepared[0]))
        gear = [l.lstrip("-* ").strip() for l in _sheet_section(text, "Equipment")
                if _CONSUMABLE.search(l)]
        if gear:
            lines.append(f"[ ] {name}: rations and consumables to check: "
                         + "; ".join(_short(g, 90) for g in gear[:4]))
    orders = [l for l in section_lines(state, "Live State Flags") + section_lines(state, "World State")
              if _ORDER.search(l)]
    lines.append("[ ] Marching order and watch order: "
                 + (_short(orders[0].strip().lstrip("-* ")) if orders
                    else "not set. Say who leads and who takes first watch."))
    threads = section_lines(state, "Open Threads") + section_lines(state, "Active Quests")
    if threads:
        lines.append("[ ] Open threads: which do you pursue first?")
        lines += [f"      {_bullet(t)}" for t in threads[:5]]
    else:
        lines.append("[ ] Open threads: none recorded yet.")
    return "Before you play (say 'skip' or just act to ignore; /prep shows this again):\n" + "\n".join(lines)


def _short(text: str, limit: int = 140) -> str:
    text = " ".join(text.replace("*", "").split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def session_start(camp_dir, *, recap=True, prep=True, min_gap=None, now=None) -> list:
    """The blocks to show when a session starts. Empty for a brand new campaign."""
    gap = gap_hours(camp_dir, now)
    if gap is None:
        return []
    out = []
    if recap and gap >= (gap_setting() if min_gap is None else min_gap):
        text = build_recap(camp_dir)
        if text:
            out.append(text)
    if prep:
        out.append(build_prep(camp_dir))
    return out
