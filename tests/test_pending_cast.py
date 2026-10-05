"""A question can offer a cast, but only the next explicit answer may spend."""
import json
from pathlib import Path

import pytest

from tests.localdm_fakes import FakeBridge, FakeClient
from localdm import context, llm
from localdm.play import Session

MODELS = llm.Models("dm-local", "dm-advisor", "dm-council")
QUESTION = "I cast Mage Armor, right?"


@pytest.fixture
def session(tmp_path, monkeypatch):
    root = tmp_path / "root"
    camp = root / "campaigns" / "demo"
    (camp / "characters").mkdir(parents=True)
    sheet = Path(__file__).parent / "fixtures" / "Kairos_Level1.md"
    (camp / "characters" / "Kairos.md").write_text(sheet.read_text(encoding="utf-8"), encoding="utf-8")
    (camp / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    (camp / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    client = FakeClient(lambda *args: "The room is quiet.\n" + json.dumps({
        "cast": "Mage Armor" if len(client.dm_calls()) == 1 else None,
        "command": None, "escalate": None}))
    return Session("demo", client, MODELS, camp_dir=camp, bridge=FakeBridge())


def slots(session):
    return session._cast_lookup("Mage Armor")[2].extra["slots"]["1"]["used"]


def offer(session):
    out = " ".join(session.handle(QUESTION))
    assert "was not cast" in out
    assert "yes" in out and "no" in out
    assert slots(session) == 0
    return out


@pytest.mark.parametrize("answer", ["yes", "y", " YES "])
def test_confirm_casts_once_without_classifier(session, answer):
    offer(session)
    before = len(session.client.dm_calls())
    assert "AC is now 15" in " ".join(session.handle(answer))
    assert slots(session) == 1
    assert len(session.client.dm_calls()) == before + 1  # outcome narration only
    assert session.turn == 2
    turns = session.memory.turns()
    assert any(t["role"] == "player" and t["text"] == answer.strip() for t in turns)
    assert any(t["role"] == "engine" and "yes" in t["text"] for t in turns)
    session.handle("yes")
    assert slots(session) == 1


@pytest.mark.parametrize("answer", ["no", "n"])
def test_decline_is_recorded_and_does_not_call_model(session, answer):
    offer(session)
    before = len(session.client.dm_calls())
    assert "cancel" in " ".join(session.handle(answer)).lower()
    assert len(session.client.dm_calls()) == before
    assert slots(session) == 0
    session.handle("yes")
    assert slots(session) == 0


@pytest.mark.parametrize("line", ["I look around.", "/usage", "/reload", "[[roll mode: players]]",
                                  "[[roll mode: players]]\n/usage",
                                  "[[roll mode: players]]\nyes", "yes please"])
def test_other_input_expires_offer(session, line):
    offer(session)
    session.handle(line)
    session.handle("yes")
    assert slots(session) == 0


def test_blank_preserves_offer(session):
    offer(session)
    assert session.handle("   ") == []
    session.handle("yes")
    assert slots(session) == 1


def test_recreated_session_drops_offer(session):
    offer(session)
    fresh = Session("demo", session.client, MODELS, camp_dir=session.camp_dir, bridge=FakeBridge())
    fresh.handle("yes")
    assert slots(fresh) == 0


def test_combat_started_after_offer_refuses_and_consumes_it(session):
    offer(session)
    session.bridge.snapshots = [{"status": "active"}]
    assert "fight is running" in " ".join(session.handle("yes"))
    assert slots(session) == 0
    session.bridge.snapshots = [None]
    session.handle("yes")
    assert slots(session) == 0


def test_confirmation_rechecks_slots(session):
    offer(session)
    sheet = session.camp_dir / "characters" / "Kairos.md"
    sheet.write_text(sheet.read_text(encoding="utf-8").replace("| 1st | 2 | 0 |", "| 1st | 2 | 2 |"), encoding="utf-8")
    assert "no level 1 slot left" in " ".join(session.handle("yes"))
    assert slots(session) == 2
    assert context.party_stats(session.camp_dir)[0]["ac"] == 12


def test_confirmation_rechecks_known_spell(session):
    offer(session)
    sheet = session.camp_dir / "characters" / "Kairos.md"
    sheet.write_text(sheet.read_text(encoding="utf-8").replace("Mage Armor, ", ""), encoding="utf-8")
    assert "not" in " ".join(session.handle("yes")).lower()
    assert slots(session) == 0
    assert context.party_stats(session.camp_dir)[0]["ac"] == 12


def test_engine_prompt_has_priority_and_drops_offer(session, monkeypatch):
    offer(session)
    session.pending = {"args": ["attack"], "rolls": [], "react": "Shield?"}
    calls = []
    monkeypatch.setattr(session, "_engine", lambda *args: calls.append(args) or ["reaction"])
    assert session.handle("yes") == ["reaction"]
    assert calls[0][2] == ["yes"]
    session.pending = None
    session.handle("yes")
    assert slots(session) == 0


def test_unresolvable_spell_does_not_offer(session):
    session.client.responder = lambda *args: 'The room is quiet.\n{"cast": "Wish"}'
    out = " ".join(session.handle("Can I cast Wish?"))
    assert "Say yes" not in out
    assert session.pending_cast is None
