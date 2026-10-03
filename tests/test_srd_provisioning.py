"""dnd-gm#289: the generated SRD dataset is a provisioned input, not an optional one.

WHY THIS FILE EXISTS
====================
`systems/dnd5e/data/dnd5e_srd.json` is generated output and is gitignored. Nothing
built it. On engine `origin/main` at `ed30a7e`, same commit and same machine:

    no dataset    9 failed, 3355 passed, 107 skipped, 2 errors
    with dataset  4 failed, 3491 passed,  51 skipped

Ten tests were written against a file that no CI run and no fresh clone had. The
suite was not broken, it was unprovisioned, and it had been red on that alone
since PR #202.

This file is the part of the answer that is a *test* rather than a workflow step.
`scripts/provision_srd.py` builds the dataset before the suite and refuses to leave
one that is unusable. Two things follow, and this file holds both down:

  1. The gate is exercised by the suite, so it cannot rot into a function that
     accepts anything. `tests/test_srd_provisioning.py::test_a_dataset_that_lost_its
     _fvtt_half_is_refused` is the mutant-proof: delete the check and this goes red.

  2. The dataset in *this* checkout has to satisfy the gate. That is what makes
     "CI provisions the dataset" checkable from inside a test run rather than only
     by reading a YAML file -- if the provisioning step is ever dropped from the
     workflow, this test is one of the things that goes red, and so are the ten
     tests that read the data.

WHAT WOULD MAKE THESE FAIL
==========================
  - the gate accepting a dataset with no `features` category, which is what a
    refused `api.github.com` tree fetch leaves behind, and what
    `build_srd.py --no-fvtt` writes on purpose
  - the gate accepting a 2024 dataset, or one with no recorded edition, read by an
    engine that adjudicates 2014
  - the gate accepting a category below its recorded floor, or `_meta` disagreeing
    with the records beside it
  - the `provision_srd.py --check` entry point the workflow calls exiting non-zero
    on the dataset the workflow just built -- the two drifting apart is the failure
    this last test exists for, and nothing else in the suite would notice
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provision_srd.py"
DATASET = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"

_spec = importlib.util.spec_from_file_location("provision_srd", SCRIPT)
provision = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(provision)


def _dataset_with(**overrides):
    """A dataset meeting every floor, with `overrides` applied to `_meta`."""
    dataset = {"_meta": {"edition": provision._edition(),
                         "built_at": "1970-01-01T00:00:00Z",
                         "total_records": 0,
                         "record_counts": {c: 0 for c in provision.FLOORS}},
               **{c: [{"name": f"fixture {i}"} for i in range(floor)]
                  for c, floor in provision.FLOORS.items()}}
    for cat, name, _why in provision.SMOKE:
        dataset[cat].append({"name": name})
    for key, value in overrides.items():
        dataset["_meta"][key] = value
    dataset["_meta"]["record_counts"] = {
        c: len(dataset[c]) for c in dataset if isinstance(dataset[c], list)}
    dataset["_meta"]["total_records"] = sum(
        len(v) for v in dataset.values() if isinstance(v, list))
    return dataset


# ── the gate has teeth ────────────────────────────────────────────────────────

def test_a_dataset_that_lost_its_fvtt_half_is_refused(tmp_path):
    """The failure mode this gate exists for, reproduced in miniature.

    `_fetch` returns None on any exception, and `api.github.com`'s unauthenticated
    limit is 60/hour, so one refused tree fetch empties `features`. `build_srd.py`
    writes that dataset anyway -- its own refusal only fires when *every* category
    is empty -- and the suite then runs 260 features short and says nothing.

    Mutant: deleting the missing-category check from `provision_srd.verify` leaves
    this green, which is the mutation that would put a silent coverage drop back.
    """
    dataset = _dataset_with()
    del dataset["features"]

    path = tmp_path / "no-fvtt.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dataset, fh)

    problems = provision.verify(path)
    assert problems, ("a dataset with no 'features' category was accepted: it is "
                      "what a rate-limited build leaves behind, and what "
                      "--no-fvtt writes on purpose")
    assert any("features" in p for p in problems), problems


def test_a_dataset_that_is_the_wrong_edition_is_refused(tmp_path):
    """Mutant: dropping the edition comparison leaves this green.

    The edition is the one field that decides whether the rest of the file is
    usable at all: this engine adjudicates 2014, and a 2024 dataset answers a 2014
    rules question with 2024 text.
    """
    path = tmp_path / "2024.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_dataset_with(edition="2024"), fh)

    problems = provision.verify(path)
    assert problems, "a 2024 dataset was accepted by a 2014 engine"
    assert any("2014" in p for p in problems), problems


def test_a_dataset_under_a_recorded_floor_is_refused(tmp_path):
    """A category that shrank is an upstream failure, not a smaller SRD.

    The floors are minimums, so upstream may add records freely; they fire when a
    source stops answering, which is the moment a floor earns its keep.
    """
    dataset = _dataset_with()
    dataset["monsters"] = dataset["monsters"][:5]

    path = tmp_path / "short.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dataset, fh)

    problems = provision.verify(path)
    assert problems, "a dataset 295 monsters short of its floor was accepted"
    assert any("floor" in p for p in problems), problems


def test_the_records_this_suite_reads_by_name_are_required(tmp_path):
    """Floors alone are not enough: a full-size dataset can still be the wrong one.

    A dataset that meets every floor but has lost Mage Armor leaves
    `test_dm_boundary_lens.py` red with a message about a spell that "was not
    cast", which points the reader at the DM boundary rather than at the data.

    Mutant: emptying `provision_srd.SMOKE` leaves this green.
    """
    dataset = _dataset_with()
    cat, name, _why = provision.SMOKE[1]              # spells / Mage Armor
    dataset[cat] = [r for r in dataset[cat]
                    if str(r["name"]).lower() != name.lower()]

    path = tmp_path / "no-mage-armor.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dataset, fh)

    problems = provision.verify(path)
    assert problems, f"a dataset with no {name!r} was accepted"
    assert any(name in p for p in problems), problems


def test_an_absent_dataset_is_refused_and_the_message_names_the_command(tmp_path):
    """The message is the whole point of refusing rather than skipping.

    A skip says "not checked". This says which command checks it.
    """
    problems = provision.verify(tmp_path / "there-is-no-such-dataset.json")
    assert problems, "an absent dataset was accepted"
    message = "\n".join(problems)
    assert "scripts/provision_srd.py" in message, message
    assert "gitignored" in message, message


# ── the gate, against this checkout ───────────────────────────────────────────

def test_the_dataset_in_this_checkout_satisfies_the_gate():
    """If the workflow stopped provisioning, this is one of the tests that go red.

    The dataset is absent in a fresh clone, so this fails there -- and
    `verify(DATASET)`'s own report carries the provisioning command, so the failure
    says what to do rather than only that something is wrong.
    """
    problems = provision.verify(DATASET)
    assert problems == [], problems


def test_provision_srd_check_exits_zero_on_this_checkout():
    """The entry point the workflow runs is the one under test, not a relative.

    `main()` can drift from `verify()` -- a wrong exit code, a `--check` that
    silently builds, a path resolved against the wrong root -- and nothing else in
    the suite would notice, because everything else calls `verify` directly. This
    runs the command as a subprocess from the repository root.
    """
    proc = subprocess.run([sys.executable, str(SCRIPT), "--check"],
                          capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0, (
        f"provision_srd.py --check exited {proc.returncode} on a dataset its own "
        f"verify() accepts.\nstdout: {proc.stdout}\nstderr: {proc.stderr}")
    assert "verified" in proc.stdout, proc.stdout
