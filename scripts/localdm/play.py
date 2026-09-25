#!/usr/bin/env python3
"""play.py: run a session on a small local model, with a smarter advisor on call.

    python3 scripts/localdm/play.py -c <campaign> [--show-gm-notes] [--budget 12000]

Type what your character does. While a roll is pending, type the number on the
die (no modifier), or yes / no for a reaction. Other commands:
    /c <tactics command>      run a grid combat command directly
    /advise <who> <question>  historian, continuity, director, tactician,
                              designer, or council
    /usage                    tokens used, by role and model
    /quit                     stop

Environment: see llm.py (GM_LLM_URL, GM_DM_MODEL, GM_ADVISOR_MODEL, ...).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shlex
import sys

if __package__ in (None, ""):                        # run as a script
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import localdm                                    # noqa: F401  (puts scripts/ on sys.path)

from localdm import advisor, context, llm, reply, triggers      # noqa: E402
from localdm.bridge import Bridge, parse_player_command          # noqa: E402
from localdm.memory import Memory                               # noqa: E402
from localdm.summarizer import Summarizer                       # noqa: E402

ENEMY_PICK = ("You choose actions for monsters in a tabletop fight. Reply with only the "
              "number of the best option for this creature.\n/no_think")
NARRATE = ("Narrate what the Engine section says just happened, in 1 to 4 sentences. "
           "Then the JSON line with null for both fields.")
MAX_ENEMY_TURNS = 20
ESCALATE_EVERY = 3            # player turns between two DM-asked escalations
_OPTION = re.compile(r"^(\d+)\. ", re.M)


def _join(*parts) -> str:
    return "\n\n".join(p for p in parts if p)


class Session:
    def __init__(self, campaign, client, models, *, camp_dir, bridge=None,
                 show_notes: bool = False, budget: int = 12000, reasoning="env",
                 local_client=None):
        self.campaign = campaign
        self.client, self.models = client, models      # client: advisors
        self.local = local_client or client            # local: dm, picks, summaries
        self.camp_dir = pathlib.Path(camp_dir)
        self.bridge = bridge or Bridge(campaign, self.camp_dir)
        self.memory = Memory(self.camp_dir)
        # reasoning_effort for local-tier calls; advisors (cloud) get none sent.
        self.reasoning = llm.reasoning_from_env() if reasoning == "env" else reasoning
        self.summarizer = Summarizer(self.local, models.fast, self.memory,
                                     reasoning=self.reasoning)
        self.show_notes, self.budget = show_notes, budget
        self.pending = None            # {"args": [...], "rolls": [...]} while the player rolls
        self.saved_notes = ""          # from /advise, used by the next DM call
        self.turn = 0
        self.last_escalation = -ESCALATE_EVERY

    # ── model calls ────────────────────────────────────────────────────────

    def _state(self) -> str:
        try:
            return (self.camp_dir / "state.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    def _dm(self, *, player="", engine="", notes="", task="") -> reply.DMReply:
        msgs = context.build_messages(context.dm_prompt(), context.state_digest(self._state()),
                                      self.memory.summary(), self.memory.unsummarized(),
                                      engine=engine, notes=notes, player=player, task=task,
                                      budget=self.budget)
        return reply.parse(self.local.chat(self.models.dm, msgs, max_tokens=500, role="dm",
                                            reasoning=self.reasoning).text)

    def _consult(self, names, question, model=None) -> str:
        recent = "\n".join(f"{context.LABEL[t['role']]}: {t['text']}"
                           for t in self.memory.unsummarized()[-6:] if t["role"] in context.LABEL)
        ctx = _join(context.state_digest(self._state()), self.memory.summary(), recent)
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
        notes, self.saved_notes = self.saved_notes, ""
        return notes

    def _notes_out(self, notes) -> list:
        return [f"[GM notes]\n{notes}"] if self.show_notes and notes else []

    def _narrate(self, engine_text: str) -> list:
        notes = _join(self._take_notes(), self._trigger_notes())
        r = self._dm(engine=engine_text, notes=notes, task=NARRATE)
        if r.narration:
            self.memory.add("dm", r.narration)
        return self._notes_out(notes) + ([r.narration] if r.narration else [])

    # ── engine ─────────────────────────────────────────────────────────────

    def _engine_context(self) -> str:
        snap = self.bridge.snapshot()
        if not snap or snap["status"] != "active":
            return ""
        ids = ", ".join(f"{t['id']} = {t['name']}" for t in snap["tokens"])
        return f"{self.bridge.run(['status']).text}\nToken ids: {ids}"

    def _players_turn(self) -> bool:
        snap = self.bridge.snapshot()
        return bool(snap and snap["status"] == "active" and snap["current"]
                    and snap["current"]["controller"] == "player")

    def _engine(self, args, rolls=(), react=None, narrate=True) -> list:
        full = list(args) + [x for n in rolls for x in ("--roll", str(n))]
        if react:
            full += ["--react", react]
        res = self.bridge.run(full)
        if res.needs_roll or res.needs_react:
            self.pending = {"args": list(args), "rolls": list(rolls)}
            hint = ("Type yes or no." if res.needs_react
                    else "Roll it and type the number on the die (no modifier).")
            return [f"{res.text}\n{hint}"]
        self.pending = None
        self.memory.add("engine", res.text)
        if res.code != 0:
            return [f"(engine) {res.text}"]
        out = self._narrate(res.text) if narrate else [res.text]
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
                args = ["choose", tid, str(self._pick(opts.text))]
                res = self.bridge.run(args)
                if res.needs_roll or res.needs_react:
                    self.pending = {"args": args, "rolls": []}
                    return self._flush(log) + [res.text]
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
        return self._narrate(text)

    # ── input ──────────────────────────────────────────────────────────────

    def handle(self, line: str) -> list:
        line = line.strip()
        if not line:
            return []
        if self.pending:
            low = line.lower()
            if line.isdigit():
                return self._engine(self.pending["args"], self.pending["rolls"] + [int(line)])
            if low in ("yes", "no", "y", "n"):
                return self._engine(self.pending["args"], self.pending["rolls"],
                                    react="yes" if low.startswith("y") else "no")
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
        return self._player_turn(line)

    def _advise(self, rest: str) -> list:
        try:
            names, question, council = advisor.parse_advise(rest)
        except ValueError as e:
            return [str(e)]
        notes = self._consult(names, question,
                              self.models.council if council else self.models.advisor)
        self.saved_notes = _join(self.saved_notes, notes)
        if self.show_notes:
            return [f"[GM notes]\n{notes}"]
        return ["(The advisors have been consulted. Their notes will guide the next scene.)"]

    def _usage(self) -> list:
        rows = llm.totals(self.memory.dir / "usage.jsonl")
        return [f"{role}  {model}  {calls} calls  {p} in  {c} out"
                for role, model, calls, p, c in rows] or ["(no calls yet)"]

    def _player_turn(self, line: str) -> list:
        notes = _join(self._take_notes(), self._trigger_notes())
        engine = self._engine_context()
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
            self.memory.add("dm", r.narration)
            out.append(r.narration)
        args = parse_player_command(r.command) if r.command else None
        if args and self._players_turn():
            out += self._engine(args)
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
    s = Session(args.campaign, client, models, camp_dir=camp_dir, local_client=local,
                show_notes=args.show_gm_notes, budget=args.budget)
    print(f"Local DM: {models.dm} via {local.base_url}; advisor {models.advisor} via "
          f"{client.base_url}. /quit to stop.")
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
        except llm.LLMError as e:
            out = [f"(model unavailable: {e})"]
        for chunk in out:
            print(chunk + "\n")
    s.summarizer.join(timeout=60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
