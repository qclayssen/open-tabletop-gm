"""The headless skill check has to be replayable, and the guard that says so has
to be able to see a module it was not told about.

#306 found two things, and this file covers both.

The first is a roll nobody can re-run. `localdm/play.py` asked the `random`
module's global generator for a d20:

    if total is None:
        total = random.randint(1, 20) + bonus

That is the single most consequential roll in the product -- a skill check, the
thing the whole table is watching -- and there was no `--seed` anywhere that
captured it, because there was no seed. #117 made `dice.new_rng()` the canonical
factory precisely so a roll could carry a quotable seed; #292 found
`combat.py`'s bare `random.Random` and routed it. This was the third reader, in a
file no guard covered.

It is also the *fallback* arm. `self.display.request_roll(...)` returns `None`
exactly when no display is registered, so the roll a table got running headless
was the unreproducible one. With a display up the player rolls in the browser and
that result is theirs to keep, which is why this arm is a fallback and not the
only path.

The second thing is the guard. `test_legacy_dice_replay.py` used to name two
files:

    for name in ("dice.py", "combat.py"):

Grepping the source beats trusting nobody will reach for `random.` again -- that
instinct was right, and the file list is why it was not enough. A guard over two
filenames cannot see a third module, so every file added after #117 was unguarded
by construction. This file asserts the walk reaches a module nobody remembered to
add, by planting a violation in a file the guard has never heard of.

The house rule here is that nothing changes about what a roll comes out as:
`dice.new_rng(seed)` is `random.Random(seed)` plus a `.seed_value`, and an
unseeded generator was already OS-seeded. What changes is that the seed exists to
be read.
"""
from __future__ import annotations

import ast
import pathlib
import random
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dice  # noqa: E402
import world  # noqa: E402
from localdm import play  # noqa: E402

NULLS = '\n{"escalate": null, "command": null}'
MODELS = None                                     # filled in below, after llm imports
SHEET = """# Kairos
## Identity
- **Race:** Kenku (Multiverse) | **Class:** Wizard 1 | **Level:** 1
## Combat Stats
- **HP:** 6 / 8 | **Temp HP:** 0
- **AC:** 12 | **Initiative:** +2 | **Speed:** 30 ft
## Skills
| Skill | Ability | Bonus | Proficient |
|-------|---------|-------|-----------|
| Insight | WIS | +3 | - |
| Investigation | INT | +5 | yes |
## Known Spells / Cantrips
- **Cantrips:** Fire Bolt, Mind Sliver
"""
#: The DM's two replies to "I search the shed.": the scene, then the check it
#: asks for. `+5` is Investigation on the sheet above, so a d20 of N reports N+5.
CHECK_DC = 16
BONUS = 5


def _models():
    from localdm import llm
    return llm.Models("dm-local", "dm-advisor", "dm-council")


def _build_session(tmp_path, monkeypatch, seed=None):
    """A real `Session`, driven through the real check path, with no display.

    `display = None` is what makes this the fallback arm -- the arm that used to
    be unseedable -- rather than the browser roll, so the test is aimed at the
    code path #306 was about.

    `seed` is applied to the module's stream rather than to a parameter, because
    this is the seam a GM or a harness has: there is no `--seed` flag on this
    entry point, which is exactly why the roll was unquotable before. The
    `rng=` parameter on `_ability_check` is the programmatic equivalent and is
    exercised directly further down.
    """
    from tests.localdm_fakes import FakeBridge, FakeClient
    from localdm.play import Session

    if seed is not None:
        monkeypatch.setattr(play, "_CHECK_RNG", dice.new_rng(seed))

    camp = tmp_path / "demo"
    (camp / "characters").mkdir(parents=True, exist_ok=True)
    (camp / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    (camp / "characters" / "Kairos.md").write_text(SHEET, encoding="utf-8")

    # A responder rather than a fixed list of replies: the injection tests below
    # roll a second check on the same session, and a two-item iterator would run
    # out and fail with StopIteration instead of saying anything about dice.
    asked = {"n": 0}

    def respond(model, messages, role):
        asked["n"] += 1
        if asked["n"] % 2:
            return f"You start to search the shed.\n{{\"check\": \"Investigation {CHECK_DC}\"}}"
        return "Behind the crates, a torn sleeve." + NULLS

    client = FakeClient(respond)
    return Session("demo", client, _models(), camp_dir=camp, bridge=FakeBridge())


def _session(tmp_path, monkeypatch, seed=None):
    """Build a session and run one player turn through it."""
    return _build_session(tmp_path, monkeypatch, seed=seed).handle("I search the shed.")


def _rolled_total(out) -> int:
    """Pull the d20 back out of the narration, minus the sheet's bonus.

    The line is the engine's own report of the roll ("Kairos rolled an
    Investigation check: 17 against DC 16"), so this reads the number the product
    told the table rather than recomputing it. Reading the report is also the
    stronger assertion: a roll the engine computes but never states would fail
    here, which is right -- a roll nobody is told is a roll nobody can check.
    """
    for line in out:
        if "rolled an" in line and "check:" in line:
            total = int(line.split("check:")[1].split("against")[0].strip())
            return total - BONUS
    raise AssertionError(f"no rolled total in {out!r}")


# ─── play.py: the headless d20 ───────────────────────────────────────────────

def test_the_check_stream_carries_a_quotable_seed():
    """The property that was missing, stated as the smallest thing that says it.

    Before the fix `play.py` called `random.randint` and there was no object to
    ask. An unseeded `random.Random()` has no `.seed_value` either, so this is an
    AttributeError rather than a failed assertion if the factory is bypassed
    again -- the shape of failure the #117 tests settled on.
    """
    seed = play._CHECK_RNG.seed_value
    assert isinstance(seed, int) and not isinstance(seed, bool)
    assert seed >= 0, "a secrets-backed seed is a non-negative integer"


def test_two_fresh_check_streams_do_not_share_a_seed():
    """The two-policy symptom #117 closed: a generator that looks fresh and is
    not, re-seeded or not. Asserted on the value rather than the faces, because a
    generator that ignored its seed would pass a face comparison often enough to
    be useless."""
    assert dice.new_rng().seed_value != dice.new_rng().seed_value


def test_a_skill_check_is_reproducible_from_its_seed(tmp_path, monkeypatch):
    """The whole point, at the seam the table would replay from.

    Same seed in, same d20 reported out. Pre-fix this could not be written at all:
    there was no seed to set and nothing carrying one to read.
    """
    first = _rolled_total(_session(tmp_path, monkeypatch, seed=7))
    second = _rolled_total(_session(tmp_path, monkeypatch, seed=7))

    assert first == second, (first, second)
    assert first == dice.new_rng(7).randint(1, 20), "the seed did not govern the roll"


def test_different_seeds_can_move_the_roll(tmp_path, monkeypatch):
    """A test that only proved `seed 7 == seed 7` would pass a stream that ignored
    its seed and always rolled 1. Thirty seeds cannot."""
    totals = {_rolled_total(_session(tmp_path, monkeypatch, seed=s)) for s in range(30)}
    assert len(totals) > 5, totals


def test_the_check_is_still_a_d20(tmp_path, monkeypatch):
    """The fix moved where the number comes from, not what it is: a d20 plus the
    sheet's Investigation bonus, and nothing else."""
    totals = {_rolled_total(_session(tmp_path, monkeypatch, seed=s)) for s in range(40)}
    assert all(1 <= t <= 20 for t in totals), sorted(totals)


def test_the_roll_can_be_injected_without_touching_the_module_stream(tmp_path, monkeypatch):
    """The programmatic half of the seam.

    `_ability_check` takes `rng` so a replay can name the seed it is replaying
    from without rebinding module state -- two callers replaying the same roll at
    the same time would otherwise share one stream. Nothing on the live path
    passes it, which is asserted by the four tests above going through
    `_CHECK_RNG` instead.
    """
    session = _build_session(tmp_path, monkeypatch, seed=7)
    session.handle("I search the shed.")
    injected = session._ability_check(f"Investigation {CHECK_DC}", "again.",
                                      rng=dice.new_rng(7))
    assert _rolled_total(injected) == dice.new_rng(7).randint(1, 20)


def test_an_injected_stream_does_not_disturb_the_module_stream(tmp_path, monkeypatch):
    """The reason the parameter exists rather than a bare `_CHECK_RNG` read.

    Asserted with `getstate()`, which is the direct way to say the module's
    generator was not advanced -- the same technique
    `test_dice_seed_integrity.py` used for the discarded draw in `cli.py`.
    """
    session = _build_session(tmp_path, monkeypatch, seed=11)
    session.handle("I search the shed.")
    before = random.getstate()
    session._ability_check(f"Investigation {CHECK_DC}", "again", rng=dice.new_rng(3))
    assert random.getstate() == before


# ─── world.py: the factory, and the local that shadowed the module ────────────

def _faction_camp(tmp_path, monkeypatch, name="demo") -> str:
    """A campaign with three factions, so a tick has something to roll for.

    `tick_factions` takes a campaign *name* and resolves it through
    `paths.find_campaign`, which appends `campaigns/` to `$GM_CAMPAIGN_ROOT`
    itself -- so the env var is the root, not `root/campaigns`. The faction
    fields are `Faction`'s own: `current` segments of a 4-segment clock, and
    `created` in the past so the "a clock added today must not fill today" guard
    does not skip every one of them.
    """
    root = tmp_path / "campaigns"
    camp = root / name
    camp.mkdir(parents=True)
    (camp / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    (camp / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    (camp / "faction_log.md").write_text("# faction log\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(tmp_path))
    _write_factions(camp)
    return name


def _write_factions(camp: pathlib.Path) -> None:
    import json
    factions = {
        label: {"goal": f"the {label} agenda", "clock_size": 4, "current": 1,
                "lean": 0, "created": "1 Jan 2020"}
        for label in ("Ninefold Enclave", "Saltmarsh Guild", "Lantern Court")
    }
    (camp / "factions.json").write_text(
        json.dumps({"version": 2, "current_tick": 0, "tick_interval": "day",
                    "factions": factions}), encoding="utf-8")


def _faces(lines) -> list:
    """The d6 each faction rolled, in the order the lines report them."""
    import re
    found = []
    for line in lines:
        found += [int(m) for m in re.findall(r"\(d6 (\d)", line)]
    return found


def test_a_world_tick_consumes_the_seed_it_was_given(tmp_path, monkeypatch):
    """`tick_factions` took an `rng` from the caller or built its own -- and built
    its own with `random.Random()`, so it carried no `.seed_value` and an unseeded
    tick could not be quoted.

    Asserted against the stream itself rather than against a second run's output,
    because a tick *mutates* the clocks it reads: re-running it would compare two
    different starting states and call that reproducibility. Comparing the faces
    to the seeded stream says what is actually claimed -- this tick drew its d6s
    from the generator it was handed, in order.

    Factions clock d6s rather than adjudicating rules, so nothing at the table
    depends on the faces. That is why this was the lower-severity half of #306 and
    why its constructor exemption is recorded as debt rather than quietly dropped.
    """
    camp_name = _faction_camp(tmp_path, monkeypatch)

    rolled = world.tick_factions(camp_name, 1, rng=dice.new_rng(5))
    expected = dice.new_rng(5)
    assert _faces(rolled) == [expected.randint(1, 6) for _ in range(3)], _faces(rolled)


def test_a_world_tick_replays_identically_from_the_same_seed(tmp_path, monkeypatch):
    """The end-to-end form of the above: reset the clocks, tick again, get the
    same report. Without the reset the second run reads advanced clocks, which is
    a different question."""
    camp = tmp_path / "campaigns" / "demo"
    camp_name = _faction_camp(tmp_path, monkeypatch)

    first = world.tick_factions(camp_name, 1, rng=dice.new_rng(5))
    _write_factions(camp)                       # the tick above moved every clock
    second = world.tick_factions(camp_name, 1, rng=dice.new_rng(5))

    assert first == second, (first, second)


def test_the_unseeded_tick_still_produces_faces(tmp_path, monkeypatch):
    """The fallback arm kept working. `_dice.new_rng()` is OS-seeded, exactly as
    `random.Random()` was, so a tick without a seed still advances clocks -- and
    still varies, so a stub returning a constant would fail here."""
    camp = tmp_path / "campaigns" / "demo"
    camp_name = _faction_camp(tmp_path, monkeypatch)

    seen = set()
    for _ in range(60):
        seen.update(_faces(world.tick_factions(camp_name, 1)))
        _write_factions(camp)
    assert len(seen) >= 4, f"expected most of a d6 over 60 unseeded ticks, saw {seen}"


def test_no_local_named_dice_can_shadow_the_module():
    """The latent half of the world.py finding.

    It bound a local called `dice` to a `random.Random`. That is not a style
    point: in a module that also imports the dice module, one more `dice`
    reference inside that scope silently becomes a random generator, and whatever
    breaks points at the wrong file entirely.

    The check is on *bindings*, not on the name appearing: `dice.new_rng()` reads
    the module and is correct, `dice = rng or ...` rebinds it and is the bug. An
    `ast.Name` in Store context is the difference between the two.
    """
    tree = ast.parse(_source_of(world.tick_factions))

    assert any(isinstance(n, ast.Name) and n.id == "_dice" for n in ast.walk(tree)), (
        "expected the aliased module reference to be what builds the stream")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "dice":
            role = "binds a local" if isinstance(node.ctx, ast.Store) else "reads"
            pytest.fail(f"`dice` {role} at line {node.lineno}, shadowing the module")


# ─── the guard's reach, which is the actual fix ───────────────────────────────

def _source_of(fn) -> str:
    import inspect
    import textwrap
    return textwrap.dedent(inspect.getsource(fn))


def _script_reads(path: pathlib.Path):
    """`random.<name>` this file evaluates, excluding the constructor."""
    found: list = []

    class Visitor(ast.NodeVisitor):
        def visit_Attribute(self, node):                       # noqa: N802
            if (isinstance(node.value, ast.Name) and node.value.id == "random"
                    and node.attr != "Random"):
                found.append(f"random.{node.attr}")
            self.generic_visit(node)

    Visitor().visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def test_play_py_no_longer_reaches_for_the_global_generator():
    """The call site, asserted directly rather than only through the walk.

    The walk in `test_legacy_dice_replay.py` covers this file, but that file is
    about the policy and this one is about the roll. A reader who never opens the
    policy test should still be able to find out what this module draws from.
    """
    assert _script_reads(ROOT / "scripts" / "localdm" / "play.py") == []
    assert not hasattr(play, "random"), (
        "play.py imports `random` again; nothing in it should need the module now")


def test_the_walk_catches_a_module_it_was_never_told_about():
    """The structural claim, proved by planting a violation.

    A guard is only as good as the set of things it looks at, and a directory
    walk's advantage over a two-filename list is only real if a file nobody
    registered still gets checked. So this writes a new module into `scripts/`
    that reaches for the global generator and asserts the walk's own predicate
    fires on it.

    It plants and removes the file rather than invoking pytest's assertion
    helpers, because the claim is about the *predicate* the real guard runs over
    whatever it finds -- including a file this test invented a moment ago.
    """
    planted = ROOT / "scripts" / "_planted_rng_violation.py"
    assert not planted.exists(), "a stale planted file would make this vacuous"
    planted.write_text("import random\n\n\ndef pick(names):\n"
                       "    return random.choice(names)\n", encoding="utf-8")
    try:
        assert _script_reads(planted) == ["random.choice"], (
            "the walk's predicate missed a violation in an unregistered module, "
            "which is exactly the case the two-filename list could not see")
    finally:
        planted.unlink()

    assert not planted.exists(), "the planted module must not outlive the test"


def test_the_planted_module_is_gone_so_the_suite_is_repeatable():
    """Order-dependence guard: a file left behind by the test above would fail
    every later run of the real walk, which is a confusing way to find out."""
    assert not list((ROOT / "scripts").glob("_planted_rng_violation.py"))
