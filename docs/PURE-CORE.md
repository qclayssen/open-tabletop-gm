# The pure rules core

`scripts/tactics/purecore.py` is the engine's rules as a function:

```python
from tactics import purecore

new_state, events = purecore.apply(state, {"cmd": "attack", "token": "kairos",
                                           "target": "frog-1", "seed": 7})
```

`apply(state: dict, command: dict, *, roller=None) -> (state, events)`

- `state` is an encounter dict (`Encounter.to_dict()`, the shape of
  `combat/encounter.json`). It is never mutated; the new state is a fresh dict.
- `command` is the CLI verb plus its arguments by name (`cmd`, `token`, `square`,
  `target`, `attack`, `reactions`, `advantage`, `seed`, `rolls`, ...). The full
  list is in the module docstring.
- `events` is plain JSON data: one `{"type": "log", round, actor, kind, text, rolls}`
  per fight-log entry the command added, then one `{"type": "result", text, data}`.
- A refusal raises `CombatError`. A command waiting on a player's die or choice
  raises `PendingRoll` or `DecisionNeeded`. Either way the caller keeps its old
  state, which is the CLI's contract too: a command that did not finish changed
  nothing.

## What "pure" means here

The core reads no clock, opens no socket, starts no process, writes no file and
pushes nothing to the display. Randomness comes only from the roller you inject
or a `seed` in the command; with neither, a command that needs a die raises
`ValueError` instead of drawing from OS entropy. So the same seed, state and
command give byte-identical output (`json.dumps(..., sort_keys=True)`), and a
fight can be replayed from its commands. `tests/test_tactics_purecore.py` pins
both claims, the first by running the core with `urllib`, `subprocess`, `socket`,
`tactics.sync` and every filesystem write blocked.

Roll receipts are the one write the engine used to do mid-command. The core parks
them on `enc.receipt_sink` (`core.log`) and the edge writes them.

## What a seed reproduces

A `seed` replays only under CPython: it feeds `random.Random`, whose stream no
other language shares. A trace therefore stores the dice faces actually rolled,
and a replay (in Python or any other language) feeds them back through
`roller=` instead of re-seeding. The one draw that is not a die, the pick
`choose auto` makes among options, comes from `roller.policy_rng` (an object
with `random()`); unset, it is the fight's crc32 stream, as before. Output is
plain JSON: a value JSON cannot hold in `data` raises `TypeError`.

`apply(..., receipts=True)` adds the parked roll receipts to the events as
`{"type": "receipt", actor, kind, rolls}`; the default output is unchanged.

Comparing outputs: Python against Python is byte for byte
(`json.dumps(..., sort_keys=True)`, which the tests pin across processes with
different `PYTHONHASHSEED`). Across languages, compare parsed JSON, not bytes:
Python's `round()` rounds halves to even and floats print as `3.0`, not `3`.

## Pure queries

`reachable(state, token)`, `area(state, shape, size, caster, target)`,
`cover(state, attacker, target)` and `line_of_sight(state, a, b)` answer from the
state alone. The read-only commands (`status`, `options`, `targets`, `sight`,
...) also go through `apply` and return the state unchanged.

## The edges

`purecore.execute(enc, command, roller) -> (text, data)` is the same dispatch on
an `Encounter` already in memory. `tactics/cli.py` is the edge that owns what the
core must not touch: it loads and saves `encounter.json`, resolves the seed and
the pending-roll file, writes the receipts, syncs the tracker and calls
`sync.push_display`. The localdm bridge runs the CLI, so it reaches the core the
same way. `start`, `end`, `rest`, formations, scenes and the budget tools touch
the campaign on disk by nature and stay in the CLI.

Keeping `start` (initiative), `end`, `rest`, scenes and formations outside the
core is a debt, not a design: phase 16 (TSP-12) must give them an oracle
(function-level traces) before the port can claim them.

## Golden traces

`tests/traces/` holds the core's behaviour as data, so a second implementation (the
TypeScript port, phases 15-16) can be graded against it without running Python.
`scripts/tactics/traces.py` records and replays them; `tests/test_tactics_traces.py`
is the replay test.

Regenerate everything, from a clean tree, with one command (about two minutes, it
runs every `test_tactics_*` test once):

```bash
python3 scripts/tactics/traces.py --generate
```

Running it twice gives byte-identical files, whatever `PYTHONHASHSEED` is. Regenerate
in the same PR as any change that alters rules output (D-16: parity before
features, and the Python engine is the oracle). Do not regenerate to make a failing
replay pass: a red replay asks whether the rules changed on purpose, and if they did
not, the trace is right. Never edit a trace by hand. The manifest names the commit the
traces were recorded from, so commit the code first and generate from that tree
(`--generate` refuses a dirty tree; `--allow-dirty` is for scratch runs).

### Files

| File | Holds |
|---|---|
| `manifest.json` | every test id in `tests/test_tactics_*.py`, mapped to trace ids or to the reason it has none; the engine commit (stored once, here); counts; a sha256 per file |
| `test_tactics_<module>.json` | the `apply` traces recorded from that module's tests |
| `grid-random.json` | 500 seeded randomised grid cases |

Each file is one JSON document with one canonical record per line, so a diff names
the record. Every record is `receipts.canonical` bytes (sorted keys, compact UTF-8).

### A record

```json
{"schema": "tactics-trace/1", "id": "engine-3fa9c1d20b7e", "kind": "apply",
 "source": "tests/test_tactics_engine.py::test_x",
 "state": {}, "command": {"cmd": "attack", "token": "frog-1", "target": "kairos"},
 "seed": 3, "tape": [[20, 8], [4, 2]], "policy_draws": [],
 "result": {"state": {}, "events": []}}
```

- `kind`: `apply` is `purecore.apply(state, command, roller=..., receipts=True)`.
  `query` is one of the pure queries on an encounter state (`command.query` names it
  and carries its arguments). `grid` is the same geometry on a bare grid dict.
- `tape`: the dice faces actually rolled, `[sides, face]` in call order, including the
  second d20 of advantage. `seed` only says where they came from under CPython.
- `policy_draws`: the `random()` values `choose auto` used to pick an option.
- `command.rolls`, `player_roll` and `for_me` are the player's own faces and the
  roller's mode; they are data, not dice, so they are not on the tape.
- `result` is `{"state", "events"}`, or `{"raises": "CombatError" | "PendingRoll" |
  "DecisionNeeded" | "BadFace", "message": ..., "pending": ...}` where `pending` is the
  exception's `to_dict()` (a pause for a die or a choice). A query or grid record has
  `{"value": ...}` instead. Refusals are traces: the port must refuse in the same places.
- `receipts=True` is part of the contract, so the events include the
  `{"type": "receipt", actor, kind, rolls}` entries the edge writes to disk.

### Replaying, in any language

1. Build the roller from the record: engine dice come from `tape` in order (each
   entry is a `randint(1, sides)`; refuse an entry whose `sides` differ from what the
   engine rolls), the player's faces from `command.rolls`, the pick from
   `policy_draws`. Nothing is ever seeded, so no Mersenne Twister is needed.
2. Run the command on `state`.
3. The tape and `policy_draws` must be fully consumed. A short or long tape is an
   error, not a pass.
4. Compare `state'` and `events` as **parsed JSON**, not bytes: Python's `round()`
   rounds halves to even and prints `3.0` where JavaScript prints `3`. Byte equality
   is the Python-against-Python check only (`traces.replay` against the stored
   result, which is what the replay test asserts).

Two things to know about a recorded state. Keys are sorted, including the `tokens`
dictionary, so the iteration order of tokens (and of any other dictionary the engine
walks, such as the order of an area's `affected` list) is alphabetical in a trace.
Each stored result is what the core returns for exactly that state, so a port that
parses the file in order reproduces it. And a sheet token's `source.path` is rewritten
from the temporary campaign it was read from to `<root>/campaigns/...`.

### What is recorded, and what is not

Every test in `tests/test_tactics_*.py` appears in the manifest. A test either has
traces or a reason. The recorder wraps the engine entry points the tests call
(`engine.move|attack|multiattack|check|dash|disengage|dodge|stand_up|end_turn|
death_save|undo_move`, the read-only `reachable|approach|preview_move|attack_options`,
`spells.cast|use_action|castable`, `ai.choose|options`, `policy.choose_auto`,
`actions.hide|escape|ready|trigger|help_action`, `sight.sight`, `statecard.statecard`)
and `purecore.execute` (which every CLI-driven test reaches), from the outside while
pytest runs with `TACTICS_RECORD_TRACES=<dir>` set (see `tests/conftest.py`; without
it the hook does nothing). A call is kept only if `purecore.apply` reproduces what the
live call did. A call that cannot be expressed as a command (`move(as_reaction=True)`,
`cast(readied=...)`, `choose(limit=...)`), that raises a Python-specific error, or does
not replay is not recorded, and its test says why in the manifest
(`unrecorded_calls`). A whole-fight test keeps at most 20 evenly spaced records, and
the manifest says how many it made (`sampled`).

Not covered, and the debt phase 16 (TSP-12) must close with function-level traces:
`start` (initiative dice), `end`, `rest`, scenes, formations, the budget tools and the
encounter generator. They stay in the CLI, outside the core, so `apply` cannot supply
their oracle. Only CPython can reproduce a seed, so nothing in a trace depends on one;
there are no CPython-only traces.

The randomised grid cases are generated by `traces.generate_grid_cases(seed, n,
start=0)`; case `i` has its own rng, so phase 15 can draw 10,000 from the same code
(`start` skips ahead). Boards are 8x8 to 20x20 with 0-30% walls and some difficult
ground, water, features and hazards; 1-4 tokens of size M or L; and every query
(`reachable`, `cover`, `line_of_sight`, `area` for sphere, cylinder, cone, line and
cube) at both the encounter and the bare-grid level. The generated inputs are stored,
so replay uses no RNG.
