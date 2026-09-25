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
import shlex
from dataclasses import dataclass

PLAYER_VERBS = ("move", "attack", "dash", "disengage", "dodge", "stand", "death-save", "end-turn")


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
            except SystemExit as e:                      # argparse
                code = e.code if isinstance(e.code, int) else 1
        return Result(int(code or 0), buf.getvalue().strip())

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
