"""purecore.py: the rules core as a pure function, apply(state, command) -> (state, events).

Everything the engine decides about a fight (distance, cover, action economy,
damage, the turn order) is computed here from a state dict and a command dict,
and nothing else. The core reads no clock, opens no socket, starts no process,
writes no file and pushes nothing to the display. Randomness comes only from the
roller the caller injects, or from a `seed` in the command; with neither, a
command that needs a die raises rather than reaching for the OS entropy pool.

    new_state, events = purecore.apply(state, {"cmd": "attack", "token": "kairos",
                                               "target": "frog-1", "seed": 7})

Same seed, same state, same command: byte-identical output
(`json.dumps(..., sort_keys=True)`), which is what lets a trace be replayed.

Where the edges are. The CLI (cli.py) and the localdm bridge (which runs the
CLI) own persistence: they load encounter.json, build the command from argv,
call `execute` (or `apply`), then save the file, write the roll receipts the
core parked for them, sync the tracker and push the display. The core hands
those over as data: `events` carries the log entries and the rolls, and
`execute` parks receipts on `enc.receipt_sink` (see core.log) instead of
writing them.

Two entry points share one dispatch table:

    apply(state, command, *, roller=None) -> (state, events)
        dict in, dict out; the input is never mutated. For a caller that holds
        the state as data (a replay, a test, an agent).
    execute(enc, command, roller) -> (text, data)
        the same dispatch on an Encounter already in memory, mutating it. The
        CLI uses this one because it has the Encounter loaded and wants the
        GM-facing text. `apply` is `execute` plus a copy in and a dict out.

Pure queries, none of which change the state: `reachable`, `area`, `cover`,
`line_of_sight`, and the read-only commands (status, options, preview, targets,
...) which go through `apply` or `execute` and return the state untouched.

Command dict. `cmd` is the CLI verb. The other keys are the CLI's argument
names, and a key that is absent means "not given":
    token, square, target, attack, option, name, dc, sense, by, source,
    advantage ("normal" | "advantage" | "disadvantage"), reactions
    ({decision key: bool}), words ([str]), level, kind, what ([str]),
    trigger, ally, n, difficulty, mode, action, condition, changes ([str]),
    players (bool), rolls ([int], the natural faces a player rolled), seed.
Refusals raise `CombatError` (the message is safe to show the GM);
`PendingRoll` and `DecisionNeeded` mean the command is waiting for the player
and nothing has changed.

Out of scope here, because they touch the disk by nature: `start`, `end`,
`rest`, formations, scenes and the budget tools. They stay in the CLI.
"""

from __future__ import annotations

import copy
import json
import random
import re

import dice                               # scripts/dice.py (on sys.path via tactics/__init__)

from . import actions, ai, effects, engine, policy, sight, slots, spells, statecard
from .core import CombatError, rules_for
from .grid import area as grid_area
from .grid import parse_square
from .roller import Roller
from .state import Encounter

# The commands that read the fight and never change it. `apply` still returns a
# state for them (unchanged) so a caller has one shape to handle.
QUERIES = frozenset({"status", "options", "preview", "reachable", "approach", "targets",
                     "log", "spells", "preview-area", "sight", "card"})


class _NoSeed(random.Random):
    """The rng of a roller nobody seeded. Any roll through it is a caller error:
    the pure core never falls back to OS entropy."""

    def random(self):
        raise ValueError("this command rolls dice: pass a roller or a seed")

    def getrandbits(self, k):
        raise ValueError("this command rolls dice: pass a roller or a seed")


def make_roller(command: dict, roller: Roller = None) -> Roller:
    """The injected roller, or one built from the command's `seed` and `rolls`."""
    if roller is not None:
        return roller
    seed = command.get("seed")
    rng = dice.new_rng(seed) if seed is not None else _NoSeed()
    return Roller(rng=rng, supplied=list(command.get("rolls") or []),
                  supplied_source="player" if command.get("player_roll") else "verbal",
                  for_me=bool(command.get("for_me")))


# ─── apply ────────────────────────────────────────────────────────────────────

def apply(state: dict, command: dict, *, roller: Roller = None, receipts: bool = False) -> tuple:
    """Run one command against a state dict. Returns (new_state, events).

    The input state is not modified. events is a list of plain dicts: one
    {"type": "log", ...} per entry the command added to the fight log (round,
    actor, kind, text, rolls), then one {"type": "result", "text", "data"}.
    A refusal or a pause raises (see the module docstring) and returns nothing,
    which is the same contract as the CLI: a command that did not finish
    changed nothing.

    receipts=True also returns the roll receipts the core parked (the one write
    the CLI edge owns) as {"type": "receipt", actor, kind, rolls} events after
    the log entries and before the result. The default output is unchanged.
    `roller.policy_rng`, if set, supplies the draw `choose auto` uses to pick an
    option (default: the fight's crc32 stream). Events must be plain JSON: data
    holding a set or an object raises TypeError rather than emitting a repr."""
    enc = Encounter.from_dict(copy.deepcopy(state))
    enc.receipt_sink = []           # the core parks receipts; it never writes them
    before = len(enc.log)
    text, data = execute(enc, command, make_roller(command, roller))
    events = [dict(entry, type="log") for entry in enc.log[before:]]
    if receipts:
        events += [{"type": "receipt", "actor": actor, "kind": kind, "rolls": rolls}
                   for actor, kind, rolls, _states in enc.receipt_sink]
    events.append({"type": "result", "text": text, "data": data})
    return enc.to_dict(), _plain(events)


def _plain(value):
    """Round-trip through JSON so the events hold only plain data (tuples become
    lists). Strict: a value JSON cannot hold raises TypeError, because a repr in
    an event is a detail another language could never reproduce."""
    return json.loads(json.dumps(value))


# ─── pure queries ─────────────────────────────────────────────────────────────

def reachable(state: dict, token: str) -> dict:
    """{"walk": {square: feet}, "dash": {square: feet}} for a token."""
    return engine.reachable(Encounter.from_dict(copy.deepcopy(state)), token)


def area(state: dict, shape: str, size: int, caster: str, target: str = None, *,
         width: int = 5, from_self: bool = True) -> dict:
    """Squares inside an area of effect, from squares given as labels ("D4")."""
    enc = Encounter.from_dict(copy.deepcopy(state))
    return grid_area(enc.board(), shape, size, parse_square(caster),
                     parse_square(target) if target else None, width, from_self)


def cover(state: dict, attacker: str, target: str) -> dict:
    """{"los": bool, "cover": 0 | 2 | 5} between two tokens, other bodies counted."""
    enc = Encounter.from_dict(copy.deepcopy(state))
    a, t = engine._resolve(enc, attacker), engine._resolve(enc, target)
    others = {sq for x in enc.tokens.values() if x.active and x.id not in (a.id, t.id)
              for sq in x.squares}
    return enc.board().cover(a.pos, t.pos, creatures=others,
                             attacker_size=a.size, target_size=t.size)


def line_of_sight(state: dict, a: str, b: str) -> bool:
    """Does some line from a corner of a's square to a corner of b's clear every wall?"""
    enc = Encounter.from_dict(copy.deepcopy(state))
    return enc.board().line_of_sight(engine._resolve(enc, a).pos, engine._resolve(enc, b).pos)


# ─── execute ──────────────────────────────────────────────────────────────────

def execute(enc: Encounter, command: dict, roller: Roller) -> tuple:
    """Run `command` on `enc` in place. Returns (text, data).

    This is the CLI's dispatch for every command that reads or changes a loaded
    fight, moved out of the CLI so it holds no file, display or clock call."""
    cmd = command.get("cmd")
    handler = _HANDLERS.get(cmd)
    if handler is None:
        raise CombatError(f"unknown command {cmd!r}")
    return handler(enc, command, roller)


def _reactions(command) -> dict:
    return command.get("reactions") or {}


def _advantage(command) -> str:
    """The GM's own ruling for this roll, if they made one."""
    return command.get("advantage") or "normal"


def exhaustion_label(token) -> str:
    """The exhaustion level as a word, for the lines the GM reads."""
    for c in token.conditions:
        m = re.fullmatch(r"(?:exhausted|exhaustion)\s*(\d)", c.lower())
        if m:
            return m.group(1)
    return str((token.extra or {}).get("exhaustion_level") or "")


def cmd_status(enc) -> str:
    if enc.status == "active":
        act = "action used" if enc.turn.action_used else "action ready"
        head = (f"Round {enc.round}, {enc.current.name}'s turn "
                f"({engine.remaining_movement(enc)} ft left, {act}).")
    else:
        head = f"Combat ended after round {enc.round}."
    parts = []
    for tid in enc.order:
        x = enc.tokens[tid]
        level = exhaustion_label(x) if x.has("exhaustion") else ""
        tags = [f"exhaustion {level}" if c == "exhaustion" and level else c
                for c in x.conditions]
        if x.concentration:
            tags.append(f"concentrating: {x.concentration}")
        tags += [e["name"] for e in x.effects if not e.get("conditions") and e.get("name")]
        cond = f" [{', '.join(tags)}]" if tags else ""
        left = slots.summary(x)
        if left:
            cond += f" ({left})"
        parts.append(f"{x.name} dead" if x.dead else f"{x.name} {x.square} {x.hp}/{x.max_hp}{cond}")
    ready = actions.readied_lines(enc)
    return f"{head}\n" + " | ".join(parts) + ("\n" + "\n".join(ready) if ready else "")


def cmd_options(enc, ref):
    t = engine._resolve(enc, ref)
    if t.controller == "player":
        raise CombatError(f"{t.name} is player-controlled: wait for the player's action.")
    opts = ai.options(enc, t)
    if not opts:
        return f"{t.name} has nothing to do (cannot act or no targets): end-turn.", {"options": []}
    lines = [f"{t.name} ({t.square}, {t.hp}/{t.max_hp} HP). Pick one, then: choose {t.id} <n>"]
    lines += [f"{o['n']}. {o['label']}" for o in opts]
    waiting = ai.recharging(enc, t)
    if waiting:
        lines.append(f"({', '.join(waiting)} recharging)")
    special = ai.specials(enc, t)
    if special:
        lines.append(f"(Or narrate a special the engine does not run: {', '.join(special)})")
    return "\n".join(lines), {"options": opts}


def split_name(enc, token, words: list, names: list) -> tuple:
    """("spell or action name", [targets]) from loose words: the longest prefix
    that names one of `names`, so both `cast kairos "magic missile" frog-1` and
    `cast kairos magic missile frog-1` work."""
    low = [n.lower() for n in names]
    for i in range(len(words), 0, -1):
        cand = " ".join(words[:i]).lower()
        if cand in low or any(n.startswith(cand) for n in low) and i == 1:
            return " ".join(words[:i]), words[i:]
    return (words[0], words[1:]) if words else ("", [])


def _cast(enc, roller, c) -> tuple:
    caster = engine._resolve(enc, c["token"])
    words = c.get("words") or []
    name, targets = split_name(enc, caster, words, engine.rules_for(enc).known_spells(caster))
    if not name:
        raise CombatError("Name the spell: cast <token> \"<spell>\" [targets].")
    data = spells.cast(enc, roller, caster, name, targets, c.get("level"), _reactions(c))
    return data["text"], data


def _use(enc, roller, c) -> tuple:
    t = engine._resolve(enc, c["token"])
    words = c.get("words") or []
    if len(words) < 2:
        raise CombatError("use <token> \"<action>\" <square|target>")
    name, rest = " ".join(words[:-1]), words[-1]
    data = spells.use_action(enc, roller, t, name, rest, _reactions(c))
    return data["text"], data


def _adjust(enc, c) -> str:
    t = engine._resolve(enc, c["token"])
    done = []
    for pair in c.get("changes") or []:
        key, _, val = pair.partition("=")
        if key not in ("hp", "temp_hp", "ac", "speed", "max_hp") or not val.lstrip("-").isdigit():
            raise CombatError(f"adjust takes hp=N temp_hp=N ac=N speed=N max_hp=N, not {pair!r}")
        setattr(t, key, int(val))
        done.append(f"{key} {val}")
    t.hp = max(0, min(t.hp, t.max_hp))
    text = f"{t.name}: {', '.join(done)}."
    engine._log(enc, "adjust", t.id, f"GM: {text}")
    return text


def _condition(enc, c) -> str:
    t = engine._resolve(enc, c["token"])
    action, condition = c.get("action"), c.get("condition") or ""
    if action == "remove" and condition.lower() == "concentration":
        if not t.concentration:
            raise CombatError(f"{t.name} is not concentrating.")
        text = effects.end_concentration(enc, t, "GM ruling")
        engine._log(enc, "condition", t.id, f"GM: {text}")
        return text
    if action == "remove" and any(condition.lower() in e.get("conditions", []) for e in t.effects):
        names = effects.remove_granting(t, condition.lower())
        t.remove_condition(condition)
        text = f"{t.name}: {condition.lower()} removed (ends {', '.join(names)})."
        engine._log(enc, "condition", t.id, f"GM: {text}")
        return text
    R = rules_for(enc)
    name = condition.lower()
    if c.get("level") is not None:
        name = f"exhaustion {c['level']}"
    # The statblock's condition immunities bind a GM-typed add too, not
    # only rider saves: the engine owns the rule, the GM cannot forget it.
    base = name.split()[0]
    immune = {x.lower() for x in t.condition_immunities}
    if action == "add" and base in immune:
        text = f"{t.name} is immune to being {base}; nothing added."
    else:
        more = R.set_condition(t, name) if action == "add" else R.clear_condition(t, name)
        said = name
        if t.has("exhaustion") and exhaustion_label(t):
            said = f"exhaustion {exhaustion_label(t)}"
        text = f"{t.name}: {said} {'added' if action == 'add' else 'removed'}."
        if action == "add":
            # What the condition is doing to this creature, so nobody has to
            # remember which of fourteen it is.
            text += " " + " ".join(more + R.condition_notes(t))
            text += " " + " ".join(effects.check_incapacitated(enc, t))
        else:
            text += (" " + " ".join(more)) if more else ""
    engine._log(enc, "condition", t.id, f"GM: {text}")
    return text


def _death_save(enc, c, roller):
    t = engine._resolve(enc, c["token"])
    if enc.current.id != t.id:
        raise CombatError(f"It is {enc.current.name}'s turn, not {t.name}'s.")
    return engine.death_save(enc, roller)["text"], {}


def _choose(enc, c, roller):
    t = engine._resolve(enc, c["token"])
    if t.controller == "player":
        raise CombatError(f"{t.name} is player-controlled: use move/attack for the player's choice.")
    n = str(c.get("n"))
    if n == "auto":
        data = policy.choose_auto(enc, roller, t, c.get("difficulty") or "normal", _reactions(c),
                                  getattr(roller, "policy_rng", None))
        return f"{data['option']['label']} [{data['profile']}]. {data['text']}", data
    if n.isdigit():
        data = ai.choose(enc, roller, t, int(n), _reactions(c))
        return f"{data['option']['n']}. {data['text']}", data
    raise CombatError(f"choose {t.id} <n>: an option number, or auto.")


def _data_text(fn):
    """A handler whose engine call returns a dict carrying its own "text"."""
    def run(enc, c, roller):
        data = fn(enc, c, roller)
        return data["text"], data
    return run


def _text_only(fn):
    def run(enc, c, roller):
        return fn(enc, c, roller)["text"], {}
    return run


def _fog(enc, c, roller):
    mode = c["mode"]
    enc.meta["fog"] = mode
    return f"Fog of war: {mode}." + {
        "hide": " The display dims what no PC sees and hides the creatures there.",
        "dim": " The display dims what no PC sees; every creature stays shown.",
        "off": " The display shows the whole map."}[mode], {}


def _reactions_mode(enc, c, roller):
    t = engine._resolve(enc, c["token"])
    t.reactions = c["mode"]
    return f"{t.name}: spell reactions {c['mode']}.", {}


def _targets(enc, c, roller):
    data = {"targets": engine.attack_options(enc, c["token"])}
    legal = [t for t in data["targets"] if t["legal"]]
    return "; ".join(f"{t['attack']} -> {t['target_name']} {t['hit_percent']}%"
                     for t in legal) or "No target in range.", data


def _reachable(enc, c, roller):
    data = engine.reachable(enc, c["token"])
    return f"{len(data['walk'])} squares walking, {len(data['dash'])} more with Dash.", data


def _spells_list(enc, c, roller):
    rows = spells.castable(enc, c["token"])
    return ("; ".join(f"{r['name']}" + ("" if r["ok"] else f" (no: {r['reason'].rstrip('.')})")
                      for r in rows) or "No spells."), {"spells": rows}


def _preview_area(enc, c, roller):
    caster = engine._resolve(enc, c["token"])
    name, targets = split_name(enc, caster, c.get("words") or [],
                               engine.rules_for(enc).known_spells(caster))
    data = spells.preview(enc, caster, name, targets[0] if len(targets) == 1 else targets,
                          c.get("level"))
    return data["text"], data


def _log_tail(enc, c, roller):
    return "\n".join(e["text"] for e in enc.log[-c["n"]:]) or "(empty log)", {}


def _help(enc, c, roller):
    return actions.help_action(enc, c["token"], c.get("ally"), c.get("target"))["text"], {}


def _ready(enc, c, roller):
    data = actions.ready(enc, roller, c["token"], c.get("kind"), " ".join(c.get("what") or []) or None,
                         c.get("target"), c.get("trigger") or "", c.get("level"))
    return data["text"], data


_HANDLERS = {
    "status": lambda enc, c, r: (cmd_status(enc), {}),
    "options": lambda enc, c, r: cmd_options(enc, c["token"]),
    "preview": _data_text(lambda enc, c, r: engine.preview_move(enc, c["token"], c["square"])),
    "reachable": _reachable,
    "approach": _data_text(lambda enc, c, r: engine.approach(enc, c["token"], c["target"])),
    "targets": _targets,
    "log": _log_tail,
    "spells": _spells_list,
    "preview-area": _preview_area,
    "cast": lambda enc, c, r: _cast(enc, r, c),
    "use": lambda enc, c, r: _use(enc, r, c),
    "help": _help,
    "hide": _data_text(lambda enc, c, r: actions.hide(enc, r, c["token"])),
    "escape": _text_only(lambda enc, c, r: actions.escape(enc, r, c["token"])),
    "ready": _ready,
    "trigger": _text_only(lambda enc, c, r: actions.trigger(enc, r, c["token"], c.get("target"),
                                                             _reactions(c))),
    "sight": _data_text(lambda enc, c, r: sight.sight(enc, c["token"], players=bool(c.get("players")))),
    "card": _data_text(lambda enc, c, r: statecard.statecard(enc, c["token"],
                                                             players=bool(c.get("players")))),
    "fog": _fog,
    "reactions": _reactions_mode,
    "choose": _choose,
    "move": _data_text(lambda enc, c, r: engine.move(enc, r, c["token"], c["square"], _reactions(c))),
    "attack": _data_text(lambda enc, c, r: engine.attack(
        enc, r, c["token"], c["target"], c.get("attack"), _reactions(c), _advantage(c))),
    "check": _data_text(lambda enc, c, r: engine.check(
        enc, r, c["token"], c["name"], c.get("dc") or 0, _advantage(c), c.get("sense") or "",
        c.get("by"), c.get("source"))),
    "multiattack": _data_text(lambda enc, c, r: engine.multiattack(
        enc, r, c["token"], c["target"], c.get("option"), _reactions(c))),
    "dash": _text_only(lambda enc, c, r: engine.dash(enc, c["token"])),
    "disengage": _text_only(lambda enc, c, r: engine.disengage(enc, c["token"])),
    "dodge": _text_only(lambda enc, c, r: engine.dodge(enc, c["token"])),
    "stand": _text_only(lambda enc, c, r: engine.stand_up(enc, c["token"])),
    "death-save": _death_save,
    "undo-move": _text_only(lambda enc, c, r: engine.undo_move(enc)),
    "end-turn": _text_only(lambda enc, c, r: engine.end_turn(enc, r)),
    "condition": lambda enc, c, r: (_condition(enc, c), {}),
    "adjust": lambda enc, c, r: (_adjust(enc, c), {}),
}
