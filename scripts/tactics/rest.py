"""rest.py: short and long rest — the pause in a fight that costs the party resources.

    short_rest(enc, token_ids, roller)   1 hour: Hit Dice, short-rest features, Pact Magic
    long_rest(enc, token_ids)            8 hours: HP, half the Hit Dice, every spell slot
    cmd_rest(args, enc, roller)          the `rest` command, on a running encounter
    cmd_rest_campaign(args, dir, name)   the same command with no fight running
    advance_calendar(campaign, kind)     move the in-world clock on by the rest

Why this module exists next to slots.py: slots.py is the one place that knows
what a spell slot is, and it can only be read on the way back up if something
decides a rest happened. A slot that is only ever spent is a resource the party
runs out of mid-campaign, so the two are not separable.

Two rules the 5e rests turn on, and the second one is the caster's, not the
engine's:

  * A long rest restores every slot, full stop (PHB p201). There is nothing to
    decide, so the engine does it and says so.
  * A short rest restores nothing for most classes (PHB p201). A Warlock's Pact
    Magic refills by itself because the feature says it does; a Wizard's Arcane
    Recovery buys slots back with a once-per-long-rest resource, so the engine
    spends the cheapest slots it can and reports exactly which ones, because
    "up to half your level in slot levels" is a ceiling the caster chooses
    within. Everything else the GM rules.

Hit Dice are the same shape of decision and are handled the same way: a long
rest restores half, rounded up, whatever the caster did with the rest of them.
Short-rest healing spends Hit Dice and rolls for them, which is the one place
this module rolls a die — and only with the roller's own dice, and only when
the GM says `--for-me`, because a player's hit points are the player's to roll.

Both rests take an Encounter because that is where the rules live: Rules.heal is
what clears `unconscious` on the way back up, effects.end_concentration walks
enc.tokens, and sync writes a sheet per PC token. A rest between fights has no
combat/encounter.json to hand them, so `cmd_rest_campaign` builds the same
Encounter in memory from characters/*.md and commits the four stores it touches
as one unit: the sheets, tracker.json, calendar.json, and the faction clocks the
calendar would roll. That is the whole of what "out of combat" means here. The
same engine, the same rules, and a party read from its own sheets instead of from
a fight.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import safeio

from . import effects, slots, sync
from .core import CombatError, log, rules_for
from .roller import Roller
from .state import Encounter, Token

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]

# Conditions a long rest ends (PHB Appendix A). "exhaustion" is reduced by one
# rather than cleared, so it is handled apart in _clear_long_rest_conditions.
LONG_REST_CLEAR = {"frightened", "charmed", "poisoned"}


# ─── hit dice ─────────────────────────────────────────────────────────────────

def _hit_die_type(token: Token) -> str:
    """The hit die notation for a token, whatever shape it was stored in.

    build_srd.py writes "d10"; the engine writes {"die": "d10", "remaining": n}.
    """
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return str(hd.get("die") or "1d10")
    if isinstance(hd, str) and hd.strip():
        return hd.strip()
    return "1d10"


def _hit_dice_remaining(token: Token) -> int:
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return int(hd.get("remaining") or 0)
    if isinstance(hd, int) and not isinstance(hd, bool):
        return hd
    return 0


def _set_hit_dice_remaining(token: Token, n: int) -> None:
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        hd["remaining"] = n
    else:
        token.extra["hit_dice"] = {"die": _hit_die_type(token), "remaining": n}


def _hit_dice_total(token: Token) -> int:
    """How many Hit Dice the character has in total."""
    hd = token.extra.get("hit_dice")
    if isinstance(hd, dict):
        return int(hd.get("total") or 1)
    return 1


def _con_mod(token: Token) -> int:
    scores = token.extra.get("abilities") or {}
    try:
        return (int(str(scores["con"]).strip()) - 10) // 2
    except (KeyError, TypeError, ValueError):
        return int(token.saves.get("con") or 0)


# ─── features ─────────────────────────────────────────────────────────────────

def _recharge(token: Token, kinds: tuple) -> list:
    """Reset the features in `extra["usage"]` that recharge on one of `kinds`."""
    out = []
    for name, data in (token.extra.get("usage") or {}).items():
        if not isinstance(data, dict):
            continue
        if data.get("type") in kinds:
            data["left"] = data.get("max", 1)
            out.append(name)
        elif data.get("charged") is False:
            data["charged"] = True
            out.append(name)
    return out


SHORT_REST_RECHARGE = ("short_rest", "per short rest")
LONG_REST_RECHARGE = ("long_rest", "per day", "per long rest", "recharge after rest")


# ─── the rests ────────────────────────────────────────────────────────────────

def _targets(enc: Encounter, token_ids: list = None) -> list:
    """The named tokens, or every PC — a monster does not benefit from the party's rest.

    A name that is not there is a CombatError, not the KeyError Encounter.token
    raises: `cli.main` turns CombatError into a refusal with an exit code and
    prints nothing, while a KeyError escaped every handler and killed the localdm
    REPL mid-session. Both rests take this path, so one refusal covers both.
    """
    if token_ids is None:
        return [t for t in enc.tokens.values() if t.side == "pc"]
    try:
        return [enc.token(tid) for tid in token_ids]
    except KeyError as e:
        raise CombatError(str(e.args[0])) from None


def short_rest(enc: Encounter, token_ids: list = None, roller: Roller = None) -> list:
    """An hour's rest. Returns GM-facing lines describing what happened."""
    lines = []
    for token in _targets(enc, token_ids):
        if token.dead:
            lines.append(f"{token.name} is dead; short rest has no effect.")
            continue
        if token.hp <= 0:
            # PHB p197: a creature that drops to 0 HP is unconscious, and the
            # unconscious condition says it cannot move or speak. Spending a Hit
            # Die is a thing it is not doing, so the refusal belongs here rather
            # than in the GM's head: without it `_spend_hit_dice` would happily
            # roll healing onto a dying character during the one hour it is
            # supposed to be lying unconscious through.
            lines.append(f"{token.name} is at 0 HP: unconscious, and a Hit Die is not "
                         f"something an unconscious creature spends. A short rest has no "
                         f"effect until it stabilises, or until a long rest.")
            continue
        token.reaction_used = False
        token.dodging = False
        lines += _spend_hit_dice(token, roller)
        recharged = _recharge(token, SHORT_REST_RECHARGE)
        if recharged:
            lines.append(f"{token.name}: recharged {', '.join(recharged)}.")
        lines += slots.short_rest(token)
    return lines or ["Short rest: nothing to recover."]


def _spend_hit_dice(token: Token, roller: Roller) -> list:
    """Spend Hit Dice to heal. In auto mode all of them, in players mode none.

    A player rolls their own hit points, so the engine will not spend them on
    the GM's say-so: it reports what is available and waits for `--for-me`, the
    same rule every other player die in this engine already follows.
    """
    remaining, total = _hit_dice_remaining(token), _hit_dice_total(token)
    if remaining <= 0 or token.hp >= token.max_hp:
        return []
    die, con = _hit_die_type(token), _con_mod(token)
    if roller is None:
        roller = Roller()
    if not roller.for_me:
        return [f"{token.name}: {remaining}/{total} Hit Dice ({die} {con:+d} each) available. "
                f"Use `rest short --for-me` to spend them."]
    healed = spent = 0
    while remaining > 0 and token.hp < token.max_hp:
        gained = min(roller.roll(die, token.name, "Hit Die").total + con, token.max_hp - token.hp)
        token.hp += gained
        healed += gained
        spent += 1
        remaining -= 1
    _set_hit_dice_remaining(token, remaining)
    noun = "Hit Die" if spent == 1 else "Hit Dice"
    return [f"{token.name}: spent {spent} {noun} ({die} {con:+d}), healed {healed} HP "
            f"(now {token.hp}/{token.max_hp})."]


def long_rest(enc: Encounter, token_ids: list = None) -> list:
    """Eight hours. Returns GM-facing lines describing what happened."""
    lines = []
    for token in _targets(enc, token_ids):
        if token.dead:
            lines.append(f"{token.name} is dead; long rest has no effect.")
            continue
        if token.hp < token.max_hp:
            # Through Rules.heal, not a direct write: heal is the one path that
            # clears `unconscious` when a creature at 0 HP comes back up. Writing
            # token.hp here healed the HP and left the condition, so the creature
            # stayed asleep and can_act refused every later action.
            healed = rules_for(enc).heal(token, token.max_hp - token.hp)["healed"]
            lines.append(f"{token.name}: healed {healed} HP (now {token.hp}/{token.max_hp}).")
        if token.temp_hp > 0:
            token.temp_hp = 0
            lines.append(f"{token.name}: temporary HP cleared.")
        total = _hit_dice_total(token)
        restored = (total + 1) // 2
        _set_hit_dice_remaining(token, min(_hit_dice_remaining(token) + restored, total))
        lines.append(f"{token.name}: {restored} Hit Dice restored "
                     f"(now {_hit_dice_remaining(token)}/{total}).")
        lines += slots.long_rest(token)
        recharged = _recharge(token, LONG_REST_RECHARGE)
        if recharged:
            lines.append(f"{token.name}: recharged {', '.join(recharged)}.")
        cleared = _clear_long_rest_conditions(token)
        if cleared:
            lines.append(f"{token.name}: cleared {', '.join(cleared)}.")
        if token.hp > 0:
            token.death_saves.update(successes=0, failures=0)
            token.stable = False
        token.reaction_used = False
        token.dodging = False
        if token.concentration:
            lines.append(effects.end_concentration(enc, token, "long rest"))
            token.concentration = None
    return lines or ["Long rest: nothing to recover."]


def _clear_long_rest_conditions(token: Token) -> list:
    """Conditions a long rest ends. Exhaustion goes down by one, not to zero."""
    cleared = []
    for cond in list(token.conditions):
        low = cond.lower()
        if low not in LONG_REST_CLEAR and low != "exhaustion":
            continue
        if low == "exhaustion":
            level = int(token.extra.get("exhaustion_level") or 0) - 1
            token.extra["exhaustion_level"] = max(0, level)
            if level <= 0:
                token.remove_condition("exhaustion")
                cleared.append("exhaustion")
            else:
                cleared.append(f"exhaustion (down to level {level})")
        else:
            token.remove_condition(low)
            cleared.append(low)
    return cleared


# ─── the command ──────────────────────────────────────────────────────────────

def cmd_rest(args, enc: Encounter, roller: Roller = None) -> tuple:
    """The `rest short|long [--token NAME]` command, on the caller's encounter.

    Takes the encounter `cli.run` has already loaded rather than loading its
    own: the caller saves the copy it holds after every command, so a second
    load here would mean a second copy, and whichever was written last would be
    the one that did not rest.
    """
    if enc.status != "active":
        raise CombatError("No combat is running. Rest between fights, or start one first.")
    token_ids = [args.token] if getattr(args, "token", None) else None
    mark = len(roller.log) if roller else 0
    if args.type == "short":
        lines = short_rest(enc, token_ids, roller)
    else:
        lines = long_rest(enc, token_ids)
    # A Hit Die the GM said "--for-me" to is a die the engine rolled on a player's
    # behalf, and it went unrecorded: nothing called core.log here, so the roll was
    # in roller.log and in no receipt and in no log entry. `mark` scopes the entry
    # to the rest's own dice, so the log never claims the rolls of the command
    # before it.
    if roller:
        log(enc, "rest", "", " ".join(lines), roller, mark)
    return "\n".join(lines), {"rest_type": args.type,
                              "targets": [t.id for t in _targets(enc, token_ids)],
                              "slots": {t.id: slots.read(t) for t in _targets(enc, token_ids)}}


def advance_calendar(campaign: str, rest_type: str) -> str:
    """Move the in-world clock on by the rest. '' if the campaign has no calendar.

    A campaign without a calendar — every test, and every table that has not run
    `calendar.py init` — is not an error: the rest happened, only the clock did
    not move. Read and arithmetic come from `calendar_step`, which writes
    nothing, so this is that plus the one write the transaction needs to be able
    to roll back.
    """
    path, data, line = calendar_step(campaign, rest_type)
    if path is None:
        return ""
    safeio.atomic_write_json(path, data)
    _calendar_module()._send_date(data)
    return line


# ─── the calendar, as a step rather than a write ───────────────────────────────

_CALENDAR = None


def _calendar_module():
    """scripts/calendar.py, loaded by path under a private name.

    By path, not by `import calendar`: that name belongs to the stdlib and half
    the tree (Flask, argparse's own formatting) has it already, so a plain
    import here would either shadow the stdlib or hand back the stdlib's. The
    arithmetic stays calendar.py's own `_advance_hours`, because the in-world
    clock is that file's business and a second implementation of "eight hours
    later" is the bug this replaces.
    """
    global _CALENDAR
    if _CALENDAR is None:
        name = "gm_calendar_for_rest"
        spec = importlib.util.spec_from_file_location(name, _SCRIPTS / "calendar.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _CALENDAR = mod
    return _CALENDAR


REST_HOURS = {"short": 1, "long": 8}       # systems/dnd5e/system.md, "Rests"

# Narrated, never enforced. The 24-hour limit is outside the SRD this engine
# reads, so a second long rest still heals; the DM is told to narrate the limit.
_LONG_REST_LIMIT = ("The once-per-24-hours limit applies; narrate it. "
                    "The rest still resolves.")


def _hour_of(data: dict) -> int | None:
    """The in-world hour of a calendar dict: hours since year 0, day 1, hour 0.

    The same number `world.in_game_hour` reads off disk. The rest cannot call
    that: the advanced calendar is only staged, and the file still says the
    hour before the rest. No dict, or a dict with no day and month, is no clock.
    """
    if not isinstance(data, dict) or not data:
        return None
    try:
        day, month = int(data["day"]), int(data["month"])
        year = int(data.get("year") or 0)
        hour = int(data.get("hour") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    months = data.get("months")
    per_year = len(months) if isinstance(months, list) and months else 12
    month_len = data.get("month_length") or 30
    try:
        month_len = max(1, int(month_len))
    except (TypeError, ValueError):
        month_len = 30
    return ((max(0, year) * per_year + (max(0, month) - 1)) * month_len
            + (max(0, day) - 1)) * 24 + hour


def _prior_long_rest(value):
    """An earlier stamp as an int, or None when the field is absent or junk."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_long_rest(state: dict, tokens, cal_data) -> list:
    """Stamp each rested entity and warn when the new hour is inside 24 of the last.

    Mutates `state` only. The caller stages that dict on the Transaction; this
    writes nothing. A dead token did not rest. No calendar means no stamp and
    no warning. Never refuses, skips, or clamps the heal `long_rest` already did.
    """
    hour = _hour_of(cal_data) if cal_data else None
    if hour is None:
        return []
    lines = []
    for token in tokens:
        if token.dead:
            continue
        ent = state.get(token.name.lower())
        if not isinstance(ent, dict):
            continue
        prev = _prior_long_rest(ent.get("last_long_rest"))
        if prev is not None and hour - prev < 24:
            lines.append(f"{token.name}: this long rest falls inside 24 hours of the last one. "
                         f"{_LONG_REST_LIMIT}")
        ent["last_long_rest"] = hour
    return lines


def calendar_step(campaign: str, rest_type: str) -> tuple:
    """What the clock would read after this rest, without writing it.

    Returns `(path, calendar dict, line)`, and `(None, None, "")` for a campaign
    with no calendar: `calendar.py` refuses to invent a date, and so does this.
    Read apart from write because the out-of-combat rest has to know what the
    calendar will say before it writes anything at all, or a refused rest would
    leave the party's sheets healed and the world's clock behind.
    """
    cal = _calendar_module()
    try:
        data = cal._load(campaign)
    except (SystemExit, OSError):
        return None, None, ""
    if not data:
        return None, None, ""
    hours = REST_HOURS.get(rest_type)
    if hours is None:
        raise CombatError(f"Unknown rest '{rest_type}': use short or long.")
    cal._advance_hours(data, hours)
    label = "Short rest" if rest_type == "short" else "Long rest"
    return (pathlib.Path(cal._cal_path(campaign)), data,
            f"  {label} (+{hours} hour{'s' if hours != 1 else ''}) → {cal._format_date(data)}")


# ─── out of combat: sheets, tracker, clock, factions ───────────────────────────

class Transaction:
    """The stores one rest touches, staged and committed as a unit.

    A rest between fights is four stores: the character sheets, tracker.json,
    calendar.json and the faction clocks. Writing them one at a time leaves the
    campaign describing two different evenings when the third write fails, and
    the party is the one that pays for it. So every file is staged first, the
    originals are kept, and `commit` restores what it already replaced if any
    write raises. A store the rest cannot change is `guard`ed rather than staged:
    it is remembered so a rollback reaches it, and otherwise never touched.

    A refusal never reaches `commit`, which is the case that has to leave
    nothing behind: a rest that cannot happen has no partial version.
    """

    def __init__(self) -> None:
        self._files: list = []          # [path, new text or None (guard), original or None]

    def _original(self, path: pathlib.Path):
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def stage(self, path, text: str) -> None:
        path = pathlib.Path(path)
        self._files.append([path, text, self._original(path)])

    def stage_json(self, path, data) -> None:
        """Staged with safeio.atomic_write_json's exact spelling, so the file a
        transaction writes is byte-identical to the one sync writes."""
        self.stage(path, json.dumps(data, indent=2))

    def guard(self, path) -> None:
        path = pathlib.Path(path)
        if path.exists():
            self._files.append([path, None, self._original(path)])

    def commit(self) -> list:
        """Write every staged file. Returns the paths actually replaced.

        Anything already written is put back if a later write raises, so the
        campaign is left as it was rather than half a night older. The rollback
        writes without a backup: the `.bak` is the copy from *before* this rest,
        which is exactly what a restore wants to keep rather than overwrite.
        """
        written = []
        try:
            for path, text, original in self._files:
                if text is None or text == original:
                    continue
                safeio.atomic_write_text(path, text)
                written.append((path, original))
        except BaseException:
            for path, original in reversed(written):
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    safeio.atomic_write_text(path, original, backup=False)
            raise
        return [p for p, _ in written]


def _party_encounter(camp_dir, campaign: str = "") -> Encounter:
    """The campaign's own character sheets as one Encounter, with no map.

    A 1x1 board of open ground: the rests never ask where anybody is standing,
    and `Encounter.board()` is only reached if something does, so a rest cannot
    fail for want of a map. `system` comes from the campaign header, because a
    campaign that plays another system gets that system's sheet reader rather
    than this repo's default. roll_mode is left at the Encounter default: the
    rest path asks for no player roll (the only die it rolls is a Hit Die, and
    only under `--for-me`), so reading state.md here would be a second copy of
    a flag nothing on this path reads.
    """
    from paths import campaign_system

    system = campaign_system(campaign, default="dnd5e") if campaign else "dnd5e"
    enc = Encounter(campaign=campaign or "rest", system=system,
                    grid={"name": "rest", "rows": ["."], "diagonals": "5"})
    R = rules_for(enc)
    for path in sorted((pathlib.Path(camp_dir) / "characters").glob("*.md")):
        token = R.token_from_sheet(path, path.stem.lower(), (0, 0))
        enc.tokens[token.id] = token
    return enc


def _faction_store(campaign: str) -> list:
    """The files world.py owns, named by world.py rather than here.

    A rest moves the calendar, and the calendar is what rolls faction clocks, so
    those files belong inside the transaction. Asking world.py for its own paths
    is what keeps the guard list from drifting when it renames one; the import is
    lazy and optional for the same reason calendar.py's is, because a campaign
    with no faction clocks still gets to rest.
    """
    try:
        import world
    except ImportError:                                # no world clocks installed
        return []
    out = []
    for path_of in (world.factions_file, world.faction_log_file, world.state_file):
        try:
            out.append(pathlib.Path(path_of(campaign)))
        except Exception:                              # noqa: BLE001 - an unresolvable
            pass                                        # campaign is not a rest failure
    return out


def cmd_rest_campaign(args, camp_dir, campaign: str, roller: Roller = None) -> tuple:
    """`rest short|long [--token NAME]` with no fight running. Returns (text, data).

    The out-of-combat rest the campaign actually spends its evenings on. It is
    the same `short_rest`/`long_rest` the fight path runs, over the same
    Encounter shape, reading the party from characters/*.md because there is no
    encounter file to read it from.

    Everything is decided before anything is written. The party is parsed, the
    rest resolves in memory, and the four stores are staged; only then does
    `Transaction.commit` touch the disk. That is what "all or none" has to mean
    when the stores live in four files.
    """
    try:
        enc = _party_encounter(camp_dir, campaign)
    except (OSError, ValueError) as e:
        raise CombatError(f"Cannot read the party's sheets: {e}") from None
    if not enc.tokens:
        raise CombatError(f"No character sheets in {pathlib.Path(camp_dir) / 'characters'}, "
                          f"so there is nobody to rest.")
    R = rules_for(enc)
    token_ids = [args.token] if getattr(args, "token", None) else None
    targets = _targets(enc, token_ids)      # CombatError on a name that is not there
    if not targets:
        raise CombatError("Nothing to rest.")

    if args.type == "short":
        lines = short_rest(enc, token_ids, roller)
    else:
        lines = long_rest(enc, token_ids)

    tx = Transaction()
    written_sheets = []
    for name, path, old, new in sync.stage_sheets(camp_dir, enc, R):
        if path is None:
            written_sheets.append((name, None, ""))
        elif new == old:
            written_sheets.append((name, path, ""))
        else:
            tx.stage(path, new)
            written_sheets.append((name, path, sync.short_diff(old, new)))
    # The hour comes from the staged calendar, not from the file, which still
    # shows the time before this rest. The stamp is set on the tracker dict
    # before it is staged, so commit is the only write and a rollback drops it.
    cal_path, cal_data, cal_line = calendar_step(campaign, args.type)
    state = sync.tracker_state(camp_dir, enc)
    limit_lines = (_record_long_rest(state, targets, cal_data)
                   if args.type == "long" else [])
    tx.stage_json(pathlib.Path(camp_dir) / "tracker.json", state)
    if cal_path is not None:
        tx.stage_json(cal_path, cal_data)
    # Faction clocks move on whole days (scripts/calendar.py, _tick_world), which
    # no rest is, so they are guarded rather than staged: remembered so a
    # rollback reaches them, and otherwise byte-for-byte untouched. A test pins
    # that, because a rest that quietly rolls a faction is a rules change
    # nobody asked for.
    for guard in _faction_store(campaign):
        tx.guard(guard)

    out = list(lines)
    if cal_line:
        out.append(cal_line)
    out.extend(limit_lines)
    for name, path, diff in written_sheets:
        if path is None:
            out.append(f"{name}: no sheet in characters/, nothing written.")
        elif diff:
            out.append(f"{name}'s sheet updated (backup {path.name}.bak):\n{diff}")
        else:
            out.append(f"{name}'s sheet already up to date.")
    # Last, and the only thing that touches the disk: if any of the writes above
    # fail, the four stores are back where they started and nothing is reported.
    tx.commit()
    if cal_path is not None:
        _calendar_module()._send_date(cal_data)
    return "\n".join(out), {"rest_type": args.type,
                            "targets": [t.id for t in targets],
                            "slots": {t.id: slots.read(t) for t in targets},
                            "calendar": bool(cal_path),
                            "sheets": [name for name, _, _ in written_sheets]}
