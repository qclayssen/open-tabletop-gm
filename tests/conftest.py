"""Per-test setup for the whole suite: `sys.path`, and the fixture SRD patch.

Why the sys.path half exists
============================
Three test modules could not be collected on their own. Measured, not guessed:
running each of the 137 files in `tests/` alone with the exact command CI uses
(`python -m pytest tests/ -q -n 4`, which adds only the cwd to `sys.path`),
three errored at import:

    tests/test_schemas.py        ModuleNotFoundError: No module named 'tactics'
    tests/test_dice_rng.py      ModuleNotFoundError: No module named 'dice'
    tests/test_localdm_stall.py ModuleNotFoundError: No module named 'localdm'

They passed in a full-suite run because some *other* module puts `scripts/` on
`sys.path` while the collection is being built -- `tests/localdm_fakes.py` does
it, and it happens to be imported first. So the suite's green depended on
collection order, which is the defect class that has bitten this repo before and
the one `agents/dev/verifier.md` asks about by name.

What it cost, concretely
------------------------
A mutation proof against one of those three files reports **a collection error**
where it should report a failed assertion. Both are non-zero, so a harness that
only checks the exit code calls it a proof. That is how a repair gets called
verified while pinning nothing, and it is not hypothetical: two of the proofs in
the #234 brief were wrong that way until the failure *kind* was checked as well
as the exit status.

So this is a three-line fix and not a framework: one path, added once, at the
place pytest already guarantees runs before any test module is imported.

Why the SRD-patch half exists
=============================
`tests/tactics_fixtures.py` used to replace the production
`tactics_spells._srd` with a fixture lookup by a bare assignment at import time:

    spells_rules._srd = srd_spell

and nothing ever put it back. Whichever test module imported `tactics_fixtures`
first therefore decided, for the rest of the process, whether the spell resolver
read the checked-in fixture or the real `dnd5e_srd.json`. That is a global
mutation with no teardown: it made every later test order-dependent, including
the ones that mean to exercise the production lookup at all.

The patch now has the same lifetime as a test:

  `fixture_srd`      autouse. Installs the fixture lookup before the test and
                     restores the PRODUCTION function after it -- including when
                     the test fails, which the old assignment never did. It
                     restores the captured production function rather than
                     "whatever was there when the test began", because the
                     latter would faithfully restore a previous test's leak and
                     so make the invariant untestable from inside a test.
  `production_srd`   requested explicitly by the tests that must exercise the
                     real lookup. It puts the production function back for the
                     duration of that one test and yields it.
  `raw_srd`          requested by the pair of tests that check the invariant at
                     all. They opt out of the *install* half, so they observe the
                     module exactly as the previous test left it -- which is the
                     only way a leak is visible from inside a test, since the
                     next test's own setup would otherwise overwrite it before
                     anything could look.

Fixture ordering does the rest: at the same scope pytest sets up autouse
fixtures before explicitly-requested ones, so `production_srd` always runs after
`fixture_srd` and always undoes it on the way out. Neither fixture needs to know
the other exists.

Why `fixture_srd` is autouse rather than requested per module: 32 modules import
`tactics_fixtures`, and making each of them also request a fixture is a change to
all 32 for the sake of a rule that should hold automatically. Why it is not
session-scoped: a session fixture would reintroduce the exact leak being fixed,
only with a tidier name.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SRD_DATASET = pathlib.Path(__file__).resolve().parent.parent / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"


def require_srd_dataset():
    """Fail unless the generated SRD dataset is present. Return it if so.

    WHY A FAILURE AND NOT A SKIP (dnd-gm#289)
    =========================================
    `test_monster_defenses.py` and `test_export_bestiary.py` used to carry
    `skipUnless(DATA.exists())` / `skipif(not DATA.exists())`, which hid 47 tests
    behind one green tick. The dataset is generated output and is gitignored, so
    those gates were *always* closed on a fresh clone and on every CI run -- the
    suite reported that it had checked nothing about monster defenses or about the
    bestiary exporter, and reported it as a pass. This repo already has checks of
    that shape: playwright absent turns 80 display tests green, and a
    `--no-fvtt` dataset's empty `features` key reads as "0 of 0 covered".

    A skip is the wrong tool for a missing *input*. It is the right tool for a
    capability the platform does not have -- no browser, no `node`, no `mkfifo` --
    where the honest answer really is "not checked here". The dataset is not that:
    it is a build step this repository owns, it takes 79 seconds, and CI now runs
    it. So an unprovisioned checkout gets a failure that names the command, which
    is the one thing a bare skip cannot do.

    `pytest.fail` rather than an assert at module scope: a module-level assert is
    a *collection error*, and `agents/dev/verifier.md` is right that a collection
    error is not a failed assertion.

    What kind of red this produces depends on the caller, and the difference is
    deliberate rather than incidental. Called from a `unittest.TestCase.setUp` it
    reports FAILED, which is what `test_monster_defenses.py` does. Called from a
    pytest fixture it reports as an ERROR at setup, because that is where pytest
    surfaces a failed setup phase; `test_export_bestiary.py` does that. Both are
    red, counted separately, and carry this message. The ERROR form is not ideal
    and was kept because the alternative -- 21 copies of this call in the test
    bodies -- is worse: it puts the precondition in 21 places instead of one, and
    the next person adding a test to the file has a 21-in-21 chance of forgetting
    it, which is the failure this whole change is about.
    """
    if not SRD_DATASET.exists():
        pytest.fail(
            f"{SRD_DATASET.name} is absent. It is generated output and is "
            f"gitignored, so a fresh clone and every CI run start without it.\n"
            f"  Provision it with:  python3 scripts/provision_srd.py\n"
            f"  Then check it with:  python3 scripts/provision_srd.py --check",
            pytrace=False)
    return SRD_DATASET


def _spells_module():
    """The `systems/dnd5e/tactics_spells.py` module object, as production holds it."""
    import tactics.rules as rules_mod

    return sys.modules[type(rules_mod.load("dnd5e")).__module__]._spells_module()


def _fixture_lookup():
    from tests import tactics_fixtures

    return tactics_fixtures.srd_spell


# Captured before any fixture can patch it, so the teardown has something true to
# restore even if this module is the first thing a run ever touches. Loading the
# source file under a throwaway module name guarantees it: this conftest is
# imported before any test module, so `tactics_spells` here is production's.
_PRODUCTION_SRD = _spells_module()._srd


@pytest.fixture(autouse=True)
def fixture_srd(request):
    """The checked-in SRD fixture for this test; production `_srd` restored after.

    Autouse, so the spell resolver's data source is the same in every test no
    matter which module pytest collected first. The `finally` is the whole fix:
    it runs on failure as well as on success, and it restores the captured
    production function rather than the previous occupant of the slot.
    """
    spells = _spells_module()
    if "raw_srd" not in request.fixturenames:
        spells._srd = _fixture_lookup()
    try:
        yield _fixture_lookup()
    finally:
        spells._srd = _PRODUCTION_SRD


@pytest.fixture
def production_srd():
    """The real `tactics_spells._srd`, for a test that must exercise it.

    Runs after the autouse `fixture_srd` and yields the production function.
    Teardown is the reverse order, so `fixture_srd` gets its own teardown back
    and the module is left exactly as the suite found it.
    """
    spells = _spells_module()
    spells._srd = _PRODUCTION_SRD
    yield _PRODUCTION_SRD


@pytest.fixture
def raw_srd():
    """Do not install the fixture lookup, so this test sees what came before it.

    Yields the module. The autouse teardown still runs, so a test that patches
    something is cleaned up; what this fixture changes is only the install half.
    """
    return _spells_module()