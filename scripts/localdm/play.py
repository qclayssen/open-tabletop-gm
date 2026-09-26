#!/usr/bin/env python3
"""play.py: run a session on a small local model, with a smarter advisor on call.

    python3 scripts/localdm/play.py -c <campaign> [--show-gm-notes] [--budget 12000]
                                    [--display-url URL | --no-display]

Type what your character does. While a roll is pending, type the number on the
die (no modifier), or yes / no for a reaction. Other commands:
    /c <tactics command>      run a grid combat command directly
    /advise <who> <question>  historian, continuity, director, tactician,
                              designer, or council
    /usage                    tokens used, by role and model
    /quit                     stop

Environment: see llm.py (GM_LLM_URL, GM_DM_MODEL, GM_ADVISOR_MODEL, ...).
Narration is mirrored to the display at --display-url, GM_DISPLAY_URL,
localhost:$GM_DISPLAY_PORT, display/.port, else localhost:5001 (display_bridge.py);
grid combat updates follow the same display.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import random
import re
import shlex
import sys
import threading

if __package__ in (None, ""):                        # run as a script
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import localdm                                    # noqa: F401  (puts scripts/ on sys.path)

from localdm import advisor, autopilot, context, display_bridge, llm, reply, triggers  # noqa: E402
from localdm.bridge import Bridge, parse_player_command, resolve_names          # noqa: E402
from localdm.memory import Memory                               # noqa: E402
from localdm.summarizer import Summarizer                       # noqa: E402

ENEMY_PICK = ("You choose actions for monsters in a tabletop fight. Reply with only the "
              "number of the best option for this creature.\n/no_think")
FIGHT_SUMMARY = ("The fight is over. The Engine section is its full log. In 2 to 5 sentences, "
                 "recount how it went, using only what the log says happened. No numbers, "
                 "nothing the log does not show. Then the JSON line with null for every field.")
COMBAT_PARSE = ("A grid fight is running on the engine. Do not narrate and do not ask for a "
                "check: put the player's action in the JSON command field and leave the "
                "narration empty.")
NO_ACTION = ("(engine) I could not read that as a fight action. Name an attack, a move, a "
             "spell or end turn.")
NARRATE = ("Narrate what the Engine section says just happened, in 1 to 4 sentences. "
           "Then the JSON line with null for both fields.")
CHECK_TASK = ("Narrate the outcome of that check in 1 to 4 sentences: what the character "
              "finds or fails to find. Do not mention the number or the DC. Then the JSON "
              "line with null for every field.")
MAX_ENEMY_TURNS = 20
LOG_CAP = 6000                # characters of fight log handed to the end-of-fight summary
ESCALATE_EVERY = 3            # player turns between two DM-asked escalations
SHADOW = ("Review the latest exchange against the campaign notes. In at most 3 short "
          "bullets: a contradiction to fix, a thread or NPC worth bringing back, or what to "
          "set up next. If nothing needs attention, answer only: nothing.")
_DIRECTIVES = re.compile(r"^(?:\s*\[\[.*?\]\])+")
_OPTION = re.compile(r"^(\d+)\. ", re.M)


def _hint(res) -> str:
    return ("Type yes or no." if res.needs_react
            else "Roll it and type the number on the die (no modifier).")


def _waiting(pending) -> str:
    return ("Still waiting on your answer: type yes or no." if pending.get("react")
            else "Still waiting on your roll: type the number on the die (no modifier).")


def _join(*parts) -> str:
    return "\n\n".join(p for p in parts if p)


class Session:
    def __init__(self, campaign, client, models, *, camp_dir, bridge=None,
                 show_notes: bool = False, budget: int = 12000, reasoning="env",
                 local_client=None, shadow: bool = False, combat: str = "model",
                 flavor: str = "big"):
        self.campaign = campaign
        self.client, self.models = client, models      # client: advisors
        self.local = local_client or client            # local: dm, picks, summaries
        self.camp_dir = pathlib.Path(camp_dir)
        self.bridge = bridge or Bridge(campaign, self.camp_dir)
        self.memory = Memory(self.camp_dir)
        self.memory.seed_from_tail()
        # reasoning_effort for local-tier calls; advisors (cloud) get none sent.
        self.reasoning = llm.reasoning_from_env() if reasoning == "env" else reasoning
        self.summarizer = Summarizer(self.local, models.fast, self.memory,
                                     reasoning=self.reasoning)
        self.show_notes, self.budget = show_notes, budget
        self.pending = None            # {"args": [...], "rolls": [...]} while the player rolls
        self.saved_notes = ""          # from /advise, used by the next DM call
        self.turn = 0
        self.display = None            # set by main(): the browser display, if any
        self.directives = []           # table settings from the display, for the next DM call
        self._narrated = []            # this turn's narration, for the display
        # combat "engine": the player's line is parsed, enemies pick by the
        # engine's policy and results are templated (autopilot.py); the model
        # speaks once, at the end of the fight (flavor "big"), or never (flavor "off").
        self.combat, self.flavor = combat, flavor
        self.queue = []                # engine commands left in the player's plan
        self.fight_log = []            # engine text of the running fight, summarized at its end
        self.last_target = ""
        self.last_escalation = -ESCALATE_EVERY
        # Shadow advisor: after each player turn, one advisor reviews it in the
        # background; its notes guide the next turn. Local DMs almost never
        # escalate on their own (milestone 6 benchmark).
        self.shadow = shadow
        self._shadow_thread = None
        self._notes_lock = threading.Lock()

    # ── model calls ────────────────────────────────────────────────────────

    def _state(self) -> str:
        try:
            return (self.camp_dir / "state.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    def _digest(self) -> str:
        return _join(context.state_digest(self._state()), context.sheet_digest(self.camp_dir),
                     context.notes_digest(self.camp_dir))

    AGENCY_FIX = ("Your last draft wrote speech, thoughts or feelings for the player's "
                  "character. Rewrite it: narrate only the world's and the NPCs' response, "
                  "and never say what the player's character says, thinks or feels.")

    def _dm(self, *, player="", engine="", notes="", task="") -> reply.DMReply:
        digest = self._digest()

        def call(extra_task):
            if self.directives:
                extra_task = _join(extra_task, "Table settings: " + " ".join(self.directives))
            msgs = context.build_messages(context.dm_prompt(), digest,
                                          self.memory.summary(), self.memory.unsummarized(),
                                          engine=engine, notes=notes, player=player,
                                          task=extra_task, budget=self.budget)
            return reply.parse(self.local.chat(self.models.dm, msgs, max_tokens=600, role="dm",
                                               reasoning=self.reasoning).text)

        r = call(task)
        if reply.speaks_for_player(r.narration):          # guardrail: one corrective retry
            retry = call(f"{task}\n{self.AGENCY_FIX}".strip())
            if not reply.speaks_for_player(retry.narration):
                return retry
        return r

    def _consult(self, names, question, model=None) -> str:
        recent = "\n".join(f"{context.LABEL[t['role']]}: {t['text']}"
                           for t in self.memory.unsummarized()[-6:] if t["role"] in context.LABEL)
        ctx = _join(self._digest(), self.memory.summary(), recent)
        return advisor.consult(self.client, model or self.models.advisor, names, question, ctx)

    def _trigger_notes(self) -> str:
        if context.council_setting(self._state()) == "off":
            return ""
        new = triggers.fresh(triggers.check(self.bridge.snapshot()), self.memory.seen())
        if not new:
            return ""
        self.memory.mark_seen(t.key for t in new)
        return self._consult(triggers.advisors_for(new), triggers.question(new))

    def _take_notes(self) -> str:
        with self._notes_lock:
            notes, self.saved_notes = self.saved_notes, ""
        return notes

    def _save_notes(self, notes: str) -> None:
        with self._notes_lock:
            self.saved_notes = _join(self.saved_notes, notes)

    def _start_shadow(self, line: str, narration: str):
        if not self.shadow or not narration or (self._shadow_thread
                                                and self._shadow_thread.is_alive()):
            return None
        names = advisor.pick(f"{line} {narration}")[:1]
        question = f"{SHADOW}\n\nPlayer: {line}\nGM: {narration}"

        def run():
            notes = self._consult(names, question)
            body = notes.split(":", 1)[-1].strip().lower()
            if body and not body.startswith(("nothing", "(unavailable")):
                self._save_notes(notes)

        self._shadow_thread = threading.Thread(target=run, daemon=True)
        self._shadow_thread.start()
        return self._shadow_thread

    def join_background(self, timeout=None) -> None:
        if self._shadow_thread:
            self._shadow_thread.join(timeout)
        self.summarizer.join(timeout)

    def _say(self, text: str) -> None:
        """Narration the player sees: remembered, and kept for the display."""
        self.memory.add("dm", text)
        self._narrated.append(text)

    def take_narration(self) -> str:
        """This turn's narration as one text (paragraphs kept), then forgotten."""
        text, self._narrated = "\n\n".join(self._narrated), []
        return text

    def _notes_out(self, notes) -> list:
        return [f"[GM notes]\n{notes}"] if self.show_notes and notes else []

    def _narrate(self, engine_text: str) -> list:
        if self.combat == "engine":
            return self._template(engine_text)
        notes = _join(self._take_notes(), self._trigger_notes())
        r = self._dm(engine=engine_text, notes=notes, task=NARRATE)
        if r.narration:
            self._say(r.narration)
        return self._notes_out(notes) + ([r.narration] if r.narration else [])

    def _template(self, engine_text: str) -> list:
        prose = autopilot.narrate(engine_text, seed=str(self.turn))
        if prose:
            self._say(prose)
        return [prose] if prose else []

    def _close_fight(self, out: list) -> list:
        """End the fight, then one model summary of what the engine logged (never live)."""
        end = self.bridge.run(["end"])                 # write sheets, tracker, session log
        self.memory.add("engine", end.text)
        log, self.fight_log = "\n".join(self.fight_log)[-LOG_CAP:], []
        summary = []
        if self.flavor != "off" and log:
            try:
                r = self._dm(engine=log, task=FIGHT_SUMMARY)
            except llm.LLMError:
                r = None
            if r and r.narration:
                self._say(r.narration)
                summary = [r.narration]
        return out + summary + [end.text]

    # ── engine ─────────────────────────────────────────────────────────────

    def _engine_context(self) -> str:
        snap = self.bridge.snapshot()
        if not snap or snap["status"] != "active":
            return ""
        ids = ", ".join(f"{t['id']} = {t['name']}" for t in snap["tokens"])
        return f"{self.bridge.run(['status']).text}\nToken ids: {ids}"

    def _foes_down(self) -> bool:
        snap = self.bridge.snapshot()
        return bool(snap and snap["status"] == "active"
                    and not any(t["side"] == "enemy" and not t["dead"] for t in snap["tokens"]))

    def _players_turn(self) -> bool:
        snap = self.bridge.snapshot()
        return bool(snap and snap["status"] == "active" and snap["current"]
                    and snap["current"]["controller"] == "player")

    def _engine(self, args, rolls=(), reacts=(), narrate=True) -> list:
        # Every answer so far is replayed, in order: the CLI keeps the seed and
        # maps the n-th --react onto the n-th question it asked.
        full = (list(args) + [x for n in rolls for x in ("--roll", str(n))]
                + [x for a in reacts for x in ("--react", a)])
        if args and args[0] == "end":                  # a fight closed by hand: forget its log
            self.fight_log = []
        res = self.bridge.run(full)
        if res.needs_roll or res.needs_react:
            self.pending = {"args": list(args), "rolls": list(rolls), "reacts": list(reacts),
                            "react": res.needs_react}
            return [f"{res.text}\n{_hint(res)}"]
        self.pending = None
        if args[0] == "choose" and res.code == 0:      # an enemy turn that waited on a reaction
            end = self.bridge.run(["end-turn"])
            res = type(res)(res.code, f"{res.text}\n{end.text}")
        self.memory.add("engine", res.text)
        if res.code != 0:
            self.queue = []
            return [f"(engine) {res.text}"]
        if self.combat == "engine" and args[0] != "end":
            self.fight_log.append(res.text)
        out = self._narrate(res.text) if narrate else [res.text]
        if self.combat == "engine" and "All enemies are down" in res.text:
            self.queue = []
            return self._close_fight(out)
        if self.queue:                                 # the rest of the player's plan
            nxt = self.queue.pop(0)
            if nxt[0] == "end-turn" and self.combat == "engine" and self._foes_down():
                self.queue = []                        # the kill was the last act: close the fight once
                return self._close_fight(out)
            return out + self._engine(nxt)
        return out + self._enemy_phase()

    def _pick(self, menu: str) -> int:
        valid = _OPTION.findall(menu)
        try:
            text = reply.strip_think(self.local.chat(
                self.models.fast, [{"role": "system", "content": ENEMY_PICK},
                                 {"role": "user", "content": menu}],
                max_tokens=16, temperature=0.2, role="enemy-pick",
                reasoning=self.reasoning).text)
        except llm.LLMError:
            return 1
        m = re.search(r"\d+", text)
        return int(m.group()) if m and m.group() in valid else 1

    def _enemy_phase(self) -> list:
        """Run GM-controlled turns until a player's turn, then narrate them in one call."""
        log = []
        for _ in range(MAX_ENEMY_TURNS):
            snap = self.bridge.snapshot()
            if (not snap or snap["status"] != "active" or not snap["current"]
                    or snap["current"]["controller"] == "player"):
                break
            tid = snap["current"]["id"]
            opts = self.bridge.run(["options", tid])
            if opts.code != 0:
                log.append(opts.text)
                break
            if _OPTION.search(opts.text):
                n = "auto" if self.combat == "engine" else str(self._pick(opts.text))
                args = ["choose", tid, n]
                res = self.bridge.run(args)
                if res.needs_roll or res.needs_react:
                    self.pending = {"args": args, "rolls": [], "react": res.needs_react}
                    return self._flush(log) + [f"{res.text}\n{_hint(res)}"]
                log.append(res.text)
            else:
                log.append(opts.text)
            end = self.bridge.run(["end-turn"])
            log.append(end.text)
            if end.code != 0:
                break
        return self._flush(log)

    def _flush(self, log) -> list:
        if not log:
            return []
        text = "\n".join(log)
        self.memory.add("engine", text)
        if self.combat == "engine":
            self.fight_log.append(text)
        return self._narrate(text)

    # ── input ──────────────────────────────────────────────────────────────

    def handle(self, line: str) -> list:
        line = line.strip()
        line = self._take_directives(line)
        if not line:
            return []
        if self.pending:
            low = line.lower()
            p = self.pending
            if line.isdigit() and not p.get("react"):
                return self._engine(p["args"], p["rolls"] + [int(line)], p.get("reacts", []))
            if low in ("yes", "no", "y", "n"):
                return self._engine(p["args"], p["rolls"], p.get("reacts", [])
                                    + ["yes" if low.startswith("y") else "no"])
        if line.isdigit() and not self.pending:
            return ["No roll is waiting on you. Say what your character does."]
        if line.startswith("/c "):
            try:
                args = shlex.split(line[3:])
            except ValueError as e:
                return [f"(engine) {e}"]
            return self._engine(args, narrate=False)
        if line.startswith("/advise"):
            return self._advise(line[len("/advise"):])
        if line == "/usage":
            return self._usage()
        if self.pending:                # the engine is waiting: free text must not reach the DM
            return [f"(engine) {_waiting(self.pending)}"]
        return self._player_turn(line)

    def _ability_check(self, spec: str, line: str) -> list:
        """The DM asked for a check: the player rolls in the browser (or it is rolled here
        with no display), then the DM narrates the outcome."""
        m = re.match(r"\s*([A-Za-z ]+?)\s*(?:DC\s*)?(\d+)?\s*$", spec)
        skill, dc = (m.group(1), int(m.group(2) or 12)) if m else (spec, 12)
        found = context.skill_bonus(self.camp_dir, skill)
        who, skill, bonus = found or ("", skill.title(), 0)
        total = None
        if self.display is not None and self.display.registered:
            self.display.narrate(self.take_narration())      # the scene first, then the roll prompt
            total = self.display.request_roll(who or "any", bonus, f"{skill} check", dc)
        if total is None:
            total = random.randint(1, 20) + bonus
        article = "an" if skill[:1] in "AEIOU" else "a"
        result = (f"{who or 'The player'} rolled {article} {skill} check: {total} against DC "
                  f"{dc}: {'success' if total >= dc else 'failure'}.")
        self.memory.add("engine", result)
        r = self._dm(engine=result, task=CHECK_TASK)
        if r.narration:
            self._say(r.narration)
            return [f"({result})", r.narration]
        return [f"({result})"]

    @staticmethod
    def _directive(text: str) -> str:
        """A display setting as an instruction. The narration-length slider defaults to
        500 words, which read as "write long" to a small model; only a smaller target counts,
        and as a cap."""
        m = re.search(r"narration length.*?~?\s*(\d+)\s*words", text, re.I)
        if m:
            n = int(m.group(1))
            return f"Narration cap: {n} words at most." if n < 300 else ""
        return text.strip()

    def _take_directives(self, line: str) -> str:
        """[[...]] lines from the display (narration length, roll mode) steer the next DM call."""
        m = _DIRECTIVES.match(line)
        self.directives = []
        if m:
            self.directives = [self._directive(d) for d in re.findall(r"\[\[(.*?)\]\]", m.group(0))]
            self.directives = [d for d in self.directives if d]
            line = line[m.end():].strip()
        return line

    def _advise(self, rest: str) -> list:
        try:
            names, question, council = advisor.parse_advise(rest)
        except ValueError as e:
            return [str(e)]
        notes = self._consult(names, question,
                              self.models.council if council else self.models.advisor)
        self._save_notes(notes)
        if self.show_notes:
            return [f"[GM notes]\n{notes}"]
        return ["(The advisors have been consulted. Their notes will guide the next scene.)"]

    def _usage(self) -> list:
        rows = llm.totals(self.memory.dir / "usage.jsonl")
        return [f"{role}  {model}  {calls} calls  {p} in  {c} out"
                for role, model, calls, p, c in rows] or ["(no calls yet)"]

    def _autopilot(self, line: str):
        """Output for a combat action parsed without a model, or None."""
        if self.combat != "engine" or not self._players_turn():
            return None
        from tactics import state
        enc = state.load(state.encounter_path(self.camp_dir))
        p = autopilot.plan(line, enc, enc.current.id, self.last_target)
        if p is None:
            return None
        self.turn += 1
        self.memory.add("player", line)
        if p.ask:
            return [p.ask]
        self.last_target = p.target or self.last_target
        self.queue = [list(c) for c in p.cmds[1:]]
        return self._engine(p.cmds[0])

    def _player_turn(self, line: str) -> list:
        auto = self._autopilot(line)
        if auto is not None:
            return auto
        in_fight = self.combat == "engine" and self._players_turn()
        notes = "" if in_fight else _join(self._take_notes(), self._trigger_notes())
        engine = self._engine_context()
        if in_fight:                     # the model only reads the action; the engine narrates
            r = self._dm(player=line, engine=engine, task=COMBAT_PARSE)
            self.turn += 1
            self.memory.add("player", line)
            args = parse_player_command(r.command) if r.command else None
            if not args:
                return [NO_ACTION]
            snap = self.bridge.snapshot()
            return self._engine(resolve_names(args, snap["tokens"]) if snap else args)
        self.turn += 1
        r = self._dm(player=line, engine=engine, notes=notes)
        # Small models escalate far too often (every turn in the first live run).
        if r.escalate and self.turn - self.last_escalation >= ESCALATE_EVERY:
            self.last_escalation = self.turn
            notes = _join(notes, self._consult(advisor.pick(r.escalate), r.escalate))
            r = self._dm(player=line, engine=engine, notes=notes)
        self.memory.add("player", line)
        out = self._notes_out(notes)
        if r.narration:
            self._say(r.narration)
            out.append(r.narration)
        if r.check and not self._players_turn():
            out += self._ability_check(r.check, line)
        args = parse_player_command(r.command) if r.command else None
        if args and self._players_turn():
            snap = self.bridge.snapshot()
            out += self._engine(resolve_names(args, snap["tokens"]) if snap else args)
        if not notes:                        # nobody advised this turn: review it
            self._start_shadow(line, r.narration)
        self.summarizer.maybe_start()
        return out


def main(argv=None) -> int:
    from paths import find_campaign
    ap = argparse.ArgumentParser(prog="play.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("-c", "--campaign", required=True)
    ap.add_argument("--show-gm-notes", action="store_true",
                    help="print advisor notes (spoilers: for a GM, not a player)")
    ap.add_argument("--budget", type=int, default=12000, help="prompt size budget, in characters")
    ap.add_argument("--combat", choices=["engine", "model"],
                    default=os.environ.get("GM_COMBAT", "engine"),
                    help="engine (default): grid combat runs with no model call until "
                         "the end-of-fight summary; model: the DM model reads actions and picks for enemies")
    ap.add_argument("--flavor", choices=["big", "off"], default=os.environ.get("GM_FLAVOR", "big"),
                    help="engine combat: one model summary when the fight ends (big), or none")
    ap.add_argument("--no-shadow", action="store_true",
                    help="no background advisor review after each turn (also GM_SHADOW=0)")
    ap.add_argument("--display-url", default="", metavar="URL",
                    help="display for narration and grid combat (default: GM_DISPLAY_URL, "
                         "localhost:$GM_DISPLAY_PORT, display/.port, else localhost:5001)")
    ap.add_argument("--no-display", action="store_true",
                    help="send nothing to any display (narration or grid combat)")
    args = ap.parse_args(argv)
    camp_dir = find_campaign(args.campaign)
    if not camp_dir.exists():
        print(f"No campaign {args.campaign!r} at {camp_dir}")
        return 1
    usage = camp_dir / "localdm" / "usage.jsonl"
    client = llm.Client(usage_log=usage)
    local_url = os.environ.get("GM_LOCAL_URL", "").strip()
    local = llm.Client(base_url=local_url, api_key="", usage_log=usage) if local_url else client
    models = llm.Models.from_env()
    shadow = not args.no_shadow and os.environ.get("GM_SHADOW", "1") != "0"
    s = Session(args.campaign, client, models, camp_dir=camp_dir, local_client=local,
                show_notes=args.show_gm_notes, budget=args.budget, shadow=shadow,
                combat=args.combat, flavor=args.flavor)
    print(f"Local DM: {models.dm} via {local.base_url}; advisor {models.advisor} via "
          f"{client.base_url}. /quit to stop.")
    display = display_bridge.from_args(args.campaign, url=args.display_url,
                                       disabled=args.no_display)
    s.display = display
    if display:
        os.environ["GM_DISPLAY_URL"] = display.url     # grid combat pushes (tactics/sync.py) follow
        if display.register():
            display.push_party(context.party_stats(camp_dir))
    else:
        os.environ["TACTICS_NO_DISPLAY"] = "1"
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip() in ("/quit", "/exit"):
            break
        try:
            out = s.handle(line)
            narration = s.take_narration()   # emptied every turn: sent at most once
        except llm.LLMError as e:
            out = [f"(model unavailable: {e})"]
            s.take_narration()               # the terminal does not show it either
            narration = ""
        for chunk in out:
            print(chunk + "\n")
        if display and narration:
            display.narrate(narration)
    s.join_background(timeout=60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
