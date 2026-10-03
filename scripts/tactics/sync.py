"""sync.py: keep the rest of the skill consistent with the combat engine.

The encounter file is the source of truth during a grid fight; this module
mirrors it outward after every command:

  tracker.json    conditions and death saves (scripts/tracker.py's format)
  display         sidebar HP, conditions and turn order (POST /stats, the
                  same payload push_stats.py sends) and the grid (POST /combat)
  state.md        `## Active Combat` points at combat/encounter.json
  on end          character sheets and session-log.md

Display pushes are best-effort: with the display off, nothing happens.
Set TACTICS_NO_DISPLAY=1 (tests do) to skip them entirely.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import urllib.parse

import safeio   # scripts/safeio.py (on sys.path via tactics/__init__)

from . import slots as slots_mod
from .grid import SQUARE_FT
_SKILL = pathlib.Path(__file__).resolve().parents[2]
_push = None


def _push_stats():
    global _push
    if _push is None:
        spec = importlib.util.spec_from_file_location(
            "push_stats_for_tactics", _SKILL / "display" / "push_stats.py")
        _push = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_push)
    return _push


def display_enabled() -> bool:
    return os.environ.get("TACTICS_NO_DISPLAY", "") not in ("1", "true", "yes")


def _post(path: str, payload: dict) -> None:
    if not display_enabled():
        return
    ps = _push_stats()
    url = ps.FLASK_URL.replace("/stats", path)
    base = os.environ.get("GM_DISPLAY_URL", "").strip().rstrip("/")
    port = os.environ.get("GM_DISPLAY_PORT", "").strip()
    if base:                                # a display anywhere (localdm/play.py sets it)
        url = base + path
    elif port.isdigit():                    # a display on another port (see gm-display-app.py)
        url = url.replace("localhost:5001", f"localhost:{port}")
    host = urllib.parse.urlsplit(url).hostname
    token = ps._read_token() if host in ("localhost", "127.0.0.1", "::1") else ""
    ps._send(url, json.dumps(payload).encode("utf-8"), token)


# ─── tracker.json ─────────────────────────────────────────────────────────────

def tracker_state(camp_dir, enc, drop_monsters: bool = False) -> dict:
    """tracker.json's next contents, merged onto whatever is already on disk.

    Split out from the write because the out-of-combat rest has to know what the
    tracker will say before it writes anything: the sheets, the tracker and the
    calendar are one transaction, and a store whose new text cannot be computed
    without writing it cannot be part of one.
    """
    path = pathlib.Path(camp_dir) / "tracker.json"
    state = safeio.load_json_safe(path)
    for t in enc.tokens.values():
        key = t.name.lower()
        if t.side != "pc" and (t.dead or drop_monsters):
            state.pop(key, None)
            continue
        ent = state.setdefault(key, {"name": t.name, "conditions": [], "concentration": None,
                                     "effects": [], "death_saves": {}})
        ent["conditions"] = list(t.conditions)
        ent["concentration"] = t.concentration
        ent["death_saves"] = {"successes": t.death_saves["successes"],
                              "failures": t.death_saves["failures"], "stable": t.stable}
    return state


def sync_tracker(camp_dir, enc, drop_monsters: bool = False) -> None:
    """Write each token's conditions and death saves in tracker.py's format."""
    safeio.atomic_write_json(pathlib.Path(camp_dir) / "tracker.json",
                             tracker_state(camp_dir, enc, drop_monsters))


# ─── display ──────────────────────────────────────────────────────────────────

def _movement_left(enc) -> int:
    from . import engine                   # the engine's own count (speed changes mid-turn)
    return engine.remaining_movement(enc) if enc.status == "active" and enc.order else 0


def _runs(visible) -> list:
    """Visible squares as [row, first col, last col] runs, top row first.

    The display polls this every turn, and one label per square was ~200
    strings of JSON each time (P8: 1809 bytes on Frog Pond, now 212). Runs
    are a few dozen small numbers for the same information; the display
    expands them back into squares.
    """
    by_row: dict = {}
    for x, y in visible:
        by_row.setdefault(y, []).append(x)
    out = []
    for y in sorted(by_row):
        start = prev = None
        for x in sorted(by_row[y]):
            if prev is not None and x == prev + 1:
                prev = x
                continue
            if prev is not None:
                out.append([y, start, prev])
            start = prev = x
        out.append([y, start, prev])
    return out


def portrait_for(t) -> str | None:
    """The portrait filename for a token, or None if it has none.

    Pure lookup -- the name in, a filename or None out. Whether a fight wants
    portraits at all is decided by the map (`portraits: true` in its JSON, read
    into meta), because that is the GM saying "put faces on this one". A wrong
    face on a creature is a lie the table cannot check, so nothing here guesses
    at a near-match, and most of the SRD's 334 monsters have no art anyway.
    """
    from . import token_portraits
    return token_portraits.resolve(t.name)


def snapshot(enc, meta: dict = None) -> dict:
    """Everything the grid view needs, as one JSON-able dict."""
    def effect_names(t) -> list:
        # Named effects worth showing on the map: not the ones that only grant
        # a condition (the condition is shown already), no duplicates.
        names = [e["name"] for e in t.effects if e.get("name") and not e.get("conditions")]
        if "ac_before_mage_armor" in t.extra:
            names.append("mage armor")
        return list(dict.fromkeys(names))

    def slots(t) -> dict:
        # The display's contract: string levels, {used, total}. It draws these
        # as pips, and it has always read `total` — see the _normalize_slot()
        # fallback it kept for older payloads. Routed through slots.read() so a
        # file that arrived in the display's own {"remaining","max"} spelling is
        # translated once, here, rather than by every reader in the display.
        return {str(lv): dict(s) for lv, s in slots_mod.read(t).items()}

    from .core import rules_for
    R = rules_for(enc)

    def threat(t) -> int:
        """Opportunity-attack reach in feet right now (engine._provokers' test), else 0."""
        return R.reach(t) if t.active and R.can_react(t) and R.opportunity_attack(t) else 0

    portraits = bool((meta or {}).get("portraits"))

    from . import sight
    visible = sight.fog(enc)
    cur = enc.current
    seen = {t.id for t in enc.tokens.values() if sight.shown(enc, t, visible)}
    # An unseen creature's turn: no id, no name, no position (the display says "Enemy turn").
    hidden_turn = bool(cur and cur.id not in seen)
    return {"status": enc.status, "round": enc.round,
            "current": None if hidden_turn or not cur else cur.id, "unseen_turn": hidden_turn,
            "order": [i for i in enc.order if i in seen], "grid": enc.grid, "meta": meta or {},
            # What the board's ruler needs to read feet the way grid.distance does: the
            # diagonal rule rides in grid["diagonals"], the square size is here.
            "square_ft": SQUARE_FT,
            # Squares no PC can see are dimmed; in "hide" mode the creatures there are left out.
            # "runs" is [row, first col, last col] per run: one entry per stretch, not per square.
            "fog": None if visible is None else {"mode": sight.fog_mode(enc),
                                                 "runs": _runs(visible),
                                                 "count": len(visible)},
            # An unseen creature's action economy would tell the players how it moves.
            "turn": {} if hidden_turn else {
                     "movement_left": _movement_left(enc),
                     "action_used": enc.turn.action_used, "pending": enc.turn.pending,
                     "bonus_used": enc.turn.bonus_used,
                     "reaction": bool(cur and not cur.reaction_used)},
            "tokens": [{"id": t.id, "name": t.name, "side": t.side, "x": t.x, "y": t.y,
                        "hp": t.hp, "max_hp": t.max_hp, "ac": t.ac, "conditions": t.conditions,
                        "dead": t.dead, "controller": t.controller,
                        "concentration": t.concentration, "effects": effect_names(t),
                        "reactions": t.reactions,
                        "readied": (t.extra.get("readied") or {}).get("label") or None,
                        "hidden": t.has("hidden"), "threat": threat(t),
                        # A filename for the display to draw inside the token's
                        # shape, or None. Display-only, like a map's `image`: the
                        # engine never reads it and every rule is unchanged, so a
                        # token with no portrait looks exactly as it always did.
                        "portrait": portrait_for(t) if portraits else None,
                        "slots": slots(t) if t.side == "pc" else {}}
                       # A hidden or unseen enemy is not drawn: players must not see where it is.
                       for t in enc.tokens.values() if sight.shown(enc, t, visible)],
            "log": sight.redact_log(enc, enc.log[-8:], visible)}


def push_display(enc, meta: dict = None) -> None:
    if not display_enabled():
        return
    from . import sight
    visible = sight.fog(enc)
    live = [t for t in (enc.tokens[i] for i in enc.order)
            if t.active and sight.shown(enc, t, visible)]       # the sidebar is the players' too
    cur = enc.current
    turn_order = None if enc.status != "active" else {
        "order": [t.name for t in live],
        "current": cur.name if cur and cur in live else "Enemy turn" if cur else "",
        "round": enc.round}
    players = [{"name": t.name, "conditions": list(t.conditions),
                "hp": {"current": t.hp, "max": t.max_hp, "temp": t.temp_hp}}
               for t in enc.tokens.values() if t.side == "pc"]
    _post("/stats", {"players": players, "turn_order": turn_order})
    _post("/combat", {"combat": snapshot(enc, meta)})


# ─── state.md ─────────────────────────────────────────────────────────────────

def set_active_combat(camp_dir, body: str) -> None:
    """Replace the body of `## Active Combat` in state.md (append the section if missing)."""
    path = pathlib.Path(camp_dir) / "state.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    section = re.compile(r"(^## Active Combat[^\n]*\n)(.*?)(?=^## |\Z)", re.M | re.S)
    if section.search(text):
        text = section.sub(lambda m: m.group(1) + body.rstrip() + "\n\n", text, count=1)
    else:
        text = text.rstrip() + "\n\n## Active Combat\n" + body.rstrip() + "\n"
    safeio.atomic_write_text(path, text)


# ─── end of combat ────────────────────────────────────────────────────────────

def find_sheet(camp_dir, name: str):
    """The sheet for a character name, or None.

    Filename first, because that is the convention every campaign follows and it
    costs one comparison. Then the sheet's own "# Name" heading, which is what
    the character is actually called.

    The second pass exists because the filename is not the name. A sheet called
    tamsin.md whose title is "Tamsin Underbough" -- the shape a character creator
    produces, since a person has two names and a file usually gets one -- matched
    nothing, and the caller then said "no sheet in characters/, nothing written."
    That sentence was false: the sheet was there. It just meant the fight's
    damage, spent hit dice and death saves were dropped on the floor, and the
    sheet still showed full HP. A wrong filename is a naming slip; losing a
    fight's results to one is not the same size of thing.
    """
    folder = pathlib.Path(camp_dir) / "characters"
    paths = sorted(folder.glob("*.md")) if folder.is_dir() else []
    for p in paths:
        if p.stem.lower() == name.lower():
            return p
    want = name.lower()
    for p in paths:
        try:
            head = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^#\s+(.+?)\s*$", head, re.M)
        if m and m.group(1).strip().lower() == want:
            return p
    return None


def short_diff(old: str, new: str) -> str:
    """Only the changed lines, prefixed - and +."""
    return "\n".join(l for l in difflib.unified_diff(old.splitlines(), new.splitlines(),
                                                      n=0, lineterm="")
                     if l[:1] in "+-" and not l.startswith(("+++", "---")))


def stage_sheets(camp_dir, enc, rules) -> list:
    """Every PC's sheet with this token's results written in, and nothing on disk yet.

    `[(name, path or None, old_text, new_text)]`. The read and the write are
    separated for the same reason tracker_state() is: a rest that cannot read
    every sheet must not have written any of them.
    """
    out = []
    for t in enc.tokens.values():
        if t.side != "pc":
            continue
        path = find_sheet(camp_dir, t.name)
        if path is None:
            out.append((t.name, None, "", ""))
            continue
        old = path.read_text(encoding="utf-8")
        t.conditions = rules.lasting_conditions(t)
        out.append((t.name, path, old, rules.write_back(old, t)))
    return out


def write_sheets(camp_dir, enc, rules) -> list:
    """Write each PC's results into the campaign's own sheet copy, after
    backing it up to <sheet>.md.bak. Returns [(name, path or None, diff)]."""
    out = []
    for name, path, old, new in stage_sheets(camp_dir, enc, rules):
        diff = ""
        if path is not None and new != old:
            safeio.atomic_write_text(path, new)   # keeps the .bak
            diff = short_diff(old, new)
        out.append((name, path, diff))
    return out


def summary_lines(enc, meta: dict) -> list:
    """3 to 5 lines for the session log."""
    pcs = [t for t in enc.tokens.values() if t.side == "pc"]
    foes = [t for t in enc.tokens.values() if t.side == "enemy"]
    down = [t.name for t in foes if t.dead]
    standing = [t.name for t in foes if not t.dead]
    rounds = f"{enc.round} round{'s' if enc.round != 1 else ''}"
    lines = [f"### Grid combat: {meta.get('name') or enc.grid.get('name') or 'battle'} ({rounds})",
             f"- {', '.join(t.name for t in pcs)} vs {', '.join(t.name for t in foes)}."]
    if standing:
        lines.append(f"- Defeated: {', '.join(down) or 'none'}. Still standing: {', '.join(standing)}.")
    else:
        lines.append(f"- All enemies defeated: {', '.join(down)}.")
    for t in pcs:
        state = "dead" if t.dead else f"{t.hp}/{t.max_hp} HP"
        used = slots_mod.spent_summary(t)
        lines.append(f"- {t.name}: {state}" + (f", spell slots used: {used}" if used else "") + ".")
    return lines[:5]


def append_session_log(camp_dir, lines: list) -> bool:
    """Write the combat summary into session-log.md, above the blank template.

    templates/session-log.md ends with a `## Session Template` block (and its
    `---` rules) that /gm end copies from. Appending at end of file put real
    combat history under that heading, so the summary is inserted just above
    the template instead. A log with no template block is appended to.
    """
    path = pathlib.Path(camp_dir) / "session-log.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    block = "\n".join(lines).strip("\n") + "\n"
    m = re.search(r"^(?:---[ \t]*\n\s*)?## Session Template[^\n]*$", text, re.M)
    if m:
        head = text[:m.start()].rstrip("\n")
        new = head + "\n\n" + block + "\n" + text[m.start():]
    else:
        new = text.rstrip("\n") + "\n\n" + block
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)
    return True
