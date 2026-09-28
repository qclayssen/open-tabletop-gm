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


# Guardrail: a failed check must change the world. Applied Standard 16.
#
# The model narrates the failure, and its phrasing overlaps the defect:
# "you fail to pick the lock, but the door swings wide" is the prose we want, while
# "you fail to pick the lock" is exactly what we are guarding against. So the
# phrases split in two.
#
#   HARD  — never acceptable in the prose at all, in any context. "Nothing happens"
#           and "try again" are a stall by definition, so they need no escape.
#   SOFT  — legitimate inside good fail-forward prose, so they only count when
#           nothing else shows the world reacting.
#
# The forward list is "something else happened" vocabulary: a third party, a noise,
# or a stated consequence. Deliberately NOT scenery — an early draft listed door,
# corridor and torchlight, and "You fail to open the door. The lock is jammed."
# then read as forward motion, which is the exact sentence this exists to catch.
#
# A miss is the expensive direction: flagging good prose costs one wasted call and
# the caller keeps the original draft anyway (see play._check_narration), while
# missing a real stall ships the defect. So the forward list leans generous.
_HARD_STALL = re.compile(
    r"\bnothing happens\b"
    r"|\b(?:try|roll) (?:again|another time|once more)\b"
    r"|\bsituation (?:remains|is) unchanged\b"
    r"|\bnothing has changed\b", re.I)
_SOFT_STALL = re.compile(
    r"\byou fail\b"
    r"|\bthe attempt fails\b"
    r"|\byou (?:don'?t|do not) succeed\b"
    r"|\byou are unable\b"
    r"|\bunsuccessful\b", re.I)
_FORWARD = re.compile(
    # a third party perceived something
    r"\b(?:hear|hears|heard|notice|notices|noticed|sees?|saw|spot|spots|turns?|"
    r"watches?|stares?|waits?|arrives?|comes?|enters?|rounds?|approaches?|listens?|"
    r"shouts?|yells?|calls?|summons?|sends?)\b"
    # the attempt made a noise or left a mark
    r"|\b(?:snap|snaps|snapped|echo|echoes|echoed|creak|creaks|rattle|clatter|thud|"
    r"crack|clangs?|bangs?|thumps?|slides?|slips?|springs?|gives?|shifts?|wrenches?|"
    r"tears?|rends?|drops?|falls?|spills?|sweeps?)\b"
    # something audible or a person arrived
    r"|\b(?:noise|noises|sound|sounds|commotion|alarm|alert|footsteps|boots|voices?|"
    r"guards?|patrol|shadow|shadows|trapped|exposed|cover)\b"
    # a consequence stated outright
    r"|\b(?:instead|however|until|before|must|now|already|too late|at the cost|"
    r"in exchange|only if|forced)\b", re.I)


def is_dead_stop(narration: str) -> bool:
    """True when a failed check was narrated as a stall: the attempt failed and
    nothing in the world changed, so the only move left is to ask for a reroll.

    A heuristic, not a proof. `_check_narration` reads a flag as "worth rewriting
    once", not "must be rewritten" — a false positive costs a call and nothing else.
    """
    text = narration or ""
    if _HARD_STALL.search(text):
        return True
    return bool(_SOFT_STALL.search(text)) and not _FORWARD.search(text)
