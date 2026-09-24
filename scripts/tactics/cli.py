"""cli.py: the GM's one-call commands for grid combat (run via scripts/tactics/combat.py).

    python3 scripts/tactics/combat.py -c <campaign> <command> [args] [--roll N] [--json]

Setup and flow
    start <map> --pc NAME@SQ [--pc ...] --monster "SRD NAME@SQ" [...] [--ally "SRD NAME@SQ"]
    status                         whose turn, positions, HP
    options <token>                numbered choices for a GM-controlled creature
    choose <token> <n>             run option n
    end-turn                       next creature in initiative
    end                            finish: write sheets, tracker, session log

Actions (the current creature)
    move <token> <square>          e.g. move kairos D5   (preview <token> <square> checks first)
    attack <token> <target> [attack name]
    dash | disengage | dodge | stand <token>
    death-save <token>
    undo-move                      take back the last move this turn
    condition <token> add|remove <condition>     GM ruling (e.g. a grapple rider)
    adjust <token> hp=N temp_hp=N ac=N           GM correction
    log [n]                        last n combat log lines
    reachable <token>              squares reachable walking and with Dash (for the display)

Dice. Under roll_mode "players" a player's roll is asked for, never invented:
the command stops (exit code 2, nothing changed) and says what to roll.
Re-run the same command with --roll <natural result> (the die face, or the
dice total, without modifiers); repeat --roll for several rolls. --for-me lets
the engine roll this time. --react yes|no answers "take the opportunity attack?".

Output is 1 to 4 plain lines for the GM; --json prints the full result.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import sys

from paths import find_campaign            # scripts/paths.py (on sys.path via tactics/__init__)

from . import ai, engine, maps, state, sync
from .grid import parse_square
from .roller import PendingRoll, Roller
from .state import Encounter

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]

_DISPLAY_CAMPAIGN = _SCRIPTS.parent / "display" / ".campaign"
READ_ONLY = ("status", "options", "preview", "reachable", "log")


class Stop(Exception):
    """Print a message and exit with a code; nothing is saved."""

    def __init__(self, text: str, code: int = 1):
        super().__init__(text)
        self.text, self.code = text, code


# ─── context ──────────────────────────────────────────────────────────────────

def _campaign(args) -> str:
    name = args.campaign or os.environ.get("GM_CAMPAIGN", "")
    if not name and _DISPLAY_CAMPAIGN.exists():
        name = _DISPLAY_CAMPAIGN.read_text(encoding="utf-8").strip()
    if not name:
        raise Stop("No campaign: pass -c <campaign>.")
    return name


def _camp_dir(args) -> pathlib.Path:
    d = find_campaign(_campaign(args))
    if not d.exists():
        raise Stop(f"Campaign folder not found: {d}")
    return d


def _roll_mode(camp_dir) -> str:
    try:
        text = (camp_dir / "state.md").read_text(encoding="utf-8")
    except OSError:
        return "players"
    m = re.search(r"roll_mode\W+(players|auto)\b", text)
    return m.group(1) if m else "players"


def _roller(args) -> Roller:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    return Roller(rng=rng, supplied=list(args.roll or []),
                  supplied_source="player" if args.player_roll else "verbal",
                  for_me=bool(args.for_me))


def _load(camp_dir) -> Encounter:
    path = state.encounter_path(camp_dir)
    if not path.exists():
        raise Stop("No grid combat is running. Start one: start <map> --pc NAME@SQ --monster NAME@SQ")
    return state.load(path)


def _reactions(enc, args) -> dict:
    if not args.react:
        return {}
    return {t.id: args.react == "yes" for t in enc.tokens.values() if t.controller == "player"}


def _next_hint(enc) -> str:
    if enc.status != "active":
        return ""
    t = enc.current
    if enc.turn.pending == "death_save":
        return f"Waiting for {t.name}'s death save: death-save {t.id} --roll <d20>."
    if not engine.rules_for(enc).can_act(t):
        return f"{t.name} cannot act: end-turn."
    if t.controller == "player":
        return f"Waiting for {t.name}."
    return f"Next: options {t.id}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _placement(spec: str):
    if "@" not in spec:
        raise Stop(f"{spec!r}: use NAME@SQUARE, e.g. \"giant frog@J5\".")
    name, sq = spec.rsplit("@", 1)
    try:
        return name.strip(), parse_square(sq)
    except ValueError as e:
        raise Stop(str(e)) from None


# ─── commands ─────────────────────────────────────────────────────────────────

def cmd_start(args, camp_dir):
    path = state.encounter_path(camp_dir)
    if path.exists() and not args.force and state.load(path).status == "active":
        raise Stop("A grid combat is already running. Run end first, or start --force.")
    try:
        m = maps.load(args.map)
    except (FileNotFoundError, ValueError) as e:
        raise Stop(str(e)) from None
    enc = Encounter(campaign=_campaign(args), grid=m["grid"], meta=m["meta"],
                    roll_mode=args.roll_mode or _roll_mode(camp_dir))
    R = engine.rules_for(enc)
    for spec in args.pc or []:
        name, pos = _placement(spec)
        sheet = sync.find_sheet(camp_dir, name)
        if sheet is None:
            raise Stop(f"No sheet for {name} in {camp_dir / 'characters'}.")
        t = R.token_from_sheet(sheet, _slug(name), pos)
        enc.tokens[t.id] = t
    groups = [(s, "enemy") for s in args.monster or []] + [(s, "ally") for s in args.ally or []]
    totals, seen = {}, {}
    for spec, _side in groups:
        key = _placement(spec)[0].lower()
        totals[key] = totals.get(key, 0) + 1
    for spec, side in groups:
        name, pos = _placement(spec)
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
        base, n = _slug(name.split()[-1]), seen[key]
        tid = f"{base}-{n}"
        while tid in enc.tokens:
            n += 1
            tid = f"{base}-{n}"
        display = name.title() + (f" {seen[key]}" if totals[key] > 1 else "")
        try:
            t = R.token_from_monster(name, tid, display, pos)
        except ValueError as e:
            raise Stop(str(e)) from None
        t.side = side
        enc.tokens[t.id] = t
    if not any(t.side == "pc" for t in enc.tokens.values()):
        raise Stop("Add at least one --pc NAME@SQUARE.")
    problems = state.validate(enc)
    if problems:
        raise Stop("Cannot start: " + "; ".join(problems))
    res = engine.begin(enc, _roller(args))
    sync.set_active_combat(camp_dir, f"Grid combat in progress on {m['meta']['name']}: read "
                                     "`scripts/tactics.md`, then run `combat.py status`. "
                                     "`combat/encounter.json` holds HP, positions and turn order.")
    return enc, f"Grid combat on {m['meta']['name']}. {res['text']}"


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
        cond = f" [{', '.join(x.conditions)}]" if x.conditions else ""
        parts.append(f"{x.name} dead" if x.dead else f"{x.name} {x.square} {x.hp}/{x.max_hp}{cond}")
    return f"{head}\n" + " | ".join(parts)


def cmd_options(enc, ref):
    t = engine._resolve(enc, ref)
    if t.controller == "player":
        raise Stop(f"{t.name} is player-controlled: wait for the player's action.")
    opts = ai.options(enc, t)
    if not opts:
        return f"{t.name} has nothing to do (cannot act or no targets): end-turn.", {"options": []}
    lines = [f"{t.name} ({t.square}, {t.hp}/{t.max_hp} HP). Pick one, then: choose {t.id} <n>"]
    lines += [f"{o['n']}. {o['label']}" for o in opts]
    special = ai.specials(enc, t)
    if special:
        lines.append(f"(Or narrate a special the engine does not run: {', '.join(special)})")
    return "\n".join(lines), {"options": opts}


def _adjust(enc, args) -> str:
    t = engine._resolve(enc, args.token)
    done = []
    for pair in args.changes:
        key, _, val = pair.partition("=")
        if key not in ("hp", "temp_hp", "ac", "speed", "max_hp") or not val.lstrip("-").isdigit():
            raise Stop(f"adjust takes hp=N temp_hp=N ac=N speed=N max_hp=N, not {pair!r}")
        setattr(t, key, int(val))
        done.append(f"{key} {val}")
    t.hp = max(0, min(t.hp, t.max_hp))
    text = f"{t.name}: {', '.join(done)}."
    engine._log(enc, "adjust", t.id, f"GM: {text}")
    return text


def _end(camp_dir, enc) -> str:
    if enc.status != "active":
        raise Stop("This combat has already ended.")
    enc.status = "ended"
    written = sync.write_sheets(camp_dir, enc, engine.rules_for(enc))
    lines = sync.summary_lines(enc, enc.meta)
    logged = sync.append_session_log(camp_dir, lines)
    sync.set_active_combat(camp_dir, "*(none)*")
    engine._log(enc, "end", "", f"Combat ended after round {enc.round}.")
    out = [f"Combat ended after round {enc.round}. " + lines[2].lstrip("- ")]
    for name, path, diff in written:
        if path is None:
            out.append(f"{name}: no sheet in characters/, nothing written.")
        elif diff:
            out.append(f"{name}'s sheet updated (backup {path.name}.bak):\n{diff}")
        else:
            out.append(f"{name}'s sheet already up to date.")
    if logged:
        out.append("Session log: combat summary appended.")
    return "\n".join(out)


def run(args) -> int:
    camp_dir = _camp_dir(args)
    roller = _roller(args)
    data = {}
    if args.cmd == "start":
        enc, text = cmd_start(args, camp_dir)
    else:
        enc = _load(camp_dir)
        cmd = args.cmd
        if cmd == "status":
            text = cmd_status(enc)
        elif cmd == "options":
            text, data = cmd_options(enc, args.token)
        elif cmd == "preview":
            data = engine.preview_move(enc, args.token, args.square)
            text = data["text"]
        elif cmd == "reachable":
            data = engine.reachable(enc, args.token)
            text = f"{len(data['walk'])} squares walking, {len(data['dash'])} more with Dash."
        elif cmd == "log":
            text = "\n".join(e["text"] for e in enc.log[-args.n:]) or "(empty log)"
        elif cmd == "choose":
            t = engine._resolve(enc, args.token)
            if t.controller == "player":
                raise Stop(f"{t.name} is player-controlled: use move/attack for the player's choice.")
            data = ai.choose(enc, roller, t, args.n, _reactions(enc, args))
            text = f"{data['option']['n']}. {data['text']}"
        elif cmd == "move":
            data = engine.move(enc, roller, args.token, args.square, _reactions(enc, args))
            text = data["text"]
        elif cmd == "attack":
            data = engine.attack(enc, roller, args.token, args.target, args.attack)
            text = data["text"]
        elif cmd in ("dash", "disengage", "dodge"):
            text = getattr(engine, cmd)(enc, args.token)["text"]
        elif cmd == "stand":
            text = engine.stand_up(enc, args.token)["text"]
        elif cmd == "death-save":
            t = engine._resolve(enc, args.token)
            if enc.current.id != t.id:
                raise Stop(f"It is {enc.current.name}'s turn, not {t.name}'s.")
            text = engine.death_save(enc, roller)["text"]
        elif cmd == "undo-move":
            text = engine.undo_move(enc)["text"]
        elif cmd == "end-turn":
            text = engine.end_turn(enc, roller)["text"]
        elif cmd == "condition":
            t = engine._resolve(enc, args.token)
            (t.add_condition if args.action == "add" else t.remove_condition)(args.condition)
            text = f"{t.name}: {args.condition.lower()} {'added' if args.action == 'add' else 'removed'}."
            engine._log(enc, "condition", t.id, f"GM: {text}")
        elif cmd == "adjust":
            text = _adjust(enc, args)
        else:                                               # end
            text = _end(camp_dir, enc)

    if args.cmd not in READ_ONLY:
        if roller.supplied:
            text += f"\n(Unused --roll values ignored: {roller.supplied})"
        state.save(enc, state.encounter_path(camp_dir))
        sync.sync_tracker(camp_dir, enc, drop_monsters=enc.status == "ended")
        sync.push_display(enc, enc.meta)
        if args.cmd == "choose" and enc.status == "active":
            text += "\nThen: end-turn"
        elif args.cmd in ("start", "end-turn", "death-save"):
            hint = _next_hint(enc)
            if hint:
                text += "\n" + hint
    if args.json:
        print(json.dumps({"text": text, "result": data, "combat": sync.snapshot(enc, enc.meta)},
                         default=str, indent=1))
    else:
        print(text)
    return 0


# ─── argument parsing ─────────────────────────────────────────────────────────

def _common(sub: bool) -> argparse.ArgumentParser:
    """Shared flags, accepted before or after the command. Subcommand copies
    default to SUPPRESS so they never overwrite a value given up front."""
    p = argparse.ArgumentParser(add_help=False)
    none = argparse.SUPPRESS if sub else None
    off = argparse.SUPPRESS if sub else False
    p.add_argument("-c", "--campaign", default=none, help="campaign name")
    p.add_argument("--json", action="store_true", default=off, help="print the full result as JSON")
    p.add_argument("--roll", type=int, action="append", default=none,
                   help="a player's natural roll, no modifier (repeat for several)")
    p.add_argument("--player-roll", action="store_true", default=off,
                   help="the --roll values came from the player's dice roller")
    p.add_argument("--for-me", action="store_true", default=off,
                   help="Roll for me: the engine rolls the player's dice this time")
    p.add_argument("--react", choices=["yes", "no"], default=none,
                   help="answer a player's opportunity attack prompt")
    p.add_argument("--seed", type=int, default=none, help="seed the engine's dice (demos, tests)")
    return p


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="combat.py", parents=[_common(False)],
                                  description="Grid combat commands for the GM.",
                                  formatter_class=argparse.RawDescriptionHelpFormatter,
                                  epilog=__doc__.split("\n\n", 1)[1])
    sub = top.add_subparsers(dest="cmd", required=True, metavar="command")
    c = [_common(True)]
    s = sub.add_parser("start", parents=c, help="start grid combat on a map")
    s.add_argument("map")
    s.add_argument("--pc", action="append", metavar="NAME@SQ")
    s.add_argument("--monster", action="append", metavar="'SRD NAME@SQ'")
    s.add_argument("--ally", action="append", metavar="'SRD NAME@SQ'")
    s.add_argument("--roll-mode", choices=["players", "auto"])
    s.add_argument("--force", action="store_true", help="replace a running combat")
    sub.add_parser("status", parents=c, help="round, turn, positions, HP")
    s = sub.add_parser("options", parents=c, help="numbered choices for a creature")
    s.add_argument("token")
    s = sub.add_parser("choose", parents=c, help="run a numbered option")
    s.add_argument("token")
    s.add_argument("n", type=int)
    for name in ("move", "preview"):
        s = sub.add_parser(name, parents=c)
        s.add_argument("token")
        s.add_argument("square")
    s = sub.add_parser("attack", parents=c)
    s.add_argument("token")
    s.add_argument("target")
    s.add_argument("attack", nargs="*")
    for name in ("dash", "disengage", "dodge", "stand", "death-save", "reachable"):
        s = sub.add_parser(name, parents=c)
        s.add_argument("token")
    sub.add_parser("undo-move", parents=c)
    sub.add_parser("end-turn", parents=c)
    sub.add_parser("end", parents=c, help="end combat, write sheets and the session log")
    s = sub.add_parser("condition", parents=c, help="GM: add or remove a condition")
    s.add_argument("token")
    s.add_argument("action", choices=["add", "remove"])
    s.add_argument("condition")
    s = sub.add_parser("adjust", parents=c, help="GM: correct hp, temp_hp, ac, speed")
    s.add_argument("token")
    s.add_argument("changes", nargs="+")
    s = sub.add_parser("log", parents=c)
    s.add_argument("n", nargs="?", type=int, default=6)
    return top


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if isinstance(getattr(args, "attack", None), list):
        args.attack = " ".join(args.attack) or None
    try:
        return run(args)
    except Stop as e:
        print(e.text)
        return e.code
    except engine.CombatError as e:
        print(str(e))
        return 1
    except PendingRoll as e:
        what = "the d20 face" if e.notation.startswith("1d20") else "the dice total"
        adv = f" with {e.advantage}" if e.advantage != "normal" else ""
        prior = "".join(f"--roll {v} " for v in (args.roll or []))
        print(f"{e.who} rolls {e.notation}{adv} for {e.label}. Nothing has happened yet.\n"
              f"Re-run the same command with {prior}--roll <{what}, no modifier>, "
              f"or --for-me to let the engine roll.")
        return 2
    except engine.DecisionNeeded as e:
        print(f"{e.prompt} Re-run the same command with --react yes or --react no.")
        return 2

