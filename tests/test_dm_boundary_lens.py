"""The DM boundary baseline: what the engine's own checks catch, and what they miss.

WHY THESE TESTS EXIST
=====================

Issue #127 asks for a measurement of two rates -- how often the DM asks for a check,
and how often the DM decides something the engine owns -- and the first thing a
measurement needs is a definition precise enough to be measured twice.

The definition used here: **puppeting is the DM deciding something the engine owns.**
The engine owns combat decisions and every mechanical number; the DM narrates outcomes
and chooses roleplayed reactions. `tests/dm_boundary_lens.py` enumerates the boundaries
and the shipped check that would catch each one, and this module drives the four
measurements it can drive without an endpoint.

WHAT IS DELIBERATELY NOT CLAIMED
=================================

The model-side rates are not measured and are not guessed. Whether a 9B model requests
a check on an uncertain turn, and how often it puppets in live play, both need an
endpoint. The two harnesses that have one (`dnd-gm/test_dice_lens.py`,
`dnd-gm/test_puppet_lens.py`) are named in the lens report rather than replaced, and
`test_the_report_names_the_gap_instead_of_hiding_it` keeps that true.

So every number below is a rate over the DETECTORS, measured on a pinned corpus. That
is not nothing: per-detector recall and the false-positive rate on near-miss controls
are exactly what a live run cannot cheaply produce, and they are the numbers a change to
`reply.py` has to be judged against.

THE THREE FINDINGS THESE TESTS PIN
==================================

Each is a measured gap in a shipped guardrail, not a proposed behaviour. They are
asserted as facts about the current build so that a later change to any of them has to
be deliberate.

  1. `reply.reveals_check_outcome` misses two of four planted pre-resolved shapes: a
     result stated as a bare fragment ("A name, inked.") and a result stated as
     knowledge ("You know there is no name here."). Both are the beat the guardrail
     exists for: the check has not been rolled yet.
  2. `reply.speaks_for_player` reads no player line, so it fires on the DM faithfully
     echoing the player's own dialogue back, and on a physical sensation with no
     emotion in it. Two false positives on twelve controls, which is a wasted model
     call each rather than a shipped defect -- the retry is adopted only when clean.
  3. `reply.speaks_for_player` reads second person only, so the third-person shape
     ("Kairos feels a chill") is invisible to the loop. The puppet lens catches it; the
     lens is an outer-repo instrument, not something the loop consults.

Findings 1 and 3 are coverage gaps in the engine's own boundary and belong to
follow-up work. They are reported here rather than fixed, because #127 is a
measurement and a guardrail edit would move the baseline the next issue builds on.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from dm_boundary_lens import (  # noqa: E402
    BOUNDARIES, BOUNDARY_IDS, CLEAN, PLANTED, PC_NAMES, measure, report, violation_rate,
)
from localdm import llm, reply                                # noqa: E402
from localdm.play import Session                              # noqa: E402
from tests.localdm_fakes import FakeBridge, FakeClient        # noqa: E402

NULLS = '\n{"escalate": null, "command": null}'
MODELS = llm.Models("dm-local", "dm-advisor", "dm-council")
SHEET = ROOT / "tests" / "fixtures" / "Kairos_Level1.md"

BASE = measure()


# ── helpers: a real campaign, and a scripted DM ───────────────────────────────

def camp(root, name="cat9"):
    """A campaign with the fixture sheet in it, and nothing sealed.

    `root` is a CAMPAIGN ROOT, not a campaign directory, because `Session._cast_spell`
    hands the campaign name to `tracker`, which resolves it through
    `paths.campaigns_dir()`. Pointing GM_CAMPAIGN_ROOT at the sandbox is what keeps
    that write inside tmp and off the player's save.

    `tests/fixtures/Kairos_Level1.md` is the player-facing sheet the outer-repo
    harnesses build their sandboxes from. Its skills are what M2's policy verdicts are
    computed against: Perception +2, Investigation +6, Stealth +4, and no Swim.
    """
    d = root / name
    (d / "characters").mkdir(parents=True)
    (d / "characters" / SHEET.name).write_text(SHEET.read_text(encoding="utf-8"),
                                               encoding="utf-8")
    (d / "state.md").write_text("# Campaign: " + name + "\n\n## Current Situation\n"
                                "A quiet room.\n", encoding="utf-8")
    return d


def root_for(tmp_path, monkeypatch):
    """A GM_CAMPAIGN_ROOT pointing inside tmp, for the tests that reach tracker.

    `paths.campaigns_dir()` is `GM_CAMPAIGN_ROOT / "campaigns"`, so the root handed
    here is the PARENT of the directory the campaigns land in.
    """
    root = tmp_path / "root"
    (root / "campaigns").mkdir(parents=True)
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    return root / "campaigns"


def session(root, responder, *, bridge=None, combat="model", name="cat9"):
    c = FakeClient(responder)
    s = Session(name, c, MODELS, camp_dir=camp(root, name), bridge=bridge or FakeBridge(),
                shadow=False, combat=combat)
    return s, c


def quiet(text="The reeds whisper."):
    return lambda m, msgs, role: text + NULLS


def fight_snapshot(current="kairos", controller="player"):
    return {"status": "active", "round": 1, "key": "frog-1,kairos",
            "current": {"id": current, "name": current.title(),
                        "side": "pc" if controller == "player" else "enemy",
                        "controller": controller},
            "tokens": [{"id": "kairos", "name": "Kairos", "side": "pc", "hp": 8,
                        "max_hp": 8, "dead": False, "controller": "player"},
                       {"id": "frog-1", "name": "Giant Frog 1", "side": "enemy", "hp": 18,
                        "max_hp": 18, "dead": False, "controller": "gm"}]}


def with_fight(s: Session):
    """Put a real, started encounter on disk under the campaign.

    `Session._autopilot` loads `combat/encounter.json` through `tactics.state` and
    reads `enc.current` before it decides whether the player's line is a command, so a
    fight whose state only lives in the fake bridge cannot reach the combat beat. The
    same two lines tests/test_localdm_fightq.py uses, plus a started turn.
    """
    from tactics import engine, state
    enc = state.Encounter(campaign="cat9",
                          grid={"width": 12, "height": 10,
                                "rows": ["." * 12] * 10,
                                "legend": {".": "floor"},
                                "terrain_types": {"floor": {"cost": 5,
                                                             "blocks_sight": False}}},
                          meta={"name": "The Reading Room"})
    enc.tokens = {"kairos": _tok("kairos", "Kairos", "pc", 0, 0, controller="player"),
                  "frog-1": _tok("frog-1", "Giant Frog 1", "enemy", 9, 4)}
    enc.order = ["kairos", "frog-1"]
    enc.round, enc.turn_index = 1, 0
    engine._start_turn(enc, _roller())
    state.save(enc, state.encounter_path(s.camp_dir))
    return s


def _roller():
    from tactics.roller import Roller
    return Roller()


def _tok(tid, name, side, x, y, hp=8, controller="gm"):
    from tactics.state import Token
    return Token(id=tid, name=name, side=side, x=x, y=y, hp=hp, max_hp=hp, ac=12, speed=30,
                 dex_mod=2, controller=controller,
                 attacks=[{"name": "Dagger", "type": "melee_or_ranged", "source": "weapon",
                           "bonus": 4, "reach": 5, "range": [20, 60],
                           "damage": [{"dice": "1d4+2", "type": "piercing"}], "flags": []}],
                 source={"kind": "fixture", "ref": tid})


def user_text(call):
    return call[2][1]["content"]


# ── M1: the detection rate, and the definition it rests on ────────────────────

def test_the_boundary_set_is_the_nine_decisions_the_loop_can_be_crossed_on():
    """The definition is the measurement. A boundary added or dropped silently changes
    every rate below, so the set is pinned rather than derived."""
    assert BOUNDARY_IDS == ("check-outcome", "check-decision", "player-voice",
                            "cast-result", "mechanical-number", "economy-grant",
                            "fake-system-block", "mid-fight-check", "mid-fight-cast")
    assert len(set(BOUNDARY_IDS)) == len(BOUNDARY_IDS), "a boundary id is duplicated"


def test_every_boundary_names_the_module_that_owns_the_decision():
    """A boundary with no owner is a boundary nobody enforces."""
    for b in BOUNDARIES:
        assert "/" in b.owned or "." in b.owned, f"{b.id} names no owning module: {b.owned!r}"
        assert b.beat in ("exploration", "conversation", "combat")


def test_the_two_gates_are_measured_by_the_loop_not_by_a_detector():
    """`mid-fight-check` and `mid-fight-cast` are not text shapes: a fight either rolls
    the check or does not. M4 drives them through Session, so they carry no detector
    and must not be scored by M1 as though they did."""
    gates = {b.id for b in BOUNDARIES if b.kind == "gate"}
    assert gates == {"check-decision", "mid-fight-check", "mid-fight-cast"}
    assert {r.boundary for r in BASE.rows}.isdisjoint(gates)


def test_every_boundary_that_is_not_a_gate_has_at_least_one_detector():
    """A boundary with no detector is a coverage gap, and it is the finding. The set as
    it stands has one for every non-gate boundary; if a future edit leaves a text
    boundary unwatched this is the test that says so."""
    for b in BOUNDARIES:
        if b.kind == "text":
            assert b.detectors, f"{b.id} is a text boundary with no detector"


def test_every_planted_case_is_catchable_by_its_own_boundary_detector():
    """The corpus rule: a planted case is a shape the boundary is written to catch, so
    zero caught would be a detector miss rather than a corpus artefact. This is the
    check that keeps a silent miss from reading as a pass."""
    for r in BASE.rows:
        assert r.planted, f"{r.detector} has no planted case, so its rate is invented"
        assert r.caught or r.missed, f"{r.detector} scored neither a catch nor a miss"


def test_the_clean_corpus_is_near_misses_and_not_empty_prose():
    """A control that is merely legal-ish measures the regex, not the defect. Four of
    them carry a quotation, an involuntary reaction, an offered choice or a number the
    engine did produce; requiring more would pad the corpus with prose no detector
    reads."""
    assert len(CLEAN) >= 12
    assert sum(1 for c in CLEAN if '"' in c.reply) >= 4
    assert any("flinch" in c.reply for c in CLEAN), "no involuntary-reaction control"
    assert any("could" in c.reply for c in CLEAN), "no offered-choice control"
    assert any(c.rolled for c in CLEAN), "no post-roll control"


def test_the_baseline_detection_rate_is_what_the_report_says():
    """The pinned number. 14 of 17 planted crossings are caught; the three misses are
    the two pre-resolved shapes the shipped guard does not read and the third-person
    shape it cannot see. If any of that changes, this fails and the baseline moves
    deliberately."""
    assert sum(r.caught for r in BASE.rows) == 14
    assert sum(r.planted for r in BASE.rows) == 17
    assert round(violation_rate(BASE), 2) == 0.82


def test_the_two_coverage_gaps_are_pinned_as_gaps():
    """Finding 1: the pre-roll guard misses the two verb-less shapes."""
    r = BASE.row("reply.reveals_check_outcome")
    assert r.caught == 2 and r.planted == 4
    assert r.missed == ("pre-resolved-fragment", "pre-resolved-knowledge")
    # and it is not a corpus artefact: the puppet lens reads the same beat as a
    # pre-resolved attempt, which is what makes this a detector gap and not a
    # disagreement about what a violation is.
    from puppet_lens_detector import detect as lens
    for cid in r.missed:
        case = next(c for c in PLANTED if c.id == cid)
        assert any(f.kind == "pre-resolved"
                   for f in lens(case.player, case.reply, PC_NAMES, rolled=False))


def test_the_speaks_for_player_false_positives_are_pinned():
    """Finding 2: two of twelve controls trip the agency guard, because it reads no
    player line. The cost is a wasted model call, not a shipped defect: the retry is
    adopted only when it comes back clean (play._dm)."""
    r = BASE.row("reply.speaks_for_player")
    assert r.false_positives == ("echo-of-own-words", "sensation-not-emotion")
    assert r.caught == 1 and r.planted == 2


def test_third_person_puppeting_is_invisible_to_the_loop():
    """Finding 3: the guardrail reads second person only, so the shape the outer-repo
    lens was built for never reaches it."""
    assert BASE.row("reply.speaks_for_player").missed == ("third-person-emotion",)
    assert not reply.speaks_for_player(
        "Kairos feels a chill of recognition and knows at once that Hesper lied.")
    assert BASE.row("puppet_lens_detector").caught == 2


def test_the_only_noisy_detector_is_the_one_with_no_player_line():
    """Attributes the false positives rather than averaging them away: the lens reads
    the player's own words, so it is clean on all twelve controls."""
    assert BASE.row("puppet_lens_detector").false_positives == ()
    assert BASE.noisy == ["reply.speaks_for_player"]


def test_the_report_names_the_gap_instead_of_hiding_it():
    """A rate with no stated boundary is worse than no rate, and the boundary here is
    the endpoint this lane does not have."""
    text = report(BASE, commit="478fc7e")
    assert "dnd-gm/test_dice_lens.py" in text and "dnd-gm/test_puppet_lens.py" in text
    assert "rate over the" in text and "DETECTORS" in text
    for b in BOUNDARIES:
        if b.detectors:
            assert b.detectors[0].id in text


# ── M2: the check policy, through the real loop ───────────────────────────────

def m2_summary(tmp_path) -> str:
    """Verdict distribution over check requests that reached `Session._ability_check`.

    The denominator is check REQUESTS, not turns: one turn asks for one check, and the
    number worth reporting is what the engine did with each one. Driven through the
    real method so the sheet lookup, the retry ledger and the policy are all in the
    path, and so "no model call" is true by construction rather than by assertion.
    """
    s, _c = session(tmp_path, quiet())
    verdicts: dict = {}

    def verdict(spec, meta=None):
        out = s._ability_check(spec, "I try it.", meta)
        first = out[0]
        kind = ("retry-refused" if "already tried" in first
                else "not-on-sheet" if "is not a skill on this sheet" in first
                else "no-stakes" if "with no stakes" in first
                else "auto-passive" if "Passive" in first
                else "auto-easy" if "has no stakes and is easy" in first
                else "rolled")
        verdicts[kind] = verdicts.get(kind, 0) + 1
        return out

    verdict("Perception moderate", {"stakes": "the guard turns", "target": "the lock"})
    verdict("Stealth hard", {"stakes": "the guard hears me", "target": "the guard"})
    verdict("Perception easy", {})                        # passive already answers it
    verdict("Stealth very easy", {"stakes": ""})          # no stakes, easy: free success
    verdict("Investigation hard", {"stakes": ""})         # no stakes, above EASY_MAX
    verdict("Swim hard", {"stakes": "the current takes me", "target": "the river"})
    verdict("Perception 30", {"stakes": "the bell rings", "target": "the ward-lock"})
    verdict("Perception 30", {"stakes": "the bell rings", "target": "the ward-lock"})

    total = sum(verdicts.values())
    return (f"{len(verdicts)} kinds over {total} requests: "
            f"{', '.join(f'{k} {v}' for k, v in sorted(verdicts.items()))}; "
            f"rolled {verdicts.get('rolled', 0)}/{total}, "
            f"retry refused {verdicts.get('retry-refused', 0)}")


def test_the_check_policy_answers_every_kind_it_can_see(tmp_path):
    """The whole point of M2: the engine, not the model, decides what happens to a
    requested check. Every verdict below is produced by `checks.decide` or by the
    sheet lookup inside `_ability_check`, never by a model."""
    s, _c = session(tmp_path, quiet())
    rolled = s._ability_check("Perception moderate",
                              "I look.", {"stakes": "the guard turns", "target": "the lock"})
    assert "rolled a Perception check" in rolled[0] and "against DC 15" in rolled[0]

    passive = s._ability_check("Perception easy", "I look.", {})
    assert "Passive Perception 12 meets DC 10" in passive[0]

    no_stakes = s._ability_check("Investigation hard", "I read it.", {"stakes": ""})
    assert "was asked for with no stakes" in no_stakes[0]
    assert "nothing was rolled" in no_stakes[0]

    easy = s._ability_check("Stealth very easy", "I edge.", {"stakes": ""})
    assert "at DC 5 has no stakes and is easy" in easy[0]

    off_sheet = s._ability_check("Swim hard", "I swim.", {"stakes": "the current",
                                                          "target": "the river"})
    assert "Swim is not a skill on this sheet, so nothing was rolled" in off_sheet[0]
    assert "Stealth" in off_sheet[0]          # it names the sheet's skills back


def test_an_identical_retry_in_one_scene_is_refused_rather_than_rolled_again(tmp_path):
    """The ledger's whole job. A DC 30 check can never succeed, so the second attempt
    is the retry rather than another roll, and no dice are spent on it."""
    s, c = session(tmp_path, quiet())
    meta = {"stakes": "the bell rings", "target": "the ward-lock"}
    first = s._ability_check("Perception 30", "I try again.", meta)
    second = s._ability_check("Perception 30", "I try again.", meta)
    assert "rolled a Perception check" in first[0]
    assert "already tried Perception (the ward-lock) here" in second[0]
    assert "Repeating it changes nothing" in second[0]


def test_a_different_target_is_a_different_attempt_not_a_retry(tmp_path):
    """The ledger keys on the target, so a false positive here would be a player told
    they may not try the second door."""
    s, _c = session(tmp_path, quiet())
    s._ability_check("Perception 30", "I try the door.",
                     {"stakes": "the bell rings", "target": "the ward-lock"})
    other = s._ability_check("Perception 30", "I try the window.",
                            {"stakes": "the bell rings", "target": "the window"})
    assert "rolled a Perception check" in other[0]


def test_the_check_policy_summary_is_reported_with_its_denominator(tmp_path):
    """The string the committed baseline carries. Asserted so the report cannot claim a
    distribution the loop does not produce."""
    text = m2_summary(tmp_path)
    assert "requests:" in text and "rolled" in text and "retry refused" in text
    for kind in ("rolled", "auto-passive", "no-stakes", "auto-easy", "not-on-sheet",
                 "retry-refused"):
        assert kind in text, f"M2 never produced a {kind} verdict: {text}"


# ── M3: does the turn ever ask? ───────────────────────────────────────────────

def test_every_out_of_fight_turn_asks_the_uncertainty_question(tmp_path):
    """M3. This is PR #81's defect pinned: `play._player_turn` used to call `_dm` with
    no task, `context.build_messages` hit `if task:` and emitted no "## Your task"
    block at all, so nothing ever posed the question "is this outcome uncertain?" and a
    full playtest transcript had zero "check" fields. The die was decorative because
    nothing asked the model to consider it."""
    s, c = session(tmp_path, quiet())
    for line, beat in (("I try to sneak past the guard.", "exploration"),
                       ("I ask the archivist what happened to the last student.",
                        "conversation")):
        s.handle(line)
    for call in c.dm_calls():
        sent = user_text(call)
        assert "## Your task" in sent, beat
        assert "uncertain" in sent.lower() or "uncertain outcome" in sent.lower(), beat
    assert len(c.dm_calls()) == 2


def test_a_fight_turn_asks_for_a_command_and_refuses_a_check_in_the_open(tmp_path):
    """M3 in the combat beat, where the ask is deliberately a different one: the model
    reads the action and emits a tactics command, and a check or cast in that reply is
    refused in a visible engine line rather than rolled."""
    def fake(m, msgs, role):
        return '{"check": "Perception hard", "cast": "Mage Armor", "command": null}'

    s, c = session(tmp_path, fake,
                   bridge=FakeBridge(snapshots=[fight_snapshot()]), combat="engine")
    with_fight(s)
    out = s.handle("I try to listen for the guard.")
    sent = user_text(c.dm_calls()[0])
    assert "## Your task" in sent
    assert "grid fight" in sent.lower() and "json command field" in sent.lower()
    assert any("Perception hard was not rolled" in o for o in out)
    assert any("Mage Armor was not cast" in o for o in out)
    assert len(c.dm_calls()) == 1, (
        "one draft, then the engine speaks: the refusal is not a re-draft")


def m3_summary() -> str:
    """The turn-ask rate, from the two out-of-fight beats and the combat beat.

    Denominator: DM turns. The out-of-fight ask is the uncertainty question PR #81
    added; the combat ask is a different one on purpose, and this string says so
    rather than averaging them into a single percentage that means nothing.
    """
    return ("2/2 out-of-fight turns carried '## Your task' with the uncertainty "
            "question; the combat beat asks for a tactics command and refuses a check "
            "in the open (1/1)")


def m4_summary() -> str:
    """The routing rate: of the lines a sheet could answer, how many the engine claims.

    Denominator: ROUTABLE, six player lines. Two fall through to the model, and for
    those the `cast` field is honoured with no check that the line was a cast at all,
    which is #251's B2 in its surviving form.
    """
    claimed = sum(1 for _line, want in ROUTABLE if want)
    dropped = [line for line, want in ROUTABLE if not want]
    return (f"{claimed}/{len(ROUTABLE)} answerable lines claimed by the engine with no "
            f"model call; unclaimed: {'; '.join(dropped)}")

#: Player lines a character sheet could answer, and whether the explore classifier
#: (`fightq.classify`, scope="explore") claims them. The two rows marked
#: `claimed=False` are still the live form of the #251 B2 finding: the classifier
#: does not claim them, so they reach the model. What changed on 2026-10-03 is
#: what happens next. This file used to carry the comment "nothing stops a `cast`
#: field being set on a line that fell through", and that was true when written.
#: #251 added the `CAST_ON_A_QUESTION` guard in `scripts/localdm/play.py`, so a
#: `cast` arriving on an unclaimed line is now refused out loud and costs
#: nothing. The two rows stay in this table because the ROUTING RATE is the
#: measurement: claiming them is the better fix and would change the number.
#:
#: The claimed half is already pinned by tests/test_localdm_fightq.py, whose responder
#: raises if the model is called. This table exists to measure the UNCLAIMED half, which
#: nothing covers.
ROUTABLE = (
    ("what is my AC right now", True),
    ("what's my hp?", True),
    ("what is my passive perception?", True),
    ("what is my AC right now and how many first-level slots do I have left", True),
    ("how many first-level slots do I have left", False),
    ("can I cast magic missile", False),
)


def test_the_engine_claims_four_of_six_answerable_lines_and_lets_two_through(tmp_path):
    """M4. The measured routing rate, and the two gaps in it."""
    claimed = []
    for i, (line, want) in enumerate(ROUTABLE):
        s, c = session(tmp_path, quiet(), name=f"cat9-{i}")
        s.handle(line)
        got = c.dm_calls() == []
        assert got is want, f"{line!r}: engine claimed={got}, expected {want}"
        (claimed if got else []).append(line)
    assert len(claimed) == 4


def test_an_unclaimed_status_question_no_longer_reaches_a_cast_and_spends_a_slot(
        tmp_path, monkeypatch):
    """#251's B2, closed. This assertion used to be the other way round.

    The line does not reach the engine's router, so it reaches the model, and
    `_player_turn` honours `r.cast`. The defect was that nothing checked whether
    the player's line was a cast at all, so "how many first-level slots do I
    have left" could cost a slot and the player was never told. This test pinned
    that as the current behaviour on purpose, and said so: "a routing change
    that closes it must fail this test."

    That routing change is `CAST_ON_A_QUESTION` in `scripts/localdm/play.py`,
    added by #251 in `25c1c50`. The tripwire fired, which is the tripwire working,
    so the assertion is inverted rather than deleted: the defect being closed is
    the thing worth pinning, and a deleted test would let it reopen silently.

    `tests/test_spell_command_boundaries.py` covers the same guard as a class
    (four question-shaped lines) and pins the other side, that an imperative cast
    still spends the slot. This one stays because it is the lens file's measured
    record of where the boundary sits, and the row above it is still `False`.

    GM_CAMPAIGN_ROOT is pointed at tmp so `tracker.cmd_effect` (which `_cast_spell`
    calls) resolves the sandboxed campaign and never the real one.
    """
    root = root_for(tmp_path, monkeypatch)

    def fake(m, msgs, role):
        return ('Somewhere behind you a bell sounds once.\n'
                '{"escalate": null, "command": null, "check": null, "cast": "Mage Armor"}')

    s, c = session(root, fake)
    out = s.handle("how many first-level slots do I have left")
    sheet = (s.camp_dir / "characters" / SHEET.name).read_text(encoding="utf-8")
    assert any(CAST_ON_A_QUESTION.format(spell="Mage Armor").split(":")[0] in o
               or "was not cast: that was a question" in o for o in out), (
        "the refusal was dropped rather than spoken, which is a different defect: "
        "a dropped request the player cannot see is worse than a refused one")
    assert "| 1st | 2 | 0 |" in sheet, (
        "a slot was spent answering a status question. The guard is in "
        "scripts/localdm/play.py; if this fires, the guard stopped firing.")
    assert "AC is now 15" not in " ".join(out), (
        "Mage Armor applied to a question, so the sheet changed on a line that asked "
        "for nothing")
    assert len(c.dm_calls()) == 1, (
        "one draft, then the engine speaks: the refusal is not a re-draft")


def test_a_sheet_question_the_router_claims_never_reaches_the_model(tmp_path):
    """The claimed half of M4, re-pinned here so the measurement and its assertion sit
    together. tests/test_localdm_fightq.py already covers the same shape with a
    responder that raises; this one asserts the answer's source as well as the absence
    of the call."""
    s, c = session(tmp_path, quiet())
    out = s.handle("what is my AC right now")
    assert out == ["Kairos_Level1: AC 12."]
    assert c.dm_calls() == []
    assert [t["role"] for t in s.memory.turns()] == ["player", "engine"]


def test_a_check_asked_during_a_fight_is_refused_and_nothing_is_rolled(tmp_path):
    """`mid-fight-check`. The `check` field is read only outside a fight, so the roll
    the player was expecting never happens -- and the player is told, because silence
    would read as a dropped turn."""
    def fake(m, msgs, role):
        return '{"check": "Perception hard", "command": null}'

    s, c = session(tmp_path, fake,
                   bridge=FakeBridge(snapshots=[fight_snapshot()]), combat="engine")
    with_fight(s)
    out = s.handle("I try to listen for the guard.")
    assert any("A fight is running, so Perception hard was not rolled" in o for o in out)
    assert any("Name an attack, a move, a spell or end turn" in o for o in out)
    assert not any("rolled a Perception check" in o for o in out)
    assert len(c.dm_calls()) == 1, (
        "one draft, then the engine speaks: the refusal is not a re-draft")


def test_a_cast_asked_during_a_fight_is_refused_and_nothing_is_spent(tmp_path):
    """`mid-fight-cast`. Same shape: the engine owns every spell in a fight, the field
    is refused out loud, and no slot is spent.

    The line is one `autopilot.plan` cannot read as a command, so the path under test is
    the one the report names: the model's own `cast` field reaching `_player_turn`. A
    literal "I cast mage armor" is parsed by the autopilot and routed to
    `tactics.spells.cast` instead, which is a second defence in front of this one.
    """
    def fake(m, msgs, role):
        return '{"cast": "Mage Armor", "command": null}'

    s, c = session(tmp_path, fake,
                   bridge=FakeBridge(snapshots=[fight_snapshot()]), combat="engine")
    with_fight(s)
    out = s.handle("I murmur the words of mage armor.")
    assert any("Mage Armor was not cast: a fight is running" in o for o in out)
    sheet = (s.camp_dir / "characters" / SHEET.name).read_text(encoding="utf-8")
    assert "| 1st | 2 | 0 |" in sheet, "a slot was spent during a fight"
    assert len(c.dm_calls()) == 1, "one draft, then the engine refuses in the open"


def test_the_full_report_carries_all_four_measurements(tmp_path, monkeypatch):
    """What the committed baseline prints. M1 always; M2, M3 and M4 filled in, so a
    report with an empty measurement half is a report nobody should read."""
    text = report(BASE, commit="478fc7e", m2=m2_summary(tmp_path),
                  m3=m3_summary(), m4=m4_summary())
    assert "not measured in this run" not in text
    assert "M2  check policy" in text and "M3  turn asks" in text and "M4  routing" in text
    assert "not measured here" in text            # the live gap is still named


def test_no_spelling_uses_an_em_dash():
    """The fork bans them in docs, comments and UI text."""
    src = (ROOT / "tests" / "dm_boundary_lens.py").read_text(encoding="utf-8")
    assert "\u2014" not in src