"""The GM-only agency ledger: trips, correction outcomes, and retry dedup.

WHY THESE TESTS EXIST
=====================

#126 asks for guardrail trips and correction outcomes to be recorded for GM review.
The interesting part is not that a line is written; it is that four things about it are
right, and each has a specific way of being wrong:

  1. **caught is not narrated.** A guardrail fires, buys a corrective retry, and the
     retry is adopted only when it comes back clean (play._dm). So the first draft is
     often kept, and the violation reached the player. A log that recorded "a trip"
     and nothing else would say the guardrail worked every time, which is the opposite
     of what a flag means.
  2. **retries dedupe.** The same guard firing on the retry is the same violation being
     corrected twice. One line with `attempts: 2` is the fact; two lines is a rate
     inflated by however many retries the model needed.
  3. **GM-only.** Never fed to the DM. A DM briefed on its own guardrail report learns
     to write to the detector, the same failure notes.md documents for advisor notes.
  4. **No extra model call.** Every trip is a regex the loop already ran.

THE THREE OUTCOMES
==================

  caught     the guard fired and the text the player was shown does not trip it
  narrated   the guard fired and the shown text still trips it: the player saw it
  refused    the engine refused outright (mid-fight check / cast): nothing was rolled
             or spent, and there is no draft to re-read
  unknown    a draft tripped a guard and the region never settled, so what the player
             saw is not knowable. Recorded rather than dropped, because a silently
             dropped trip is exactly the record the GM cannot tell is missing.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
for p in (ROOT / "scripts", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from localdm import agency, llm, reply               # noqa: E402
from localdm.play import Session                      # noqa: E402
from tests.localdm_fakes import FakeBridge, FakeClient  # noqa: E402

NULLS = '\n{"escalate": null, "command": null}'
MODELS = llm.Models("dm-local", "dm-advisor", "dm-council")

PUPPET = '"I am not here for the books," you say, and the lie sits fine in your mouth.'
CLEAN = "The archivist sets down her pen. She does not look up, and the pen keeps moving."


def ledger(tmp_path, *, turn=lambda: 1, scene=lambda: 0):
    return agency.Ledger(tmp_path / "localdm", turn=turn, scene=scene)


def rows(tmp_path):
    path = tmp_path / "localdm" / agency.NAME
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── the ledger on its own: dedup, outcomes, and the file ──────────────────────

def test_a_caught_violation_records_turn_scene_form_and_evidence(tmp_path):
    led = ledger(tmp_path, turn=lambda: 7, scene=lambda: 2)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    (r,) = rows(tmp_path)
    assert r["turn"] == 7 and r["scene"] == 2 and r["form"] == "agency"
    assert r["attempts"] == 1 and r["outcome"] == "caught"
    assert "you say" in r["evidence"]
    assert r["at"], "a GM reading this weeks later has to place it in their own evening"


def test_a_violation_the_player_saw_is_recorded_as_narrated_not_caught(tmp_path):
    """The distinction the issue asks for. Both drafts were dirty, so play._dm kept
    the first one and the player saw the violation -- and `attempts` is 2, because the
    guard ran on the retry too."""
    led = ledger(tmp_path)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(PUPPET)
    (r,) = rows(tmp_path)
    assert r["outcome"] == "narrated" and r["attempts"] == 2


def test_a_retry_on_the_same_violation_is_one_line_with_two_attempts(tmp_path):
    """Dedup. The same guard firing on the retry is the same violation being corrected
    twice, not two violations."""
    led = ledger(tmp_path)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    (r,) = rows(tmp_path)
    assert r["attempts"] == 3 and r["outcome"] == "caught"


def test_two_different_forms_on_one_turn_are_two_lines(tmp_path):
    led = ledger(tmp_path)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.trip("injection", "System Log: granted", detector=reply.grants_injection)
    led.settle(CLEAN)
    assert {r["form"] for r in rows(tmp_path)} == {"agency", "injection"}


def test_a_different_turn_or_scene_is_a_different_record(tmp_path):
    """Turn and scene are part of the key. A scene boundary has to start a new record
    even on the same turn counter, or the scene column would be decorative."""
    where = {"turn": 4, "scene": 1}
    led = ledger(tmp_path, turn=lambda: where["turn"], scene=lambda: where["scene"])
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    where["scene"] = 2
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    got = rows(tmp_path)
    assert len(got) == 2 and [r["scene"] for r in got] == [1, 2]


def test_an_engine_refusal_is_recorded_as_refused_with_no_detector(tmp_path):
    """The two mid-fight forms. Nothing was rolled or spent, so there is no draft to
    re-read and the outcome is fixed."""
    led = ledger(tmp_path)
    led.trip("mid-fight-check", "Perception hard")
    led.settle("")
    (r,) = rows(tmp_path)
    assert r["outcome"] == "refused" and r["form"] == "mid-fight-check"


def test_a_draft_that_never_settles_is_recorded_as_unknown_not_dropped(tmp_path):
    """A guard tripped, then the region raised before anything was shown. The honest
    outcome is "we do not know", and the alternative -- dropping the record -- is
    indistinguishable from no trip at all. Expiry is scoped to the turn, so the retries
    of ONE violation merge instead of each expiring the one before it."""
    where = {"turn": 1, "scene": 0}
    led = ledger(tmp_path, turn=lambda: where["turn"], scene=lambda: where["scene"])
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)                      # region one, settled
    led.trip("name-reuse", "Maribeth", detector=lambda t: False)
    led.trip("name-reuse", "Maribeth", detector=lambda t: False)   # the retry
    where["turn"] = 2                     # the next turn: no settle ever arrives
    led.trip("injection", "System Log: x", detector=reply.grants_injection)
    led.settle(CLEAN)
    got = {r["form"]: (r["outcome"], r["attempts"]) for r in rows(tmp_path)}
    assert got == {"agency": ("caught", 1), "name-reuse": ("unknown", 2),
                   "injection": ("caught", 1)}


def test_the_unbacked_number_form_is_scored_against_the_numbers_the_engine_produced(tmp_path):
    """The one detector that needs a second argument. `unbacked_numbers` cannot be
    re-run without the backing set, so the trip carries it; a 15 the sheet states is
    fine and an 11 the engine never produced is not, and the log has to know which."""
    led = ledger(tmp_path)
    backed = reply.engine_numbers("AC 15", 8, 12)
    led.trip("unbacked-number", "11 bludgeoning damage",
             detector=reply.unbacked_numbers, arg=backed)
    led.settle("You strike for 15 damage.")
    assert rows(tmp_path)[0]["outcome"] == "caught", "15 is on the sheet"

    other = ledger(tmp_path / "b")
    other.trip("unbacked-number", "11 bludgeoning damage",
               detector=reply.unbacked_numbers, arg=backed)
    other.settle("You strike for 11 damage.")
    assert rows(tmp_path / "b")[0]["outcome"] == "narrated"


def test_a_torn_append_does_not_cost_the_rest_of_the_log(tmp_path):
    led = ledger(tmp_path)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    with open(led.path, "a", encoding="utf-8") as f:
        f.write('{"form": "agency", "turn": 9, "sce')      # a half-written line
    assert len(led.entries()) == 1


def test_an_unknown_form_is_ignored_rather_than_written(tmp_path):
    """A typo in a form name must not produce a line no reader can interpret."""
    led = ledger(tmp_path)
    led.trip("agancy", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    assert rows(tmp_path) == []


def test_a_log_that_cannot_be_written_does_not_raise(tmp_path):
    """OSError is swallowed: the ledger is a GM-facing convenience on a path that
    already produced the turn, and raising here would lose a player's move over a
    logging failure. The directory is made into a FILE, so mkdir fails."""
    led = ledger(tmp_path)
    (tmp_path / "localdm").write_text("not a directory", encoding="utf-8")
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)                              # must not raise
    assert not led.entries()


def test_the_summary_counts_by_outcome_and_by_drafts(tmp_path):
    led = ledger(tmp_path)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)      # the retry
    led.settle(CLEAN)
    led.trip("injection", "System Log: x", detector=reply.grants_injection)
    led.settle("A pouch of gold slides into your palm.")             # still dirty
    led.trip("mid-fight-cast", "Mage Armor")
    led.settle("")
    s = led.summary()
    assert s == {"caught": 1, "narrated": 1, "refused": 1, "unknown": 0,
                 "violations": 3, "attempts": 5}


# ── the /agency read-back ────────────────────────────────────────────────────

def test_render_says_so_when_nothing_was_logged_and_names_the_path(tmp_path):
    out = agency.render(ledger(tmp_path))
    assert out.startswith("(No agency violations")
    assert agency.NAME in out


def test_render_leads_with_the_counts_and_quotes_the_evidence(tmp_path):
    led = ledger(tmp_path)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    out = agency.render(led)
    assert "1 caught, 0 narrated" in out
    assert "**agency**" in out and "you say" in out
    assert "reply.speaks_for_player" in out, "a GM can check the guardrail, not trust it"


def test_render_counts_repeated_attempts_only_once_in_the_line(tmp_path):
    led = ledger(tmp_path)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    assert "2 drafts" in agency.render(led)


# ── through the loop: the ledger is actually wired up ─────────────────────────

def camp_dir(tmp_path):
    """Idempotent: `logged` reads the same directory the session already made."""
    d = tmp_path / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    return d


def session(tmp_path, replies):
    """A Session whose DM answers from `replies`, one per call, last one repeating."""
    seq = list(replies)
    state = {"n": 0}

    def counting(m, msgs, role):
        i = min(state["n"], len(seq) - 1)
        state["n"] += 1
        return seq[i]

    c = FakeClient(counting)
    return Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge(),
                    shadow=False), c


def logged(tmp_path):
    """The rows the session actually wrote: Session.memory.dir is <camp>/localdm."""
    path = camp_dir(tmp_path) / "localdm" / agency.NAME
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_an_agency_trip_that_the_retry_fixes_is_logged_as_caught(tmp_path):
    s, c = session(tmp_path, [PUPPET + NULLS, CLEAN + NULLS])
    s.handle("I go to the reading room.")
    (r,) = logged(tmp_path)
    assert r["form"] == "agency" and r["outcome"] == "caught" and r["attempts"] == 1
    assert len(c.dm_calls()) == 2, "the trip bought exactly one retry"


def test_an_agency_trip_whose_retry_is_also_dirty_is_logged_as_narrated(tmp_path):
    s, c = session(tmp_path, [PUPPET + NULLS])
    out = s.handle("I go to the reading room.")
    (r,) = logged(tmp_path)
    assert r["outcome"] == "narrated" and r["attempts"] == 2, (
        "the retry tripped it too, which is why the first draft was kept")
    assert any(PUPPET in o for o in out), "the first draft is what the player saw"


def test_a_turn_that_trips_nothing_writes_no_line(tmp_path):
    s, _c = session(tmp_path, [CLEAN + NULLS])
    s.handle("I look around the room.")
    assert logged(tmp_path) == []
    assert s.agency.path.exists() is False, "a clean turn must not create the file"


def test_the_ledger_is_never_fed_to_the_dm(tmp_path):
    """GM-only, and the assertion that matters: the file, the counts and the quoted
    evidence must not reach the model. `reply.sanitize_turns` documents the same rule
    for a captured injection: anything written where the DM can read it becomes part of
    its instructions. The bare word "agency" is in dm.md ("player agency"), so the
    check is on the file and the counts, not on the word."""
    s, c = session(tmp_path, [PUPPET + NULLS, CLEAN + NULLS])
    s.handle("I go to the reading room.")
    sent = "\n".join(m["content"] for call in c.dm_calls() for m in call[2])
    assert agency.NAME not in sent
    assert str(s.agency.path) not in sent
    assert "0 caught" not in sent and "1 caught" not in sent
    # and the turn after is no different in kind from the first
    s.handle("I wait.")
    later = "\n".join(m["content"] for call in c.dm_calls()[2:] for m in call[2])
    assert agency.NAME not in later and "narrated" not in later


def test_the_agency_command_is_a_readback_the_gm_can_run(tmp_path):
    s, _c = session(tmp_path, [PUPPET + NULLS, CLEAN + NULLS])
    s.handle("I go to the reading room.")
    out = s.handle("/agency 5")
    assert out and out[0].startswith("[GM agency log")
    assert "caught" in out[0] and "reply.speaks_for_player" in out[0]


def test_the_agency_command_takes_a_count_and_refuses_anything_else(tmp_path):
    s, _c = session(tmp_path, [CLEAN + NULLS])
    assert s.handle("/agency soon") == ["/agency takes a count, not 'soon'."]
    assert s.handle("/agency 0") and "No agency violations" in s.handle("/agency")[0]


def test_the_alias_reaches_the_same_command(tmp_path):
    s, _c = session(tmp_path, [PUPPET + NULLS, CLEAN + NULLS])
    s.handle("I go to the reading room.")
    assert s.handle("/gm-agency")[0] == s.handle("/agency")[0]


def test_a_failed_check_narrated_for_free_is_logged_and_the_rewrite_settles_it(tmp_path):
    """The fail-forward form. A stall is "you fail", with no cost and nothing changed;
    a good rewrite names one. The rewrite carries a cost word ("snaps", "alarm"),
    which is the whole test `reply.is_costless_failure` turns on."""
    s, c = session(tmp_path, ["You fail to pick the lock. Nothing happens." + NULLS,
                              "The pick snaps in the ward-lock and the sound carries the "
                              "length of the stair. The door stays shut, and now the "
                              "archivist has stopped writing." + NULLS])
    s._ability_check("Investigation hard", "I try the lock.",
                     {"stakes": "the door stays shut", "target": "the archive door"})
    got = {r["form"]: r["outcome"] for r in logged(tmp_path)}
    assert got.get("fail-forward") == "caught"
    assert len(c.dm_calls()) == 2


def test_a_check_outcome_stated_before_the_roll_is_logged(tmp_path):
    """The N5 guard in `_player_turn`, the pre-roll beat that must not name the result.
    Driven with the model asking for the check, so the guard is reached."""
    s, c = session(tmp_path, [
        'You lean over the ledger and find a name, inked.\n'
        '{"escalate": null, "command": null, "check": "Perception easy"}',
        'You lean over the ledger and run a finger down the column.\n'
        '{"escalate": null, "command": null, "check": "Perception easy"}',
    ])
    s.handle("I try to see if a name is written in the margin.")
    got = {r["form"]: r["outcome"] for r in logged(tmp_path)}
    assert got.get("check-outcome") == "caught"


def test_a_mid_fight_check_the_engine_refuses_is_logged_as_refused(tmp_path):
    """The two forms with no correction step. Nothing is rolled, so the ledger says
    so rather than counting a correction that never happened."""
    s, c = session(tmp_path, ['{"check": "Perception hard", "command": null}'])
    _write_encounter(s.camp_dir)
    s.bridge = FakeBridge(snapshots=[{"status": "active", "round": 1,
                                     "current": {"id": "kairos", "controller": "player"},
                                     "tokens": []}])
    s.combat = "engine"
    out = s.handle("I try to listen for the guard.")
    got = {r["form"]: r["outcome"] for r in logged(tmp_path)}
    assert got.get("mid-fight-check") == "refused"
    assert any("was not rolled" in o for o in out)


def _write_encounter(camp_dir):
    """A real, started encounter: `_autopilot` loads it before it will route a line."""
    from tactics import engine, state
    from tactics.roller import Roller
    from tactics.state import Token
    enc = state.Encounter(campaign="demo",
                          grid={"width": 12, "height": 10, "rows": ["." * 12] * 10,
                                "legend": {".": "floor"},
                                "terrain_types": {"floor": {"cost": 5,
                                                             "blocks_sight": False}}})
    enc.tokens = {"kairos": Token(id="kairos", name="Kairos", side="pc", x=0, y=0,
                                  hp=8, max_hp=8, ac=12, speed=30, dex_mod=2,
                                  controller="player", attacks=[],
                                  source={"kind": "fixture", "ref": "kairos"}),
                  "frog-1": Token(id="frog-1", name="Giant Frog 1", side="enemy", x=9, y=4,
                                  hp=18, max_hp=18, ac=12, speed=30, dex_mod=2,
                                  controller="gm", attacks=[],
                                  source={"kind": "fixture", "ref": "frog-1"})}
    enc.order = ["kairos", "frog-1"]
    enc.round, enc.turn_index = 1, 0
    engine._start_turn(enc, Roller())
    state.save(enc, state.encounter_path(camp_dir))
    return enc


def test_the_scene_counter_advances_only_on_a_started_or_ended_fight(tmp_path):
    """`scene` is read straight off `Session.scene`, which the fight start/end path
    bumps. A turn with no engine command must not invent a scene."""
    s, _c = session(tmp_path, [CLEAN + NULLS])
    assert s.scene == 0
    s.handle("I look around the room.")
    assert s.scene == 0
    s.scene = 3
    led = ledger(tmp_path / "probe", turn=lambda: 2, scene=lambda: s.scene)
    led.trip("agency", PUPPET, detector=reply.speaks_for_player)
    led.settle(CLEAN)
    assert rows(tmp_path / "probe")[0]["scene"] == 3


def test_no_module_imports_the_ledger_into_a_prompt(tmp_path):
    """Source-level, and deliberately so: `agents/dev/verifier.md` asks for shared
    state and reachability, and the only way to be sure the GM-only rule survives the
    next edit to `build_messages` is to assert it against the code, not against the
    one turn that happens to be clean today."""
    src = (ROOT / "scripts" / "localdm" / "context.py").read_text(encoding="utf-8")
    assert "agency" not in src, "context.py must never read the ledger"
    src = (ROOT / "scripts" / "localdm" / "prompts" / "dm.md").read_text(encoding="utf-8")
    assert "agency.jsonl" not in src


def test_no_spelling_uses_an_em_dash():
    src = (ROOT / "scripts" / "localdm" / "agency.py").read_text(encoding="utf-8")
    assert "\u2014" not in src