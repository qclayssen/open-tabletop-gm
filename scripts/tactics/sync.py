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

from .grid import label

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
    port = os.environ.get("GM_DISPLAY_PORT", "").strip()
    if port.isdigit():                      # a display on another port (see gm-display-app.py)
        url = url.replace("localhost:5001", f"localhost:{port}")
    ps._send(url, json.dumps(payload).encode("utf-8"), ps._read_token())


# ─── tracker.json ─────────────────────────────────────────────────────────────

def sync_tracker(camp_dir, enc, drop_monsters: bool = False) -> None:
    """Write each token's conditions and death saves in tracker.py's format."""
    path = pathlib.Path(camp_dir) / "tracker.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        state = {}
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
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ─── display ──────────────────────────────────────────────────────────────────

def _movement_left(enc) -> int:
    from . import engine                   # the engine's own count (speed changes mid-turn)
    return engine.remaining_movement(enc) if enc.status == "active" and enc.order else 0


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
        return {str(lv): {"used": s.get("used", 0), "total": s.get("total", 0)}
                for lv, s in sorted((t.extra.get("slots") or {}).items())}

    from . import sight
    visible = sight.fog(enc)
    cur = enc.current
    seen = {t.id for t in enc.tokens.values() if sight.shown(enc, t, visible)}
    # An unseen creature's turn: no id, no name, no position (the display says "Enemy turn").
    hidden_turn = bool(cur and cur.id not in seen)
    return {"status": enc.status, "round": enc.round,
            "current": None if hidden_turn or not cur else cur.id, "unseen_turn": hidden_turn,
            "order": [i for i in enc.order if i in seen], "grid": enc.grid, "meta": meta or {},
            # Squares no PC can see are dimmed; in "hide" mode the creatures there are left out.
            "fog": None if visible is None else {"mode": sight.fog_mode(enc),
                                                 "visible": sorted(label(p) for p in visible)},
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
                        "hidden": t.has("hidden"),
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
    path.write_text(text, encoding="utf-8")


# ─── end of combat ────────────────────────────────────────────────────────────

def find_sheet(camp_dir, name: str):
    folder = pathlib.Path(camp_dir) / "characters"
    for p in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        if p.stem.lower() == name.lower():
            return p
    return None


def short_diff(old: str, new: str) -> str:
    """Only the changed lines, prefixed - and +."""
    return "\n".join(l for l in difflib.unified_diff(old.splitlines(), new.splitlines(),
                                                      n=0, lineterm="")
                     if l[:1] in "+-" and not l.startswith(("+++", "---")))


def write_sheets(camp_dir, enc, rules) -> list:
    """Write each PC's results into the campaign's own sheet copy, after
    backing it up to <sheet>.md.bak. Returns [(name, path or None, diff)]."""
    out = []
    for t in enc.tokens.values():
        if t.side != "pc":
            continue
        path = find_sheet(camp_dir, t.name)
        if path is None:
            out.append((t.name, None, ""))
            continue
        old = path.read_text(encoding="utf-8")
        t.conditions = rules.lasting_conditions(t)
        new = rules.write_back(old, t)
        diff = ""
        if new != old:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
            path.write_text(new, encoding="utf-8")
            diff = short_diff(old, new)
        out.append((t.name, path, diff))
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
        slots = t.extra.get("slots") or {}
        used = ", ".join(f"level {lv} {s['used']}/{s['total']}" for lv, s in sorted(slots.items())
                         if s.get("used"))
        lines.append(f"- {t.name}: {state}" + (f", spell slots used: {used}" if used else "") + ".")
    return lines[:5]


def append_session_log(camp_dir, lines: list) -> bool:
    path = pathlib.Path(camp_dir) / "session-log.md"
    if not path.exists():
        return False
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(lines) + "\n")
    return True
