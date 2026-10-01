"""Display Party Input and the map's click-to-act, against the real engine state.

N-8: a fight question typed into Party Input is answered by tactics/fightq.py
with no model call, instead of being queued for the GM.
Click-to-act: /combat/do answered 409 on every click when the display was
started after the fight began, because only POST /combat filled its snapshot
cache while the map itself was drawn from GET /combat/state.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
import tactics_fixtures as fx                                        # noqa: E402
from tactics import state, sync                                      # noqa: E402


def _app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_fightq", str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def rig(tmp_path, monkeypatch):
    m = _app()
    camp = tmp_path / "camp"
    (camp / "combat").mkdir(parents=True)
    enc = fx.start(fx.encounter([fx.kairos(pos=(0, 0)), fx.frog(pos=(3, 0))]),
                   ["kairos", "frog-1"])
    state.save(enc, state.encounter_path(camp))
    (tmp_path / ".campaign").write_text("camp", encoding="utf-8")
    m.CAMP_FILE = str(tmp_path / ".campaign")
    m.QUEUE_FILE = str(tmp_path / ".input_queue")
    m._find_campaign = lambda name: camp
    m._token_ok = lambda: True
    m._device_ok = lambda d, ip: "approved"
    m._persist_input_queue = lambda: None
    m._broadcast = lambda payload: None
    m._current_stats = {"players": [{"name": "Kairos"}]}
    m._current_combat = None
    m._rate_buckets.clear()
    m._input_queue.clear()
    m._sent.clear()
    m._queue_status.clear()
    m.snapshot = sync.snapshot(enc)
    return m, m.app.test_client(), enc


def _post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json",
                       headers={"X-DND-Device": "d"})


def test_a_fight_question_is_answered_not_queued(rig):
    m, client, _ = rig
    r = _post(client, "/player-input/send", {"character": "Kairos",
                                             "text": "how far is the nearest frog?"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["answered"] is True
    assert "Frog 1" in " ".join(body["lines"]) and "ft" in " ".join(body["lines"])
    assert not m._queued_characters() and not m._sent


def test_a_command_or_story_line_is_still_queued(rig):
    m, client, _ = rig
    for text in ("attack the frog", "I tell the frog a bedtime story"):
        r = _post(client, "/player-input/send", {"character": "Kairos", "text": text})
        assert r.status_code == 204, text
    assert "Kairos" in m._queued_characters()


def test_a_question_is_queued_when_no_fight_is_active(rig):
    m, client, enc = rig
    enc.status = "ended"
    state.save(enc, state.encounter_path(m._find_campaign("camp")))
    r = _post(client, "/player-input/send", {"character": "Kairos", "text": "what can I do"})
    assert r.status_code == 204


def test_click_to_act_works_when_the_display_started_after_the_fight(rig):
    """The 409 root cause: no push ever reached this display, only the engine knew."""
    m, client, snap_enc = rig
    snapshot = m.snapshot
    assert m._current_combat is None
    m._run_tactics = lambda args, extra=(): (
        (0, json.dumps({"combat": snapshot})) if args[0] == "status"
        else (0, json.dumps({"text": "Kairos moves.", "result": {}})))
    # the map is drawn from this and shows "Your turn"
    assert client.get("/combat/state").get_json()["current"] == "kairos"
    r = _post(client, "/combat/do", {"cmd": "move", "args": ["kairos", "B1"]})
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_click_to_act_without_any_fight_still_says_so(rig):
    m, client, _ = rig
    m._run_tactics = lambda args, extra=(): (1, "No active combat.")
    r = _post(client, "/combat/do", {"cmd": "move", "args": ["kairos", "B1"]})
    assert r.status_code == 409
