#!/usr/bin/env python3
"""
world.py — off-screen NPC and faction actions (Blades in the Dark style)

Faction clocks track progress toward goals without constant GM attention.
Ticks follow in-game time (days/weeks), not sessions. The system decides
*whether and how far*, the GM decides *what it looks like*.

Usage:
    CAMPAIGN=my-campaign

    # Add a faction with a goal and clock size
    python3 world.py -c $CAMPAIGN add "Red Hand" --goal "seize the granary" --clock 6

    # Advance time and tick factions
    python3 world.py -c $CAMPAIGN tick --days 3

    # Manually adjust a faction's clock (player interference)
    python3 world.py -c $CAMPAIGN clock "Red Hand" -2   # sabotaged
    python3 world.py -c $CAMPAIGN clock "Red Hand" +1   # helped

    # Prevent a faction from advancing (story veto)
    python3 world.py -c $CAMPAIGN hold "Red Hand"
    python3 world.py -c $CAMPAIGN release "Red Hand"

    # View all factions
    python3 world.py -c $CAMPAIGN status

    # Clear (end of arc/session)
    python3 world.py -c $CAMPAIGN clear
"""

import argparse
import json
import os
import pathlib
import sys
import time
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

# Use scripts/dice.py functions directly
import random


def roll(n: int, sides: int) -> list[int]:
    """Roll n d<sides> dice and return the results."""
    return [random.randint(1, sides) for _ in range(n)]


# ─── Paths ────────────────────────────────────────────────────────────────────

def get_campaign_dir(campaign: str) -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("OPENTTG_CAMPAIGNS_DIR", str(pathlib.Path.home() / ".local" / "share" / "open-tabletop-gm" / "campaigns"))
    ) / campaign


def factions_file(campaign: str) -> pathlib.Path:
    return get_campaign_dir(campaign) / "factions.json"


def faction_log_file(campaign: str) -> pathlib.Path:
    return get_campaign_dir(campaign) / "faction_log.md"


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Faction:
    name: str
    goal: str
    clock_size: int = 4          # segments: 4, 6, or 8
    current: int = 0             # current segments filled
    held: bool = False           # vetoed by GM
    progress_history: list = field(default_factory=list)  # [(tick, change, notes)]
    created: str = ""
    last_ticked: Optional[str] = None


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
    
    with open(fpath) as f:
        data = json.load(f)
    
    state = WorldState()
    state.current_tick = data.get("current_tick", 0)
    state.tick_interval = data.get("tick_interval", "day")
    
    for name, fd in data.get("factions", {}).items():
        state.factions[name] = Faction(
            name=fd["name"],
            goal=fd["goal"],
            clock_size=fd.get("clock_size", 4),
            current=fd.get("current", 0),
            held=fd.get("held", False),
            progress_history=fd.get("progress_history", []),
            created=fd.get("created", ""),
            last_ticked=fd.get("last_ticked")
        )
    
    return state


def save_state(state: WorldState, campaign: str) -> None:
    fpath = factions_file(campaign)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    
    data = {
        "current_tick": state.current_tick,
        "tick_interval": state.tick_interval,
        "factions": {
            name: {
                "name": f.name,
                "goal": f.goal,
                "clock_size": f.clock_size,
                "current": f.current,
                "held": f.held,
                "progress_history": f.progress_history,
                "created": f.created,
                "last_ticked": f.last_ticked
            }
            for name, f in state.factions.items()
        }
    }
    
    # Atomic write
    tmp = fpath.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.rename(fpath)


def log_faction_event(campaign: str, event: str) -> None:
    """Append event to human-readable log."""
    lpath = faction_log_file(campaign)
    lpath.parent.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(lpath, "a") as f:
        f.write(f"\n## {timestamp}\n{event}\n")


# ─── Core functions ───────────────────────────────────────────────────────────

def add_faction(campaign: str, name: str, goal: str, clock_size: int) -> None:
    state = load_state(campaign)
    
    if name in state.factions:
        print(f"Faction '{name}' already exists.")
        return
    
    now = datetime.now().isoformat()
    state.factions[name] = Faction(
        name=name,
        goal=goal,
        clock_size=clock_size,
        created=now
    )
    
    save_state(state, campaign)
    print(f"Added faction '{name}': '{goal}' ({clock_size} segments)")
    log_faction_event(campaign, f"- Added faction **{name}**: {goal}")


def tick_factions(campaign: str, days: int) -> None:
    state = load_state(campaign)
    interval = state.tick_interval
    interval_name = "day" if interval == "day" else "week"
    
    if interval == "day":
        steps = days
    else:  # week
        steps = days // 7
    
    if steps < 1:
        print(f"No full {interval_name}s to advance.")
        return
    
    log_lines = [f"\n### {steps} {interval_name}{'' if steps == 1 else 's'} passed\n"]
    
    for faction in state.factions.values():
        if faction.held:
            continue
        
        # Base roll: 1-3 no progress, 4-5 one segment, 6 two segments
        roll_val = roll(1, 6)[0]
        progress = 0
        if roll_val >= 4:
            progress = 1 if roll_val <= 5 else 2
        
        # Modify by progress_history (last entry, if any)
        if faction.progress_history:
            last_change = faction.progress_history[-1][1]
            progress += last_change  # -1 for damage, +1 for help
        
        # Clamp to [0, clock_size]
        old = faction.current
        faction.current = max(0, min(faction.clock_size, faction.current + progress))
        faction.last_ticked = datetime.now().isoformat()
        
        # Record in history
        faction.progress_history.append([state.current_tick, progress, f"roll={roll}"])
        
        # Log if anything changed
        if progress != 0 or old != faction.current:
            log_lines.append(f"- **{faction.name}**: {old}/{faction.clock_size} → {faction.current}/{faction.clock_size} (roll {roll})")
        
        # Check for completion
        if faction.current >= faction.clock_size:
            log_lines.append(f"  ⚡ **COMPLETE**: {faction.goal}")
            # Reset clock but keep faction active
            faction.current = 0
    
    state.current_tick += steps
    save_state(state, campaign)
    
    if log_lines:
        print("\n".join(log_lines))
        log_faction_event(campaign, "\n".join(log_lines))
    else:
        print(f"No progress made this tick.")


def modify_clock(campaign: str, faction_name: str, delta: int, notes: str = "") -> None:
    state = load_state(campaign)
    
    if faction_name not in state.factions:
        print(f"Faction '{faction_name}' not found.")
        return
    
    faction = state.factions[faction_name]
    old = faction.current
    faction.current = max(0, min(faction.clock_size, faction.current + delta))
    faction.progress_history.append([state.current_tick, delta, notes])
    
    save_state(state, campaign)
    
    msg = f"{faction_name}: {old}/{faction.clock_size} → {faction.current}/{faction.clock_size}"
    if delta > 0:
        msg = f"+{delta} → {msg} ({notes or 'helped'})"
    elif delta < 0:
        msg = f"{delta} → {msg} ({notes or 'sabotaged'})"
    else:
        msg = f"→ {msg}"
    
    print(msg)
    log_faction_event(campaign, f"- {msg}")


def hold_faction(campaign: str, faction_name: str) -> None:
    state = load_state(campaign)
    
    if faction_name not in state.factions:
        print(f"Faction '{faction_name}' not found.")
        return
    
    state.factions[faction_name].held = True
    save_state(state, campaign)
    print(f"Held '{faction_name}' (will not advance)")
    log_faction_event(campaign, f"- Held **{faction_name}** (GM veto)")


def release_faction(campaign: str, faction_name: str) -> None:
    state = load_state(campaign)
    
    if faction_name not in state.factions:
        print(f"Faction '{faction_name}' not found.")
        return
    
    state.factions[faction_name].held = False
    save_state(state, campaign)
    print(f"Released '{faction_name}' (will advance again)")
    log_faction_event(campaign, f"- Released **{faction_name}**")


def show_status(campaign: str) -> None:
    state = load_state(campaign)
    
    if not state.factions:
        print("No factions defined.")
        return
    
    print(f"Current tick: {state.current_tick} ({state.tick_interval})\n")
    
    for faction in state.factions.values():
        bar = "█" * faction.current + "░" * (faction.clock_size - faction.current)
        status = "[HELD]" if faction.held else "[ACTIVE]"
        print(f"{faction.name} {status}")
        print(f"  Goal: {faction.goal}")
        print(f"  Progress: [{bar}] {faction.current}/{faction.clock_size}")
        print()


def clear_all(campaign: str) -> None:
    state = WorldState()
    save_state(state, campaign)
    print("Cleared all factions.")
    log_faction_event(campaign, "\n--- All factions cleared ---\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Off-screen NPC and faction actions")
    parser.add_argument("-c", "--campaign", required=True, help="Campaign name")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Add faction
    add_p = subparsers.add_parser("add", help="Add a new faction")
    add_p.add_argument("name", help="Faction name")
    add_p.add_argument("--goal", required=True, help="Faction's goal")
    add_p.add_argument("--clock", type=int, default=4, choices=[4, 6, 8], help="Clock size")
    
    # Tick
    tick_p = subparsers.add_parser("tick", help="Advance time and tick factions")
    tick_p.add_argument("--days", type=int, default=1, help="Days to advance")
    
    # Clock
    clock_p = subparsers.add_parser("clock", help="Manually adjust a faction's clock")
    clock_p.add_argument("faction", help="Faction name")
    clock_p.add_argument("delta", type=int, help="Change (-2 to +2 recommended)")
    clock_p.add_argument("--notes", default="", help="Why the change happened")
    
    # Hold/Release
    hold_p = subparsers.add_parser("hold", help="Prevent a faction from advancing")
    hold_p.add_argument("faction", help="Faction name")
    
    release_p = subparsers.add_parser("release", help="Allow a faction to advance again")
    release_p.add_argument("faction", help="Faction name")
    
    # Status
    subparsers.add_parser("status", help="Show all factions")
    
    # Clear
    subparsers.add_parser("clear", help="Clear all factions")
    
    args = parser.parse_args()
    
    if args.command == "add":
        add_faction(args.campaign, args.name, args.goal, args.clock)
    elif args.command == "tick":
        tick_factions(args.campaign, args.days)
    elif args.command == "clock":
        modify_clock(args.campaign, args.faction, args.delta, args.notes)
    elif args.command == "hold":
        hold_faction(args.campaign, args.faction)
    elif args.command == "release":
        release_faction(args.campaign, args.faction)
    elif args.command == "status":
        show_status(args.campaign)
    elif args.command == "clear":
        clear_all(args.campaign)


if __name__ == "__main__":
    main()
