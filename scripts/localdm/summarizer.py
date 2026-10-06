"""summarizer.py: fold old turns into the rolling summary, in a background thread.

The DM prompt carries the summary plus only the most recent turns, so the
prompt stays small however long the session runs. Folding uses the local
model: it is cheap, and a summary does not need the advisor tier.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from .context import LABEL
from .llm import LLMError
from .reply import strip_think

# Structured handoff prompt — emits YAML with fixed field order
HANDOFF_PROMPT = ("You keep the running summary of a tabletop roleplaying session. "
                  "Emit a YAML block with the following fields in this exact order. "
                  "Keep names, places, promises, open threads, items, injuries and what "
                  "each NPC thinks of the party. Drop blow-by-blow detail. "
                  "Total output at most {words} words.\n\n"
                  "written_at: \"<session label>\"\n"
                  "written_because: <trigger>      # session_end | context_full | beat_landed\n"
                  "pacing_used: <tempo>\n"
                  "scenes_completed: []\n"
                  "scenes_remaining: []\n"
                  "where_we_are: \"<one line>\"\n"
                  "in_flight:\n"
                  "  - \"<promise or debt>\"\n"
                  "party_state: \"<one line>\"\n"
                  "open_threads: []\n"
                  "world_moved: []\n"
                  "next_session_opens_on: \"<one line>\"\n/no_think")


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

    def _build_handoff_prompt(self, summary_so_far: str, new_turns: str, trigger: str) -> list[dict]:
        """Build the messages for the handoff LLM call."""
        session_label = self._session_label()
        system = HANDOFF_PROMPT.format(words=self.words)
        user = (f"## Trigger: {trigger}\n"
                f"## Session so far\n{summary_so_far or '(empty)'}\n\n"
                f"## New turns\n{new_turns}")
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    def _session_label(self) -> str:
        """Generate a session label like 'session 7, 23 Harvestmoon'."""
        try:
            state_text = (self.memory.dir.parent / "state.md").read_text(encoding="utf-8")
        except OSError:
            return f"session {self.memory.summarized() // 14 + 1}, {datetime.now().strftime('%d %B')}"
        # Extract session count from header
        import re
        m = re.search(r"\*\*Session count:\*\*\s*(\d+)", state_text)
        session_num = int(m.group(1)) if m else (self.memory.summarized() // 14 + 1)
        # Try to get in-world date from World State
        date_m = re.search(r"\*\*In-world date:\*\*\s*([^\n]+)", state_text)
        date_str = date_m.group(1).strip() if date_m else datetime.now().strftime('%d %B')
        return f"session {session_num}, {date_str}"

    def fold(self, trigger: str = "context_full") -> bool:
        """Fold unsummarized turns into a structured handoff.
        
        Args:
            trigger: One of "session_end", "context_full", "beat_landed"
        """
        turns = self.memory.turns()
        start, upto = self.memory.summarized(), len(turns) - self.keep
        if upto <= start:
            return False
        new = "\n".join(f"{LABEL.get(t['role'], t['role'])}: {t['text']}"
                        for t in turns[start:upto])
        messages = self._build_handoff_prompt(self.memory.summary() or "(empty)", new, trigger)
        text = strip_think(self.client.chat(self.model, messages, max_tokens=self.words * 2,
                                            temperature=0.3, role="summary",
                                            reasoning=self.reasoning).text)
        if not text:
            return False
        self.memory.set_summary(text, upto)
        return True

    def write_handoff(self, trigger: str) -> bool:
        """Write a handoff at a specific trigger point (session_end, beat_landed).
        
        This is called synchronously from the main thread, not the background folder.
        """
        turns = self.memory.turns()
        start = self.memory.summarized()
        if start >= len(turns):
            # No new turns, but still write a handoff with current summary
            return self._write_handoff_without_new_turns(trigger)
        new = "\n".join(f"{LABEL.get(t['role'], t['role'])}: {t['text']}"
                        for t in turns[start:])
        messages = self._build_handoff_prompt(self.memory.summary() or "(empty)", new, trigger)
        text = strip_think(self.client.chat(self.model, messages, max_tokens=self.words * 2,
                                            temperature=0.3, role="summary",
                                            reasoning=self.reasoning).text)
        if not text:
            return False
        # For session_end/beat_landed, we don't advance the summarized cursor
        # The handoff is written to summary.md but meta.json summarized stays
        self.memory.dir.mkdir(parents=True, exist_ok=True)
        from .memory import _write
        _write(self.memory.dir / "summary.md", text.strip() + "\n")
        return True

    def _write_handoff_without_new_turns(self, trigger: str) -> bool:
        """Write a handoff when there are no new turns to fold."""
        messages = self._build_handoff_prompt(self.memory.summary() or "(empty)", "(no new turns)", trigger)
        text = strip_think(self.client.chat(self.model, messages, max_tokens=self.words * 2,
                                            temperature=0.3, role="summary",
                                            reasoning=self.reasoning).text)
        if not text:
            return False
        self.memory.dir.mkdir(parents=True, exist_ok=True)
        from .memory import _write
        _write(self.memory.dir / "summary.md", text.strip() + "\n")
        return True

    def _safe_fold(self) -> None:
        try:
            self.fold("context_full")
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