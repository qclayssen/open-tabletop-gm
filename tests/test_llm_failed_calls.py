"""#237: a failed model call is evidence and must be recorded as a failure.

Before this, `Client.chat` called `self._log(...)` as its last statement, after three
`raise` paths. A 504, a 401, a refused connection, a malformed reply and a blank answer all
left no row at all -- so the evidence file described only the calls that worked, and a run
in which every call failed produced the same file as a run in which every call succeeded.

The transport-level cases here go through the real `_http` with `urlopen` patched, because
the real transport converts every error into `LLMError` and a fake raising a raw
`HTTPError` would test a path nothing in production takes. No network. Every "secret" is
written by this file.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import llm          # noqa: E402

# Things that must never reach usage.jsonl. The prompt is what the table said, the body is
# what the upstream answered, the key is the credential. A usage log is read by run
# reports, archived and grepped, so anything written here becomes a copy of the
# conversation or a copy of the credential.
PROMPT_SECRET = "the ferryman's daughter is the moonstone"
BODY_SECRET = "PROMPT-ECHOED-BY-UPSTREAM: the ferryman's daughter is the moonstone"


@contextlib.contextmanager
def real_transport(exc):
    """The real `_http`, with `urlopen` patched to fail the way an endpoint fails."""
    def fake_urlopen(req, timeout=None):
        if isinstance(exc, urllib.error.HTTPError):
            # `_http` reads the error body, so it needs a readable file.
            exc.fp = io.BytesIO(b'{"error": "upstream said no"}')
        raise exc

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(urllib.request, "urlopen", fake_urlopen)
        yield


def client(log, **kw):
    return llm.Client(base_url="http://x:1", api_key=kw.pop("api_key", ""),
                      usage_log=log, **kw)


def rows(log):
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def ok_transport(text="hi", usage=None):
    def transport(url, body, headers, timeout):
        return {"model": body["model"], "choices": [{"message": {"content": text}}],
                "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20}}
    return transport


# ── the defect: a failure leaves no evidence ────────────────────────────────

@pytest.mark.parametrize("exc,expected", [
    (urllib.error.HTTPError("u", 504, "Gateway Timeout", {}, None), "http-504"),
    (urllib.error.HTTPError("u", 401, "Unauthorized", {}, None), "http-401"),
    (urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None), "http-429"),
    (urllib.error.HTTPError("u", 404, "Not Found", {}, None), "http-404"),
    (urllib.error.URLError(ConnectionRefusedError("refused")), "unreachable"),
    (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
])
def test_a_failed_call_writes_a_row_and_still_raises(tmp_path, exc, expected):
    """The whole issue, once per transport failure shape."""
    log = tmp_path / "usage.jsonl"
    with real_transport(exc):
        with pytest.raises(llm.LLMError):
            client(log, transport=llm._http).chat("dm-local", [], role="dm")
    assert len(rows(log)) == 1, "a failed call left no evidence"
    assert rows(log)[0]["error"] == expected


def test_a_non_json_reply_is_recorded_as_bad_json(tmp_path):
    """Reachable because the decode moved out of the reachability block in `_http`."""
    class Resp:
        def read(self):
            return b"<html>gateway</html>"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    log = tmp_path / "usage.jsonl"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(urllib.request, "urlopen", lambda *a, **k: Resp())
        with pytest.raises(llm.LLMError):
            client(log, transport=llm._http).chat("dm-local", [], role="dm")
    assert rows(log)[0]["error"] == "bad-json"


def test_a_malformed_reply_is_recorded_as_well_as_raised(tmp_path):
    """One of the two in-process raise paths, which never go through `_http`."""
    log = tmp_path / "usage.jsonl"
    with pytest.raises(llm.LLMError):
        client(log, transport=lambda *a: {"nope": 1}).chat("dm-local", [])
    assert rows(log)[0]["error"] == "unexpected-reply"


def test_a_blank_answer_is_recorded_as_well_as_raised(tmp_path):
    log = tmp_path / "usage.jsonl"
    with pytest.raises(llm.LLMError):
        client(log, transport=lambda *a: {
            "model": "m", "choices": [{"message": {"content": "", "reasoning": "hmm"}}],
            "usage": {}}).chat("dm-local", [], max_tokens=10)
    assert rows(log)[0]["error"] == "no-answer-budget"


# ── what a failure row carries ──────────────────────────────────────────────

def test_a_failure_row_has_role_model_duration_and_status(tmp_path):
    log = tmp_path / "usage.jsonl"
    with real_transport(urllib.error.HTTPError("u", 504, "", {}, None)):
        with pytest.raises(llm.LLMError):
            client(log, transport=llm._http).chat("qwen3.5:9b", [], role="dm")
    row = rows(log)[0]
    assert row["status"] == "error"
    assert row["role"] == "dm"
    assert row["model"] == "qwen3.5:9b"
    assert isinstance(row["seconds"], (int, float)) and row["seconds"] >= 0
    assert row["error_type"] == "HTTPError"


def test_a_success_row_is_marked_ok(tmp_path):
    """So a reader never has to infer status from the presence of a field."""
    log = tmp_path / "usage.jsonl"
    client(log, transport=ok_transport()).chat("dm-local", [], role="dm")
    assert rows(log)[0]["status"] == "ok"


def test_failure_tokens_are_null_and_not_zero(tmp_path):
    """Zero is a real value in this log -- a cache hit reports zero uncached tokens.

    A failed row carrying 0 would deflate the totals silently and would read as "the
    endpoint said this call cost nothing", which is a different and false claim.
    """
    log = tmp_path / "usage.jsonl"
    with real_transport(urllib.error.HTTPError("u", 500, "", {}, None)):
        with pytest.raises(llm.LLMError):
            client(log, transport=llm._http).chat("dm-local", [])
    row = rows(log)[0]
    for key in ("prompt_tokens", "completion_tokens", "cache_read", "cache_created"):
        assert key in row, f"{key} should be present and unknown, not absent"
        assert row[key] is None, f"{key} should be null, not {row[key]!r}"


# ── no credential or prompt leakage ─────────────────────────────────────────

def test_a_failure_row_contains_no_prompt_and_no_upstream_body(tmp_path):
    """`_http` puts up to 300 characters of upstream body in the LLMError message.

    That body can echo the prompt. Writing it into a file that gets archived and grepped
    turns the usage log into a copy of the conversation, so the message is classified, not
    copied.
    """
    log = tmp_path / "usage.jsonl"
    exc = llm.LLMError(f"HTTP 400 from http://x: {BODY_SECRET}")
    with pytest.raises(llm.LLMError):
        client(log, api_key="sk-should-never-appear",
               transport=lambda *a: (_ for _ in ()).throw(exc)
               ).chat("dm-local", [{"role": "user", "content": PROMPT_SECRET}], role="dm")
    blob = log.read_text(encoding="utf-8")
    assert PROMPT_SECRET not in blob
    assert BODY_SECRET not in blob
    assert "sk-should-never-appear" not in blob


def test_the_error_field_is_a_classification_not_a_message(tmp_path):
    """Even a credential that leaked into an exception message must not be logged."""
    log = tmp_path / "usage.jsonl"
    exc = llm.LLMError("HTTP 400 from http://user:pw@host/v1/chat/completions: "
                       "auth token ABCDEF123456 is not valid")
    with pytest.raises(llm.LLMError):
        client(log, transport=lambda *a: (_ for _ in ()).throw(exc)).chat("dm-local", [])
    row = rows(log)[0]
    assert row["error"] == "other"          # unrecognised shape, and it says so
    assert len(row["error"]) <= 20          # never the message
    assert "ABCDEF" not in json.dumps(row)
    assert "pw@" not in json.dumps(row)


@pytest.mark.parametrize("exc,expected", [
    (urllib.error.HTTPError("u", 401, "", {}, None), "http-401"),
    (urllib.error.HTTPError("u", 429, "", {}, None), "http-429"),
    (urllib.error.HTTPError("u", 500, "", {}, None), "http-500"),
    (TimeoutError("t"), "timeout"),
    (urllib.error.URLError(ConnectionRefusedError("refused")), "unreachable"),
    (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
    (json.JSONDecodeError("x", "{", 0), "bad-json"),
    (llm.LLMError("unexpected reply: {...}"), "unexpected-reply"),
    (llm.LLMError("no answer: 900/900 completion tokens went to reasoning"),
     "no-answer-budget"),
    (llm.LLMError("something else entirely"), "other"),
])
def test_error_classification(exc, expected):
    assert llm._classify_error(exc) == expected


# ── the log stays usable ────────────────────────────────────────────────────

def test_a_failure_to_write_the_evidence_does_not_mask_the_error(tmp_path):
    """Losing the row is bad. Turning a 504 into an unraisable OSError is worse."""
    log = tmp_path / "usage.jsonl"
    log.mkdir()                                # a directory where a file should be
    with real_transport(urllib.error.HTTPError("u", 502, "", {}, None)):
        with pytest.raises(llm.LLMError):
            client(log, transport=llm._http).chat("dm-local", [])


def test_no_log_configured_means_no_failure(tmp_path):
    """`usage_log=None` must stay a supported configuration, not raise."""
    with real_transport(urllib.error.HTTPError("u", 500, "", {}, None)):
        with pytest.raises(llm.LLMError):
            client(tmp_path / "none.jsonl", transport=llm._http).chat("dm-local", [])


def test_a_successful_call_with_no_log_is_unaffected():
    c = llm.Client(base_url="http://x", api_key="", transport=ok_transport())
    assert c.chat("dm-local", []).text == "hi"


# ── aggregation: the totals have to agree with the rows ─────────────────────

def test_totals_counts_a_failed_call_without_inventing_tokens(tmp_path):
    """Before, a role whose every call failed showed no row and therefore no entry."""
    log = tmp_path / "usage.jsonl"
    client(log, transport=ok_transport()).chat("dm-local", [], role="dm")
    for _ in range(3):
        with real_transport(urllib.error.HTTPError("u", 504, "", {}, None)):
            with pytest.raises(llm.LLMError):
                client(log, transport=llm._http).chat("dm-local", [], role="dm")
    assert llm.totals(log) == [("dm", "dm-local", 4, 100, 20)]


def test_totals_agrees_with_the_rows_it_read(tmp_path):
    """The acceptance criterion in the form that catches a drift: sum and compare.

    Whatever `totals()` reports, re-deriving it from the raw rows has to reproduce. A
    future edit that widens a tuple or filters a status without saying so breaks this,
    rather than quietly changing a number in somebody's run report.
    """
    log = tmp_path / "usage.jsonl"
    client(log, transport=ok_transport()).chat("dm-local", [], role="dm")
    client(log, transport=ok_transport()).chat("dm-local", [], role="advisor")
    with real_transport(urllib.error.HTTPError("u", 429, "", {}, None)):
        with pytest.raises(llm.LLMError):
            client(log, transport=llm._http).chat("dm-local", [], role="advisor")

    raw = rows(log)
    assert len(raw) == 3
    for role, model, calls, p, c in llm.totals(log):
        mine = [r for r in raw if r["role"] == role and r["model"] == model]
        assert len(mine) == calls
        assert p == sum(int(r["prompt_tokens"] or 0) for r in mine)
        assert c == sum(int(r["completion_tokens"] or 0) for r in mine)


def test_failures_reports_the_classification_and_a_count(tmp_path):
    log = tmp_path / "usage.jsonl"
    for code, n in ((504, 3), (401, 1)):
        for _ in range(n):
            with real_transport(urllib.error.HTTPError("u", code, "", {}, None)):
                with pytest.raises(llm.LLMError):
                    client(log, transport=llm._http).chat("dm-local", [], role="dm")
    assert llm.failures(log) == [("dm", "dm-local", "http-504", 3),
                                 ("dm", "dm-local", "http-401", 1)]


def test_failures_is_empty_for_a_clean_run(tmp_path):
    log = tmp_path / "usage.jsonl"
    client(log, transport=ok_transport()).chat("dm-local", [], role="dm")
    assert llm.failures(log) == []
    assert llm.failures(tmp_path / "nothing.jsonl") == []


def test_cache_totals_ignores_the_nulls_in_a_failed_row(tmp_path):
    """A refused call was never billed a cache hit, so it contributes nothing.

    `int(None or 0)` makes that work; the test is here so that removing the nulls does not
    turn `cache_totals` into a TypeError, which is the failure worth catching.
    """
    log = tmp_path / "usage.jsonl"
    client(log, transport=ok_transport(
        usage={"prompt_tokens": 50, "cache_read_input_tokens": 900})).chat(
            "dm-local", [], role="dm")
    with real_transport(urllib.error.HTTPError("u", 500, "", {}, None)):
        with pytest.raises(llm.LLMError):
            client(log, transport=llm._http).chat("dm-local", [])
    assert llm.cache_totals(log) == (900, 0, 50)


def test_the_row_shape_is_a_superset_of_the_old_one(tmp_path):
    """`totals()` rows are unpacked positionally by play.py and by other tests.

    Asserting the field set makes adding one a deliberate act rather than something a
    reader discovers as an IndexError in somebody else's report.
    """
    log = tmp_path / "usage.jsonl"
    client(log, transport=ok_transport()).chat("dm-local", [], role="dm")
    assert set(rows(log)[0]) == {"t", "status", "role", "model", "prompt_tokens",
                                  "completion_tokens", "seconds", "cache_read",
                                  "cache_created"}
