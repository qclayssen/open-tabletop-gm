"""The localdm loop answers informational lines from the engine, with no model call."""
from __future__ import annotations

import pytest

from tests.localdm_fakes import FakeBridge, FakeClient
from tests.test_localdm_play import MODELS, NULLS, _enc, camp_dir, fight
from localdm import bridge as bridge_mod
from localdm.bridge import Bridge, Result, normalize
from localdm.play import Session
from tactics import state

SHEET = ("# Kairos\n**HP:** 6/8 | **AC:** 15\n\n## Skills\n| Skill | Ability | Bonus |\n|---|---|---|\n"
         "| Perception | Wis | +2 |\n| Insight | Wis | +0 |\n| Investigation | Int | +6 |\n\n"
         "## Equipment & Inventory\n**Weapons:**\n- Quarterstaff\n\n**Adventuring Gear:**\n"
         "- Rope, 50 ft\n- Healer's kit\n\n**Currency:** 12gp 0sp 0cp\n\n## Backstory & Notes\n")


def _refuse_model(m, msgs, role):
    raise AssertionError("the model must not be called for this line")


def explore(tmp_path, sheet=SHEET):
    d = camp_dir(tmp_path, "council: off")
    if sheet:
        (d / "characters").mkdir()
        (d / "characters" / "Kairos.md").write_text(sheet, encoding="utf-8")
    c = FakeClient(_refuse_model)
    return Session("demo", c, MODELS, camp_dir=d, bridge=FakeBridge()), c


def in_fight(tmp_path, responder=_refuse_model, handlers=None):
    d = camp_dir(tmp_path, "council: off")
    state.save(_enc(), state.encounter_path(d))
    c = FakeClient(responder)
    b = FakeBridge([fight()], handlers or {"status": lambda a: Result(0, "Round 1.")})
    s = Session("demo", c, MODELS, camp_dir=d, bridge=b)
    s.combat = "engine"
    return s, c, b


@pytest.mark.parametrize("line", ["how far is the frog?", "what can I do?", "how much movement do I have left?",
                                  "what's my hp?", "what is my AC?"])
def test_fight_questions_never_reach_the_model(tmp_path, line):
    s, c, b = in_fight(tmp_path)
    out = s.handle(line)
    assert c.calls == [] and out
    assert not [r for r in b.ran if r[0] not in ("status",)], "nothing rolled, moved or ended"


def test_distance_answer_comes_from_engine_facts(tmp_path):
    s, c, _ = in_fight(tmp_path)
    out = s.handle("how far is the frog?")
    assert "Giant Frog 1" in out[0] and "ft away" in out[0]


def test_a_fight_command_still_goes_to_the_parser(tmp_path):
    replies = iter(['Go.\n{"escalate": null, "command": "dash kairos"}', "Fast." + NULLS])
    s, c, b = in_fight(tmp_path, lambda m, msgs, role: next(replies),
                       {"status": lambda a: Result(0, "Round 1."), "dash": lambda a: Result(0, "Dashes.")})
    s.handle("hmm, let me think")
    assert c.calls and ["dash", "kairos"] in b.ran


@pytest.mark.parametrize("line,needle", [
    ("what is my AC?", "AC 15"),
    ("how many hit points do I have?", "6/8 HP"),
    ("what is my passive perception?", "passive Perception 12"),
    ("what's my passive investigation", "passive Investigation 16"),
    ("what do I have in my pack?", "Rope, 50 ft"),
])
def test_sheet_questions_are_answered_without_the_model(tmp_path, line, needle):
    s, c = explore(tmp_path)
    out = s.handle(line)
    assert c.calls == []
    assert needle in " ".join(out)


def test_no_inventory_line_is_said_not_invented(tmp_path):
    s, c = explore(tmp_path, "# Kairos\n**HP:** 8/8 | **AC:** 12\n")
    assert "no inventory line" in s.handle("what do I have?")[0]
    assert c.calls == []


@pytest.mark.parametrize("sheet,line,needle", [
    ("# Kairos\n**AC:** 15\n", "how many hit points do I have?", "no HP"),
    ("# Kairos\n**HP:** 6/8\n", "what is my AC?", "no AC"),
    ("# Kairos\n", "how many hit points do I have?", "no HP"),
])
def test_a_number_the_sheet_does_not_state_is_said_absent_not_filled_in(tmp_path, sheet, line, needle):
    """party_stats defaults a missing HP to 1/1 and a missing AC to 10 for the sidebar.
    A sheet question is not the sidebar: with neither field on the sheet the answer has to
    say so, because a confident "Kairos: 1/1 HP." is a guess wearing the sheet's name."""
    s, c = explore(tmp_path, sheet)
    out = s.handle(line)
    assert needle in out[0]
    assert not [n for n in ("1/1", "AC 10") if n in " ".join(out)]
    assert c.calls == []


def test_an_active_ac_override_still_wins_over_the_sheet(tmp_path):
    """Mage Armor is an engine fact, so it is the sheet's AC plus the override, not the
    override alone: reading the sheet directly must not lose the still-active effect."""
    import json
    d = camp_dir(tmp_path, "council: off")
    (d / "characters").mkdir()
    (d / "characters" / "Kairos.md").write_text(SHEET, encoding="utf-8")
    (d / "tracker.json").write_text(json.dumps({"kairos": {"effects": [
        {"name": "mage armor", "ac": 13}]}}), encoding="utf-8")
    c = FakeClient(_refuse_model)
    s = Session("demo", c, MODELS, camp_dir=d, bridge=FakeBridge())
    assert "AC 13" in " ".join(s.handle("what is my AC?"))
    assert c.calls == []


@pytest.mark.parametrize("line", ["what is the guard's AC?", "what is the old man carrying?",
                                  "I check my pack for rope", "give me a moment",
                                  "how much armor do I need", "can I see the goblin's armor",
                                  "should I buy armor"])
def test_other_lines_still_reach_the_model(tmp_path, line):
    s, c = explore(tmp_path)
    c.responder = lambda m, msgs, role: "Ok." + NULLS
    s.handle(line)
    assert c.calls


def test_no_sheet_leaves_the_question_to_the_story(tmp_path):
    s, c = explore(tmp_path, sheet=None)
    c.responder = lambda m, msgs, role: "Ok." + NULLS
    s.handle("what is my AC?")
    assert c.calls


# bridge: verbs, arity, phrasing, approach, incomplete commands

def test_cast_is_a_player_verb():
    assert bridge_mod.parse_player_command("cast kairos fire bolt frog-1")[0] == "cast"


def test_framing_words_are_dropped_and_short_commands_are_refused():
    assert normalize(["attack", "kairos", "at", "the", "frog-1"])[0] == ["attack", "kairos", "frog-1"]
    assert normalize(["cast", "kairos", "fire", "bolt", "on", "frog-1"])[0] == \
        ["cast", "kairos", "fire", "bolt", "frog-1"]
    assert "not complete" in normalize(["cast", "kairos"])[1]
    assert "not complete" in normalize(["move"])[1]


def test_move_toward_a_creature_names_it_for_engine_approach():
    enc = _enc()
    assert normalize(["move", "kairos", "toward", "the", "giant-frog-1"], enc)[0] == \
        ["move", "kairos", "giant-frog-1"]
    assert normalize(["move", "kairos", "toward", "frog"], enc)[0] == ["move", "kairos", "giant-frog-1"]
    assert normalize(["move", "kairos", "D5"], enc)[0] == ["move", "kairos", "D5"]


def test_a_short_model_command_never_reaches_the_engine(tmp_path):
    s, c, b = in_fight(tmp_path, lambda m, msgs, role: 'Ok.\n{"command": "cast kairos"}')
    out = s.handle("hmm, let me think")
    assert "not complete" in out[-1]
    assert not [r for r in b.ran if r[0] == "cast"]


def test_model_move_toward_runs_as_a_move_at_the_creature(tmp_path):
    s, c, b = in_fight(tmp_path, lambda m, msgs, role: 'Ok.\n{"command": "move kairos toward the frog"}',
                       {"status": lambda a: Result(0, "Round 1."), "move": lambda a: Result(0, "Moved.")})
    s.handle("hmm, let me think")
    assert ["move", "kairos", "giant-frog-1"] in b.ran


def test_bridge_survives_systemexit_and_code_one(tmp_path, monkeypatch):
    from tactics import cli
    b = Bridge("demo", tmp_path)

    def boom(argv):
        raise SystemExit(2)
    monkeypatch.setattr(cli, "main", boom)
    r = b.run(["cast"])
    assert r.code == 2 and "not complete" in r.text

    def friendly(argv):
        print("That command is not complete: the following arguments are required: token.")
        return 1
    monkeypatch.setattr(cli, "main", friendly)
    r = b.run(["cast"])
    assert r.code == 1 and r.text.startswith("That command is not complete")
