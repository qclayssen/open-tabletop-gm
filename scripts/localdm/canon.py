"""canon.py: the raw, never-summarized layer of memory.

`transcript.jsonl` holds every turn but the DM only ever sees the last few,
and everything older than that is folded into `summary.md` — a lossy
paraphrase. So an NPC's exact words, what was actually agreed, and how a
truth was worded all evaporate the moment a turn leaves the recent window,
and the next session re-invents them.

`canon.jsonl` is the third layer: append-only, verbatim, never folded.

    {"kind": "dialogue",    "speaker": "Maribeth", "text": "Keep it under...", "turn": 41}
    {"kind": "interaction", "speaker": "Maribeth", "text": "Kairos handed...", "turn": 42}
    {"kind": "reveal",      "key": "moonstone-true-nature", "text": "The stone...", "turn": 63}

Extraction runs on the same window the Summarizer is about to compress, in
its own background thread, on the local model. Verbatim is enforced rather
than requested: a record is kept only if its text occurs character-for-
character in the narration it came from, so a paraphrase is dropped instead
of poisoning canon with reworded dialogue.
"""
from __future__ import annotations

import json
import re
import threading

from .reply import scrub_injection, strip_think

KINDS = ("dialogue", "interaction", "reveal", "death")

# A quotation mark pair the model may or may not have carried over from the
# narration. Stripped at render time so canon is not double-quoted.
_QUOTES = ("“”", "\"\"", "«»", "‘’", "''")

_OBJECT = re.compile(r"\{[^{}]*\}", re.S)
_ARRAY = re.compile(r"\[.*\]", re.S)


def _collapse(text: str) -> str:
    """Narration and extracted spans must be comparable across line wrapping."""
    return re.sub(r"\s+", " ", text or "").strip()


def unquote(text: str) -> str:
    text = _collapse(text)
    for pair in _QUOTES:
        if len(text) >= 2 and text[0] == pair[0] and text[-1] == pair[1]:
            return text[1:-1].strip()
    return text


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _collapse(text).lower()).strip("-")


class Canon:
    """canon.jsonl next to the transcript. Append-only; existing lines are
    never rewritten, so a corrupted append costs one line, not the file."""

    def __init__(self, dir_or_memory):
        base = getattr(dir_or_memory, "dir", dir_or_memory)
        self.dir = base
        self._lock = threading.RLock()

    @property
    def _path(self):
        return self.dir / "canon.jsonl"

    def records(self) -> list:
        """Canon records, scrubbed on read. #261.

        `canon.jsonl` is appended from what the DM said, so it is model-authored
        text on its way back to the model. `memory.turns()` has always scrubbed;
        this path had not. Scrubbed here rather than in `relevant()` so every
        caller gets it -- `relevant()` ranks over these dicts and both the rank
        and the render read `text`.

        The file is not rewritten; only what the model reads is cleaned.
        """
        with self._lock:
            if not self._path.exists():
                return []
            lines = self._path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue                     # a torn append: skip it, keep the rest
            if isinstance(data, dict) and data.get("text"):
                data = dict(data)
                data["text"] = scrub_injection(str(data["text"]))
                out.append(data)
        return out

    def _seen(self) -> tuple:
        """(reveal/death keys, exact (kind, speaker, text) tuples) already recorded."""
        keys, exacts = set(), set()
        for r in self.records():
            if r.get("kind") in ("reveal", "death") and r.get("key"):
                keys.add((r["kind"], r["key"]))
            exacts.add((r.get("kind", ""), r.get("speaker", ""), r["text"]))
        return keys, exacts

    def add(self, records) -> list:
        """Append the records that are not already here. Returns what was kept.

        Reveals and deaths are deduped on their key, so the same truth or death
        cannot be recorded twice with different wording. Dialogue and
        interaction dedupe on exact text, so a repeated line is kept out of the
        replay budget.
        """
        with self._lock:
            keys, exacts = self._seen()
            fresh = []
            for r in records or []:
                kind = r.get("kind")
                if kind not in KINDS:
                    continue
                text = _collapse(r.get("text", ""))
                if not text:
                    continue
                speaker = _collapse(r.get("speaker", ""))
                if kind == "death" and not speaker:
                    continue
                key = _slug(speaker if kind == "death" else (r.get("key") or text[:60]))
                if kind in ("reveal", "death") and (kind, key) in keys:
                    continue
                if (kind, speaker, text) in exacts:
                    continue
                if kind in ("reveal", "death"):
                    keys.add((kind, key))
                exacts.add((kind, speaker, text))
                fresh.append({"kind": kind, "speaker": speaker, "text": text,
                              "turn": int(r.get("turn", 0) or 0),
                              **({"key": key} if kind in ("reveal", "death") else {}),
                              **({"dead": True} if kind == "death" else {})})
            if not fresh:
                return []
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                for r in fresh:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            return fresh

    def relevant(self, query: str, limit: int = 8) -> list:
        """The records most worth replaying for the scene in `query`.

        Ranked by how many of the query's words the record mentions, then by
        recency. Matching terms break the tie toward the thing just said, so a
        busy campaign keeps its prompt small without losing the live thread.
        """
        records = self.records()
        if not records:
            return []
        terms = {w.lower() for w in re.findall(r"[A-Za-z][\w'-]{2,}", query or "")}

        def score(r):
            hay = f"{r.get('speaker', '')} {r['text']}".lower()
            overlap = sum(1 for t in terms if t in hay)
            return (overlap, int(r.get("turn", 0) or 0))

        return sorted(records, key=score, reverse=True)[:max(0, limit)]


HEADER = ("## Canon — already said out loud, verbatim\n"
          "These were actually spoken or shown to the player. Keep a character's "
          "voice in these exact words, and never re-reveal or contradict one of "
          "them.")


def render_one(record) -> str:
    """One canon record as a line, labelled with what kind of thing it is."""
    kind = record.get("kind", "dialogue")
    who = _collapse(record.get("speaker", ""))
    if kind == "dialogue" and who:
        return f"- {who}: {unquote(record['text'])}"
    if kind == "reveal":
        return f"- already revealed: {record['text']}"
    if kind == "death":
        return f"- DEAD, stays dead: {who}: {record['text']}"
    if kind == "interaction" and who:
        return f"- {who}, said or done: {record['text']}"
    return f"- {kind}: {record['text']}"


def render(records) -> str:
    """The canon block, or "" when there is nothing to say."""
    lines = [render_one(r) for r in records or []]
    return HEADER + "\n" + "\n".join(lines) if lines else ""


PROMPT = """You keep the canon of a tabletop roleplaying session: the lines that must not be forgotten or reworded. Read the new turns and return a JSON array. Each element:

{"kind": "dialogue"|"interaction"|"reveal"|"death", "speaker": "NPC name or empty; required for death", "key": "reveals/deaths only, short_snake_case", "text": "..."}

- dialogue: something an NPC said, word for word.
- interaction: what the player and an NPC actually did or agreed, in one plain sentence.
- reveal: a fact about the world, a person or an item that the narration disclosed to the player.
- death: a named character who died in the narration; speaker is the character's name. This is permanent canon.

Rules:
- Copy "text" character for character from the narration. Never paraphrase, summarize or rewrite it, even to shorten it. A line that is not a verbatim span will be discarded.
- Quote nothing: give the words without quotation marks.
- Only what the player was actually shown. No invented lore and nothing you expect later. Signalled danger the player was actually shown (a rumour, a warning, a cost someone else already paid) is canon; the outcome it points to is not.
- Most turns have one or two entries. Return [] if there is nothing worth keeping.
- Array only, no prose, no code fence.

## Canon so far
{canon}

## New turns
{turns}
/no_think"""
# Substituted with str.replace, not .format: the prompt is full of literal JSON
# braces and escaping them would be a bug waiting to happen.


def _text_field(data, key):
    v = data.get(key)
    return _collapse(v) if isinstance(v, str) else ""


def parse(text: str) -> list:
    """The model's candidate records, as dicts. Tolerant: small models wrap the
    array in a fence, prepend a sentence, or return one object per line."""
    text = strip_think(text)
    data = None
    for candidate in (_ARRAY.search(text), text if text.strip().startswith("[") else None):
        if candidate is None:
            continue
        try:
            data = json.loads(candidate if isinstance(candidate, str) else candidate.group(0))
            break
        except json.JSONDecodeError:
            continue
    if data is None:                        # line-delimited or a bare object
        found = []
        for m in _OBJECT.finditer(text):
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
            found.extend(obj if isinstance(obj, list) else [obj])
        data = found
    if not isinstance(data, list):
        data = [data] if isinstance(data, dict) else []
    return [d for d in data if isinstance(d, dict) and d.get("text")]


def verify(candidates: list, narration: str, turn: int = 0) -> list:
    """Keep only records whose text is literally in the narration.

    This is the whole point: the model is asked for verbatim, and is not
    believed. Whitespace is collapsed on both sides first so a span that
    merely crossed a line break still counts as verbatim.
    """
    hay = _collapse(narration)
    out = []
    for d in candidates:
        kind = d.get("kind")
        if kind not in KINDS:
            continue
        span = _collapse(d.get("text", "")).strip('"“”«')
        if not span or span not in hay:
            continue                          # paraphrased or invented: dropped
        speaker = _text_field(d, "speaker")
        if kind == "death" and not speaker:
            continue
        record = {"kind": kind, "speaker": speaker, "text": span, "turn": turn}
        if kind == "reveal":
            record["key"] = _slug(_text_field(d, "key") or span[:60])
        elif kind == "death":
            record["key"] = _slug(speaker)
            record["dead"] = True
        out.append(record)
    return out


class Extractor:
    """Pulls canon out of the window the Summarizer is about to fold.

    Reads transcript turns between its own cursor and `len(turns) - keep`, so
    it never waits for the fold and never loses a turn to it — the transcript
    is append-only. Same cheap local tier and same background-thread shape as
    Summarizer, so a long extraction never blocks the player's turn.
    """

    def __init__(self, client, model: str, memory, canon: Canon, *, keep: int = 6,
                 batch: int = 8, reasoning: str | None = None):
        self.client, self.model, self.memory, self.canon = client, model, memory, canon
        self.reasoning = reasoning
        self.keep, self.batch = keep, batch
        self.last_error = ""
        self._thread = None

    def _cursor(self) -> int:
        canon_meta = self.memory.meta().get("canon")
        return int(canon_meta.get("upto", 0)) if isinstance(canon_meta, dict) else 0

    def _set_cursor(self, upto: int) -> None:
        self.memory.update_meta(canon={"upto": upto})

    def due(self) -> bool:
        return len(self.memory.unsummarized()) >= self.keep + self.batch

    def extract(self) -> bool:
        turns = self.memory.turns()
        start, upto = self._cursor(), len(turns) - self.keep
        if upto <= start:
            return False
        # Each turn is verified against itself, so one turn's text can never
        # become another's verbatim span.
        fresh = []
        for i, turn in enumerate(turns[start:upto], start):
            if turn["role"] != "dm" or not turn["text"].strip():
                continue
            known = self.canon.relevant(turn["text"], limit=6)
            prompt = PROMPT.replace("{canon}", render(known) or "(empty)") \
                            .replace("{turns}", turn["text"])
            messages = [{"role": "system", "content": prompt},
                        {"role": "user", "content": "Return the JSON array."}]
            reply = self.client.chat(self.model, messages, max_tokens=500,
                                     temperature=0.2, role="canon",
                                     reasoning=self.reasoning)
            fresh.extend(verify(parse(reply.text), turn["text"], turn=i))
        # Advance the cursor even when nothing was kept, or a window that
        # yields no canon would be rescanned for the rest of the campaign.
        self.canon.add(fresh)
        self._set_cursor(upto)
        return bool(fresh)

    def _safe_extract(self) -> None:
        try:
            self.extract()
        except Exception as e:                # a failed extraction must not kill the thread
            self.last_error = str(e)

    def maybe_start(self):
        if (self._thread and self._thread.is_alive()) or not self.due():
            return None
        self._thread = threading.Thread(target=self._safe_extract, daemon=True)
        self._thread.start()
        return self._thread

    def join(self, timeout=None) -> None:
        if self._thread:
            self._thread.join(timeout)
