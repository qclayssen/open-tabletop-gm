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
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from paths import find_campaign  # noqa: E402
import safeio  # noqa: E402

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

def _camp_dir(campaign: str, camp_dir=None) -> pathlib.Path:
    if camp_dir is not None:
        path = pathlib.Path(camp_dir)
        if not path.is_dir():
            raise RhythmError(f"campaign_dir: {path} is not a directory")
        return path
    return find_campaign(campaign, migrate=False)


def load(campaign: str, camp_dir=None) -> dict:
    """Everything rhythm knows about a campaign, resolved. Raises RhythmError."""
    cdir = _camp_dir(campaign, camp_dir)
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


def set_value(campaign, target, axis, value, dry_run=False, camp_dir=None):
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
    cdir = _camp_dir(campaign, camp_dir)
    load(campaign, camp_dir=cdir)  # refuse to edit a block that does not parse
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
        safeio.atomic_write_text(path, text)
    return changes


# ── authoring, directives, advance ───────────────────────────────────────────

# Scene-entry lines. The spec writes these with an em dash; this fork does not
# put em dashes in text the table sees, so the break is a hyphen.
_DIRECTIVE_TEXT = {
    ("brisk", "urgent"):
        "[[Scene {id} - brisk, urgent. Clock running. Cut hard, land a decision before this scene ends.]]",
    ("brisk", "ambient"):
        "[[Scene {id} - brisk, ambient. Keep momentum. Skip routine and travel.]]",
    ("measured", "ambient"):
        "[[Scene {id} - measured, ambient. Standard tempo - give the scene the time it needs.]]",
    ("calm", "ambient"):
        "[[Scene {id} - calm, ambient. Linger. Let the players talk and the NPCs answer.]]",
    ("calm", "none"):
        "[[Scene {id} - calm, no pressure. Slow and atmospheric; resolving nothing is allowed.]]",
    ("calm", "urgent"):
        "[[Scene {id} - calm, urgent. Nothing cuts. Everything is at stake. Do not let them breathe.]]",
    ("brisk", "none"):
        "[[Scene {id} - brisk, no pressure. Fast and airborne. Keep it moving and light.]]",
}
_PRESSURE_POINT_LINE = (
    "[[Pressure point - the two-thirds turn. Force a decision or escalation "
    "before the session closes.]]"
)
_SURFACES = ("browser", "repl")
_SCENE_FIELDS = ("id", "label", "location", "intent", "tempo", "pressure",
                 "pressure_point", "exit_when", "stall_after")


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    if (text == "" or text != text.strip()
            or any(ch in text for ch in ":#{}[]&*!|>'\"%@`,")
            or text.lower() in ("null", "true", "false", "yes", "no")):
        return json.dumps(text, ensure_ascii=False)
    return text


def _pressure_flag(value, where: str) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str) and value.strip().lower() in ("true", "yes"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "no", "null", ""):
        return False
    raise RhythmError(f"pressure_point_count: {where} pressure_point must be true or false, "
                      f"got {value!r}")


def _axis_or_refuse(value, axis: str):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("null", ""):
        return None
    if isinstance(value, str) and value.strip().lower() in AXES[axis]:
        return value.strip().lower()
    raise RhythmError(f"invalid_{axis}: {value!r} "
                      f"(expected {' | '.join(AXES[axis])} | null)")


def normalize_plan(plan: dict) -> dict:
    """A plan that may be written. Exactly one pressure point, or RhythmError
    whose message starts with a reason token."""
    if not isinstance(plan, dict):
        raise RhythmError("plan_shape: a session plan must be a mapping")
    raw_scenes = plan.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise RhythmError("pressure_point_count: a session plan needs exactly one "
                          "pressure point, found 0")
    scenes = []
    seen = set()
    for i, raw in enumerate(raw_scenes, start=1):
        if not isinstance(raw, dict):
            raise RhythmError(f"scene_shape: scene {i} must be a mapping")
        sid = raw.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise RhythmError(f"scene_id: scene {i} must have an id")
        sid = sid.strip()
        if sid in seen:
            raise RhythmError(f"scene_id: duplicate scene id {sid!r}")
        seen.add(sid)
        scene = {"id": sid, "pressure_point": _pressure_flag(raw.get("pressure_point", False),
                                                            f"scene {sid!r}")}
        for key in ("label", "location", "intent", "exit_when"):
            if raw.get(key) not in (None, ""):
                scene[key] = str(raw[key])
        for axis in AXES:
            if axis in raw:
                scene[axis] = _axis_or_refuse(raw.get(axis), axis)
        if raw.get("stall_after") not in (None, ""):
            try:
                scene["stall_after"] = int(raw["stall_after"])
            except (TypeError, ValueError) as exc:
                raise RhythmError(f"stall_after: scene {sid!r} stall_after must be an integer") from exc
        scenes.append(scene)
    found = sum(1 for scene in scenes if scene["pressure_point"])
    if found != 1:
        raise RhythmError("pressure_point_count: a session plan needs exactly one "
                          f"pressure point, found {found}")
    out = {"scenes": scenes}
    if plan.get("session") not in (None, ""):
        out["session"] = plan["session"]
    if plan.get("in_world"):
        out["in_world"] = str(plan["in_world"])
    if plan.get("days"):
        out["days"] = [str(day) for day in plan["days"]]
    for axis in AXES:
        if axis in plan:
            out[axis] = _axis_or_refuse(plan.get(axis), axis)
    current = plan.get("current_scene") or scenes[0]["id"]
    current = str(current)
    if current not in seen:
        raise RhythmError(f"current_scene: {current!r} is not a scene in this plan")
    out["current_scene"] = current
    return out


def render_plan(plan: dict) -> str:
    """Fenced YAML for a normalized plan. Authoring writes this once; later edits
    stay line-targeted and do not come back through here."""
    lines = ["## Session Plan", "```yaml"]
    for key in ("session", "in_world", "tempo", "pressure"):
        if key in plan:
            lines.append(f"{key}: {_yaml_scalar(plan[key])}")
    if plan.get("days"):
        lines.append("days:")
        for day in plan["days"]:
            lines.append(f"  - {_yaml_scalar(day)}")
    lines.append("scenes:")
    for scene in plan["scenes"]:
        first = True
        for key in _SCENE_FIELDS:
            if key not in scene and key != "pressure_point":
                continue
            if key == "pressure_point" or key in scene:
                value = scene.get(key, False) if key == "pressure_point" else scene[key]
                prefix = "  - " if first else "    "
                lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
                first = False
    lines.append(f"current_scene: {_yaml_scalar(plan['current_scene'])}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _plan_section_span(text: str):
    heads = list(_H2.finditer(text))
    for i, match in enumerate(heads):
        if match.group(1) != "Session Plan":
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        return match.start(), end
    return None


def _place_plan(text: str, block: str) -> str:
    span = _plan_section_span(text)
    if span is not None:
        start, end = span
        return text[:start] + block + text[end:]
    heads = list(_H2.finditer(text))
    for match in heads:
        if match.group(1) == "Campaign Arc":
            return text[:match.start()] + block + "\n" + text[match.start():]
    sep = "" if text.endswith("\n\n") or text == "" else "\n"
    return text + sep + block


def author_plan(campaign: str, plan: dict, *, dry_run: bool = False, camp_dir=None) -> dict:
    """Write a session plan. Invalid plans raise RhythmError before any write.
    The write goes through safeio, so a failure leaves the previous file intact."""
    normalized = normalize_plan(plan)
    cdir = _camp_dir(campaign, camp_dir)
    path = cdir / "state.md"
    text = _read(path)
    new = _place_plan(text, render_plan(normalized))
    if not new.endswith("\n"):
        new += "\n"
    if not dry_run:
        safeio.atomic_write_text(path, new)
    return load(campaign, camp_dir=cdir) if not dry_run else normalized


def _current(loaded: dict):
    current = loaded.get("current_scene")
    for scene in loaded.get("scenes") or []:
        if scene["id"] == current:
            return scene
    return None


def directive_lines(scene: dict, *, turns_quiet: int = 0) -> list[str]:
    """The [[...]] lines for one scene. stall_after adds advice; it does not
    move current_scene."""
    tempo, pressure = scene["tempo_resolved"], scene["pressure_resolved"]
    template = _DIRECTIVE_TEXT.get((tempo, pressure))
    if template is None:
        pressure_word = "no pressure" if pressure == "none" else pressure
        template = f"[[Scene {{id}} - {tempo}, {pressure_word}.]]"
    lines = [template.format(id=scene["id"])]
    if scene.get("pressure_point"):
        lines.append(_PRESSURE_POINT_LINE)
    stall = scene.get("stall_after")
    if stall is not None:
        try:
            limit = int(stall)
        except (TypeError, ValueError):
            limit = None
        if limit is not None and turns_quiet >= limit:
            exit_when = scene.get("exit_when") or "the exit"
            lines.append(
                f"[[Stall guard - no decision or discovery in {limit} turns. "
                f"Move toward: {exit_when}.]]")
    return lines


def emission_key(scene: dict) -> str:
    return f"{scene['id']}:{scene['tempo_resolved']}:{scene['pressure_resolved']}"


def emit_scene_directives(campaign: str, *, camp_dir=None, surface: str = "repl",
                          turns_quiet: int = 0) -> list[str]:
    """Scene-entry directives for one surface, or [] when this tuple was already sent.

    browser and repl share last_emitted, so a second surface does not repeat the
    same tuple. A later edit that changes tempo or pressure changes the tuple and
    the next call re-emits. An empty list is a decision, not a missing result.
    """
    if surface not in _SURFACES:
        raise RhythmError(f"unknown_surface: {surface!r} (expected browser | repl)")
    loaded = load(campaign, camp_dir=camp_dir)
    scene = _current(loaded)
    if scene is None:
        return []
    key = emission_key(scene)
    if loaded.get("last_emitted") == key:
        return []
    lines = directive_lines(scene, turns_quiet=turns_quiet)
    cdir = _camp_dir(campaign, camp_dir)
    path = cdir / "state.md"
    text = _read(path)
    new = _set_lines(text, "Session Plan", None, "last_emitted", key)
    if new is None:
        raise RhythmError("session_plan: no ## Session Plan block to record last_emitted")
    safeio.atomic_write_text(path, new)
    return lines


def _advance_result(completed: bool, report: str, current_scene) -> dict:
    if not str(report).strip():
        raise RhythmError("advance_silent: an advance produced no report")
    return {"completed": bool(completed), "report": str(report), "current_scene": current_scene}


def auto_advance(campaign: str, *, precondition_met: bool = True, camp_dir=None) -> dict:
    """Step toward the pressure point, or report why the step did not happen.

    Never waits for a precondition that has not arrived, and never returns
    without a report. stall_after is not a trigger: this moves only when called.
    A write that dies mid-flight reports advance_interrupted and leaves the
    previous plan in place.
    """
    loaded = load(campaign, camp_dir=camp_dir)
    current = loaded.get("current_scene")
    if not precondition_met:
        return _advance_result(False, "precondition_unmet: the next scene was not reached", current)
    scenes = loaded.get("scenes") or []
    points = [scene for scene in scenes if scene.get("pressure_point")]
    if not points:
        return _advance_result(False, "no_remaining_pressure_point: the plan has no pressure point",
                               current)
    ids = [scene["id"] for scene in scenes]
    point_id = points[0]["id"]
    ahead = list(ids) if current not in ids else ids[ids.index(current) + 1:]
    if point_id not in ahead:
        return _advance_result(
            False,
            "no_remaining_pressure_point: no pressure point remains ahead of the current scene",
            current)
    nxt = ahead[0]
    cdir = _camp_dir(campaign, camp_dir)
    path = cdir / "state.md"
    new = _set_lines(_read(path), "Session Plan", None, "current_scene", nxt)
    if new is None:
        return _advance_result(False, "no_session_plan: nothing to advance", current)
    try:
        safeio.atomic_write_text(path, new)
    except (OSError, KeyboardInterrupt) as exc:
        return _advance_result(False, f"advance_interrupted: {exc}", current)
    shown = current if current is not None else "none"
    return _advance_result(True, f"advanced: {shown} -> {nxt}", nxt)


def _archive_text(loaded: dict) -> str:
    bits = []
    for scene in loaded.get("scenes") or []:
        mark = " (pressure point)" if scene.get("pressure_point") else ""
        label = scene.get("label") or ""
        bits.append(f"- {scene['id']}: {label}{mark}")
    body = "\n".join(bits) if bits else "- (no scenes)"
    return ("## Archived session plan\n"
            f"session: {loaded.get('session')}\n"
            f"current_scene was: {loaded.get('current_scene')}\n"
            f"{body}\n")


def archive_session(campaign: str, *, camp_dir=None) -> str:
    """Copy the live plan into session-log.md and clear current_scene.

    A failure names itself. Clearing happens only after the log write lands, so
    a dead log write does not drop the only copy.
    """
    loaded = load(campaign, camp_dir=camp_dir)
    if not loaded.get("scenes") and loaded.get("session") is None and not loaded.get("current_scene"):
        return "no_session_plan: nothing to archive"
    cdir = _camp_dir(campaign, camp_dir)
    log = cdir / "session-log.md"
    try:
        existing = log.read_text(encoding="utf-8") if log.exists() else "# Session Log\n"
    except OSError as exc:
        return f"archive_interrupted: {exc}"
    try:
        safeio.atomic_write_text(log, existing.rstrip() + "\n\n" + _archive_text(loaded))
    except (OSError, KeyboardInterrupt) as exc:
        return f"archive_interrupted: {exc}"
    path = cdir / "state.md"
    new = _set_lines(_read(path), "Session Plan", None, "current_scene", None)
    if new is None:
        return "archive_incomplete: session log updated but no Session Plan block to clear"
    try:
        safeio.atomic_write_text(path, new)
    except (OSError, KeyboardInterrupt) as exc:
        return ("archive_incomplete: session log updated but current_scene was not cleared "
                f"({exc})")
    return f"archived: session {loaded.get('session')} current_scene cleared"


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
    au = sub.add_parser("author", help="write a session plan from a JSON file")
    au.add_argument("--file", required=True, help="JSON object with exactly one pressure point")
    au.add_argument("--dry-run", action="store_true")
    ad = sub.add_parser("advance", help="step toward the pressure point, or report why not")
    ad.add_argument("--precondition-unmet", action="store_true")
    sub.add_parser("archive", help="archive the session plan and clear current_scene")
    a = p.parse_args(argv)
    try:
        if a.cmd == "export":
            sys.stdout.write(export_json(a.campaign))
            return 0
        if a.cmd == "author":
            payload = json.loads(pathlib.Path(a.file).read_text(encoding="utf-8"))
            author_plan(a.campaign, payload, dry_run=a.dry_run)
            print("would author" if a.dry_run else "authored")
            return 0
        if a.cmd == "advance":
            result = auto_advance(a.campaign, precondition_met=not a.precondition_unmet)
            print(result["report"])
            return 0 if result["completed"] else 1
        if a.cmd == "archive":
            print(archive_session(a.campaign))
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
