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


@dataclass
class DMReply:
    narration: str
    escalate: str | None = None
    command: str | None = None


def strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _text_field(data: dict, key: str):
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse(text: str) -> DMReply:
    text = strip_think(text)
    data = {}
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
    return DMReply(text, _text_field(data, "escalate"), _text_field(data, "command"))
