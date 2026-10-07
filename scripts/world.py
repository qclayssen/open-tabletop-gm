#!/usr/bin/env python3
"""
world.py — off-screen NPC and faction actions (Blades in the Dark style)

Faction clocks track progress toward goals without constant GM attention.
Ticks follow in-game time (days or weeks), not sessions. The script decides
*whether and how far* a faction moves; the GM decides *what it looks like*.

Everything printed here is GM-only. Clocks stay off the player display unless
the GM opts one in with `reveal`: a clock face the players never see is what
makes off-screen pressure pressure rather than bookkeeping, so hidden is the
default and only revealed clocks (name, size, filled segments, never the goal
or notes) are ever read by the display, via `revealed_clocks`.

Usage:
    # Add a faction with a goal and clock size (4, 6 or 8 segments)
    python3 world.py -c $CAMPAIGN add "Red Hand" --goal "seize the granary" --clock 6

    # Advance time and tick every active faction once per tick interval
    python3 world.py -c $CAMPAIGN tick --days 3 [--seed 7]

    # Usually called by calendar.py advance, not by hand
    python3 calendar.py -c $CAMPAIGN advance 3 days

    # The party interferes — direct segment change
    python3 world.py -c $CAMPAIGN clock "Red Hand" -2 --notes "burned their safehouse"
    python3 world.py -c $CAMPAIGN clock "Red Hand" +1 --notes "let a caravan through"

    # A one-shot nudge to the next tick's roll (helped +1 / hurt -1)
    python3 world.py -c $CAMPAIGN lean "Red Hand" -1

    # GM veto, for things that must not happen yet
    python3 world.py -c $CAMPAIGN hold "Red Hand"
    python3 world.py -c $CAMPAIGN release "Red Hand"

    # Show one clock to the players as a segmented dial (default: hidden)
    python3 world.py -c $CAMPAIGN reveal "Red Hand"
    python3 world.py -c $CAMPAIGN hide "Red Hand"

    # A full clock has fired: narrate the change, then acknowledge it
    python3 world.py -c $CAMPAIGN complete "Red Hand"

    # View all factions, or change how often a tick happens
    python3 world.py -c $CAMPAIGN status
    python3 world.py -c $CAMPAIGN set-interval week
"""

import argparse
import json
import os
import pathlib
import random
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from paths import find_campaign

# Aliased for the same reason `tactics/roller.py` and `combat.py` alias it: the
# factory is the one place that knows how a seed becomes a generator, and an
# unaliased `dice` in a module that also passes `dice` around as a local would be
# shadowed by one of them without anyone noticing.
import dice as _dice

SCHEMA_VERSION = 2

# One hidden d6 per faction per tick (Stars Without a Number "faction turn").
# 1-3: nothing happens. 4-5: one segment. 6: two segments.
TICK_FACES = {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 2}

# A clock may only be pushed this far in one direct move (party interference).
# Anything larger is the GM quietly rewriting the campaign, not interference.
MAX_CLOCK_DELTA = 3

INTERVALS = ("day", "week")

GM_ONLY = "GM-only"


# ─── Paths ────────────────────────────────────────────────────────────────────

def get_campaign_dir(campaign: str) -> pathlib.Path:
    """The campaign directory, resolved the same way as every other script.

    paths.find_campaign honours $GM_CAMPAIGN_ROOT and the legacy default root,
    so world.py reads and writes the same factions.json the rest of the suite
    reads from. (This used to consult an OPENTTG_CAMPAIGNS_DIR of its own and
    quietly wrote to a second, invisible copy of the campaign.)
    """
    return find_campaign(campaign)


def factions_file(campaign: str) -> pathlib.Path:
    return get_campaign_dir(campaign) / "factions.json"


def faction_log_file(campaign: str) -> pathlib.Path:
    return get_campaign_dir(campaign) / "faction_log.md"


def state_file(campaign: str) -> pathlib.Path:
    return get_campaign_dir(campaign) / "state.md"


def _calendar(campaign_dir) -> dict:
    """A campaign's calendar.json, or {} for a campaign without one.

    The one reader, because two scripts now need this file and a second
    `json.loads` in each of them is how one of them ends up disagreeing about
    what a campaign with no clock means.
    """
    try:
        data = json.loads((pathlib.Path(campaign_dir) / "calendar.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def in_game_date(campaign: str) -> str:
    """Today's in-world date from calendar.json, or '' if there is no calendar.

    Clocks are stamped in world time, not wall time: a faction that moved on
    "3 Harvestmoon 1247" must be findable by that date when the GM goes
    looking three sessions later.
    """
    data = _calendar(get_campaign_dir(campaign))
    day, month, year = data.get("day"), data.get("month"), data.get("year")
    if day is None or month is None:
        return ""
    months = data.get("months") or []
    label = months[month - 1] if isinstance(months, list) and 1 <= month <= len(months) else f"Month {month}"
    stamp = f"{day} {label}" + (f" {year}" if year else "")
    return stamp.strip()


def hour_of(data) -> int | None:
    """A calendar dict as one number: hours since year 0, day 1, hour 0.

    Or None for a dict that is no calendar at all. This is the reading
    `in_game_hour` makes of calendar.json, split out because a rest needs the
    same answer for a calendar it has only staged: the hour a long rest *ends*
    at is not on disk yet when the stamp is set, so `rest.py` has to ask the
    dict rather than the file. One formula, one place; two copies of it are how
    a stamp ends up disagreeing with the clock it was read from.

    `day` and `month` are required and an absent year reads as 0. Only
    differences matter, and a dict carrying neither day nor month has no clock
    to offer rather than a clock at zero.

    Hour-granular, because calendar.json records no smaller unit: the number of
    months per year is taken from the calendar's own `months` list, falling back
    to the twelve-month assumption `_month_length` already makes, and a duration
    shorter than an hour therefore lasts until the next hour of in-world time.
    That is a limit of the calendar this repo keeps, stated here rather than
    discovered by a player.
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
    return ((max(0, year) * per_year + (max(0, month) - 1)) * month_len + (max(0, day) - 1)) * 24 + hour


def in_game_hour(campaign_dir) -> int | None:
    """The campaign's clock as one number: hours since year 0, day 1, hour 0.

    Or None for a campaign with no calendar. This is the same clock
    `in_game_date` reads, reduced to something two moments can be subtracted
    into, which is what an effect with a duration needs: it must expire when an
    hour of the *fiction* has passed, and a table that stops for dinner has not
    spent one.

    takes a directory rather than a campaign name because the two callers have
    different ones: tracker.py resolves the name itself, and the DM's context
    builder is handed a path it already holds.

    The arithmetic is `hour_of`, which the rest also applies to the calendar it
    has staged and not yet written.
    """
    return hour_of(_calendar(campaign_dir))


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Faction:
    name: str
    goal: str
    clock_size: int = 4          # segments: 4, 6, or 8
    current: int = 0             # current segments filled
    held: bool = False           # vetoed by the GM
    revealed: bool = False       # GM chose to show this clock on the player display
    fired: bool = False          # clock is full; awaiting narration, not ticking
    fired_at: Optional[str] = None
    fired_tick: Optional[int] = None
    lean: int = 0                # one-shot nudge to the next tick: -2..+2
    progress_history: list = field(default_factory=list)  # [[tick, change, note]]
    created: str = ""
    last_ticked: Optional[str] = None

    def state(self) -> str:
        if self.fired:
            return "FIRED"
        if self.held:
            return "HELD"
        return "ACTIVE"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "goal": self.goal,
            "clock_size": self.clock_size,
            "current": self.current,
            "held": self.held,
            "revealed": self.revealed,
            "fired": self.fired,
            "fired_at": self.fired_at,
            "fired_tick": self.fired_tick,
            "lean": self.lean,
            "progress_history": self.progress_history,
            "created": self.created,
            "last_ticked": self.last_ticked,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "Faction":
        return cls(
            name=data.get("name", name),
            goal=data.get("goal", ""),
            clock_size=int(data.get("clock_size", 4)),
            current=int(data.get("current", 0)),
            held=bool(data.get("held", False)),
            revealed=bool(data.get("revealed", False)),
            fired=bool(data.get("fired", False)),
            fired_at=data.get("fired_at"),
            fired_tick=data.get("fired_tick"),
            lean=int(data.get("lean", 0) or 0),
            progress_history=list(data.get("progress_history", []) or []),
            created=data.get("created", ""),
            last_ticked=data.get("last_ticked"),
        )


@dataclass
class WorldState:
    factions: dict = field(default_factory=dict)  # name -> Faction
    current_tick: int = 0
    tick_interval: str = "day"  # day or week


# ─── File I/O ────────────────────────────────────────────────────────────────

def load_state(campaign: str) -> WorldState:
    fpath = factions_file(campaign)
    if not fpath.exists():
        return WorldState()

    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)

    state = WorldState()
    state.current_tick = int(data.get("current_tick", 0))
    interval = data.get("tick_interval", "day")
    state.tick_interval = interval if interval in INTERVALS else "day"

    for name, fd in (data.get("factions") or {}).items():
        state.factions[name] = Faction.from_dict(name, fd)

    return state


def save_state(state: WorldState, campaign: str) -> None:
    fpath = factions_file(campaign)
    fpath.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": SCHEMA_VERSION,
        "current_tick": state.current_tick,
        "tick_interval": state.tick_interval,
        "factions": {name: f.to_dict() for name, f in state.factions.items()},
    }

    # Atomic write
    tmp = fpath.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, fpath)


def log_faction_event(campaign: str, event: str) -> None:
    """Append an event to the human-readable GM log."""
    lpath = faction_log_file(campaign)
    lpath.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(lpath, "a", encoding="utf-8") as f:
        f.write(f"\n## {timestamp} ({GM_ONLY})\n{event}\n")


# ─── state.md: ## Faction Moves ──────────────────────────────────────────────

MOVES_HEADING = "## Faction Moves"

# The template lines that mean "this section has nothing in it yet": the
# *(none yet)* marker and templates/state.md's italic helper sentence. The
# digest drops both (is_template_line), so leaving one above real entries would
# tell the DM a section was empty when it is not. Matched on the italic shape
# rather than the exact wording, so a reworded template still matches.
_EMPTY_MOVES = ("*(none yet)*",)
_HELPER_LINE = re.compile(r"^\*[^*].*[^*]\*$")

# Where a state.md with no `## Faction Moves` gets one. templates/state.md keeps
# the section between "Open Threads & Rumours" and "Recent Events", so that is
# where a missing section is created rather than at the end of the file, where
# it would land under the DM-only notes.
_MOVES_ANCHOR = "## Recent Events"


def _moves_block(text: str, entries: list) -> Optional[str]:
    """The whole of `text` with `entries` appended under `## Faction Moves`.

    Splitting the section at the next `## ` heading is what makes this an
    append: every other section, and every move already recorded above the new
    ones, is copied through untouched. When the section is absent it is created
    just above `## Recent Events`, which is where templates/state.md keeps it.
    """
    lines = text.splitlines()

    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().lower() == MOVES_HEADING.lower()), None)

    if start is not None:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        # Template lines only ever sit above real content, so dropping them is
        # a no-op on a section that already has moves in it.
        body = [ln for ln in lines[start + 1:end]
                if ln.strip() not in _EMPTY_MOVES and not _HELPER_LINE.match(ln.strip())]
        while body and not body[-1].strip():
            body.pop()
        # A blank line after the last entry when the section is not the last
        # heading in the file, so the next `## ` does not look glued to it.
        block = entries + ([""] if end < len(lines) else [])
        lines[start + 1:end] = body + ([""] if body else []) + block
        return "\n".join(lines) + "\n"

    # No section yet: create one just above the anchor heading, keeping the
    # blank-line separation the rest of the file uses.
    at = next((i for i, ln in enumerate(lines)
               if ln.strip().lower() == _MOVES_ANCHOR.lower()), len(lines))
    head = lines[:at]
    while head and not head[-1].strip():
        head.pop()
    tail = lines[at:]
    while tail and not tail[0].strip():
        tail.pop(0)
    block = [MOVES_HEADING, ""] + entries
    out = head + ([""] if head else []) + block + (["", *tail] if tail else [])
    return "\n".join(out) + "\n"


def append_faction_moves(campaign: str, entries: list) -> bool:
    """Write tick results into state.md's `## Faction Moves`. True if written.

    world.py used to print an instruction telling the GM to record the move by
    hand while localdm/context.py's digest read that section, so a GM who
    forgot left the DM blind about the off-screen world. The clock result is
    now written here; the GM still narrates it in their own words, which
    SKILL.md asks for and which this does not attempt to fake.

    Appends only, atomically, with the same .bak convention as
    tactics/state.py's save(): a failed or interrupted write leaves the previous
    state.md intact. Returns False when there is nothing to say or no state.md
    to say it in, so a campaign without one is not disturbed.
    """
    entries = [e for e in entries if e]
    if not entries:
        return False
    spath = state_file(campaign)
    if not spath.exists():
        return False
    try:
        text = spath.read_text(encoding="utf-8")
    except OSError:
        return False

    # Idempotent: an entry already recorded verbatim (same date, same words) is
    # not written twice, so a re-run of the same advance leaves state.md alone.
    present = {ln.strip() for ln in text.splitlines()}
    entries = [e for e in entries if e.strip() not in present]
    if not entries:
        return False

    new = _moves_block(text, entries)
    if new == text:
        return False

    tmp = spath.with_name(spath.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
        f.flush()
        os.fsync(f.fileno())
    shutil.copy2(spath, spath.with_name(spath.name + ".bak"))
    os.replace(tmp, spath)
    return True


# ─── Reporting ───────────────────────────────────────────────────────────────

def _clock_bar(faction: Faction) -> str:
    filled = min(faction.current, faction.clock_size)
    return "█" * filled + "░" * (faction.clock_size - filled)


def _count(steps: int, unit: str) -> str:
    return f"{steps} {unit}{'' if steps == 1 else 's'}"


def _moved_entry(faction: Faction, old: int, note: str) -> str:
    """One state.md line for a clock that gained or lost segments.

    No segment numbers: clocks are GM-only by design, and state.md is read back
    into the DM digest, so the line says only which way the faction moved.
    """
    way = "closer to" if faction.current > old else "further from"
    return f"{faction.name} moved {way} *{faction.goal}* ({note})."


def _fired_entry(faction: Faction) -> str:
    """One state.md line for a full clock: the goal landed, the world changed."""
    return (f"**{faction.name}** has completed *{faction.goal}* "
            f"the change is now visible in the world.")


def _fired_lines(faction: Faction, lines: list) -> None:
    lines.append(
        f"  ⚡ {faction.name}: *{faction.goal}* — COMPLETE"
        + (f" ({faction.fired_at})" if faction.fired_at else "")
    )
    lines.append(f"    The clock result is recorded under `## Faction Moves`; add the visible "
                 f"change in your own words, then: world.py -c <campaign> "
                 f"complete \"{faction.name}\".")


# ─── Core functions ───────────────────────────────────────────────────────────

def add_faction(campaign: str, name: str, goal: str, clock_size: int) -> int:
    state = load_state(campaign)

    if name in state.factions:
        print(f"Faction '{name}' already exists (clock {state.factions[name].current}/"
              f"{state.factions[name].clock_size}). Use `clock` to move it.")
        return 1

    state.factions[name] = Faction(
        name=name,
        goal=goal,
        clock_size=clock_size,
        created=in_game_date(campaign) or datetime.now().isoformat(timespec="seconds"),
    )

    save_state(state, campaign)
    print(f"Added faction '{name}': {goal} ({clock_size} segments)")
    log_faction_event(campaign, f"- Added faction **{name}**: {goal} ({clock_size} segments)")
    return 0


def _steps_for(state: WorldState, days: int) -> int:
    """How many ticks are owed for this many days of in-game time."""
    return days if state.tick_interval == "day" else days // 7


def tick_factions(campaign: str, days: int, rng: Optional[random.Random] = None) -> list:
    """Advance every active faction one hidden roll per tick interval.

    Returns the report lines (empty when nothing moved). One roll *per tick*,
    not one per call: three days is three chances for a faction to act, and
    collapsing them into a single roll made long travel the safest thing a
    party could do.
    """
    state = load_state(campaign)
    unit = state.tick_interval
    steps = _steps_for(state, days)

    if steps < 1:
        return [f"[{GM_ONLY}] No full {unit}{'' if unit == 'day' else 's'} to advance "
                f"({days} day(s) at a {unit} interval)."]

    if not state.factions:
        return []

    # Was `dice = rng or random.Random()`. Two faults in one line: the generator
    # was built outside the factory, so it carried no `.seed_value` and the tick
    # could not be quoted; and the local was named `dice`, which is the module's
    # own name, so any `dice` reference in this scope silently became a random
    # generator. Renamed, and built by the factory.
    stream = rng if rng is not None else _dice.new_rng()
    today = in_game_date(campaign)
    stamp = f" ({today})" if today else ""
    lines = [f"[{GM_ONLY}] {_count(steps, unit)} passed{stamp} — one hidden d6 per faction per {unit}."]
    fired_this_tick: list = []
    skipped: dict = {}
    moves: list = []          # the lines that go into state.md's ## Faction Moves

    for step in range(steps):
        for faction in state.factions.values():
            if faction.fired:
                skipped[faction.name] = "already fired"   # waiting on `complete`
                continue
            if faction.held:
                skipped[faction.name] = "held"            # waiting on `release`
                continue
            if today and faction.created == today:
                # A clock added today must not fill today: the party has to get
                # at least one interval of warning that the world is moving.
                skipped[faction.name] = "added today"
                continue

            face = stream.randint(1, 6)
            progress = TICK_FACES[face]
            lean = faction.lean
            if lean:
                progress += lean
                faction.lean = 0  # one-shot: it is spent by the tick it applies to

            old = faction.current
            faction.current = max(0, min(faction.clock_size, old + progress))
            faction.last_ticked = today or datetime.now().isoformat(timespec="seconds")

            note = f"d6 {face}" + (f" {lean:+d}" if lean else "")
            faction.progress_history.append([state.current_tick, faction.current - old, note])

            # Every tick gets a line, including the ones where nothing happened:
            # a hidden roll the GM cannot see is how clocks quietly stop mattering.
            lines.append(
                f"- {faction.name}: {old}/{faction.clock_size} → {faction.current}/{faction.clock_size}"
                f"  (d6 {face} → {progress:+d} segment{'' if abs(progress) == 1 else 's'})"
                f"  · {unit} {step + 1}/{steps}")

            # Only a real move goes into state.md: the rolls that did nothing
            # are GM bookkeeping (faction_log.md keeps them), and writing
            # "nothing happened" every day would drown the moves that did.
            dated = f"- *{today}*: " if today else "- "
            if faction.current != old:
                moves.append(dated + _moved_entry(faction, old, "off-screen"))

            if faction.current >= faction.clock_size and not faction.fired:
                faction.fired = True
                faction.fired_at = today or datetime.now().isoformat(timespec="seconds")
                faction.fired_tick = state.current_tick + step
                fired_this_tick.append(faction)
                moves.append(dated + _fired_entry(faction))

    state.current_tick += steps
    save_state(state, campaign)

    for faction in fired_this_tick:
        _fired_lines(faction, lines)

    if skipped:
        lines.append("- Unchanged: " + ", ".join(f"{n} ({why})" for n, why in sorted(skipped.items())) + ".")

    if len(lines) == 1 and not fired_this_tick:
        # Every faction was held or already fired: say so rather than printing
        # a header and nothing under it.
        lines.append("- No faction moved.")

    # The clock is the record of what happened; state.md is the record the DM
    # reads. Written after factions.json is safely on disk so a crash between
    # the two loses the state.md note, not the tick.
    append_faction_moves(campaign, moves)

    log_faction_event(campaign, "\n".join(lines[1:]))
    return lines


def tick_for_calendar(campaign: str, days: int, seed: Optional[int] = None) -> str:
    """Tick hook for calendar.py advance. Silent when the campaign has no clocks."""
    if days < 1 or not factions_file(campaign).exists():
        return ""
    rng = _dice.new_rng(seed) if seed is not None else None
    lines = tick_factions(campaign, days, rng=rng)
    return "\n".join(lines)


def modify_clock(campaign: str, faction_name: str, delta: int, notes: str = "") -> int:
    """Direct segment change — the party acting against (or for) a faction."""
    if abs(delta) > MAX_CLOCK_DELTA:
        print(f"{delta:+d} is beyond one interference (±{MAX_CLOCK_DELTA} segments). "
              f"Use `lean` for a smaller nudge to the next tick.")
        return 1

    state = load_state(campaign)
    faction = state.factions.get(faction_name)
    if faction is None:
        print(f"Faction '{faction_name}' not found. `status` lists the known ones.")
        return 1

    old = faction.current
    faction.current = max(0, min(faction.clock_size, old + delta))
    moved = faction.current - old
    reason = notes or ("helped" if delta > 0 else "sabotaged" if delta < 0 else "no change")
    faction.progress_history.append([state.current_tick, moved, f"clock {delta:+d} — {reason}"])

    # Pushing a fired clock back means the outcome was pre-empted, not that it
    # happened and was undone: un-fire it so it can tick again.
    unfired = ""
    if delta < 0 and faction.fired and faction.current < faction.clock_size:
        faction.fired = False
        faction.fired_at = None
        faction.fired_tick = None
        unfired = "  (was fired — outcome pre-empted, clock ticking again)"

    if faction.current >= faction.clock_size and not faction.fired:
        faction.fired = True
        faction.fired_at = in_game_date(campaign) or datetime.now().isoformat(timespec="seconds")
        faction.fired_tick = state.current_tick

    save_state(state, campaign)

    # Party interference is a faction move too, and the same reasoning applies:
    # the DM reads state.md, so the outcome belongs there whether it came from a
    # hidden roll or from the party walking into it.
    today = in_game_date(campaign)
    dated = f"- *{today}*: " if today else "- "
    moves = []
    if moved:
        moves.append(dated + _moved_entry(faction, old, f"{reason}, off-screen"))
    if faction.fired:
        moves.append(dated + _fired_entry(faction))
    append_faction_moves(campaign, moves)

    msg = f"[{GM_ONLY}] {faction_name}: {old}/{faction.clock_size} → {faction.current}/{faction.clock_size} ({reason}){unfired}"
    lines = [msg]
    if faction.fired:
        _fired_lines(faction, lines)
    for line in lines:
        print(line)
    log_faction_event(campaign, "\n".join(lines))
    return 0


def set_lean(campaign: str, faction_name: str, lean: int) -> int:
    """A one-shot modifier on the next tick roll: the party helped (+1) or hurt (-1)."""
    if abs(lean) > 2:
        print(f"A lean of {lean:+d} is too strong — the roll moves 0-2 segments on its own. Use ±1, or ±2 for a decisive effect.")
        return 1

    state = load_state(campaign)
    faction = state.factions.get(faction_name)
    if faction is None:
        print(f"Faction '{faction_name}' not found. `status` lists the known ones.")
        return 1

    faction.lean = lean
    save_state(state, campaign)
    if lean:
        print(f"[{GM_ONLY}] {faction_name}: next {state.tick_interval} roll {lean:+d} "
              f"(one-shot — spent on the next tick)")
    else:
        print(f"[{GM_ONLY}] {faction_name}: tick modifier cleared")
    return 0


def hold_faction(campaign: str, faction_name: str) -> int:
    state = load_state(campaign)
    if faction_name not in state.factions:
        print(f"Faction '{faction_name}' not found. `status` lists the known ones.")
        return 1
    state.factions[faction_name].held = True
    save_state(state, campaign)
    print(f"[{GM_ONLY}] Held '{faction_name}' (will not advance)")
    log_faction_event(campaign, f"- Held **{faction_name}** (GM veto)")
    return 0


def release_faction(campaign: str, faction_name: str) -> int:
    state = load_state(campaign)
    if faction_name not in state.factions:
        print(f"Faction '{faction_name}' not found. `status` lists the known ones.")
        return 1
    state.factions[faction_name].held = False
    save_state(state, campaign)
    print(f"[{GM_ONLY}] Released '{faction_name}' (will advance again)")
    log_faction_event(campaign, f"- Released **{faction_name}**")
    return 0


def complete_faction(campaign: str, faction_name: str, outcome: str = "") -> int:
    """Acknowledge a fired clock: the goal happened (or was averted), reset to empty."""
    state = load_state(campaign)
    faction = state.factions.get(faction_name)
    if faction is None:
        print(f"Faction '{faction_name}' not found. `status` lists the known ones.")
        return 1
    if not faction.fired:
        print(f"[{GM_ONLY}] {faction_name} has not fired (clock {faction.current}/{faction.clock_size}). "
              f"Nothing to acknowledge.")
        return 1

    fired_at = faction.fired_at or "unknown date"
    faction.current = 0
    faction.fired = False
    faction.fired_at = None
    faction.fired_tick = None
    faction.lean = 0
    save_state(state, campaign)

    note = outcome or "narrated"
    print(f"[{GM_ONLY}] {faction_name}: clock fired {fired_at} — acknowledged ({note}). "
          f"Clock reset to 0/{faction.clock_size}; give the faction its next goal with "
          f"`clock` segments or a new `add`.")
    log_faction_event(campaign,
                      f"- **{faction_name}** fired {fired_at}: {faction.goal} — {note}. Clock reset.")
    return 0


def set_revealed(campaign: str, faction_name: str, revealed: bool) -> int:
    """Opt one clock in to (or out of) the player display. Per campaign, persisted."""
    state = load_state(campaign)
    if faction_name not in state.factions:
        print(f"Faction '{faction_name}' not found. `status` lists the known ones.")
        return 1
    state.factions[faction_name].revealed = revealed
    save_state(state, campaign)
    if revealed:
        print(f"[{GM_ONLY}] Revealed '{faction_name}': its dial now shows on the player display.")
    else:
        print(f"[{GM_ONLY}] Hid '{faction_name}': its dial is off the player display.")
    log_faction_event(campaign, f"- {'Revealed' if revealed else 'Hid'} **{faction_name}** "
                                f"{'to' if revealed else 'from'} the players")
    return 0


def revealed_clocks(campaign: str) -> list:
    """The only faction data allowed to reach the player display.

    Hidden clocks are absent from the result, not flagged. Each entry carries
    just name, size and filled segments: no goal, notes, lean or history.
    """
    try:
        state = load_state(campaign)
    except (OSError, ValueError):
        return []
    return [
        {"name": f.name, "size": f.clock_size, "filled": max(0, min(f.current, f.clock_size))}
        for f in state.factions.values() if f.revealed
    ]


def show_status(campaign: str) -> int:
    state = load_state(campaign)

    if not state.factions:
        print(f"[{GM_ONLY}] No factions defined. `add` one to start the world moving.")
        return 0

    print(f"[{GM_ONLY}] Tick {state.current_tick} · one hidden d6 per faction per {state.tick_interval}")
    for faction in state.factions.values():
        print(f"\n{faction.name} [{faction.state()}]" + (" (revealed)" if faction.revealed else ""))
        print(f"  Goal: {faction.goal}")
        print(f"  Progress: [{_clock_bar(faction)}] {faction.current}/{faction.clock_size}")
        if faction.lean:
            print(f"  Next {state.tick_interval} roll: {faction.lean:+d}")
        if faction.fired:
            print(f"  Fired: {faction.fired_at} (tick {faction.fired_tick}) — narrate it, then `complete`")
    return 0


def set_interval(campaign: str, interval: str) -> int:
    state = load_state(campaign)
    state.tick_interval = interval
    save_state(state, campaign)
    print(f"[{GM_ONLY}] Ticks now happen once per {interval}.")
    return 0


def clear_all(campaign: str, confirmed: bool = False) -> int:
    """Drop every clock (end of arc). The log keeps the record of what fired."""
    state = load_state(campaign)
    if not state.factions:
        print(f"[{GM_ONLY}] Nothing to clear.")
        return 0

    if not confirmed:
        print(f"[{GM_ONLY}] This drops {len(state.factions)} clock(s) — a real, irreversible "
              f"loss of the off-screen record in factions.json:")
        for f in state.factions.values():
            bar = _clock_bar(f)
            print(f"  {f.name} [{f.state()}] [{bar}] {f.current}/{f.clock_size} — {f.goal}")
        print("Re-run with --yes to clear. faction_log.md is kept either way.")
        return 1

    summary = "\n".join(
        f"- **{f.name}** was at {f.current}/{f.clock_size}"
        + (f", fired {f.fired_at}" if f.fired else "")
        + f" — {f.goal}" for f in state.factions.values()
    )
    save_state(WorldState(), campaign)
    print(f"[{GM_ONLY}] Cleared {len(state.factions)} clock(s) (faction_log.md kept).")
    log_faction_event(campaign, "\n--- All factions cleared ---\n" + summary + "\n")
    return 0


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Off-screen NPC and faction actions")
    parser.add_argument("-c", "--campaign", required=True, help="Campaign name")
    parser.add_argument("--seed", type=int, default=None,
                        help="Seed the hidden rolls (reproducible ticks)")
    # --seed is also accepted after the subcommand, which is where a GM writing
    # `tick --days 1 --seed 3` naturally puts it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--seed", type=int, default=argparse.SUPPRESS,
                        help="Seed the hidden rolls (reproducible ticks)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_p = subparsers.add_parser("add", help="Add a new faction", parents=[common])
    add_p.add_argument("name", help="Faction name")
    add_p.add_argument("--goal", required=True, help="Faction's goal")
    add_p.add_argument("--clock", type=int, default=4, choices=[4, 6, 8], help="Clock size")

    tick_p = subparsers.add_parser("tick", help="Advance time and tick factions", parents=[common])
    tick_p.add_argument("--days", type=int, default=1, help="Days to advance")

    clock_p = subparsers.add_parser("clock", help="Direct segment change (party interference)",
                                    parents=[common])
    clock_p.add_argument("faction", help="Faction name")
    clock_p.add_argument("delta", type=int, help="Change (-3 to +3)")
    clock_p.add_argument("--notes", default="", help="Why the change happened")

    lean_p = subparsers.add_parser("lean", help="One-shot modifier on the next tick roll",
                                   parents=[common])
    lean_p.add_argument("faction", help="Faction name")
    lean_p.add_argument("lean", type=int, help="Modifier (-2 to +2, 0 clears)")

    hold_p = subparsers.add_parser("hold", help="Prevent a faction from advancing", parents=[common])
    hold_p.add_argument("faction", help="Faction name")

    release_p = subparsers.add_parser("release", help="Allow a faction to advance again",
                                      parents=[common])
    release_p.add_argument("faction", help="Faction name")

    reveal_p = subparsers.add_parser("reveal", help="Show a clock to the players on the display",
                                     parents=[common])
    reveal_p.add_argument("faction", help="Faction name")

    hide_p = subparsers.add_parser("hide", help="Take a clock off the player display",
                                   parents=[common])
    hide_p.add_argument("faction", help="Faction name")

    complete_p = subparsers.add_parser("complete", help="Acknowledge a fired clock and reset it",
                                       parents=[common])
    complete_p.add_argument("faction", help="Faction name")
    complete_p.add_argument("--outcome", default="",
                            help="How it landed: 'narrated', 'averted', or a one-line note")

    interval_p = subparsers.add_parser("set-interval", help="How often a tick happens",
                                       parents=[common])
    interval_p.add_argument("interval", choices=list(INTERVALS))

    subparsers.add_parser("status", help="Show all factions (GM-only view)", parents=[common])

    clear_p = subparsers.add_parser("clear", help="Drop all faction clocks (end of arc)",
                                    parents=[common])
    clear_p.add_argument("--yes", action="store_true", help="Confirm the drop")

    args = parser.parse_args()
    if not hasattr(args, "seed"):
        args.seed = None
    campaign = args.campaign

    if   args.command == "add":           return add_faction(campaign, args.name, args.goal, args.clock)
    elif args.command == "tick":
        lines = tick_factions(campaign, args.days,
                              rng=_dice.new_rng(args.seed) if args.seed is not None else None)
        for line in lines:
            print(line)
        return 0
    elif args.command == "clock":         return modify_clock(campaign, args.faction, args.delta, args.notes)
    elif args.command == "lean":          return set_lean(campaign, args.faction, args.lean)
    elif args.command == "hold":          return hold_faction(campaign, args.faction)
    elif args.command == "release":       return release_faction(campaign, args.faction)
    elif args.command == "reveal":        return set_revealed(campaign, args.faction, True)
    elif args.command == "hide":          return set_revealed(campaign, args.faction, False)
    elif args.command == "complete":      return complete_faction(campaign, args.faction, args.outcome)
    elif args.command == "set-interval":  return set_interval(campaign, args.interval)
    elif args.command == "status":        return show_status(campaign)
    elif args.command == "clear":         return clear_all(campaign, args.yes)
    parser.print_help()
    return 2


if __name__ == "__main__":

    # Hard Rule 4: this must run on a non-UTF-8 console. Rule glyphs and arrows
    # in the summaries below are decoration; under `LC_ALL=C` stdout is ascii and
    # a single one of them raises UnicodeEncodeError, killing the CLI with a
    # traceback on line one -- which reads as broken data rather than a missing
    # glyph. Same remedy as build_srd.py (#275): reconfigure both streams, and
    # `errors="replace"` because a dropped glyph is cosmetic where a traceback is
    # a failure. A no-op when stdout is already UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # a non-TextIO wrapper, or detached
            pass
    sys.exit(main())
