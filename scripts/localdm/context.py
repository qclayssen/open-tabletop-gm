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
DIGEST_SECTIONS = ("Current Situation", "Pinned Facts", "Live State Flags", "GM Style Notes")
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
