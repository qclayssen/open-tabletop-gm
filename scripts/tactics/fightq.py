"""fightq.py: answer a player's question from the engine, with no model call.

A line typed during a fight that is not a command ("how far is the frog", "what can I
do", "what's my hp") is a question. This module recognises it and answers it from the
same read-only facts `status`, `targets`, `preview` and `reachable` report, in one to
four lines. Nothing here rolls, moves or writes, and no number comes from anywhere but
the engine.

Two halves, so other loops (exploration input triage) can reuse the first:

    classify(line, verbs=())  -> Question | None     pure text, no engine
    answer(enc, pc_id, q)     -> list[str]           read-only engine lookups

A line that starts with one of `verbs` is a command and is never a question.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# topic -> patterns, tried in this order (first match wins). Matched on the lowered line.
TOPICS = (
    ("movement", r"\b(how much|how many)\b.*\b(movement|move|feet|ft|speed)\b|\bmovement (left|remaining)\b"
                 r"|\bhow far can (i|we) (move|go|walk|run)\b"),
    ("options", r"\bwhat (can|could|should|do|are)\b.*\b(i|my|options?|actions?)\b.*"
                r"|\b(my|the) options\b|\bwhat now\b|\bwhat (are )?my (options|choices|actions)\b|\bhelp me\b"),
    ("hp", r"\b(hp|hit points?|health|hurt|wounded|how (badly|healthy))\b"),
    ("targets", r"\b(who|what)\b.*\b(can|could)\b.*\b(i )?(attack|hit|shoot|target|reach)\b"
                r"|\bany(one|thing) (in )?(range|reach)\b"),
    ("distance", r"\b(how far|distance|how close|how many (feet|ft|squares))\b|\bcan (i|we) reach\b"
                 r"|\bin (range|reach)\b|\bwhere (is|are)\b"),
    ("position", r"\bwhere am i\b|\bwhere (do|am) i stand\b|\bmy position\b|\bwhere (is|are) (everyone|everybody)\b"
                 r"|\bwho'?s? (is )?(here|left|alive)\b"),
)
# Questions about the character sheet. Only claimed when the line is about the speaker
# ("my", "I", "me"), so "what is the guard's AC" is never answered with the player's.
_SELF = re.compile(r"\b(my|i|me|mine|am|do i|have i)\b")
SELF_TOPICS = (
    ("passive", r"\bpassive\b|\bhow (perceptive|observant)\b"),
    ("ac", r"\b(ac|armou?r class|armou?r)\b"),
    ("hp", r"\b(hp|hit points?|health|hurt|wounded|how (badly|healthy))\b"),
    ("inventory", r"\b(inventory|equipment|gear|backpack|pack|carrying|what do i (have|carry|own)|"
                  r"what('?s| is) (in )?my (bag|pack|things))\b"),
)
_QUESTION_START = re.compile(
    r"^(where|how|what|whats|what's|can|could|who|which|whose|am|is|are|do|does|any)\b")
# Words that only frame the question, never name a creature.
_NOISE = set("""a an the is are am was do does did can could i we me my to of at in on from for it its
how far much many what whats what's where who which whose away close nearest closest next
distance feet ft squares square range reach there here now still left remaining from this that
attack hit and or with by""".split())


@dataclass(frozen=True)
class Question:
    topic: str            # movement | options | hp | targets | distance | position
    subject: str = ""     # what the player named, minus framing words ("frog", "giant frog 1")
    nearest: bool = False  # "the nearest / closest ..."
    text: str = ""        # the original line


def is_questionish(line: str) -> bool:
    low = line.strip().lower()
    return low.endswith("?") or bool(_QUESTION_START.match(low))


def classify(line: str, verbs=(), scope: str = "fight") -> Question | None:
    """A Question when `line` asks for information, else None (a command, or chatter).

    `verbs` are the words the caller treats as commands; a line starting with one is
    left to the command parser. `scope` is "fight" (the grid topics, plus "what is my AC")
    or "explore" (only the sheet topics: hp, ac, passive scores, inventory; nothing
    about a map that is not there).

    TODO(narrative review 4.2): a `rules_claim` tag ("house rule", "from now on", "give
    me", "developer mode") belongs here, answered with a fixed refusal and never sent to
    the model. Not built yet; see docs/design/NARRATIVE-expert-review-2026-09-30.md."""
    text = (line or "").strip()
    if not text:
        return None
    low = text.lower().replace("’", "'")
    first = re.split(r"\s+", low, maxsplit=1)[0].rstrip("?")
    if first in {v.lower() for v in verbs}:
        return None
    if not is_questionish(low):
        return None
    if scope == "explore":
        if not _SELF.search(low):
            return None
        for topic, pat in SELF_TOPICS:
            if re.search(pat, low):
                return Question(topic, text=text)
        return None
    for topic, pat in TOPICS:
        if re.search(pat, low):
            subject = " ".join(w for w in re.findall(r"[a-z0-9'-]+", low) if w not in _NOISE)
            return Question(topic, subject, nearest=bool(re.search(r"\b(nearest|closest)\b", low)),
                            text=text)
    if _SELF.search(low) and re.search(SELF_TOPICS[1][1], low):     # "what is my AC" mid-fight
        return Question("ac", text=text)
    return None


# ── answering ────────────────────────────────────────────────────────────────

def foes_named(enc, pc, subject: str, symbols: dict | None = None) -> list:
    """Living hostile tokens whose id or name matches `subject`, nearest first.
    `symbols` (token id -> map symbol, like "1") lets a bare "1" or "2" name a creature.
    An empty subject means every living hostile."""
    from .core import hostile
    grid = enc.board()
    foes = [t for t in enc.tokens.values() if t.active and hostile(pc, t)]
    words = [w for w in re.findall(r"[a-z0-9'-]+", (subject or "").lower()) if w not in _NOISE]
    if words:
        want = " ".join(words)
        by_symbol = {s.lower(): tid for tid, s in (symbols or {}).items()}
        exact = [t for t in foes if want in (t.id.lower(), t.name.lower()) or by_symbol.get(want) == t.id]
        if exact:
            foes = exact
        else:
            foes = [t for t in foes if all(w in t.name.lower() or w in t.id.lower() or
                                           w.rstrip("s") in t.name.lower() for w in words)]
    return sorted(foes, key=lambda t: (grid.distance(pc.pos, t.pos), t.id))


def _distance_lines(enc, pc, q: Question, symbols) -> list:
    from . import engine
    foes = foes_named(enc, pc, q.subject, symbols)
    if not foes:
        return [f"I do not see {q.subject or 'an enemy'} on the map. Type `map` to look."]
    grid, left = enc.board(), engine.remaining_movement(enc)
    if q.nearest or not q.subject:
        foes = foes[:1]
    legal = {r["target"] for r in engine.attack_options(enc, pc.id) if r["legal"]}
    lines = []
    for t in foes[:3]:
        d = grid.distance(pc.pos, t.pos)
        tail = "you can attack it now" if t.id in legal else (
            f"{left} ft of movement left" if pc.id == getattr(enc.current, "id", None) else "")
        lines.append(f"{t.name} is at {t.square}, {d} ft away" + (f"; {tail}." if tail else "."))
    return lines


def _movement_lines(enc, pc) -> list:
    from . import engine
    left, speed = engine.remaining_movement(enc), engine.rules_for(enc).speed(pc)
    line = f"{pc.name} has {left} ft of movement left (speed {speed} ft)."
    if not enc.turn.action_used and left:
        line += " Dash adds another " + f"{speed} ft."
    return [line]


def _hp_lines(enc, pc) -> list:
    tags = list(pc.conditions)
    lines = [f"{pc.name}: {pc.hp}/{pc.max_hp} HP, AC {pc.ac}" + (f" [{', '.join(tags)}]" if tags else "") + "."]
    hurt = [f"{t.name} {t.hp}/{t.max_hp}" for t in enc.tokens.values() if t.active and t.id != pc.id]
    if hurt:
        lines.append("Others: " + ", ".join(hurt) + ".")
    return lines


def _targets_lines(enc, pc) -> list:
    from . import engine
    legal = [r for r in engine.attack_options(enc, pc.id) if r["legal"]]
    if not legal:
        return ["No target is in range right now. Ask how far one is, or `move` closer."]
    return ["You can attack: " + "; ".join(
        f"{r['attack']} -> {r['target_name']} {r['hit_percent']}%" for r in legal[:4]) + "."]


def _options_lines(enc, pc) -> list:
    from . import engine, spells
    act = "your action is used" if enc.turn.action_used else "your action is ready"
    lines = [f"{act.capitalize()}; {engine.remaining_movement(enc)} ft of movement left."]
    lines += _targets_lines(enc, pc)
    try:
        names = [r["name"] for r in spells.castable(enc, pc.id) if r["ok"]][:6]
    except Exception:                                # a creature with no spell list
        names = []
    if names:
        lines.append("Castable: " + ", ".join(names) + ".")
    lines.append("Also: move, dash, disengage, dodge, end. Type `help` for all.")
    return lines[:4]


def _position_lines(enc, pc) -> list:
    lines = [f"{pc.name} is at {pc.square}."]
    grid = enc.board()
    for t in foes_named(enc, pc, "")[:3]:
        lines.append(f"{t.name} at {t.square}, {grid.distance(pc.pos, t.pos)} ft away.")
    return lines


def answer(enc, pc_id: str, q: Question, symbols: dict | None = None) -> list:
    """One to four lines answering `q` for the creature `pc_id`, read from the engine."""
    from .core import resolve
    pc = resolve(enc, pc_id)
    if q.topic == "distance":
        return _distance_lines(enc, pc, q, symbols)[:4]
    if q.topic == "movement":
        return _movement_lines(enc, pc)
    if q.topic in ("hp", "ac"):
        return _hp_lines(enc, pc)[:4]
    if q.topic == "targets":
        return _targets_lines(enc, pc)
    if q.topic == "options":
        return _options_lines(enc, pc)
    return _position_lines(enc, pc)[:4]


# ── exploration: the sheet answers ───────────────────────────────────────────

def answer_sheet(q: Question, facts: dict) -> list:
    """Lines for a sheet question, from `facts` the caller read off the character sheet:
    {"name", "hp": (cur, max) | None, "ac": int | None, "passive": {skill: int},
    "inventory": str}. Nothing is computed here but the wording; an absent fact is said
    to be absent, never guessed."""
    who = facts.get("name") or "Your character"
    if q.topic == "hp":
        hp = facts.get("hp")
        return [f"{who}: {hp[0]}/{hp[1]} HP."] if hp else [f"The sheet has no HP for {who}."]
    if q.topic == "ac":
        ac = facts.get("ac")
        return [f"{who}: AC {ac}."] if ac is not None else [f"The sheet has no AC for {who}."]
    if q.topic == "passive":
        scores = facts.get("passive") or {}
        if not scores:
            return [f"The sheet lists no skill bonuses for {who}, so no passive scores."]
        return [f"{who}: " + ", ".join(f"passive {k.title()} {v}" for k, v in scores.items()) + "."]
    if q.topic == "inventory":
        inv = (facts.get("inventory") or "").strip()
        return [f"{who} carries: {inv}"] if inv else [f"The sheet has no inventory line for {who}."]
    return []


# ── move toward a creature ───────────────────────────────────────────────────

FILLER = {"toward", "towards", "to", "at", "the", "on", "next", "near", "closer", "close", "up", "onto", "a", "it", "him", "her"}


def approach_target(enc, pc_id: str, words: list, symbols: dict | None = None):
    """The token id a "move toward the frog" line names, or None. Nearest match wins."""
    from .core import resolve
    pc = resolve(enc, pc_id)
    subject = " ".join(w for w in words if w.lower() not in FILLER)
    if not subject:
        return None
    found = foes_named(enc, pc, subject, symbols)
    return found[0].id if found else None
