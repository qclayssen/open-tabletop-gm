"""Milestone 6: the local DM session loop, with a fake model and fake engine."""
from __future__ import annotations

import sys

import pytest

from tests.localdm_fakes import FakeBridge, FakeClient
from tests.tactics_fixtures import ROOT, _build, _RAW, RULES
from localdm import llm
from localdm.bridge import Result
from localdm.play import Session

NULLS = '\n{"escalate": null, "command": null}'
MODELS = llm.Models("dm-local", "dm-advisor", "dm-council")
ROLL_TEXT = ("Kairos rolls 1d20+5 for Fire Bolt. Nothing has happened yet.\n"
             "Re-run the same command with --roll <the d20 face, no modifier>, "
             "or --for-me to let the engine roll.")


def camp_dir(tmp_path, state="# Campaign: demo\n"):
    d = tmp_path / "demo"
    d.mkdir(parents=True)
    (d / "state.md").write_text(state, encoding="utf-8")
    return d


def fight(current="kairos", controller="player", kairos_hp=8):
    return {"status": "active", "round": 1, "key": "frog-1,kairos",
            "current": {"id": current, "name": current.title(),
                        "side": "pc" if controller == "player" else "enemy",
                        "controller": controller},
            "tokens": [{"id": "kairos", "name": "Kairos", "side": "pc", "hp": kairos_hp,
                        "max_hp": 8, "dead": False, "controller": "player"},
                       {"id": "frog-1", "name": "Giant Frog 1", "side": "enemy", "hp": 18,
                        "max_hp": 18, "dead": False, "controller": "gm"}]}


def user_text(call):
    return call[2][1]["content"]


def test_a_plain_turn_is_one_call_and_hides_the_json(tmp_path):
    c = FakeClient(lambda m, msgs, role: "The reeds whisper." + NULLS)
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    assert s.handle("I listen.") == ["The reeds whisper."]
    assert c.roles() == ["dm"]
    assert [t["role"] for t in s.memory.turns()] == ["player", "dm"]


def test_escalation_asks_the_advisor_then_the_dm_again(tmp_path):
    replies = iter(['A carved sigil.\n{"escalate": "Is this sigil part of the lich cult lore?"}',
                    "The sigil is old, older than the town." + NULLS])

    def responder(model, msgs, role):
        return "It predates the cult." if role.startswith("advisor") else next(replies)

    c = FakeClient(responder)
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    assert s.handle("I study the altar.") == ["The sigil is old, older than the town."]
    assert c.roles()[0] == "dm" and c.roles()[-1] == "dm"
    assert "advisor:historian" in c.roles()
    assert all(call[0] == "dm-advisor" for call in c.calls if call[1].startswith("advisor"))
    assert "It predates the cult." in user_text(c.calls[-1])


def test_show_gm_notes_prints_them(tmp_path):
    replies = iter(['X\n{"escalate": "lore of the cult?"}', "Y" + NULLS])
    c = FakeClient(lambda m, msgs, role: "Note." if role.startswith("advisor") else next(replies))
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge(),
                show_notes=True)
    out = s.handle("hm")
    assert out[0].startswith("[GM notes]") and out[-1] == "Y"


def test_a_new_fight_triggers_advisors_once(tmp_path):
    c = FakeClient(lambda m, msgs, role: "Ok." if role.startswith("advisor") else "Go." + NULLS)
    b = FakeBridge([fight()], {"status": lambda a: Result(0, "Round 1, Kairos's turn.")})
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=b)
    s.handle("I ready my staff.")
    assert sorted(r for r in c.roles() if r != "dm") == ["advisor:director", "advisor:tactician"]
    c.calls.clear()
    s.handle("I wait.")
    assert c.roles() == ["dm"]


def test_council_off_disables_triggers(tmp_path):
    c = FakeClient(lambda m, msgs, role: "Go." + NULLS)
    b = FakeBridge([fight()], {"status": lambda a: Result(0, "Round 1.")})
    s = Session("demo", c, MODELS, bridge=b,
                camp_dir=camp_dir(tmp_path, "## Session Flags\ncouncil: off\n"))
    s.handle("I wait.")
    assert c.roles() == ["dm"]


def test_player_command_runs_then_the_result_is_narrated(tmp_path):
    replies = iter(['Fire gathers in your palm.\n{"escalate": null, '
                    '"command": "attack kairos frog-1 fire bolt"}',
                    "The frog shrieks, smoking." + NULLS])
    c = FakeClient(lambda m, msgs, role: next(replies))
    b = FakeBridge([fight()], {
        "status": lambda a: Result(0, "Round 1."),
        "attack": lambda a: Result(0, "Kairos hits Giant Frog 1: 7 fire damage.")})
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path, "council: off"), bridge=b)
    out = s.handle("I hurl a fire bolt at the frog.")
    assert out == ["Fire gathers in your palm.", "The frog shrieks, smoking."]
    assert ["attack", "kairos", "frog-1", "fire", "bolt"] in b.ran
    assert "7 fire damage" in user_text(c.calls[-1])


def test_a_pending_roll_waits_for_the_players_number(tmp_path):
    replies = iter(['You aim.\n{"command": "attack kairos frog-1 fire bolt"}', "Hit!" + NULLS])
    c = FakeClient(lambda m, msgs, role: next(replies))

    def attack(args):
        if "--roll" not in args:
            return Result(2, ROLL_TEXT)
        return Result(0, "Kairos hits: 7 fire damage.")

    b = FakeBridge([fight()], {"status": lambda a: Result(0, "Round 1."), "attack": attack})
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path, "council: off"), bridge=b)
    out = s.handle("I attack the frog.")
    assert "Kairos rolls 1d20+5" in out[-1] and s.pending is not None
    out = s.handle("14")
    assert b.ran[-1][-2:] == ["--roll", "14"] and out[0] == "Hit!" and s.pending is None


def test_commands_are_ignored_off_turn_or_off_list(tmp_path):
    for snap, cmd in ((fight(current="frog-1", controller="gm"), "attack kairos frog-1"),
                      (fight(), "adjust kairos hp=99")):
        c = FakeClient(lambda m, msgs, role, cmd=cmd: f'Ok.\n{{"command": "{cmd}"}}')
        b = FakeBridge([snap], {"status": lambda a: Result(0, "Round 1.")})
        s = Session("demo", c, MODELS, bridge=b,
                    camp_dir=camp_dir(tmp_path / cmd.split()[0], "council: off"))
        s.handle("go")
        assert not [r for r in b.ran if r[0] in ("attack", "adjust")]


def test_enemy_turns_pick_choose_end_and_narrate_once(tmp_path):
    def responder(model, msgs, role):
        return "2" if role == "enemy-pick" else "The frog lunges and misses." + NULLS

    c = FakeClient(responder)
    b = FakeBridge([fight(current="frog-1", controller="gm"), fight()], {
        "end-turn": lambda a: Result(0, "Round 1, Kairos's turn."),
        "options": lambda a: Result(0, "Giant Frog 1. Pick one\n1. Hop away\n2. Bite Kairos"),
        "choose": lambda a: Result(0, f"{a[2]}. Bite Kairos: miss."),
    })
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path, "council: off"), bridge=b)
    out = s.handle("/c end-turn")
    assert [r[0] for r in b.ran] == ["end-turn", "options", "choose", "end-turn"]
    assert b.ran[2] == ["choose", "frog-1", "2"]
    assert c.roles() == ["enemy-pick", "dm"] and out[-1] == "The frog lunges and misses."


def test_advise_command_uses_the_council_model_and_hides_notes(tmp_path):
    c = FakeClient(lambda m, msgs, role: "Advice.")
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    out = s.handle("/advise council how should the boss fight go?")
    assert c.calls and all(call[0] == "dm-council" for call in c.calls)
    assert "Advice." not in out[0] and "Advice." in s.saved_notes
    assert s.handle("/advise bard hi")[0].startswith("No advisor 'bard'")


def test_usage_lists_totals(tmp_path):
    d = camp_dir(tmp_path)
    client = llm.Client(base_url="http://x", api_key="", usage_log=d / "localdm" / "usage.jsonl",
                        transport=lambda *a: {"choices": [{"message": {"content": "Hi." + NULLS}}],
                                              "usage": {"prompt_tokens": 900,
                                                        "completion_tokens": 40}})
    s = Session("demo", client, MODELS, camp_dir=d, bridge=FakeBridge())
    s.handle("hello")
    assert s.handle("/usage") == ["dm  dm-local  1 calls  900 in  40 out"]


# ── end to end on the real engine, fake model ──────────────────────────────────

rules_mod = sys.modules[type(RULES).__module__]
KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")


@pytest.fixture
def real_camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    monkeypatch.setattr(rules_mod, "_lookup_monster",
                        lambda name: _build._norm_monster(_RAW[name.lower().replace(" ", "-")]))
    return d


def test_start_a_real_fight_and_let_the_frogs_act(real_camp):
    def responder(model, msgs, role):
        if role == "enemy-pick":
            return "1"
        return "Noted." if role.startswith("advisor") else "Stuff happens." + NULLS

    c = FakeClient(responder)
    s = Session("demo", c, MODELS, camp_dir=real_camp)
    out = s.handle('/c start frog-pond --pc Kairos@B7 --monster "giant frog@J5" '
                   '--monster "giant frog@M11" --seed 3')
    assert out[0].startswith("Grid combat on Frog Pond")
    snap = s.bridge.snapshot()
    assert snap["current"]["controller"] == "player" or s.pending is not None
    assert "enemy-pick" in c.roles()
    assert {"advisor:tactician", "advisor:director"} <= set(c.roles())
    assert out[-1] == "Stuff happens." or s.pending is not None


def test_escalations_are_rate_limited(tmp_path):
    def responder(model, msgs, role):
        return "Note." if role.startswith("advisor") else 'Hm.\n{"escalate": "cult lore?"}'

    c = FakeClient(responder)
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge())
    for _ in range(4):
        s.handle("I look.")
    advised = [i for i, r in enumerate(c.roles()) if r.startswith("advisor")]
    assert len(advised) == 2                      # turns 1 and 4, not 2 and 3


def test_the_fast_model_picks_for_enemies(tmp_path):
    models = llm.Models("dm-local", "dm-advisor", "dm-council", "dm-fast")
    c = FakeClient(lambda m, msgs, role: "1" if role == "enemy-pick" else "Ok." + NULLS)
    b = FakeBridge([fight(current="frog-1", controller="gm"), fight()], {
        "end-turn": lambda a: Result(0, "ok"),
        "options": lambda a: Result(0, "Frog\n1. Bite Kairos"),
        "choose": lambda a: Result(0, "1. Bite: miss.")})
    Session("demo", c, models, camp_dir=camp_dir(tmp_path, "council: off"), bridge=b).handle("/c end-turn")
    assert ("dm-fast", "enemy-pick") in [(m, r) for m, r, _ in c.calls]
    assert llm.Models("a").fast == "a"


def test_local_calls_send_reasoning_none_and_advisors_send_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("GM_REASONING", raising=False)
    replies = iter(['X\n{"escalate": "lore of the cult?"}', "Y" + NULLS])
    c = FakeClient(lambda m, msgs, role: "Note." if role.startswith("advisor") else next(replies))
    Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge()).handle("hm")
    assert {r for role, r in c.reasoning if role == "dm"} == {"none"}
    assert {r for role, r in c.reasoning if role.startswith("advisor")} == {None}


def test_a_local_client_takes_the_dm_and_picks_and_advisors_stay_on_the_router(tmp_path):
    local = FakeClient(lambda m, msgs, role: "1" if role == "enemy-pick"
                       else 'Hm.\n{"escalate": "cult lore?"}')
    router = FakeClient(lambda m, msgs, role: "Note.")
    b = FakeBridge([fight(current="frog-1", controller="gm"), fight()], {
        "end-turn": lambda a: Result(0, "ok"),
        "options": lambda a: Result(0, "Frog\n1. Bite Kairos"),
        "choose": lambda a: Result(0, "1. Bite: miss.")})
    s = Session("demo", router, MODELS, camp_dir=camp_dir(tmp_path, "council: off"), bridge=b,
                local_client=local)
    s.handle("/c end-turn")                      # uses the frog turn snapshot first
    s.handle("I look.")
    assert set(local.roles()) == {"dm", "enemy-pick"}
    assert router.roles() and all(r.startswith("advisor") for r in router.roles())
    assert s.summarizer.client is local


def test_the_shadow_advisor_reviews_in_the_background_for_the_next_turn(tmp_path):
    c = FakeClient(lambda m, msgs, role: "Bring back Mira." if role.startswith("advisor")
                   else "Reeds." + NULLS)
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge(), shadow=True)
    assert s.handle("I look.") == ["Reeds."]
    s.join_background(5)
    assert len([r for r in c.roles() if r.startswith("advisor")]) == 1
    assert "Bring back Mira." in s.saved_notes
    s.handle("I wait.")
    dm_calls = [call for call in c.calls if call[1] == "dm"]
    assert "Bring back Mira." in user_text(dm_calls[-1]) and s.saved_notes == ""


def test_a_nothing_review_is_dropped(tmp_path):
    c = FakeClient(lambda m, msgs, role: "Nothing." if role.startswith("advisor") else "Ok." + NULLS)
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge(), shadow=True)
    s.handle("I look.")
    s.join_background(5)
    assert s.saved_notes == ""


def test_no_shadow_when_the_turn_was_already_advised_or_one_is_running(tmp_path):
    import threading as th
    gate = th.Event()

    def responder(model, msgs, role):
        if role.startswith("advisor"):
            gate.wait(5)
            return "Note."
        return "Ok." + NULLS

    c = FakeClient(responder)
    s = Session("demo", c, MODELS, camp_dir=camp_dir(tmp_path), bridge=FakeBridge(), shadow=True)
    s.handle("I look.")
    s.handle("I wait.")                  # the first review is still running
    gate.set()
    s.join_background(5)
    assert len([r for r in c.roles() if r.startswith("advisor")]) == 1
    c.calls.clear()
    s.handle("/advise historian who built the stone?")
    s.handle("I study it.")              # advised this turn (saved notes): no review
    s.join_background(5)
    assert [r for r in c.roles() if r.startswith("advisor")] == ["advisor:historian"]
