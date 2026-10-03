"""Out-of-combat spell boundaries: command intent, and the receipt after the commit.

WHY THIS FILE EXISTS
====================

`docs/test-reports/TEST-REPORT-pt2-spells-advisors-2026-09-30.md` found two code bugs
and a set of DM-behaviour findings against a build from 2026-09-30. Issue #251 asks for
each report shape to be re-pinned on the current build with fake endpoint fixtures, and
for the cases newer routing PRs already fixed to be closed.

This file is the re-pin. Every case below states what it found, and the ones that no
longer reproduce say so with the reason. Nothing here asserts a defect that was not
reproduced on `4c9127b`.

THE FOUR CASES
==============

  B1  a spell slot was spent and the report thrown away when narration failed.
      `Session._cast_spell` mutates the sheet and the tracker BEFORE it narrates, and
      the narration call had no handler, so the REPL replaced the whole turn with a
      one-line error. `Session._narrate` already had the handler for exactly this
      reason. REPRODUCED and FIXED: the two `except llm.LLMError` arms now match.

  B2  a status QUESTION could spend a slot, because `cast` is a field the model sets
      and nothing checked that the player's line was a cast. The reported input no
      longer reproduces -- `fightq.classify(scope="explore")` claims an AC question and
      answers it from the sheet, so the model is never called (commit `6aba0f0`, PR
      #150). The CLASS reproduces: the router has no topic for spell slots, so
      "how many first-level slots do I have left" still reaches the model. FIXED on the
      narrow side instead: a `cast` on a question-shaped line is refused out loud.

  P3  the "already covered" guard in `_effect` is dead on the out-of-combat path.
      Each `_cast_lookup` re-reads the sheet, so `t.ac` is the sheet's AC and
      `t.ac >= base` is never true. STILL REPRODUCES, and is deliberately NOT fixed:
      see the test's docstring, which is the finding.

  P2  `state.md` is never refreshed after an out-of-combat cast. STILL TRUE and
      deliberate: `write_back` never touches the sheet's AC field (see
      `tactics_sheet.py`) and the computed AC goes to `tracker.json` for the sidebar.
      Pinned so the next reader does not mistake it for a new bug.

WHAT IS NOT HERE, AND WHY
=========================

The D-series (a DM that contradicted the engine's own AC, invented slot exhaustion,
answered a yes/no with narration) is model behaviour and needs an endpoint. The two
guards that now stand in front of it are pinned in
`tests/test_localdm_reply.py` and `tests/test_injection_guard.py`: `unbacked_numbers`
for a number the engine did not produce, and the out-of-fight routing that answers a
sheet question from the engine with no model call at all. Re-pinning DM prose without an
endpoint would be measuring the fixtures.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT / "scripts", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from localdm import llm, reply                              # noqa: E402
from localdm.play import CAST_ON_A_QUESTION, Session         # noqa: E402
from tests.localdm_fakes import FakeBridge, FakeClient      # noqa: E402

NULLS = '\n{"escalate": null, "command": null}'
MODELS = llm.Models("dm-local", "dm-advisor", "dm-council")
SHEET = ROOT / "tests" / "fixtures" / "Kairos_Level1.md"
CLEAN = "Silver light settles around your feathers and the ward takes hold."


def root_for(tmp_path, monkeypatch):
    """A GM_CAMPAIGN_ROOT inside tmp. `_cast_spell` hands the campaign name to
    `tracker`, which resolves it through `paths.campaigns_dir()`, so without this the
    test would write to a real save."""
    root = tmp_path / "root"
    (root / "campaigns").mkdir(parents=True)
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    return root / "campaigns"


def camp(root, name="cat9"):
    d = root / name
    (d / "characters").mkdir(parents=True)
    (d / "characters" / SHEET.name).write_text(SHEET.read_text(encoding="utf-8"),
                                               encoding="utf-8")
    (d / "state.md").write_text(f"# Campaign: {name}\n\n## Current Situation\nA quiet room.\n",
                                encoding="utf-8")
    return d


def session(root, replies, *, bridge=None, name="cat9", status=None):
    seq = list(replies)
    seen = {"n": 0}

    def counting(m, msgs, role):
        i = min(seen["n"], len(seq) - 1)
        seen["n"] += 1
        if isinstance(seq[i], Exception):
            raise seq[i]
        return seq[i]

    c = FakeClient(counting)
    said: list = []
    s = Session(name, c, MODELS, camp_dir=camp(root, name), bridge=bridge or FakeBridge(),
                shadow=False, on_stall=lambda _t: None,
                on_status=(status if status is not None else said.append))
    return s, c


def sheet_of(s):
    return (s.camp_dir / "characters" / SHEET.name).read_text(encoding="utf-8")


def cast_reply(spell="Mage Armor"):
    return CLEAN + '\n{"escalate": null, "command": null, "check": null, "cast": "%s"}' % spell


# ── B1: the committed mechanical receipt survives a narration failure ─────────

def test_a_slot_spent_by_a_cast_survives_a_narration_failure(tmp_path, monkeypatch):
    """The report's B1, reproduced and fixed.

    The cast is committed before the narration: the slot is incremented, the sheet is
    written and the tracker holds the effect. So a failed narration cannot un-commit
    it, and the only honest output is the engine's own line.
    """
    root = root_for(tmp_path, monkeypatch)
    boom = llm.LLMError("HTTP 401 from http://localhost:20128/v1/chat/completions")
    said: list = []
    s, c = session(root, [boom], status=said.append)
    out = s._cast_spell("Mage Armor")
    assert out and "AC is now 15" in out[0], f"the engine line was swallowed: {out}"
    assert any("no narration for the cast" in line for line in said), said
    assert "| 1st | 2 | 1 |" in sheet_of(s), "the slot was not spent at all"
    assert out[0].startswith("("), "the receipt is the engine line, not narration"


def test_the_turn_survives_a_cast_whose_narration_failed(tmp_path, monkeypatch):
    """The report's B1 observed from the turn, which is where the player lost it: the
    REPL handler at `main()` replaced the whole turn with one error line, so the
    receipt was committed to the sheet and shown to nobody.

    The first draft fails here and fails there: `_cast_spell` has no handler, so the
    exception reaches `handle` and out to `main()`. The fixed version returns the
    receipt, so the test asserts both the receipt and that no error escaped.
    """
    root = root_for(tmp_path, monkeypatch)
    boom = llm.LLMError("HTTP 401 credits exhausted")
    # The turn draft answers; the CAST_TASK narration behind it fails. Failing the
    # first call would never reach `_cast_spell` at all.
    s, c = session(root, [cast_reply(), boom, CLEAN + NULLS])
    out = s.handle("I cast mage armor.")
    assert any("AC is now 15" in chunk for chunk in out), out
    assert not any("model unavailable" in chunk for chunk in out), (
        "the REPL's own error line means the receipt never reached the player")
    assert "| 1st | 2 | 1 |" in sheet_of(s)
    assert s.handle("I wait."), "the session did not survive the failed narration"


def test_a_cast_narrated_normally_still_returns_both_chunks(tmp_path, monkeypatch):
    """The happy path must be untouched by the handler: receipt plus narration."""
    root = root_for(tmp_path, monkeypatch)
    s, c = session(root, [cast_reply()])
    out = s._cast_spell("Mage Armor")
    assert len(out) == 2 and out[0].startswith("(") and "AC is now 15" in out[0]
    assert CLEAN in out[1]
    assert "| 1st | 2 | 1 |" in sheet_of(s)


# ── B2: a status question never spends a slot ────────────────────────────────

@pytest.mark.parametrize("line", [
    "how many first-level slots do I have left",
    "can I cast magic missile",
    "which spells do I have prepared?",
    "should I cast mage armor?",
])
def test_a_question_shaped_line_never_spends_a_slot(tmp_path, monkeypatch, line):
    """The report's B2 as a class. `cast` is a field the MODEL sets; nothing about the
    player's line said they cast anything, so a DM that answered a question with a
    cast spent a slot on it. Every line here reaches the model and is refused."""
    root = root_for(tmp_path, monkeypatch)
    s, c = session(root, [cast_reply("Mage Armor")])
    out = s.handle(line)
    assert "| 1st | 2 | 0 |" in sheet_of(s), f"{line!r} spent a slot"
    assert any(CAST_ON_A_QUESTION.format(spell="Mage Armor").split(":")[0] in o
               or "was not cast: that was a question" in o for o in out), out


def test_an_imperative_cast_still_spends_the_slot(tmp_path, monkeypatch):
    """The other side of the guard. A conservative rule that also refused real casts
    would be worse than the defect, so the imperative is pinned as loudly as the
    question."""
    root = root_for(tmp_path, monkeypatch)
    s, c = session(root, [cast_reply()])
    out = s.handle("I cast mage armor.")
    assert "| 1st | 2 | 1 |" in sheet_of(s), "an imperative cast stopped working"
    assert any("AC is now 15" in o for o in out)


def test_a_cast_the_engine_cannot_apply_is_still_refused_in_the_open(tmp_path, monkeypatch):
    """PR #169's behaviour must survive the new guard: an off-sheet spell is refused
    by name, not dropped, and spends nothing."""
    root = root_for(tmp_path, monkeypatch)
    s, c = session(root, [cast_reply("Fire Bolt")])
    out = s.handle("I cast fire bolt.")
    assert any("was not applied: out of a fight the engine resolves only Mage Armor"
               in o for o in out), out
    assert "| 1st | 2 | 0 |" in sheet_of(s)


def test_a_cast_of_an_unresolvable_spell_says_what_the_engine_resolves(tmp_path, monkeypatch):
    """Bless is on Kairos's sheet and has no `mode == "effect"` entry, so the engine
    resolves only Mage Armor out of a fight. That is said out loud rather than
    narrated as a buff that never applied."""
    root = root_for(tmp_path, monkeypatch)
    s, c = session(root, [cast_reply("Bless")])
    out = s.handle("I cast bless.")
    assert any("Bless is not a spell on the character sheet" in o for o in out), out
    assert "| 1st | 2 | 0 |" in sheet_of(s)


# ── P3: the repeated-effect guard is dead, and that is the finding ────────────

def test_the_already_covered_guard_in_the_mage_armor_effect_is_unreachable(tmp_path, monkeypatch):
    """P3 from the report, STILL REPRODUCING, deliberately not fixed.

    `spells._effect` returns "AC stays N (already N or better)" when
    `t.ac >= 13 + dex_mod`. But every out-of-combat cast goes through `_cast_lookup`,
    which re-reads the sheet and builds a fresh token, and `write_back` never
    persists the AC field (see `tactics_sheet.py`). So `t.ac` is the sheet's 12 every
    time, the guard is never true, and a second cast re-reports "AC is now 15" and
    overwrites `extra["ac_before_mage_armor"]` with 12 rather than 12... which happens
    to be harmless. The message is the thing that is wrong: it promises a branch that
    cannot be reached.

    Fixing it means deciding where the computed AC is stored, which is a data-model
    question (`state.md` staleness, the P2 finding, is the same question) and not a
    one-line change. Asserted as the current behaviour so the next reader does not
    re-derive it.
    """
    root = root_for(tmp_path, monkeypatch)
    s, c = session(root, [cast_reply()])
    first = s._cast_spell("Mage Armor")
    second = s._cast_spell("Mage Armor")
    assert "AC is now 15" in first[0]
    assert "AC stays 15" not in second[0], (
        "the already-covered guard now fires; P3 is resolved and this test is stale")
    assert "AC is now 15" in second[0], "the second cast reported something else"
    assert "| 1st | 2 | 2 |" in sheet_of(s), "the second cast spent no slot"


# ── P2: the sheet's AC field and state.md are stale by design ────────────────

def test_the_sheet_ac_field_is_left_alone_and_the_computed_ac_goes_to_the_tracker(tmp_path,
                                                                                 monkeypatch):
    """P2 from the report, STILL TRUE and deliberate.

    `write_back` never touches the sheet's `**AC:**` field, so the sheet keeps its 12
    while the tracker records 15 and `context.party_stats` reads the tracker. A GM
    reading the sheet gets the pre-Mage-Armor number, which is what P2 complains about.
    Recorded rather than fixed, and pinned so a future change to `write_back` has to
    decide this deliberately.
    """
    root = root_for(tmp_path, monkeypatch)
    s, c = session(root, [cast_reply()])
    s._cast_spell("Mage Armor")
    text = sheet_of(s)
    assert "**AC:** 12" in text, "the sheet's AC field is no longer the value write_back skips"
    tracker = (s.camp_dir / "tracker.json")
    assert tracker.exists(), "the computed AC did not reach the tracker"
    import json
    data = json.loads(tracker.read_text(encoding="utf-8"))
    held = data["kairos"]["effects"]
    assert any(e["name"] == "Mage Armor" and e.get("ac") == 15
               for e in held), data
    state = (s.camp_dir / "state.md").read_text(encoding="utf-8")
    assert "AC 15" not in state, "state.md is refreshed after a cast now; P2 is stale"


# ── the routing that already closed the reported input ───────────────────────

def test_the_reported_status_question_is_answered_from_the_sheet_and_never_reaches_the_model(
        tmp_path, monkeypatch):
    """The report's own input, already fixed by a newer routing PR (commit `6aba0f0`).

    Closed rather than re-fixed: `fightq.classify(scope="explore")` claims the AC
    question, `_autopilot` returns the engine's line, `_player_turn` never runs and the
    model is never called. So there is no `cast` field to honour and no slot at risk.
    """
    root = root_for(tmp_path, monkeypatch)
    s, c = session(root, [cast_reply()])
    out = s.handle("what is my AC right now and how many first-level slots do I have left")
    assert c.dm_calls() == [], "the model was called for a sheet question"
    assert out == ["Kairos_Level1: AC 12."], out
    assert "| 1st | 2 | 0 |" in sheet_of(s)


def test_the_router_has_no_topic_for_spell_slots_which_is_why_the_guard_exists(
        tmp_path, monkeypatch):
    """The half of B2 the router did NOT close, stated as the reason the new guard is
    in `_player_turn` rather than in `fightq`. Adding a slots topic is the better fix
    and is not made here: `sheet_facts` would need a slot count, and `fightq`'s topics
    are a closed set with an answer for each. Until then the guard is what stops a
    question from costing a level."""
    from tactics import fightq
    for line in ("how many first-level slots do I have left", "can I cast magic missile"):
        assert fightq.classify(line, (), scope="explore") is None, (
            f"the router now claims {line!r}; the guard in _player_turn may be redundant")
    assert fightq.classify("what is my AC right now", (), scope="explore") is not None


def test_no_spelling_uses_an_em_dash():
    assert "\u2014" not in pathlib.Path(__file__).read_text(encoding="utf-8")