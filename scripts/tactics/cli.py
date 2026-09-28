"""cli.py: the GM's one-call commands for grid combat (run via scripts/tactics/combat.py).

    python3 scripts/tactics/combat.py -c <campaign> <command> [args] [--roll N] [--json]

Setup and flow
    start <map> --pc NAME@SQ [--pc ...] --monster "SRD NAME@SQ" [...] [--ally "SRD NAME@SQ"]
    status                         whose turn, positions, HP
    options <token>                numbered choices for a GM-controlled creature
    choose <token> <n>|auto        run option n, or let the engine pick (ai_difficulty
                                   easy|normal|deadly in state.md, or --difficulty)
    end-turn                       next creature in initiative
    end                            finish: write sheets, tracker, session log

Actions (the current creature)
    move <token> <square>          e.g. move kairos D5   (preview <token> <square> checks first)
    attack <token> <target> [attack name]      no name: the best legal attack
    multiattack <token> <target> [--option N]  every attack of a Multiattack, as one action
    dash | disengage | dodge | stand <token>
    death-save <token>
    undo-move                      take back the last move this turn
    cast <token> "<spell>" [target|square ...] [--level N]
                                   e.g. cast kairos "magic missile" frog-1 frog-2 frog-1
    use <token> "<action>" <square>   a monster's area action (breath weapon)
    help <token> <ally> <target> | hide <token> | escape <token>
    ready <token> attack|cast|move [what] [--target X] --trigger "text"
    trigger <token> [target]       the readied action happens now (a reaction)
    reactions <token> ask|auto|off Shield / Silvery Barbs: ask the player, always, never
    sight <token>                  who it sees, and with what cover (the display's cover shading)
    fog hide|dim|off               display fog of war: squares no PC sees are dimmed; "hide"
                                   also leaves out the creatures there (the default)
    condition <token> add|remove <condition>     GM ruling (e.g. a rider the engine left to you)
    adjust <token> hp=N temp_hp=N ac=N           GM correction
    log [n]                        last n combat log lines
    reachable <token>              squares reachable walking and with Dash (for the display)
    targets <token>                every attack and target with hit chance (for the display)
    spells <token>                 known spells, how they target, and whether they can be cast
    preview-area <token> "<spell>" <square|target> [--level N]
                                   who a spell would catch, chance to fail, expected damage

Dice. Under roll_mode "players" a player's roll is asked for, never invented:
the command stops (exit code 2, nothing changed) and says what to roll.
Re-run the same command with --roll <natural result> (the die face, or the
dice total, without modifiers); repeat --roll for several rolls. --for-me lets
the engine roll this time. --react yes|no answers a reaction prompt (an
opportunity attack, Shield, Silvery Barbs); repeat it when several are asked.
A paused command replays the same engine dice when re-run (combat/pending.json).

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

from . import actions, ai, effects, engine, maps, policy, sight, spells, state, sync, rest, xp_2014, xp_2024
from .grid import parse_square
from . import roller
from .roller import PendingRoll, Roller
from .state import Encounter

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]

_DISPLAY_CAMPAIGN = _SCRIPTS.parent / "display" / ".campaign"
READ_ONLY = ("status", "options", "preview", "reachable", "targets", "log", "spells",
             "preview-area", "sight", "budget", "rate")
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


def _difficulty(camp_dir) -> str:
    """ai_difficulty: easy | normal | deadly in state.md (for choose <token> auto)."""
    try:
        text = (camp_dir / "state.md").read_text(encoding="utf-8")
    except OSError:
        return "normal"
    m = re.search(r"ai_difficulty\W+(easy|normal|deadly)\b", text)
    return m.group(1) if m else "normal"


def _roller(args) -> Roller:
    seed = args.seed if args.seed is not None else getattr(args, "_seed", None)
    rng = random.Random(seed) if seed is not None else random.Random()
    return Roller(rng=rng, supplied=list(args.roll or []),
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


def _load(camp_dir) -> Encounter:
    path = state.encounter_path(camp_dir)
    if not path.exists():
        raise Stop("No grid combat is running. Start one: start <map> --pc NAME@SQ --monster NAME@SQ")
    return state.load(path)


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


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def _format_spell_slots(token) -> str:
    """Format spell slots for display: '1st: 2/4, 2nd: 1/3'."""
    slots = token.extra.get("slots") or {}
    if not slots:
        return ""
    parts = []
    for lv in sorted(slots.keys(), key=int):
        s = slots[lv]
        used = s.get("used", 0)
        total = s.get("total", 0)
        if total > 0:
            parts.append(f"{_ordinal(int(lv))}: {used}/{total}")
    return "Spell slots: " + ", ".join(parts) if parts else ""


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
        tags = list(x.conditions)
        if x.concentration:
            tags.append(f"concentrating: {x.concentration}")
        tags += [e["name"] for e in x.effects if not e.get("conditions") and e.get("name")]
        cond = f" [{', '.join(tags)}]" if tags else ""
        slot_info = f" {_format_spell_slots(x)}" if x.side == "pc" else ""
        parts.append(f"{x.name} dead" if x.dead else f"{x.name} {x.square} {x.hp}/{x.max_hp}{cond}{slot_info}")
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


# ─── Encounter design (Phase 5) ─────────────────────────────────────────────────

def cmd_budget(args, camp_dir) -> tuple[str, dict]:
    """Show XP budget thresholds for the party."""
    # camp_dir is passed but we don't need an active combat for budget
    # Load party from character files
    camp_dir_path = pathlib.Path(camp_dir) if isinstance(camp_dir, str) else camp_dir
    char_dir = camp_dir_path / "characters"
    if not char_dir.exists():
        raise Stop("No characters/ folder found in campaign.")
    
    levels = []
    if args.party == "auto":
        for p in char_dir.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            try:
                text = p.read_text(encoding="utf-8")
                m = re.search(r"\*\*Level:\*\*\s*(\d+)", text)
                if m:
                    levels.append(int(m.group(1)))
            except Exception:
                pass
    else:
        # Parse comma-separated levels
        levels = [int(x.strip()) for x in args.party.split(",") if x.strip().isdigit()]
    
    if not levels:
        raise Stop("No party levels found. Add character files or use --party '3,3,4,4'.")
    
    avg_level = round(sum(levels) / len(levels))
    players = len(levels)
    
    # Choose ruleset module
    xp_mod = xp_2024 if args.ruleset == "2024" else xp_2014
    
    lines = [f"Party: {players} PCs (avg level {avg_level}) — {args.ruleset} ruleset"]
    lines.append("")
    
    if args.ruleset == "2024":
        lines.append(f"{'Difficulty':<10} {'Per PC':>10} {'Total':>10}")
        lines.append("-" * 32)
        for diff in ("low", "moderate", "high"):
            per_pc = xp_mod._xp_budget_per_player(diff, avg_level)
            total = per_pc * players
            lines.append(f"{diff:<10} {per_pc:>10,} {total:>10,}")
    else:
        lines.append(f"{'Difficulty':<10} {'Per PC':>10} {'Total':>10}")
        lines.append("-" * 32)
        for diff in ("easy", "medium", "hard", "deadly"):
            per_pc = xp_mod._xp_per_player(diff, avg_level)
            total = per_pc * players
            lines.append(f"{diff:<10} {per_pc:>10,} {total:>10,}")
    
    lines.append("")
    lines.append("Use `combat.py rate --monsters 'goblin x4, hobgoblin'` to rate a specific encounter.")
    
    return "\n".join(lines), {"avg_level": avg_level, "players": players, "ruleset": args.ruleset}


def cmd_rate(args, camp_dir) -> tuple[str, dict]:
    """Rate encounter difficulty for a monster list."""
    from . import xp_2014, xp_2024
    
    # Load party to get average level
    camp_dir_path = pathlib.Path(camp_dir) if isinstance(camp_dir, str) else camp_dir
    char_dir = camp_dir_path / "characters"
    levels = []
    if char_dir.exists():
        for p in char_dir.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            try:
                text = p.read_text(encoding="utf-8")
                m = re.search(r"\*\*Level:\*\*\s*(\d+)", text)
                if m:
                    levels.append(int(m.group(1)))
            except Exception:
                pass
    
    if not levels:
        raise Stop("No party levels found. Add character files to characters/ folder.")
    
    avg_level = round(sum(levels) / len(levels))
    players = len(levels)
    
    # Parse monsters
    # Support both "goblin x4, hobgoblin" and "goblin:1/4:4, hobgoblin:1/2:1"
    monsters = []
    for entry in args.monsters.split(","):
        entry = entry.strip()
        if " x" in entry or " X" in entry:
            # "goblin x4" format
            parts = entry.split()
            name = parts[0]
            count = int(parts[-1].replace("x", "").replace("X", ""))
            cr = None
        elif ":" in entry:
            # "goblin:1/4:4" format
            parts = entry.split(":")
            name = parts[0]
            cr_raw = parts[1]
            count = int(parts[2]) if len(parts) > 2 else 1
            # Look up CR from SRD
            cr = _lookup_cr(name, cr_raw)
        else:
            # Just name, assume count 1
            name = entry
            count = 1
            cr = None
        
        if cr is None:
            cr = _lookup_cr(name, None)
        if cr is None:
            raise Stop(f"Could not determine CR for '{name}'. Use 'name:cr:count' format.")
        
        monsters.append((name, cr, count))
    
    # Choose ruleset module
    xp_mod = xp_2024 if args.ruleset == "2024" else xp_2014
    
    # Calculate XP
    raw_xp, mult, adj_xp = xp_mod._calc_monster_xp(monsters)
    total_monsters = sum(cnt for _, _, cnt in monsters)
    per_player = adj_xp // len(levels)
    avg_level = round(sum(levels)/len(levels))
    
    if args.ruleset == "2024":
        diff = xp_mod._classify_budget_per_player(per_player, avg_level)
        diff_labels = {"low": "Low", "moderate": "Moderate", "high": "High", "trivial": "Trivial"}
    else:
        diff = xp_mod._classify(per_player, avg_level)
        diff_labels = {"easy": "Easy", "medium": "Medium", "hard": "Hard", "deadly": "Deadly", "trivial": "Trivial"}
    
    lines = [f"Encounter: {total_monsters} monsters (×{mult:.1f}) — {args.ruleset} ruleset"]
    lines.append(f"Party: {len(levels)} PCs (avg level {avg_level})")
    lines.append("")
    for name, cr, cnt in monsters:
        xp = xp_mod.CR_XP.get(cr, 0) * cnt
        lines.append(f"  {cnt}× {name} (CR {cr}): {xp:,} XP")
    lines.append(f"")
    lines.append(f"Raw {raw_xp:,} × {mult:.1f} = Adjusted {adj_xp:,} XP")
    lines.append(f"Difficulty:  {diff_labels.get(diff, diff.upper())}  (Level {avg_level} party of {len(levels)})")
    lines.append(f"Per player:  {per_player:,} XP")
    
    return "\n".join(lines), {
        "difficulty": diff, "per_player": per_player, "total_xp": adj_xp,
        "ruleset": args.ruleset, "monsters": monsters
    }


def _lookup_cr(name: str, cr_hint: str = None) -> str | None:
    """Look up monster CR from SRD data."""
    try:
        from paths import find_campaign
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "systems" / "dnd5e"))
        from lookup import lookup_record
    except Exception:
        return None
    
    try:
        rec = lookup_record(name, category="monster")
        if rec and "cr" in rec:
            # Normalize CR to match CR_XP keys (e.g., "0.25" -> "1/4")
            cr_val = rec["cr"]
            return _normalise_cr(str(cr_val))
    except Exception:
        pass
    
    # Fallback: try with cr_hint
    if cr_hint:
        try:
            f = float(cr_hint)
            if abs(f - 0.125) < 0.001: return "1/8"
            if abs(f - 0.25) < 0.001: return "1/4"
            if abs(f - 0.5) < 0.001: return "1/2"
            return str(int(round(f)))
        except ValueError:
            pass
    
    return None


def _normalise_cr(s: str) -> str:
    """Normalize CR string to canonical key."""
    s = s.strip()
    try:
        f = float(s)
        if abs(f - 0.125) < 0.001: return "1/8"
        if abs(f - 0.25)  < 0.001: return "1/4"
        if abs(f - 0.5)   < 0.001: return "1/2"
        return str(int(round(f)))
    except ValueError:
        pass
    return s


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
    if args.cmd in ("start", "budget", "rate"):
        if args.cmd == "start":
            enc, text = cmd_start(args, camp_dir)
        elif args.cmd == "budget":
            text, data = cmd_budget(args, camp_dir)
            enc = None
        elif args.cmd == "rate":
            text, data = cmd_rate(args, camp_dir)
            enc = None
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
                                 _reactions(enc, args))
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
            text, data = rest.cmd_rest(args, camp_dir)
        elif cmd == "budget":
            text, data = cmd_budget(args, camp_dir)
        elif cmd == "rate":
            text, data = cmd_rate(args, camp_dir)
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
            (t.add_condition if args.action == "add" else t.remove_condition)(args.condition)
            text = f"{t.name}: {args.condition.lower()} {'added' if args.action == 'add' else 'removed'}."
            more = effects.check_incapacitated(enc, t) if args.action == "add" else []
            if more:
                text += " " + " ".join(more)
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
        elif args.cmd in ("start", "end-turn", "death-save", "trigger"):
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
        s.add_argument("square")
    s = sub.add_parser("attack", parents=c)
    s.add_argument("token")
    s.add_argument("target")
    s.add_argument("attack", nargs="*")
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
    s = sub.add_parser("fog", parents=c, help="display fog of war: hide, dim or off")
    s.add_argument("mode", choices=list(sight.FOG_MODES))
    s = sub.add_parser("rest", parents=c, help="short or long rest for the party (or one character)")
    s.add_argument("type", choices=["short", "long"])
    s.add_argument("--token", metavar="NAME", help="specific character (default: all PCs)")
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

    # Encounter design (Phase 5)
    s = sub.add_parser("budget", parents=c, help="XP budget thresholds for the party")
    s.add_argument("--party", default="auto", help="auto: read from character files, or comma-separated levels")
    s.add_argument("--ruleset", choices=["2014", "2024"], default="2014", help="XP ruleset")
    s = sub.add_parser("rate", parents=c, help="Rate encounter difficulty for a monster list")
    s.add_argument("monsters", help="e.g. 'goblin x4, hobgoblin' or 'goblin:1/4:4, hobgoblin:1/2:1'")
    s.add_argument("--ruleset", choices=["2014", "2024"], default="2014", help="XP ruleset")

    return top


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(argv)
    if isinstance(getattr(args, "attack", None), list):
        args.attack = " ".join(args.attack) or None
    if getattr(args, "cmd", "") == "ready" and args.kind == "move" and args.what and not args.target:
        args.target, args.what = args.what[0], []
    canon = _canonical(argv)
    camp_dir = None
    try:
        camp_dir = _camp_dir(args)
        pending = {} if args.cmd in READ_ONLY else _load_pending(camp_dir, canon)
        args._seed = pending.get("seed", random.randrange(1 << 30))
        args._decisions = list(pending.get("decisions", []))
        code = run(args)
        if args.cmd not in READ_ONLY:
            _clear_pending(camp_dir)
        return code
    except Stop as e:
        print(e.text)
        return e.code
    except engine.CombatError as e:
        print(str(e))
        return 1
    except PendingRoll as e:
        if camp_dir is not None:
            _save_pending(camp_dir, canon, args._seed, args._decisions)
        what = "the d20 face" if e.notation.startswith("1d20") else "the dice total"
        adv = f" with {e.advantage}" if e.advantage != "normal" else ""
        prior = _prior(args)
        print(f"{e.who} rolls {e.notation}{adv} for {e.label}. Nothing has happened yet.\n"
              f"Re-run the same command with {prior}--roll <{what}, no modifier>, "
              f"or --for-me to let the engine roll.")
        return 2
    except engine.DecisionNeeded as e:
        if camp_dir is not None:
            keys = list(args._decisions)
            if not keys and args.react:      # answers given up front keep their place
                keys = [OLD_FORM] * len(args.react)
            keys += [e.key] if e.key not in keys else []
            _save_pending(camp_dir, canon, args._seed, keys)
        print(f"{e.prompt} Nothing has happened yet.\n"
              f"Re-run the same command with {_prior(args)}--react yes or --react no.")
        return 2


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
