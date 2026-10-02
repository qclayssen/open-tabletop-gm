"""advisor.py: the advisor council on the smarter model, asked in parallel.

Briefs live in prompts/advisors/ (copied from the dnd-gm advisor council).
Advisors only advise the GM: short, concrete, never for the players' eyes.
"""
from __future__ import annotations

import os
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor

from .llm import LLMError
from .reply import strip_think

BRIEFS = pathlib.Path(__file__).resolve().parent / "prompts" / "advisors"
ADVISORS = ("historian", "continuity", "director", "tactician", "designer", "arbiter",
            "interface", "referee", "mascot-handler")
MAX_WORDS = 150
FALLBACK = ["continuity", "director"]
KEYWORDS = {
    "historian": ("lore", "histor", "ancient", "legend", "cult", "god", "realm", "kingdom",
                  "myth", "origin"),
    "continuity": ("earlier", "before", "remember", "thread", "promise", "npc", "contradict",
                   "consisten", "last session", "who is", "dead", "died", "deceased", "kill"),
    "director": ("scene", "reveal", "death", "dies", "dramatic", "pacing", "villain",
                 "opening", "ending", "moment"),
    "tactician": ("fight", "combat", "encounter", "enemy", "enemies", "boss", "tactic",
                  "ambush", "monster", "terrain"),
    "designer": ("rule", "homebrew", "balance", "reward", "loot", "xp", "mechanic", "magic item"),
    "arbiter": ("roll", "rolled", "die", "dice", "dc", "nat 1", "nat 20", "natural 1",
                "natural 20", "critical", "crit", "fudge", "reroll", "re-roll", "save",
                "arbitrate", "ruling", "d20", "advantage", "disadvantage", "hit or miss"),
    "interface": ("display", "readab", "layout", "turn order", "sidebar", "visual",
                  "readability", "on screen", "dice pad", "what the player sees"),
    # The two Mage Tower advisors are set-piece lenses, not general-purpose ones.
    # They only win a ranking when the question is actually about the match, so
    # they cannot displace the general council on ordinary play questions.
    "referee": ("mage tower", "the match", "the score", "the clock", "half-time",
                "halftime", "full time", "foul", "ejected", "expelled", "expulsion",
                "out of bounds", "off the pitch", "the pitch", "whistle",
                "which ruleset", "in play", "custody"),
    "mascot-handler": ("mascot", "fractal mascot", "inkling", "pest mascot",
                       "spirit statue mascot", "art elemental mascot", "tower a",
                       "tower b", "capture the mascot", "who has the mascot",
                       # College names: a question about "what does Blot do" or
                       # "is Silverquill's tower Ethereal" never says "mascot".
                       "quandrix", "prismari", "lorehold", "silverquill",
                       "witherbloom", "relative density"),
}


def brief(name: str) -> str:
    if name not in ADVISORS:
        raise ValueError(f"No advisor {name!r}. Pick one of: {', '.join(ADVISORS)}, or council.")
    own = (BRIEFS / f"{name}.md").read_text(encoding="utf-8").strip()
    shared = (BRIEFS / "_shared.md").read_text(encoding="utf-8").strip()
    text = (f"{own}\n\n{shared}\n\nAnswer the GM in at most {MAX_WORDS} words: concrete "
            "suggestions, no preamble, nothing addressed to the players.")
    # A local thinking model as advisor spent its whole budget thinking (empty answers).
    return text + "\n/no_think" if os.environ.get("GM_NO_THINK", "1") != "0" else text


def pick(question: str, limit: int = 3) -> list:
    q = question.lower()
    scores = {n: sum(q.count(k) for k in KEYWORDS[n]) for n in ADVISORS}
    ranked = [n for n in sorted(ADVISORS, key=lambda n: -scores[n]) if scores[n] > 0]
    return ranked[:limit] or list(FALLBACK)


def fight_brief(snap) -> str:
    """Compact fight state for advisor context, or "" when no fight is active.

    Advisors were inventing mechanics (a 15 ft frog-tongue reach vs the actual
    threat 5) because consult context carried no fight state. Kept small on
    purpose: HP, turn and round only, from the bridge snapshot (no engine
    load, no CLI call).
    """
    if not snap or snap.get("status") != "active":
        return ""
    cur = snap.get("current") or {}
    lines = [f"Round {snap.get('round')}, {cur.get('name', '?')}'s turn."]
    for t in snap.get("tokens") or []:
        state = "DEAD" if t.get("dead") else f"{t.get('hp')}/{t.get('max_hp')} HP"
        lines.append(f"- {t.get('name')} ({t.get('side')}): {state}")
    return "## Active fight\n" + "\n".join(lines)


def parse_advise(text: str):
    """'/advise <name|council> <question>' arguments -> (names, question, is_council)."""
    who, _, question = (text or "").strip().partition(" ")
    question = question.strip()
    if who and who != "council":
        brief(who)                                 # raises for an unknown name
    if not who or not question:
        raise ValueError(f"Usage: /advise <{'|'.join(ADVISORS)}|council> <question>")
    if who == "council":
        return pick(question), question, True
    return [who], question, False


# "Director: (unavailable: HTTP 504 ...)" — the inline marker consult() writes for
# an advisor that did not answer. Parsed back out by split_notes.
_UNAVAILABLE = re.compile(r"^([A-Za-z][A-Za-z ]*):\s*\(unavailable", re.M)


def split_notes(text: str) -> tuple[str, list]:
    """`(notes, failed_names)` from a consult block.

    consult() reports a per-advisor failure inline so a partial council is still
    visible in the transcript, but that inline text must not travel on as advice:
    the DM was being briefed on "Continuity: (unavailable: HTTP 504 ...)" as if it
    were continuity guidance (audit report B2, 2026-09-29). One place decides what
    counts as a note, so every caller gets it right by construction.
    """
    notes, failed = [], []
    for block in (text or "").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        m = _UNAVAILABLE.match(block)
        if m:
            failed.append(m.group(1).strip())
        else:
            notes.append(block)
    return "\n\n".join(notes), failed


def consult(client, model: str, names, question: str, context: str, *,
            max_tokens: int = 400, reasoning: str | None = None) -> str:
    """Ask each advisor in parallel and return their notes as one block.

    `reasoning` is passed through to the client for the same reason the DM tier
    passes it: a local thinking model given no reasoning_effort will spend the
    entire max_tokens budget thinking and return an empty answer. Measured on
    qwen3.5:4b with this brief and max_tokens=400 -- no reasoning_effort: 400
    completion tokens, content "", 13.3s; reasoning_effort="none": 47 completion
    tokens, a real answer, 1.9s. brief() already appends /no_think, and this
    model ignores it. So the note came back as the bare string "Arbiter: ",
    which is indistinguishable in the transcript from an advisor that considered
    the question and had nothing to add.
    """
    names = list(dict.fromkeys(names))

    def ask(name):
        messages = [{"role": "system", "content": brief(name)},
                    {"role": "user", "content": f"## Campaign context\n{context}\n\n"
                                                f"## GM question\n{question}"}]
        r = client.chat(model, messages, max_tokens=max_tokens, temperature=0.4,
                        role=f"advisor:{name}", reasoning=reasoning)
        answer = strip_think(r.text)
        if not answer:
            # Never render silence as a considered note. An advisor that said
            # nothing must be visibly different from one that was consulted.
            raise LLMError(f"{name} returned an empty note "
                           f"({r.completion_tokens} completion tokens)")
        return answer

    parts = []
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        futures = {n: pool.submit(ask, n) for n in names}
        for n in names:
            try:
                parts.append(f"{n.title()}: {futures[n].result()}")
            except LLMError as e:
                parts.append(f"{n.title()}: (unavailable: {e})")
    return "\n\n".join(parts)
