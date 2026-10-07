"""cli.py: the GM's one-call commands for grid combat (run via scripts/tactics/combat.py).

    python3 scripts/tactics/combat.py -c <campaign> <command> [args] [--roll N] [--json]

Setup and flow
    start <map> --pc NAME@SQ [--pc ...] --monster "SRD NAME@SQ" [...] [--ally "SRD NAME@SQ"]
    status                         whose turn, positions, HP, spell slots left
    rest short|long [--token NAME] an hour or eight: Hit Dice, features, spell slots
                                    (`--for-me` to spend the party's Hit Dice for them)
    options <token>                numbered choices for a GM-controlled creature
    choose <token> <n>|auto        run option n, or let the engine pick (ai_difficulty
                                   easy|normal|deadly in state.md, or --difficulty)
    end-turn                       next creature in initiative
    end                            finish: write sheets, tracker, session log, XP

Encounter design (no combat running — these are for before the fight)
    budget --party auto            what this party can be handed, per difficulty
    rate --monsters "goblin x4"    what a monster list costs that party
    day --party auto               what a whole adventuring day holds (2014)
    day --plan "goblin x4|orc x2"  cost a day you have already designed

Actions (the current creature)
    move <token> <square>          e.g. move kairos D5   (preview <token> <square> checks first)
    attack <token> <target> [attack name]      no name: the best legal attack
    multiattack <token> <target> [--option N]  every attack of a Multiattack, as one action
    dash | disengage | dodge | stand <token>
    death-save <token>
    check <token> <skill|ability> [--dc N] [--adv|--dis] [--sense sight|hearing]
                                     a check, out of turn, with the conditions on it
                                     (--by <token>: the check is about that creature)
    undo-move                      take back the last move this turn
    cast <token> "<spell>" [target|square ...] [--level N]
                                   e.g. cast kairos "magic missile" frog-1 frog-2 frog-1
    use <token> "<action>" <square>   a monster's area action (breath weapon)
    help <token> <ally> <target> | hide <token> | escape <token>
    ready <token> attack|cast|move [what] [--target X] --trigger "text"
    trigger <token> [target]       the readied action happens now (a reaction)
    reactions <token> ask|auto|off Shield / Silvery Barbs: ask the player, always, never
    sight <token>                  who it sees, and with what cover (the display's cover shading)
    card <token> [--players]       state card: creatures and named landmarks, feet, cover, north
    fog hide|dim|off               display fog of war: squares no PC sees are dimmed; "hide"
                                   also leaves out the creatures there (the default)
    condition <token> add|remove <condition>     GM ruling (e.g. a rider the engine left to you)
                                    "exhaustion 3" sets the level; the line says what it does
    adjust <token> hp=N temp_hp=N ac=N           GM correction
    log [n]                        last n combat log lines
    reachable <token>              squares reachable walking and with Dash (for the display)
    targets <token>                every attack and target with hit chance (for the display)
    spells <token>                 known spells, how they target, and whether they can be cast
    receipts [--rolls N]           check every roll receipt against its hash chain
    invocations [N]                every command this campaign accepted, with the seed it
                                   rolled on and whether it committed, paused or was refused
                                   (combat/invocations.jsonl; N prints one in full plus the
                                   command line that re-runs it)
    preview-area <token> "<spell>" <square|target> [--level N]
                                   who a spell would catch, chance to fail, expected damage

Dice. Under roll_mode "players" a player's roll is asked for, never invented:
the command stops (exit code 2, nothing changed) and says what to roll.
Re-run the same command with --roll <natural result> (the die face, or the
dice total, without modifiers); repeat --roll for several rolls. --for-me lets
the engine roll this time. --react yes|no answers a reaction prompt (an
opportunity attack, Shield, Silvery Barbs); repeat it when several are asked.
A paused command replays the same engine dice when re-run (combat/pending.json).
Every accepted invocation is appended to combat/invocations.jsonl with the seed it
rolled on and what came of it, so `invocations` can say afterwards what ran.

Output is 1 to 4 plain lines for the GM; --json prints the full result.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import re
import sys

import dice                               # scripts/dice.py (on sys.path via tactics/__init__)
from paths import find_campaign, _is_campaign  # scripts/paths.py (on sys.path via tactics/__init__)

from . import (actions, ai, effects, encounter, engine, formations, journal, maps, policy,
                 receipts, rest, roller, scenes, sight, slots, spells, state, statecard, sync)
from .core import rules_for
from .grid import label, parse_square
from .roller import PendingRoll, Roller
from .state import Encounter

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]

_DISPLAY_CAMPAIGN = _SCRIPTS.parent / "display" / ".campaign"
# Read-only means: nothing is written, nothing is rolled, no pending command is
# cleared. `budget`, `day` and `rate` are design tools: a fight is built before
# there is an encounter to save, so they must work with nothing running and must
# not touch combat/pending.json. `day` earns its place here the same way: it
# costs a day the GM has already designed, and reaching the save path with no
# encounter loaded is exactly how it was found broken.
READ_ONLY = ("status", "options", "preview", "reachable", "approach", "targets", "log", "spells",
             "preview-area", "sight", "card", "budget", "day", "rate", "receipts", "formation",
             "scene", "here", "invocations", "propose")
# `invocations` is here for the same reason `receipts` is: it reads the journal
# this very section writes, so it must not write anything, must not roll, and
# must not consume a pending command. A diagnosis tool that mutated the campaign
# it is diagnosing would be a second bug.
# `formation` is here even though `formation save` writes a file: pending.json
# exists to replay the *same* engine dice after a decision, and nothing under
# `formation` rolls. Saving a formation is a deliberate act, and making it
# consume a pending roll would let an unrelated interrupted attack change what
# gets written. `formation save` is also the one member that needs a running
# fight, and says so itself rather than being a read-only command.
# `scene` and `here` are here for the same reason: neither rolls, and both are
# read-only with respect to the encounter, so an interrupted attack must not be
# able to change where `scene.json` says the party is standing.
# Flags that do not change what a command means: a re-run with them added is
# the same command, so it replays the same engine dice (see _pending).
OLD_FORM = "*"     # decision key for a --react given up front (opportunity attacks)
_ANSWER_FLAGS = {"--roll": 1, "--react": 1, "--for-me": 0, "--player-roll": 0, "--json": 0}


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
    # _is_campaign, not exists(). find_campaign's not-found sentinel is
    # campaign_dir(name), which does exist whenever a stale shell sits there --
    # so exists() passes on exactly the empty folder this has to reject.
    if not _is_campaign(d):
        raise Stop(f"Campaign folder not found: {d}")
    return d


def _roll_mode(camp_dir) -> str:
    try:
        text = (camp_dir / "state.md").read_text(encoding="utf-8")
    except OSError:
        return "players"
    m = re.search(r"roll_mode\W+(players|auto)\b", text)
    return m.group(1) if m else "players"


def _difficulty(camp_dir) -> str:
    """ai_difficulty: easy | normal | deadly in state.md (for choose <token> auto)."""
    try:
        text = (camp_dir / "state.md").read_text(encoding="utf-8")
    except OSError:
        return "normal"
    m = re.search(r"ai_difficulty\W+(easy|normal|deadly)\b", text)
    return m.group(1) if m else "normal"


def _roller(args) -> Roller:
    """The engine's dice for this command, from the canonical factory.

    `dice.new_rng` is the one policy (docs/milestones/08-roll-integrity.md): a
    seeded PRNG whose seed came from `secrets` and is kept on the object as
    `.seed_value`, so anything that rolls can be quoted and re-run. Building a
    bare `random.Random` here meant the CLI's stream was the one dice stream in
    the tree with no seed to quote, and the two policies could drift apart.

    Precedence is unchanged and is the GM's to set: an explicit `--seed N` is
    the seed, otherwise the one resolved for this invocation (a paused command's
    replayed seed, or a freshly resolved one). `new_rng(None)` draws the fresh
    secrets-backed seed, which is what the old unseeded `random.Random()` did
    too, from the OS pool.
    """
    seed = args.seed if args.seed is not None else getattr(args, "_seed", None)
    return Roller(rng=dice.new_rng(seed), supplied=list(args.roll or []),
                  supplied_source="player" if args.player_roll else "verbal",
                  for_me=bool(args.for_me))


# ─── paused commands ──────────────────────────────────────────────────────────
#
# A command that stops for a player's roll or decision changes nothing. When
# the GM re-runs it with the answer, the engine must roll the same dice it
# rolled before the pause (a monster's attack before Kairos decides on
# Shield), so the seed and the decisions asked so far are kept in
# combat/pending.json, keyed by the command line without the answer flags.

def _canonical(argv: list) -> list:
    out, skip = [], 0
    for a in argv:
        if skip:
            skip -= 1
            continue
        key = a.split("=", 1)[0]
        if key in _ANSWER_FLAGS:
            skip = _ANSWER_FLAGS[key] if "=" not in a else 0
            continue
        out.append(a)
    return out


def _pending_path(camp_dir) -> pathlib.Path:
    return pathlib.Path(camp_dir) / "combat" / "pending.json"


def _load_pending(camp_dir, canon: list) -> dict:
    try:
        data = json.loads(_pending_path(camp_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if data.get("cmd") == canon else {}


def _fresh_seed() -> int:
    """One new seed, from the canonical policy. The only place this file asks
    the OS for entropy, and named so a test can plan the next seed without
    reaching into the `random` module (which is how the tests used to force a
    face, and which put a second, unrecorded dice stream in the CLI)."""
    return dice.new_rng().seed_value


def _resolve_seed(pending: dict) -> int:
    """The seed this invocation's dice run on, as one integer. Always.

    A paused command exists so the re-run lands the same faces, which means the
    seed in combat/pending.json is read and never redrawn. `dict.get(k, default)`
    cannot do that: the default argument is evaluated before the lookup, so
    `pending.get("seed", random.randrange(1 << 30))` drew a replacement seed off
    the global `random` generator on every replay and then threw it away,
    because the key was there. That was a second dice stream, unseeded by
    policy and described by no file, advancing on every paused command.

    A record with no usable seed is resolved rather than trusted: the key may be
    absent, null (a hand-edited or foreign pending.json), or a non-integer, and
    `random.Random(None)` on the other side of `_roller` would silently give up
    replay and produce an unseeded roll that no later re-run could match --
    including the next one, which would write the same null back out. So the
    canonical policy supplies one secrets-backed seed, and the next
    `_save_pending` writes that integer, making the replay reproducible from
    there on. Precedence is untouched: an explicit `--seed` still wins over
    both branches (see `_roller`).
    """
    seed = pending.get("seed")
    if isinstance(seed, int) and not isinstance(seed, bool):
        return seed
    return _fresh_seed()


def _save_pending(camp_dir, canon: list, seed, decisions: list) -> None:
    path = _pending_path(camp_dir)
    if not path.parent.exists():
        return
    path.write_text(json.dumps({"cmd": canon, "seed": seed, "decisions": decisions}),
                    encoding="utf-8")


def _clear_pending(camp_dir) -> None:
    try:
        _pending_path(camp_dir).unlink()
    except OSError:
        pass


# ─── the invocation journal ───────────────────────────────────────────────────
#
# One line per accepted invocation, appended (journal.py). The three things the
# engine already knows and nothing wrote down: the canonical argv it keys a
# paused command on, the seed it resolved, and what came of the run. See
# journal.py for the record shape and for why a pause is a record rather than a
# missing one.

def _encounter_fingerprint(camp_dir, args):
    """The fight as it stands on disk, for a journal record.

    Read from the file rather than carried out of `run()` on purpose: what a
    diagnosis needs is what the campaign actually held, and reading it here
    keeps `run()` -- the file three other backlog lanes also edit -- untouched.

    Called once before the command and once after it, so for anything that did
    not execute the two hashes are equal by measurement rather than by
    assertion: that equality is the evidence a refusal or a pause changed
    nothing.

    Both are null for a read-only command. It cannot have changed the encounter,
    so fingerprinting it would put the same hash in `before` and `after` of every
    `status` and imply a transition that did not happen.
    """
    if args.cmd in READ_ONLY:
        return {"before": None, "after": None, "map": None}
    try:
        enc = state.load(state.encounter_path(camp_dir))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # No fight running, or one this engine cannot read. A refusal before the
        # encounter was touched lands here, and "no state" is the truth.
        return {"before": None, "after": None, "map": None}
    return {"hash": receipts.state_hash(enc),
            "map": ((enc.meta or {}).get("name") or None)}


def _journal(camp_dir, args, argv, canon, outcome, code, reason="", pending=None,
             before=None):
    """Journal one invocation and return the record it wrote, or None.

    Everything the record needs that `main()` has to work out, worked out once
    here: where the seed came from, which paused invocation this one continues,
    and what the encounter looked like before and after. The five exit paths in
    `main()` therefore cannot disagree about the shape.

    `pending` is the record `_load_pending` matched, or {} for none. It is what
    decides resume-ness, not a later scan for a matching command line: pending
    is written by a pause and deleted only by a commit, so at most one pause is
    open per canonical command and it is the newest one. Anything else makes
    `attack kairos frog-1` ambiguous after the first repeat.

    Matching pending is necessary but not sufficient to carry the link:
    journal.RESUMES is the real condition, because only `committed` and `paused`
    consume that pending record. A refusal leaves it on disk, so the pause it was
    aimed at is still open and still answerable, and linking them would report an
    attempt as finished that never ran.

    Ordering: called after the state it describes is already durable, in every
    path. A journal that claimed a command ran before it had would be worse than
    one that missed it.
    """
    was_paused = bool(pending)
    if args.cmd in READ_ONLY:
        # A read never reaches `_roller`, so no dice were built at all and there
        # is no seed to quote. `args._seed` was resolved for it (the pending path
        # runs before the READ_ONLY check, for every command) but nothing was
        # ever built from it, and a number here would point at a roll that could
        # not have happened.
        seed, seed_from = None, "none"
    else:
        seed_from = "flag" if args.seed is not None else ("pending" if was_paused
                                                          else "fresh")
        # The seed that went on the dice, not the one merely resolved. `_roller`
        # builds from `--seed` when there is one, so `args._seed` on such a run is
        # a fresh integer that was never used for anything, and recording it
        # would put a number in the journal that reproduces nothing.
        seed = args.seed if args.seed is not None else args._seed
    resumes = None
    if was_paused and outcome in journal.RESUMES:
        # journal.chains, the reader's own grouping, decides what is open, so the
        # writer and the reader cannot disagree about what a resume means.
        still_open = journal.open_pauses(journal.read(camp_dir)[0])
        target = next((r for r in reversed(still_open) if r.get("cmd") == canon), None)
        resumes = target.get("seq") if isinstance(target, dict) else None
    # Re-read the file, whatever the outcome: for a pause or a refusal the two
    # hashes come back equal, which is how a reader sees at a glance that
    # nothing moved rather than being told so.
    after = _encounter_fingerprint(camp_dir, args) if outcome != "read" else None
    encounter = {"before": (before or {}).get("hash"),
                 "after": (after or {}).get("hash"),
                 "map": (after or before or {}).get("map")}
    return journal.record(camp_dir, argv, canon, seed, seed_from, outcome,
                          code, reason, encounter, resumes)


def _load(camp_dir) -> Encounter:
    path = state.encounter_path(camp_dir)
    if not path.exists():
        raise Stop("No grid combat is running. Start one: start <map> --pc NAME@SQ --monster NAME@SQ")
    return state.load(path)


def _combat_running(camp_dir) -> bool:
    """Is there a live fight for a command that can do without one?

    `end` leaves encounter.json on disk with status 'ended', and 'no file' is
    the ordinary state of a campaign between fights, so "the file exists" is not
    the test and neither is "it loads". A rest has to answer for both, because a
    party rests between fights and `rest` refusing there told the GM to rest
    between fights.
    """
    path = state.encounter_path(camp_dir)
    return path.exists() and state.load(path).status == "active"


def _reactions(enc, args) -> dict:
    """{decision key: bool}. Answers map, in order, onto the decisions this
    command has asked so far (pending.json). With nothing pending, the first
    answer covers any player creature's opportunity attack (the old form)."""
    answers = [a == "yes" for a in (args.react or [])]
    if not answers:
        return {}
    keys = getattr(args, "_decisions", []) or [OLD_FORM]
    out = {}
    for key, answer in zip(keys, answers):
        if key == OLD_FORM:                  # an answer given before any question was asked
            out.update({t.id: answer for t in enc.tokens.values()
                        if t.controller == "player" and t.id not in out})
        else:
            out[key] = answer
    return out


def _next_hint(enc) -> str:
    if enc.status != "active":
        return ""
    ready = actions.readied_lines(enc)
    hint = _turn_hint(enc)
    return "\n".join(ready + [hint]) if hint else "\n".join(ready)


def _turn_hint(enc) -> str:
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


def _auto_placements(camp_dir, enc) -> list:
    """[(sheet path, square)] for every PC sheet, on free ground near the party's edge.

    A fight the DM just narrated has no token positions yet. Rather than refusing
    (B1: "Add at least one --pc NAME@SQUARE" dead-ended the standard test scenario
    and left the model to improvise a whole ruleset), place each sheet on the
    lowest free passable square of the leftmost column, walking right as sheets
    pile up. The GM can move anyone afterwards with `move`.
    """
    folder = pathlib.Path(camp_dir) / "characters"
    sheets = sorted(p for p in folder.glob("*.md") if p.is_file()) if folder.is_dir() else []
    if not sheets:
        return []
    try:
        grid = enc.board()
    except (KeyError, ValueError):
        return []
    taken = set()
    out = []
    for sheet in sheets:
        spot = next((pos for x in range(grid.width)
                     for pos in ((x, y) for y in range(grid.height))
                     if grid.passable(pos) and pos not in taken), None)
        if spot is None:                      # a map this small has no room left
            break
        taken.add(spot)
        out.append((sheet, label(spot)))
    return out


# ─── commands ─────────────────────────────────────────────────────────────────

def cmd_start(args, camp_dir, roller=None):
    path = state.encounter_path(camp_dir)
    if path.exists() and not args.force and state.load(path).status == "active":
        raise Stop("A grid combat is already running. Run end first, or start --force.")
    try:
        m = maps.load(args.map)
    except (FileNotFoundError, ValueError) as e:
        raise Stop(str(e)) from None
    enc = Encounter(campaign=_campaign(args), grid=m["grid"], meta=m["meta"],
                    roll_mode=args.roll_mode or _roll_mode(camp_dir))
    # The fight exists from here, so it can stamp where its receipts go and
    # fingerprint itself before the initiative dice are rolled (see run()).
    enc.campaign_dir = camp_dir
    if roller is not None:
        roller.state_fn = lambda: receipts.state_hash(enc)
    R = engine.rules_for(enc)
    noted = ""
    pc_specs = list(args.pc or [])
    placed_by_hand = bool(pc_specs)
    if not pc_specs:
        # B1: a fight narrated into being ("I head out to the spar") has no square
        # yet, and refusing to start leaves the player with no way forward. Every
        # sheet in the campaign joins, placed on free ground by the board itself.
        pc_specs = [f"{p.stem}@{sq}" for p, sq in _auto_placements(camp_dir, enc)]
        if not pc_specs:
            raise Stop("Add at least one --pc NAME@SQUARE, or add a sheet to "
                       f"{camp_dir / 'characters'} for it to place.")
    for spec in pc_specs:
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
    noted += _place_formations(args, enc, camp_dir, R, m)
    if not any(t.side == "pc" for t in enc.tokens.values()):
        raise Stop("Add at least one --pc NAME@SQUARE, or add a sheet to "
                   f"{camp_dir / 'characters'} for it to place.")
    state.prepare_all(enc)          # derived stats, before validate() and initiative
    problems = state.validate(enc)
    if problems:
        raise Stop("Cannot start: " + "; ".join(problems))
    res = engine.begin(enc, roller or _roller(args))
    sync.set_active_combat(camp_dir, f"Grid combat in progress on {m['meta']['name']}: read "
                                     "`scripts/tactics.md`, then run `combat.py status`. "
                                     "`combat/encounter.json` holds HP, positions and turn order.")
    opened = "" if placed_by_hand else f" Placed {', '.join(_slug(n) for n in _pc_names(enc))}."
    return enc, f"Grid combat on {m['meta']['name']}.{opened}{noted} {res['text']}"


def _place_formations(args, enc, camp_dir, R, m) -> str:
    """Replay `--formation`s onto this map, and say what the replay had to do.

    A formation is placed *after* the hand-placed `--monster`/`--ally` tokens, so
    a GM who types a monster explicitly gets the square they asked for, and the
    formation fills in the rest.

    Every effect the replay had is returned as text rather than logged quietly,
    because all three of them are rules-relevant and the GM should not have to
    diff two files to find out: a clamped square puts two monsters adjacent that
    were two squares apart, a blocked square is a monster inside a wall, and an
    off-map anchor means the formation does not fit where it was pinned.
    """
    notes = []
    for name in (getattr(args, "formation", None) or []):
        try:
            spec = formations.load(camp_dir, name)
        except (FileNotFoundError, ValueError) as e:
            raise Stop(str(e)) from None
        at = None
        if getattr(args, "at", None):
            try:
                at = parse_square(args.at)
            except ValueError as e:
                raise Stop(str(e)) from None
        try:
            plan = formations.positions(spec, enc.board(), at=at,
                                        centre=bool(getattr(args, "centre", False)))
        except ValueError as e:
            raise Stop(str(e)) from None
        totals: dict[str, int] = {}
        for p in spec["members"]:
            key = p["name"].lower()
            totals[key] = totals.get(key, 0) + 1
        seen: dict[str, int] = {}
        for p in plan["placements"]:
            key = p["name"].lower()
            seen[key] = seen.get(key, 0) + 1
            base, n = _slug(p["name"].split()[-1]), seen[key]
            tid = f"{base}-{n}"
            while tid in enc.tokens:
                n += 1
                tid = f"{base}-{n}"
            display = p["name"].title() + (f" {seen[key]}" if totals[key] > 1 else "")
            try:
                t = R.token_from_monster(p["name"], tid, display, (p["x"], p["y"]))
            except ValueError as e:
                # A formation that names a creature this system does not have is
                # the one error worth stopping for: replaying it would silently
                # drop a monster the GM expects to be there.
                raise Stop(f"formation {spec['name']!r}: {e}") from None
            t.side = p["side"]
            enc.tokens[t.id] = t
        where = f" at {label(plan['anchor'])}" if plan["mode"] == "at" else " centred"
        notes.append(f" Formation {spec['name']!r}{where}: "
                     f"{len(plan['placements'])} token(s).")
        for p in plan["blocked"]:
            notes.append(f" {p['name']} is on {label((p['x'], p['y']))}, which "
                         f"{m['meta']['name']} does not let a creature stand on.")
        for p in plan["off_map"]:
            notes.append(f" {p['name']} would have been at {label((p['was'][0], p['was'][1]))}, "
                         f"off this {enc.board().width}x{enc.board().height} map; moved to "
                         f"{label((p['x'], p['y']))}.")
        for p in plan["separated"]:
            notes.append(f" {p['name']} and another monster both wanted "
                         f"{label((p['was'][0], p['was'][1]))}; moved the second to "
                         f"{label((p['x'], p['y']))}.")
    return "".join(notes)


def cmd_formation(args, camp_dir, enc=None) -> tuple:
    """`formation save|list|show|place`. Returns (text, data) for --json."""
    data: dict = {}
    action = args.formation_action
    if action == "list":
        names = formations.available(camp_dir)
        data = {"formations": names}
        if not names:
            return ("No formations saved yet. Set a fight up, then run "
                    "`combat.py formation save NAME`.", data)
        rows = []
        for n in names:
            spec = formations.load(camp_dir, n)
            rows.append(f"  {n}: {len(spec['members'])} token(s), captured "
                        f"{spec.get('captured') or '?'} on "
                        f"{spec.get('from_map') or 'an unnamed map'}")
        data["detail"] = {n: formations.load(camp_dir, n) for n in names}
        return "Formations in this campaign:\n" + "\n".join(rows), data

    if action == "show":
        spec = formations.load(camp_dir, args.name)
        data = {"formation": spec}
        rows = [f"{m.get('label') or m['name']} ({m['side']}) at {m['dx']:+d},{m['dy']:+d} from the anchor "
                f"— {m['nx']:.3f}, {m['ny']:.3f} of the map"
                for m in spec["members"]]
        head = (f"Formation {spec['name']!r}"
                + (f", captured on {spec['from_map']}" if spec.get("from_map") else "")
                + (f" ({spec['from_size']['width']}x{spec['from_size']['height']})"
                   if spec.get("from_size") else ""))
        if spec.get("info"):
            head += f"\n{spec['info']}"
        return head + "\n  anchor " + label(spec["anchor"]) + "\n" + "\n".join(
            "  " + r for r in rows), data

    if action == "save":
        if enc is None:
            raise Stop("No fight is running, so there is no arrangement to save. "
                       "`formation save` reads the board as it stands.")
        spec = formations.capture(enc, args.name, include_pcs=args.include_pcs,
                                  colour_from=enc.meta.get("spawns") or (),
                                  info=args.info or "")
        try:
            path = formations.save(camp_dir, spec)
        except ValueError as e:
            raise Stop(str(e)) from None
        data = {"formation": spec, "path": str(path)}
        return (f"Saved formation {spec['name']!r}: {len(spec['members'])} token(s) "
                f"to {path}. Play it with `combat.py start MAP --formation "
                f"{formations.slug(spec['name'])} --at SQUARE`, or --centre to drop it "
                f"in the middle of another map.", data)

    # place: show where a formation would land, without starting a fight
    if enc is None:
        spec = formations.load(camp_dir, args.name)
        try:
            m = maps.load(args.map)
        except (FileNotFoundError, ValueError) as e:
            raise Stop(str(e)) from None
        enc = Encounter(campaign="preview", grid=m["grid"], meta=m["meta"])
        at = None
        if args.at:
            try:
                at = parse_square(args.at)
            except ValueError as e:
                raise Stop(str(e)) from None
        try:
            plan = formations.positions(spec, enc.board(), at=at, centre=args.centre)
        except ValueError as e:
            raise Stop(str(e)) from None
        data = {"plan": plan, "map": args.map}
        rows = [f"  {p['name']} ({p['side']}) at {label((p['x'], p['y']))}"
                + ("  <- moved, the square was taken" if p["moved"] else "")
                for p in plan["placements"]]
        head = (f"{spec['name']!r} on {m['meta']['name']}"
                + (f", anchored at {args.at}" if args.at else ", centred"))
        warn = []
        for p in plan["blocked"]:
            warn.append(f"  {p['name']} is on {label((p['x'], p['y']))}, which "
                        f"{m['meta']['name']} does not let a creature stand on")
        for p in plan["off_map"]:
            warn.append(f"  {p['name']} wanted {label((p['was'][0], p['was'][1]))}, "
                        f"off this map; moved to {label((p['x'], p['y']))}")
        for p in plan["separated"]:
            warn.append(f"  {p['name']} wanted {label((p['was'][0], p['was'][1]))}, "
                        f"which another monster had; moved to {label((p['x'], p['y']))}")
        return "\n".join([head, *rows, *([""] + warn if warn else [])]), data


# ─── the scene ────────────────────────────────────────────────────────────────

def _no_scene(camp_dir) -> str:
    """The sentence for a campaign with no `scene.json`. One, used everywhere."""
    where = scenes.cd_path(camp_dir, "NAME").parent
    return ("No scene yet. Run `combat.py scene MAP`, where MAP is the name of a "
            f"`.cd` in {where}.")


def cmd_scene(args, camp_dir) -> tuple:
    """`scene <name>`. Make a Chartdown map this campaign's persistent scene."""
    if args.show:
        spec = scenes.load(camp_dir)
        if spec is None:
            return _no_scene(camp_dir), {}
        data = {"scene": spec}
        marker = spec["marker"]
        return (f"Scene {spec['map']!r} ({spec['name'] or 'unnamed'}), background "
                f"{spec['background']}, extent {spec['extent'][0]:g}x{spec['extent'][1]:g}. "
                f"Marker {marker['name']!r} at {marker['x']:g}, {marker['y']:g} of the "
                f"background"
                + (f", on {marker['place']}" if marker.get("place") else "")
                + ("" if marker.get("revealed") is True else ", not on the players' board"),
                data)

    try:
        spec = scenes.blank(camp_dir, args.name, background=args.background)
        problems = scenes.validate(spec)
        if problems:
            raise scenes.SceneError("; ".join(problems))
        path = scenes.save(camp_dir, spec)
    except scenes.SceneError as e:
        raise Stop(str(e)) from None
    data = {"scene": spec, "path": str(path)}
    return (f"Scene set to {spec['name'] or spec['map']!r} ({spec['map']}): background "
            f"{spec['background']}, extent {spec['extent'][0]:g}x{spec['extent'][1]:g}. "
            f"Put the party somewhere with `combat.py here PLACE` "
            f"(`combat.py scene --show` reads it back).", data)


def cmd_here(args, camp_dir) -> tuple:
    """`here <place>`. Snap the party marker onto a named place in the `.cd`."""
    spec = scenes.load(camp_dir)
    if spec is None:
        raise Stop(_no_scene(camp_dir))
    try:
        place = scenes.find_place(scenes.cd_path(camp_dir, spec["map"]), args.place)
        moved = scenes.snap(spec, place, name=args.name, revealed=args.reveal)
        path = scenes.save(camp_dir, moved)
    except scenes.SceneError as e:
        raise Stop(str(e)) from None
    marker = moved["marker"]
    where = f" ({place['label']})" if place.get("label") else ""
    data = {"scene": moved, "place": place, "path": str(path)}
    return (f"The party is at {args.place}{where}, {marker['x']:g}, {marker['y']:g} of "
            f"{spec['background']}. Stored in {path}."
            + ("" if marker.get("revealed") is True
               else " Not on the players' board."), data)


def _pc_names(enc) -> list:
    return [t.name for t in enc.tokens.values() if t.side == "pc"]


def _advantage(args) -> str:
    """The GM's own ruling for this roll, if they made one."""
    return getattr(args, "advantage", None) or "normal"


def _exhaustion_label(token) -> str:
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
        level = _exhaustion_label(x) if x.has("exhaustion") else ""
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
        raise Stop(f"{t.name} is player-controlled: wait for the player's action.")
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


def _split_name(enc, token, words: list, names: list) -> tuple:
    """("spell or action name", [targets]) from loose words: the longest prefix
    that names one of `names`, so both `cast kairos "magic missile" frog-1` and
    `cast kairos magic missile frog-1` work."""
    low = [n.lower() for n in names]
    for i in range(len(words), 0, -1):
        cand = " ".join(words[:i]).lower()
        if cand in low or any(n.startswith(cand) for n in low) and i == 1:
            return " ".join(words[:i]), words[i:]
    return (words[0], words[1:]) if words else ("", [])


def _cast(enc, roller, args) -> tuple:
    c = engine._resolve(enc, args.token)
    name, targets = _split_name(enc, c, args.words, engine.rules_for(enc).known_spells(c))
    if not name:
        raise Stop("Name the spell: cast <token> \"<spell>\" [targets].")
    data = spells.cast(enc, roller, c, name, targets, args.level, _reactions(enc, args))
    return data["text"], data


def _use(enc, roller, args) -> tuple:
    t = engine._resolve(enc, args.token)
    if len(args.words) < 2:
        raise Stop("use <token> \"<action>\" <square|target>")
    name, rest = " ".join(args.words[:-1]), args.words[-1]
    data = spells.use_action(enc, roller, t, name, rest, _reactions(enc, args))
    return data["text"], data


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


def _end(camp_dir, enc, campaign: str = "", award: bool = True) -> str:
    if enc.status != "active":
        raise Stop("This combat has already ended.")
    enc.status = "ended"
    rules = engine.rules_for(enc)
    written = sync.write_sheets(camp_dir, enc, rules)
    lines = sync.summary_lines(enc, enc.meta)
    logged = sync.append_session_log(camp_dir, lines)
    # Combat borrows the map slot for the length of a fight and gives it back.
    # Writing the literal `*(none)*` here used to erase the current map instead,
    # which is why "the campus is the background of the story" had nothing to
    # survive in. `scenes.ended_body` restores the scene and is byte-identical
    # to the old line for a campaign that has never opened one.
    sync.set_active_combat(camp_dir, scenes.ended_body(camp_dir))
    engine._log(enc, "end", "", f"Combat ended after round {enc.round}.")
    out = [f"Combat ended after round {enc.round}. " + lines[2].lstrip("- ")]
    for name, path, diff in written:
        if path is None:
            out.append(f"{name}: no sheet in characters/, nothing written.")
        elif diff:
            out.append(f"{name}'s sheet updated (backup {path.name}.bak):\n{diff}")
        else:
            out.append(f"{name}'s sheet already up to date.")
    if award:
        # The fight that ran is rated by the tables the GM designed it against,
        # so the award a player gets is the one the encounter was worth. Recorded
        # in the same ledger `xp.py award` writes, so `xp.py check` reconciles
        # both against the same sheets.
        #
        # Contained, because this is the last command of a session: an XP award
        # the GM can re-run by hand is worth less than a fight that ends and
        # prints everything else.
        try:
            out += encounter.award_xp(camp_dir, rules, enc, campaign)
        except Exception as exc:                                  # noqa: BLE001
            out.append(f"XP: not awarded ({exc}). Award it by hand with: "
                       f"python3 systems/dnd5e/xp.py award --campaign {campaign} "
                       f"--characters \"NAME,...\" --difficulty easy|medium|hard|deadly")
    if logged:
        out.append("Session log: combat summary appended.")
    return "\n".join(out)


def run(args) -> int:
    camp_dir = _camp_dir(args)
    if args.cmd == "invocations":
        # Reads the journal and writes nothing else (see READ_ONLY). Before the
        # branch below deliberately: `invocations` needs no encounter, and asking
        # "what did I run" has to work with no fight running, which is exactly
        # when it is asked.
        #
        # The reader hands back lines rather than printing them, so `--json` can
        # carry the text as `text` and the records as `result` without the text
        # being printed twice -- the shape every other `--json` command here uses,
        # and the one the display and the localdm bridge parse.
        code, lines = journal.render(camp_dir, getattr(args, "index", None))
        if args.json:
            found, unreadable = journal.read(camp_dir)
            print(json.dumps({"text": "\n".join(lines), "result": {
                "invocations": found,
                "open_pauses": [r["seq"] for r in journal.open_pauses(found)],
                "unreadable": unreadable}}, default=str, indent=1))
        else:
            for line in lines:
                print(line)
        return code
    roller = _roller(args)
    data = {}
    enc = None
    if args.cmd == "start":
        enc, text = cmd_start(args, camp_dir, roller)
    elif args.cmd == "receipts":
        return receipts.main(["--dir", str(camp_dir)] + (["--rolls", "6"] if args.rolls else []))
    elif args.cmd in ("budget", "day", "rate"):
        if args.cmd == "budget":
            text, data = encounter.cmd_budget(args, camp_dir, _campaign(args))
        elif args.cmd == "day":
            text, data = encounter.cmd_day(args, camp_dir, _campaign(args))
        else:
            text, data = encounter.cmd_rate(args, camp_dir, _campaign(args))
    elif args.cmd == "propose":
        text, data = encounter.cmd_propose(args, camp_dir, _campaign(args))
        if args.json:
            print(json.dumps({"text": text, "result": data}, default=str, indent=1))
        else:
            print(text)
        return 0
    elif args.cmd == "accept":
        text, data = encounter.cmd_accept(args, camp_dir, _campaign(args))
        if args.json:
            print(json.dumps({"text": text, "result": data}, default=str, indent=1))
        else:
            print(text)
        return 0
    elif args.cmd == "formation":
        # `save` reads the board as it stands, so it needs the running encounter;
        # the other three must work with nothing running, which is exactly when
        # you want to ask what formations exist.
        live = None
        if args.formation_action == "save":
            live = _load(camp_dir)
        text, data = cmd_formation(args, camp_dir, live)
        if args.json:
            print(json.dumps(data, indent=1))
        else:
            print(text)
        return 0
    elif args.cmd in ("scene", "here"):
        # Both read and write campaign files and neither is combat state, so
        # they go through the same branch as `formation` rather than through
        # `_load(camp_dir)`: asking where the party is must work with no fight
        # running, which is the only time anybody asks.
        text, data = (cmd_scene if args.cmd == "scene" else cmd_here)(args, camp_dir)
        if args.json:
            print(json.dumps(data, indent=1))
        else:
            print(text)
        return 0
    elif args.cmd == "rest" and not _combat_running(camp_dir):
        # A rest between fights. There is no encounter to hold the party, so the
        # rest builds one from the campaign's own character sheets and commits
        # the sheets, tracker and clock together (rest.cmd_rest_campaign). Same
        # branch shape as `formation`/`scene`: this is campaign state, not
        # combat state, and it has to work in exactly the situation where no
        # fight is running.
        text, data = rest.cmd_rest_campaign(args, camp_dir, _campaign(args), roller)
        if args.json:
            print(json.dumps({"text": text, "result": data}, default=str, indent=1))
        else:
            print(text)
        return 0
    else:
        enc = _load(camp_dir)
        # Two things the engine cannot know by itself: where this fight's
        # receipts belong (core.log hands them to receipts.py), and what the
        # fight looked like before each roll (Roller.state_fn).
        enc.campaign_dir = camp_dir
        roller.state_fn = lambda: receipts.state_hash(enc)
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
        elif cmd == "approach":
            data = engine.approach(enc, args.token, args.target)
            text = data["text"]
        elif cmd == "targets":
            data = {"targets": engine.attack_options(enc, args.token)}
            legal = [t for t in data["targets"] if t["legal"]]
            text = "; ".join(f"{t['attack']} -> {t['target_name']} {t['hit_percent']}%"
                             for t in legal) or "No target in range."
        elif cmd == "log":
            text = "\n".join(e["text"] for e in enc.log[-args.n:]) or "(empty log)"
        elif cmd == "spells":
            rows = spells.castable(enc, args.token)
            data = {"spells": rows}
            text = "; ".join(f"{r['name']}" + ("" if r["ok"] else f" (no: {r['reason'].rstrip('.')})")
                             for r in rows) or "No spells."
        elif cmd == "preview-area":
            c = engine._resolve(enc, args.token)
            name, targets = _split_name(enc, c, args.words, engine.rules_for(enc).known_spells(c))
            data = spells.preview(enc, c, name, targets[0] if len(targets) == 1 else targets, args.level)
            text = data["text"]
        elif cmd == "cast":
            text, data = _cast(enc, roller, args)
        elif cmd == "use":
            text, data = _use(enc, roller, args)
        elif cmd == "help":
            text = actions.help_action(enc, args.token, args.ally, args.target)["text"]
        elif cmd == "hide":
            data = actions.hide(enc, roller, args.token)
            text = data["text"]
        elif cmd == "escape":
            text = actions.escape(enc, roller, args.token)["text"]
        elif cmd == "ready":
            data = actions.ready(enc, roller, args.token, args.kind, " ".join(args.what) or None,
                                 args.target, args.trigger or "", args.level)
            text = data["text"]
        elif cmd == "trigger":
            text = actions.trigger(enc, roller, args.token, args.target,
                                   _reactions(enc, args))["text"]
        elif cmd == "sight":
            data = sight.sight(enc, args.token, players=args.players)
            text = data["text"]
        elif cmd == "card":
            data = statecard.statecard(enc, args.token, players=args.players)
            text = data["text"]
        elif cmd == "fog":
            enc.meta["fog"] = args.mode
            text = f"Fog of war: {args.mode}." + {
                "hide": " The display dims what no PC sees and hides the creatures there.",
                "dim": " The display dims what no PC sees; every creature stays shown.",
                "off": " The display shows the whole map."}[args.mode]
        elif cmd == "reactions":
            t = engine._resolve(enc, args.token)
            t.reactions = args.mode
            text = f"{t.name}: spell reactions {args.mode}."
        elif cmd == "choose":
            t = engine._resolve(enc, args.token)
            if t.controller == "player":
                raise Stop(f"{t.name} is player-controlled: use move/attack for the player's choice.")
            if args.n == "auto":
                data = policy.choose_auto(enc, roller, t, args.difficulty or _difficulty(camp_dir),
                                          _reactions(enc, args))
                text = f"{data['option']['label']} [{data['profile']}]. {data['text']}"
            elif args.n.isdigit():
                data = ai.choose(enc, roller, t, int(args.n), _reactions(enc, args))
                text = f"{data['option']['n']}. {data['text']}"
            else:
                raise Stop(f"choose {t.id} <n>: an option number, or auto.")
        elif cmd == "move":
            data = engine.move(enc, roller, args.token, args.square, _reactions(enc, args))
            text = data["text"]
        elif cmd == "attack":
            data = engine.attack(enc, roller, args.token, args.target, args.attack,
                                 _reactions(enc, args), _advantage(args))
            text = data["text"]
        elif cmd == "check":
            data = engine.check(enc, roller, args.token, args.name, args.dc or 0,
                                _advantage(args), args.sense or "", args.by, args.source)
            text = data["text"]
        elif cmd == "multiattack":
            data = engine.multiattack(enc, roller, args.token, args.target, args.option,
                                      _reactions(enc, args))
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
        elif cmd == "rest":
            # The campaign name, so a long rest taken in a fight stamps the same
            # end hour in tracker.json as one taken between fights: the rest is
            # the same event whether or not an encounter happens to be loaded.
            text, data = rest.cmd_rest(args, enc, roller, _campaign(args))
            moved = rest.advance_calendar(_campaign(args), args.type)
            if moved:
                text += "\n" + moved
        elif cmd == "condition" and args.action == "remove" and args.condition.lower() == "concentration":
            t = engine._resolve(enc, args.token)
            if not t.concentration:
                raise Stop(f"{t.name} is not concentrating.")
            text = effects.end_concentration(enc, t, "GM ruling")
            engine._log(enc, "condition", t.id, f"GM: {text}")
        elif cmd == "condition" and args.action == "remove" and any(
                args.condition.lower() in e.get("conditions", [])
                for e in engine._resolve(enc, args.token).effects):
            t = engine._resolve(enc, args.token)
            names = effects.remove_granting(t, args.condition.lower())
            t.remove_condition(args.condition)
            text = f"{t.name}: {args.condition.lower()} removed (ends {', '.join(names)})."
            engine._log(enc, "condition", t.id, f"GM: {text}")
        elif cmd == "condition":
            t = engine._resolve(enc, args.token)
            R = rules_for(enc)
            name = args.condition.lower()
            if args.level is not None:
                name = f"exhaustion {args.level}"
            # The statblock's condition immunities bind a GM-typed add too, not
            # only rider saves: the engine owns the rule, the GM cannot forget it.
            base = name.split()[0]
            immune = {c.lower() for c in t.condition_immunities}
            if args.action == "add" and base in immune:
                text = f"{t.name} is immune to being {base}; nothing added."
            else:
                if args.action == "add":
                    more = R.set_condition(t, name)
                else:
                    more = R.clear_condition(t, name)
                said = name
                if t.has("exhaustion") and _exhaustion_label(t):
                    said = f"exhaustion {_exhaustion_label(t)}"
                text = f"{t.name}: {said} {'added' if args.action == 'add' else 'removed'}."
                if args.action == "add":
                    # What the condition is doing to this creature, so nobody has to
                    # remember which of fourteen it is.
                    text += " " + " ".join(more + R.condition_notes(t))
                    text += " " + " ".join(effects.check_incapacitated(enc, t))
                else:
                    text += (" " + " ".join(more)) if more else ""
            engine._log(enc, "condition", t.id, f"GM: {text}")
        elif cmd == "adjust":
            text = _adjust(enc, args)
        else:                                               # end
            text = _end(camp_dir, enc, _campaign(args), award=not args.no_xp)

    if args.cmd not in READ_ONLY:
        if roller.supplied:
            text += f"\n(Unused --roll values ignored: {roller.supplied})"
        state.save(enc, state.encounter_path(camp_dir))
        sync.sync_tracker(camp_dir, enc, drop_monsters=enc.status == "ended")
        sync.push_display(enc, enc.meta)
        if args.cmd == "choose" and enc.status == "active":
            text += "\nThen: end-turn"
        elif args.cmd in ("start", "end-turn", "death-save", "trigger"):
            hint = _next_hint(enc)
            if hint:
                text += "\n" + hint
    if args.json:
        payload = {"text": text, "result": data}
        if enc is not None:
            payload["combat"] = sync.snapshot(enc, enc.meta)
        print(json.dumps(payload, default=str, indent=1))
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
    p.add_argument("--react", choices=["yes", "no"], action="append", default=none,
                   help="answer a reaction prompt (repeat for several, in the order asked)")
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
    s.add_argument("--formation", action="append", metavar="NAME",
                   help="replay a saved monster arrangement (repeatable). By "
                        "default its anchor goes wherever --at says, or to the "
                        "middle of the map with --centre")
    s.add_argument("--at", metavar="SQ", help="pin the formation's anchor to this square")
    s.add_argument("--centre", action="store_true",
                   help="drop the formation in the middle of the map, scaled to it. "
                        "This is how a formation crosses to a different-sized map")
    s.add_argument("--pc", action="append", metavar="NAME@SQ")
    s.add_argument("--monster", action="append", metavar="'SRD NAME@SQ'")
    s.add_argument("--ally", action="append", metavar="'SRD NAME@SQ'")
    s.add_argument("--roll-mode", choices=["players", "auto"])
    s.add_argument("--force", action="store_true", help="replace a running combat")
    sub.add_parser("status", parents=c, help="round, turn, positions, HP")
    s = sub.add_parser("options", parents=c, help="numbered choices for a creature")
    s.add_argument("token")
    s = sub.add_parser("choose", parents=c, help="run a numbered option, or auto")
    s.add_argument("token")
    s.add_argument("n", help="option number, or auto: the engine picks (no model)")
    s.add_argument("--difficulty", choices=list(policy.DIFFICULTY))
    for name in ("move", "preview"):
        s = sub.add_parser(name, parents=c)
        s.add_argument("token")
        s.add_argument("square", help="a square like D5, or a creature to walk toward")
    s = sub.add_parser("approach", parents=c, help="where a move toward a creature would end")
    s.add_argument("token")
    s.add_argument("target")
    s = sub.add_parser("attack", parents=c)
    s.add_argument("token")
    s.add_argument("target")
    s.add_argument("attack", nargs="*")
    s.add_argument("--adv", dest="advantage", action="store_const", const="advantage",
                   help="this attack has advantage, whatever the conditions say")
    s.add_argument("--dis", dest="advantage", action="store_const", const="disadvantage",
                   help="this attack has disadvantage, whatever the conditions say")
    s = sub.add_parser("check", parents=c,
                       help="a skill or ability check, out of turn, conditions applied")
    s.add_argument("token")
    s.add_argument("name", help="a skill (perception) or an ability (dex)")
    s.add_argument("--dc", type=int, help="the DC; without it the roll is reported, not judged")
    s.add_argument("--by", help="the creature the check is being made about (a charm turns on it)")
    s.add_argument("--source", help="what is frightening them, for the 'in sight' gate")
    s.add_argument("--sense", choices=["sight", "hearing", "hearing_or_sight"],
                   help="what the check depends on: blind and deaf turn on it")
    s.add_argument("--adv", dest="advantage", action="store_const", const="advantage")
    s.add_argument("--dis", dest="advantage", action="store_const", const="disadvantage")
    s = sub.add_parser("multiattack", parents=c, help="every attack of a Multiattack, one action")
    s.add_argument("token")
    s.add_argument("target")
    s.add_argument("--option", type=int, help="which Multiattack option (default: best)")
    for name in ("dash", "disengage", "dodge", "stand", "death-save", "reachable", "targets"):
        s = sub.add_parser(name, parents=c)
        s.add_argument("token")
    s = sub.add_parser("cast", parents=c, help="cast a spell")
    s.add_argument("token")
    s.add_argument("words", nargs="+", metavar="spell [target ...]")
    s.add_argument("--level", type=int, help="slot level (upcast)")
    s = sub.add_parser("preview-area", parents=c, help="who a spell would hit, without casting")
    s.add_argument("token")
    s.add_argument("words", nargs="+", metavar="spell target")
    s.add_argument("--level", type=int)
    s = sub.add_parser("spells", parents=c, help="known spells and whether they can be cast")
    s.add_argument("token")
    s = sub.add_parser("use", parents=c, help="a monster's area action (breath weapon)")
    s.add_argument("token")
    s.add_argument("words", nargs="+", metavar="action square")
    s = sub.add_parser("help", parents=c, help="Help: an ally's next attack on a target has advantage")
    s.add_argument("token")
    s.add_argument("ally")
    s.add_argument("target")
    for name in ("hide", "escape"):
        s = sub.add_parser(name, parents=c)
        s.add_argument("token")
    s = sub.add_parser("ready", parents=c, help="ready an attack, a spell or a move")
    s.add_argument("token")
    s.add_argument("kind", choices=["attack", "cast", "move"])
    s.add_argument("what", nargs="*", help="attack or spell name; the square for a move")
    s.add_argument("--target", help="who or where")
    s.add_argument("--trigger", help="the trigger, in words")
    s.add_argument("--level", type=int)
    s = sub.add_parser("trigger", parents=c, help="the readied action happens now")
    s.add_argument("token")
    s.add_argument("target", nargs="?")
    s = sub.add_parser("reactions", parents=c, help="spell reactions: ask, auto or off")
    s.add_argument("token")
    s.add_argument("mode", choices=["ask", "auto", "off"])
    s = sub.add_parser("sight", parents=c, help="who a creature sees, and with what cover")
    s.add_argument("token")
    s.add_argument("--players", action="store_true",
                   help="only what the players can see (the display passes this)")
    s = sub.add_parser("card", parents=c, help="state card: creatures and landmarks with feet and cover")
    s.add_argument("token")
    s.add_argument("--players", action="store_true",
                   help="only what the players can see (fog and hidden enemies filtered)")
    s = sub.add_parser("fog", parents=c, help="display fog of war: hide, dim or off")
    s.add_argument("mode", choices=list(sight.FOG_MODES))
    sub.add_parser("undo-move", parents=c)
    sub.add_parser("end-turn", parents=c)
    s = sub.add_parser("rest", parents=c, help="short or long rest for the party, or one creature")
    s.add_argument("type", choices=["short", "long"])
    s.add_argument("--token", metavar="NAME", help="one creature (default: every PC)")
    s = sub.add_parser("end", parents=c,
                       help="end combat, write sheets, the session log and the XP award")
    s.add_argument("--no-xp", action="store_true",
                   help="do not award XP (a fight fled from, or a campaign that "
                        "levelling handles elsewhere)")
    s = sub.add_parser("budget", parents=c,
                       help="the difficulty thresholds this party can be handed")
    s.add_argument("--party", default="auto", metavar="auto|NAMES",
                   help="'auto' (default) is every character sheet in the campaign")
    s.add_argument("--ruleset", choices=list(encounter.RULESETS),
                   help="defaults to the campaign's own system version")
    s = sub.add_parser("day", parents=c,
                       help="what a whole adventuring day costs this party (2014)")
    s.add_argument("--party", default="auto", metavar="auto|NAMES",
                   help="'auto' (default) is every character sheet in the campaign")
    s.add_argument("--ruleset", choices=list(encounter.RULESETS),
                   help="defaults to the campaign's own system version; the day "
                        "budget itself is 2014 only")
    s.add_argument("--plan", metavar="FIGHTS",
                   help="cost a day you have already designed: 'goblin x4 | orc x2'. "
                        "'|' separates fights, ',' separates monsters within one")
    s = sub.add_parser("rate", parents=c,
                       help="what a list of monsters costs this party")
    s.add_argument("--monsters", required=True, metavar="LIST",
                   help='"goblin x4, hobgoblin" — x4 or ×4, comma-separated')
    s.add_argument("--party", default="auto", metavar="auto|NAMES",
                   help="'auto' (default) is every character sheet in the campaign")
    s.add_argument("--ruleset", choices=list(encounter.RULESETS),
                   help="defaults to the campaign's own system version")
    s = sub.add_parser("propose", parents=c,
                       help="generate a bounded, seeded encounter proposal (read-only)")
    s.add_argument("--difficulty", choices=["easy", "medium", "hard"],
                   default="medium", help="target difficulty band (default: medium)")
    s.add_argument("--party", default="auto", metavar="auto|NAMES",
                   help="'auto' (default) is every character sheet in the campaign")
    s.add_argument("--map", default="", metavar="NAME",
                   help="map name to place the encounter on")
    s.add_argument("--monsters", default="", metavar="LIST",
                   help='eligible monsters: "goblin x4, hobgoblin" (default: all SRD)')
    s.add_argument("--max-enemies", type=int, default=8, metavar="N",
                   help="maximum number of enemies (default: 8)")
    s.add_argument("--proposal-seed", default="", metavar="SEED",
                   help="seed for reproducible proposals (default: derived from inputs)")
    s = sub.add_parser("accept", parents=c,
                       help="accept a proposal and start combat")
    s.add_argument("proposal_id", help="the proposal ID to accept")
    s.add_argument("--force", action="store_true",
                   help="accept even if validation fails (not recommended)")
    s = sub.add_parser("formation", parents=c,
                       help="save and replay a monster arrangement across maps")
    fs = s.add_subparsers(dest="formation_action", required=True, metavar="action")
    g = fs.add_parser("list", parents=c, help="every formation in this campaign")
    g = fs.add_parser("show", parents=c, help="one formation's members and offsets")
    g.add_argument("name")
    g = fs.add_parser("save", parents=c,
                      help="save the monsters on the board right now, as a formation")
    g.add_argument("name")
    g.add_argument("--include-pcs", action="store_true",
                   help="also save the party. Off by default: the opposition is the "
                        "reusable part, and the party is placed with --pc")
    g.add_argument("--info", metavar="TEXT",
                   help="a note for whoever plays this later (what it is for, what "
                        "the GM changes about it)")
    g = fs.add_parser("place", parents=c,
                      help="show where a formation would land on a map, start nothing")
    g.add_argument("name")
    g.add_argument("map")
    g.add_argument("--at", metavar="SQ", help="pin the anchor to this square")
    g.add_argument("--centre", action="store_true",
                   help="drop it in the middle of the map, scaled to it")
    s = sub.add_parser("scene", parents=c,
                       help="make a Chartdown map this campaign's persistent scene")
    s.add_argument("name", nargs="?",
                   help="the map's `.cd` stem, e.g. strixhaven-campus")
    s.add_argument("--background", metavar="PATH",
                   help="campaign-relative background. Defaults to the committed "
                        ".player.svg beside the .cd")
    s.add_argument("--show", action="store_true",
                   help="read the scene back instead of setting one")
    s = sub.add_parser("here", parents=c,
                       help="snap the party marker onto a named place in the scene's .cd")
    s.add_argument("place", help="a place slug or label from the .cd, e.g. biblioplex")
    s.add_argument("--name", metavar="TEXT", help="what the marker is called on screen")
    s.add_argument("--reveal", dest="reveal", action="store_true", default=None,
                   help="put the marker on the players' screen (the default)")
    s.add_argument("--hide", dest="reveal", action="store_false",
                   help="keep the marker off every browser. The GM reads it from "
                        "the terminal; there is no GM-only view to read it from")
    s = sub.add_parser("condition", parents=c, help="GM: add or remove a condition")
    s.add_argument("token")
    s.add_argument("action", choices=["add", "remove"])
    s.add_argument("condition", help='"exhaustion 3" sets an exhaustion level')
    s.add_argument("--level", type=int, choices=range(1, 7), metavar="1-6",
                   help="the level, for 'exhaustion'")
    s = sub.add_parser("adjust", parents=c, help="GM: correct hp, temp_hp, ac, speed")
    s.add_argument("token")
    s.add_argument("changes", nargs="+")
    s = sub.add_parser("log", parents=c)
    s.add_argument("n", nargs="?", type=int, default=6)
    s = sub.add_parser("receipts", parents=c,
                       help="check every roll receipt against its hash chain")
    s.add_argument("--rolls", type=int, default=0, metavar="N",
                   help="also print the last N receipts")
    s = sub.add_parser("invocations", parents=c,
                       help="every command this campaign accepted, with its seed "
                            "and outcome (combat/invocations.jsonl)")
    s.add_argument("index", nargs="?", type=int, metavar="N",
                   help="print one invocation in full, and the command that re-runs it")
    return top


def _parse(argv: list):
    """parse_args, but a malformed command prints one friendly line, not argparse's usage
    block. --help still prints normally. Returns None after such a refusal."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            return parser().parse_args(argv)
    except SystemExit as e:
        if not e.code:
            raise                                   # -h / --help: argparse already printed it
        lines = [ln for ln in err.getvalue().splitlines() if "error:" in ln]
        why = lines[-1].split("error:", 1)[1].strip() if lines else "that command is not complete"
        verb = next((a for a in argv if not a.startswith("-") and a in _verbs()), "")
        print(f"That command is not complete: {why}." + (f" Try `{verb} --help`." if verb else ""))
        return None


def _verbs() -> set:
    return set(parser()._subparsers._group_actions[0].choices)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parse(argv)
    if args is None:
        return 1
    if isinstance(getattr(args, "attack", None), list):
        args.attack = " ".join(args.attack) or None
    if getattr(args, "cmd", "") == "ready" and args.kind == "move" and args.what and not args.target:
        args.target, args.what = args.what[0], []
    canon = _canonical(argv)
    camp_dir = None
    pending = {}
    before = None
    try:
        camp_dir = _camp_dir(args)
        pending = {} if args.cmd in READ_ONLY else _load_pending(camp_dir, canon)
        args._seed = _resolve_seed(pending)
        args._decisions = list(pending.get("decisions", []))
        before = _encounter_fingerprint(camp_dir, args)
        code = run(args)
        _journal(camp_dir, args, argv, canon,
                 "read" if args.cmd in READ_ONLY else "committed", code,
                 pending=pending, before=before)
        if args.cmd not in READ_ONLY:
            _clear_pending(camp_dir)
        return code
    except Stop as e:
        if camp_dir is not None:
            _journal(camp_dir, args, argv, canon, "refused", e.code, e.text,
                     pending, before)
        print(e.text)
        return e.code
    except engine.CombatError as e:
        if camp_dir is not None:
            _journal(camp_dir, args, argv, canon, "refused", 1, str(e), pending, before)
        print(str(e))
        return 1
    except roller.BadFace as e:
        # The player mistyped a die face, not the engine: refuse the command,
        # keep the pending roll saved so the same attack can be re-run, and
        # name the legal range. Uncaught, this reached the localdm bridge and
        # killed play.py's REPL, losing the session mid-fight.
        if camp_dir is not None:
            _save_pending(camp_dir, canon, args._seed, args._decisions)
            # A pause, not a refusal: the pending record is open and waiting for
            # the same command with a legal face, so the journal links this
            # invocation to whatever resumes it. Nothing executed either way.
            _journal(camp_dir, args, argv, canon, "paused", 1, "bad-face",
                     pending, before)
        print(f"{e}. Nothing has happened yet.\n"
              f"Re-run the same command with {_prior(args, drop_last_roll=True)}"
              f"--roll <{e.legal}, no modifier>.")
        return 1
    except PendingRoll as e:
        if camp_dir is not None:
            _save_pending(camp_dir, canon, args._seed, args._decisions)
            # The advantage goes into the marker as well as the printed line.
            # The display's banner said "roll 1d20+4" for a roll the engine was
            # holding two dice for and keeping the lower of, so a player watched
            # for one number that was never going to arrive; the only place the
            # disadvantage was visible was the log entry after the fact. A "|"
            # separates it from the notation, which never contains one.
            adv = f"|{e.advantage}" if e.advantage and e.advantage != "normal" else ""
            _push_pending(camp_dir, f"roll:{e.notation}{adv}")
            _journal(camp_dir, args, argv, canon, "paused", 2, "roll",
                     pending, before)
        what = "the d20 face" if e.notation.startswith("1d20") else "the dice total"
        adv = f" with {e.advantage}" if e.advantage != "normal" else ""
        prior = _prior(args)
        if e.advantage != "normal" and e.notation.startswith("1d20"):
            # The player's kept face is supplied as one value (the design the
            # tests pin): say to roll two physical dice, not one number.
            keep = "highest" if e.advantage == "advantage" else "lowest"
            ask = f"{prior}--roll <kept d20 face: roll 2d20, keep {keep}>"
        else:
            ask = f"{prior}--roll <{what}, no modifier>"
        print(f"{e.who} rolls {e.notation}{adv} for {e.label}. Nothing has happened yet.\n"
              f"Re-run the same command with {ask}, "
              f"or --for-me to let the engine roll.")
        return 2
    except engine.DecisionNeeded as e:
        if camp_dir is not None:
            keys = list(args._decisions)
            if not keys and args.react:      # answers given up front keep their place
                keys = [OLD_FORM] * len(args.react)
            keys += [e.key] if e.key not in keys else []
            _save_pending(camp_dir, canon, args._seed, keys)
            _push_pending(camp_dir, f"react:{e.key}")
            _journal(camp_dir, args, argv, canon, "paused", 2, "reaction",
                     pending, before)
        print(f"{e.prompt} Nothing has happened yet.\n"
              f"Re-run the same command with {_prior(args)}--react yes or --react no.")
        return 2
    except ValueError as e:
        # A bad square or number typed by a player (move kairos frog-1) is a refusal,
        # not a crash: uncaught, it killed the REPL mid-fight (test report pt3, B1).
        if camp_dir is not None:
            _journal(camp_dir, args, argv, canon, "refused", 1, str(e), pending, before)
        print(f"{e}.".replace("..", "."))
        return 1


def _prior(args, drop_last_roll: bool = False) -> str:
    """The answers already given, to repeat on the re-run.

    drop_last_roll skips the final --roll: that is the one the dice rejected,
    and echoing it back would ask the player to send it again.
    """
    rolls = list(args.roll or [])
    if drop_last_roll and rolls:
        rolls = rolls[:-1]
    out = "".join(f"--roll {v} " for v in rolls)
    out += "".join(f"--react {v} " for v in (args.react or []))
    return out


def _push_pending(camp_dir, pending: str) -> None:
    """Mirror a roll/reaction wait to the display via turn.pending (B2).

    The encounter file is deliberately NOT saved: the command is paused and
    saving a half-applied encounter would corrupt the seeded re-run. The
    pending marker lives only in the pushed snapshot, and the next successful
    command overwrites it with turn.pending "". Pushes are best-effort
    (sync.push_display no-ops with TACTICS_NO_DISPLAY=1).
    """
    try:
        enc = state.load(state.encounter_path(camp_dir))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return
    if enc.status != "active":
        return
    enc.turn.pending = pending
    try:
        sync.push_display(enc, enc.meta)
    except Exception:
        return
