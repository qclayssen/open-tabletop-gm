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

WHAT CHANGED, AND WHY IT IS THE SAME FILE
-----------------------------------------
The closing line of the original version of this docstring was "these are also the
only two scripts in the tree still reading the global generator", and it was
false within weeks. `localdm/play.py` was rolling a d20 skill check off the global
generator and `world.py` was building its own `random.Random`, and neither was in
the guard -- because the guard named two files rather than walking the directory.
A list of two filenames is not a policy; it is two filenames.

So the guard here now walks `scripts/`, decides by AST rather than by line, and
carries an explicit allow-list whose entries are recorded debt rather than
unexamined omissions. `play.py` and `world.py` were on that list when it was
written and are off it now, which is the whole claim: the difference between this
version and the last one is not that more files are checked, it that a file which
did not exist when the guard was written is checked too.

The replay half of this file is unchanged. Fixing the roll sites did not alter
what any command produces: `dice.new_rng(seed)` is `random.Random(seed)` plus a
`.seed_value` attribute, and an unseeded generator was already OS-seeded.
"""
from __future__ import annotations

import ast
import pathlib
import random
import re
import runpy
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import combat  # noqa: E402
import dice  # noqa: E402

PARTY = ('[{"name":"Kairos","dex_mod":3,"hp":12,"ac":16,"type":"pc"},'
         '{"name":"Goblin","dex_mod":1,"hp":7,"ac":15,"type":"npc"}]')

# The property is not "no `random.` anywhere". In `dice.py` two lines are allowed to
# touch the module, and they are the only ones that should ever: `_RNG =
# random.Random()` at construction, and `rng = random.Random(seed)` under `--seed`.
# Both build a fresh generator. Everything else that reaches for `random.` reads the
# shared global generator, which is the bug this file exists to prevent. So strip
# the two construction forms, then assert no module attribute access survives.
#
# `combat.py` no longer has that exemption. It used to, with the same two lines, and
# that is how a second path learned how a seed becomes a generator; it is now the
# only one of the pair that builds nothing itself. `_OWN_CONSTRUCTOR` below holds it
# to that.
_CONSTRUCTOR_CALL = re.compile(r"\brandom\.Random\(")
_MODULE_ACCESS = re.compile(r"(?<![\w.])random\s*\.")
_OWN_CONSTRUCTOR = re.compile(r"\brandom\.Random\b|\bimport\s+random\b")


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


# ─── the guard, which used to name two files ──────────────────────────────────
#
# This used to be:
#
#     for name in ("dice.py", "combat.py"):
#         source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
#
# Grepping the source beats trusting that nobody will reach for `random.` again,
# and that instinct was right. The file list was the bug: a guard that inspects
# two filenames is structurally unable to see a third module, so every file added
# after #117 was unguarded by construction rather than by accident. That is how
# `localdm/play.py` came to roll a d20 off the global generator -- the single most
# consequential roll in the product, and the one whose fallback arm fires exactly
# when no display is registered, so the table got the unreproducible roll.
#
# So this walks `scripts/`. Two decisions, both load-bearing:
#
# 1. An **AST** walk, not a line scan. A line scan over the tree flags 12 lines
#    across 8 files, and 8 of those 12 are not violations: `tactics/cli.py`
#    quotes the line #117 removed inside its own docstring, `tactics/receipts.py`
#    and `tactics/roller.py` say `random.Random` in prose and in annotations, and
#    `oracle.py` annotates with the *string* `"random.Random | None"`. A guard
#    that cries wolf over 8 false positives gets deleted, which is worse than the
#    two-file version. `test_dice_seed_integrity.py::_random_module_uses` was
#    already walking the AST for the same reason, and this is that approach
#    applied to the whole tree.
#
# 2. An explicit allow-list, one entry per known debtor, each carrying its
#    reason. Not "the files we did not look at" -- the files we looked at, found
#    wanting, and declined to fix in this change, so that adding a module cannot
#    silently join the exempt set and adding a *reader* is a red test rather than
#    a code-review habit.

#: Modules permitted to read the module-level generator. Every entry is recorded
#: debt with an owner, not a permission. `play.py` and `world.py` were on this
#: list when this guard was written and are now off it, which is the proof the
#: walk is doing something a two-name list could not.
_GLOBAL_GENERATOR_EXEMPT = {
    "scripts/localdm/stall.py":
        "stall wording picks one of several phrasings at random. Not a player-facing "
        "roll, so it has no seed to quote and no receipt to appear in. Reported in "
        "dnd-gm#306 as lower severity, deliberately not folded into the play.py fix.",
    "scripts/npc_rename.py":
        "generates placeholder NPC names. Not a roll: there is no DC, no bonus and "
        "nothing at the table depends on the value. Reported in dnd-gm#306 as lower "
        "severity, deliberately not folded in.",
    "scripts/localdm/handoff.py":
        "sets seed for deterministic continuity benchmark fixture; not a player-facing "
        "roll, so it has no seed to quote and no receipt to appear in. Reported in "
        "dnd-gm#306 as lower severity, deliberately not folded in.",
}

#: Modules permitted to *construct* a generator outside `dice.new_rng()`.
#: `dice.py` is not listed because it is the seam. `combat.py` and
#: `tactics/cli.py` are absent because #292 and #117 routed them, and their
#: absence is asserted below so neither can drift back.
_CONSTRUCTOR_EXEMPT = {
    "scripts/localdm/stall.py": "same reason as the reader exemption: wording, not a roll.",
    "scripts/oracle.py":
        "reads a caller-supplied rng for a seeded oracle event. Owned by dnd-gm#291 "
        "/ open-tabletop-gm#229, which is editing this file; named here rather than "
        "silently skipped.",
    "scripts/world_queue.py":
        "a world event pick, not a rules roll. Reported in dnd-gm#306, not folded in.",
    "scripts/tactics/policy.py":
        "seeds by `zlib.crc32` of the situation, so it is deterministic by "
        "construction and carries nothing to quote.",
    "scripts/localdm/autopilot.py":
        "same `crc32` construction: deterministic by construction, so a seed would "
        "be a restatement of the input.",
    "scripts/tactics/roller.py":
        "the injected engine stream, `field(default_factory=_dice.new_rng)`. It "
        "builds no generator of its own; the annotation is what this sees.",
}

#: Modules #117 and #292 routed, named so their absence is a pinned fact.
_MUST_NOT_CONSTRUCT = ("scripts/combat.py", "scripts/tactics/cli.py")

_SCRIPTS = ROOT / "scripts"


def _repo_scripts() -> list[pathlib.Path]:
    return sorted(p for p in _SCRIPTS.rglob("*.py") if "__pycache__" not in p.parts)


def _relative(path: pathlib.Path) -> str:
    """Repo-relative, POSIX-separated, so an allow-list entry survives a move."""
    return path.relative_to(ROOT).as_posix()


def _random_reads(path: pathlib.Path) -> list:
    """`random.<name>` this module actually evaluates, where `<name>` is not the
    constructor.

    `Random` itself is excluded because `rng: Optional[random.Random]` is a type
    annotation, not a read of the shared generator, and flagging those would put
    four files on the exempt list for doing nothing wrong. Every *other* attribute
    is a read of the one shared generator, and that is the whole defect class.
    """
    found: list = []

    class Visitor(ast.NodeVisitor):
        def visit_Attribute(self, node):                       # noqa: N802
            if (isinstance(node.value, ast.Name) and node.value.id == "random"
                    and node.attr != "Random"):
                found.append((node.lineno, f"random.{node.attr}"))
            self.generic_visit(node)

    Visitor().visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def _random_constructions(path: pathlib.Path) -> list:
    """`random.Random(...)` call sites -- a second place that knows a seed."""
    found: list = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node):                           # noqa: N802
            if (isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "random" and node.func.attr == "Random"):
                found.append((node.lineno, "random.Random(...)"))
            self.generic_visit(node)

    Visitor().visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def test_the_walk_actually_covers_the_tree():
    """The walk is the fix, so the walk has to be shown to reach.

    A guard over two hardcoded names passed this test too. Asserting the file
    count and that a module added long after #117 -- `localdm/play.py` -- is in
    the walked set is what makes "it walks now" a fact rather than an intention.
    """
    walked = {_relative(p) for p in _repo_scripts()}
    assert len(walked) > 80, walked
    for expected in ("scripts/dice.py", "scripts/combat.py",
                     "scripts/localdm/play.py", "scripts/tactics/cli.py",
                     "scripts/world.py", "scripts/localdm/stall.py"):
        assert expected in walked, f"{expected} is not being scanned"


def test_no_module_reads_the_global_random_generator():
    """The regression that produced this whole file, applied to every module.

    Walks `scripts/` rather than naming files, so a module cannot join the exempt
    set by being new. `play.py` and `world.py` are absent from
    `_GLOBAL_GENERATOR_EXEMPT` because dnd-gm#306 fixed them, which is the
    property the two-name version could not express.
    """
    offenders: list = []
    for path in _repo_scripts():
        rel = _relative(path)
        if rel in _GLOBAL_GENERATOR_EXEMPT:
            continue
        for lineno, text in _random_reads(path):
            offenders.append(f"{rel}:{lineno}: {text}")

    assert not offenders, "reads of the global generator:\n  " + "\n  ".join(offenders)
    assert not any("localdm/play.py" in o or "scripts/world.py" in o for o in offenders)


def test_every_exemption_carries_its_reason():
    """An allow-list entry with no reason is indistinguishable from a hole.

    Also catches the two failure directions at once: an entry for a file that no
    longer reads the generator (the exemption outlived the bug, and will hide the
    next one) and an entry for a file that does.
    """
    for rel, reason in _GLOBAL_GENERATOR_EXEMPT.items():
        assert reason.strip(), f"{rel} is exempt with no reason recorded"
        path = ROOT / rel
        assert path.is_file(), f"{rel} is exempt but does not exist"
        assert _random_reads(path), f"{rel} is exempt but reads nothing -- drop the entry"

    for rel, reason in _CONSTRUCTOR_EXEMPT.items():
        assert reason.strip(), f"{rel} is exempt with no reason recorded"
        path = ROOT / rel
        assert path.is_file(), f"{rel} is exempt but does not exist"
        assert _random_constructions(path) or "annotation" in reason, (
            f"{rel} is exempt but constructs nothing -- drop the entry")


def test_dice_owns_the_constructor_and_nobody_else_adds_one():
    """The second half of #292, as a standing condition.

    #292's finding was that `combat.py` had a second path learning how a seed
    becomes a generator. That was fixed by editing the one file. This says the
    condition holds tree-wide, so the next second path is a red test rather than
    another sweep.
    """
    constructors = {_relative(p) for p in _repo_scripts()
                    if _random_constructions(p)}
    assert "scripts/dice.py" in constructors, "dice.py stopped building generators"

    unexpected = constructors - {"scripts/dice.py"} - set(_CONSTRUCTOR_EXEMPT)
    assert not unexpected, ("a module constructs random.Random() outside the factory: "
                            f"{sorted(unexpected)}")

    for rel in _MUST_NOT_CONSTRUCT:
        assert rel not in constructors, (
            f"{rel} constructs its own generator; #117 and #292 routed it through "
            "dice.new_rng() and it has drifted back")


def test_the_global_generator_check_would_catch_a_regression():
    """A guard that cannot fail guards nothing. Proved on the exact line #117
    removed, on the line #306 removed, and on a brand new module -- which is the
    case the old two-name list could not see at all."""
    for path in _repo_scripts():                               # never raises
        _random_reads(path)

    hits = dict(_random_reads_text("total = random.randint(1, 20) + bonus\n"))
    assert hits and "random.randint" in hits.values()
    assert _random_reads_text("    return [random.randint(1, sides) for _ in range(n)]\n")
    assert _random_reads_text("rng = dice.new_rng(seed)\n") == []
    # a brand new module that reaches for the global generator: the walk catches it
    # because it never names files, not because anyone remembered to add it
    assert _random_reads_text("x = random.choice(names)\n")
    # type annotations and prose are not reads, which is why the walk is an AST
    # walk. A line scan flagged all four of these.
    assert _random_reads_text('rng: "random.Random | None" = None\n') == []
    assert _random_reads_text("def f(rng: Optional[random.Random]) -> int:\n"
                              "    return 0\n") == []
    assert _random_reads_text('"""The line pending.get("seed", random.randrange(1 << 30)) '
                              'was removed."""\n') == []
    # the constructor is not a read, so it is governed by the other test
    assert _random_reads_text("_RNG = random.Random()\n") == []
    assert _random_constructions_text("_RNG = random.Random()\n")
    assert _random_constructions_text("rng = random.Random(seed)\n")
    assert _random_constructions_text("rng = dice.new_rng(seed)\n") == []


def _random_reads_text(source: str):
    """`_random_reads` for a snippet, for the self-test above.

    Dedented, because the snippets are written at the indentation they appear at
    in the source they quote and `ast.parse` will not accept a bare indented
    statement.
    """
    return _parse_source(source, _random_reads)


def _random_constructions_text(source: str):
    return _parse_source(source, _random_constructions)


def _parse_source(source: str, fn):
    import textwrap
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "snippet.py"
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return fn(path)



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


# ─── combat.py: the generator comes from the canonical factory ───────────────
#
# `combat.py` used to end its `--seed` branch with `random.Random(seed)`. The
# faces were always replayable, so this is not a second dice bug -- but it was a
# second path that knew how a seed becomes a generator, and the generator it
# produced had no `.seed_value` to quote. So the tests below assert *provenance*,
# not repeatability: a repeatability test would have passed before the fix too,
# which is exactly why it cannot be the one that proves the fix.


def _init_rolls_via_cli(monkeypatch, seed=None):
    """Run combat.py's `init` in-process and return (stdout, seeded generators)."""
    built = []
    real_new_rng = dice.new_rng

    def spy(s=None):
        rng = real_new_rng(s)
        built.append((s, rng))
        return rng

    monkeypatch.setattr(dice, "new_rng", spy)
    argv = ["combat.py", "init", PARTY] + (["--seed", str(seed)] if seed is not None else [])
    monkeypatch.setattr(sys, "argv", argv)
    runpy.run_path(str(ROOT / "scripts" / "combat.py"), run_name="__main__")
    return built


def _printed_d20s(out: str) -> list[int]:
    return [int(m) for m in re.findall(r"d20\((\d+)\)", out)]


def test_a_seeded_fight_is_rolled_by_the_canonical_factory(monkeypatch, capsys):
    """The generator behind `--seed N` is the one `dice.new_rng(N)` hands out, so
    the faces a transcript quotes are the factory's faces and not a private copy.

    Same seed twice already passed before this fix -- `random.Random(11)` replays
    just as well as the factory's generator from 11. So this asserts the stronger
    half: the combat CLI's d20s *are* the first draws from an independent
    `dice.new_rng(seed)`, and the generator it was handed records that seed. Both
    halves are false while combat.py builds its own.
    """
    for seed in (11, 23, 42, 7, 99):
        built = _init_rolls_via_cli(monkeypatch, seed)
        printed = sorted(_printed_d20s(capsys.readouterr().out))

        seeded = [rng for s, rng in built if s == seed]
        assert len(seeded) == 1, (seed, built)
        assert seeded[0].seed_value == seed

        reference = dice.new_rng(seed)
        assert printed == sorted(reference.randint(1, 20) for _ in range(len(printed)))


def test_the_combat_module_stream_is_built_by_the_canonical_factory(monkeypatch):
    """combat.py's unseeded stream is a factory product, so it has a seed to quote.

    `runpy.run_path` without `run_name` executes the module body and skips the
    `__main__` block, so this sees the import-time stream and nothing else: the
    factory is asked for exactly one generator, unseeded, and that generator is
    what `_RNG` ends up bound to. Before the fix combat.py built its own, so the
    factory was never asked and `_RNG` carried no `.seed_value` to quote.
    """
    built = []
    real_new_rng = dice.new_rng
    monkeypatch.setattr(dice, "new_rng",
                        lambda s=None: built.append(s) or real_new_rng(s))

    namespace = runpy.run_path(str(ROOT / "scripts" / "combat.py"))

    assert built == [None], built
    assert namespace["_RNG"].seed_value is not None


def test_an_unseeded_combat_run_builds_exactly_one_generator(monkeypatch, capsys):
    """The unseeded command reuses its one process stream: it must not ask the
    factory for a generator of its own, because a generator built per run is fresh
    entropy and the transcript of that run cannot be reproduced from it.

    Counted at the factory, which is the honest way to say "nothing was reset" --
    and the faces are then checked against an independent generator built from the
    stream's own recorded seed, so this is a claim about the rolls the run really
    produced rather than about `random` replaying a seed to itself.
    """
    built = _init_rolls_via_cli(monkeypatch)
    faces = sorted(_printed_d20s(capsys.readouterr().out))

    # One generator for the whole run, and it is the unseeded module stream.
    assert [s for s, _ in built] == [None], built

    stream = built[0][1]
    reference = dice.new_rng(stream.seed_value)
    assert faces == sorted(reference.randint(1, 20) for _ in range(len(faces)))


def test_combat_cannot_construct_its_own_generator_again():
    """`dice.py` is allowed two `random.Random` lines; combat.py is not allowed any.
    A guard that cannot fail guards nothing, so the matcher is exercised both ways
    first -- on the two lines combat.py used to have, and on the seeded branch of
    `dice.py`, which must keep passing."""
    for line in ("_RNG = random.Random()",
                 "    rng = random.Random(seed) if seed is not None else _RNG",
                 "    rng = random.Random(11)",
                 "import random"):
        assert _OWN_CONSTRUCTOR.search(line), line

    assert not _OWN_CONSTRUCTOR.search("    rng = _dice.new_rng(seed) if seed is not None else _RNG")
    assert not _OWN_CONSTRUCTOR.search("_RNG = _dice.new_rng()")

    source = (ROOT / "scripts" / "combat.py").read_text(encoding="utf-8")
    for lineno, line in enumerate(source.splitlines(), 1):
        assert not _OWN_CONSTRUCTOR.search(line), f"combat.py:{lineno}: {line.strip()}"


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
