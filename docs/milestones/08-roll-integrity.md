# Roll integrity: secrets seed, seeded PRNG

## Decision

Every dice stream is a `random.Random` whose seed is drawn from `secrets`
(`dice.new_rng()`). The stream is a seeded PRNG; the seed is the only entropy
and it is kept on the object (`rng.seed_value`) so it can be recorded.

- `scripts/dice.py` holds one default stream per process, injectable: pass
  `rng=` to `run()` / `roll_dice()`, or `set_rng()` / `seed_default(n)` to make
  out-of-combat rolls reproducible from a test or harness without patching the
  `random` module.
- `Roller.rng` (engine combat) defaults to `dice.new_rng()`, so both paths share
  one policy. `pending.json` seeds and receipts are unchanged.

## Why not the alternatives

- **Unseeded randomness**: a roll quoted in a transcript could never be
  re-run, so nobody could audit it. A recorded seed makes any roll replayable.
- **"Quantum dice" / a hardware or remote entropy service**: adds a dependency
  and a network or device failure mode (stdlib + Flask only), and buys nothing
  at the table. The OS entropy pool behind `secrets` is already unpredictable to
  players; what was missing was replayability, which a seed gives.
- **Deriving the seed from state or time**: guessable, so a player could
  predict faces.

## Non-goals

No replay CLI. `dice.py --seed N` already re-runs one roll; a replay tool for
whole sessions is not built.

## Seams

`scripts/localdm/play.py` `_ability_check` still calls `random.randint(1, 20)`
directly. That is the interception point the outer dice-lens harness
(`ForcedFaces`, keyed on caller `_ability_check`) patches, so it is left alone.
