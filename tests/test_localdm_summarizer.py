"""Milestone 6: folding old turns into the rolling summary, off the main thread."""
from __future__ import annotations

from tests.localdm_fakes import FakeClient
from localdm import llm
from localdm.memory import Memory
from localdm.summarizer import Summarizer


def filled(tmp_path, n):
    m = Memory(tmp_path)
    for i in range(n):
        m.add("player" if i % 2 == 0 else "dm", f"turn {i}")
    return m


def test_due_only_after_keep_plus_batch_turns(tmp_path):
    c = FakeClient(lambda *a: "S")
    assert not Summarizer(c, "dm-local", filled(tmp_path / "a", 13)).due()
    assert Summarizer(c, "dm-local", filled(tmp_path / "b", 14)).due()


def test_fold_summarizes_all_but_the_last_keep_turns(tmp_path):
    m = filled(tmp_path, 14)
    m.set_summary("Earlier: a storm.", 0)
    c = FakeClient(lambda *a: "<think></think>The party crossed the pond.")
    assert Summarizer(c, "dm-local", m).fold()
    assert m.summary() == "The party crossed the pond." and m.summarized() == 8
    model, role, messages = c.calls[0]
    user = messages[1]["content"]
    assert (model, role) == ("dm-local", "summary")
    assert "Earlier: a storm." in user and "Player: turn 0" in user and "turn 8" not in user


def test_maybe_start_runs_in_the_background_once(tmp_path):
    m = filled(tmp_path, 14)
    s = Summarizer(FakeClient(lambda *a: "Summary."), "dm-local", m)
    assert s.maybe_start() is not None
    s.join(5)
    assert m.summarized() == 8 and s.maybe_start() is None


def test_a_model_error_leaves_the_summary_alone(tmp_path):
    m = filled(tmp_path, 14)

    def boom(*a):
        raise llm.LLMError("offline")

    s = Summarizer(FakeClient(boom), "dm-local", m)
    s.maybe_start()
    s.join(5)
    assert m.summarized() == 0 and "offline" in s.last_error
