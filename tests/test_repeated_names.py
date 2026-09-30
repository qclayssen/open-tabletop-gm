"""The "Marcus" bug: one repeated name, and then every person in the scene has it.

Correcting a repeated name made the model name *everyone* that, and the defect
worsened with engagement: the more turns mentioned the name, the more often it
came back, because each repetition is another mention the recent window, the
summary and canon all carry forward. Nothing in the prompt wins against that, so
the ledger that catches it is in code, on the reply path.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import reply                                # noqa: E402
from localdm.play import Session                         # noqa: E402
from localdm import llm                                  # noqa: E402
from tests.localdm_fakes import FakeBridge, FakeClient    # noqa: E402
from tests.test_localdm_play import camp_dir              # noqa: E402

NULLS = '\n{"escalate": null, "command": null}'
MODELS = llm.Models("dm-local", "dm-advisor", "dm-council")

# The escalation, as it reads in a real reply: one name on every person in the
# room, and the "guard" and "innkeeper" are the tell, not the count.
ESCALATED = ("Marcus the guard blocks the stair. Marcus the innkeeper slides a key "
             "across the bar. Marcus the porter keeps his eyes down. The lamp gutters.")
# The same defect with no second name to compare it to.
ESCALATED_BARE = ("Marcus watches the door. Marcus checks the street. Marcus counts "
                  "the coins. Maribeth says nothing.")
# One name, two people, two mentions: the smallest shape of the bug.
SPLIT_NAME = ("Marcus the guard blocks the stair. Marcus the innkeeper slides a key "
              "across. Maribeth watches from the step.")


def user_text(call) -> str:
    return "\n".join(m["content"] for m in call[2] if m["role"] == "user")


# ─── the detector ──────────────────────────────────────────────────────────

def test_one_name_on_three_people_is_the_bug():
    assert reply.NameLedger().suspect(ESCALATED) == "Marcus"


def test_the_bug_is_caught_without_a_second_name_to_compare_it_to():
    assert reply.NameLedger().suspect(ESCALATED_BARE) == "Marcus"


def test_one_name_on_two_people_is_enough():
    assert reply.NameLedger().suspect(SPLIT_NAME) == "Marcus"


def test_ordinary_prose_names_nobody_too_often():
    ledger = reply.NameLedger()
    for text in (
        "The frog lunges and Kairos's blade turns it aside.",
        "Rain falls on the terrace as students cross toward the tower.",
        "Mage Armor settles over your shoulders. Two level 1 slots spent, one left.",
        "The archivist runs a finger down the ledger without looking up.",
        "A giant frog croaks and hops back two squares.",
        "You step deeper into the shadowed aisles, lamp in hand.",
    ):
        assert ledger.suspect(text) == "", text


def test_a_repeated_common_noun_is_not_a_name():
    """The other half of the same over-application: a storm that is very
    repetitive, and not a person. Weather nouns go at the head of sentences
    constantly, so they are excluded by name rather than by shape."""
    assert reply.NameLedger().suspect(
        "Thunder rolls over the tower. Thunder shakes the shutters. "
        "Thunder fades toward the river. Maribeth does not look up."
    ) == ""


def test_a_reply_about_one_character_says_their_name_repeatedly():
    """Three sentences about one person is a reply about one person, not a bug."""
    assert reply.NameLedger().suspect(
        "Maribeth does not look up. Maribeth turns a page. Maribeth answers at last."
    ) == ""


# ─── the false positive the rule must not cost ─────────────────────────────

def test_a_pc_named_in_almost_every_turn_is_never_throttled():
    """The recurring-character case the exemption exists for.

    A PC is named in most turns by design, and the sheet digest vouches for the
    name, so the ledger never even looks at it.
    """
    ledger = reply.NameLedger()
    ledger.establish("### Player character: Kairos\nA Level 1 wizard.\n"
                     "### NPCs\n- Maribeth, the archivist")
    for text in ("Kairos steps into the rain. Maribeth watches from the stair.",
                 "Kairos says nothing. Kairos waits. Maribeth turns a page.",
                 "Kairos reads the note. Kairos folds it. The porter nods.",
                 "Kairos answers. Maribeth does not believe him. Kairos waits.",
                 "Kairos leaves. Kairos does not look back. Wren stares at the door."):
        assert ledger.suspect(text) == "", text
        ledger.observe(text)


def test_an_established_npc_may_be_named_three_times_in_one_reply():
    ledger = reply.NameLedger()
    ledger.establish("### NPCs\n- Maribeth, the archivist, keeps the moonstone")
    assert ledger.suspect("Maribeth sets down the stone. Maribeth locks the case. "
                          "Maribeth will not meet your eye. The porter waits.") == ""


def test_a_long_conversation_with_one_npc_is_not_an_escalation():
    """Sharing the room is not the defect; being several people is.

    Ten turns of Halda, with a passer-by in two of them, is a scene. The scene
    rule has to leave it alone, or it fires on every long conversation.
    """
    ledger = reply.NameLedger()
    for text in ("Halda sets down her quill. The porter passes the window.",
                 "Halda reads a line aloud. A student hurries past.",
                 "Halda turns a page. The lamp gutters.",
                 "Halda answers at last. The porter does not look up.",
                 "Halda shuts the ledger. A student coughs by the door."):
        assert ledger.suspect(text) == "", text
        ledger.observe(text)


# ─── the defect worsens with engagement, so the scene is measured ───────────

def test_the_slow_escalation_is_caught_after_a_few_turns_of_it():
    """The shape that never trips a per-reply rule: one mention a turn, more and
    more people wearing it, and it spreads the longer the scene runs."""
    ledger = reply.NameLedger()
    for text in ("The porter sets down a tray. Halda the archivist does not look up.",
                 "Marcus the porter sets down a tray. Halda does not look up.",
                 "Marcus the archivist does not look up. Wren the student hurries by."):
        ledger.observe(text)
    assert ledger.suspect("Marcus waves Halda off. Wren stares at the door.") == "Marcus"


def test_a_new_scene_clears_the_ledger_but_keeps_the_campaigns_names():
    ledger = reply.NameLedger()
    ledger.establish("### Player character: Kairos")
    for text in ("The porter sets down a tray. Halda the archivist does not look up.",
                 "Marcus the porter sets down a tray. Halda does not look up.",
                 "Marcus the archivist does not look up. Wren the student hurries by."):
        ledger.observe(text)
    assert ledger.suspect("Marcus waves Halda off. Wren stares at the door.") == "Marcus"
    ledger.begin()
    assert ledger.suspect("Marcus waves Halda off. Wren stares at the door.") == ""
    assert "Kairos" in ledger.established


# ─── the guarantee: one corrective retry, in session ───────────────────────

def test_an_over_applied_name_is_rewritten_once(tmp_path):
    clean = ("The guard blocks the stair. The innkeeper slides a key across. "
             "The porter keeps his eyes down. The lamp gutters.")
    replies = iter([ESCALATED + NULLS, clean + NULLS])
    c = FakeClient(lambda m, msgs, role: next(replies))
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    out = s._dm(player="I ask the guard about the moonstone.")
    assert len(c.dm_calls()) == 2, "one corrective retry, not a loop"
    assert out.narration == clean
    # the rewrite was told which name, and what to do with the rest of the cast
    sent = user_text(c.dm_calls()[-1])
    assert "Marcus" in sent and "the guard" in sent
    # and the rejected draft was never observed, so the name does not compound
    assert ledger_casts(s) == set()


def ledger_casts(session) -> set:
    return {n for turn in session.names.turns for n in turn}


def test_a_failed_name_retry_keeps_the_original_draft(tmp_path):
    """Same bargain as every other guardrail: a flag means worth rewriting once."""
    c = FakeClient(lambda m, msgs, role: ESCALATED + NULLS)
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    out = s._dm(player="I ask the guard about the moonstone.")
    assert len(c.dm_calls()) == 2
    assert out.narration.startswith("Marcus the guard")


def test_the_name_trip_asks_no_advisor(tmp_path):
    """Detection is a count, not a judgement, so it must not cost a cloud call."""
    replies = iter([ESCALATED + NULLS, "The porter keeps his eyes down." + NULLS])
    c = FakeClient(lambda m, msgs, role: next(replies))
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    s._dm(player="I look around the room.")
    assert c.advisor_roles() == []


def test_a_clean_turn_costs_exactly_one_call(tmp_path):
    c = FakeClient(lambda m, msgs, role: "The guard shifts his weight by the door."
                   + NULLS)
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    s._dm(player="I wait.")
    assert len(c.dm_calls()) == 1


def test_a_pc_named_in_most_turns_costs_exactly_one_call(tmp_path):
    """The false positive, measured where it would cost the player: a scene in
    which the player character is named in nearly every reply is not rewritten,
    not once in five turns."""
    d = camp_dir(tmp_path, state="# Campaign: demo\n## Scene\n"
                 "A wizard walks the arcana. Kairos is the player character.\n")
    c = FakeClient(lambda m, msgs, role: "Kairos reads the note. Maribeth waits. "
                   "Kairos folds it. The porter nods." + NULLS)
    s = Session("demo", c, MODELS, camp_dir=d, bridge=FakeBridge())
    for _ in range(5):
        s._dm(player="I go on.")
    assert len(c.dm_calls()) == 5, "a recurring PC must not be throttled"


def test_a_fight_starting_is_a_new_scene(tmp_path):
    from localdm.bridge import Result
    bridge = FakeBridge(handlers={"start": lambda args: Result(0, "Fight started: 1 round.")})
    s = Session("demo", FakeClient(lambda m, msgs, role: "Rain." + NULLS), MODELS,
                camp_dir=camp_dir(tmp_path), bridge=bridge)
    s.names.observe("Marcus the porter sets down a tray. Halda does not look up.")
    s.names.observe("Marcus the archivist does not look up. Wren hurries by.")
    s._engine(["start", "rotd"])
    # the new scene's own reply is the only thing the ledger holds now
    assert s.names.cast() == set()
