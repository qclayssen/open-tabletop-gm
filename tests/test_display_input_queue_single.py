"""One player-input queue: every producer is seen by every consumer.

Audit C1 (docs/audits/AUDIT-2026-10-02.md in the outer repo). The display app
kept two queues. `/player-input` and `/combat/do` appended to an in-memory list
persisted to `player_input.json`, which only `/player-input/drain` served;
`/player-input/send` and `/skip` wrote `.input_queue`, which `wrapper.py` (and
`autorun_wait.py`, `drain_queue.py`) claim. So a grid click never reached a GM
running `wrapper.py`, and the drain endpoint never returned a Party Input Send.

The fix keeps `.input_queue` as the only queue. This file runs each producer
against each consumer, through the real route and the real consumer code, so a
second queue cannot come back without a red cell in the matrix:

    producers: Send, Skip, legacy POST /player-input, POST /combat/do
    consumers: POST /player-input/drain, check_input.py (display down),
               wrapper.py's _inject_queue (POSIX only: it needs termios)

It also pins what putting grid outcomes in the shared file must not break: a
second grid action does not replace the first, and Recall or a resend never
removes an outcome the engine already applied.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "display"
PARTY = {"players": [{"name": "Kairos"}, {"name": "Mira"}]}
GRID = {"status": "active", "round": 1, "current": "kairos",
        "order": ["kairos", "frog-1"],
        "tokens": [{"id": "kairos", "name": "Kairos", "controller": "player"},
                   {"id": "frog-1", "name": "Giant Frog 1", "controller": "gm"}]}
UNREACHABLE = "http://" + "127.0.0.1" + ":9/unreachable"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(DISPLAY / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def app_mod():
    return _load("gm_display_app_single_queue", "gm-display-app.py")


@pytest.fixture
def app(app_mod, tmp_path, monkeypatch):
    """The real Flask app, with every runtime file in tmp_path."""
    monkeypatch.setattr(app_mod, "QUEUE_FILE", str(tmp_path / ".input_queue"))
    # Pre-fix, /player-input and /combat/do persisted here. Redirected so that
    # running this file against the old code never writes the real display/.
    monkeypatch.setattr(app_mod, "INPUT_FILE", str(tmp_path / "player_input.json"),
                        raising=False)
    monkeypatch.setattr(app_mod, "_token_ok", lambda: True)
    monkeypatch.setattr(app_mod, "_device_ok", lambda device_id, ip: "approved")
    monkeypatch.setattr(app_mod, "_broadcast", lambda payload: None)
    monkeypatch.setattr(app_mod, "_current_stats", dict(PARTY))
    monkeypatch.setattr(app_mod, "_current_combat", dict(GRID))
    monkeypatch.setattr(app_mod, "_fight_answer", lambda character, text: None)
    monkeypatch.setattr(
        app_mod, "_run_tactics",
        lambda args, extra=(): (0, json.dumps({"text": "Kairos moves B7 to D5\n(10 ft left).",
                                               "result": {}})))
    app_mod._rate_buckets.clear()
    app_mod._input_queue.clear()
    app_mod._sent.clear()
    app_mod._queue_status.clear()
    return app_mod


def _post(app, path, body):
    client = app.app.test_client()
    return client.post(path, json=body, headers={"X-DND-Device": "test-device"})


# Each producer, and the text the GM must end up reading for it.
PRODUCERS = {
    "send": (lambda app: _post(app, "/player-input/send",
                               {"character": "Mira", "text": "steps behind the barrel"}),
             "[Mira]: steps behind the barrel"),
    "skip": (lambda app: _post(app, "/player-input/skip", {"character": "Mira"}),
             "[Mira]: skips their turn"),
    "legacy": (lambda app: _post(app, "/player-input",
                                 {"character": "Mira", "text": "takes cover"}),
               "[Mira]: takes cover"),
    "grid": (lambda app: _post(app, "/combat/do", {"cmd": "move", "args": ["kairos", "D5"]}),
             "[Kairos]: (grid) Kairos moves B7 to D5 (10 ft left)."),
}


def _consume_drain(app, tmp_path) -> str:
    r = _post(app, "/player-input/drain", {})
    assert r.status_code == 200
    return "\n".join(f"[{e['character']}]: {e['text']}" for e in r.get_json())


def _consume_check_input(app, tmp_path) -> str:
    mod = _load("check_input_single_queue", "check_input.py")
    mod.READY_FILE = pathlib.Path(app.QUEUE_FILE)
    mod.DRAIN_URL = UNREACHABLE              # the display is down: file path only
    mod.CONSUMED_URL = UNREACHABLE
    mod.TOKEN_FILE = tmp_path / ".token"
    mod.NARRATION_TARGET = tmp_path / "narration_target"
    mod.ROLL_PREFS = tmp_path / "roll_prefs.json"
    if hasattr(mod, "QUEUE_FILE"):           # the pre-fix JSON fallback
        mod.QUEUE_FILE = pathlib.Path(app.INPUT_FILE)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        mod.main()
    return out.getvalue()


def _consume_wrapper(app, tmp_path) -> str:
    pytest.importorskip("termios")           # wrapper.py is a POSIX PTY wrapper
    mod = _load("wrapper_single_queue", "wrapper.py")
    mod.QUEUE_FILE = app.QUEUE_FILE
    mod.CAMP_FILE = str(tmp_path / ".campaign")
    mod.STATS_FILE = str(tmp_path / "stats.json")
    mod.AUDIT_LOG = str(tmp_path / "input_log.json")
    pathlib.Path(mod.CAMP_FILE).write_text("camp", encoding="utf-8")
    pathlib.Path(mod.STATS_FILE).write_text(json.dumps(PARTY), encoding="utf-8")
    mod._notify_consumed = lambda: None
    mod.time = types.SimpleNamespace(sleep=lambda s: None, time=mod.time.time)
    read_fd, write_fd = os.pipe()
    try:
        mod._inject_queue(write_fd)
        os.close(write_fd)
        write_fd = -1
        chunks = []
        while True:
            chunk = os.read(read_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)


CONSUMERS = {
    "drain": _consume_drain,
    "check_input": _consume_check_input,
    "wrapper": _consume_wrapper,
}


def _wrapper_form(line: str) -> str:
    """wrapper.py strips shell metacharacters, parentheses among them."""
    return line.replace("(", "").replace(")", "")


@pytest.mark.parametrize("consumer", sorted(CONSUMERS))
@pytest.mark.parametrize("producer", sorted(PRODUCERS))
def test_every_producer_reaches_every_consumer(app, tmp_path, producer, consumer):
    produce, expected = PRODUCERS[producer]
    r = produce(app)
    assert r.status_code in (200, 204), r.get_data(as_text=True)
    got = CONSUMERS[consumer](app, tmp_path)
    want = _wrapper_form(expected) if consumer == "wrapper" else expected
    assert want in got, f"{producer} -> {consumer}: GM saw {got!r}"
    # Delivered once: the consumer took it, so the queue is empty again.
    assert not os.path.exists(app.QUEUE_FILE)


def test_two_grid_actions_both_reach_the_gm(app):
    _post(app, "/combat/do", {"cmd": "move", "args": ["kairos", "D5"]})
    app._run_tactics = lambda args, extra=(): (0, json.dumps({"text": "Kairos hits.",
                                                              "result": {}}))
    _post(app, "/combat/do", {"cmd": "attack", "args": ["kairos", "frog-1"]})
    lines = pathlib.Path(app.QUEUE_FILE).read_text(encoding="utf-8").splitlines()
    assert lines == ["[Kairos]: (grid) Kairos moves B7 to D5 (10 ft left).",
                     "[Kairos]: (grid) Kairos hits."]


def test_a_resend_and_a_recall_leave_grid_outcomes_alone(app):
    _post(app, "/combat/do", {"cmd": "move", "args": ["kairos", "D5"]})
    _post(app, "/player-input/send", {"character": "Kairos", "text": "shouts a warning"})
    _post(app, "/player-input/send", {"character": "Kairos", "text": "whispers instead"})
    lines = pathlib.Path(app.QUEUE_FILE).read_text(encoding="utf-8").splitlines()
    assert lines == ["[Kairos]: (grid) Kairos moves B7 to D5 (10 ft left).",
                     "[Kairos]: whispers instead"]
    r = _post(app, "/player-input/recall", {"character": "Kairos"})
    assert r.status_code == 204
    lines = pathlib.Path(app.QUEUE_FILE).read_text(encoding="utf-8").splitlines()
    assert lines == ["[Kairos]: (grid) Kairos moves B7 to D5 (10 ft left)."]
    # A grid outcome is not a recallable action, so it holds no QUEUED badge.
    assert app._queued_characters() == set()


def test_the_legacy_route_cannot_forge_a_second_line(app):
    r = _post(app, "/player-input", {"character": "Mira", "text": "waves\n[Kairos]: surrenders"})
    assert r.status_code == 204
    lines = pathlib.Path(app.QUEUE_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and lines[0].startswith("[Mira]: ")
    r = _post(app, "/player-input", {"character": "Mira]: x\n[Kairos", "text": "hi"})
    assert r.status_code == 400


def test_drain_clears_the_sent_log_it_delivered(app):
    _post(app, "/player-input/send", {"character": "Mira", "text": "ducks"})
    assert "Mira" in app._sent
    _post(app, "/player-input/drain", {})
    assert app._sent == {} and app._queue_status == []
