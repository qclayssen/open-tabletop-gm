"""Milestone 6: the chat client. No network: a fake transport stands in."""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import llm          # noqa: E402


def fake(text="Hello.", seen=None):
    def transport(url, body, headers, timeout):
        if seen is not None:
            seen.append((url, body, headers))
        return {"model": body["model"], "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
    return transport


def test_chat_posts_the_openai_shape_and_returns_text():
    seen = []
    c = llm.Client(base_url="http://x:1/", api_key="k", transport=fake(seen=seen))
    r = c.chat("dm-local", [{"role": "user", "content": "hi"}], max_tokens=50)
    url, body, headers = seen[0]
    assert url == "http://x:1/v1/chat/completions"
    assert body["model"] == "dm-local" and body["max_tokens"] == 50 and body["stream"] is False
    assert headers["Authorization"] == "Bearer k"
    assert (r.text, r.prompt_tokens, r.completion_tokens) == ("Hello.", 10, 3)


def test_no_key_means_no_auth_header():
    seen = []
    llm.Client(base_url="http://x", api_key="", transport=fake(seen=seen)).chat("m", [])
    assert "Authorization" not in seen[0][2]


def test_usage_is_appended_once_per_call(tmp_path):
    log = tmp_path / "usage.jsonl"
    c = llm.Client(base_url="http://x", api_key="", transport=fake(), usage_log=log)
    c.chat("dm-local", [], role="dm")
    c.chat("dm-advisor", [], role="advisor:historian")
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["role"] for r in rows] == ["dm", "advisor:historian"]
    assert rows[1]["model"] == "dm-advisor" and rows[0]["prompt_tokens"] == 10


def test_totals_group_by_role_and_model(tmp_path):
    log = tmp_path / "usage.jsonl"
    c = llm.Client(base_url="http://x", api_key="", transport=fake(), usage_log=log)
    c.chat("dm-local", [], role="dm")
    c.chat("dm-local", [], role="dm")
    c.chat("dm-advisor", [], role="advisor:director")
    assert llm.totals(log) == [("dm", "dm-local", 2, 20, 6),
                               ("advisor:director", "dm-advisor", 1, 10, 3)]
    assert llm.totals(tmp_path / "missing.jsonl") == []


def test_a_reply_without_choices_raises():
    c = llm.Client(base_url="http://x", api_key="", transport=lambda *a: {"error": "nope"})
    with pytest.raises(llm.LLMError):
        c.chat("m", [])


def test_an_unreachable_endpoint_raises_llm_error():
    c = llm.Client(base_url="http://127.0.0.1:9", api_key="", timeout=2)
    with pytest.raises(llm.LLMError):
        c.chat("m", [])


def test_models_url_and_key_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GM_DM_MODEL", "qwen3:14b")
    monkeypatch.delenv("GM_ADVISOR_MODEL", raising=False)
    monkeypatch.delenv("GM_COUNCIL_MODEL", raising=False)
    monkeypatch.setenv("GM_LLM_URL", "http://ollama:11434/")
    monkeypatch.delenv("GM_LLM_KEY", raising=False)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "ok")
    m = llm.Models.from_env()
    assert (m.dm, m.advisor, m.council) == ("qwen3:14b", "dm-advisor", "dm-council")
    c = llm.Client()
    assert c.base_url == "http://ollama:11434" and c.api_key == "ok"


def test_reasoning_effort_is_sent_only_when_asked(monkeypatch):
    seen = []
    c = llm.Client(base_url="http://x", api_key="", transport=fake(seen=seen))
    c.chat("m", [])
    c.chat("m", [], reasoning="none")
    assert "reasoning_effort" not in seen[0][1] and seen[1][1]["reasoning_effort"] == "none"
    monkeypatch.delenv("GM_REASONING", raising=False)
    assert llm.reasoning_from_env() == "none"
    monkeypatch.setenv("GM_REASONING", "off")
    assert llm.reasoning_from_env() is None
    monkeypatch.setenv("GM_REASONING", "Medium")
    assert llm.reasoning_from_env() == "medium"
