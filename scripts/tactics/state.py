"""state.py: the encounter, saved to <campaign>/combat/encounter.json.

The file is the single source of truth for a grid fight: map, tokens, turn
order, round, the current actor's action economy and the combat log. Every
command loads it, works on the loaded copy, and writes it back atomically
(temp file + os.replace, previous version kept as encounter.json.bak), so a
crash mid-command leaves either the old state or the new one, never half.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import shutil
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields

from . import schemas
from .grid import Grid, label

SCHEMA_VERSION = 3
SIDES = ("pc", "ally", "enemy", "neutral")


def _score(value):
    """An ability score or level as an int, or None if it is not one.

    Sheets and hand-made tokens carry these as text often enough that a
    derived stat should refuse to guess rather than raise.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _hit_die(extra: dict) -> str:
    """The hit die notation for a token, whatever shape it was stored in.

    build_srd.py and the character layer have always written hit dice as a bare
    string ("d10"); the engine writes {"die": "d10", "remaining": n}.
    """
    hd = (extra or {}).get("hit_dice")
    if isinstance(hd, str) and hd.strip():
        return hd.strip()
    if isinstance(hd, dict):
        return str(hd.get("die") or "").strip()
    return ""


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
    effects: list = field(default_factory=list)     # timed and linked effects, see effects.py
    reactions: str = "ask"       # spell reactions (Shield, Silvery Barbs): ask | auto | off
    source: dict = field(default_factory=dict)      # {"kind": "srd", "ref": "giant-frog"} | {"kind": "sheet", ...}
    extra: dict = field(default_factory=dict)       # system-specific data (spell slots, ...)

    @property
    def spell_slots(self) -> dict:
        """The caster's spell slots: ``{"1": {"total": 2, "used": 0}, ...}``.

        A view onto ``extra["slots"]``, not a second copy of the number. The
        data lives in `extra` because the character sheet, the display sidebar
        and the encounter file all already speak that shape (see slots.py for
        the argument), and a second name for the same value is how a sheet and
        a sidebar start disagreeing. What this buys is the typed, self-describing
        way to reach it: the dataclass field the schema documents, backed by the
        one copy of the data.

        Read and write it through slots.read()/write()/spend() for anything that
        changes a count — this is the stored shape, not a normalised one.
        """
        return (self.extra or {}).get("slots") or {}

    @spell_slots.setter
    def spell_slots(self, value: dict) -> None:
        self.extra["slots"] = schemas.SPELL_SLOTS.coerce(value or {})

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

    # ── the document ──
    def to_dict(self) -> dict:
        """Serialise, checking the shape on the way out.

        Validating here as well as on load is deliberate: the engine mutates
        tokens in place, so a bad value usually arrives through a rules call
        rather than a file, and refusing to save is the last place to catch it.
        """
        d = asdict(self)
        schemas.TOKEN_SCHEMA.validate(d, self.id or "token")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Token:
        """Coerce and validate a saved token, then build it.

        Coercion first: a file that says ``"hp": "18"`` (a sheet reader writing
        text) or a condition in capitals loads instead of failing twenty
        commands into a session.
        """
        return cls(**schemas.TOKEN_SCHEMA.clean(dict(d), "token"))

    # ── derived stats ──
    def prepare_derived(self) -> dict:
        """Compute the stats a rules query needs, and report them.

        Fill-only, and idempotent: a number that came off a character sheet or
        an SRD stat block is authoritative and is never recomputed or
        overwritten here. The returned dict is a snapshot for callers and tests
        to read, not a field to store, because a derived value that survives in
        encounter.json is a stale value the day a rules table changes
        (``rules.initiative`` and ``rules.skill_bonus`` are the resolvers of
        record; this reports what they would see).

        Stateless with respect to conditions on purpose. Exhaustion and a
        cursed blade are condition modifiers applied at roll time by the
        system rules, never baked into a token: a stat that depends on a
        condition would flip value mid-fight and stop being reproducible.
        """
        scores = self.extra.get("abilities") or {}
        if not self.dex_mod and _score(scores.get("dex")) is not None:
            self.dex_mod = schemas.ability_mod(scores["dex"])
        skills = dict(self.extra.get("skills") or {})
        if "perception" not in skills and _score(scores.get("wis")) is not None:
            skills["perception"] = schemas.ability_mod(scores["wis"])
        self._estimate_max_hp(scores)
        return {
            "init_mod": self.dex_mod + (_score(self.extra.get("initiative_bonus")) or 0),
            "save_mods": dict(self.saves),
            "skill_mods": skills,
            "max_hp": self.max_hp,
            "max_hp_estimated": bool(self.extra.get("max_hp_estimated")),
        }

    def _estimate_max_hp(self, scores: dict) -> None:
        """Supply a max_hp only for a token that never had one, and label it.

        A max_hp the player can see must come from their sheet. This is the
        fallback for a hand-made token, so the engine has a number to work with
        and the display never shows a blank HP bar. It averages the first hit
        die and adds the fixed CON modifier for each later level, and it sets
        ``extra["max_hp_estimated"]`` so nothing presents it as authoritative.
        """
        if self.max_hp or self.hp or self.dead:
            return
        if (self.source or {}).get("kind") == "srd":
            return                                  # a stat block is never estimated
        die = _hit_die(self.extra)
        con, level = _score(scores.get("con")), _score(self.extra.get("level"))
        if not die or con is None or not level or level < 1:
            return
        self.max_hp = schemas.MAX_HP_FORMULA.evaluate({
            "die_average": schemas.die_average(die),
            "con_mod": schemas.ability_mod(con),
            "level": level,
        })
        self.extra["max_hp_estimated"] = True


@dataclass
class TurnState:
    actor: str = ""
    movement_budget: int = 0     # feet available this turn (speed, doubled by Dash)
    base_speed: int = None       # speed when the turn started; the budget follows later
                                 # changes (None in older files: no adjustment)
    movement_used: int = 0
    diag_parity: int = 0         # diagonals taken so far, for the "5-10-5" variant
    action_used: bool = False
    bonus_used: bool = False
    disengaged: bool = False
    moves: list = field(default_factory=list)   # [{"from": [x,y], "to": [x,y], "feet": n}] for undo
    undo_locked: bool = False    # an action, reaction or roll happened since the last move
    pending: str = ""            # "death_save": must be rolled before anything else this turn
    spells: list = field(default_factory=list)  # [{"level": n, "casting": "action"|"bonus"}] cast this turn


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
        d["tokens"] = {k: v.to_dict() for k, v in self.tokens.items()}
        d["version"] = SCHEMA_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Encounter:
        d = migrate(d)
        d = dict(d)
        d["tokens"] = {k: Token.from_dict(v) for k, v in d.get("tokens", {}).items()}
        d["turn"] = TurnState(**d.get("turn", {}))
        return cls(**d)


# ─── migration ────────────────────────────────────────────────────────────────
#
# A saved fight must keep working across an upgrade. Each step turns version N
# into N+1 in memory; the next save() (atomic, with a .bak) writes the current
# version, so an old file upgrades itself the first time the GM touches it.
#
# Two rules: a document from the future raises rather than being down-converted
# (this engine does not know what a newer writer meant by a field it invented),
# and a step never writes to disk. A fight that is only read must not be
# modified behind the GM's back.

def migrate_v1_to_v2(d: dict) -> dict:
    """v1 -> v2: the shapes the character and display layers wrote.

    ``extra`` could be absent or null, and hit dice were written as a bare die
    string (build_srd.py's ``hp_dice``) or as {"remaining", "max"} rather than
    the engine's {"die", "remaining"}. Both forms turn up in a campaign that has
    been edited by hand or merged from the character app, and the engine
    expects one shape.
    """
    out = copy.deepcopy(d)
    for token in (out.get("tokens") or {}).values():
        if not isinstance(token, dict):
            continue
        extra = token.get("extra")
        token["extra"] = extra if isinstance(extra, dict) else {}
        hd = token["extra"].get("hit_dice")
        if isinstance(hd, str) and hd.strip():
            token["extra"]["hit_dice"] = {"die": hd.strip()}
        elif isinstance(hd, dict) and "die" not in hd:
            # {"remaining": n, "max": n}: the die size is not in this shape, so
            # it is left to prepare_derived rather than invented here.
            token["extra"].pop("hit_dice")
        saves = token.get("death_saves")
        if isinstance(saves, dict):
            token["death_saves"] = {"successes": int(saves.get("successes") or 0),
                                    "failures": int(saves.get("failures") or 0)}
    return out


def migrate_v2_to_v3(d: dict) -> dict:
    """v2 -> v3: spell slots in one shape.

    Slots have been tracked since v1 but never written down anywhere, so three
    different shapes are in the wild: the engine's ``{"used", "total"}``, the
    display's ``{"remaining", "max"}`` (0.7.0 normalised both ends of that
    pipeline by hand), and a bare number where the count should be. Each of them
    arrives in ``extra["slots"]`` from a different writer — the sheet reader,
    the display, a GM editing a campaign file — and until this step they were
    only normalised at the point of use, which meant a level spent against one
    spelling of the data and a status line reading another.

    The key names a slot level, and JSON has no integer keys, so the file is
    written the way the sheet and the display both name it: a string. Nothing is
    dropped and no count is invented; a level the coercer cannot read is left
    exactly as it was for schemas.SPELL_SLOTS to name on load.
    """
    out = copy.deepcopy(d)
    for token in (out.get("tokens") or {}).values():
        if not isinstance(token, dict):
            continue
        extra = token.get("extra")
        if not isinstance(extra, dict) or "slots" not in extra:
            continue
        extra["slots"] = schemas.SPELL_SLOTS.coerce(extra["slots"])
    return out


MIGRATIONS = {(1, 2): migrate_v1_to_v2, (2, 3): migrate_v2_to_v3}


def migrate(d: dict) -> dict:
    """Run every step from the file's version up to SCHEMA_VERSION."""
    version = d.get("version", 1)        # files written before versioning existed
    if not isinstance(version, int):
        raise ValueError(f"encounter schema version {version!r} is not a number")
    if version > SCHEMA_VERSION:
        raise ValueError(f"encounter schema version {version} is newer than this engine "
                         f"({SCHEMA_VERSION}); update open-tabletop-gm before opening it")
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get((version, version + 1))
        if step is None:
            raise ValueError(f"no migration from encounter schema v{version} to v{version + 1}")
        d = step(d)
        d["version"] = version + 1
        version += 1
    return d


def prepare_all(enc: Encounter) -> dict:
    """Derived stats for every token in the encounter, keyed by token id."""
    return {tid: t.prepare_derived() for tid, t in enc.tokens.items()}


# The schema and the dataclass must not drift: a Token field with no field
# description is a value that reaches the file unchecked, and a description for
# a field that does not exist hides a rename. Both fail at import.
_UNKNOWN = set(schemas.TOKEN_SCHEMA.fields) ^ {f.name for f in dataclass_fields(Token)}
if _UNKNOWN:
    raise RuntimeError(f"TOKEN_SCHEMA and Token disagree about: {', '.join(sorted(_UNKNOWN))}")


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
        if t.reactions not in ("ask", "auto", "off"):
            problems.append(f"{t.id}: reactions {t.reactions!r} not ask|auto|off")
        if t.extra.get("slots"):
            try:
                schemas.SPELL_SLOTS.validate(t.extra["slots"], f"{t.id}: spell slots")
            except schemas.SchemaError as e:
                problems.append(str(e))
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
