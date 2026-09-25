"""Milestone 6: play.py mirrors narration to the display. A fake display on a
free port stands in; nothing here touches port 5001."""
from __future__ import annotations

import builtins
import http.server
import json
import os
import socket
import threading

import pytest

import tests.localdm_fakes  # noqa: F401  (puts scripts/ on sys.path)
from localdm import display_bridge, llm, play

NULLS = '\n{"escalate": null, "command": null}'
NARRATION = "The quad bell tolls.\n\nMage Tower shadows stretch across the grass."


class FakeDisplay:
    """Records every POST /chunk body and header, and other POSTs by path.
    status: the reply code."""

    def __init__(self, status=204):
        self.posts, self.headers, self.other, self.status = [], [], [], status
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/chunk":
                    owner.posts.append(json.loads(body.decode("utf-8")))
                    owner.headers.append({k.lower(): v for k, v in self.headers.items()})
                else:
                    owner.other.append((self.path, json.loads(body.decode("utf-8") or "null")))
                self.send_response(owner.status)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def texts(self):
        return [p["text"] for p in self.posts if "text" in p]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def display():
    d = FakeDisplay()
    yield d
    d.close()


def closed_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


# ── endpoint ───────────────────────────────────────────────────────────────

def test_endpoint_order(tmp_path):
    r = display_bridge.resolve_url
    assert r("", {}, tmp_path) == "http://localhost:5001"
    (tmp_path / ".port").write_text("5077\n", encoding="utf-8")
    assert r("", {}, tmp_path) == "http://localhost:5077"
    assert r("", {"GM_DISPLAY_PORT": "5051"}, tmp_path) == "http://localhost:5051"
    env = {"GM_DISPLAY_PORT": "5051", "GM_DISPLAY_URL": "http://localhost:5060/"}
    assert r("", env, tmp_path) == "http://localhost:5060"
    assert r("http://127.0.0.1:6000", env, tmp_path) == "http://127.0.0.1:6000"
    (tmp_path / ".scheme").write_text("https", encoding="utf-8")
    assert r("", {"GM_DISPLAY_PORT": "5051"}, tmp_path) == "https://localhost:5051"


def test_no_display_builds_nothing():
    assert display_bridge.from_args("c", disabled=True, env={"GM_DISPLAY_URL": "http://x"}) is None


# ── the client ─────────────────────────────────────────────────────────────

def test_register_sends_the_campaign_and_token(display, tmp_path):
    d = display_bridge.Display(display.url, "strixhaven-kairos", token="t0k")
    assert d.register()
    assert display.posts == [{"campaign": "strixhaven-kairos"}]
    assert display.headers[0].get("x-dnd-token") == "t0k"


def test_the_token_stays_on_this_machine():
    assert display_bridge.Display("http://192.0.2.7:5001", "c", token="t0k").token == ""
    assert display_bridge.Display("http://localhost:5051", "c", token="t0k").token == "t0k"


def test_long_narration_splits_on_paragraphs():
    paras = ["a" * 2000, "b" * 2000, "c" * 100]
    assert display_bridge.split_paragraphs("\n\n".join(paras)) == [
        paras[0], "\n\n".join(paras[1:])]


def test_a_refusing_display_warns_once(tmp_path):
    bad = FakeDisplay(status=403)
    try:
        err = (tmp_path / "err.txt").open("w", encoding="utf-8")
        d = display_bridge.Display(bad.url, "demo", token="", err=err)
        assert not d.register()
        assert not d.narrate("One.")
        assert not d.narrate("Two.")
        err.close()
        assert (tmp_path / "err.txt").read_text(encoding="utf-8").count("display") == 1
    finally:
        bad.close()


def test_a_late_display_gets_the_campaign_first(tmp_path):
    err = (tmp_path / "err.txt").open("w", encoding="utf-8")
    d = display_bridge.Display(closed_url(), "demo", token="", err=err)
    assert not d.register()
    late = FakeDisplay()
    try:
        d.url = late.url
        assert not d.narrate("Too soon.")                  # backing off after the failure
        d.retry_at = 0.0
        assert d.narrate("Hello.")
        assert late.posts == [{"campaign": "demo"}, {"text": "Hello."}]
    finally:
        late.close()
        err.close()


# ── the REPL ───────────────────────────────────────────────────────────────

ADVICE = "SECRET-ADVICE: the dean is the culprit."


def responder(body):
    if body["model"] == "dm-advisor":
        return ADVICE
    last = body["messages"][-1]["content"]
    if "BREAK" in last.splitlines()[-1]:
        raise llm.LLMError("cannot reach http://ollama: Connection refused")
    return "<think>hidden reasoning here</think>" + NARRATION + NULLS


def run_repl(monkeypatch, tmp_path, lines, *argv, env=None):
    root = tmp_path / "root"
    (root / "campaigns" / "demo").mkdir(parents=True)
    (root / "campaigns" / "demo" / "state.md").write_text("# Campaign: demo\n",
                                                          encoding="utf-8")
    # setenv first so monkeypatch restores what play.main writes to os.environ
    for k in ("GM_DISPLAY_URL", "GM_DISPLAY_PORT", "GM_LOCAL_URL", "GM_DM_MODEL",
              "GM_ADVISOR_MODEL", "GM_FAST_MODEL", "GM_COUNCIL_MODEL", "TACTICS_NO_DISPLAY"):
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)

    def transport(url, body, headers, timeout):
        return {"choices": [{"message": {"content": responder(body)}}], "usage": {}}

    monkeypatch.setattr(llm, "_http", transport)
    feed = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    return play.main(["-c", "demo", "--no-shadow", *argv])


def test_one_turn_is_one_narration_event(monkeypatch, tmp_path, capsys, display):
    assert run_repl(monkeypatch, tmp_path, ["I look around.", "", "I wait."],
                    "--display-url", display.url) == 0
    out = capsys.readouterr().out
    assert out.count("The quad bell tolls.") == 2           # the terminal shows every reply
    assert display.posts[0] == {"campaign": "demo"}
    assert display.texts() == [NARRATION, NARRATION]        # one per player turn, paragraphs kept


def test_gm_display_url_is_used(monkeypatch, tmp_path, display):
    assert run_repl(monkeypatch, tmp_path, ["I look around."],
                    env={"GM_DISPLAY_URL": display.url}) == 0
    assert display.texts() == [NARRATION]


def test_no_display_sends_nothing(monkeypatch, tmp_path, capsys, display):
    assert run_repl(monkeypatch, tmp_path, ["I look around."],
                    "--display-url", display.url, "--no-display") == 0
    assert "The quad bell tolls." in capsys.readouterr().out
    assert display.posts == []
    assert os.environ.get("TACTICS_NO_DISPLAY") == "1"      # grid combat pushes too


def test_grid_combat_pushes_follow_the_display(monkeypatch, tmp_path, display):
    from tactics import sync
    assert run_repl(monkeypatch, tmp_path, [], "--display-url", display.url) == 0
    assert os.environ.get("GM_DISPLAY_URL") == display.url
    monkeypatch.setenv("GM_DISPLAY_PORT", "5001")           # the URL wins over the port
    sync._post("/combat", {"status": "none"})
    assert display.other == [("/combat", {"status": "none"})]


def test_a_stopped_display_is_not_fatal(monkeypatch, tmp_path, capsys):
    assert run_repl(monkeypatch, tmp_path, ["I look around.", "I wait."],
                    "--display-url", closed_url()) == 0
    cap = capsys.readouterr()
    assert cap.out.count("The quad bell tolls.") == 2
    assert cap.err.count("narration stays in the terminal") == 1


def test_notes_reasoning_and_errors_stay_in_the_terminal(monkeypatch, tmp_path, capsys,
                                                         display):
    lines = ["/advise historian Who is behind the thefts?", "I question the dean.",
             "/usage", "BREAK"]
    assert run_repl(monkeypatch, tmp_path, lines, "--display-url", display.url,
                    "--show-gm-notes") == 0
    out = capsys.readouterr().out
    assert "SECRET-ADVICE" in out and "model unavailable" in out and "dm-local" in out
    sent = json.dumps(display.posts)
    for leak in ("SECRET-ADVICE", "GM notes", "hidden reasoning", "escalate",
                 "model unavailable", "Connection refused", "dm-local", "calls"):
        assert leak not in sent
    assert display.texts() == [NARRATION]                    # only the dean turn
