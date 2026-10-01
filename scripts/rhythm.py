#!/usr/bin/env python3
"""
rhythm.py - the campaign's tempo and pressure, resolved and machine-readable.

Two axes, three tiers (docs/specs/RHYTHM-SCHEMA.md in the dnd-gm root repo):

    tempo     brisk | measured | calm       how fast the DM cuts between beats
    pressure  none  | ambient  | urgent     stakes per unit of time

    scene -> session -> campaign -> default (measured / ambient)

Campaign tier lives in world.md under `## Campaign Rhythm`; the session plan
(scene and session tiers) lives in state.md under `## Session Plan`. Both are a
fenced YAML block. The two axes resolve independently and every resolved value
carries its provenance, so a consumer never has to walk the tiers itself.

This is a narration dial. Nothing in scripts/tactics/ reads it: tempo never
changes a roll, a rule, or what is true in the fiction.

Usage:
    python3 rhythm.py -c <campaign> show
    python3 rhythm.py -c <campaign> plan
    python3 rhythm.py -c <campaign> resolve
    python3 rhythm.py -c <campaign> export --json
    python3 rhythm.py -c <campaign> validate
    python3 rhythm.py -c <campaign> set <scene-id|session|campaign> <tempo|pressure|preset> <value> [--dry-run]

A YAML parse failure is a hard error naming file and line, never an empty result.
Values that do not parse as an enum warn (file, line, value) and fall back a tier.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from paths import find_campaign  # noqa: E402

TEMPO = ("brisk", "measured", "calm")
PRESSURE = ("none", "ambient", "urgent")
AXES = {"tempo": TEMPO, "pressure": PRESSURE}
DEFAULTS = {"tempo": "measured", "pressure": "ambient"}

# The seven named presets over the 3x3 grid (spec 2.3).
PRESETS = {
    "urgent":   ("brisk", "urgent"),
    "brisk":    ("brisk", "ambient"),
    "measured": ("measured", "ambient"),
    "calm":     ("calm", "ambient"),
    "languid":  ("calm", "none"),
    "dread":    ("calm", "urgent"),
    "caper":    ("brisk", "none"),
}

# The old prose-only `pacing` dial (spec 2.4). `adventure` sets tempo only.
LEGACY_PACING = {
    "adventure": {"tempo": "brisk"},
    "mixed": {"tempo": "measured", "pressure": "ambient"},
    "downtime": {"tempo": "calm", "pressure": "ambient"},
}

_PLACEHOLDER = re.compile(r"^<[^>]*>$")
_H2 = re.compile(r"^## +(.+?)\s*$", re.M)
_FENCE = re.compile(r"^```ya?ml[^\n]*\n(.*?)^```", re.M | re.S)
_LEGACY_LINE = re.compile(r"^\s*(?:[-*]\s+)?\*{0,2}pacing\*{0,2}\s*[:=]\s*\*{0,2}\s*`?([A-Za-z_-]+)",
                          re.I)


class RhythmError(Exception):
    """A hard error (unreadable YAML, bad structure). Message starts with file:line."""


def _yaml():
    try:
        import yaml
    except ImportError:
        raise RhythmError("PyYAML is required to read rhythm blocks: pip3 install pyyaml")
    return yaml


# ── reading ──────────────────────────────────────────────────────────────────

def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _block(text: str, heading: str):
    """(body, first_body_line_1based, (start, end)) of the first yaml fence under
    `## heading`, else None. (start, end) is the body's span in `text`."""
    heads = list(_H2.finditer(text))
    for i, m in enumerate(heads):
        if m.group(1) != heading:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        f = _FENCE.search(text, m.end(), end)
        if not f:
            return None
        return f.group(1), text[:f.start(1)].count("\n") + 1, f.span(1)
    return None


def _key_lines(node, path=()):
    """{path tuple: 1-based line offset inside the block} for every node, from yaml.compose."""
    out = {}
    yaml = _yaml()
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            p = path + (k.value,)
            out[p] = v.start_mark.line
            out.update(_key_lines(v, p))
    elif isinstance(node, yaml.SequenceNode):
        for i, v in enumerate(node.value):
            p = path + (i,)
            out[p] = v.start_mark.line
            out.update(_key_lines(v, p))
    return out


def load_block(text: str, heading: str, label: str):
    """Parse the YAML block under `## heading`.

    Returns (data, line_of) where data is a dict ({} when there is no block) and
    line_of(path) gives the 1-based file line of a key path. Any YAML error is a
    RhythmError naming label:line.
    """
    found = _block(text, heading)
    if found is None:
        return {}, lambda p: 0
    body, first, _ = found
    yaml = _yaml()
    try:
        node = yaml.compose(body)
        data = yaml.safe_load(body)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = first + (mark.line if mark else 0)
        why = getattr(e, "problem", None) or str(e)
        raise RhythmError(f"{label}:{line}: unreadable YAML under '## {heading}': {why}")
    if data is None:
        return {}, lambda p: 0
    if not isinstance(data, dict):
        raise RhythmError(f"{label}:{first}: '## {heading}' must hold a YAML mapping")
    lines = _key_lines(node)
    return data, lambda p: first + lines.get(tuple(p), 0)


def _axis_value(val, axis, label, line, warnings):
    """A legal enum value or None. Unset and <placeholder> are None silently; anything
    else that is not an enum value warns (file, line, value) and counts as unset, so it
    falls back a tier."""
    if val is None:
        return None
    if isinstance(val, str) and (not val.strip() or _PLACEHOLDER.match(val.strip())):
        return None
    if isinstance(val, str) and val.strip().lower() in AXES[axis]:
        return val.strip().lower()
    warnings.append(f"{label}:{line}: invalid {axis} {val!r} "
                    f"(expected {' | '.join(AXES[axis])}); falling back a tier")
    return None


def _legacy_pacing(state_text: str, world_block: dict, warnings, world_line):
    """The old `pacing` value as {axis: value}. Source: a `pacing:` key in the Campaign
    Rhythm block, else a `pacing` line under state.md `## Session Flags`."""
    raw, label, line = None, "", 0
    if "pacing" in world_block:
        raw, label, line = world_block["pacing"], "world.md", world_line(("pacing",))
    else:
        heads = list(_H2.finditer(state_text))
        for i, m in enumerate(heads):
            if m.group(1) != "Session Flags":
                continue
            end = heads[i + 1].start() if i + 1 < len(heads) else len(state_text)
            base = state_text[:m.end()].count("\n") + 1
            for n, ln in enumerate(state_text[m.end():end].splitlines()):
                hit = _LEGACY_LINE.match(ln)
                if hit:
                    raw, label, line = hit.group(1), "state.md", base + n
                    break
    if raw is None or (isinstance(raw, str) and _PLACEHOLDER.match(raw.strip())):
        return {}
    key = str(raw).strip().lower()
    if key in LEGACY_PACING:
        return dict(LEGACY_PACING[key])
    warnings.append(f"{label}:{line}: invalid pacing {raw!r} "
                    f"(expected {' | '.join(LEGACY_PACING)}); ignored")
    return {}


def resolve_axis(axis, scene=None, session=None, campaign=None):
    """(value, tier) for one axis: nearest non-null ancestor wins, then the default."""
    for tier, val in (("scene", scene), ("session", session), ("campaign", campaign)):
        if val is not None:
            return val, tier
    return DEFAULTS[axis], "default"


# ── the model ────────────────────────────────────────────────────────────────

def load(campaign: str) -> dict:
    """Everything rhythm knows about a campaign, resolved. Raises RhythmError."""
    cdir = find_campaign(campaign, migrate=False)
    world_text = _read(cdir / "world.md")
    state_text = _read(cdir / "state.md")
    warnings: list[str] = []

    wdata, wline = load_block(world_text, "Campaign Rhythm", "world.md")
    legacy = _legacy_pacing(state_text, wdata, warnings, wline)
    camp = {}
    for axis in AXES:
        val = _axis_value(wdata.get(axis), axis, "world.md", wline((axis,)), warnings)
        camp[axis] = val if val is not None else legacy.get(axis)

    sdata, sline = load_block(state_text, "Session Plan", "state.md")
    sess = {a: _axis_value(sdata.get(a), a, "state.md", sline((a,)), warnings) for a in AXES}

    raw_scenes = sdata.get("scenes")
    if raw_scenes is None:
        raw_scenes = []
    if not isinstance(raw_scenes, list):
        raise RhythmError(f"state.md:{sline(('scenes',))}: 'scenes' must be a list")
    scenes = []
    for i, sc in enumerate(raw_scenes):
        if not isinstance(sc, dict) or not sc.get("id"):
            raise RhythmError(f"state.md:{sline(('scenes', i))}: scene {i + 1} must be a "
                              "mapping with an 'id'")
        own = {a: _axis_value(sc.get(a), a, "state.md", sline(("scenes", i, a)), warnings)
               for a in AXES}
        entry = {"id": str(sc["id"]), "label": sc.get("label"), "intent": sc.get("intent"),
                 "tempo": own["tempo"], "pressure": own["pressure"]}
        for axis in AXES:
            val, tier = resolve_axis(axis, own[axis], sess[axis], camp[axis])
            entry[axis + "_resolved"], entry[axis + "_from"] = val, tier
        entry["pressure_point"] = bool(sc.get("pressure_point", False))
        entry["stall_after"] = sc.get("stall_after")
        entry["exit_when"] = sc.get("exit_when")
        scenes.append(entry)

    in_world = sdata.get("in_world")
    _check_drift(cdir, in_world, warnings)
    return {
        "campaign": cdir.name,
        "campaign_tempo": camp["tempo"] or DEFAULTS["tempo"],
        "campaign_pressure": camp["pressure"] or DEFAULTS["pressure"],
        "session": sdata.get("session"),
        "session_tempo": sess["tempo"],
        "session_pressure": sess["pressure"],
        "current_scene": sdata.get("current_scene"),
        "in_world": in_world,
        "scenes": scenes,
        "last_emitted": sdata.get("last_emitted"),
        "warnings": warnings,
    }


def _check_drift(cdir, in_world, warnings):
    """calendar.json is authoritative: disagreement is a warning, never a repair."""
    if not in_world:
        return
    try:
        cal = json.loads((cdir / "calendar.json").read_text(encoding="utf-8"))
        months = cal.get("months") or []
        expect = f"{cal['day']} {months[cal['month'] - 1]} {cal['year']}"
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return
    if expect not in str(in_world):
        warnings.append(f"state.md: Session Plan in_world {in_world!r} disagrees with "
                        f"calendar.json ({expect}); calendar.json wins")


def export_json(campaign: str) -> str:
    """Byte-deterministic: fixed key order, no timestamps, trailing newline."""
    return json.dumps(load(campaign), indent=2, ensure_ascii=False) + "\n"


# ── mutation: line-targeted, never a reserialize ─────────────────────────────

def _set_lines(text, heading, scene_id, key, value):
    """Rewrite one `key:` line inside the block under `## heading` (a scene's own
    lines when scene_id is set, else the block's top level). Returns new text, or
    None when the block or scene is missing. Comments and prose survive; a missing
    key is inserted right after the scene's id line (or at the top of the block)."""
    found = _block(text, heading)
    if found is None:
        return None
    body, _, (lo, hi) = found
    lines = body.split("\n")
    start, end, indent = 0, len(lines), ""
    if scene_id is not None:
        idre = re.compile(r"^(\s*)-\s+id:\s*[\"']?" + re.escape(scene_id) + r"[\"']?\s*(#.*)?$")
        anyid = re.compile(r"^\s*-\s+id:")
        start = next((i for i, ln in enumerate(lines) if idre.match(ln)), None)
        if start is None:
            return None
        indent = idre.match(lines[start]).group(1) + "  "
        end = next((i for i in range(start + 1, len(lines)) if anyid.match(lines[i])), len(lines))
    key_re = re.compile(r"^(" + re.escape(indent) + r"(?:- )?" + key + r":[ \t]*)([^#\n]*?)([ \t]*(?:#.*)?)$")
    new_value = value if value is not None else "null"
    for i in range(start + (1 if scene_id is not None else 0), end):
        new, n = key_re.subn(lambda m: m.group(1) + new_value + m.group(3), lines[i], count=1)
        if n:
            lines[i] = new
            break
    else:
        lines.insert(start + 1 if scene_id is not None else 0, f"{indent}{key}: {new_value}")
    return text[:lo] + "\n".join(lines) + text[hi:]


def set_value(campaign, target, axis, value, dry_run=False):
    """Apply one `set`. Returns the list of (file, key, value) changes made (or planned)."""
    if axis == "preset":
        if value not in PRESETS:
            raise RhythmError(f"invalid preset {value!r} (expected {' | '.join(PRESETS)})")
        pairs = list(zip(("tempo", "pressure"), PRESETS[value]))
    else:
        if axis not in AXES:
            raise RhythmError(f"unknown axis {axis!r} (expected tempo | pressure | preset)")
        if value == "null":
            if target == "campaign":
                raise RhythmError("campaign has no tier above it to inherit from; pick a value")
            pairs = [(axis, None)]
        elif value in AXES[axis]:
            pairs = [(axis, value)]
        else:
            raise RhythmError(f"invalid {axis} {value!r} (expected {' | '.join(AXES[axis])})")
    cdir = find_campaign(campaign, migrate=False)
    load(campaign)  # refuse to edit a block that does not parse
    fname, heading, scene = (("world.md", "Campaign Rhythm", None) if target == "campaign"
                             else ("state.md", "Session Plan", None if target == "session" else target))
    path = cdir / fname
    text = _read(path)
    changes = []
    for k, v in pairs:
        new = _set_lines(text, heading, scene, k, v)
        if new is None:
            what = f"scene {target!r} in ## Session Plan" if scene else f"## {heading}"
            raise RhythmError(f"{fname}: no {what} to edit")
        text = new
        changes.append((fname, f"{target}.{k}", v))
    if not dry_run:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        os.replace(tmp, path)
    return changes


# ── text views ───────────────────────────────────────────────────────────────

def _fmt_scene(sc, current):
    mark = ">" if sc["id"] == current else " "
    flag = " [pressure point]" if sc["pressure_point"] else ""
    return (f"{mark} {sc['id']}  {sc['tempo_resolved']}/{sc['pressure_resolved']}"
            f" ({sc['tempo_from']}/{sc['pressure_from']})  {sc['label'] or ''}{flag}")


def cmd_show(d):
    print(f"Campaign rhythm: tempo {d['campaign_tempo']}, pressure {d['campaign_pressure']}")
    if d["session"] is None and not d["scenes"]:
        print("No session plan. Run measured/ambient and read the table (Standard 6).")
    else:
        print(f"Session {d['session']}: tempo {d['session_tempo'] or 'inherit'}, "
              f"pressure {d['session_pressure'] or 'inherit'}, current scene "
              f"{d['current_scene'] or 'none'}")
    for w in d["warnings"]:
        print(f"warning: {w}")


def cmd_plan(d):
    if not d["scenes"]:
        print("No scenes planned.")
    for sc in d["scenes"]:
        print(_fmt_scene(sc, d["current_scene"]))
        if sc["exit_when"]:
            print(f"      exit when: {sc['exit_when']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Campaign tempo and pressure, resolved.")
    p.add_argument("-c", "--campaign", required=True, metavar="NAME")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="campaign rhythm and session, one screen")
    sub.add_parser("plan", help="this session's scenes, resolved")
    sub.add_parser("resolve", help="fully resolved plan")
    ex = sub.add_parser("export", help="machine-readable export")
    ex.add_argument("--json", action="store_true", required=True)
    sub.add_parser("validate", help="value checks, days drift, YAML parse")
    st = sub.add_parser("set", help="set one axis (or a preset) on a scene, session or campaign")
    st.add_argument("target", help="a scene id, 'session' or 'campaign'")
    st.add_argument("axis", choices=("tempo", "pressure", "preset"))
    st.add_argument("value")
    st.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.cmd == "export":
            sys.stdout.write(export_json(a.campaign))
            return 0
        if a.cmd == "set":
            for f, k, v in set_value(a.campaign, a.target, a.axis, a.value, a.dry_run):
                print(f"{'would set' if a.dry_run else 'set'} {k} = {v} in {f}")
            return 0
        d = load(a.campaign)
        if a.cmd == "show":
            cmd_show(d)
        elif a.cmd in ("plan", "resolve"):
            cmd_plan(d)
        elif a.cmd == "validate":
            for w in d["warnings"]:
                print(f"warning: {w}")
            print("ok" if not d["warnings"] else f"{len(d['warnings'])} warning(s)")
            return 1 if d["warnings"] else 0
    except RhythmError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
