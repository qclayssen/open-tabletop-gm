#!/usr/bin/env python3
"""
dice.py — D&D 5e dice roller

Usage:
    python3 dice.py <notation> [--silent]

Notation supported:
    d20               single d20
    2d6               2 six-sided dice, sum
    d20+5             roll + flat modifier
    4d6kh3            roll 4d6, keep highest 3 (ability score generation)
    4d6kl3            roll 4d6, keep lowest 3
    d20 adv           advantage: roll twice, take higher
    d20 dis           disadvantage: roll twice, take lower
    d20+3 adv         advantage with modifier
    2d6+3             multiple dice + modifier

Output (unless --silent):
    Rolls: [x, x, x]  →  Total: N
    For advantage/disadvantage, shows both rolls and which was taken.
    Flags natural 20 (CRITICAL HIT) and natural 1 (FUMBLE) on single d20s.

Reproducibility:
    --seed N   roll from a seeded stream and print the seed with a replay
               command, so a roll quoted in a transcript can be re-run and
               land the same faces. Same shape as the tactical engine's
               Roller.rng and its receipts; this is the free-standing CLI's
               half of that idea, since the engine cannot cover a roll the
               GM typed by hand.
"""

import random
import re
import secrets
import sys

def new_rng(seed=None):
    """A PRNG for dice. With no seed, the seed is drawn from `secrets` (the OS
    entropy pool), so an unseeded stream is unpredictable; with one, the stream
    is replayable. The seed is kept on the returned object as `.seed_value` so a
    caller can quote it. See docs/milestones/08-roll-integrity.md."""
    if seed is None:
        seed = secrets.randbits(64)
    rng = random.Random(seed)
    rng.seed_value = seed
    return rng


# One stream per process, so an unseeded CLI run is a single sequence rather
# than a fresh generator per call. Callers that need a replay pass `rng=` to
# run()/roll_dice(), or swap the default with set_rng() / seed_default().
_RNG = new_rng()


def get_rng():
    return _RNG


def set_rng(rng):
    """Replace the process default stream; returns the previous one so a test
    can restore it."""
    global _RNG
    prev, _RNG = _RNG, rng
    return prev


def seed_default(seed):
    """Make the process default stream reproducible from `seed`."""
    set_rng(new_rng(seed))
    return _RNG


def parse_notation(notation: str):
    """Parse dice notation string. Returns (num_dice, die_size, modifier, keep_mode, keep_count)."""
    notation = notation.strip().lower()

    # Strip advantage/disadvantage suffix
    adv = "adv" in notation or "advantage" in notation
    dis = "dis" in notation or "disadvantage" in notation
    notation = re.sub(r'\s*(adv|dis|advantage|disadvantage)\w*', '', notation).strip()

    # Match: [N]d[S][kh/kl N][+/-M]
    pattern = r'^(\d*)d(\d+)(?:(kh|kl)(\d+))?([+-]\d+)?$'
    m = re.match(pattern, notation.replace(' ', ''))
    if not m:
        raise ValueError(f"Cannot parse dice notation: '{notation}'")

    num_dice = int(m.group(1)) if m.group(1) else 1
    die_size = int(m.group(2))
    keep_mode = m.group(3)       # 'kh' or 'kl' or None
    keep_count = int(m.group(4)) if m.group(4) else None
    modifier = int(m.group(5)) if m.group(5) else 0

    return num_dice, die_size, modifier, keep_mode, keep_count, adv, dis


def roll_dice(num_dice, die_size, rng=None):
    rng = rng if rng is not None else _RNG
    return [rng.randint(1, die_size) for _ in range(num_dice)]


def format_modifier(mod):
    if mod == 0:
        return ""
    return f" + {mod}" if mod > 0 else f" - {abs(mod)}"


def run(notation: str, silent: bool = False, rng=None) -> int:
    num_dice, die_size, modifier, keep_mode, keep_count, adv, dis = parse_notation(notation)

    # Advantage / disadvantage (only meaningful for single d20)
    if adv or dis:
        roll_a = roll_dice(num_dice, die_size, rng)
        roll_b = roll_dice(num_dice, die_size, rng)
        total_a = sum(roll_a) + modifier
        total_b = sum(roll_b) + modifier
        chosen = max(total_a, total_b) if adv else min(total_a, total_b)
        label = "ADV" if adv else "DIS"
        if not silent:
            print(f"[{label}] Roll A: {roll_a} = {total_a}{format_modifier(modifier)}")
            print(f"[{label}] Roll B: {roll_b} = {total_b}{format_modifier(modifier)}")
            taken = "A" if (adv and total_a >= total_b) or (dis and total_a <= total_b) else "B"
            print(f"Takes roll {taken} → Total: {chosen}")
        return chosen

    rolls = roll_dice(num_dice, die_size, rng)

    # Keep highest / lowest
    if keep_mode and keep_count:
        sorted_rolls = sorted(rolls, reverse=(keep_mode == 'kh'))
        kept = sorted_rolls[:keep_count]
        dropped = sorted_rolls[keep_count:]
        result = sum(kept) + modifier
        if not silent:
            kept_str = " + ".join(str(r) for r in kept)
            drop_str = f"  (dropped: {dropped})" if dropped else ""
            mod_str = format_modifier(modifier)
            print(f"Rolls: {rolls}{drop_str}")
            print(f"Kept ({keep_mode}{keep_count}): [{kept_str}]{mod_str} = {result}")
        return result

    result = sum(rolls) + modifier
    if not silent:
        if num_dice == 1 and die_size == 20:
            raw = rolls[0]
            flag = ""
            if raw == 20:
                flag = "  *** CRITICAL HIT (nat 20)! ***"
            elif raw == 1:
                flag = "  *** FUMBLE (nat 1)! ***"
            mod_str = format_modifier(modifier)
            print(f"Roll: {raw}{mod_str} = {result}{flag}")
        else:
            mod_str = format_modifier(modifier)
            print(f"Rolls: {rolls}{mod_str} = {result}")
    return result


if __name__ == "__main__":
    argv = list(sys.argv[1:])

    seed = None
    if "--seed" in argv:
        i = argv.index("--seed")
        if i + 1 >= len(argv):
            print("--seed needs an integer: --seed 7")
            sys.exit(1)
        try:
            seed = int(argv[i + 1])
        except ValueError:
            print(f"--seed needs an integer, got '{argv[i + 1]}'")
            sys.exit(1)
        del argv[i:i + 2]

    args = [a for a in argv if a != "--silent"]
    silent = "--silent" in argv

    if not args:
        print("Usage: python3 dice.py <notation> [--seed N]  e.g. d20+5  2d6  4d6kh3  d20 adv")
        sys.exit(1)

    notation = " ".join(args)
    rng = new_rng(seed) if seed is not None else _RNG
    result = run(notation, silent=silent, rng=rng)
    if seed is not None and not silent:
        # A roll quoted in a transcript is only auditable if the reader can
        # re-run it and land the same faces, so the seed goes out with it.
        # The path is spelled out because a bare `dice.py` is not re-runnable
        # from the repo root, which is where the reader is.
        print(f"(seed {seed} - replay: python3 scripts/dice.py {notation} --seed {seed})")
    if silent:
        print(result)
