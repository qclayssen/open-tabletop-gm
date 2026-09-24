"""state.py: the encounter, saved to <campaign>/combat/encounter.json.

The file is the single source of truth for a grid fight: map, tokens, turn
order, round, the current actor's action economy and the combat log. Every
command loads it, works on the loaded copy, and writes it back atomically
(temp file + os.replace, previous version kept as encounter.json.bak), so a
crash mid-command leaves either the old state or the new one, never half.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
from dataclasses import asdict, dataclass, field

from .grid import Grid, label

SCHEMA_VERSION = 1
SIDES = ("pc", "ally", "enemy", "neutral")


@dataclass
class Token:
    id: str                      # stable slug used on the command line: "kairos", "frog-1"
    name: str                    # display name: "Kairos", "Giant Frog 1"
    side: str                    # pc | ally | enemy | neutral
    x: int
    y: int
    hp: int
    max_hp: int
    ac: int
    speed: int = 30
    swim_speed: int = 0
    temp_hp: int = 0
    dex_mod: int = 0
    controller: str = "gm"       # "player": a human decides and (roll_mode players) rolls
    initiative: int = None
    init_roll: int = None
    conditions: list = field(default_factory=list)
    attacks: list = field(default_factory=list)     # [{name, type, bonus, reach, range, damage, flags}]
    saves: dict = field(default_factory=dict)       # {"dex": 2, ...}
    resistances: list = field(default_factory=list)
    immunities: list = field(default_factory=list)
    vulnerabilities: list = field(default_factory=list)
    condition_immunities: list = field(default_factory=list)
    death_saves: dict = field(default_factory=lambda: {"successes": 0, "failures": 0})
    stable: bool = False
    dead: bool = False
    reaction_used: bool = False
    dodging: bool = False        # took the Dodge action; cleared at the start of their next turn
    concentration: str = None
    source: dict = field(default_factory=dict)      # {"kind": "srd", "ref": "giant-frog"} | {"kind": "sheet", ...}
    extra: dict = field(default_factory=dict)       # system-specific data (spell slots, ...)

    @property
    def pos(self) -> tuple:
        return (self.x, self.y)

    @property
    def square(self) -> str:
        return label(self.pos)

    def has(self, condition: str) -> bool:
        return condition.lower() in (c.lower() for c in self.conditions)

    def add_condition(self, condition: str) -> None:
        if not self.has(condition):
            self.conditions.append(condition.lower())

    def remove_condition(self, condition: str) -> None:
        self.conditions = [c for c in self.conditions if c.lower() != condition.lower()]

    @property
    def active(self) -> bool:
        """On the board and able to be targeted (dead creatures are not)."""
        return not self.dead


@dataclass
class TurnState:
    actor: str = ""
    movement_budget: int = 0     # feet available this turn (speed, doubled by Dash)
    movement_used: int = 0
    diag_parity: int = 0         # diagonals taken so far, for the "5-10-5" variant
    action_used: bool = False
    bonus_used: bool = False
    disengaged: bool = False
    moves: list = field(default_factory=list)   # [{"from": [x,y], "to": [x,y], "feet": n}] for undo
    undo_locked: bool = False    # an action, reaction or roll happened since the last move
    pending: str = ""            # "death_save": must be rolled before anything else this turn


@dataclass
class Encounter:
    campaign: str
    grid: dict
    tokens: dict = field(default_factory=dict)  # id -> Token
    order: list = field(default_factory=list)   # token ids, initiative order
    round: int = 0
    turn_index: int = 0
    turn: TurnState = field(default_factory=TurnState)
    log: list = field(default_factory=list)
    status: str = "active"                       # active | ended
    system: str = "dnd5e"
    roll_mode: str = "players"                   # campaign default: players | auto
    meta: dict = field(default_factory=dict)     # map display info: name, labels, zones, colors
    version: int = SCHEMA_VERSION

    # ── derived ──
    def board(self) -> Grid:
        """The Grid, built once per map dict (not a dataclass field, so never saved)."""
        cached = self.__dict__.get("_board")
        if cached is None or cached[0] is not self.grid:
            cached = (self.grid, Grid.from_dict(self.grid))
            self.__dict__["_board"] = cached
        return cached[1]

    def token(self, ref: str) -> Token:
        """Find a token by id or (case-insensitive) name."""
        if ref in self.tokens:
            return self.tokens[ref]
        low = (ref or "").strip().lower()
        for t in self.tokens.values():
            if t.name.lower() == low or t.id.lower() == low:
                return t
        raise KeyError(f"no token {ref!r}. Tokens: {', '.join(self.tokens)}")

    @property
    def current(self) -> Token:
        return self.tokens[self.order[self.turn_index]] if self.order else None

    def at(self, pos: tuple):
        for t in self.tokens.values():
            if t.active and t.pos == tuple(pos):
                return t
        return None

    # ── serialisation ──
    def to_dict(self) -> dict:
        d = asdict(self)
        d["tokens"] = {k: asdict(v) for k, v in self.tokens.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Encounter":
        d = dict(d)
        if d.get("version") != SCHEMA_VERSION:
            raise ValueError(f"encounter schema version {d.get('version')} is not {SCHEMA_VERSION}")
        d["tokens"] = {k: Token(**v) for k, v in d.get("tokens", {}).items()}
        d["turn"] = TurnState(**d.get("turn", {}))
        return cls(**d)


# ─── Validation ───────────────────────────────────────────────────────────────

def validate(enc: Encounter) -> list:
    """Return a list of problems (empty = valid)."""
    problems = []
    try:
        grid = enc.board()
    except (KeyError, ValueError) as e:
        return [f"map: {e}"]
    seen = {}
    for tid, t in enc.tokens.items():
        if tid != t.id:
            problems.append(f"token key {tid!r} does not match id {t.id!r}")
        if t.side not in SIDES:
            problems.append(f"{t.id}: side {t.side!r} not one of {SIDES}")
        if not grid.in_bounds(t.pos):
            problems.append(f"{t.id}: {t.pos} is off the map")
        elif not grid.passable(t.pos):
            problems.append(f"{t.id}: stands in a wall at {t.square}")
        if t.active:
            if t.pos in seen:
                problems.append(f"{t.id} and {seen[t.pos]} share {t.square}")
            seen[t.pos] = t.id
        if not (0 <= t.hp <= t.max_hp):
            problems.append(f"{t.id}: hp {t.hp} outside 0..{t.max_hp}")
        if t.temp_hp < 0:
            problems.append(f"{t.id}: negative temp hp")
    for tid in enc.order:
        if tid not in enc.tokens:
            problems.append(f"turn order names unknown token {tid!r}")
    if len(set(enc.order)) != len(enc.order):
        problems.append("turn order repeats a token")
    if enc.order and not 0 <= enc.turn_index < len(enc.order):
        problems.append(f"turn index {enc.turn_index} out of range")
    if enc.roll_mode not in ("players", "auto"):
        problems.append(f"roll_mode {enc.roll_mode!r} not players|auto")
    if enc.status not in ("active", "ended"):
        problems.append(f"status {enc.status!r} not active|ended")
    return problems


# ─── Files ────────────────────────────────────────────────────────────────────

def encounter_path(campaign_dir) -> pathlib.Path:
    return pathlib.Path(campaign_dir) / "combat" / "encounter.json"


def save(enc: Encounter, path) -> None:
    """Write atomically. encounter.json always exists and is always complete:
    the previous version is copied to .bak first, then the new file replaces it
    in one os.replace."""
    problems = validate(enc)
    if problems:
        raise ValueError("refusing to save an invalid encounter: " + "; ".join(problems))
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(enc.to_dict(), f, indent=1, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    os.replace(tmp, path)


def load(path) -> Encounter:
    path = pathlib.Path(path)
    with open(path, encoding="utf-8") as f:
        enc = Encounter.from_dict(json.load(f))
    problems = validate(enc)
    if problems:
        raise ValueError(f"{path} is invalid: " + "; ".join(problems))
    return enc
