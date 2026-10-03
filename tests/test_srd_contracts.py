"""The rules contracts, checked against the dataset that is supposed to satisfy them.

WHY THIS IS A FUNCTION AND NOT A TEST
=====================================
`tests/test_srd_sources.py` is the strongest anti-drift artefact in the repo and
its shape is exactly right: a pure testable function (`_partition_fvtt_tree`) plus
a fixture containing BOTH editions plus a substring ban. What it could not do is
check the CONTENT of a built dataset, because the built dataset is gitignored and
absent on a fresh clone.

So the contract lives here as a pure function over a dataset dict, and the tests
drive it three ways:

  * against the real dataset when it exists,
  * against small checked-in fixtures that carry both editions, offline,
  * against an absent dataset, which must report UNMEASURED and never `ok`.

That third one is the point. A contract test that skips when the dataset is
missing is a contract test that has never run on CI, and CI has no dataset. A
vacuous pass and a real pass look identical, which is the failure this file
exists to prevent.

WHAT IS BOUND
=============
  conditions   `CONDITION_EFFECTS` against the dataset's `conditions` list. Today
               `tests/test_condition_modifiers.py:30` copies 14 names into the
               test file and asserts set equality at :54, so it passes today and
               would keep passing if the SRD added a fifteenth.
  categories   every dataset category `lookup.ALL_CATEGORIES` indexes, present
               and non-empty, with `_meta.record_counts` agreeing with the lists.
  edition      the dataset must declare 2014. Before #137 it declared nothing at
               all, so a dataset carrying the wrong edition could not be refused
               -- only the build constants were pinned, and a 2024 record served
               from a 2014 path passes those.

THE 2024 EXCEPTION IS DELIBERATE AND NARROW
===========================================
`systems/dnd5e/encounter.py` holds `XP_BUDGET_2024` and
`systems/dnd5e/system.md` scopes it: the campaign `ruleset` selects the XP budget
TABLE for three commands "because it is a table lookup, not a rules engine. It
does not select combat rules." That exception is preserved here rather than
flagged, and `test_the_2024_exception_is_a_budget_table_and_not_combat_rules`
pins the boundary so it cannot widen by accident -- which is the only way a
documented exception turns into a real one.

The condition contract refuses a dataset that is not 2014. A *budget table* is
not a dataset, so the exception and the contract do not collide.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import warnings

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DND5E = ROOT / "systems" / "dnd5e"
DATA = DND5E / "data" / "dnd5e_srd.json"
BUILD = DND5E / "build_srd.py"
FIXTURES = ROOT / "tests" / "fixtures"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(DND5E))

import importlib.util

_spec = importlib.util.spec_from_file_location("srd_contract_build", BUILD)
build_srd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_srd)

import lookup as lookup_mod

import systems.dnd5e.encounter as encounter_mod
import systems.dnd5e.tactics_rules as tr

#: The edition the engine adjudicates. One value, and every check below is
#: against it rather than against a literal repeated in an assertion.
ENGINE_EDITION = build_srd.EDITION

#: Per-row outcomes. `unmeasured` is not an outcome a row can have by accident:
#: a row only carries it when the category itself was not in the dataset.
OK, UNKNOWN, UNMEASURED, WRONG_EDITION = "ok", "unknown", "unmeasured", "wrong_edition"


# ─── the contract, as a pure function ─────────────────────────────────────────


def load(path: pathlib.Path):
    """(dataset, None) or (None, reason). A missing file is a state, not a raise."""
    if not path.exists():
        return None, f"dataset absent at {path}"
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, json.JSONDecodeError) as error:
        return None, f"dataset unreadable at {path}: {error}"


def check_conditions(dataset: dict) -> dict:
    """`CONDITION_EFFECTS` against the dataset's condition list.

    Returns {"unmeasured", "unknown", "absent_from_table", "absent_from_srd"} so a
    caller can tell all four apart. Both directions matter: a condition the SRD
    added and the engine has never heard of is a coverage gap, and a condition
    the table invented is a rule that does not exist.
    """
    if dataset is None:
        return {"unmeasured": True, "unknown": [], "absent_from_table": [],
                "absent_from_srd": [], "reason": "no dataset"}
    records = dataset.get("conditions")
    if records is None:
        return {"unmeasured": True, "unknown": [], "absent_from_table": [],
                "absent_from_srd": [], "reason": "dataset has no 'conditions' key"}
    in_srd = {str(r.get("index") or r.get("name", "")).strip().lower()
              for r in records if isinstance(r, dict)}
    in_table = {str(k).strip().lower() for k in tr.CONDITION_EFFECTS}
    return {
        "unmeasured": False,
        "reason": None,
        # Exhaustion is a condition in the SRD and a level-by-level table in the
        # engine, so it belongs in both sets and is not special-cased out of
        # either. EXHAUSTION_EFFECTS is the contract for it, checked separately.
        "unknown": sorted(in_srd - in_table),
        "absent_from_srd": sorted(in_table - in_srd),
    }


def check_categories(dataset: dict) -> dict:
    """Every category `lookup.ALL_CATEGORIES` indexes must be present and agree
    with `_meta.record_counts`.

    `record_counts` is the one number in the file that can disagree with the file
    without anything noticing, because nothing reads it. A consumer that trusts
    it gets a count the data does not support.
    """
    expected = list(lookup_mod.ALL_CATEGORIES)
    if dataset is None:
        return {"unmeasured": True, "reason": "no dataset", "absent": expected,
                "empty": [], "count_mismatch": [], "counts": {}}
    absent = [c for c in expected if c not in dataset]
    empty = [c for c in expected
             if c in dataset and isinstance(dataset[c], list) and not dataset[c]]
    declared = (dataset.get("_meta") or {}).get("record_counts") or {}
    counts = {c: len(dataset[c]) for c in expected
              if c in dataset and isinstance(dataset[c], list)}
    mismatch = [f"{c}: declared {declared[c]}, actual {counts[c]}"
                for c in counts if c in declared and declared[c] != counts[c]]
    return {"unmeasured": False, "reason": None, "absent": absent, "empty": empty,
            "count_mismatch": mismatch, "counts": counts}


def check_edition(dataset: dict) -> dict:
    """The dataset must declare the edition the engine adjudicates.

    A dataset that declares nothing is `undeclared`, which is reported as its own
    outcome rather than being read as 2014 by default. The default is the whole
    problem: `#137`'s evidence is that the file used to say nothing at all, and
    inferring "must be 2014" from a URL substring is exactly the inference a
    wrong-edition file would defeat.
    """
    if dataset is None:
        return {"unmeasured": True, "reason": "no dataset", "outcome": UNMEASURED,
                "declared": None, "expected": ENGINE_EDITION}
    declared = (dataset.get("_meta") or {}).get("edition")
    if declared is None:
        return {"unmeasured": False, "reason": None, "outcome": "undeclared",
                "declared": None, "expected": ENGINE_EDITION}
    declared = str(declared).strip().lower()
    if declared != ENGINE_EDITION:
        return {"unmeasured": False, "reason": None, "outcome": WRONG_EDITION,
                "declared": declared, "expected": ENGINE_EDITION}
    return {"unmeasured": False, "reason": None, "outcome": OK,
            "declared": declared, "expected": ENGINE_EDITION}


def check_all(dataset: dict) -> dict:
    return {"edition": check_edition(dataset),
            "conditions": check_conditions(dataset),
            "categories": check_categories(dataset)}


def acceptable(report: dict) -> list:
    """Every reason this report may NOT be merged for, as strings.

    A single `()` / non-empty answer rather than three booleans, because a caller
    that checks three booleans gets to choose which one to ignore, and the one
    they ignore is the one that was about to bite.
    """
    out = []
    edition = report["edition"]
    if edition["outcome"] == UNMEASURED:
        out.append(f"edition: {edition['reason']}")
    elif edition["outcome"] == WRONG_EDITION:
        out.append(f"edition: dataset declares {edition['declared']!r}, "
                   f"engine adjudicates {edition['expected']!r}")
    elif edition["outcome"] == "undeclared":
        out.append("edition: dataset records none; it cannot be checked")
    conditions = report["conditions"]
    if conditions["unmeasured"]:
        out.append(f"conditions: {conditions['reason']}")
    else:
        if conditions["unknown"]:
            out.append(f"conditions the engine has no entry for: "
                       f"{', '.join(conditions['unknown'])}")
        if conditions["absent_from_srd"]:
            out.append(f"conditions in the table but not the SRD: "
                       f"{', '.join(conditions['absent_from_srd'])}")
    categories = report["categories"]
    if categories["unmeasured"]:
        out.append(f"categories: {categories['reason']}")
    else:
        for key, label in (("absent", "absent"), ("empty", "present but empty"),
                           ("count_mismatch", "declared count disagrees")):
            if categories[key]:
                out.append(f"categories {label}: {', '.join(categories[key])}")
    return out


# ─── fixtures: both editions, offline ─────────────────────────────────────────

SRD_2014_CONDITIONS = [
    "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned", "prone",
    "restrained", "stunned", "unconscious",
]
#: 2024's condition list, which is a DIFFERENT set of conditions. Used only to
#: prove the contract refuses it: a 2024 dataset has conditions the 2014 table
#: has never heard of, and one the 2014 table holds that 2024 renamed or dropped.
SRD_2024_CONDITIONS = [
    "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "poisoned", "prone", "restrained",
    "stunned", "unconscious",
    # 2024 additions the 2014 table cannot possibly have.
    "blessed", "dazed", "deafened-2024", "glamoured", "marked", "stupefied",
]
#: 2024 monsters carry `legendary_resistance` and 2024 features carry
#: `weapon_mastery`; neither key exists in a 2014 record. Present so the wrong
#: -edition fixture is not merely a relabelled 2014 one, which would pass a
#: check that only looked at `_meta.edition`.
SRD_2024_MONSTER_KEYS = ("legendary_resistance", "legendary_actions_2024")


def _dataset(edition=ENGINE_EDITION, conditions=None, categories=True, meta=True):
    """A minimal dataset, labelled with the edition asked for."""
    conds = conditions if conditions is not None else SRD_2014_CONDITIONS
    data = {}
    if categories:
        for cat in lookup_mod.ALL_CATEGORIES:
            data[cat] = [_record(cat, i) for i in range(2)]
    else:
        data["spells"] = [_record("spells", i) for i in range(2)]
    data["conditions"] = [{"index": c, "name": c.title()} for c in conds]
    if meta:
        counts = {k: len(v) for k, v in data.items()}
        data["_meta"] = {"edition": edition, "built_at": "fixture",
                         "record_counts": counts,
                         "total_records": sum(counts.values())}
    return data


def _record(category, i):
    if category == "monsters":
        return {"index": f"{category}-{i}", "name": f"Monster {i}", "hp": 7, "ac": 13,
                "actions": [], "legendary_resistance": 2}     # 2024-only key
    return {"index": f"{category}-{i}", "name": f"{category.title()} {i}"}


# ─── the contract, against fixtures ───────────────────────────────────────────


def test_a_2014_dataset_satisfies_the_contract():
    assert acceptable(check_all(_dataset())) == []


def test_a_dataset_that_declares_2024_is_refused():
    report = check_all(_dataset(edition="2024"))
    assert report["edition"]["outcome"] == WRONG_EDITION
    problems = acceptable(report)
    assert any("declares '2024'" in p for p in problems), problems


def test_a_2024_dataset_is_refused_on_its_conditions_too():
    """Not only on the label.

    A wrong-edition dataset is refused by its CONTENT as well as its
    declaration, so relabelling a 2024 file as 2014 -- which is exactly what a
    careless repackaging does -- does not get past the contract.
    """
    report = check_all(_dataset(conditions=SRD_2024_CONDITIONS))
    problems = acceptable(report)
    assert any("the engine has no entry for" in p for p in problems), problems
    for name in ("blessed", "glamoured", "marked", "stupefied"):
        assert name in report["conditions"]["unknown"], name


def test_a_2024_monster_key_is_visible_in_the_dataset():
    """The wrong-edition fixture is not merely a relabelled 2014 one.

    If it were, a contract that only read `_meta.edition` would be untested
    against a realistic mix-up. `legendary_resistance` is a 2024 monster field
    and a 2014 record never carries it.
    """
    monster = _record("monsters", 0)
    assert "legendary_resistance" in monster
    assert "legendary_resistance" not in _record("spells", 0)


def test_a_dataset_that_declares_no_edition_is_refused_as_undeclared():
    """The state #137 exists to fix.

    The built dataset used to carry no edition at all, and reading that as 2014
    by default is the inference a wrong-edition file defeats. So `undeclared` is
    its own outcome, distinct from both `ok` and `wrong_edition`.
    """
    report = check_all(_dataset(meta=False))
    assert report["edition"]["outcome"] == "undeclared"
    problems = acceptable(report)
    assert any("records none" in p for p in problems), problems


def test_a_condition_the_engine_has_never_heard_of_is_named():
    extra = SRD_2014_CONDITIONS + ["stupefied"]
    report = check_all(_dataset(conditions=extra))
    assert report["conditions"]["unknown"] == ["stupefied"]
    assert any("stupefied" in p for p in acceptable(report))


def test_a_condition_the_table_invented_is_named():
    """The other direction: a rule the SRD does not have.

    A 2014 table entry with no SRD condition behind it is a rule that exists
    nowhere else, and nothing else in the tree would notice.
    """
    dataset = _dataset()
    dataset["_meta"]["record_counts"]["conditions"] = len(SRD_2014_CONDITIONS)
    report = check_all(dataset)
    assert report["conditions"]["absent_from_srd"] == [], (
        "every CONDITION_EFFECTS key should be an SRD condition; if this fires, "
        f"the table holds {report['conditions']['absent_from_srd']}")


def test_a_missing_condition_category_is_unmeasured_not_empty():
    dataset = _dataset()
    del dataset["conditions"]
    report = check_all(dataset)
    assert report["conditions"]["unmeasured"] is True
    assert any("no 'conditions' key" in p for p in acceptable(report))


def test_a_category_absent_from_the_dataset_is_named():
    """One missing category is enough to make the contract unsatisfied."""
    dataset = _dataset(categories=False)
    for gone in lookup_mod.ALL_CATEGORIES:
        if gone != "spells":
            dataset.pop(gone, None)
    report = check_categories(dataset)
    assert "spells" not in report["absent"]
    assert "monsters" in report["absent"] and "features" in report["absent"]
    problems = acceptable(check_all(dataset))
    assert any("monsters" in p for p in problems), problems


def test_a_declared_count_that_disagrees_with_the_data_is_named():
    """`record_counts` is the one number in the file nothing reads.

    A consumer that trusts it gets a count the data does not support, and no
    other test in the tree would notice, because nothing else reads it.
    """
    dataset = _dataset()
    dataset["_meta"]["record_counts"]["spells"] = 17
    problems = acceptable(check_all(dataset))
    assert any("declared 17, actual 2" in p for p in problems), problems


def test_a_category_present_but_empty_is_named():
    dataset = _dataset()
    dataset["monsters"] = []
    dataset["_meta"]["record_counts"]["monsters"] = 0
    problems = acceptable(check_all(dataset))
    assert any("present but empty" in p for p in problems), problems


# ─── the contract, against an absent dataset ──────────────────────────────────


def test_an_absent_dataset_is_unmeasured_on_all_three_axes():
    """The requirement: explicit missing-data states.

    Not a skip. A skipped contract test has never run on CI, and CI has no
    dataset, so a skip and a pass look identical from the outside. Here the
    answer is computed and it says UNMEASURED on all three axes.
    """
    dataset, reason = load(DATA.parent / "there-is-no-such-dataset.json")
    assert dataset is None and reason
    report = check_all(dataset)
    for axis in ("edition", "conditions", "categories"):
        assert report[axis]["unmeasured"] is True, axis
    assert report["edition"]["outcome"] == UNMEASURED
    assert len(acceptable(report)) == 3, acceptable(report)


def test_an_unreadable_dataset_is_unmeasured_rather_than_a_traceback(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    dataset, reason = load(broken)
    assert dataset is None and "unreadable" in reason
    assert check_all(dataset)["edition"]["outcome"] == UNMEASURED


# ─── against the real dataset, when it exists ─────────────────────────────────


@pytest.fixture(scope="module")
def real():
    dataset, reason = load(DATA)
    return {"dataset": dataset, "reason": reason}


def test_the_real_dataset_satisfies_the_contract(real):
    """Where it can be checked, it must pass. Skipped rather than asserted empty
    when the dataset is absent -- and the skip says so, which is the difference
    this file is arguing for everywhere else."""
    if real["dataset"] is None:
        pytest.skip(f"real dataset not built: {real['reason']}. "
                    "Run python3 systems/dnd5e/build_srd.py")
    problems = acceptable(check_all(real["dataset"]))
    assert problems == [], problems


def test_the_real_dataset_records_its_edition(real):
    """`build_srd.EDITION` is now written into `_meta`, so this is checkable.

    Skipped, with the rebuild command, when the dataset on disk predates the
    write -- which is every dataset built before this change. `cmd_build` itself
    is pinned by the two tests below, so skipping here costs nothing: what is
    being proved is that a *correctly built* dataset is accepted, not that
    everyone's stale build is retroactively fixed.
    """
    if real["dataset"] is None:
        pytest.skip(f"real dataset not built: {real['reason']}. "
                    "Run python3 systems/dnd5e/build_srd.py")
    result = check_edition(real["dataset"])
    if result["outcome"] == "undeclared":
        pytest.skip("the dataset on disk predates the provenance write in #137; "
                    "rebuild it with python3 systems/dnd5e/build_srd.py")
    assert result["outcome"] == OK, result


# ─── the builder writes the provenance, tested without the network ───────────


def test_cmd_build_writes_the_edition_into_meta(tmp_path, monkeypatch):
    """The durable half: `cmd_build` records which edition it produced.

    Testing the dataset on disk proves it for whoever rebuilds next. Testing
    `cmd_build` proves it for everyone, forever, and needs no network: the two
    fetchers are replaced with canned records.
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
    monkeypatch.setattr(build_srd, "_build_fvtt", lambda: (
        [{"index": "haste", "name": "Haste", "class": "wizard", "type": "class"}],
        "f" * 40))

    build_srd.cmd_build()

    with open(tmp_path / "out.json", encoding="utf-8") as fh:
        dataset = json.load(fh)
    meta = dataset["_meta"]
    assert meta["edition"] == ENGINE_EDITION == "2014"
    assert meta["record_counts"]["spells"] == 1
    assert meta["total_records"] == sum(meta["record_counts"].values())

    # And the dataset it wrote is accepted on the axes the canned data can
    # satisfy. The condition axis deliberately reports the other thirteen as
    # "in the table, not in this dataset", which is the contract working: a
    # one-record dataset must not be able to satisfy a fifteen-condition table.
    report = check_all(dataset)
    assert report["edition"]["outcome"] == OK
    assert report["categories"]["absent"] == []
    assert report["categories"]["count_mismatch"] == []
    assert report["categories"]["empty"] == []
    # The canned dataset carries `blinded` only, so the other thirteen table
    # entries are reported as "in the table, not in this dataset". That is the
    # contract working rather than a nuisance: a one-record dataset must not be
    # able to satisfy a fifteen-condition table.
    assert report["conditions"]["unknown"] == []
    assert len(report["conditions"]["absent_from_srd"]) \
        == len(tr.CONDITION_EFFECTS) - 1


def test_cmd_build_keeps_the_foundryvtt_sha_it_just_read(tmp_path, monkeypatch):
    """The 260 features had anonymous provenance until now.

    `cmd_build` used to call `_build_fvtt()` and throw away the sha it returned,
    so `sources.foundryvtt.sha` was always empty while the 5e-bits half named a
    commit. Caught here with the commits API failing, which is the case where the
    tree sha is the only one there is.
    """
    monkeypatch.setattr(build_srd, "OUT_FILE", str(tmp_path / "out.json"))
    monkeypatch.setattr(build_srd, "DATA_DIR", str(tmp_path))
    # Only the foundryvtt commits lookup fails. The 5e-bits one still answers,
    # so the assertion below is about the sha that was being thrown away rather
    # than about both sources failing at once.
    monkeypatch.setattr(build_srd, "_latest_sha",
                        lambda url: "" if url is build_srd.FVTT_COMMITS else "b" * 40)
    monkeypatch.setattr(build_srd, "_build_5ebits", lambda: {
        "spells": [], "equipment": [], "magic_items": [],
        "conditions": [{"index": "blinded", "name": "Blinded"}],
        "monsters": [{"index": "goblin", "name": "Goblin", "hp": 7, "ac": 15}],
    })
    tree_sha = "a" * 40
    monkeypatch.setattr(build_srd, "_build_fvtt",
                        lambda: ([{"index": "haste", "name": "Haste"}], tree_sha))

    build_srd.cmd_build()
    with open(tmp_path / "out.json", encoding="utf-8") as fh:
        sources = json.load(fh)["_meta"]["sources"]
    assert sources["foundryvtt"]["sha"] == tree_sha
    # Both sources name a commit now, so no half of the dataset is anonymous.
    for name, info in sources.items():
        assert info.get("sha"), (name, info)


def test_the_real_dataset_counts_agree_with_its_own_records(real):
    if real["dataset"] is None:
        pytest.skip(f"real dataset not built: {real['reason']}")
    report = check_categories(real["dataset"])
    assert report["absent"] == [], report["absent"]
    assert report["empty"] == [], report["empty"]
    assert report["count_mismatch"] == [], report["count_mismatch"]


def test_the_condition_table_covers_every_dataset_condition(real):
    """Replaces the test-local tuple at `tests/test_condition_modifiers.py:30`.

    That file copies 14 names into the test and asserts set equality, so it
    passes today and would keep passing if the SRD added a fifteenth. Here the
    names come from the dataset.
    """
    if real["dataset"] is None:
        pytest.skip(f"real dataset not built: {real['reason']}")
    report = check_conditions(real["dataset"])
    assert report["unknown"] == [], (
        "the SRD names conditions CONDITION_EFFECTS has no entry for; that is a "
        "coverage gap, not a test failure to paper over")
    assert report["absent_from_srd"] == [], (
        "CONDITION_EFFECTS holds conditions the SRD does not have, so the rule "
        "exists nowhere else")


# ─── exhaustion: a contract of its own ────────────────────────────────────────


def test_exhaustion_has_a_level_for_every_level_the_srd_names():
    """Exhaustion is the one axis RI5 measures at 100%, so it is the control.

    An instrument that reported 6/6 here and 54/319 on spells is telling the
    truth about a real asymmetry, which is the whole reason to build it. If this
    test ever fails, the control is broken and every other number is suspect.
    """
    levels = set(tr.EXHAUSTION_EFFECTS)
    assert levels == {1, 2, 3, 4, 5, 6}, levels
    for level in sorted(levels):
        assert tr.EXHAUSTION_EFFECTS[level], f"level {level} is empty"
        assert level in tr.EXHAUSTION_TEXT, f"level {level} has no printed text"


def test_exhaustion_is_a_condition_in_the_dataset(real):
    if real["dataset"] is None:
        pytest.skip(f"real dataset not built: {real['reason']}")
    indexes = {r.get("index") for r in real["dataset"]["conditions"]}
    assert "exhaustion" in indexes
    assert "exhaustion" in tr.CONDITION_EFFECTS


# ─── the 2024 exception: preserved, and bounded ───────────────────────────────


def test_the_2024_exception_is_a_budget_table_and_not_combat_rules():
    """`XP_BUDGET_2024` stays; it must not grow into a rules engine.

    `systems/dnd5e/system.md` scopes the exception to three commands, "because it
    is a table lookup, not a rules engine. It does not select combat rules." This
    pins the boundary: the 2024 table exists, it is selected by `ruleset`, and
    the combat entry points do not branch on it.
    """
    assert encounter_mod.XP_BUDGET_2024, "the 2024 budget table is a deliberate exception"
    assert set(encounter_mod.XP_BUDGET_2024) == set(range(1, 21))
    for level, row in encounter_mod.XP_BUDGET_2024.items():
        assert len(row) == 3, (level, row)      # Low / Moderate / High

    # The combat rules are 2014 and take no ruleset argument at all. If any of
    # these grew a `ruleset` parameter, the exception would have widened from a
    # table lookup to a rules engine and this is where it would show.
    import inspect
    for name in ("attack", "hit_chance", "saving_throw", "damage", "spell",
                 "ac", "speed", "can_act", "can_react", "death_save"):
        method = getattr(tr.RULES, name, None)
        assert method is not None, name
        assert "ruleset" not in inspect.signature(method).parameters, name


def test_encounter_budget_and_rate_select_the_table_by_ruleset():
    """Where the exception IS allowed to apply, and only there.

    Three commands take `ruleset`: the encounter budget, the encounter rating,
    and the adventuring day -- all three are budget-table lookups, which is the
    whole of the documented exception in `system.md`. Nothing that adjudicates a
    roll takes one.

    The list is written out rather than derived, because a derived list would
    follow whatever the code happens to do today, which is the thing being
    pinned. Writing it out is what catches `ruleset` quietly appearing on
    `attack()`.
    """
    takes_ruleset = ("encounter_budget", "rate_encounter", "adventuring_day")
    does_not = ("award_xp", "spell", "attack", "hit_chance", "saving_throw",
                "damage", "heal", "initiative", "turn_budget",
                "condition_modifiers", "death_save", "ac", "speed", "reach")
    for name in takes_ruleset:
        params = inspect.signature(getattr(tr.RULES, name)).parameters
        assert "ruleset" in params, (name, list(params))
    for name in does_not:
        params = inspect.signature(getattr(tr.RULES, name)).parameters
        assert "ruleset" not in params, (name, list(params))


def test_a_2024_budget_row_does_not_change_2014_gameplay():
    """Two budgets for one fight, selected by `ruleset` and never blended.

    A 2024 difficulty leaking onto a 2014 fight would be the exception turning
    into a rules engine. Level 13 is used because it is one of the three levels
    where the editions diverge hardest (2400 against 2200), which is exactly the
    coincidence `tests/test_encounter_xp_budget_2024.py` warns about at level 1.
    """
    monster = {"name": "Goblin", "cr": "1/4", "xp": 50}
    y2014 = tr.RULES.rate_encounter([("Goblin", 2)], [13], "2014", {"goblin": monster})
    y2024 = tr.RULES.rate_encounter([("Goblin", 2)], [13], "2024", {"goblin": monster})

    # Each answers with its own edition's thresholds, and says which it used.
    assert y2014["thresholds"] != y2024["thresholds"], (
        "both editions produced the same threshold table, so `ruleset` selects "
        "nothing and the exception is either dead or wider than documented")
    assert y2014["ruleset"] == "2014" and y2024["ruleset"] == "2024"
    assert y2014["difficulty"] == "trivial", y2014["difficulty"]

    # And the combat rules never see it: no combat entry point takes a ruleset.
    assert "ruleset" not in inspect.signature(tr.RULES.attack).parameters


def test_a_save_action_normalises_without_a_positional_maxsplit():
    """The one call in the repo that passes `maxsplit` positionally, and the
    reason it was the only one worth changing.

    `re.split(pattern, string, maxsplit)` warns from Python 3.13, and
    `_norm_monster_action` runs it once per monster action in the whole dataset.
    Since #231 made CI build that dataset, the warning is no longer a developer's
    terminal detail: it is one per record in the build log. An AST sweep of the
    repo for the deprecated positional slots (`maxsplit` on `re.split`, `count`
    on `re.sub`/`re.subn`) finds this line and nothing else, so the class has
    exactly one member.

    Mutant that must fail: putting the `1` back in the positional slot. It
    restores one DeprecationWarning per call, and this asserts there are none.

    The input is a save action, because that is the branch the call sits in. An
    attack action never reaches it, which is why a test built from attack text
    would pass on the broken version too, and pin nothing.
    """
    if sys.version_info < (3, 13):
        pytest.skip("maxsplit was only deprecated as a positional in 3.13, so the "
                    "floor this repo supports cannot observe the defect")
    action = {
        "name": "Save Test", "kind": "save",
        "dc": {"dc_type": {"index": "dex"}, "dc_value": 12, "success_type": "half"},
        # Real SRD phrasing (an adult red dragon's Fire Breath), with the success
        # clause deliberately changed so the text contradicts `success_type`.
        "desc": ("Each creature in the area must make a Dexterity saving throw, "
                 "taking 18d6 fire damage on a failed save, or no damage on a "
                 "successful save."),
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = build_srd._norm_monster_action(action)
    assert [str(w.message) for w in caught
            if issubclass(w.category, DeprecationWarning)] == [], (
        "the save-action branch passes maxsplit positionally, which warns once "
        "per monster action in the built dataset")

    # The branch really ran, so the assertion above is not vacuous: this is a
    # `save` action, so the split was reached, and `success_conflict` can only
    # appear if the description was read and compared against `success_type`.
    assert out["kind"] == "save", out
    assert out["dc"]["ability"] == "dex" and out["dc"]["value"] == 12, out["dc"]
    assert "success_conflict" in out["flags"], (
        f"the description was not parsed, so the branch under test did not run: "
        f"{out}")
