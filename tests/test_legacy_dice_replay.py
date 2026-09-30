"""test_legacy_dice_replay.py: a roll from these two CLIs must be re-runnable.

The tactical engine already proves its own rolls: `Roller.rng` is an injected
`random.Random` and `scripts/tactics/receipts.py` hash-chains every roll into an
append-only log. These two scripts are the other half, and the engine cannot
cover them, because they are the GM's hand-typed tools: a `dice.py d20+5` run
against the terminal is a real roll that the engine never sees and never
records.

Both used the `random` module's global generator, so the same command produced a
different result every time and a transcript quoting one could not be checked by
anyone, including the GM who ran it. A seed is the smallest thing that makes the
claim checkable: same seed, same faces, and the seed printed with the result so
the reader has the command to re-run.

These are also the only two scripts in the tree still reading the global
generator, so the check that they do not go back to it is worth stating.
"""
from __future__ import annotations

import pathlib
import random
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import combat  # noqa: E402
import dice  # noqa: E402

PARTY = ('[{"name":"Kairos","dex_mod":3,"hp":12,"ac":16,"type":"pc"},'
         '{"name":"Goblin","dex_mod":1,"hp":7,"ac":15,"type":"npc"}]')

# The property is not "no `random.` anywhere". Two lines are allowed to touch the
# module, and they are the only ones that should ever: `_RNG = random.Random()` at
# construction, and `rng = random.Random(seed)` under `--seed`. Both build a fresh
# generator. Everything else that reaches for `random.` reads the shared global
# generator, which is the bug this file exists to prevent. So strip the two
# construction forms, then assert no module attribute access survives.
_CONSTRUCTOR_CALL = re.compile(r"\brandom\.Random\(")
_MODULE_ACCESS = re.compile(r"(?<![\w.])random\s*\.")


def _reads_global_generator(line: str) -> bool:
    """True if `line` reads the global `random` generator rather than building one."""
    return bool(_MODULE_ACCESS.search(_CONSTRUCTOR_CALL.sub("", line)))


def _run(*args: str) -> str:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "dice.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def _combat(*args: str) -> str:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "combat.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


# ─── the property that matters: a seed replays ────────────────────────────────

def test_the_same_seed_lands_the_same_faces():
    assert dice.run("2d6+3", rng=random.Random(42)) == dice.run("2d6+3", rng=random.Random(42))


def test_a_seeded_advantage_roll_replays():
    """Advantage consumes two rolls from the stream, so this is where a naive
    re-seed that forgets the second draw would drift."""
    a = dice.run("d20 adv", rng=random.Random(5))
    b = dice.run("d20 adv", rng=random.Random(5))
    assert a == b


def test_a_seeded_keep_highest_roll_replays():
    assert dice.run("4d6kh3", rng=random.Random(7)) == dice.run("4d6kh3", rng=random.Random(7))


def test_a_seeded_roll_is_in_range():
    """A replay that returns 37 is not a replay. Cheap, and it catches a broken
    generator as well as a wrong one."""
    for seed in range(30):
        total = dice.run("d20+5", rng=random.Random(seed))
        assert 6 <= total <= 25, (seed, total)


def test_different_seeds_actually_move():
    """A generator that ignored its seed would pass every test above. Assert the
    seeds spread, over enough seeds that this cannot flake."""
    totals = {dice.run("d20", rng=random.Random(s)) for s in range(60)}
    assert len(totals) > 10, totals


# ─── the generators themselves ───────────────────────────────────────────────

def test_roll_dice_takes_an_injected_generator():
    assert dice.roll_dice(3, 6, random.Random(1)) == dice.roll_dice(3, 6, random.Random(1))


def test_neither_script_reads_the_global_random_generator():
    """The regression that produced this whole file. Grepping the source beats
    trusting that nobody will reach for `random.` again."""
    for name in ("dice.py", "combat.py"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        for line in source.splitlines():
            assert not _reads_global_generator(line), f"{name}: {line.strip()}"


def test_the_global_generator_check_would_catch_a_regression():
    """A guard that cannot fail guards nothing. The check above is a source scan,
    so prove it on the exact line someone would reintroduce."""
    assert _reads_global_generator("    return [random.randint(1, sides) for _ in range(n)]")
    assert _reads_global_generator("    raw = random.randint(1, 20)")

    # and it must not cry wolf over the two sanctioned forms
    assert not _reads_global_generator("_RNG = random.Random()")
    assert not _reads_global_generator("    rng = random.Random(seed) if seed is not None else _RNG")
    assert not _reads_global_generator("    return [rng.randint(1, sides) for _ in range(n)]")


def test_an_unseeded_call_still_works():
    """Every existing caller passes no rng. That has to keep working."""
    assert 6 <= dice.run("d20+5") <= 25
    assert 1 <= len(combat.roll(4, 6)) == 4
    order = combat.initiative_order([{"name": "A", "dex_mod": 0, "hp": 1, "ac": 10}],
                                    random.Random(3))
    assert order[0]["initiative_roll"] >= 1


# ─── combat.py: a whole fight from one seed ──────────────────────────────────

def test_seeded_initiative_is_replayable():
    import json
    a = combat.initiative_order(json.loads(PARTY), random.Random(11))
    b = combat.initiative_order(json.loads(PARTY), random.Random(11))
    assert [c["initiative_roll"] for c in a] == [c["initiative_roll"] for c in b]


def test_seeded_attack_including_damage_is_replayable():
    a = combat.resolve_attack(20, 5, "2d6+1", rng=random.Random(1))
    b = combat.resolve_attack(20, 5, "2d6+1", rng=random.Random(1))
    assert a == b
    assert a["damage_rolls"] == b["damage_rolls"]


def test_seeded_damage_follows_the_attack_roll_from_one_stream():
    """The damage dice must come from the same seeded stream as the d20, or a
    replay reproduces the hit and not the hurt."""
    r = combat.resolve_attack(20, 5, "3d6", rng=random.Random(9))
    assert r["hit"] and len(r["damage_rolls"]) == 3


# ─── the CLI is where the GM actually meets this ─────────────────────────────

def test_the_cli_prints_the_seed_and_a_replay_command():
    out = _run("d20+5", "--seed", "42")
    assert "seed 42" in out
    assert "--seed 42" in out


def test_the_cli_replays_identically():
    assert _run("2d6+3", "--seed", "42") == _run("2d6+3", "--seed", "42")


def test_silent_prints_only_the_number():
    """`dice.py d20 --silent` is documented in SKILL-scripts.md as returning an
    integer, so the replay line must not break a caller parsing it."""
    out = _run("d20", "--silent", "--seed", "42").strip()
    assert out.isdigit(), out


def test_a_non_integer_seed_is_refused_rather_than_guessed():
    for bad in (["--seed", "abc"], ["--seed"]):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "dice.py"), "d20", *bad],
                              capture_output=True, text=True, encoding="utf-8")
        assert proc.returncode != 0, bad


def test_the_combat_cli_replays_a_fight():
    first = _combat("init", PARTY, "--seed", "11")
    second = _combat("init", PARTY, "--seed", "11")
    state = [ln for ln in first.splitlines() if ln.startswith("STATE_JSON:")]
    assert state and state == [ln for ln in second.splitlines() if ln.startswith("STATE_JSON:")]
    assert "seed 11" in first


def test_the_seed_may_precede_the_positional_arguments():
    """`init --seed 11 '<JSON>'` and `init '<JSON>' --seed 11` are the same fight;
    a GM will type it both ways."""
    a = _combat("init", "--seed", "11", PARTY)
    b = _combat("init", PARTY, "--seed", "11")
    assert a == b


def test_the_printed_replay_command_is_itself_re_runnable():
    """The hint is the whole point: it has to be pasteable, not decorative. This
    was a real bug once, when `init --seed N '<JSON>'` printed a hint built from
    the wrong argv slot. Runs the printed text back through the shell and demands
    the output match the run that printed it."""
    cases = [
        (_run, ("2d6+3", "--seed", "42")),
        (_combat, ("init", PARTY, "--seed", "11")),
        (_combat, ("init", "--seed", "11", PARTY)),
        (_combat, ("attack", "--atk", "20", "--ac", "5", "--dmg", "2d6+1", "--seed", "1")),
    ]
    for runner, args in cases:
        first = runner(*args)
        hint = first.strip().splitlines()[-1]
        # Shape: (seed N - replay: <command>)
        match = re.fullmatch(r"\(seed (\d+) - replay: (.*)\)", hint)
        assert match, hint
        command = match.group(2)
        again = subprocess.run(command, shell=True, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        assert again.returncode == 0, (command, again.stderr)
        assert again.stdout == first, command


def test_the_attack_cli_replays():
    args = ("attack", "--atk", "20", "--ac", "5", "--dmg", "2d6+1", "--seed", "1")
    assert _combat(*args) == _combat(*args)


def test_tracker_still_works_and_needs_no_seed():
    state = _combat("init", PARTY).split("STATE_JSON: ")[1].strip()
    out = _combat("tracker", state, "2")
    assert "COMBAT" in out


def test_the_documented_invocations_still_work():
    """SKILL-scripts.md is the GM's reference for these two scripts. Every form
    listed there has to keep working, seeded or not."""
    for args in (("d20+5",), ("2d6+3",), ("4d6kh3",), ("d20", "adv"), ("d20+3", "dis")):
        assert _run(*args).strip(), args
