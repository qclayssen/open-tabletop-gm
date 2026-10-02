"""canon.jsonl: the verbatim layer. What was actually said, kept exactly."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm.canon import Canon, Extractor, parse, render, unquote, verify  # noqa: E402
from localdm.context import build_messages                                   # noqa: E402
from localdm.memory import Memory                                             # noqa: E402
from tests.localdm_fakes import FakeClient

NARRATION = ('Maribeth does not look up from the ledger. "Keep it under your tongue, '
             'or I\'ll sew it there," she says, and slides the toll-coin back. '
             'The stone in your pack is not a gift. It is a receipt.')


# ── verbatim is enforced, not requested ───────────────────────────────────────

def test_a_paraphrase_is_dropped_not_stored():
    out = verify(parse('[{"kind":"dialogue","speaker":"Maribeth",'
                       '"text":"She told him to keep quiet."}]'), NARRATION)
    assert out == [], "a reworded line must never reach canon"


def test_the_exact_words_survive_a_line_break():
    """A span that only differs by wrapping is still verbatim."""
    wrapped = "Keep it under your tongue,\nor I'll sew it there"
    payload = json.dumps([{"kind": "dialogue", "speaker": "Maribeth", "text": wrapped}])
    out = verify(parse(payload), NARRATION)
    assert len(out) == 1
    assert out[0]["text"] == "Keep it under your tongue, or I'll sew it there"


def test_verify_keeps_all_three_kinds_and_tags_reveals():
    out = verify(parse('[{"kind":"dialogue","speaker":"Maribeth","text":"It is a receipt."},'
                       '{"kind":"interaction","speaker":"Maribeth","text":"slides the toll-coin back"},'
                       '{"kind":"reveal","key":"Moonstone Nature","text":"It is a receipt."}]'),
                 NARRATION, turn=7)
    assert [r["kind"] for r in out] == ["dialogue", "interaction", "reveal"]
    assert out[0]["speaker"] == "Maribeth" and out[0]["turn"] == 7
    assert out[2]["key"] == "moonstone-nature", "keys slug so dedupe survives casing"


def test_verify_rejects_an_unknown_kind_and_an_invented_span():
    out = verify(parse('[{"kind":"lore","text":"The moonstone belongs to the Empress."},'
                       '{"kind":"reveal","text":"Maribeth is secretly the Empress."}]'), NARRATION)
    assert out == []


def test_a_reveal_without_a_key_still_gets_one():
    out = verify(parse('[{"kind":"reveal","text":"It is a receipt."}]'), NARRATION)
    assert out[0]["key"] == "it-is-a-receipt"


def test_death_requires_a_named_subject_and_verbatim_text():
    narration = "The guard dies at the gate."
    out = verify(parse('[{"kind":"death","speaker":"The guard",'
                       '"text":"The guard dies at the gate."}]'), narration, turn=9)
    assert out == [{"kind": "death", "speaker": "The guard",
                    "key": "the-guard", "dead": True,
                    "text": narration, "turn": 9}]
    assert verify(parse('[{"kind":"death","text":"The guard dies at the gate."}]'),
                  narration) == []
    assert verify(parse('[{"kind":"death","speaker":"The guard",'
                        '"text":"The guard falls."}]'), narration) == []


# ── tolerating what a small model actually sends back ─────────────────────────

def test_parse_accepts_a_fenced_or_wrapped_array():
    assert len(parse('```json\n[{"kind":"reveal","text":"a"}]\n```')) == 1
    assert len(parse('Sure! Here it is:\n[{"kind":"reveal","text":"a"}]\nHope that helps.')) == 1


def test_parse_accepts_one_object_per_line():
    got = parse('{"kind":"reveal","text":"a"}\n{"kind":"dialogue","text":"b"}')
    assert [d["kind"] for d in got] == ["reveal", "dialogue"]


def test_parse_of_junk_is_empty_not_an_exception():
    assert parse("I think the ring belonged to her mother, actually.") == []
    assert parse("") == []


def test_a_span_cannot_be_stolen_from_another_turn():
    """Each turn is verified against itself, so turn N's words cannot be
    recorded as turn N+1's verbatim quote."""
    out = verify(parse('[{"kind":"reveal","text":"It is a receipt."}]'),
                 "The rain keeps falling on the empty street.")
    assert out == []


# ── the store ─────────────────────────────────────────────────────────────────

def test_records_round_trip_and_survive_a_reload(tmp_path):
    c = Canon(tmp_path)
    c.add([{"kind": "dialogue", "speaker": "Maribeth", "text": "It is a receipt.", "turn": 3}])
    assert Canon(tmp_path).records() == [
        {"kind": "dialogue", "speaker": "Maribeth", "text": "It is a receipt.", "turn": 3}]


def test_a_reveal_is_never_recorded_twice_however_it_is_worded(tmp_path):
    """The failure this file exists to prevent: the same truth disclosed in
    two sessions, reworded the second time."""
    c = Canon(tmp_path)
    c.add([{"kind": "reveal", "key": "moonstone-nature", "text": "It is a receipt.", "turn": 1}])
    again = c.add([{"kind": "reveal", "key": "Moonstone Nature",
                    "text": "That rock is basically a receipt.", "turn": 90}])
    assert again == [] and len(c.records()) == 1


def test_death_is_persisted_as_an_explicit_dead_flag_and_deduped_by_subject(tmp_path):
    c = Canon(tmp_path)
    first = {"kind": "death", "speaker": "The Guard",
             "text": "The Guard dies at the gate.", "turn": 2}
    saved = c.add([first])
    assert saved == [{**first, "key": "the-guard", "dead": True}]
    again = c.add([{**first, "text": "The Guard is dead.", "turn": 12}])
    assert again == []
    assert Canon(tmp_path).records() == saved


def test_an_identical_repeated_line_costs_no_budget(tmp_path):
    c = Canon(tmp_path)
    rec = {"kind": "dialogue", "speaker": "Maribeth", "text": "Not again.", "turn": 1}
    assert c.add([rec]) and c.add([rec]) == []


def test_a_torn_append_does_not_lose_the_file(tmp_path):
    c = Canon(tmp_path)
    c.add([{"kind": "reveal", "text": "It is a receipt.", "turn": 1}])
    with open(tmp_path / "canon.jsonl", "a", encoding="utf-8") as f:
        f.write('{"kind": "reveal", "te')            # a half-written line
    assert [r["text"] for r in c.records()] == ["It is a receipt."]


def test_blank_and_wrongly_typed_records_are_refused(tmp_path):
    c = Canon(tmp_path)
    assert c.add([{"kind": "dialogue", "text": ""}, {"kind": "lore", "text": "x"}]) == []
    assert c.records() == []


# ── choosing what to replay ───────────────────────────────────────────────────

def test_relevance_ranks_the_scene_over_the_rest(tmp_path):
    c = Canon(tmp_path)
    c.add([{"kind": "dialogue", "speaker": "Maribeth", "text": "The moor is watching.", "turn": 90},
           {"kind": "dialogue", "speaker": "Bram", "text": "You speak with Maribeth at the gate.", "turn": 4}])
    top = c.relevant("I ask Maribeth about the toll at the gate", limit=1)
    assert top[0]["speaker"] == "Bram"


def test_relevance_breaks_a_tie_on_the_most_recent(tmp_path):
    c = Canon(tmp_path)
    c.add([{"kind": "dialogue", "speaker": "Maribeth", "text": "First.", "turn": 2},
           {"kind": "dialogue", "speaker": "Maribeth", "text": "Second.", "turn": 8}])
    assert c.relevant("Maribeth", limit=1)[0]["text"] == "Second."


def test_relevance_of_an_empty_canon_is_empty(tmp_path):
    assert Canon(tmp_path).relevant("anything") == []


# ── the replay block ──────────────────────────────────────────────────────────

def test_the_block_does_not_double_quote_a_line_that_arrived_quoted():
    assert unquote('"Keep it under your tongue."') == "Keep it under your tongue."
    assert unquote("“It is a receipt.”") == "It is a receipt."
    assert unquote("no quotes here") == "no quotes here"


def test_render_labels_each_kind_so_the_dm_knows_what_it_is():
    out = render([{"kind": "dialogue", "speaker": "Maribeth", "text": '"It is a receipt."'},
                  {"kind": "reveal", "text": "It is a receipt."},
                  {"kind": "interaction", "speaker": "Maribeth", "text": "counts the coin"},
                  {"kind": "death", "speaker": "Maribeth", "dead": True,
                   "text": "Maribeth died at the gate."}])
    assert "- Maribeth: It is a receipt." in out
    assert "- already revealed: It is a receipt." in out
    assert "said or done" in out
    assert "- DEAD, stays dead: Maribeth: Maribeth died at the gate." in out
    assert render([]) == ""


def test_build_messages_carries_the_canon_between_summary_and_turns():
    msgs = build_messages("sys", "", "We met a tollkeeper.", [{"role": "dm", "text": "She nods."}],
                          canon=[{"kind": "dialogue", "speaker": "Maribeth", "text": "Not again."},
                                 {"kind": "death", "speaker": "The Guard", "dead": True,
                                  "text": "The Guard died at the gate."}])
    user = msgs[1]["content"]
    assert user.index("Story so far") < user.index("Canon") < user.index("Recent turns")
    assert "Maribeth: Not again." in user
    assert "DEAD, stays dead: The Guard" in user


def test_canon_is_trimmed_before_the_recent_turns():
    """A verbatim line the player already heard is worth more than a stale
    turn that is about to be summarized away."""
    records = [{"kind": "dialogue", "speaker": "Maribeth", "text": f"line {i} {'x' * 80}"}
               for i in range(20)]
    recent = [{"role": "dm", "text": f"turn {i}"} for i in range(20)]
    user = build_messages("sys", "", "", recent, canon=records, budget=1200)[1]["content"]
    assert "Canon" in user, "some canon must survive"
    kept = user.count("Maribeth: line")
    assert 0 < kept < 20, "canon is trimmed record by record, not all at once"
    assert all(f"turn {i}" in user for i in range(20)), "no turn was lost to make room"


def test_the_best_matching_canon_survives_the_trim():
    """canon.relevant sorts best-first, so that is the order the records arrive
    in: the least relevant line is the one that gets dropped."""
    records = [{"kind": "reveal", "text": "The moor is watching the gate."}] \
        + [{"kind": "dialogue", "speaker": "Bram", "text": "irrelevant " + "x" * 80}] * 20
    user = build_messages("sys", "", "", [], canon=records, budget=400)[1]["content"]
    assert "The moor is watching the gate." in user


def test_no_canon_leaves_the_prompt_exactly_as_it_was():
    a = build_messages("sys", "dig", "sum", [{"role": "dm", "text": "x"}])
    b = build_messages("sys", "dig", "sum", [{"role": "dm", "text": "x"}], canon="")
    assert a == b


# ── the play.py wiring ────────────────────────────────────────────────────────

def _session(tmp_path, responder):
    from tests.localdm_fakes import FakeBridge
    from localdm import llm
    from localdm.play import Session
    camp = tmp_path / "demo"
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    return Session("demo", FakeClient(responder), llm.Models("dm-local", "a", "c"),
                   camp_dir=camp, bridge=FakeBridge())


DM_REPLY = "She slides the coin back.\n{\"escalate\": null, \"command\": null}"


def user_text(call):
    return call[2][1]["content"]


def test_the_dm_actually_receives_the_canon(tmp_path):
    s = _session(tmp_path, lambda *a: DM_REPLY)
    s.canon.add([{"kind": "dialogue", "speaker": "Maribeth",
                  "text": "Keep it under your tongue.", "turn": 1}])
    s.handle("I ask Maribeth about the toll at the gate")
    assert "Keep it under your tongue." in user_text(s.local.calls[0])


def test_the_relevant_line_is_the_one_that_survives_the_cap(tmp_path):
    """Relevance is a ranking, not a filter: the cap is what keeps the prompt
    small, so with more canon than room the scene's own line must be the one
    kept, and a better-matching line beats a merely more recent one."""
    s = _session(tmp_path, lambda *a: DM_REPLY)
    s.canon.add([{"kind": "dialogue", "speaker": "Vael", "text": "The moonstone remembers.",
                  "turn": 99}]                                          # recent, off-scene
                + [{"kind": "dialogue", "speaker": "Maribeth",
                    "text": "The toll at the gate is two silver.", "turn": 1}])
    s.canon_limit = 1
    s.handle("I ask about the toll at the gate")
    user = user_text(s.local.calls[0])
    assert "The toll at the gate is two silver." in user
    assert "moonstone remembers" not in user


def test_a_session_with_no_canon_sends_none(tmp_path):
    s = _session(tmp_path, lambda *a: DM_REPLY)
    s.handle("I look around.")
    assert "## Canon" not in user_text(s.local.calls[0])


def test_a_short_session_never_even_starts_the_extractor(tmp_path):
    s = _session(tmp_path, lambda *a: DM_REPLY)
    s.handle("I look around.")
    assert s.extractor._thread is None


# ── the background extractor ──────────────────────────────────────────────────

def _one_canon_call(payload):
    return FakeClient(lambda model, messages, role: payload)


def test_extraction_rides_the_window_the_summarizer_would_fold(tmp_path):
    m = Memory(tmp_path)
    for i in range(10):
        m.add("dm" if i % 2 else "player", f"line {i}")
    ex = Extractor(_one_canon_call('[]'), "fast", m, Canon(m), keep=6, batch=4)
    assert ex.due() and ex.extract() is False        # nothing worth keeping
    assert ex._cursor() == 4, "advance even on an empty result, or we rescan forever"


def test_it_reads_only_never_already_summarized_turns(tmp_path):
    m = Memory(tmp_path)
    for i in range(10):
        m.add("dm", "It is a receipt.")
    ex = Extractor(_one_canon_call('[]'), "fast", m, Canon(m), keep=6, batch=4)
    ex.extract()
    assert ex._cursor() == 4 and m.summarized() == 0, "the transcript is still whole"


def test_extraction_lands_verbatim_records_in_the_file(tmp_path):
    m = Memory(tmp_path)
    m.add("dm", NARRATION)
    for i in range(8):
        m.add("player", f"line {i}")
    payload = ('[{"kind":"dialogue","speaker":"Maribeth",'
               '"text":"Keep it under your tongue, or I\'ll sew it there,"},'
               '{"kind":"reveal","key":"moonstone-nature","text":"It is a receipt."}]')
    ex = Extractor(_one_canon_call(payload), "fast", m, Canon(m), keep=6)
    ex.extract()
    kept = Canon(m).records()
    assert {r["kind"] for r in kept} == {"dialogue", "reveal"}
    assert kept[0]["text"] == "Keep it under your tongue, or I'll sew it there,"


def test_punctuation_the_player_never_heard_is_rejected():
    """The narration ends that clause on a comma; a model that tidies it into a
    full stop has reworded the line, so it does not get to be the canon."""
    out = verify(parse('[{"kind":"dialogue","speaker":"Maribeth",'
                       '"text":"Keep it under your tongue, or I\'ll sew it there."}]'), NARRATION)
    assert out == []


def test_a_second_pass_over_the_same_window_adds_nothing(tmp_path):
    m = Memory(tmp_path)
    m.add("dm", NARRATION)
    for i in range(8):
        m.add("player", f"line {i}")
    payload = '[{"kind":"reveal","key":"r1","text":"It is a receipt."}]'
    ex = Extractor(_one_canon_call(payload), "fast", m, Canon(m), keep=6)
    ex.extract()
    ex._set_cursor(0)                                # pretend the cursor was lost
    assert ex.extract() is True                      # it re-reads...
    assert len(Canon(m).records()) == 1              # ...but canon does not double up


def test_a_failing_extraction_is_contained_and_the_cursor_stays_put(tmp_path):
    m = Memory(tmp_path)
    for i in range(10):
        m.add("dm", f"line {i}")

    def boom(model, messages, role):
        raise RuntimeError("model is down")

    ex = Extractor(FakeClient(boom), "fast", m, Canon(m), keep=6, batch=4)
    ex._safe_extract()
    assert "model is down" in ex.last_error and ex._cursor() == 0
    assert Canon(m).records() == []


def test_a_short_session_never_extracts(tmp_path):
    m = Memory(tmp_path)
    m.add("dm", NARRATION)
    ex = Extractor(_one_canon_call('[]'), "fast", m, Canon(m), keep=6, batch=8)
    assert not ex.due() and ex.maybe_start() is None
