"""summarizer.py: fold old turns into the rolling summary, in a background thread.

The DM prompt carries the summary plus only the most recent turns, so the
prompt stays small however long the session runs. Folding uses the local
model: it is cheap, and a summary does not need the advisor tier.
"""
from __future__ import annotations

import threading

from .context import LABEL
from .llm import LLMError
from .reply import strip_think

PROMPT = ("You keep the running summary of a tabletop roleplaying session. Merge the new "
          "turns into the summary. Keep names, places, promises, open threads, items, "
          "injuries and what each NPC thinks of the party. Drop blow-by-blow detail. "
          "At most {words} words of plain prose.\n/no_think")


class Summarizer:
    def __init__(self, client, model: str, memory, *, keep: int = 6, batch: int = 8,
                 words: int = 250, reasoning: str | None = None):
        self.client, self.model, self.memory = client, model, memory
        self.reasoning = reasoning
        self.keep, self.batch, self.words = keep, batch, words
        self.last_error = ""
        self._thread = None

    def due(self) -> bool:
        return len(self.memory.unsummarized()) >= self.keep + self.batch

    def fold(self) -> bool:
        turns = self.memory.turns()
        start, upto = self.memory.summarized(), len(turns) - self.keep
        if upto <= start:
            return False
        new = "\n".join(f"{LABEL.get(t['role'], t['role'])}: {t['text']}"
                        for t in turns[start:upto])
        messages = [{"role": "system", "content": PROMPT.format(words=self.words)},
                    {"role": "user", "content": f"## Summary so far\n"
                                                f"{self.memory.summary() or '(empty)'}\n\n"
                                                f"## New turns\n{new}"}]
        text = strip_think(self.client.chat(self.model, messages, max_tokens=self.words * 2,
                                            temperature=0.3, role="summary",
                                            reasoning=self.reasoning).text)
        if not text:
            return False
        self.memory.set_summary(text, upto)
        return True

    def _safe_fold(self) -> None:
        try:
            self.fold()
        except LLMError as e:
            self.last_error = str(e)

    def maybe_start(self):
        if (self._thread and self._thread.is_alive()) or not self.due():
            return None
        self._thread = threading.Thread(target=self._safe_fold, daemon=True)
        self._thread.start()
        return self._thread

    def join(self, timeout=None) -> None:
        if self._thread:
            self._thread.join(timeout)
