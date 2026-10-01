"""Applied Standard 16 — a failed check must change the world, not stall it.

WHY
===
Fail forward was measured but never instructed. `probe/narrative_probe.py` scored
DM output for dead stops ("you fail", "nothing happens", "the attempt fails"), and
the probe duly failed it — but no prompt in the chain ever told the DM to produce a
complication instead. The per-turn task handed to the model for every single check
made it worse, asking what the character "finds or fails to find", which is a dead
stop written as an instruction.

These are prompt contracts, not behaviour tests: the model is the thing under test
and it is not available here. What is testable is that the instruction exists, that
it is wired into the code path every check actually travels, and that the dead-stop
phrasing does not creep back in. The wiring half is a real test — CHECK_TASK is a
module constant that `_ability_check` feeds to the model, so asserting on what the
model was told is asserting on behaviour-determining input.

Run from repo root:
    python3 -m pytest tests/test_fail_forward.py -v
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import reply                      # noqa: E402
from localdm.play import CHECK_FAIL, CHECK_OK, Session  # noqa: E402

SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
DM_PROMPT = (ROOT / "scripts" / "localdm" / "prompts" / "dm.md").read_text(encoding="utf-8")
# The prompt is hard-wrapped for readability; compare on collapsed whitespace so a
# rewrap is not a test failure.
DM_PROMPT_FLAT = " ".join(DM_PROMPT.split())
STATE_TEMPLATE = (ROOT / "templates" / "state.md").read_text(encoding="utf-8")
DND5E = (ROOT / "systems" / "dnd5e" / "system.md").read_text(encoding="utf-8")

# The phrases the probe scores as a dead stop (see narrative_probe.fail_forward).
DEAD_STOP_PHRASES = ["you fail", "nothing happens", "the attempt fails",
                     "you don't succeed", "you do not succeed", "you are unable"]

NULLS = '\n{"escalate": null, "command": null}'


def _standard_16() -> str:
    """The body of Applied Standard 16, so assertions cannot match another section."""
    start = SKILL.index("### 16.")
    end = SKILL.index("\n## ", start)
    return SKILL[start:end]


# --- the doctrine is stated -------------------------------------------------

def test_the_doctrine_exists_as_applied_standard_16():
    assert "### 16. Fail Forward" in SKILL


def test_it_says_a_failure_moves_the_world():
    body = _standard_16().lower()
    assert "dead stop" in body
    assert "new situation" in body


def test_it_carries_the_door_scenario_the_design_started_from():
    # The motivating example is the regression fixture: if the door gets a natural 1,
    # the door opens anyway and the guards arrive. Both halves of that are asserted.
    body = _standard_16()
    assert "natural 1" in body.lower()
    assert "door" in body.lower() and "guard" in body.lower()


def test_it_forbids_the_dead_stop_phrasing():
    body = _standard_16().lower()
    for phrase in DEAD_STOP_PHRASES:
        assert phrase not in body, f"Standard 16 teaches a dead stop: {phrase!r}"


def test_it_is_not_a_soft_success():
    # The obvious way to overcorrect is to let failures quietly succeed. Standard 7
    # governs that, and Standard 16 must not be read as overriding it.
    body = _standard_16().lower()
    assert "not a soft success" in body


def test_it_still_stops_short_of_playing_the_pc():
    body = _standard_16().lower()
    assert "never resolve it for them" in body or "never decide" in body


# --- the instruction is wired into the path every check travels -------------

def test_the_runtime_prompt_carries_the_rule():
    assert "failed check must change the world" in DM_PROMPT


def test_the_runtime_prompt_forbids_the_dead_stops():
    # Only the two canonical dead stops are named in dm.md. The prompt is token-
    # sensitive (it ships to a small local model every turn), so it names the shape
    # of the failure rather than enumerating every phrase the probe happens to
    # score. The full list lives in reply.is_dead_stop, which is what enforces it.
    for phrase in ("you fail", "nothing happens"):
        assert phrase in DM_PROMPT, f"the runtime prompt should ban {phrase!r}"
    assert 'Never narrate "you fail", "nothing happens" or "try again"' in DM_PROMPT_FLAT


def test_the_failure_task_instructs_a_complication_instead():
    low = CHECK_FAIL.lower()
    assert "make the world move" in low
    assert "never say the attempt simply failed" in low
    assert "concrete cost" in low
    assert "do not decide what the character does" in low


def test_the_success_task_does_not_pay_for_the_failure_instructions():
    # The token win: a success is the common case and used to be handed the whole
    # fail-forward paragraph it would never use.
    low = CHECK_OK.lower()
    assert "nothing happens" not in low
    assert "concrete cost" not in low
    assert "make the world move" not in low
    assert len(CHECK_OK) < len(CHECK_FAIL)


def test_both_tasks_still_suppress_the_numbers():
    # Fail forward must not cost us the rule that keeps the DC out of the prose.
    for task in (CHECK_OK, CHECK_FAIL):
        low = task.lower()
        assert "do not mention the number" in low and "dc" in low
        assert "json line with null" in low


# --- the invariant is enforced in script, not asked for politely ------------

def test_the_dead_stop_detector_flags_a_stall():
    assert reply.is_dead_stop("You fail to open the door. The lock is jammed.")
    assert reply.is_dead_stop("You are still standing in the hallway. Nothing happens.")
    assert reply.is_dead_stop("The pick slips. Nothing happens. Try again.")


def test_the_detector_does_not_flag_the_door_scenario():
    # The whole point: the motivating narration opens the door and summons the
    # guards. It contains "you fail"-shaped language and must not be retried.
    text = ("You twist the lock too hard. CLICK-SNAP! The door flies open wide, but the "
            "crack echoes down the hall. Heavy boots are rounding the corner behind you.")
    assert not reply.is_dead_stop(text)


def test_the_detector_allows_legitimate_partial_failure():
    for text in (
        "You fail to pick the lock cleanly, but the door gives an inch.",
        "You are unable to work the tumbler, and the torchlight slides under the door.",
        "The attempt fails, and the noise brings a guard to the landing.",
    ):
        assert not reply.is_dead_stop(text), text


def test_a_stalled_failure_is_rewritten_once(monkeypatch, tmp_path):
    """The guarantee: a dead stop costs one corrective retry, not a bad turn."""
    from tests.test_localdm_play import camp_dir
    from tests.localdm_fakes import FakeBridge, FakeClient
    from localdm import llm

    models = llm.Models("dm-local", "dm-advisor", "dm-council")
    replies = iter(["You fail. The lock is jammed. Nothing happens.", "The pick snaps "
                    "cleanly off and the door crashes open; boots answer from the hall."])

    def responder(model, messages, role):
        return next(replies)

    c = FakeClient(responder)
    s = Session("demo", c, models, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    out = s._check_narration("Kairos rolled a Sleight of Hand check: 3 against DC 15: "
                             "failure.", ok=False)
    assert len(c.calls) == 2, "one corrective retry, not a loop"
    assert "boots answer" in out.narration
    assert "FAIL_FORWARD_FIX" not in out.narration


def test_a_good_failure_costs_exactly_one_call(tmp_path):
    from tests.test_localdm_play import camp_dir
    from tests.localdm_fakes import FakeBridge, FakeClient
    from localdm import llm

    models = llm.Models("dm-local", "dm-advisor", "dm-council")
    c = FakeClient(lambda m, msgs, role: "The pick snaps off and boots answer from the "
                                        "hall." + NULLS)
    s = Session("demo", c, models, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    out = s._check_narration("Kairos rolled a Sleight of Hand check: 3 against DC 15: "
                             "failure.", ok=False)
    assert len(c.calls) == 1
    assert "boots answer" in out.narration


def test_a_success_is_never_sent_through_the_retry(tmp_path):
    from tests.test_localdm_play import camp_dir
    from tests.localdm_fakes import FakeBridge, FakeClient
    from localdm import llm

    models = llm.Models("dm-local", "dm-advisor", "dm-council")
    c = FakeClient(lambda m, msgs, role: "Behind the crates, a torn sleeve." + NULLS)
    s = Session("demo", c, models, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    s._check_narration("Kairos rolled an Investigation check: 17 against DC 13: success.",
                       ok=True)
    assert len(c.calls) == 1


def test_the_check_task_reaches_the_model():
    """The contract above is worthless unless _ability_check actually sends it."""
    import inspect
    src = inspect.getsource(Session._ability_check)
    assert "_check_narration" in src


# --- the consequence survives compaction ------------------------------------

def test_the_state_template_has_a_slot_for_a_live_complication():
    assert "Live Complications" in STATE_TEMPLATE


def test_the_slot_says_to_clear_a_resolved_complication():
    tail = STATE_TEMPLATE.split("**Live Complications**", 1)[1]
    assert "clear" in tail.lower()


def test_the_dnd5e_module_points_nat_1_at_the_standard():
    assert "Applied Standard 16" in DND5E


def test_the_standard_names_the_state_slot_it_writes_to():
    # Doctrine that does not say where to record the consequence loses it at compaction.
    assert "Live Complications" in _standard_16()


# --- it is not just the probe's problem -------------------------------------

def test_the_probe_scores_exactly_what_the_runtime_retries_on():
    """One list, not two. A probe that drifts from the runtime passes on output the
    live DM would have thrown away, which is worse than having no probe."""
    probe = (ROOT / "probe" / "narrative_probe.py").read_text(encoding="utf-8")
    assert "from localdm.reply import is_dead_stop" in probe
    assert "dead_stops = [" not in probe, "the probe kept its own copy of the list"


# --- a failed check must also COST something (roadmap T2) --------------------
#
# is_dead_stop only asks "did the world move". A failure can move the world for
# free: the lock gives, the door opens, nobody minds, and the character learns the
# attempt is risk-free. The guard below asks the second question dm.md:49-52 puts
# in writing ("cost something concrete"), and the engine hands the model the stakes
# the check itself named plus how badly the roll missed.

FREE_RIDE = ("The lock clicks halfway and the bolt slides back. The door stays shut, "
             "but the way is clear.")
PAID = "The pick snaps off in the lock and the crack echoes down the hall."


def test_a_failure_that_changes_the_world_for_free_is_flagged_as_costless():
    assert not reply.is_dead_stop(FREE_RIDE)             # it moved, so the old guard passes it
    assert reply.is_costless_failure(FREE_RIDE)


def test_a_failure_with_a_named_cost_is_not_costless():
    for text in (
        PAID,
        "The pick slips and a guard turns at the noise.",
        "You get it open, but it takes an hour and the torch is burned down to a stub.",
        "The latch gives, and the scrape of metal alerts the patrol.",
        "The lock yields at the price of your lockpicks, bent beyond use.",
    ):
        assert not reply.is_costless_failure(text), text


def test_a_dead_stop_is_costless_too():
    assert reply.is_costless_failure("Nothing happens.")
    assert reply.is_costless_failure("")


def _session(tmp_path, replies):
    from tests.test_localdm_play import camp_dir
    from tests.localdm_fakes import FakeBridge, FakeClient
    from localdm import llm

    models = llm.Models("dm-local", "dm-advisor", "dm-council")
    it = iter(replies)
    c = FakeClient(lambda m, msgs, role: next(it))
    return Session("demo", c, models, camp_dir=camp_dir(tmp_path), bridge=FakeBridge()), c


FAIL = "Kairos rolled a Sleight of Hand check: 3 against DC 15: failure."


def test_a_free_failure_is_rewritten_once_with_the_stakes_named(tmp_path):
    s, c = _session(tmp_path, [FREE_RIDE + NULLS, PAID + NULLS])
    out = s._check_narration(FAIL, ok=False, margin=-12, stakes="the ferryman wakes")
    assert len(c.calls) == 2, "one corrective retry, not a loop"
    assert "snaps off" in out.narration
    sent = str(c.calls[1])
    assert "the ferryman wakes" in sent                     # the stake the check named
    assert "missed badly" in sent                        # the magnitude, from the margin


def test_the_stakes_reach_even_the_first_failure_draft(tmp_path):
    s, c = _session(tmp_path, [PAID + NULLS])
    s._check_narration(FAIL, ok=False, margin=-4, stakes="the ferryman wakes")
    assert len(c.calls) == 1
    assert "the ferryman wakes" in str(c.calls[0])


def test_a_costly_failure_is_never_rewritten(tmp_path):
    s, c = _session(tmp_path, [PAID + NULLS])
    s._check_narration(FAIL, ok=False, margin=-4)
    assert len(c.calls) == 1


def test_if_the_rewrite_is_no_better_the_first_non_stall_draft_is_kept(tmp_path):
    s, c = _session(tmp_path, [FREE_RIDE + NULLS, "Nothing happens." + NULLS])
    out = s._check_narration(FAIL, ok=False, margin=-4)
    assert "bolt slides back" in out.narration           # never swapped for a dead stop


def test_a_success_ignores_stakes_and_cost(tmp_path):
    s, c = _session(tmp_path, [FREE_RIDE + NULLS])
    s._check_narration("Kairos rolled a check: 17 against DC 13: success.", ok=True,
                       margin=4, stakes="the ferryman wakes")
    assert len(c.calls) == 1
    assert "the ferryman wakes" not in str(c.calls[0])


def test_ability_check_passes_the_requests_stakes():
    import inspect
    assert "stakes=req.stakes" in inspect.getsource(Session._ability_check)
