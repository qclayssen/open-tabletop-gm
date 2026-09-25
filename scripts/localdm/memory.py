"""memory.py: what the local DM remembers between turns, in <campaign>/localdm/.

    transcript.jsonl   one {"role", "text"} per line; roles: player, dm, engine
    summary.md         the rolling summary of every turn before meta["summarized"]
    meta.json          {"summarized": <turn index>, "seen": [<trigger keys>]}

The summarizer writes from a background thread while the REPL appends turns,
so every read and write takes the same lock.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading


def _write(path: pathlib.Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


class Memory:
    def __init__(self, camp_dir):
        self.dir = pathlib.Path(camp_dir) / "localdm"
        self._lock = threading.RLock()

    @property
    def _transcript(self) -> pathlib.Path:
        return self.dir / "transcript.jsonl"

    def add(self, role: str, text: str) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self._transcript, "a", encoding="utf-8") as f:
                f.write(json.dumps({"role": role, "text": text}, ensure_ascii=False) + "\n")

    def seed_from_tail(self, limit: int = 6) -> int:
        """A campaign with no transcript yet starts from what the display already showed
        (session_tail.json: the opening scene, or the last exchanges of a resumed game)."""
        path = self.dir.parent / "session_tail.json"
        if self.turns() or not path.exists():
            return 0
        try:
            chunks = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        texts = [c["text"].strip() for c in chunks
                 if isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip()]
        for text in texts[-limit:]:
            self.add("dm", text)
        return len(texts[-limit:])

    def turns(self) -> list:
        with self._lock:
            if not self._transcript.exists():
                return []
            lines = self._transcript.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def summary(self) -> str:
        path = self.dir / "summary.md"
        with self._lock:
            return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def meta(self) -> dict:
        path = self.dir / "meta.json"
        with self._lock:
            if not path.exists():
                return {}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return data if isinstance(data, dict) else {}

    def update_meta(self, **changes) -> None:
        with self._lock:
            data = self.meta()
            data.update(changes)
            self.dir.mkdir(parents=True, exist_ok=True)
            _write(self.dir / "meta.json", json.dumps(data, indent=1, ensure_ascii=False))

    def summarized(self) -> int:
        return int(self.meta().get("summarized", 0))

    def unsummarized(self) -> list:
        with self._lock:
            return self.turns()[self.summarized():]

    def set_summary(self, text: str, upto: int) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            _write(self.dir / "summary.md", text.strip() + "\n")
            self.update_meta(summarized=upto)

    def seen(self) -> set:
        return set(self.meta().get("seen", []))

    def mark_seen(self, keys) -> None:
        with self._lock:
            self.update_meta(seen=sorted(self.seen() | set(keys)))
