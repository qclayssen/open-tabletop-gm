#!/usr/bin/env python3
"""provision_srd.py — put the generated SRD dataset in the checkout, or prove it is not there.

WHY THIS EXISTS (dnd-gm#289)
============================
`systems/dnd5e/data/dnd5e_srd.json` is generated output and is gitignored, so a
fresh clone and every CI run started without it. Ten tests were written against
it. Measured on engine `origin/main` at `ed30a7e`, same commit, same machine:

    no dataset    9 failed, 3355 passed, 107 skipped, 2 errors
    with dataset  4 failed, 3491 passed,  51 skipped

The suite was not broken. It was **unprovisioned**, and had been red on that
alone since PR #202.

So a decision had to be made about what CI owes the suite, and the three answers
are not equivalent:

  build it        CI matches a provisioned developer machine, and the stale-record
                  paths in `test_srd_fixture_isolation.py` are covered at all.
                  Cost: `build_srd.py` reaches the network — 5e-bits/5e-srd-api and
                  foundryvtt/dnd5e — so CI inherits an upstream that moves.
  skip it         honest about the dependency, and dishonest about coverage: a
                  skipped module and a passing module are the same green tick.
  commit it       the shape most of this suite already uses. Rejected here: the
                  file is 1.6 MB of MIT+OGL and CC-BY-4.0 text that
                  `.gitignore` deliberately excluded on licensing grounds, and
                  reversing that is a policy call, not a bug fix.

This is the build-it answer, with the part that makes it sound.

THE PART THAT MAKES IT SOUND: A GATE, NOT JUST A BUILD
======================================================
`build_srd.py` decides for itself whether the result is worth keeping. It writes
unless *every* category came back empty, so a build that lost the whole
FoundryVTT half — one 403 from `api.github.com`, whose unauthenticated limit is
60/hour and which `_fetch` turns into `None` on any exception — writes a
1.2 MB dataset with `features: []`, prints a warning to stderr, and exits 0.
The suite then runs 260 features short and reports green. That is the false
green this file exists to prevent, so provisioning here is two steps and the
second is not optional:

    build  →  verify

`verify` refuses a dataset that is the wrong edition, is missing a category, has
a category under its recorded floor, has an inconsistent `total_records`, or is
missing one of the specific records the suite reads. Floors, not exact counts:
upstream legitimately grows, and a tripwire should fire on a pack that vanished,
not on a monster that was added.

Deliberately NOT a skip
======================
When the dataset is missing, the affected tests **fail**. They do not skip. A
skip is a green tick with no reason attached to it in a checks list, and this
suite already has three of that shape — `test_monster_defenses.py` and
`test_export_bestiary.py` hid 47 tests behind `skipUnless(DATA.exists())`, and
playwright's absence once turned 80 display tests green. This file's own message
names the command that fixes the problem, which is the one thing a bare skip
cannot do. `docs/FIRST-SESSION.md` and `docs/TACTICAL-COMBAT.md` still say
`build_srd.py --no-fvtt`, which is a dataset this gate rejects; they now point here.

OFFLINE
=======
With the dataset already present, this script never touches the network — it
verifies what is there and exits. That is what makes it safe to run before every
suite and safe to run on a machine with no egress.

Usage
-----
    python3 scripts/provision_srd.py              # build if absent, then verify
    python3 scripts/provision_srd.py --check      # verify only; never builds
    python3 scripts/provision_srd.py --force      # rebuild even if present
    python3 scripts/provision_srd.py --selftest   # prove this checker can fail

Exit codes: 0 provisioned and verified, 1 verification failed (a dataset is there
and it is not usable), 2 it could not be provisioned (the build did not run or
did not finish).
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"
BUILD = ROOT / "systems" / "dnd5e" / "build_srd.py"

def _edition() -> str:
    """The edition the builder produces, read out of its source with `ast`.

    Read rather than copied, so the gate cannot end up enforcing a dead constant
    after the engine changes edition. Parsed rather than imported, because
    importing `build_srd` prints a PyYAML nag on a checkout without PyYAML and
    runs 40 lines of module setup to read one string -- and `--check` is
    documented as side-effect-free.

    Raises if the assignment is missing, which is the right outcome: a builder
    that stopped recording its edition cannot be gated on one.
    """
    tree = ast.parse(BUILD.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if any(isinstance(t, ast.Name) and t.id == "EDITION" for t in targets):
            value = ast.literal_eval(node.value)
            return str(value)
    raise ValueError(f"{BUILD.name} does not assign EDITION at module level")


#: Minimum records per category. Recorded at `ed30a7e` (spells 319, equipment
#: 237, magic_items 362, conditions 15, monsters 334, features 260) and set below
#: that so ordinary upstream movement does not trip them. Raise them, never lower
#: them to make a red build go green: a floor that has been lowered to fit the
#: data is not a floor.
#:
#: `features` is the load-bearing one. It is the only category that comes from
#: the half of the build which fails *softly* — a refused tree fetch empties it —
#: and it is the only one `build_srd.py --no-fvtt` leaves out on purpose. That is
#: why this script has no `--no-fvtt`: the provisioner builds the whole dataset or
#: it builds nothing.
FLOORS = {
    "spells": 300,
    "equipment": 200,
    "magic_items": 300,
    "conditions": 14,
    "monsters": 300,
    "features": 240,
}

#: Records the suite reads by name. A dataset that meets every floor but has lost
#: these would still leave a test red with a message about a spell that "was not
#: cast", which points at the wrong file. Each entry names the test that fails
#: without it.
SMOKE = [
    ("spells", "Fire Bolt", "test_srd_fixture_isolation.py, both stale-record tests"),
    ("spells", "Mage Armor", "test_dm_boundary_lens.py::test_an_unclaimed_status_question"
                            "_can_reach_a_cast_and_spend_a_slot"),
    ("monsters", "Goblin", "test_srd_contracts.py, three monster-record contracts"),
    ("monsters", "Giant Frog", "test_rest_out_of_combat.py, the frog-pond campaign"),
]


def _absent_message() -> str:
    return (
        f"{DATASET.relative_to(ROOT)} is absent. It is generated output and is "
        f"gitignored, so a fresh clone and every CI run start without it.\n"
        f"  Provision it with:  python3 scripts/provision_srd.py\n"
        f"  Then check it with:  python3 scripts/provision_srd.py --check"
    )


# ─── verification ─────────────────────────────────────────────────────────────

def verify(path: pathlib.Path) -> list[str]:
    """Return the list of reasons `path` is not a usable dataset. Empty is good."""
    if not path.exists():
        return [_absent_message()]
    try:
        with open(path, encoding="utf-8") as fh:
            dataset = json.load(fh)
    except (OSError, ValueError) as exc:
        return [f"{path.name} does not parse as JSON: {exc}"]
    if not isinstance(dataset, dict):
        return [f"{path.name} is a {type(dataset).__name__}, not an object"]

    problems: list[str] = []
    meta = dataset.get("_meta")
    if not isinstance(meta, dict):
        problems.append("no _meta block: this file was not written by build_srd.py, "
                        "or was written by a version old enough to omit it")
        return problems

    # The edition decides whether the rest of the file is usable at all. A 2024
    # dataset read by this engine answers 2014 rules questions with 2024 text, and
    # a dataset with no recorded edition is the same risk with less information.
    try:
        edition = _edition()
    except Exception as exc:                                   # noqa: BLE001
        return [f"cannot read EDITION from {BUILD.name}: {exc}"]
    got = meta.get("edition")
    if got != edition:
        problems.append(f"edition is {got!r}, this engine adjudicates {edition!r}"
                        + ("" if got else " (no edition recorded: a pre-edition build)"))

    counts = meta.get("record_counts")
    if not isinstance(counts, dict):
        counts = {}
        problems.append("no _meta.record_counts")

    for cat, floor in FLOORS.items():
        records = dataset.get(cat)
        if not isinstance(records, list):
            problems.append(f"category {cat!r} is missing: it is either a build that "
                            f"failed partway or one written by --no-fvtt")
            continue
        if len(records) < floor:
            problems.append(f"category {cat!r} holds {len(records)} records, under the "
                            f"floor of {floor}: an upstream source is failing, not shrinking")
        if counts.get(cat) != len(records):
            problems.append(f"_meta.record_counts[{cat!r}] says {counts.get(cat)!r} but "
                            f"the file holds {len(records)}")

    total = meta.get("total_records")
    live = sum(len(v) for v in dataset.values() if isinstance(v, list))
    if isinstance(total, int) and total != live:
        problems.append(f"_meta.total_records says {total} but the file holds {live}")

    for cat, name, why in SMOKE:
        records = dataset.get(cat)
        if not isinstance(records, list):
            continue
        if not any(isinstance(r, dict) and str(r.get("name", "")).lower() == name.lower()
                   for r in records):
            problems.append(f"{cat[:-1]} {name!r} is absent, and {why} reads it")
    return problems


# ─── building ─────────────────────────────────────────────────────────────────

def build() -> int:
    """Run build_srd.py as a child process, not an import.

    An import would make this script's exit code depend on the builder's module
    level, and the builder calls `sys.exit()` on a dead source. As a child, its
    stderr is the CI log and its non-zero status is a provisioning failure, which
    is the distinction the two exit codes below exist to keep.
    """
    # PyYAML first, and said plainly. `build_srd.py` without it skips the whole
    # FoundryVTT half *by design* and exits 0 -- 5e-bits spells and monsters, no
    # class or racial features -- so the missing dependency would be discovered
    # downstream as a mysterious dataset, or as this script's own gate refusing it
    # with a message about `features` that names the wrong cause. The
    # `floor-310` job is the case that matters: it installs pytest and nothing
    # else, deliberately, so provisioning there has to say what it needs rather
    # than quietly produce half a dataset.
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("provision-srd: PyYAML is not installed, and the FoundryVTT half of "
              "the dataset cannot be built without it.\n"
              "  Install it with:  python3 -m pip install pyyaml\n"
              "  (build_srd.py --no-fvtt would build a dataset this gate refuses: "
              "it has no 'features' category.)")
        return 2
    print(f"provision-srd: building {DATASET.relative_to(ROOT)} "
          f"(network: 5e-bits/5e-srd-api, foundryvtt/dnd5e)")
    return subprocess.call([sys.executable, str(BUILD)])


# ─── selftest ─────────────────────────────────────────────────────────────────

def selftest() -> int:
    """Prove the gate rejects what it claims to.

    A gate that cannot fail is the thing this whole file is an answer to, so the
    mutations are written out and each one is checked to turn the gate red. Every
    fixture is written to a temporary directory; nothing here touches the real
    dataset and nothing here touches the network.

    What this cannot do is catch a mutation that disables its own reporting --
    replacing the `problems.append` calls with `pass` leaves this exiting 0 with
    "SELFTEST FAILED" printed on every fixture. That is the same limit
    `scripts/check_py_floor.py` documents, and the workflow greps for the string
    rather than trusting the exit code, for the same reason.
    """
    def good() -> dict:
        """A dataset that meets every floor and carries every smoke record.

        Built to the floors rather than from a captured real file, so the selftest
        needs no network and no dataset of its own to exist first.
        """
        dataset: dict = {"_meta": {
            "edition": _edition(), "built_at": "1970-01-01T00:00:00Z",
            "total_records": 0, "record_counts": {c: 0 for c in FLOORS}}}
        for cat, floor in FLOORS.items():
            dataset[cat] = [{"name": f"fixture {i}"} for i in range(floor)]
        # Add the smoke records by name, in the category SMOKE says they live in.
        # Built from SMOKE rather than retyped, so adding a smoke record without
        # teaching this fixture about it cannot leave the gate untested.
        for cat, name, _why in SMOKE:
            dataset[cat].append({"name": name})
        for cat in FLOORS:
            dataset["_meta"]["record_counts"][cat] = len(dataset[cat])
        dataset["_meta"]["total_records"] = sum(
            len(v) for v in dataset.values() if isinstance(v, list))
        return dataset

    def retotal(d: dict) -> dict:
        """Make `_meta` agree with the categories again.

        Every fixture below mutates one thing. If it also leaves `_meta` stale --
        which is what happens the moment you delete a category or truncate one --
        then `total_records` disagrees with the file as a side effect, the gate
        rejects the fixture for that reason instead, and the check this fixture
        was written to prove is never reached. Measured: the floor check could be
        deleted outright and this selftest still passed, for exactly that reason.
        """
        d["_meta"]["total_records"] = sum(
            len(v) for v in d.values() if isinstance(v, list))
        return d

    def drop_cat(d: dict) -> dict:
        d.pop("features")
        d["_meta"]["record_counts"].pop("features")
        return retotal(d)

    def under_floor(d: dict) -> dict:
        d["monsters"] = d["monsters"][:1]
        d["_meta"]["record_counts"]["monsters"] = 1
        return retotal(d)

    def drop_smoke(d: dict) -> dict:
        """Lose one record the suite reads by name, keeping every floor met.

        This is the fixture the floors alone cannot catch, which is why SMOKE
        exists at all: a dataset can be full-size and still not carry the spell
        `test_dm_boundary_lens.py` casts.
        """
        cat, name, _why = SMOKE[3]                      # monsters / Giant Frog
        d[cat] = [r for r in d[cat] if str(r["name"]).lower() != name.lower()]
        d["_meta"]["record_counts"][cat] = len(d[cat])
        return retotal(d)

    def wrong_edition(d: dict) -> dict:
        d["_meta"]["edition"] = "2024"
        return d

    def no_edition(d: dict) -> dict:
        del d["_meta"]["edition"]
        return d

    def lying_total(d: dict) -> dict:
        d["_meta"]["total_records"] += 7
        return d

    def lying_counts(d: dict) -> dict:
        d["_meta"]["record_counts"]["spells"] += 3
        return d

    def no_meta(d: dict) -> dict:
        d.pop("_meta")
        return d

    # Each fixture names the reason it must be rejected by, not merely that it
    # must be rejected at all. Without that third column these fixtures are not
    # independent: measured, the `under the floor` fixture was still being caught
    # by the `total_records` check that its own truncation had also tripped, so
    # deleting the floor check entirely left the selftest green. A selftest that
    # passes for the wrong reason is the same false green as a suite that skips,
    # one level down.
    MUST_REJECT = [
        ("a 2024 dataset", wrong_edition, "this engine adjudicates"),
        ("a dataset with no recorded edition", no_edition, "no edition recorded"),
        ("a build that lost the FoundryVTT half", drop_cat, "is missing"),
        ("a category under its floor", under_floor, "under the floor of"),
        ("a record the suite reads by name", drop_smoke, "is absent, and"),
        ("a total_records that disagrees with the file", lying_total,
         "_meta.total_records says"),
        ("a record_counts that disagrees with the file", lying_counts,
         "record_counts["),
        ("a file with no _meta block", no_meta, "no _meta block"),
    ]

    print("provision-srd: selftest — every fixture below must be REJECTED, "
          "and by its own reason")
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)

        # The control. Without it, a gate that rejects everything would pass this
        # whole selftest, which is the shape of bug this file exists to prevent.
        ok = tmpdir / "good.json"
        _write(ok, good())
        problems = verify(ok)
        if problems:
            failed += 1
            print(f"  NOT REJECTED: a well-formed dataset -- the gate rejects "
                  f"everything:\n      {problems[0]}")
        else:
            print("  rejected nothing: a well-formed dataset verified")

        absent = verify(tmpdir / "there-is-no-such-dataset.json")
        if not absent:
            failed += 1
            print("  NOT REJECTED: an absent dataset -- the gate passed a file that "
                  "does not exist")
        elif "gitignored" not in absent[0]:
            failed += 1
            print(f"  WRONG REASON: an absent dataset was rejected for {absent[0]!r}, "
                  f"which does not say the file is generated output")

        for label, mutate, reason in MUST_REJECT:
            path = tmpdir / (label.replace(" ", "-") + ".json")
            _write(path, mutate(good()))
            problems = verify(path)
            if not problems:
                failed += 1
                print(f"  NOT REJECTED: {label} -- the gate passed it")
            elif not any(reason in p for p in problems):
                failed += 1
                print(f"  WRONG REASON: {label} was rejected, but for a different "
                      f"reason than the one it exists to prove. The gate reported:\n"
                      f"      {problems[0]}\n"
                      f"    and this fixture exists to prove {reason!r} still fires.")
            else:
                hit = next(p for p in problems if reason in p)
                print(f"  rejected {label}: {hit}")

        # A file that is not JSON at all: the gate must say so rather than
        # crashing on json.load, because a traceback is not a provisioning report.
        broken = tmpdir / "broken.json"
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        problems = verify(broken)
        if not problems or "does not parse as JSON" not in problems[0]:
            failed += 1
            print("  NOT REJECTED, or not as unparseable: a file that is not JSON")
        else:
            print(f"  rejected a file that is not JSON: "
                  f"{problems[0].splitlines()[0]}")

    if failed:
        print(f"provision-srd: SELFTEST FAILED -- {failed} fixture(s) did not get the "
              f"rejection they exist to prove")
        return 1
    print(f"provision-srd: selftest passed, {len(MUST_REJECT) + 3} fixtures, "
          f"{len(FLOORS)} categories, {len(SMOKE)} smoke records")
    return 0


def _write(path: pathlib.Path, dataset: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dataset, fh)


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="build the generated SRD dataset if it is absent, then verify "
                    "that it is usable")
    ap.add_argument("--check", action="store_true",
                    help="verify only; never build and never touch the network")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if a dataset is already present")
    ap.add_argument("--selftest", action="store_true",
                    help="prove this gate rejects the fixtures it claims to, then exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.check and args.force:
        print("provision-srd: --check and --force are opposites; pick one")
        return 2

    if args.check:
        problems = verify(DATASET)
        if problems:
            print("provision-srd: the dataset is not usable:")
            for p in problems:
                print(f"  - {p}")
            return 1
        print(f"provision-srd: {DATASET.relative_to(ROOT)} verified")
        return 0

    if DATASET.exists() and not args.force:
        # Present: verify, never rebuild. This is the path a cached or already
        # provisioned checkout takes, and it is why this script can run before
        # every suite without costing a network round trip.
        print(f"provision-srd: {DATASET.relative_to(ROOT)} is already present; "
              f"verifying without rebuilding (--force to rebuild)")
    else:
        rc = build()
        if rc != 0:
            print(f"provision-srd: could not build the dataset (build step returned "
                  f"{rc}). Nothing is provisioned; the tests that read it will fail "
                  f"rather than skip, which is the intended signal.")
            return 2

    problems = verify(DATASET)
    if problems:
        print("provision-srd: the build did not produce a usable dataset:")
        for p in problems:
            print(f"  - {p}")
        print("  Refusing to leave a dataset that would make the suite green by "
              "running less of it.")
        return 1

    with open(DATASET, encoding="utf-8") as fh:
        meta = json.load(fh)["_meta"]
    print(f"provision-srd: {DATASET.relative_to(ROOT)} verified -- "
          f"{meta['total_records']} records, edition {meta['edition']}, built "
          f"{meta['built_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
