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
    ps._send(ps.FLASK_URL.replace("/stats", path), json.dumps(payload).encode("utf-8"),
             ps._read_token())


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

def snapshot(enc, meta: dict = None) -> dict:
    """Everything the grid view needs, as one JSON-able dict."""
    return {"status": enc.status, "round": enc.round,
            "current": enc.current.id if enc.current else None,
            "order": enc.order, "grid": enc.grid, "meta": meta or {},
            "turn": {"movement_left": max(0, enc.turn.movement_budget - enc.turn.movement_used),
                     "action_used": enc.turn.action_used, "pending": enc.turn.pending},
            "tokens": [{"id": t.id, "name": t.name, "side": t.side, "x": t.x, "y": t.y,
                        "hp": t.hp, "max_hp": t.max_hp, "ac": t.ac, "conditions": t.conditions,
                        "dead": t.dead, "controller": t.controller} for t in enc.tokens.values()],
            "log": enc.log[-8:]}


def push_display(enc, meta: dict = None) -> None:
    if not display_enabled():
        return
    live = [t for t in (enc.tokens[i] for i in enc.order) if t.active]
    turn_order = None if enc.status != "active" else {
        "order": [t.name for t in live], "current": enc.current.name if enc.current else "",
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
