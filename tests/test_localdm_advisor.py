"""Milestone 6: advisor briefs, routing and parallel consults."""
from __future__ import annotations

import threading

import pytest

from tests.localdm_fakes import FakeClient
from localdm import advisor, llm


def test_brief_joins_the_role_and_the_shared_rules():
    b = advisor.brief("historian")
    assert b.startswith("# Lore Historian") and "never decide" in b.lower()
    assert "150 words" in b
    with pytest.raises(ValueError):
        advisor.brief("bard")


def test_pick_routes_by_keyword_and_falls_back():
    assert advisor.pick("How should the lich fight the party?")[0] == "tactician"
    assert advisor.pick("What does the ancient cult legend say?")[0] == "historian"
    assert advisor.pick("hmm") == ["continuity", "director"]
    assert len(advisor.pick("fight enemy lore legend rule balance scene reveal earlier")) == 3


def test_parse_advise():
    assert advisor.parse_advise("historian who built the tower?") == (
        ["historian"], "who built the tower?", False)
    names, q, council = advisor.parse_advise("council the boss fight next session")
    assert council and q == "the boss fight next session" and "tactician" in names
    for bad in ("", "bard hello", "historian"):
        with pytest.raises(ValueError):
            advisor.parse_advise(bad)


def test_consult_asks_every_advisor_at_the_same_time():
    barrier = threading.Barrier(3, timeout=5)

    def responder(model, messages, role):
        barrier.wait()                         # breaks if the calls run one by one
        return f"<think>x</think>Advice from {role}."

    c = FakeClient(responder)
    out = advisor.consult(c, "dm-advisor", ["historian", "director", "tactician"],
                          "Who is the lich?", "CONTEXT")
    assert out.split("\n\n") == ["Historian: Advice from advisor:historian.",
                                 "Director: Advice from advisor:director.",
                                 "Tactician: Advice from advisor:tactician."]
    model, role, messages = c.calls[0]
    assert model == "dm-advisor" and "CONTEXT" in messages[1]["content"]
    assert "Who is the lich?" in messages[1]["content"]


def test_one_failing_advisor_does_not_sink_the_others():
    def responder(model, messages, role):
        if role == "advisor:director":
            raise llm.LLMError("HTTP 429")
        return "Fine."

    out = advisor.consult(FakeClient(responder), "m", ["historian", "director"], "q", "")
    assert "Historian: Fine." in out and "Director: (unavailable: HTTP 429)" in out


def test_consult_sends_reasoning_effort_so_a_thinking_model_answers():
    """A local thinking model left to think spends the whole budget and returns
    an empty note, which used to render as a bare "Arbiter: " -- indistinguishable
    from an advisor that had nothing to add. Measured on qwen3.5:4b: 400/400
    completion tokens on reasoning and content "" without this, 47 tokens and a
    real answer with reasoning_effort="none"."""
    c = FakeClient(lambda m, msg, role: "Fail forward; the desk was cleaned.")
    advisor.consult(c, "qwen3.5:4b", ["arbiter"], "q", "", reasoning="none")
    assert c.reasoning == [("advisor:arbiter", "none")]


def test_an_empty_advisor_answer_is_marked_unavailable_not_silent():
    """advisor.consult only used to mark LLMError. A model that returned an empty
    string produced "Arbiter: ", which reads in the transcript as a considered
    silence -- the GM is then told the advisors have been consulted."""
    c = FakeClient(lambda m, msg, role: "")
    out = advisor.consult(c, "m", ["arbiter", "historian"], "q", "")
    assert "Arbiter: (unavailable: arbiter returned an empty note" in out
    assert "Historian: (unavailable: historian returned an empty note" in out
    # And never a bare "Arbiter: " with nothing after it.
    assert "Arbiter: \n" not in out and "Arbiter: \n\n" not in out


def test_one_silent_advisor_does_not_hide_the_others():
    def responder(model, messages, role):
        return "" if role == "advisor:arbiter" else "Check DC 13, not 10."

    out = advisor.consult(FakeClient(responder), "m", ["arbiter", "director"], "q", "")
    assert "Arbiter: (unavailable:" in out
    assert "Director: Check DC 13, not 10." in out
