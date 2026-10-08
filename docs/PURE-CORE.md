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
