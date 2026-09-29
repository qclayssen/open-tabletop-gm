"""notes.py: the advisor council's notes, kept on disk in <campaign>/localdm/.

    notes.md   one block per consult, newest last:
               ## 2026-09-29 21:14 - /advise council - historian, director
               <the notes, verbatim>

Session.saved_notes used to be the only home for advisor output, which means
the notes lived in RAM until the next DM call consumed them and then vanished
with the process. A /advise council about a plot thread, run between sessions,
was simply gone by morning - and the GM, who is the only person who can act on
"Continuity Keeper: you promised Mira her brother in session 2", had nothing to
come back to. This is the audit finding from the 2026-09-29 report.

The notes are written verbatim and never folded or rewritten, for the same
reason canon.jsonl exists: an advisor's exact wording is the thing the GM wants
to read, and a lossy summary of it is a different claim.

This file is append-only and never read back into a DM prompt. Notes reach the
DM through Session.saved_notes, as before; writing them is about the GM being
able to read them afterwards, not about changing what the model is told.
"""
from __future__ import annotations

import datetime
import os
import pathlib
import re
import threading

NAME = "notes.md"
MAX_BYTES = 256 * 1024        # a long campaign's notes stay readable in an editor


# A body line that looks like an entry heading would forge an entry, so such
# lines are stored with one leading backslash and unescaped on read. Files
# written before this existed have no escaped lines and read exactly as before.
_ESC = re.compile(r"^(\\*)## ", re.MULTILINE)
_UNESC = re.compile(r"^\\(\\*## )", re.MULTILINE)


def _escape(body: str) -> str:
    return _ESC.sub(lambda m: "\\" + m.group(0), body)


def _unescape(body: str) -> str:
    return _UNESC.sub(r"\1", body)


class Notes:
    """Append-only log of advisor notes for one campaign.

    Same bargain as Memory: the shadow advisor writes from its own thread while
    the REPL reads, so every read and write takes the lock.
    """

    def __init__(self, camp_dir):
        self.dir = pathlib.Path(camp_dir) / "localdm"
        self._lock = threading.RLock()

    @property
    def path(self) -> pathlib.Path:
        return self.dir / NAME

    def add(self, body: str, *, source: str = "", advisors=()) -> bool:
        """File one consult's notes. Returns False if there was nothing to file."""
        body = (body or "").strip()
        if not body:
            return False
        # Local time with an explicit offset: a GM reading this file weeks later
        # must be able to place a consult in their own evening, and a naive stamp
        # is ambiguous for the hour either side of a DST change.
        stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
        who = ", ".join(advisors)
        head = f"## {stamp} - {source or 'advisor'}" + (f" - {who}" if who else "")
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"\n{head}\n\n{_escape(body)}\n")
            self._trim()
        return True

    def _trim(self) -> None:
        """Drop the oldest blocks once the log outgrows MAX_BYTES.

        A whole leading block is dropped, never a partial one: half a note read
        as advice is worse than no note.
        """
        try:
            text = self.path.read_text(encoding="utf-8")
            if len(text.encode("utf-8")) <= MAX_BYTES:
                return
        except OSError:
            return
        blocks = text.split("\n## ")
        if len(blocks) < 2:
            return
        kept = blocks[-1:]
        for block in reversed(blocks[:-1]):        # newest blocks that fit
            if sum(len(b.encode("utf-8")) + 4 for b in kept) + len(block.encode("utf-8")) + 4 > MAX_BYTES:
                break
            kept.insert(0, block)
        with self._lock:
            tmp = self.path.with_name(NAME + ".tmp")
            try:
                tmp.write_text("## " + "\n## ".join(kept), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                tmp.unlink(missing_ok=True)

    def entries(self, limit: int | None = None) -> list:
        """`(stamp, source, advisors, body)` per consult, oldest first.

        `limit` of 0 or None means no limit. Guarded rather than left to
        `out[-limit:]`, which for limit=0 is the whole list, not none.
        """
        with self._lock:
            try:
                text = self.path.read_text(encoding="utf-8")
            except OSError:
                return []
        out = []
        for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
            head, _, body = block.partition("\n")
            parts = [p.strip() for p in head.split(" - ")]
            stamp = parts[0] if parts else ""
            source = parts[1] if len(parts) > 1 else ""
            advisors = [a.strip() for a in parts[2].split(",") if a.strip()] \
                if len(parts) > 2 else []
            out.append((stamp, source, advisors, _unescape(body).strip()))
        return out[-limit:] if limit and limit > 0 else out

    def recent(self, limit: int = 5) -> str:
        """The last `limit` consults as markdown, newest last. "" when empty."""
        blocks = []
        for stamp, source, advisors, body in self.entries(limit):
            head = f"## {stamp} - {source or 'advisor'}"
            if advisors:
                head += f" - {', '.join(advisors)}"
            blocks.append(f"{head}\n\n{body}")
        return "\n\n".join(blocks)
