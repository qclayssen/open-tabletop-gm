#!/usr/bin/env python3
"""play.py: play a grid fight yourself, in the terminal. The game runs the GM's side.

    python3 scripts/tactics/play.py [tutorial|kobolds|frogs|mephit] [options]

You play Kairos (tests/fixtures/Kairos_Level1.md, or --sheet for your own
character). The enemies take their turns on their own: the game asks the
engine for their options and picks the best one, as the GM would. Every rule
(movement, cover, hit rolls, saves, spells, reactions) is the same engine the
GM uses in a real session, through the same commands (scripts/tactics/cli.py).

  tutorial  Training Yard, two kobolds, with lessons on each step (start here)
  kobolds   the same fight without the lessons
  frogs     two giant frogs on Frog Pond (hard: bites grapple and restrain)
  mephit    an ice mephit on the Firejolt rooftops (Frost Breath, a 15 ft cone)

Options
  --sheet PATH      play your own character sheet instead of Kairos
  --map NAME --at SQ --monster "SRD NAME@SQ" [...]   a fight of your own
  --auto-dice       the game rolls your dice too (default: you are asked)
  --seed N          same seed, same enemy dice
  --display         also show the battle map on a running display
  --keep            keep the throwaway campaign folder afterwards

Type `help` during the game for the commands. Needs the SRD dataset
(python3 systems/dnd5e/build_srd.py --no-fvtt). Guide: docs/TUTORIAL.md.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass

_HERE = pathlib.Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if pathlib.Path(p or ".").resolve() != _HERE]
sys.path.insert(0, str(_HERE.parent))

from tactics import cli, engine, state  # noqa: E402
from tactics.grid import col_label, label  # noqa: E402

KAIROS = _HERE.parents[1] / "tests" / "fixtures" / "Kairos_Level1.md"
CAMPAIGN = "play"
MAX_ROUNDS = 30

SCENARIOS = {
    "tutorial": {"map": "training-yard", "at": "B5", "monsters": ["kobold@K2", "kobold@K6"],
                 "lessons": True, "setup": [["adjust", "{pc}", "ac=15"]], "surprised": True,
                 "intro": "Two kobolds are climbing over the back fence of the Training Yard.\n"
                          "Kairos, a first-level wizard, is alone, with Mage Armor cast this morning\n"
                          "(AC 15). He spots them first: they are surprised and lose their first turn.\n"
                          "Follow the lessons, then win the fight."},
    "kobolds": {"map": "training-yard", "at": "B5", "monsters": ["kobold@K2", "kobold@K6"],
                "intro": "Two kobolds in the Training Yard. Drive them off."},
    "frogs": {"map": "frog-pond", "at": "B7", "monsters": ["giant frog@J5", "giant frog@M11"],
              "intro": "Two giant frogs rise from Frog Pond. Their bites grapple and restrain: keep your distance."},
    "mephit": {"map": "firejolt-rooftops", "at": "F8", "monsters": ["ice mephit@I3"],
               "intro": "An ice mephit perches on the Firejolt rooftops. Watch for its Frost Breath (a 15 ft cone)."},
}


@dataclass
class Lesson:
    title: str
    text: str
    verbs: tuple
    done: bool = False


def tutorial_lessons() -> list:
    return [
        Lesson("Read the map",
               "You are @. The kobolds are 1 and 2. Columns are letters, rows are numbers:\n"
               "you stand on B5. Terrain: . open ground, # wall (blocks moving and sight),\n"
               "o crate or hay bales (half cover, +2 AC, for whoever is behind it),\n"
               ", mud and ~ water (each square costs 10 ft instead of 5).\n"
               "Type `map` to draw it again.", ("map",)),
        Lesson("Where can you go?",
               "Type `reach`. * marks the squares you can walk to this turn, + the ones that\n"
               "need the Dash action too. Kairos has 30 ft: six squares. A diagonal step costs\n"
               "5 ft, like a straight one.", ("reach",)),
        Lesson("Check a move first",
               "Type `preview D4`. It shows the path, what it costs, and whether leaving an\n"
               "enemy's reach would give it an opportunity attack. Nothing moves yet.", ("preview",)),
        Lesson("Move",
               "Type `move D4` (or any square marked *). You can split your movement:\n"
               "move, act, then move again with what is left. `undo` takes a move back\n"
               "until you act.", ("move",)),
        Lesson("Check your odds",
               "Type `targets`. It lists every attack you can make from here, with your\n"
               "chance to hit (cover and advantage are already counted).", ("targets",)),
        Lesson("Attack",
               "Type `attack {foe}` to hurl Fire Bolt (1d10 fire) at creature {foe}. You roll your own\n"
               "dice: type the number your d20 shows, without modifiers (the game adds +5),\n"
               "or just press Enter to let the game roll.", ("attack", "cast")),
        Lesson("End your turn",
               "You have used your action. Type `end`. The kobolds then take their turns;\n"
               "the game plays them the way a GM would.", ("end",)),
        Lesson("Your spells",
               "Type `spells` to see what Kairos knows and what can be cast right now.", ("spells",)),
        Lesson("Cast a spell",
               "Try `cast magic missile {foe} {foe} {foe}`: three darts that never miss, one\n"
               "target per dart (it spends a 1st-level slot). Or `cast mind sliver {foe}`: the\n"
               "kobold makes an INT save. `area mind sliver {foe}` shows its chance to fail first.",
               ("cast",)),
        Lesson("Defend yourself",
               "When a kobold would hit you, the game can offer Shield (+5 AC until your next\n"
               "turn): answer y or n. On your turn, `dodge` makes attacks against you harder,\n"
               "`disengage` lets you walk away without opportunity attacks, `dash` doubles\n"
               "your movement. Type `end` when you are done.", ("end",)),
    ]


HELP = """Commands (squares like D4; creatures by their map symbol, like 1, or their id, like kobold-1):
  map                  draw the battle map            reach        where you can move (* walk, + Dash)
  status               everyone's HP and square       log [n]      the last n things that happened
  preview D4           cost and risk of a move        move D4      move there (walks around walls)
  targets              your attacks and hit chances   attack 1     your best attack at creature 1
  attack 1 dagger      a named attack                 spells       your spells, and which you can cast
  cast fire bolt 1     cast at a creature             cast magic missile 1 1 2   one target per dart
  area mind sliver 2   preview a spell: who, what chance, expected damage
  dash | disengage | dodge | hide | stand | escape      the other actions
  undo                 take back your last move       reactions ask|auto|off   Shield and Silvery Barbs
  end                  end your turn                  quit         leave the game
When a roll is asked for, type the die face (no modifier) or press Enter to let the game roll.
When a reaction is offered (an opportunity attack, Shield), answer y or n."""

ALIASES = {"m": "map", "s": "status", "go": "move", "a": "attack", "c": "cast", "t": "targets",
           "r": "reach", "e": "end", "done": "end", "pass": "end", "end-turn": "end",
           "q": "quit", "exit": "quit", "undo-move": "undo", "preview-area": "area", "h": "help"}
SIMPLE = {"dash", "disengage", "dodge", "hide", "stand", "escape"}
NEEDS = {"preview": 1, "move": 1, "attack": 1, "cast": 1, "area": 2, "reactions": 1}


def _color(on: bool):
    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if on else text
    return paint


def _clean(text: str) -> str:
    """Drop the hints meant for the GM (Next: options ..., Then: end-turn)."""
    keep = [ln for ln in text.splitlines()
            if not ln.startswith(("Next: ", "Then: ", "Waiting for ")) and not ln.endswith(": end-turn.")]
    return "\n".join(keep)


class Game:
    """One fight: the player types commands, the game plays the GM's side."""

    def __init__(self, camp: pathlib.Path, scenario: dict, pc_name: str, *, seed=None,
                 ask=input, out=print, color=False):
        self.camp, self.sc, self.pc_name = camp, scenario, pc_name
        self.seed, self.step = seed, 0
        self.ask, self.out, self.paint = ask, out, _color(color)
        self.lessons = tutorial_lessons() if scenario.get("lessons") else []
        self.quit = False
        self.fresh = False                          # the map is on screen, nothing has moved
        self.tipped = False

    # ── engine calls ─────────────────────────────────────────────────────────

    def _call(self, argv: list) -> tuple:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                code = cli.main(["-c", CAMPAIGN, *argv])
            except SystemExit:                      # argparse: a malformed command
                last = (buf.getvalue().strip().splitlines() or ["?"])[-1]
                return 1, f"That command is not complete ({last})."
        return code, buf.getvalue().rstrip()

    def run_cmd(self, *argv) -> tuple:
        """Run one engine command, answering any roll or reaction it stops for.
        A command that stops has changed nothing; it is re-run with the answer
        and replays the same dice (combat/pending.json)."""
        self.step += 1
        seed = [] if self.seed is None else ["--seed", str(self.seed * 1000 + self.step)]
        answers = []
        while True:
            code, text = self._call([*argv, *answers, *seed])
            if code != 2:
                return code, text
            question = text.split(" Nothing has happened yet.")[0].strip()
            if "--react yes or --react no" in text:
                if self.lessons and not self.tipped:
                    self.tipped = True
                    self.out(self.paint("[Tip] A reaction: something you can do outside your turn, once per "
                                        "round.\n      Shield and Silvery Barbs each cost a 1st-level "
                                        "slot. Answer y or n.", "33"))
                reply = self._prompt(f"{question} [y/n] ")
                if reply is None:
                    return 1, "No answer given."
                answers += ["--react", "yes" if reply.startswith("y") else "no"]
            elif "--roll" in text:
                reply = self._roll(question)
                if reply is None:
                    return 1, "No roll given."
                answers += ["--for-me"] if reply == "" else ["--roll", reply]
            else:
                return code, text

    def _prompt(self, question: str):
        while True:
            try:
                reply = self.ask(question).strip().lower()
            except EOFError:
                return None
            if reply in ("y", "n", "yes", "no"):
                return reply
            self.out("Please answer y or n.")

    def _roll(self, question: str):
        while True:
            try:
                reply = self.ask(f"{question}\n  Your roll (Enter = roll for me): ").strip()
            except EOFError:
                return None
            if reply == "" or reply.isdigit():
                return reply
            self.out("Type the number on the die, or press Enter.")

    def _json(self, *argv) -> tuple:
        code, text = self._call([*argv, "--json"])
        if code:
            self.out(text)
            return code, {}
        return 0, json.loads(text)["result"]

    def enc(self):
        return state.load(state.encounter_path(self.camp))

    # ── the board ────────────────────────────────────────────────────────────

    def symbols(self, enc) -> dict:
        """token id -> one-character map symbol: the PC @ (more PCs by initial), enemies 1-9,
        allies a-z. Not the PC's initial: column letters would read like it (K2, K6)."""
        out, n, a = {}, 0, 0
        pcs = [t for t in enc.tokens.values() if t.side == "pc"]
        for t in sorted(enc.tokens.values(), key=lambda t: (t.side != "pc", t.id)):
            if t.side == "pc":
                out[t.id] = "@" if len(pcs) == 1 else t.name[:1].upper()
            elif t.side == "enemy":
                n += 1
                out[t.id] = str(n) if n < 10 else chr(ord("A") + n - 10)
            else:
                out[t.id] = "abcdefghijklmnopqrstuvwxyz"[a % 26]
                a += 1
        return out

    def draw(self, overlay: dict | None = None) -> str:
        enc, p = self.enc(), self.paint
        grid, sym = enc.board(), self.symbols(enc)
        at = {t.pos: t for t in enc.tokens.values() if t.dead}
        at.update({t.pos: t for t in enc.tokens.values() if not t.dead})   # the living on top
        lines = ["    " + "".join(f"{col_label(x):>3}" for x in range(grid.width))]
        for y in range(grid.height):
            row = []
            for x in range(grid.width):
                ch, t, sq = grid.rows[y][x], at.get((x, y)), label((x, y))
                if t is not None and t.dead:
                    cell = p("  x", "2")
                elif t is not None:
                    cell = p(f"{sym[t.id]:>3}", {"pc": "1;36", "enemy": "1;31"}.get(t.side, "1;32"))
                elif overlay and sq in overlay:
                    cell = p(f"{overlay[sq]:>3}", "33")
                else:
                    cell = p(f"{ch:>3}", "2" if ch in "#_" else "34" if ch == "~" else "0")
                row.append(cell)
            lines.append(f"{y + 1:>3} " + "".join(row))
        lines.append("    . ground  # wall  o cover  , difficult  ~ water  x fallen"
                     + ("  * walk  + Dash" if overlay else ""))
        return "\n".join(lines + [self.roster(enc)])

    def roster(self, enc) -> str:
        sym, parts = self.symbols(enc), []
        for tid in enc.order:
            t = enc.tokens[tid]
            if t.dead:
                parts.append(f"{sym[t.id]} {t.name} (fallen)")
                continue
            tags = list(t.conditions) + ([f"concentrating: {t.concentration}"] if t.concentration else [])
            extra = f" [{', '.join(tags)}]" if tags else ""
            you = " (you)" if t.controller == "player" else ""
            parts.append(f"{sym[t.id]} {t.name}{you} {t.square} {t.hp}/{t.max_hp} HP AC {t.ac}{extra}")
        return "    " + "\n    ".join(parts)

    # ── the loop ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        sc = self.sc
        self.out(self.paint(f"== {sc.get('title', 'Grid combat')} ==", "1"))
        if sc.get("intro"):
            self.out(sc["intro"])
        argv = ["start", sc["map"], "--pc", f"{self.pc_name}@{sc['at']}", "--force"]
        for m in sc["monsters"]:
            argv += ["--monster", m]
        code, text = self.run_cmd(*argv)
        self.out(_clean(text))
        pid = cli._slug(self.pc_name)
        for cmd in sc.get("setup", []):
            code = code or self.run_cmd(*[a.format(pc=pid) for a in cmd])[0]
        if code == 0:
            self.out(self.draw())
            self.fresh = True
            self.out("Type `help` at any time for every command.")
        return code == 0

    def outcome(self, enc):
        pcs = [t for t in enc.tokens.values() if t.side == "pc"]
        foes = [t for t in enc.tokens.values() if t.side == "enemy"]
        if not any(t.active for t in foes):
            return "victory"
        if all(t.dead or (t.hp <= 0 and t.stable) for t in pcs):
            return "defeat"
        if enc.round > MAX_ROUNDS:
            return "draw"
        return None

    def play(self) -> str:
        while not self.quit:
            enc = self.enc()
            result = self.outcome(enc)
            if result:
                return result
            cur = enc.current
            if cur.controller == "player" and enc.turn.pending == "death_save":
                self.out(self.paint(f"\n-- Round {enc.round}: {cur.name} is dying. Death save! --",
                                    "1;33"))
                _, text = self.run_cmd("death-save", cur.id)
                self.out(_clean(text))
                self.end_turn()
            elif self.sc.get("surprised") and enc.round == 1 and cur.side == "enemy":
                # 5e surprise (PHB p.189): no move or action on its first turn. The kobolds
                # start 40 ft away, so the no-reaction part never comes up in round 1.
                self.out(self.paint(f"-- {cur.name}'s turn --", "1;31") + "\n  surprised: loses its turn.")
                self.end_turn()
            elif not engine.rules_for(enc).can_act(cur):
                self.out(f"{cur.name} cannot act this turn.")
                self.end_turn()
            elif cur.controller == "player":
                self.fresh = self.fresh and enc.round == 1
                self.player_turn(cur)
            else:
                self.enemy_turn(cur)
        return "quit"

    def end_turn(self):
        code, text = self.run_cmd("end-turn")
        if code:
            self.out(_clean(text))

    def enemy_turn(self, t):
        self.fresh = False
        code, text = self.run_cmd("options", t.id)
        if code == 0 and "\n1. " in text:
            code, text = self.run_cmd("choose", t.id, "1")
            self.out(self.paint(f"-- {t.name}'s turn --", "1;31"))
            for line in _clean(text).removeprefix("1. ").splitlines():
                for event in re.split(r"(?<=[.!]) (?=[A-Z])", line):
                    self.out(self.paint("  " + event, "31"))
        else:
            self.out(self.paint(f"-- {t.name}'s turn --", "1;31") + "\n  holds still.")
        self.end_turn()

    def player_turn(self, t):
        enc = self.enc()
        act = "action used" if enc.turn.action_used else "action ready"
        self.out(self.paint(f"\n-- Round {enc.round}: your turn, {t.name} ({t.hp}/{t.max_hp} HP, "
                            f"{engine.remaining_movement(enc)} ft, {act}) --", "1;36"))
        if not self.fresh:                          # the map was just drawn at the start
            self.out(self.draw())
        self.fresh = False
        self.show_lesson()
        while not self.quit:
            try:
                line = self.ask(self.prompt_line())
            except EOFError:
                self.quit = True
                return
            words = line.strip().split()
            if not words:
                continue
            verb = ALIASES.get(words[0].lower(), words[0].lower())
            ok, finished = self.do(t.id, verb, words[1:])
            if ok:
                self.learn(verb)
            if finished or self.outcome(self.enc()):
                return

    def prompt_line(self) -> str:
        enc = self.enc()
        t = enc.current
        act = "action used" if enc.turn.action_used else "action ready"
        return f"{t.name} {t.hp}/{t.max_hp} HP | {engine.remaining_movement(enc)} ft | {act} > "

    def do(self, pid: str, verb: str, rest: list) -> tuple:
        """Run one player command. Returns (succeeded, turn over)."""
        if verb in ("help", "?"):
            self.out(HELP)
            return True, False
        if verb == "quit":
            self.quit = True
            return True, True
        if verb == "end":
            self.end_turn()
            return True, True
        if verb == "map":
            self.out(self.draw())
            return True, False
        if verb == "reach":
            code, data = self._json("reachable", pid)
            if code:
                return False, False
            overlay = {sq: "+" for sq in data["dash"]}
            overlay.update({sq: "*" for sq in data["walk"]})
            self.out(self.draw(overlay))
            return True, False
        if len(rest) < NEEDS.get(verb, 0):
            self.out(f"`{verb}` needs more: type `help`.")
            return False, False
        rest = self.resolve(rest)
        if verb in SIMPLE:
            argv = [verb, pid]
        else:
            argv = {
                "status": ["status"], "log": ["log", *rest[:1]], "targets": ["targets", pid],
                "spells": ["spells", pid], "undo": ["undo-move"],
                "preview": ["preview", pid, *rest[:1]], "move": ["move", pid, *rest[:1]],
                "attack": ["attack", pid, *rest], "cast": ["cast", pid, *rest],
                "area": ["preview-area", pid, *rest], "reactions": ["reactions", pid, *rest[:1]],
            }.get(verb)
        if argv is None:
            self.out(f"Unknown command {verb!r}. Type `help` for the list.")
            return False, False
        code, text = self.run_cmd(*argv)
        if verb in ("targets", "spells") and code == 0:
            text = "  " + text.replace("; ", "\n  ")
        self.out(_clean(text))
        return code == 0, False

    def resolve(self, words: list) -> list:
        """Map symbols (1, 2, K) to token ids; leave squares, names and flag values alone."""
        sym = {s.lower(): tid for tid, s in self.symbols(self.enc()).items()}
        out, skip = [], False
        for w in words:
            out.append(w if skip or w.startswith("-") else sym.get(w.lower(), w))
            skip = w == "--level"
        return out

    # ── lessons ──────────────────────────────────────────────────────────────

    def show_lesson(self):
        nxt = next((ls for ls in self.lessons if not ls.done), None)
        if nxt is None:
            return
        n = self.lessons.index(nxt) + 1
        self.out(self.paint(f"\n[Lesson {n}/{len(self.lessons)}: {nxt.title}]", "1;33"))
        enc = self.enc()
        sym = self.symbols(enc)
        foe = next((sym[t.id] for t in enc.tokens.values() if t.side == "enemy" and t.active), "1")
        self.out(nxt.text.format(foe=foe))

    def learn(self, verb: str):
        hit = next((ls for ls in self.lessons if not ls.done and verb in ls.verbs), None)
        if hit is None:
            return
        hit.done = True
        if all(ls.done for ls in self.lessons):
            self.out(self.paint("\n[Tutorial complete] You know every move. Now win the fight!", "1;33"))
        elif verb != "end":                         # after `end`, the next lesson waits for your turn
            self.show_lesson()

    def finish(self, result: str) -> None:
        if result != "quit" and self.enc().status == "active":
            _, text = self.run_cmd("end")
            self.out(_clean(text.splitlines()[0]))
        self.out(self.paint({
            "victory": "\nVictory!",
            "defeat": f"\nDefeat. {self.pc_name} falls." + (
                "\nTips: the kobolds step out, sling you and step back behind the wall. Stand where\n"
                "the wall or the hay bales block their line of sight and make them come to you;\n"
                "Magic Missile never misses, and Shield turns a hit into a miss." if self.sc.get("map") ==
                "training-yard" else "\nTry again: the dice may be kinder."),
            "draw": f"\nThe fight drags past round {MAX_ROUNDS}; both sides withdraw.",
            "quit": "\nYou leave the fight.",
        }[result], "1"))


def make_campaign(root: pathlib.Path, sheet: pathlib.Path, roll_mode: str) -> tuple:
    """A throwaway campaign folder with one character. (folder, character name)."""
    d = root / "campaigns" / CAMPAIGN
    (d / "characters").mkdir(parents=True)
    name = sheet.stem.split("_")[0]
    shutil.copy(sheet, d / "characters" / f"{name}.md")
    (d / "state.md").write_text(f"# Campaign: {CAMPAIGN}\n\n## Active Combat\n*(none)*\n\n"
                                f"## Session Flags\nroll_mode: {roll_mode}\n", encoding="utf-8")
    (d / "session-log.md").write_text(f"# Session Log: {CAMPAIGN}\n", encoding="utf-8")
    return d, name


RESULT_CODES = {"victory": 0, "defeat": 3, "draw": 4, "quit": 5}


def main(argv=None, ask=input, out=print) -> int:
    ap = argparse.ArgumentParser(description="Play a grid fight in the terminal.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("scenario", nargs="?", default="tutorial", choices=sorted(SCENARIOS))
    ap.add_argument("--sheet", type=pathlib.Path, default=KAIROS, help="your character sheet (.md)")
    ap.add_argument("--map", help="a map from display/maps (a fight of your own)")
    ap.add_argument("--at", help="your starting square with --map")
    ap.add_argument("--monster", action="append", metavar="'SRD NAME@SQ'", help="with --map")
    ap.add_argument("--auto-dice", action="store_true", help="the game rolls your dice too")
    ap.add_argument("--seed", type=int, help="same seed, same enemy dice")
    ap.add_argument("--display", action="store_true", help="show the map on a running display")
    ap.add_argument("--keep", action="store_true", help="keep the campaign folder")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    sc = dict(SCENARIOS[args.scenario], title=args.scenario.title())
    if args.map:
        if not (args.at and args.monster):
            ap.error("--map needs --at SQUARE and at least one --monster 'NAME@SQ'")
        sc = {"map": args.map, "at": args.at, "monsters": args.monster, "title": args.map}
    if not args.sheet.exists():
        ap.error(f"no sheet at {args.sheet}")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="tactics-play-"))
    saved = {k: os.environ.get(k) for k in ("GM_CAMPAIGN_ROOT", "TACTICS_NO_DISPLAY")}
    os.environ["GM_CAMPAIGN_ROOT"] = str(tmp)
    if not args.display:
        os.environ["TACTICS_NO_DISPLAY"] = "1"
    camp, name = make_campaign(tmp, args.sheet, "auto" if args.auto_dice else "players")
    color = not args.no_color and sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    game = Game(camp, sc, name, seed=args.seed, ask=ask, out=out, color=color)
    result = "quit"
    try:
        if not game.start():
            return 1
        result = game.play()
        game.finish(result)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if args.keep:
            out(f"Campaign kept at {camp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return RESULT_CODES[result]


if __name__ == "__main__":
    sys.exit(main())
