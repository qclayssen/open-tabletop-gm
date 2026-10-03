#!/usr/bin/env python3
"""
combat.py — D&D 5e combat tracker

Usage:
    python3 combat.py init <combatants_json>
        Rolls initiative for all combatants and prints turn order.
        combatants_json: JSON array of {"name": str, "dex_mod": int, "hp": int, "ac": int, "type": "pc"|"npc"}

    python3 combat.py tracker <state_json>
        Prints the current combat tracker table from a JSON state blob.

    python3 combat.py attack --atk <bonus> --ac <target_ac> --dmg <notation> [--crit]
        Resolves a single attack roll and damage.

Input / Output is JSON-friendly so the GM agent can pipe state between turns.

Example:
    python3 combat.py init '[{"name":"Flerb","dex_mod":0,"hp":12,"ac":16,"type":"pc"},
                              {"name":"Goblin","dex_mod":1,"hp":7,"ac":15,"type":"npc"}]'
"""

import json
import shlex
import sys
import re

# Aliased because `def dice` below takes the name: an unaliased `import dice`
# would leave `dice` bound to that function by the time __main__ asks it for a
# generator. Same shape, and the same reason, as scripts/tactics/roller.py.
import dice as _dice  # scripts/dice.py (on sys.path as this script's own directory)

# One module-level stream, seeded-able for a replay, built by the canonical
# factory so it carries `.seed_value` like every other generator in the tree.
# See scripts/dice.py for why the free-standing CLI needs this even though the
# tactical engine has receipts: these two are the GM's hand-typed tools, and the
# engine cannot cover them.
_RNG = _dice.new_rng()


def roll(n, sides, rng=None):
    rng = rng or _RNG
    return [rng.randint(1, sides) for _ in range(n)]


def dice(notation: str, rng=None) -> tuple[int, list[int]]:
    """Parse NdS+M notation, return (total, individual_rolls)."""
    m = re.match(r'^(\d*)d(\d+)([+-]\d+)?$', notation.strip().lower())
    if not m:
        raise ValueError(f"Bad dice notation: {notation}")
    n = int(m.group(1)) if m.group(1) else 1
    s = int(m.group(2))
    mod = int(m.group(3)) if m.group(3) else 0
    rolls = roll(n, s, rng)
    return sum(rolls) + mod, rolls


def initiative_order(combatants: list[dict], rng=None) -> list[dict]:
    """Roll d20+dex_mod for each combatant, sort descending."""
    rng = rng or _RNG
    for c in combatants:
        raw = rng.randint(1, 20)
        c["initiative_roll"] = raw
        c["initiative"] = raw + c.get("dex_mod", 0)
        c["conditions"] = []
        c["temp_hp"] = 0
    return sorted(combatants, key=lambda x: (x["initiative"], x.get("dex_mod", 0)), reverse=True)


def print_tracker(combatants: list[dict], round_num: int = 1):
    print(f"\n{'='*68}")
    print(f"  COMBAT — Round {round_num}")
    print(f"{'='*68}")
    print(f"  {'#':<3} {'Name':<18} {'Init':>5} {'HP':>8} {'AC':>4}  Conditions")
    print(f"  {'-'*62}")
    for i, c in enumerate(combatants, 1):
        hp_str = f"{c['hp']}/{c.get('max_hp', c['hp'])}"
        cond = ", ".join(c.get("conditions", [])) or "—"
        marker = "► " if i == 1 else "  "
        print(f"  {marker}{i:<2} {c['name']:<18} {c['initiative']:>5} {hp_str:>8} {c['ac']:>4}  {cond}")
    print(f"{'='*68}\n")


def resolve_attack(atk_bonus: int, target_ac: int, dmg_notation: str, is_crit: bool = False,
                   rng=None) -> dict:
    rng = rng or _RNG
    raw = rng.randint(1, 20)
    total_atk = raw + atk_bonus
    hit = raw == 20 or (raw != 1 and total_atk >= target_ac)
    crit = raw == 20

    result = {
        "d20": raw,
        "attack_bonus": atk_bonus,
        "total": total_atk,
        "target_ac": target_ac,
        "hit": hit,
        "crit": crit,
        "fumble": raw == 1,
    }

    if hit:
        dmg, rolls = dice(dmg_notation, rng)
        if crit:
            # Double the dice rolls on crit
            extra, extra_rolls = dice(dmg_notation.split("+")[0].split("-")[0], rng)
            dmg += extra
            rolls += extra_rolls
        result["damage"] = dmg
        result["damage_rolls"] = rolls
        result["damage_notation"] = dmg_notation

    return result


def format_attack(r: dict) -> str:
    lines = []
    flag = ""
    if r["crit"]:
        flag = " *** CRITICAL HIT! ***"
    elif r["fumble"]:
        flag = " *** FUMBLE — automatic miss ***"

    atk_str = f"d20({r['d20']}) + {r['attack_bonus']} = {r['total']} vs AC {r['target_ac']}"
    outcome = "HIT" if r["hit"] else "MISS"
    lines.append(f"Attack: {atk_str} — {outcome}{flag}")

    if r.get("damage") is not None:
        note = " (crit: doubled dice)" if r["crit"] else ""
        lines.append(f"Damage: {r['damage_rolls']} + mod = {r['damage']} {r['damage_notation'].split('+')[0].split('-')[0][1:]}dmg{note}")

    return "\n".join(lines)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        sys.exit(1)

    # --seed is stripped from argv before the positional arguments are read, so
    # it can appear anywhere: `init '<JSON>' --seed 7` and `init --seed 7 '<JSON>'`
    # are the same fight.
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
    rng = _dice.new_rng(seed) if seed is not None else _RNG

    cmd = argv[0]
    rest = argv[1:]

    if cmd == "init":
        combatants = json.loads(rest[0])
        # Store max_hp
        for c in combatants:
            c["max_hp"] = c["hp"]
        ordered = initiative_order(combatants, rng)
        print_tracker(ordered)
        print("Initiative rolls:")
        for c in ordered:
            print(f"  {c['name']}: d20({c['initiative_roll']}) + {c.get('dex_mod',0)} = {c['initiative']}")
        print()
        print("STATE_JSON:", json.dumps(ordered))
        if seed is not None:
            # rest[0] is the combatants JSON, whichever side of --seed it was
            # typed on, so the hint names the argument that actually held it.
            # shlex.quote rather than hand-rolled single quotes: a combatant
            # named "Grigor's Wraith" would otherwise break the paste.
            print(f"(seed {seed} - replay: python3 scripts/combat.py init "
                  f"{shlex.quote(rest[0])} --seed {seed})")

    elif cmd == "tracker":
        state = json.loads(rest[0])
        round_num = int(rest[1]) if len(rest) > 1 else 1
        print_tracker(state, round_num)

    elif cmd == "attack":
        args = rest
        atk = int(args[args.index("--atk") + 1])
        ac = int(args[args.index("--ac") + 1])
        dmg = args[args.index("--dmg") + 1]
        crit = "--crit" in args
        result = resolve_attack(atk, ac, dmg, crit, rng)
        print(format_attack(result))
        if seed is not None:
            crit_flag = " --crit" if crit else ""
            print(f"(seed {seed} - replay: python3 scripts/combat.py attack --atk {atk} --ac {ac} "
                  f"--dmg {dmg}{crit_flag} --seed {seed})")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
