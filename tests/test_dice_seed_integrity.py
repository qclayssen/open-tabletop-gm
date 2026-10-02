"""#117: the tactics CLI rolls on the canonical dice stream, and a paused command
replays the seed it saved.

`scripts/dice.py` holds the one RNG policy (docs/milestones/08-roll-integrity.md):
every dice stream is a `random.Random` seeded from `secrets`, with the resolved
seed kept on the object as `.seed_value` so any roll can be quoted and re-run.
`Roller.rng` defaults to `dice.new_rng()` for that reason. The CLI did not:

    rng = random.Random(seed) if seed is not None else random.Random()

A bare `random.Random` has no `seed_value`, so the GM-facing engine was the one
dice stream in the tree with no seed to quote, and a second policy free to drift
from the first. `random.Random()` is OS-seeded, so this was never an entropy
hole. It was a provenance hole, and what these tests assert is the seed on the
object, not the faces it produces.

The second defect was in how the CLI chose that seed for a paused command:

    args._seed = pending.get("seed", random.randrange(1 << 30))

`dict.get`'s default is evaluated before the lookup, so the fallback drew a
replacement seed off the *global* `random` generator on every re-run of a paused
command and then discarded it, because the key was present. That is a third
dice stream: not the canonical one, not recorded in pending.json, and advanced by
a code path with no other reason to touch the module.

The third was the null case. `dict.get` hands back whatever was stored, so a
pending.json whose seed was null (hand-edited, migrated, or written by
something that knew the shape but not the policy) resolved to `None`, and
`_roller` read that as "no seed" and built an unseeded generator. The paused
command replayed with fresh dice, and the next pause wrote the same null back
out, so that command could never replay again -- permanently, and silently,
since an unseeded roll looks exactly like a seeded one.

Nothing here changes what a roll comes out as. An explicit `--seed N` builds
the same `random.Random(N)` it always did, and an unseeded one was already
OS-seeded. What changes is that the seed exists to be read.
"""
from __future__ import annotations

import ast
import json
import random
import sys

import pytest

from tests.tactics_fixtures import RULES, ROOT, _RAW, _build
from tactics import cli

rules_mod = sys.modules[type(RULES).__module__]
KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")
CLI_SOURCE = ROOT / "scripts" / "tactics" / "cli.py"


# ─── the campaign the CLI is driven against ───────────────────────────────────
#
# The same shape tests/test_tactics_cli.py builds, and repeated for the same
# reason: a shared builder would put a mutable campaign fixture in the path of
# every tactics lane at once.

@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    monkeypatch.setattr(rules_mod, "_lookup_monster",
                        lambda name: _build._norm_monster(_RAW[name.lower().replace(" ", "-")]))
    return d


def run(capsys, *argv):
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def begin(capsys, *extra):
    return run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--monster", "giant frog@M11", "--seed", "3", *extra)


def _next_to_kairos(camp):
    """Put the frog next to Kairos on B7 with the turn, so its attack lands and
    Silvery Barbs asks for a reaction."""
    path = camp / "combat" / "encounter.json"
    enc = json.loads(path.read_text(encoding="utf-8"))
    enc["tokens"]["frog-1"]["x"], enc["tokens"]["frog-1"]["y"] = 2, 6
    enc["turn_index"] = enc["order"].index("frog-1")
    enc["turn"] = {"actor": "frog-1", "movement_budget": 30}
    path.write_text(json.dumps(enc), encoding="utf-8")


def _hitting_seed() -> int:
    """A seed whose first d20 lands on Kairos and trips Silvery Barbs."""
    return next(s for s in range(500) if 9 <= random.Random(s).randint(1, 20) <= 16)


def _other_hitting_seed() -> int:
    """A second one, so a test can tell "reused the saved seed" from "resolved
    a new one" by the value alone."""
    first = _hitting_seed()
    return next(s for s in range(500)
                if 9 <= random.Random(s).randint(1, 20) <= 16 and s != first)


def _missing_seed() -> int:
    """A seed whose first d20 is a natural 1: no hit, so no Silvery Barbs."""
    return next(s for s in range(500) if random.Random(s).randint(1, 20) == 1)


def _pending(camp) -> dict:
    return json.loads((camp / "combat" / "pending.json").read_text(encoding="utf-8"))


def _args(**over):
    """A parsed namespace with the fields `_roller` reads, defaulted."""
    args = cli._parse(["-c", "demo", "status"])
    args._seed = 4242
    for key, value in over.items():
        setattr(args, key, value)
    return args


# ─── the canonical factory ───────────────────────────────────────────────────

def test_the_cli_stream_carries_its_resolved_seed():
    """`random.Random(seed)` has no `seed_value`, so before the fix this is an
    AttributeError rather than a failed assertion: the seed was not there to be
    quoted."""
    assert cli._roller(_args(seed=7)).rng.seed_value == 7


def test_an_unseeded_command_still_gets_one():
    """No `--seed` and no saved seed: the canonical policy supplies a fresh
    secrets-backed one. Before the fix this was a bare `random.Random()` with no
    `seed_value`, so an unseeded command had nothing to record either."""
    first = cli._roller(_args(seed=None, _seed=None)).rng
    second = cli._roller(_args(seed=None, _seed=None)).rng
    assert isinstance(first.seed_value, int) and first.seed_value >= 0
    assert first.seed_value != second.seed_value          # a new command, a new seed


def test_an_explicit_seed_lands_the_faces_it_always_did():
    """The no-visible-change guard: a seeded command must consume the identical
    stream it did before, from the same integer, in the same order."""
    expected = random.Random(7)
    faces = [expected.randint(1, 20) for _ in range(5)]
    r = cli._roller(_args(seed=7))
    assert [r.rng.randint(1, 20) for _ in range(5)] == faces


def test_an_explicit_seed_still_beats_the_saved_one():
    """Precedence is the GM's and predates the fix: `--seed N` is the seed, and
    only in its absence is the one saved by a pause read."""
    args = _args(seed=7, _seed=99)
    assert cli._roller(args).rng.seed_value == 7
    args.seed = None
    assert cli._roller(args).rng.seed_value == 99


# ─── pending.json seeds ──────────────────────────────────────────────────────

def test_a_usable_pending_seed_is_returned_unchanged():
    """The other half of the resolver: a saved seed is READ, not redrawn. A test
    that only covered the null branch would pass on a resolver that replaced
    every seed with a fresh one, which is the exact opposite of replay."""
    for stored in (0, 1, 2 ** 31, 2 ** 80, -5):
        assert cli._resolve_seed({"seed": stored}) == stored


@pytest.mark.parametrize("stored", [None, "", "abc", 3.5, True, [], {}])
def test_a_pending_seed_that_is_not_an_integer_is_resolved(stored):
    """The coverage audit.

    `dict.get` hands back whatever was stored, so a null (or otherwise unusable)
    seed reached `_roller` as "no seed" and produced an unseeded roll that no
    re-run could match -- and the next pause wrote the same unusable value
    straight back out, so the command could never replay again. Rejecting the
    record would strand a fight mid-decision, so it is resolved instead: one
    seed from the canonical policy, resolved once, persisted by the next
    `_save_pending`. `True` is in the list because a JSON boolean is an int in
    Python, and `random.Random(True)` would have quietly seeded from 1.
    """
    resolved = cli._resolve_seed({"seed": stored})
    assert isinstance(resolved, int) and not isinstance(resolved, bool) and resolved >= 0


def test_a_missing_seed_key_resolves_too():
    assert isinstance(cli._resolve_seed({}), int)


def test_resolving_a_seed_leaves_the_global_generator_alone():
    """The removed line drew off the module-level generator. `random.getstate()`
    is the direct way to say it no longer does."""
    before = random.getstate()
    cli._resolve_seed({})
    cli._resolve_seed({"seed": None})
    assert random.getstate() == before


# ─── paused replay ───────────────────────────────────────────────────────────

def test_a_paused_command_replays_its_saved_seed(camp, capsys, monkeypatch):
    """The whole point of combat/pending.json: the re-run lands the same faces.

    `drawn` is the assertion that matters. Before the fix every one of these
    re-runs drew a replacement seed from `cli.random.randrange` and discarded it;
    counting the resolutions is what pins that down, because the replay itself
    looked identical either way -- the discarded seed was never used.

    Kills the mutant where the resolver ignores the saved seed and draws a fresh
    one every time: the pause would still replay, but `drawn` would grow.
    """
    drawn = []
    hit = _hitting_seed()
    monkeypatch.setattr(cli, "_fresh_seed", lambda: drawn.append(hit) or hit)
    begin(capsys)
    _next_to_kairos(camp)
    code, out = run(capsys, "attack", "frog-1", "kairos")
    assert code == 2 and "Silvery Barbs" in out
    assert _pending(camp)["seed"] == hit
    resolved_so_far = len(drawn)

    answers = ["--react", "no"]
    for _ in range(3):
        code, out = run(capsys, "attack", "frog-1", "kairos", *answers)
        if code == 0:
            break
        answers += ["--react", "no"]
    assert code == 0
    assert len(drawn) == resolved_so_far         # the re-runs resolved nothing


def test_a_paused_replay_does_not_move_the_global_generator(camp, capsys, monkeypatch):
    """Module-level rather than per-file: the discarded draw was a third dice
    stream, and this says the module's generator is not it. It also covers the
    other consumer in the same process, play.py's `_ability_check`, which reads
    `random.randint` and is the seam the outer dice-lens harness forces faces
    into (docs/milestones/08-roll-integrity.md).

    Kills the mutant that keeps the fix but leaves the discarded draw behind.
    """
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _next_to_kairos(camp)
    code, _ = run(capsys, "attack", "frog-1", "kairos")
    assert code == 2
    before = random.getstate()
    run(capsys, "attack", "frog-1", "kairos", "--react", "no")
    assert random.getstate() == before


def test_a_null_pending_seed_is_replaced_and_the_replay_recovers(camp, capsys, monkeypatch):
    """End to end, and the audit's requirement in one test: a pending.json whose
    seed is null (hand-edited, migrated, written by something that knew the
    shape but not the policy) must still replay, and an explicit `--seed` must
    still outrank it.

    The null is injected between two real pauses rather than written by hand, so
    the `cmd` it sits beside is one the CLI actually produced.

    Kills the pre-fix line itself: it returns the stored null, so `_roller`
    builds an unseeded generator, the pause it writes back is null again, and
    neither assertion below is reachable.
    """
    hit = _hitting_seed()
    monkeypatch.setattr(cli, "_fresh_seed", lambda: hit)
    begin(capsys)
    _next_to_kairos(camp)
    code, _ = run(capsys, "attack", "frog-1", "kairos")
    assert code == 2 and _pending(camp)["seed"] == hit

    pending_path = camp / "combat" / "pending.json"
    paused = json.loads(pending_path.read_text(encoding="utf-8"))
    paused["seed"] = None
    pending_path.write_text(json.dumps(paused), encoding="utf-8")

    resolved, replacement = [], _other_hitting_seed()
    monkeypatch.setattr(cli, "_fresh_seed", lambda: resolved.append(replacement) or replacement)
    code, out = run(capsys, "attack", "frog-1", "kairos")
    assert code == 2 and "Silvery Barbs" in out            # paused again, not crashed
    assert len(resolved) == 1, resolved                   # the null drew one replacement
    assert _pending(camp)["seed"] == replacement          # and it was written back

    # Replayable from here on: that integer is read, not redrawn.
    answers = ["--react", "no"]
    for _ in range(3):
        code, out = run(capsys, "attack", "frog-1", "kairos", *answers)
        if code == 0:
            break
        answers += ["--react", "no"]
    assert code == 0 and len(resolved) == 1, resolved


def test_an_explicit_seed_outranks_a_pending_one(camp, capsys, monkeypatch):
    """Precedence with a saved seed actually sitting in pending.json rather than
    in a namespace: the explicit seed misses and the saved one would have hit,
    so the outcome itself names which stream rolled.

    `--seed` is deliberately pinned on BOTH invocations. It is not one of
    `_ANSWER_FLAGS`, so it is part of the key the pending record is filed under;
    adding it only to the re-run would miss the record altogether and say nothing
    about precedence. That is a pre-existing property of `_canonical`, not
    something #117 changed, and it is reported rather than fixed here.

    Kills the mutant where `_roller` reads only the resolved seed: the saved one
    is `miss`, so the attack would miss and the second pause would not happen.
    """
    drawn = []
    hit, miss = _hitting_seed(), _missing_seed()
    monkeypatch.setattr(cli, "_fresh_seed", lambda: drawn.append(miss) or miss)
    begin(capsys)
    _next_to_kairos(camp)
    # The resolved pending seed is deliberately the seed that would have MISSED,
    # so the pause below can only have happened if `--seed` outranked it.
    code, out = run(capsys, "attack", "frog-1", "kairos", "--seed", str(hit))
    assert code == 2 and "Silvery Barbs" in out
    assert _pending(camp)["seed"] == miss              # saved, but not what rolled
    resolved_so_far = len(drawn)

    code, out = run(capsys, "attack", "frog-1", "kairos", "--seed", str(hit))
    assert code == 2 and "Silvery Barbs" in out        # the pinned seed governed again
    assert len(drawn) == resolved_so_far               # the saved seed was read, not redrawn


# ─── the guard that keeps it fixed ───────────────────────────────────────────

def _random_module_uses(source: str) -> list:
    """Every `random` import or `random.<name>` read in `source`, via the AST.

    An AST walk rather than a grep, so the `random.randrange(1 << 30)` quoted in
    _resolve_seed's own docstring does not read as a call site.
    """
    found: list = []

    class Visitor(ast.NodeVisitor):
        def visit_Import(self, node):                       # noqa: N802
            found.extend(a.name for a in node.names if a.name.split(".")[0] == "random")

        def visit_ImportFrom(self, node):                   # noqa: N802
            if (node.module or "").split(".")[0] == "random":
                found.append(f"from {node.module} import ...")

        def visit_Attribute(self, node):                    # noqa: N802
            if isinstance(node.value, ast.Name) and node.value.id == "random":
                found.append(f"random.{node.attr}")
            self.generic_visit(node)

    Visitor().visit(ast.parse(source))
    return found


def test_the_cli_names_no_random_generator_at_all():
    """`test_legacy_dice_replay.py` guards dice.py and combat.py against falling
    back to the module-level generator. tactics/cli.py was the third reader and
    was not in that list, because it had a legitimate one: the pending-seed
    fallback. With that gone there is no reason for it to import `random`."""
    assert _random_module_uses(CLI_SOURCE.read_text(encoding="utf-8")) == []


def test_the_source_scan_would_catch_a_return_to_the_global_generator():
    """A guard that cannot fail guards nothing: prove it on the exact line #117
    removed, and on the import that line could not survive without."""
    assert _random_module_uses("import random\nx = random.randrange(1 << 30)\n")
    assert _random_module_uses("rng = random.Random()\n")
    assert _random_module_uses("from random import Random\n")
    # and it must stay quiet on the sanctioned forms
    assert _random_module_uses("rng = dice.new_rng(seed)\n") == []
    prose = '"""Notes.\n\nThe line pending.get("seed", random.randrange(1 << 30)) was removed.\n"""\n'
    assert _random_module_uses(prose) == []