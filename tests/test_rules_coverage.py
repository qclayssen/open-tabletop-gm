"""#135: the coverage instrument, and what it must never report.

WHY
===
`ROADMAP-ideas.md` RI5 could not state, and could not falsify, what fraction of
2014 5e the engine implements. The claim it carried was "58 of 319 spells
(18.2%)", produced by a method nobody recorded. This file is the instrument, and
its most important job is the one that is least like a coverage number.

THE THREE STATES, AND WHY THE THIRD ONE IS THE POINT
=====================================================
Every record carries exactly one of `mechanical`, `reference`, `unmeasured`. The
third exists because the dataset is gitignored (`.gitignore:50`): on a fresh
clone `dnd5e_srd.json` does not exist, and an instrument that reported "0% of 319
spells" would be indistinguishable from one reporting that the engine implements
nothing. Both are wrong. The second is worse, because a 0% reads as a
measurement. So with no dataset the report says `unmeasured`, every count is
`null`, and `dataset_absent` names the reason.

The same rule applies one level down. A dataset with no `features` key (which is
what `build_srd.py --no-fvtt` writes) has features nobody measured, not zero
features. Reporting that axis as "0 of 0, 100% covered" would convert a missing
upstream fetch into a success.

These are the assertions that most directly test the requirement. Everything else
here is bookkeeping on top of them.

THE HEADLINE NUMBER DOES NOT REPRODUCE
======================================
RI5 says 58 of 319 spells. The instrument says **54** at a level 5 caster and
**55** at level 1, and the difference is Eldritch Blast: `tactics_spells.BUILTIN`
sets `narrate_from_level: 5` because the SRD record holds one number and the
spell deals more beams above 4th. Spell coverage is a function of the caster, so
the measurement conditions are part of the report rather than an assumption
buried in it. `test_the_headline_number_does_not_reproduce` pins the 54 and says
where the roadmap's number went.

WHAT WOULD MAKE THESE FAIL
==========================
  - a `total` that is 0 where it should be null, for an absent dataset
  - a `mechanical` count that is 0 where it should be null
  - a `mechanical` row carrying a reason, or a non-mechanical row carrying none
  - a reason outside `REASONS`, or one paired with a state it may not pair with
  - the defect counter folded into the coverage percentage
  - a refuted 2014 claim reappearing as a defect
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "rules_coverage.py"
DATA = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"
SAMPLE = ROOT / "tests" / "fixtures" / "rules_coverage_sample.json"
ABSENT = ROOT / "tests" / "fixtures" / "there-is-no-such-dataset.json"

_spec = importlib.util.spec_from_file_location("rules_coverage", SCRIPT)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)


@pytest.fixture(scope="module")
def fixture_report():
    """A report from the small checked-in fixture: always available offline.

    Named `fixture_report` rather than `measured` because a module-level fixture
    called `measured` is shadowed by the same-named test argument, which turns a
    fixture read into a subscript of the fixture *definition*. It happened here,
    and the error (`FixtureFunctionDefinition object is not subscriptable`) reads
    like a library bug rather than a name collision.
    """
    return rc.build_report(SAMPLE)


@pytest.fixture(scope="module")
def real():
    """A report from the real dataset, or None when it is not built.

    NOT skipped when absent -- that is the point. A test that skips when the
    dataset is missing is a test that has never run on CI, and CI has no
    dataset. These assertions hold either way; only the expected numbers differ.
    """
    report = rc.build_report(DATA)
    return report if report["state"] == "measured" else None


# ─── the third state: absent data is unmeasured, never zero ───────────────────


def test_an_absent_dataset_is_unmeasured_not_zero():
    """The requirement, as one test.

    Every assertion here is a way the instrument could report "0%" and be wrong:
    a zero total, a zero mechanical count, or a zero coverage percentage. All
    three must be null, and the reason must be named.

    Mutant: `_unmeasured_report` using `0` instead of `None` for `mechanical`.
    That mutant reports "the engine implements 0 of 319 spells", which is a
    claim about the engine made with no data behind it.
    """
    report = rc.build_report(ABSENT)

    assert report["state"] == "unmeasured"
    assert "absent" in report["unmeasured_reason"]
    assert report["defects"] is None, (
        "a defect total computed with no dataset would assert something about "
        "records nobody opened")
    for category, stats in report["summary"].items():
        assert stats["mechanical"] is None, category
        assert stats["reference"] is None, category
        assert stats["coverage_percent"] is None, category
        # The nominal totals ARE reported: they are what a reader needs to know
        # what was not looked at. They are not measurements.
        assert stats["total"] > 0, category
        assert stats["unmeasured"] == stats["total"], category


def test_every_unmeasured_record_says_why():
    report = rc.build_report(ABSENT)
    assert report["records"]
    for row in report["records"]:
        assert row["state"] == "unmeasured", row
        assert row["reason"] == "dataset_absent", row
    states = {row["state"] for row in report["records"]}
    assert states == {"unmeasured"}, states


def test_the_unmeasured_report_never_prints_a_percentage(runtime=None):
    """The text report must not contain a coverage figure at all.

    A reader scanning a log should not be able to find "0.0%" and believe it.
    """
    out = rc.render(rc.build_report(ABSENT))
    assert "UNMEASURED" in out
    assert "%" not in out, out
    assert "0.0" not in out, out
    assert "build the SRD" in out or "build_srd.py" in out


def test_a_category_the_dataset_lacks_is_unmeasured_not_empty(tmp_path):
    """`--no-fvtt` writes a dataset with no `features` key.

    Reporting that axis as "0 of 0, 100% covered" would turn a missing upstream
    fetch into a success. The three states have to be reachable per category, not
    only for a wholly absent file.

    Mutant: `_summary` computing `coverage_percent` from a zero total.
    """
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data.pop("features")
    path = tmp_path / "no-fvtt.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    report = rc.build_report(path)
    assert report["state"] == "measured"
    assert "features" in report["missing_categories"]

    features = report["summary"]["feature"]
    assert features["total"] is None
    assert features["mechanical"] is None
    assert features["coverage_percent"] is None
    assert features["reason"] == "dataset_absent"
    assert not [r for r in report["records"] if r["category"] == "feature"]

    # The other axes are still measured, and stay measured.
    assert report["summary"]["spell"]["total"] == len(
        [r for r in report["records"] if r["category"] == "spell"])


def test_exhaustion_is_measurable_with_no_dataset_because_it_is_code(fixture_report):
    """Exhaustion comes from `EXHAUSTION_EFFECTS`, not from the SRD file.

    So it is the one axis that stays meaningful when the data is gone, and it is
    reported as its own axis rather than folded into conditions: "exhaustion is
    6 of 6" and "charmed is 87% covered" are different claims about different
    rules, and one number would hide exactly the asymmetry worth seeing.
    """
    stats = fixture_report["summary"]["exhaustion"]
    assert stats["total"] == 6
    assert stats["mechanical"] == 6
    assert stats["coverage_percent"] == 100.0
    assert [r["index"] for r in fixture_report["records"] if r["category"] == "exhaustion"] \
        == ["1", "2", "3", "4", "5", "6"]


# ─── per-record machine-readable rows ─────────────────────────────────────────


def test_every_record_is_one_of_three_states_with_a_valid_reason(fixture_report):
    for row in fixture_report["records"]:
        assert row["state"] in rc.STATES, row
        assert row["category"] in rc.CATEGORIES, row
        assert set(row) == {"category", "index", "name", "state", "reason", "detail"}
        if row["state"] == "mechanical":
            assert row["reason"] is None, row
        else:
            assert row["reason"] in rc.REASONS, row
            assert row["state"] in rc._REASON_STATES[row["reason"]], row


def test_every_documented_reason_is_reachable_from_the_fixture(fixture_report):
    """A reason nobody can produce is a reason nobody can audit.

    Mutant: a REASONS entry that no record in the fixture emits. That entry
    would be documentation of a state the instrument cannot actually report,
    which is worse than not having it.
    """
    seen = set()
    for row in fixture_report["records"]:
        if row["reason"]:
            seen.add(row["reason"])
        blocking = (row.get("detail") or {}).get("blocking")
        if blocking:
            seen.add(f"blocking_flag:{blocking}")
    # `dataset_absent` is the one reason the fixture CANNOT reach: it only
    # appears when there is no dataset, which is the other half of this file.
    expected = {"no_anchor", "builtin_forced_narrate", "builtin_level_narrate",
                "blocking_flag", "save_nothing_to_apply", "no_structured_action",
                "action_unparsed", "action_area_unknown",
                "action_damage_unresolved", "no_table_entry",
                "clause_not_enforced", "unsupported_listed",
                "unsupported_unlisted", "reference_only"}
    assert expected - seen == set(), (
        f"documented but unreachable from the fixture: {sorted(expected - seen)}")
    # ...and every reason the fixture does not reach is reachable somewhere.
    assert set(rc.REASONS) - seen == {"dataset_absent"}, set(rc.REASONS) - seen


def test_the_fixture_reaches_every_state(fixture_report):
    states = {row["state"] for row in fixture_report["records"]}
    assert states == {"mechanical", "reference"}, states


def test_counts_are_computed_from_the_rows_not_asserted(fixture_report):
    """`summary` must agree with `records`, or the two can drift apart.

    A summary that is hand-maintained next to the rows is the duplicate source
    of truth this repo has been bitten by. Mutant: hardcoding a percentage.
    """
    for category, stats in fixture_report["summary"].items():
        rows = [r for r in fixture_report["records"] if r["category"] == category]
        assert stats["total"] == len(rows), category
        assert stats["mechanical"] == sum(1 for r in rows
                                          if r["state"] == "mechanical"), category
        assert stats["reference"] == sum(1 for r in rows
                                         if r["state"] == "reference"), category
        if stats["total"]:
            expected = round(100.0 * stats["mechanical"] / stats["total"], 1)
            assert stats["coverage_percent"] == expected, category


def test_the_fixture_holds_the_states_it_claims_to_cover(fixture_report):
    """The fixture's own `_covers` map must not drift from what it produces.

    A fixture that claims to cover `feature unlisted` while the engine has since
    added that feature to `UNSUPPORTED_FEATURES` would leave the reason
    unreachable and the test above would catch it -- but this says which one.
    """
    fixture = json.loads(SAMPLE.read_text(encoding="utf-8"))
    by_index = {r["index"]: r for r in fixture_report["records"]}
    assert by_index["arcane-recovery"]["state"] == "mechanical"
    assert by_index["danger-sense"]["reason"] == "unsupported_unlisted"
    assert by_index["second-wind"]["reason"] == "unsupported_listed"
    assert by_index["blinded"]["state"] == "mechanical"
    assert by_index["charmed"]["reason"] == "documented_deviation"
    assert by_index["petrified"]["reason"] == "clause_not_enforced"
    assert by_index["frozen"]["reason"] == "no_table_entry"
    assert by_index["longsword"]["reason"] == "reference_only"
    assert by_index["fire-bolt"]["state"] == "mechanical"
    assert by_index["flame-strike"]["state"] == "reference"
    assert fixture["_covers"], "the fixture must say why each record is in it"
    # Every documented reason has a named record behind it, so a reader can look
    # the state up rather than take the enum's word for it.
    assert "spell no_anchor" in fixture["_covers"]
    assert "monster no_structured_action" in fixture["_covers"]


def test_a_condition_with_an_unenforced_clause_is_not_scored_mechanical(fixture_report):
    """Petrified is applied AND has an unenforced 2014 clause.

    Scoring it 100% mechanical while reporting its missing resistance two
    sections away would be two claims about one record, and only one true. The
    row is `reference` with `clause_not_enforced`, and the clause is named.
    """
    row = next(r for r in fixture_report["records"] if r["index"] == "petrified")
    assert row["state"] == "reference"
    assert row["reason"] == "clause_not_enforced"
    assert row["detail"]["unenforced_clauses"] == ["resistance to all damage"]
    # The parts that ARE applied are still reported, so the row says how much
    # works and not merely that something does not.
    assert "attack_roll" in row["detail"]["enforced"]
    assert row["detail"]["enforced"] == sorted(row["detail"]["enforced"])


# ─── the defect counter is a separate number with a target of zero ─────────────


def test_the_defect_counter_is_separate_from_coverage(fixture_report):
    """Two numbers, never merged.

    A record can be fully mechanical and still contribute a defect: petrified's
    enforced keys are applied and its resistance clause is not. Collapsing the two
    into one score would make that invisible.
    """
    defects = fixture_report["defects"]
    assert defects["target"] == 0
    assert set(defects["categories"]) == {
        "assumptions_without_consumers", "discarded_metadata",
        "ignored_clauses", "unsupported_declarations"}
    assert defects["total"] == sum(defects["categories"].values())
    for name, entries in defects["entries"].items():
        assert isinstance(entries, list), name
        assert len(entries) == defects["categories"][name], name
        for entry in entries:
            assert entry.get("why_a_defect"), entry


def test_every_defect_carries_evidence(fixture_report):
    """A defect with no citation is an opinion.

    Each one names the file and line, or quotes the SRD text, so a reviewer can
    check it instead of believing it.
    """
    entries = fixture_report["defects"]["entries"]
    for entry in entries["assumptions_without_consumers"]:
        assert ".py" in entry["where"], entry
        assert entry["records"] >= 1, entry
    for clause in entries["ignored_clauses"]:
        assert clause["evidence"].count('"') >= 2, clause
        assert clause["status"] == "open", clause
    for entry in entries["unsupported_declarations"]:
        assert entry["records"] and entry["records"] > 0, entry


def test_the_instrument_ignores_a_patched_srd_and_puts_it_back(fixture_report):
    """The bug this whole assertion exists for.

    `tactics_spells._srd` is a mutable module global and
    `tests/tactics_fixtures.py` replaces it at import time with a six-spell
    fixture, permanently (that is issue #231, unfixed on this branch). Run
    inside the full suite, the instrument therefore measured the FIXTURE and
    reported it as the 319-spell dataset: three tests failed, and worse, the
    headline coverage number was fiction.

    So the instrument installs the production function for the duration and
    restores whatever it found. Both halves are asserted: a report that reads
    the fixture is wrong, and an instrument that leaves the module patched is a
    new leak of exactly the kind it was written to avoid.
    """
    import tactics.rules as rules_mod

    system = sys.modules[type(rules_mod.load("dnd5e")).__module__]
    spells_mod = system._spells_module()
    original = spells_mod._srd

    def liar(key):                                    # a fixture-shaped stub
        return {"name": key.title(), "level": 1, "casting": "action", "range": 60,
                "origin": "point", "damage": {"type": "fire", "slot": {"1": "99d99"}},
                "flags": []}

    spells_mod._srd = liar
    try:
        patched = rc.build_report(SAMPLE)
        assert spells_mod._srd is liar, "the instrument must restore what it found"

        fire_bolt = next(r for r in patched["records"] if r["index"] == "fire-bolt")
        flame = next(r for r in patched["records"] if r["index"] == "flame-strike")
        # Fire Bolt would be a narrate-free mechanical hit under the liar too, so
        # the spell that proves it is the one the liar would get WRONG: Flame
        # Strike has no attack roll and its SRD record carries no damage the
        # engine can roll, so a stub that invents damage for everything makes it
        # mechanical. Against the real dataset it is `reference`.
        assert fire_bolt["state"] == "mechanical"     # agrees, so not decisive alone
        assert flame["state"] == "reference", (
            f"Flame Strike read as {flame['state']}: the instrument used a patched "
            "_srd rather than the production one")
    finally:
        spells_mod._srd = original

    # And with the module back as found, the report is the same as before.
    again = rc.build_report(SAMPLE)
    assert again["summary"] == fixture_report["summary"]


def test_two_confirmed_2014_clauses_are_counted_as_defects(fixture_report):
    """The positive half of "validate each alleged rules defect before implementing".

    Three of RI5's six clauses are refuted and get no defect. The other three:
    one is a documented house ruling with its own section, and TWO are real 2014
    omissions the engine resolves nowhere. Those two must be counted, or the
    defect counter is measuring nothing.

    Mutant: emptying `ignored_clauses`, which drops the total from 6 to 4 and
    looks like progress.
    """
    clauses = {c["condition"]: c for c
               in fixture_report["defects"]["entries"]["ignored_clauses"]}
    assert set(clauses) == {"petrified", "unconscious"}
    for condition, clause in clauses.items():
        assert clause["status"] == "open", clause
        assert clause["clause"], clause
        assert "SRD 5.1" in clause["evidence"], clause
        assert clause["why_a_defect"], clause
        # The same fact drives the per-record row, from the same list.
        row = next(r for r in fixture_report["records"]
                   if r["category"] == "condition" and r["index"] == condition)
        assert row["state"] == "reference"
        assert row["reason"] == "clause_not_enforced"
        assert clause["clause"] in row["detail"]["unenforced_clauses"]


def test_a_mechanical_row_carrying_a_reason_is_refused():
    """The guard, tested directly, because no real row trips it.

    Nothing in the dataset produces a mechanical row that also carries a reason,
    so asserting it only through `build_report` tests nothing: the mutant that
    deletes the guard (MUTATION 6 in the PR) leaves the suite green. Here the
    check is called with the contradiction on purpose.

    A mechanical row that names a fallback reason is a report that disagrees
    with itself, and the disagreement is invisible in a percentage.
    """
    with pytest.raises(rc.CoverageError, match="mechanical rows carry no reason"):
        rc._row("spell", "fire-bolt", "Fire Bolt", "mechanical", "no_anchor")


def test_a_non_mechanical_row_with_no_reason_is_refused():
    """The other half of the same invariant: `reference` must say why."""
    with pytest.raises(rc.CoverageError, match="not in REASONS"):
        rc._row("spell", "fire-bolt", "Fire Bolt", "reference", None)


def test_a_reason_paired_with_the_wrong_state_is_refused():
    """`unmeasured` only ever pairs with `dataset_absent`.

    This is the rule that makes "40% coverage with no data" impossible to write
    by accident, and it is a table rather than a comment so the compiler-level
    check exists at all.
    """
    with pytest.raises(rc.CoverageError, match="may not pair"):
        rc._row("spell", "fire-bolt", "Fire Bolt", "unmeasured", "no_anchor")
    # `mechanical` + any reason is caught by the earlier guard, which is the
    # better message: it names the actual contradiction rather than the pairing.
    with pytest.raises(rc.CoverageError, match="mechanical rows carry no reason"):
        rc._row("spell", "fire-bolt", "Fire Bolt", "mechanical", "dataset_absent")


def test_an_unknown_state_is_refused():
    with pytest.raises(rc.CoverageError, match="unknown state"):
        rc._row("spell", "fire-bolt", "Fire Bolt", "mostly_fine")


def test_three_of_the_six_roadmap_clauses_are_refuted(fixture_report):
    """The "validate each alleged rules defect against 2014" requirement.

    ROADMAP-ideas.md RI5 lists six condition clauses "resolved in the table and
    enforced by nothing". Three of them are not 2014 rules, and implementing them
    would have introduced rules errors. The refutations are kept as data rather
    than dropped, so nobody re-adds them from the roadmap.

    Each `why_not` quotes the SRD 5.1 text. These were read out of
    `dnd5e_srd.json`'s own `conditions[].description`, not from memory.
    """
    refuted = {c["condition"]: c for c in fixture_report["defects"]["refuted_claims"]}
    assert set(refuted) == {"poisoned", "incapacitated", "invisible"}
    for clause in refuted.values():
        assert clause["claimed"], clause
        assert clause["why_not"].count('"') >= 2, clause
        assert clause["implementing_it_would"], clause
    # The invisible one is the dangerous claim: treating invisible as blinded
    # would invert the creature's own attack rolls.
    assert "opposite" in refuted["invisible"]["implementing_it_would"]


def test_refuted_clauses_are_never_counted_as_defects(fixture_report):
    """A refuted claim must not appear in any defect category."""
    defects = fixture_report["defects"]
    open_clauses = {c["condition"] for c in defects["entries"]["ignored_clauses"]}
    assert not (open_clauses & {"poisoned", "incapacitated", "invisible"})


def test_a_documented_house_ruling_is_neither_defect_nor_compliance(fixture_report):
    """Charmed is a deliberate deviation, and that is a user's call.

    The SRD says a charmed creature cannot attack or target its charmer. The
    engine makes the attack at disadvantage instead and says why in the comment
    above `CONDITION_EFFECTS`. That is a documented house ruling, so counting it
    as a defect would be wrong (it is deliberate) and scoring it as compliance
    would also be wrong (it is not the 2014 rule). It gets its own section.
    """
    deviations = {d["condition"]: d
                  for d in fixture_report["defects"]["documented_deviations"]}
    assert "charmed" in deviations
    entry = deviations["charmed"]
    assert "needs a user decision" in entry["status"]
    assert ".py" in entry["where"] or "CONDITION_EFFECTS" in entry["where"]

    # Charmed's per-record row carries it too, as `documented_deviation`. Not
    # `clause_not_enforced`, because that would file a deliberate house ruling
    # as an oversight, and not `mechanical`, because the SRD clause is genuinely
    # not applied -- this is the one place the two could be confused and they
    # are not the same thing.
    charmed = next(r for r in fixture_report["records"]
                   if r["category"] == "condition" and r["index"] == "charmed")
    assert charmed["state"] == "reference"
    assert charmed["reason"] == "documented_deviation"
    assert "user" in charmed["detail"]["deviation"]
    assert "attack_roll" in charmed["detail"]["enforced"], (
        "the parts that ARE applied still get reported, so the row says how much "
        "works rather than only that something does not")

    # And it is not smuggled into a defect bucket. Checked by the `condition`
    # field rather than by substring: the `harmful_to` assumption entry names
    # charmed in its prose, and a substring test would call that a smuggling.
    entries = fixture_report["defects"]["entries"]
    assert all(c.get("condition") != "charmed" for group in entries.values()
               for c in group if isinstance(c, dict))


def test_the_defects_are_absent_when_the_data_is():
    """No dataset, no defect total.

    An absent file plus a confident "0 defects" would be the same failure as
    "0% coverage": a number about records nobody opened.
    """
    report = rc.build_report(ABSENT)
    assert report["defects"] is None
    assert "unmeasured" in report["defects_note"] or "not counted" in report["defects_note"]


# ─── measured numbers, against the real dataset ───────────────────────────────


def test_the_headline_number_does_not_reproduce():
    """RI5 says 58 of 319 spells. The instrument says 54.

    Pinned because the roadmap's number is the thing this issue exists to
    replace, and a number that quietly drifts is how the original became
    unfalsifiable in the first place. If a future change really does make more
    spells mechanical, this test is where that should be argued about.
    """
    if not DATA.exists():
        pytest.skip("real dataset absent; the fixture report still runs")
    report = rc.build_report(DATA)
    stats = report["summary"]["spell"]
    assert stats["total"] == 319
    assert stats["mechanical"] == 54, (
        f"spell coverage is now {stats['mechanical']}, was 54. If that is "
        "intended, update this test and ROADMAP-ideas.md together.")
    assert stats["coverage_percent"] == 16.9


def test_spell_coverage_depends_on_the_caster_and_the_report_says_so():
    """Eldritch Blast is mechanical at level 1 and reference at 5.

    `BUILTIN` sets `narrate_from_level: 5` because the SRD record holds one
    number and the spell deals more beams above 4th. So "the spell coverage
    figure" is a function of a measurement condition, and the condition has to
    travel with the number.
    """
    if not DATA.exists():
        pytest.skip("real dataset absent; the fixture report still runs")
    low = rc.build_report(DATA, {"caster_level": 1})
    mid = rc.build_report(DATA, {"caster_level": 5})
    mid_states = {r["index"]: r["state"] for r in mid["records"]
                  if r["category"] == "spell"}
    assert mid["summary"]["spell"]["mechanical"] == 54
    assert low["summary"]["spell"]["mechanical"] == 55

    blob = json.dumps(mid)
    assert '"caster_level": 5' in blob, (
        "the measurement conditions must be in the report; a coverage number "
        "without them is a number with a hidden assumption")
    by_index = {r["index"]: r for r in low["records"] if r["category"] == "spell"}
    assert by_index["eldritch-blast"]["state"] == "mechanical"
    assert by_index["eldritch-blast"]["state"] != mid_states["eldritch-blast"]


def test_the_twelve_staples_all_narrate():
    """RI5's characterisation of the gap, checked against the engine.

    All twelve of the spells it names demote to `narrate`. This is the finding
    that motivates the coverage work, so it is asserted rather than quoted.
    """
    if not DATA.exists():
        pytest.skip("real dataset absent")
    report = rc.build_report(DATA)
    by_index = {r["index"]: r for r in report["records"] if r["category"] == "spell"}
    for index in ("haste", "fly", "invisibility", "greater-invisibility",
                  "darkness", "fog-cloud", "wall-of-force", "expeditious-retreat",
                  "counterspell", "dispel-magic", "teleport", "dimension-door"):
        row = by_index.get(index)
        assert row is not None, index
        assert row["state"] == "reference", (index, row)
        assert row["reason"], row


def test_every_axis_is_present_with_a_computed_total(real, fixture_report):
    report = real or fixture_report
    assert set(report["summary"]) == set(rc.CATEGORIES)
    for category, stats in report["summary"].items():
        if stats["total"] is None:
            continue
        assert stats["total"] >= 0, category
        assert stats["mechanical"] <= stats["total"], category
        assert stats["reference"] + stats["mechanical"] + (stats["unmeasured"] or 0) \
            == stats["total"], category


def test_the_real_dataset_carries_provenance(real):
    """Provenance is not optional: a coverage number without it is anonymous.

    This used to be where the missing piece showed. `dnd5e_srd.json` recorded a
    sha for 5e-bits and NOT for foundryvtt, because `build_srd.cmd_build` called
    `_latest_sha(FVTT_COMMITS)` and then discarded the value that `_build_fvtt`
    returned. That was a real gap in 260 features' provenance.

    The gap is closed. `build_srd.cmd_build` now records the foundryvtt tree sha
    it actually read, and its own comment says why it records `""` rather than
    guessing when the tree cannot be read: "a wrong sha would be worse than a
    missing one". So the assertion is no longer "there is no sha" -- that would
    be a test for a gap the code deliberately closed -- and it is deliberately
    NOT an equality check against one pinned sha either, because the value moves
    every time upstream `foundryvtt/dnd5e` advances, and pinning it would make
    this test red for a reason that has nothing to do with the engine.

    What must hold is the shape: a sha is either a full 40-character lowercase
    git object id, or empty to say "the tree could not be read". Anything else --
    a truncated id, a branch name, a placeholder -- is provenance that looks
    specific and is not, which is the failure this whole test exists to prevent.
    """
    if not real:
        pytest.skip("real dataset absent")
    provenance = real["provenance"]
    assert provenance["built_at"]
    assert provenance["edition"] == "2014"
    assert provenance["sources"]
    fvtt = provenance["sources"].get("foundryvtt") or {}
    assert fvtt.get("repo") == "foundryvtt/dnd5e"
    assert fvtt.get("branch") == "master"
    assert fvtt.get("fetched_at")
    sha = fvtt.get("sha") or ""
    assert sha == "" or re.fullmatch(r"[0-9a-f]{40}", sha), (
        f"the foundryvtt provenance names a sha that is not a git object id: {sha!r}. "
        f"build_srd records '' when the tree cannot be read, so this is neither a "
        f"real commit nor the honest empty -- which is the one thing provenance "
        f"must never be.")


# ─── what the file says may be done with it ───────────────────────────────────
#
# `_meta.sources` answers "which commit did this come from". It does not answer
# "may I ship this", and that is the question a consumer of a REDISTRIBUTED
# dataset has: the data travels, the repository does not. So the license and
# attribution pointers have to be inside the data (dnd-gm#139).
#
# They live beside the provenance test above rather than in test_srd_contracts.py
# because they are the same class of claim about the same `_meta` block, and
# because the builder is reached here the way it is reached there: loaded from
# its file, with the network replaced.


def _load_build_srd():
    script = ROOT / "systems" / "dnd5e" / "build_srd.py"
    spec = importlib.util.spec_from_file_location("build_srd", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_srd = _load_build_srd()


def _assert_the_license_pointer_is_there(meta):
    """Every field systems/dnd5e/NOTICE documents, so the two cannot drift.

    Asserting the string "CC-BY-4.0" appears somewhere would be satisfied by a
    key nobody reads. Each field is named instead, so renaming one is a failure
    rather than a silent loss of the pointer.
    """
    block = meta["license"]
    assert block["identifier"] == "CC-BY-4.0"
    assert block["url"].startswith("https://creativecommons.org/licenses/by/4.0/")
    assert "game text" in block["covers"], block
    assert block["notice"] == "systems/dnd5e/NOTICE"

    credit = meta["attribution"]
    assert "System Reference Document 5.1" in credit["system_reference_document"]
    assert "Wizards of the Coast" in credit["system_reference_document"]
    assert "build_srd.py" in credit["modifications"]
    assert credit["notice"] == "systems/dnd5e/NOTICE"

    # Both upstreams LICENSE their own repositories as MIT, which covers their
    # packaging and not the SRD text inside. Recorded separately because a
    # reader shown only one of the two is misled by whichever they saw, and the
    # MIT one is the misleading one.
    for name in ("5e-bits", "foundryvtt"):
        assert meta["sources"][name]["license"] == "MIT", name
        assert meta["sources"][name]["license_url"].startswith("https://"), name
    # The 5e-bits half named its path and the foundryvtt half did not, which
    # made the same question answerable two ways depending on the source.
    assert meta["sources"]["5e-bits"]["path"] == "packages/5e-database"
    assert meta["sources"]["foundryvtt"]["path"] == "packs/_source"


def test_the_real_dataset_says_what_may_be_done_with_it(real):
    """A built dataset carries the pointer; a fixture cannot, and does not have to.

    The checked-in fixture is a hand-written sample, not something build_srd.py
    wrote, so it has no `_meta.license` and never will. That is not the gap
    #139 is about: the gap is a GENERATED file arriving with nothing in it.
    """
    if not real:
        pytest.skip("real dataset absent")
    with open(DATA, encoding="utf-8") as fh:
        meta = json.load(fh)["_meta"]
    _assert_the_license_pointer_is_there(meta)


@pytest.mark.parametrize("skip_fvtt", [False, True])
def test_the_builder_writes_the_license_pointer_into_both_shapes(
        tmp_path, monkeypatch, skip_fvtt):
    """A full build and a --no-fvtt build, neither touching the network.

    --no-fvtt is a separate shape and not a smaller copy of one: it is what you
    get with no PyYAML, and its `features` list is empty. A pointer that only
    reached the full build would leave every such dataset unattributed, which is
    the whole gap #139 describes.
    """
    monkeypatch.setattr(build_srd, "OUT_FILE", str(tmp_path / "out.json"))
    monkeypatch.setattr(build_srd, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(build_srd, "_latest_sha", lambda url: "0" * 40)
    monkeypatch.setattr(build_srd, "_build_5ebits", lambda: {
        "spells": [{"index": "fire-bolt", "name": "Fire Bolt", "level": 0,
                    "mechanics": {"casting": "action", "flags": []}}],
        "equipment": [{"index": "club", "name": "Club"}],
        "magic_items": [{"index": "pewter", "name": "Pewter"}],
        "conditions": [{"index": "blinded", "name": "Blinded"}],
        "monsters": [{"index": "goblin", "name": "Goblin", "hp": 7, "ac": 15,
                      "actions": []}],
    })
    monkeypatch.setattr(build_srd, "_build_fvtt",
                        lambda: ([{"index": "haste", "name": "Haste"}], "f" * 40))

    build_srd.cmd_build(skip_fvtt=skip_fvtt)

    with open(tmp_path / "out.json", encoding="utf-8") as fh:
        meta = json.load(fh)["_meta"]
    _assert_the_license_pointer_is_there(meta)
    # Proves the two parameterisations really are the two shapes, so this is not
    # one build asserted twice.
    assert bool(meta["record_counts"]["features"]) is not skip_fvtt


# ─── the CLI ──────────────────────────────────────────────────────────────────


def _run(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=False)
    return proc.returncode, proc.stdout, proc.stderr


def test_the_json_output_is_machine_readable():
    code, out, err = _run("--data", str(SAMPLE), "--json")
    assert code == 0, err
    report = json.loads(out)
    assert report["schema"] == rc.SCHEMA_VERSION
    assert report["state"] == "measured"
    assert isinstance(report["records"], list) and report["records"]


def test_category_filters_the_json_without_breaking_the_summary():
    _, out, err = _run("--data", str(SAMPLE), "--json", "--category", "spell")
    assert err == ""
    report = json.loads(out)
    assert {r["category"] for r in report["records"]} == {"spell"}
    # The summary is left whole on purpose: a filtered read that silently dropped
    # the other axes would make a 2-record file look like a 319-record dataset.
    assert set(report["summary"]) == set(rc.CATEGORIES)


def test_the_absent_dataset_exits_cleanly_and_says_so():
    """Exit 0, not a traceback.

    The state is a reportable outcome, and a CI job that runs this on a fresh
    clone should get a readable "unmeasured" rather than a stack trace -- while
    still not getting a coverage number.
    """
    code, out, err = _run("--data", str(ABSENT))
    assert code == 0, err
    assert "UNMEASURED" in out
    assert "%" not in out, f"a coverage figure leaked into the unmeasured path: {out!r}"


def test_an_unreadable_dataset_is_unmeasured_rather_than_a_traceback(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    code, out, err = _run("--data", str(broken))
    assert code == 0, err
    assert "UNMEASURED" in out
    report = rc.build_report(broken)
    assert report["state"] == "unmeasured"
    assert "unreadable" in report["unmeasured_reason"]


# ─── the invariants that must hold for any report at all ──────────────────────


def test_no_measured_report_ever_contains_an_unmeasured_row_by_accident(fixture_report):
    """A measured report with unmeasured rows is a partial dataset, and it must
    say which category, not blur them together.

    The fixture is complete, so this is about the shape of the code rather than
    one file: `missing_categories` is the only way an axis becomes unmeasured
    inside a measured report, and it is empty here.
    """
    assert fixture_report["state"] == "measured"
    assert fixture_report["missing_categories"] == []
    assert {r["state"] for r in fixture_report["records"]} == {"mechanical", "reference"}


def test_a_reported_number_is_never_derived_from_a_default(fixture_report):
    """Every number in a measured report traces to a row.

    Spot-checked on the fixture's monsters axis, where the number is a count of
    token builds rather than a table lookup, because that is the axis where a
    hardcoded figure would survive longest.
    """
    monsters = [r for r in fixture_report["records"] if r["category"] == "monster"]
    assert monsters, "the fixture must carry monsters for this axis to be checked"
    assert len(monsters) == fixture_report["summary"]["monster"]["total"]
    for row in monsters:
        # A creature can have NO actions at all (a Sea Horse, a hazard, a corpse),
        # which is `no_structured_action` and not a malformed row -- so this
        # asserts the invariant `mechanical <= total`, not `total >= 1`.
        assert row["detail"]["mechanical_actions"] <= row["detail"]["actions"], row


def test_a_monster_reports_its_dominant_reason_not_the_first_one(fixture_report):
    """The reason is the most common one, with a fixed priority for ties.

    The Giant Spider is the case that needed it: its Bite parses and its Web is
    an attack whose damage could not be read, so both reasons are in play and
    the reported one must be stable across runs. An earlier draft of this
    instrument reported whichever came first in the action list, which is a
    number that changes if upstream reorders `actions`.
    """
    spider = next(r for r in fixture_report["records"] if r["index"] == "giant-spider")
    assert spider["state"] == "reference"
    assert spider["reason"] in ("action_damage_unresolved", "action_unparsed"), spider
    assert set(spider["detail"]["partial_reasons"]) <= {
        "action_damage_unresolved", "action_area_unknown", "action_unparsed"}
    # Deterministic: building it twice gives the same answer.
    again = next(r for r in rc.build_report(SAMPLE)["records"]
                 if r["index"] == "giant-spider")
    assert again["reason"] == spider["reason"]


def test_a_partial_monster_reports_what_does_work(fixture_report):
    """`partial_reasons` and `mechanical_actions` are what stop a 65% monster
    number from being unreadable. Without them the report says "116 of 334
    monsters are reference" and nothing about the 218 that are not."""
    measured_monsters = [r for r in fixture_report["records"]
                         if r["category"] == "monster"]
    for row in measured_monsters:
        assert "mechanical_actions" in row["detail"], row
        if row["state"] == "reference":
            assert "partial_reasons" in row["detail"], row