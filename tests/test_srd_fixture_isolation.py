"""#231: the SRD fixture must not outlive a test, and the production lookup must
be exercised with the data absent.

WHY
===
`tests/tactics_fixtures.py` replaced the production `tactics_spells._srd` with a
fixture lookup by a bare assignment at import time:

    spells_rules._srd = srd_spell

with no teardown. That is a process-wide global mutation decided by collection
order, so:

  * a test that means to exercise the production SRD lookup passes or fails
    depending on whether some *other* module imported `tactics_fixtures` first;
  * a test that patched `_srd` for itself could not put the production function
    back, because nothing knew it had been displaced;
  * nothing failed loudly. The suite was green in every order, which is what
    lets this class of defect survive: a green suite and an order-dependent
    suite look identical until you reverse the order.

The fix is in `tests/conftest.py`: `fixture_srd` is autouse and restores the
production function on teardown, and `production_srd` opts a test back into the
real lookup.

WHAT WOULD MAKE THESE FAIL
==========================
The proof for the lifetime half is an ORDERING one, run as a subprocess in
`test_the_production_lookup_survives_another_module_installing_the_fixture`:
run this file after `test_tactics_spells.py` (which imports the fixtures module
and so used to install the patch for good) and every production-lookup test
below still passes. On the pre-fix source that ordering turns them red.

Each production-lookup test also fails if `tactics_spells._srd` stops returning
None for absent data, or stops refusing a stale record; the mutants are named in
each docstring.

Note on how to read a failure here: `agents/dev/verifier.md` is right that a
collection error is not a failed assertion, and this file is deliberately built
so that reverting the fix produces *assertion* failures. The `production_srd`
fixture is requested, not assumed, by the tests that need it, and the helper
that resolves production `_srd` is defined here rather than imported, so a
reverted `conftest.py` costs an assertion rather than the whole module.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE_SRD = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _spells_module():
    """`systems/dnd5e/tactics_spells.py`, reached the way production reaches it."""
    import tactics.rules as rules_mod

    return sys.modules[type(rules_mod.load("dnd5e")).__module__]._spells_module()


def _lookup_module():
    """`systems/dnd5e/lookup.py`, on `sys.path` as production puts it."""
    here = str(ROOT / "systems" / "dnd5e")
    if here not in sys.path:
        sys.path.insert(0, here)
    import lookup

    return lookup


def _rules_module():
    import tactics.rules as rules_mod

    return sys.modules[type(rules_mod.load("dnd5e")).__module__]


# ─── the patch has a lifetime ─────────────────────────────────────────────────
#
# These two are a pair and the ordering inside the module is load-bearing. The
# first patches `_srd` and deliberately does not restore it; the second runs
# immediately after and asserts the leak did not survive. Deleting the leaking
# test deletes the proof with it, so each names the other.


def test_a_test_that_leaks_its_own_patch(raw_srd):
    """Deliberately patch `_srd` mid-test and never restore it.

    Requests `raw_srd` so the autouse fixture's *install* is skipped. Without
    that, the next test's own setup would put `srd_spell` back before anything
    could observe the leak, and the test below would pass with the teardown
    deleted -- a mutation this pair was written to catch, and one an earlier
    draft of this file did not catch.
    """
    raw_srd._srd = lambda key: {"name": "LEAKED", "level": 9}
    assert raw_srd._srd("fire bolt")["name"] == "LEAKED"


def test_the_fixture_patch_is_removed_after_every_test(raw_srd):
    """Runs immediately after the leak above; proves the teardown ran.

    Mutant: a `fixture_srd` whose `finally` clause is dropped, or which restores
    whatever `_srd` happened to be at setup time rather than the captured
    production function. Either leaves `LEAKED` installed and this fails -- which
    is verified, not assumed: dropping the `finally` turns this file red.
    """
    got = raw_srd._srd("fire bolt")
    assert got != {"name": "LEAKED", "level": 9}, (
        "`tactics_spells._srd` is still the function the previous test installed. "
        "A patch outlived its test, which is the defect this file exists to pin.")
    assert raw_srd._srd.__name__ == "_srd", (
        "between tests the resolver must be back to the production function, not "
        f"to some third one ({raw_srd._srd!r})")


def test_the_fixture_data_is_what_the_spell_tests_read(fixture_srd):
    """The autouse patch does what the old import-time assignment used to do.

    Mutant: an autouse `fixture_srd` that patches nothing (a no-op body, or a
    patch aimed at the wrong module) leaves the resolver on the production
    function, and the spell suite goes red with a missing `Kairos casts Fire
    Bolt` line. Asserted here against the fixture's own numbers so the failure
    is local rather than spread across three modules.
    """
    from tests import tactics_fixtures as fx

    spec = _spells_module().resolve(fx.caster(), "fire bolt")
    assert spec["mode"] == "attack"
    assert spec["range"] == 120 and spec["damage"] == [{"dice": "1d10", "type": "fire"}]


_IMPORT_PROBE = """
import sys
sys.path.insert(0, {scripts!r})
import tests.tactics_fixtures as fx          # what 32 test modules do
spells = fx.spells_rules
print("FIXTURE" if spells._srd is fx.srd_spell else "PRODUCTION")
"""


def test_importing_the_fixtures_module_alone_leaves_production_in_place():
    """The defect itself, as one assertion that involves no fixture machinery.

    `tests/tactics_fixtures.py` carried `spells_rules._srd = srd_spell` at module
    level. Importing it -- which 32 test modules do -- displaced the production
    lookup for the rest of the process, with no teardown. Run in a subprocess so
    the answer comes from a plain `import`, not from a pytest fixture that might
    itself be the thing masking the bug.

    Mutant: restoring the import-time assignment. The conftest fixture alone is
    not a fix if the module still displaces the global on import, because the
    first test to run already sees the wrong `_srd` before any fixture setup.

    This is also the assertion that keeps the two halves honest. If `fixture_srd`
    ever became a no-op, the pair of leak tests above would go quiet rather than
    red, and this one would not notice -- but a no-op `fixture_srd` combined with
    a clean import here is a suite that reads the REAL dataset in every spell
    test, which `test_the_spell_tests_pass_in_either_collection_order` catches.
    """
    probe = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE.format(scripts=str(ROOT / "scripts"))],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "PRODUCTION", (
        "importing tests.tactics_fixtures left `tactics_spells._srd` as the "
        "fixture lookup (printed FIXTURE). The module displaces a production "
        "global at import time and never restores it.")


def test_the_production_lookup_is_restored_not_the_patch(production_srd):
    """`production_srd` yields the real function, not a copy of the fixture.

    Mutant: a `production_srd` that restores `srd_spell` (a plausible typo, and
    a silent one -- the function it returns would still be callable) is caught
    here by identity rather than by a downstream coincidence.
    """
    from tests import tactics_fixtures as fx

    assert production_srd is not fx.srd_spell
    assert _spells_module()._srd is production_srd
    assert production_srd.__name__ == "_srd"


# ─── ordering independence, measured ──────────────────────────────────────────

def _run(*targets, env_extra=None):
    """Run test targets in a subprocess and return (exit code, output).

    Targets are passed through verbatim, so callers name node ids and not files:
    naming this file would recurse, since this module spawns the runner that
    collects it.
    """
    env = dict(os.environ)
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         *targets],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env,
        check=False,
    )
    return proc.returncode, proc.stdout


# `test_tactics_spells.py` imports `tests.tactics_fixtures`, so before the fix it
# installed the `_srd` patch as a side effect of collection. Running the
# production tests after it is the ordering that used to decide whether
# production was reachable at all.
#
# Named individually, and skipped when the environment already put us inside one
# of these subprocess runs, so a run started from the outside does not recurse.
_SPELL_TESTS = "tests/test_tactics_spells.py"
_SELF = "tests/test_srd_fixture_isolation.py"

#: The production-lookup tests, by node id. This is the set whose outcome the
#: ordering decides.
_PRODUCTION_NODES = [
    f"{_SELF}::test_production_lookup_with_no_dataset_at_all",
    f"{_SELF}::test_the_spell_resolver_says_build_the_srd_when_the_dataset_is_absent",
    f"{_SELF}::test_the_monster_lookup_says_build_the_srd_when_the_dataset_is_absent",
    f"{_SELF}::test_suggestions_stay_best_effort_with_no_dataset",
    f"{_SELF}::test_a_stale_spell_record_is_refused_rather_than_half_used",
    f"{_SELF}::test_a_monster_without_structured_actions_names_the_rebuild",
]

_SUBPROCESS_MARKER = "OTGM_ORDERING_SUBPROCESS"


@pytest.mark.skipif(os.environ.get(_SUBPROCESS_MARKER) == "1",
                    reason="already inside an ordering subprocess run")
def test_the_production_lookup_survives_another_module_installing_the_fixture():
    """THE acceptance criterion: "prove ordering independence", as a runnable check.

    Before the fix this subprocess run FAILS: `test_tactics_spells.py` installs
    the fixture lookup at import time and never restores it, so the production
    tests below read the fixture, `production_srd` has nothing to restore, and
    their `pytest.raises(ValueError)` assertions do not fire.

    Asserted on the exit code *and* on the summary line: a collection error and a
    failing assertion both exit non-zero, and `tests/conftest.py`'s own docstring
    records the cost of confusing the two, so the failure kind is checked too.
    """
    code, out = _run(_SPELL_TESTS, *_PRODUCTION_NODES,
                     env_extra={_SUBPROCESS_MARKER: "1"})
    assert code == 0, ("spell tests, then this module's production-lookup tests, "
                       f"should both pass in that order:\n{out}")
    assert "error" not in out.rsplit("\n", 2)[-2].lower(), (
        f"a collection error is not a failed assertion, and it would still have "
        f"a non-zero exit code:\n{out}")


@pytest.mark.skipif(os.environ.get(_SUBPROCESS_MARKER) == "1",
                    reason="already inside an ordering subprocess run")
def test_the_spell_tests_pass_in_either_collection_order():
    """The two orders the spell suite is most sensitive to, both green.

    Before the fix both orders were green too -- which is the point worth making
    explicit. What changed is what a *failure* now means: a leak surfaces as one
    of these going red, rather than as a differently-ordered green that no
    reviewer would look at twice.
    """
    files = ["tests/test_condition_modifiers.py", "tests/test_spell_slots.py",
             "tests/test_tactics_spells.py"]
    for order in (files, list(reversed(files))):
        code, out = _run(*order, env_extra={_SUBPROCESS_MARKER: "1"})
        assert code == 0, f"{order}:\n{out}"


# ─── production lookup with missing data ──────────────────────────────────────


@pytest.fixture
def absent_dataset(tmp_path, monkeypatch):
    """`lookup.py` pointed at an empty tmp dir, with its cache reset.

    `lookup` caches in module globals (`_data`, `_loaded`), so repointing
    `DATA_FILE` is not enough: the cache has to be reset or the test silently
    reads whatever the real dataset loaded first. Both are undone afterwards --
    the same discipline this file is about, applied to the code under test.
    """
    lookup = _lookup_module()
    monkeypatch.setattr(lookup, "DATA_FILE", str(tmp_path / "dnd5e_srd.json"))
    monkeypatch.setattr(lookup, "SUPPLEMENTAL_FILE", str(tmp_path / "supp.json"))
    monkeypatch.setattr(lookup, "_data", {}, raising=False)
    monkeypatch.setattr(lookup, "_loaded", False, raising=False)
    return lookup


def test_production_lookup_with_no_dataset_at_all(absent_dataset):
    """No `dnd5e_srd.json` on disk: every lookup is None, nothing raises.

    This is the fresh-clone state, and it must never read as "no such spell" --
    the caster has to be told to build the SRD, which is the next test.

    Mutant: a `lookup_record` that raises, or an `_load` that raises instead of
    leaving `_data` empty.
    """
    assert absent_dataset.lookup_record("goblin", category="monster") is None
    assert absent_dataset.lookup("fire bolt", category="spell") is None
    assert absent_dataset.suggest("gobln") == []


def test_the_spell_resolver_says_build_the_srd_when_the_dataset_is_absent(
        production_srd, absent_dataset):
    """`resolve` refuses with the build instruction, not a traceback or a default.

    Mutant: a `_srd` that lets `FileNotFoundError` escape, or a `resolve` that
    returns an empty spec. The sentence is asserted in full because the string is
    the product: it is what a player sees instead of a number the engine invented.
    """
    from tests import tactics_fixtures as fx

    assert production_srd("fire bolt") is None
    with pytest.raises(ValueError) as excinfo:
        _spells_module().resolve(fx.caster(), "fire bolt")
    assert "build the SRD" in str(excinfo.value) and "build_srd.py" in str(excinfo.value)


def test_the_monster_lookup_says_build_the_srd_when_the_dataset_is_absent(
        absent_dataset):
    """`_lookup_monster` names the build command when it finds nothing.

    Mutant: a `_lookup_monster` returning `{}`, which hands `token_from_monster`
    a dict with no `hp` and raises `KeyError` in the token builder instead.

    The encounter tests monkeypatch this function out entirely
    (`tests/test_phase5_encounter_design.py:60`), so before this file the real
    function's absent-data branch had no test at all.
    """
    with pytest.raises(ValueError) as excinfo:
        _rules_module()._lookup_monster("goblin")
    assert "no SRD monster 'goblin'" in str(excinfo.value)
    assert "build_srd.py" in str(excinfo.value)


def test_suggestions_stay_best_effort_with_no_dataset(absent_dataset):
    """A missing dataset yields no suggestions and no traceback.

    `_srd_suggest`'s docstring claims a missing dataset is a reason to say "no
    such monster", never a traceback in the middle of designing a fight. That is
    a claim about a code path the fixtures never reached, because the fixtures
    answered the lookup before it got there.

    Mutant: a `_srd_suggest` that lets the exception out, or that returns a
    non-empty list.
    """
    assert _rules_module()._srd_suggest("gobln") == []


# ─── production lookup with stale data ───────────────────────────────────────

_STALE = {
    "spells": [{"name": "Fire Bolt", "level": 0, "mechanics": {
        "damage_type": "fire", "attack": "ranged",
        "damage_at_level": {"1": "1d10"}}}],
    "monsters": [{"name": "Goblin", "index": "goblin"}],
}


@pytest.fixture
def stale_dataset(tmp_path, monkeypatch, request):
    """A dataset written by a `build_srd.py` older than spell mechanics.

    Records carry the old `damage_type` / `damage_at_level` shape and monster
    records carry no `actions` key -- the two shapes that were later superseded,
    and the two refusal branches in `tactics_spells._current` and
    `tactics_rules._lookup_monster`.
    """
    assert FIXTURE_SRD.exists(), (
        "the real dataset is absent, so 'stale' has nothing to be stale against")
    lookup = _lookup_module()
    monkeypatch.setattr(lookup, "DATA_FILE", str(tmp_path / "stale.json"))
    monkeypatch.setattr(lookup, "_data", {}, raising=False)
    monkeypatch.setattr(lookup, "_loaded", False, raising=False)
    with open(tmp_path / "stale.json", "w", encoding="utf-8") as fh:
        json.dump(_STALE, fh)
    return lookup


def test_a_stale_spell_record_is_refused_rather_than_half_used(production_srd,
                                                              stale_dataset):
    """A pre-mechanics record is treated as missing, not as truth.

    A dataset from before spell mechanics has records whose `mechanics` carry no
    `casting` key. Every one of those would fall back to a 5 ft range -- Fire
    Bolt, a 120 ft spell, becomes point-blank -- so `_current()` refuses them.

    Mutant: dropping the `_current` check from `tactics_spells._srd` makes Fire
    Bolt resolve here, and the `pytest.raises` below does not fire. The explicit
    `lookup_record` assertion first is what makes the mutant's failure legible:
    the record really is in the dataset, it is the *resolver* that must refuse it.
    """
    from tests import tactics_fixtures as fx

    assert stale_dataset.lookup_record("Fire Bolt", category="spell") is not None
    assert production_srd("fire bolt") is None
    with pytest.raises(ValueError) as excinfo:
        _spells_module().resolve(fx.caster(), "fire bolt")
    assert "build the SRD" in str(excinfo.value)


def test_a_monster_without_structured_actions_names_the_rebuild(stale_dataset):
    """The other stale branch: a monster record predating structured actions.

    Mutant: `_lookup_monster` returning the record regardless, which raises
    `AttributeError` deep inside `token_from_monster` instead of telling the GM
    to rebuild.
    """
    with pytest.raises(ValueError) as excinfo:
        _rules_module()._lookup_monster("goblin")
    assert "predates structured actions" in str(excinfo.value)
    assert "build_srd.py" in str(excinfo.value)


def test_a_cached_spell_from_an_old_dataset_is_replaced_not_trusted(production_srd):
    """The stale check also guards the caster's cached spellbook, not just the read.

    `mechanics()` deletes a cached entry whose mechanics fail `_current` and
    looks it up again. With the real dataset present, the re-lookup succeeds and
    the range comes back as 120 ft -- the number the stale cache lacked.

    Mutant: removing the `if base is not None and not _current(base)` branch
    leaves the stale entry in place and `range` stays None.
    """
    from tests import tactics_fixtures as fx

    assert FIXTURE_SRD.exists()
    caster = fx.caster()
    caster.extra["spellbook"] = {"fire bolt": {"damage_type": "fire", "attack": "ranged",
                                               "damage_at_level": {"1": "1d10"}}}
    spec = _spells_module().resolve(caster, "fire bolt")
    assert spec["range"] == 120
    assert "casting" in caster.extra["spellbook"]["fire bolt"]


def test_a_half_built_dataset_answers_for_the_categories_it_has(absent_dataset):
    """Some categories present, some not: that is the shape a dead fetch leaves.

    The build refuses to write an all-empty dataset, and `lookup` tolerates a
    missing one, so the middle case is the one that has to work.

    Mutant: an `_load` that raises on a missing key, or one that treats a
    partial file as absent and throws away the monsters.
    """
    partial = {"_meta": {"total_records": 1, "record_counts": {"monsters": 1}},
               "monsters": [{"name": "Goblin", "index": "goblin", "hp": 7, "ac": 15,
                             "cr": "1/4", "xp": 50}]}
    with open(absent_dataset.DATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(partial, fh)
    absent_dataset._data, absent_dataset._loaded = {}, False

    assert absent_dataset.lookup_record("goblin", category="monster")["hp"] == 7
    assert absent_dataset.lookup_record("fire bolt", category="spell") is None
    assert absent_dataset.lookup("goblin", category="monster").startswith("## Goblin")