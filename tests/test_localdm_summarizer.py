"""Milestone 6: folding old turns into the rolling summary, off the main thread."""
from __future__ import annotations

import yaml

from tests.localdm_fakes import FakeClient
from localdm import llm
from localdm.memory import Memory
from localdm.summarizer import Summarizer


def filled(tmp_path, n):
    m = Memory(tmp_path)
    for i in range(n):
        m.add("player" if i % 2 == 0 else "dm", f"turn {i}")
    return m


def _valid_handoff(text: str) -> dict:
    """Parse and validate a handoff YAML block."""
    # Extract YAML from the summary (may be the whole text or in a fence)
    m = re.search(r"```ya?ml\s*\n(.*?)\n```", text, re.S)
    if m:
        yaml_text = m.group(1)
    else:
        yaml_text = text
    data = yaml.safe_load(yaml_text)
    assert isinstance(data, dict), "handoff must be a YAML mapping"
    # Required fields in fixed order
    required = ["written_at", "written_because", "pacing_used", "scenes_completed",
                "scenes_remaining", "where_we_are", "in_flight", "party_state",
                "open_threads", "world_moved", "next_session_opens_on"]
    for field in required:
        assert field in data, f"missing required field: {field}"
    assert data["written_because"] in ("session_end", "context_full", "beat_landed")
    assert isinstance(data["in_flight"], list)
    assert isinstance(data["scenes_completed"], list)
    assert isinstance(data["scenes_remaining"], list)
    assert isinstance(data["open_threads"], list)
    assert isinstance(data["world_moved"], list)
    return data


import re


def test_due_only_after_keep_plus_batch_turns(tmp_path):
    c = FakeClient(lambda *a: "S")
    assert not Summarizer(c, "dm-local", filled(tmp_path / "a", 13)).due()
    assert Summarizer(c, "dm-local", filled(tmp_path / "b", 14)).due()


def test_fold_summarizes_all_but_the_last_keep_turns(tmp_path):
    m = filled(tmp_path, 14)
    m.set_summary("Earlier: a storm.", 0)
    # FakeClient returns a valid handoff YAML
    handoff_yaml = """written_at: "session 1, 01 January"
written_because: context_full
pacing_used: brisk
scenes_completed: ["s1"]
scenes_remaining: ["s2"]
where_we_are: "The party crossed the pond."
in_flight:
  - "Promise to find the lost child"
party_state: "Party at Frog Pond, all healthy"
open_threads: ["Who burned the ledger"]
world_moved: ["Salt Guild advances"]
next_session_opens_on: "The guildhall at dawn"
"""
    c = FakeClient(lambda *a: handoff_yaml)
    assert Summarizer(c, "dm-local", m).fold("context_full")
    summary = m.summary()
    data = _valid_handoff(summary)
    assert data["written_because"] == "context_full"
    assert data["where_we_are"] == "The party crossed the pond."
    assert m.summarized() == 8
    model, role, messages = c.calls[0]
    user = messages[1]["content"]
    assert (model, role) == ("dm-local", "summary")
    assert "Trigger: context_full" in user
    assert "Earlier: a storm." in user


def test_fold_writes_handoff_at_session_end_trigger(tmp_path):
    m = filled(tmp_path, 10)
    m.set_summary("Previous summary.", 5)
    handoff_yaml = """written_at: "session 1, 01 January"
written_because: session_end
pacing_used: brisk
scenes_completed: ["s1"]
scenes_remaining: []
where_we_are: "Session ended at the tavern."
in_flight:
  - "Promise to return the book"
party_state: "Party rested at the inn"
open_threads: []
world_moved: []
next_session_opens_on: "The tavern morning"
"""
    c = FakeClient(lambda *a: handoff_yaml)
    assert Summarizer(c, "dm-local", m).write_handoff("session_end")
    summary = m.summary()
    data = _valid_handoff(summary)
    assert data["written_because"] == "session_end"
    # summarized cursor should NOT advance for session_end
    assert m.summarized() == 5


def test_fold_writes_handoff_at_beat_landed_trigger(tmp_path):
    m = filled(tmp_path, 10)
    m.set_summary("Previous summary.", 5)
    handoff_yaml = """written_at: "session 1, 01 January"
written_because: beat_landed
pacing_used: brisk
scenes_completed: ["s1"]
scenes_remaining: ["s2"]
where_we_are: "Beat 1a completed: the inciting incident."
in_flight:
  - "The broker is now expendable"
party_state: "Party at the guildhall"
open_threads: ["Who burned the ledger"]
world_moved: ["Salt Guild 4/6 - ledger fire reported"]
next_session_opens_on: "The guildhall, before the tide turns"
"""
    c = FakeClient(lambda *a: handoff_yaml)
    assert Summarizer(c, "dm-local", m).write_handoff("beat_landed")
    summary = m.summary()
    data = _valid_handoff(summary)
    assert data["written_because"] == "beat_landed"
    # summarized cursor should NOT advance for beat_landed
    assert m.summarized() == 5


def test_maybe_start_runs_in_the_background_once(tmp_path):
    m = filled(tmp_path, 14)
    handoff_yaml = """written_at: "session 1, 01 January"
written_because: context_full
pacing_used: brisk
scenes_completed: ["s1"]
scenes_remaining: ["s2"]
where_we_are: "Background fold."
in_flight: []
party_state: "Party at Frog Pond"
open_threads: []
world_moved: []
next_session_opens_on: "Frog Pond morning"
"""
    s = Summarizer(FakeClient(lambda *a: handoff_yaml), "dm-local", m)
    assert s.maybe_start() is not None
    s.join(5)
    assert m.summarized() == 8 and s.maybe_start() is None
    data = _valid_handoff(m.summary())
    assert data["written_because"] == "context_full"


def test_a_model_error_leaves_the_summary_alone(tmp_path):
    m = filled(tmp_path, 20)  # 20 turns, summarized=5 -> 15 unsummarized >= 14 (due)
    m.set_summary("Good summary.", 5)

    def boom(*a):
        raise llm.LLMError("offline")

    s = Summarizer(FakeClient(boom), "dm-local", m)
    s.maybe_start()
    s.join(5)
    assert m.summarized() == 5 and "offline" in s.last_error
    assert m.summary() == "Good summary."


def test_write_handoff_without_new_turns(tmp_path):
    """write_handoff should work even when there are no new turns."""
    m = filled(tmp_path, 5)
    m.set_summary("Existing summary.", 5)
    handoff_yaml = """written_at: "session 1, 01 January"
written_because: session_end
pacing_used: brisk
scenes_completed: ["s1"]
scenes_remaining: []
where_we_are: "No new turns this session."
in_flight: []
party_state: "Party at camp"
open_threads: []
world_moved: []
next_session_opens_on: "Camp morning"
"""
    c = FakeClient(lambda *a: handoff_yaml)
    s = Summarizer(c, "dm-local", m)
    assert s.write_handoff("session_end")
    data = _valid_handoff(m.summary())
    assert data["written_because"] == "session_end"
    assert m.summarized() == 5  # cursor unchanged