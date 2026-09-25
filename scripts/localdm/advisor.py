"""advisor.py: the advisor council on the smarter model, asked in parallel.

Briefs live in prompts/advisors/ (copied from the dnd-gm advisor council).
Advisors only advise the GM: short, concrete, never for the players' eyes.
"""
from __future__ import annotations

import pathlib
from concurrent.futures import ThreadPoolExecutor

from .llm import LLMError
from .reply import strip_think

BRIEFS = pathlib.Path(__file__).resolve().parent / "prompts" / "advisors"
ADVISORS = ("historian", "continuity", "director", "tactician", "designer")
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
}


def brief(name: str) -> str:
    if name not in ADVISORS:
        raise ValueError(f"No advisor {name!r}. Pick one of: {', '.join(ADVISORS)}, or council.")
    own = (BRIEFS / f"{name}.md").read_text(encoding="utf-8").strip()
    shared = (BRIEFS / "_shared.md").read_text(encoding="utf-8").strip()
    return (f"{own}\n\n{shared}\n\nAnswer the GM in at most {MAX_WORDS} words: concrete "
            "suggestions, no preamble, nothing addressed to the players.")


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
        raise ValueError("Usage: /advise <historian|continuity|director|tactician|designer"
                         "|council> <question>")
    if who == "council":
        return pick(question), question, True
    return [who], question, False


def consult(client, model: str, names, question: str, context: str, *,
            max_tokens: int = 300) -> str:
    names = list(dict.fromkeys(names))

    def ask(name):
        messages = [{"role": "system", "content": brief(name)},
                    {"role": "user", "content": f"## Campaign context\n{context}\n\n"
                                                f"## GM question\n{question}"}]
        r = client.chat(model, messages, max_tokens=max_tokens, temperature=0.4,
                        role=f"advisor:{name}")
        return strip_think(r.text)

    parts = []
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        futures = {n: pool.submit(ask, n) for n in names}
        for n in names:
            try:
                parts.append(f"{n.title()}: {futures[n].result()}")
            except LLMError as e:
                parts.append(f"{n.title()}: (unavailable: {e})")
    return "\n\n".join(parts)
