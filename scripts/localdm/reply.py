"""reply.py: split a DM reply into narration and its trailing JSON block.

The DM prompt asks for narration, then one JSON line:
    {"escalate": "<question for the advisor>" | null, "command": "<tactics command>" | null}
Small models forget it or mangle it; a reply without valid JSON is all
narration, and wrong-typed fields are treated as null.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```\s*$", re.S)
_BARE = re.compile(r"(\{[^{}]*\})\s*$", re.S)


_CUT_JSON = re.compile(r'\s*\{\s*"(?:escalate|command|check)"[^{}]*\Z')
_PROMPT_TAIL = re.compile(r"\s*(?:\n|^)\s*What (?:do|would) you (?:do|like to do)(?: next)?\?\s*\Z", re.I)


@dataclass
class DMReply:
    narration: str
    escalate: str | None = None
    command: str | None = None
    check: str | None = None        # "Investigation 13": skill and DC for an ability check


def strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _text_field(data: dict, key: str):
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse(text: str) -> DMReply:
    text = strip_think(text)
    data = {}
    text = _CUT_JSON.sub("", text)                       # reply cut off inside the JSON line
    m = _FENCED.search(text) or _BARE.search(text)
    if m:
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and data:
            text = text[:m.start()].rstrip()
        else:
            data = {}
    text = _PROMPT_TAIL.sub("", text).rstrip()
    return DMReply(text, _text_field(data, "escalate"), _text_field(data, "command"),
                   _text_field(data, "check"))


# Guardrail: the DM may not put words, thoughts or feelings in the player's mouth.
_PLAYER_VOICE = re.compile(
    r"\byou\s+(?:say|ask|reply|answer|whisper|mutter|murmur|shout|call out|announce|"
    r"decide|realize|feel|think|wonder|smile|nod|sigh|laugh|remember)\b"
    r"|[\"\u201d],?\s+you\s+\w+", re.I)


def speaks_for_player(narration: str) -> bool:
    """True when the narration writes speech, thoughts or feelings for the player."""
    return bool(_PLAYER_VOICE.search(narration or ""))
