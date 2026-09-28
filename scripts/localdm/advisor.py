"""advisor.py: the advisor council on the smarter model, asked in parallel.

Briefs live in prompts/advisors/ (copied from the dnd-gm advisor council).
Advisors only advise the GM: short, concrete, never for the players' eyes.
"""
from __future__ import annotations

import os
import pathlib
from concurrent.futures import ThreadPoolExecutor

from .llm import LLMError
from .reply import strip_think

BRIEFS = pathlib.Path(__file__).resolve().parent / "prompts" / "advisors"
ADVISORS = ("historian", "continuity", "director", "tactician", "designer", "arbiter",
            "interface")
MAX_WORDS = 150
FALLBACK = ["continuity", "director"]
KEYWORDS = {
    "historian": ("lore", "histor", "ancient", "legend", "cult", "god", "realm", "kingdom",
                  "myth", "origin"),
    "continuity": ("earlier", "before", "remember", "thread", "promise", "npc", "contradict",
                   "consisten", "last session", "who is"),
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
