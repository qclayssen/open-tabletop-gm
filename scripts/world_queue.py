#!/usr/bin/env python3
"""world_queue.py: the `## World Queue` in state.md, off-screen pressure waiting to surface.

Faction clocks (world.py) and the random-event oracle (oracle.py) both generate
things that happen off-screen. This is where they are stored until the GM
decides to surface one. The script keeps the ledger; the GM decides what fires
and how it looks. Everything printed here is GM-only.

    python3 world_queue.py -c NAME list                 # pending entries (default)
    python3 world_queue.py -c NAME roll [--seed N]      # seed at most one event
    python3 world_queue.py -c NAME add --trigger T --event E --ask A --if-ignored I ...
    python3 world_queue.py -c NAME fire ev-1            # status -> fired
    python3 world_queue.py -c NAME dismiss ev-1 --reason "wrong scene"
    python3 world_queue.py -c NAME requeue ev-1         # dismissed -> pending
    python3 world_queue.py -c NAME start                # the /gm start report
    python3 world_queue.py -c NAME validate             # schema + expires_by

Rules kept here on purpose (docs/specs/EVENTS-AND-CONTEXT.md):
  * `trigger` is human prose judged by the GM. It is never parsed.
  * There is no date field. Supply is per session, quota-bounded (`roll` adds
    one entry only while fewer than 3 are pending); firing is always the GM's call. Nothing auto-fires.
  * A rolled row has `demands: null`. A d100 chooses a *what*, never a *how much*,
    and the roll itself is never stored or printed.
  * `demands` is a pressure claim (none | ambient | urgent | null). It is a
    default, not a command: `fire` reports it, the GM may dismiss instead.
  * Fired and dismissed entries are never deleted.

Only the stdlib is used, so the fenced YAML is read and written by the small
parser below. It handles exactly the flat list-of-mappings shape this section
uses. Comments above the first entry are kept; comments elsewhere are not.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sys

import oracle
from paths import find_campaign

HEADING = "## World Queue"
_ANCHOR = "## Recent Events"     # templates/state.md keeps the queue just above it

DEMANDS = ("none", "ambient", "urgent")          # plus null: no claim
SURFACES = ("rumour", "visible_change", "npc_move", "deadline")
STATUSES = ("pending", "fired", "dismissed")
FIELDS = ("id", "trigger", "event", "ask", "if_ignored", "demands", "surfaces_as",
          "visible_to_players", "expires_by", "status", "dismissals", "dismissed_reason")
PENDING_CAP = 3            # `roll` adds an entry only while pending < this
DISMISS_SURFACE_AT = 3     # dismissals of one id that /gm start surfaces
ROLL_LIFETIME = 2          # a rolled row expires this many sessions ahead

DEFAULT_HEADER = [
    "# Off-screen pressure waiting to surface. Checked at /gm start. An entry with",
    "# demands set forces a pressure override on the scene it lands in (a default,",
    "# not a command: the GM can dismiss it with a reason). trigger is prose the GM",
    "# judges; nothing here fires on its own.",
]

# ── the fenced YAML subset ───────────────────────────────────────────────────

_ITEM = re.compile(r"^-\s+([A-Za-z_]\w*)\s*:\s*(.*)$")
_KEY = re.compile(r"^\s+([A-Za-z_]\w*)\s*:\s*(.*)$")
_FENCE = re.compile(r"^```ya?ml[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)


def _scalar(raw: str):
    raw = raw.strip()
    if raw[:1] == '"':
        try:
            return json.JSONDecoder().raw_decode(raw)[0]
        except ValueError:
            return raw.strip('"')
    if raw[:1] == "'":
        end = raw.find("'", 1)
        return raw[1:end if end > 0 else None].replace("''", "'")
    raw = re.sub(r"\s+#.*$", "", raw).strip()
    if raw in ("", "null", "~"):
        return None
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def _dump(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if re.fullmatch(r"[a-z][a-z_]*", text) and text not in ("null", "true", "false"):
        return text
    return json.dumps(text, ensure_ascii=False)


def parse_block(body: str) -> tuple:
    """(header_comment_lines, entries) from the text inside the yaml fence."""
    header, entries, cur = [], [], None
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            if cur is None:
                header.append(line.rstrip())
            continue
        m = _ITEM.match(s if s.startswith("-") else "")
        if m:
            cur = {m.group(1): _scalar(m.group(2))}
            entries.append(cur)
            continue
        m = _KEY.match(line)
        if m and cur is not None:
            cur[m.group(1)] = _scalar(m.group(2))
    return header, entries


def render_block(header: list, entries: list) -> str:
    out = list(header or DEFAULT_HEADER)
    for e in entries:
        keys = [k for k in FIELDS if k in e] + [k for k in e if k not in FIELDS]
        for i, k in enumerate(keys):
            out.append(("- " if i == 0 else "  ") + f"{k}: {_dump(e[k])}")
    return "\n".join(out) + "\n"


# ── locating and rewriting the section in state.md ───────────────────────────

def _section_span(text: str):
    """(start, end) character offsets of the World Queue section, or None."""
    m = re.search(r"^## World Queue[ \t]*$", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^## ", text[m.end():], re.M)
    return m.start(), (m.end() + nxt.start() if nxt else len(text))


def read_queue(state_text: str) -> tuple:
    """(header, entries). A campaign with no section or no fence has an empty queue."""
    span = _section_span(state_text or "")
    if not span:
        return [], []
    f = _FENCE.search(state_text[span[0]:span[1]])
    return parse_block(f.group(1)) if f else ([], [])


def pending(entries: list) -> list:
    return [e for e in entries if e.get("status", "pending") == "pending"]


def write_queue(state_text: str, header: list, entries: list) -> str:
    """state.md text with the queue block replaced; the section is created above
    `## Recent Events` when absent. Every other byte is copied through."""
    block = f"{HEADING}\n```yaml\n{render_block(header, entries)}```\n"
    span = _section_span(state_text)
    if span:
        old = state_text[span[0]:span[1]]
        f = _FENCE.search(old)
        if f:
            new = old[:f.start()] + f"```yaml\n{render_block(header, entries)}```" + old[f.end():]
        else:
            new = block + ("\n" if span[1] < len(state_text) else "")
        return state_text[:span[0]] + new + state_text[span[1]:]
    at = re.search(rf"^{re.escape(_ANCHOR)}[ \t]*$", state_text, re.M)
    if at:
        return state_text[:at.start()] + block + "\n" + state_text[at.start():]
    return state_text.rstrip("\n") + "\n\n" + block


def session_count(state_text: str) -> int:
    m = re.search(r"\*\*Session count:\*\*\s*(\d+)", state_text or "")
    return int(m.group(1)) if m else 0


def _state_path(campaign: str):
    return find_campaign(campaign) / "state.md"


def _load(campaign: str):
    path = _state_path(campaign)
    if not path.exists():
        raise SystemExit(f"error: no state.md for campaign '{campaign}'")
    text = path.read_text(encoding="utf-8")
    header, entries = read_queue(text)
    return path, text, header, entries


def _save(path, text: str, header: list, entries: list) -> None:
    """Atomic write with a .bak, the same convention as world.append_faction_moves."""
    new = write_queue(text, header, entries)
    if new == text:
        return
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
        f.flush()
        os.fsync(f.fileno())
    shutil.copy2(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)


# ── the interface R3 (rhythm) consumes ───────────────────────────────────────

def forced_pressure(entry: dict):
    """The pressure an entry demands, or None when it makes no claim.

    `none` is a claim (deliberately low stakes); null is no claim. The caller
    compares this with the scene's resolved pressure and re-emits the directive
    only when they differ (RHYTHM-SCHEMA.md 7.1.1).
    """
    d = entry.get("demands")
    return d if d in DEMANDS else None


def find_entry(entries: list, entry_id: str) -> dict:
    for e in entries:
        if e.get("id") == entry_id:
            return e
    raise SystemExit(f"error: no queue entry '{entry_id}'")


def fire_entry(campaign: str, entry_id: str) -> dict:
    """pending -> fired. Returns the entry (with `demands` for the caller)."""
    path, text, header, entries = _load(campaign)
    e = find_entry(entries, entry_id)
    if e.get("status", "pending") != "pending":
        raise SystemExit(f"error: {entry_id} is {e.get('status')}, not pending")
    e["status"] = "fired"
    _save(path, text, header, entries)
    return e


def dismiss_entry(campaign: str, entry_id: str, reason: str) -> dict:
    if not reason.strip():
        raise SystemExit("error: dismiss needs a one-line --reason")
    path, text, header, entries = _load(campaign)
    e = find_entry(entries, entry_id)
    if e.get("status", "pending") == "fired":
        raise SystemExit(f"error: {entry_id} already fired; fired entries are kept as is")
    e["status"] = "dismissed"
    e["dismissals"] = int(e.get("dismissals") or 0) + 1
    e["dismissed_reason"] = " ".join(reason.split())
    _save(path, text, header, entries)
    return e


def requeue_entry(campaign: str, entry_id: str) -> dict:
    path, text, header, entries = _load(campaign)
    e = find_entry(entries, entry_id)
    if e.get("status") != "dismissed":
        raise SystemExit(f"error: {entry_id} is {e.get('status')}, only dismissed entries requeue")
    e["status"] = "pending"
    _save(path, text, header, entries)
    return e


def _next_id(entries: list) -> str:
    nums = [int(m.group(1)) for e in entries
            if (m := re.fullmatch(r"ev-(\d+)", str(e.get("id", ""))))]
    return f"ev-{max(nums, default=0) + 1}"


def add_entry(campaign: str, fields: dict) -> dict:
    path, text, header, entries = _load(campaign)
    e = {"id": _next_id(entries), "trigger": None, "event": None, "ask": None,
         "if_ignored": None, "demands": None, "surfaces_as": "rumour",
         "visible_to_players": False, "expires_by": None, "status": "pending"}
    e.update({k: v for k, v in fields.items() if k in FIELDS and k != "id"})
    entries.append(e)
    _save(path, text, header, entries)
    return e


def roll_event(campaign: str, rng=None):
    """Seed at most one entry, only while pending < PENDING_CAP. Returns the new
    entry or None. The d100 is rolled through dice.py and deliberately not stored:
    a known schedule is metagamable, a known roll is not."""
    path, text, header, entries = _load(campaign)
    if len(pending(entries)) >= PENDING_CAP:
        return None
    _, label = oracle.random_event_focus(rng=rng)
    now = session_count(text)
    e = {"id": _next_id(entries), "trigger": "session opens",
         "event": f"random event focus: {label}. Interpret against current threads, "
                  "NPCs and places before firing.",
         "ask": None, "if_ignored": None,
         "demands": None,                       # a d100 does not decide pressure
         "surfaces_as": "rumour", "visible_to_players": False,
         "expires_by": f"session {now + ROLL_LIFETIME}", "status": "pending"}
    entries.append(e)
    _save(path, text, header, entries)
    return e


# ── reports ──────────────────────────────────────────────────────────────────

def _expires_session(e: dict):
    m = re.fullmatch(r"\s*session\s+(\d+)\s*", str(e.get("expires_by") or ""), re.I)
    return int(m.group(1)) if m else None


def validate_entries(entries: list, now: int) -> list:
    """Problems as strings. Expiry is surfaced here, never auto-cleaned."""
    out, seen = [], set()
    for e in entries:
        i = e.get("id") or "(no id)"
        if i in seen:
            out.append(f"{i}: duplicate id")
        seen.add(i)
        if "fires_on" in e:
            out.append(f"{i}: fires_on is not allowed (a dated queue becomes a calendar players learn)")
        if e.get("status", "pending") not in STATUSES:
            out.append(f"{i}: status {e.get('status')!r} is not one of {'|'.join(STATUSES)}")
        if e.get("demands") not in (*DEMANDS, None):
            out.append(f"{i}: demands {e.get('demands')!r} is not none|ambient|urgent|null")
        if e.get("surfaces_as") not in SURFACES:
            out.append(f"{i}: surfaces_as {e.get('surfaces_as')!r} is not one of {'|'.join(SURFACES)}")
        if not e.get("event") or not e.get("trigger"):
            out.append(f"{i}: needs both trigger and event")
        exp = _expires_session(e)
        if e.get("status", "pending") == "pending" and exp is not None and now > exp:
            out.append(f"{i}: expired (expires_by {e.get('expires_by')}, now session {now}); "
                       "fire it, dismiss it, or rewrite it")
    return out


def _brief(e: dict) -> str:
    d = e.get("demands")
    return (f"{e.get('id')}  [{e.get('surfaces_as')}]  trigger: {e.get('trigger')}  "
            f"demands: {d if d is not None else 'none claimed'}  "
            f"expires: {e.get('expires_by') or '-'}\n      {e.get('event')}")


def start_report(entries: list, now: int) -> list:
    lines = [f"world queue: {len(pending(entries))} pending"]
    lines += ["  " + _brief(e) for e in pending(entries)]
    for e in entries:
        if int(e.get("dismissals") or 0) >= DISMISS_SURFACE_AT:
            lines.append(f"  SEE: {e.get('id')} dismissed {e['dismissals']} times "
                         f"(last: {e.get('dismissed_reason')}). Badly written, or aimed at the wrong scene?")
    lines += ["  CHECK: " + p for p in validate_entries(entries, now)]
    return lines


def digest_lines(section_body: str) -> list:
    """What the DM prompt gets from the queue (localdm/context.state_digest).

    Fired entries in full: the DM narrates them. Pending entries as the bare
    event, marked unsurfaced, so a hidden hook is not blurted out early.
    Dismissed entries are dropped."""
    m = _FENCE.search(section_body)
    if not m:
        return []
    out = []
    for e in parse_block(m.group(1))[1]:
        status = e.get("status", "pending")
        if status == "fired":
            out.append(f"- {e.get('id')} FIRED ({e.get('surfaces_as')}): {e.get('event')}"
                       + (f" | ask: {e['ask']}" if e.get("ask") else "")
                       + (f" | if ignored: {e['if_ignored']}" if e.get("if_ignored") else ""))
        elif status == "pending":
            out.append(f"- {e.get('id')} pending, not yet surfaced (do not reveal): {e.get('event')}")
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="World Queue: off-screen pressure (GM-only)")
    ap.add_argument("-c", "--campaign", required=True, help="Campaign name")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="pending entries with triggers and demands")
    sub.add_parser("start", help="the /gm start report")
    sub.add_parser("validate", help="schema problems and expired entries")
    p = sub.add_parser("roll", help="seed at most one event, only while pending < 3")
    p.add_argument("--seed", type=int)
    p = sub.add_parser("add", help="author an entry")
    for k in ("trigger", "event", "ask", "if-ignored", "expires-by"):
        p.add_argument("--" + k, required=k in ("trigger", "event"))
    p.add_argument("--demands", choices=[*DEMANDS, "null"], default="null")
    p.add_argument("--surfaces-as", choices=SURFACES, default="rumour")
    p.add_argument("--visible", action="store_true", help="visible_to_players: true")
    for name in ("fire", "dismiss", "requeue"):
        p = sub.add_parser(name)
        p.add_argument("id")
        if name == "dismiss":
            p.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    c = a.campaign

    if a.cmd in (None, "list"):
        _, text, _, entries = _load(c)
        rows = pending(entries)
        print(f"{len(rows)} pending" if rows else "no pending entries")
        for e in rows:
            print(_brief(e))
        return 0
    if a.cmd == "roll":
        e = roll_event(c, random.Random(a.seed) if a.seed is not None else None)
        if e is None:
            print(f"queue full: {PENDING_CAP} pending, nothing rolled")
            return 0
        print("seeded " + _brief(e))
        print("      fill in ask and if_ignored before firing; demands stays null")
        return 0
    if a.cmd == "add":
        e = add_entry(c, {
            "trigger": a.trigger, "event": a.event, "ask": a.ask,
            "if_ignored": a.if_ignored, "expires_by": a.expires_by,
            "demands": None if a.demands == "null" else a.demands,
            "surfaces_as": a.surfaces_as, "visible_to_players": a.visible})
        print("added " + _brief(e))
        return 0
    if a.cmd == "fire":
        e = fire_entry(c, a.id)
        print(f"fired {e['id']}: {e.get('event')}")
        d = forced_pressure(e)
        if d is not None:
            print(f"demands: pressure {d}. If it differs from the scene's pressure, "
                  "re-emit the directive with it (or dismiss instead; the GM can refuse).")
        print("record it under ## Faction Moves or ## Recent Events")
        return 0
    if a.cmd == "dismiss":
        e = dismiss_entry(c, a.id, a.reason)
        print(f"dismissed {e['id']} ({e['dismissals']}x): {e['dismissed_reason']}")
        return 0
    if a.cmd == "requeue":
        e = requeue_entry(c, a.id)
        print(f"requeued {e['id']}")
        return 0
    _, text, _, entries = _load(c)
    now = session_count(text)
    if a.cmd == "start":
        print("\n".join(start_report(entries, now)))
        return 0
    problems = validate_entries(entries, now)
    print("\n".join(problems) if problems else "world queue ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
