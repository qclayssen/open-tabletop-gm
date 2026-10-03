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


# ── prompt caching ────────────────────────────────────────────────────────────
# Every test below is off-by-default: the local Ollama path must be untouched.

MSGS = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "U"}]


def test_cache_control_is_off_unless_the_operator_opts_in(monkeypatch):
    """Local Ollama must never see `cache_control`; it cannot use it."""
    monkeypatch.delenv(llm.CACHE_ENV, raising=False)
    seen = []
    llm.Client(base_url="http://x", api_key="", transport=fake(seen=seen)).chat("m", MSGS)
    assert seen[0][1]["messages"] == MSGS


def test_opt_in_marks_the_system_message_and_not_the_user_one(monkeypatch):
    """Only the system message is a breakpoint.

    The user message carries the campaign digest and the recent turns, both of
    which change every turn, so a breakpoint there would invalidate on the next
    request and report a 0% hit rate while looking correctly configured.
    """
    monkeypatch.setenv(llm.CACHE_ENV, "1")
    seen = []
    llm.Client(base_url="http://x", api_key="", transport=fake(seen=seen)).chat("m", MSGS)
    sent = seen[0][1]["messages"]
    assert sent[0]["content"] == [{"type": "text", "text": "SYS",
                                   "cache_control": {"type": "ephemeral"}}]
    assert sent[1] == {"role": "user", "content": "U"}


def test_apply_cache_control_does_not_mutate_its_input_and_is_idempotent():
    """Twice-applied markers must not nest arrays inside each other.

    A retry path that re-wraps an already-wrapped system message would send a
    content array containing a content array, which the endpoint rejects.
    """
    once = llm.apply_cache_control(MSGS)
    assert MSGS[0]["content"] == "SYS", "input was mutated"
    twice = llm.apply_cache_control(once)
    assert twice == once


def test_cache_tokens_are_read_off_the_reply_and_logged(tmp_path):
    """A cache that hits but is never logged is indistinguishable from one that
    does not exist, which is how this stays broken for months."""
    monkey = pytest.MonkeyPatch()
    monkey.setenv(llm.CACHE_ENV, "1")
    log = tmp_path / "usage.jsonl"

    def cached(url, body, headers, timeout):
        return {"model": body["model"], "choices": [{"message": {"content": "Hi."}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 5,
                          "cache_read_input_tokens": 2200,
                          "cache_creation_input_tokens": 2300}}

    try:
        r = llm.Client(base_url="http://x", api_key="", transport=cached,
                       usage_log=log).chat("m", MSGS, role="dm")
    finally:
        monkey.undo()
    assert (r.cache_read, r.cache_created) == (2200, 2300)
    assert llm.cache_totals(log) == (2200, 2300, 40)


def test_cache_totals_tolerates_rows_logged_before_this_change(tmp_path):
    """usage.jsonl files already on disk carry no cache keys at all.

    `totals()` reads `r["prompt_tokens"]` by key, so a naive `r["cache_read"]`
    in `cache_totals` raises KeyError on the first pre-existing row and takes
    /usage down for every session that started before this landed.
    """
    log = tmp_path / "usage.jsonl"
    log.write_text(json.dumps({"t": "old", "role": "dm", "model": "dm-local",
                               "prompt_tokens": 10, "completion_tokens": 3,
                               "seconds": 1.0}) + "\n", encoding="utf-8")
    assert llm.cache_totals(log) == (0, 0, 10)
    assert llm.cache_totals(tmp_path / "missing.jsonl") == (0, 0, 0)


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


def test_a_blank_answer_spent_on_reasoning_says_so():
    """qwen3.5:4b with no reasoning_effort burns the whole max_tokens budget in
    the message's "reasoning" field and returns content "". The caller used to
    get "" back and could not tell that apart from a model with nothing to say."""
    spent = {"choices": [{"message": {"content": "", "reasoning": "hmm, the rules say..."}}],
             "usage": {"prompt_tokens": 1034, "completion_tokens": 400}}

    def transport(url, body, headers, timeout):
        return spent

    c = llm.Client(base_url="http://x", api_key="", transport=transport)
    with pytest.raises(llm.LLMError) as err:
        c.chat("qwen3.5:4b", [{"role": "user", "content": "hi"}], max_tokens=400,
               role="advisor:arbiter")
    msg = str(err.value)
    assert "400/400" in msg and "reasoning" in msg and "max_tokens" in msg


def test_finish_reason_is_reported_and_defaults_to_stop():
    """The DM's reply is narration plus a JSON line in that order, so a reply the
    endpoint cut short has no directive in it at all. Nothing read finish_reason
    before, so the turn was silently voided. It has to be readable from here."""
    seen = []
    c = llm.Client(base_url="http://x", api_key="", transport=fake(seen=seen))
    assert c.chat("m", []).finish_reason == "stop", "an endpoint that omits it is not truncating"

    def truncating(url, body, headers, timeout):
        return {"model": "m", "choices": [{"finish_reason": "length",
                                           "message": {"content": "A long scene and th"}}]}

    c = llm.Client(base_url="http://x", api_key="", transport=truncating)
    r = c.chat("m", [], max_tokens=600)
    assert r.finish_reason == "length" and r.text.endswith("and th")


def test_a_genuinely_empty_answer_without_reasoning_is_returned_as_empty():
    """Not every blank answer is a budget problem. With no reasoning behind it and
    tokens to spare, "   " is a real (if useless) answer and the caller decides."""
    blank = {"choices": [{"message": {"content": "   "}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 3}}

    def transport(url, body, headers, timeout):
        return blank

    c = llm.Client(base_url="http://x", api_key="", transport=transport)
    assert c.chat("m", [{"role": "user", "content": "hi"}], max_tokens=400,
                  role="advisor:arbiter").text == "   "


@pytest.mark.parametrize("model,expected", [
    ("qwen3.5:4b", "none"),
    ("qwen3:14b", "none"),
    ("space-bunny-alpha", "medium"),
    ("openai/gpt-oss-20b", "medium"),
    ("dm-local", "none"),        # opaque combo name: safe fallback
    (None, "none"),
])
def test_reasoning_default_follows_model_family(monkeypatch, model, expected):
    monkeypatch.delenv("GM_REASONING", raising=False)
    assert llm.reasoning_from_env(model) == expected


def test_env_override_beats_model_detection(monkeypatch):
    monkeypatch.setenv("GM_REASONING", "low")
    assert llm.reasoning_from_env("space-bunny-alpha") == "low"
    monkeypatch.setenv("GM_REASONING", "off")
    assert llm.reasoning_from_env("space-bunny-alpha") is None
