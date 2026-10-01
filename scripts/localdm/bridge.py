"""bridge.py: tactics commands in-process, and a small snapshot of the fight.

Commands go through tactics.cli.main exactly as the GM would type them, with
stdout and stderr captured, so the loop sees the same 1 to 4 lines. Exit codes:
0 done, 1 refused (nothing changed), 2 waiting for the player (a --roll or a
--react answer) or an argparse error.
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import re
import shlex
from dataclasses import dataclass

PLAYER_VERBS = ("move", "attack", "cast", "dash", "disengage", "dodge", "stand", "death-save",
                "end-turn")
# Words in the whole command (verb, the actor id the engine commands take first, then the
# target or spell) before it is worth running: the tactics REPL's NEEDS, one actor id up.
NEEDS = {"move": 3, "attack": 3, "cast": 3}
# "attack at the frog", "cast fire bolt on the frog": framing words, never a target.
FILLER = ("at", "on", "the")
INCOMPLETE = "That command is not complete. Name what to {verb}, or say it in plain words."


@dataclass
class Result:
    code: int
    text: str

    # The engine's own wording; argparse usage text also mentions --roll.
    @property
    def needs_roll(self) -> bool:
        # A reaction question also lists the dice already rolled as --roll N.
        return (self.code == 2 and "Re-run the same command with" in self.text
                and "--roll" in self.text and not self.needs_react)

    @property
    def needs_react(self) -> bool:
        return (self.code == 2 and "Re-run the same command with" in self.text
                and "--react yes or --react no" in self.text)


def parse_player_command(cmd: str):
    """The DM's suggested command as argv, or None unless it is a plain player action."""
    try:
        args = shlex.split(cmd or "")
    except ValueError:
        return None
    if not args or args[0] not in PLAYER_VERBS or any(a.startswith("-") for a in args[1:]):
        return None
    return args


def resolve_names(args: list, tokens: list) -> list:
    """Replace a token's display name ("Giant Frog", split by shlex) with its id."""
    names = {t["name"].lower(): t["id"] for t in tokens}
    out, i = list(args[:1]), 1
    while i < len(args):
        for n in (3, 2, 1):
            hit = names.get(" ".join(args[i:i + n]).lower())
            if hit:
                out.append(hit)
                i += n
                break
        else:
            out.append(args[i])
            i += 1
    return out


def normalize(args: list, enc=None):
    """Make a parsed player command match what the tactics REPL would send.

    Drops at/on/the from attack and cast, and turns "move <actor> toward the frog" into
    a move at the creature's id, which the engine walks toward with `engine.approach`.
    Returns (args, problem): `problem` is a friendly line when the command is too short to
    run, and the engine is then never called."""
    verb = args[0]
    if verb in ("attack", "cast"):
        args = [verb, *[a for a in args[1:] if a.lower() not in FILLER]]
    if len(args) < NEEDS.get(verb, 0):
        return args, INCOMPLETE.format(verb=verb)
    if verb == "move" and len(args) >= 3 and enc is not None:
        from tactics import fightq
        rest = args[2:]
        if not (len(rest) == 1 and re.fullmatch(r"[A-Za-z]{1,2}\d{1,3}", rest[0])):
            foe = fightq.approach_target(enc, args[1], rest)
            if foe is not None:
                args = [verb, args[1], foe]
    return args, ""


class Bridge:
    def __init__(self, campaign: str, camp_dir):
        self.campaign = campaign
        self.camp_dir = pathlib.Path(camp_dir)

    def run(self, args: list) -> Result:
        from tactics import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                code = cli.main(["-c", self.campaign, *args])
            except SystemExit as e:
                # cli.main now refuses an incomplete command itself (code 1, one friendly
                # line); this is the safety net for anything argparse still raises.
                code = e.code if isinstance(e.code, int) else 1
        text = buf.getvalue().strip()
        if code and not text:
            text = "That command is not complete. Type `/c help` for the commands."
        return Result(int(code or 0), text)

    def snapshot(self):
        from tactics import state
        path = state.encounter_path(self.camp_dir)
        if not path.exists():
            return None
        try:
            enc = state.load(path)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        cur = enc.current if enc.status == "active" and enc.order else None
        return {
            "status": enc.status,
            "round": enc.round,
            "key": ",".join(sorted(enc.tokens)),
            "current": None if cur is None else {"id": cur.id, "name": cur.name,
                                                 "side": cur.side, "controller": cur.controller},
            "tokens": [{"id": t.id, "name": t.name, "side": t.side, "hp": t.hp,
                        "max_hp": t.max_hp, "dead": t.dead, "controller": t.controller}
                       for t in enc.tokens.values()],
        }

    def is_combat_active(self) -> bool:
        snap = self.snapshot()
        return bool(snap and snap.get("status") == "active")
