"""llm.py: one chat call to an OpenAI-compatible endpoint (OmniRoute by default).

Model names are OmniRoute combo names by default, so which real model answers
(local Ollama, a free tier, paid Claude) is decided in OmniRoute, not here.

Environment:
    GM_LLM_URL        endpoint root, default http://localhost:20128 (OmniRoute)
    GM_LOCAL_URL      optional: send the local tier (dm, picks, summaries) straight
                      here, e.g. http://localhost:11434 (Ollama), and only the
                      advisors through GM_LLM_URL. OmniRoute's request queue
                      times local calls out at 15 s (requestQueue.maxWaitMs).
    GM_LLM_KEY        bearer token; falls back to OMNIROUTE_API_KEY
    GM_DM_MODEL       every turn, enemy picks, summaries   (default dm-local)
    GM_ADVISOR_MODEL  escalations and triggers             (default dm-advisor)
    GM_COUNCIL_MODEL  /advise council                      (default dm-council)
    GM_FAST_MODEL     enemy picks and summaries            (default: GM_DM_MODEL)
    GM_REASONING      reasoning_effort sent on local-tier calls (dm, picks,
                      summaries): none, low, medium, high, or off to send
                      nothing. Always wins when set. When unset, the default is
                      looked up from the DM model name (REASONING_BY_MODEL),
                      falling back to none. Qwen3.5 ignores /no_think and
                      spends its whole budget reasoning unless this is "none".
    GM_CACHE_CONTROL  1/on/true/yes marks the system message as an Anthropic
                      prompt-cache breakpoint. Off by default, and the local
                      path should stay off: a local model server neither uses
                      nor needs it. Only turn it on when the endpoint forwards
                      cache_control to a provider that bills cached prefixes
                      (Anthropic via a proxy that passes the field through),
                      and read /usage to confirm cache_read is actually moving.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_URL = "http://localhost:20128"


class LLMError(Exception):
    """The endpoint failed, or answered with something that is not a chat reply."""


@dataclass
class Reply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    # Why generation stopped. "length" means the answer hit `max_tokens` and was
    # cut off mid-sentence, which for the DM is not a short answer: the JSON
    # directive line is the LAST thing the prompt asks for, so a truncated turn
    # arrives as narration with no directive in it at all. Nothing in the tree
    # read this before, so the turn was silently voided (the 2026-09-28 arbiter
    # run, 2629+ tokens at a 3000-token cap, finish_reason: length). Callers that
    # need a whole turn decide for themselves; a truncated advisor note is still
    # worth reading, so this reports rather than raises.
    finish_reason: str = "stop"
    # Prompt-cache accounting, when the endpoint reports it. Per turn, the input
    # actually billed is prompt + cache_read, because Anthropic excludes cached
    # tokens from input_tokens/prompt_tokens. `cache_totals` is the lifetime
    # aggregate and additionally counts `cache_created`, which is a one-off.
    cache_read: int = 0
    cache_created: int = 0


@dataclass
class Models:
    dm: str = "dm-local"
    advisor: str = "dm-advisor"
    council: str = "dm-council"
    fast: str = ""                 # enemy picks and summaries; empty means dm

    def __post_init__(self):
        self.fast = self.fast or self.dm

    @classmethod
    def from_env(cls) -> "Models":
        return cls(os.environ.get("GM_DM_MODEL") or cls.dm,
                   os.environ.get("GM_ADVISOR_MODEL") or cls.advisor,
                   os.environ.get("GM_COUNCIL_MODEL") or cls.council,
                   os.environ.get("GM_FAST_MODEL") or "")


def _http(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"HTTP {e.code} from {url}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise LLMError(f"cannot reach {url}: {e}") from e


# Prompt caching, OFF by default. GM_CACHE_CONTROL=1 turns it on.
#
# It is opt-in rather than auto-detected because the endpoint this project
# actually uses cannot be sniffed: GM_LLM_URL points at OmniRoute
# (http://localhost:20128), which proxies upstream to whatever the combo name
# resolves to -- including Anthropic. Matching on "api.anthropic.com" in the URL
# would therefore never fire here, and the honest question is not "which host is
# this" but "does this operator's proxy forward cache_control". A wrong guess
# either silently strips the markers (caching never happens and nobody knows why)
# or trips the proxy's schema validation on every single turn. So the operator
# answers it once, in the environment, and local Ollama keeps working untouched
# because it never turns this on.
CACHE_ENV = "GM_CACHE_CONTROL"


def caching_enabled() -> bool:
    return os.environ.get(CACHE_ENV, "").strip().lower() in ("1", "on", "true", "yes")


def apply_cache_control(messages: list) -> list:
    """Mark the system message as an ephemeral cache breakpoint.

    Returns a new list; the input is not mutated. A system message whose content
    is already a block array (the marker applied on a retry, say) is passed
    through untouched rather than double-wrapped, so calling this twice on the
    same messages cannot nest arrays inside each other.

    Only the system message is marked. The user message holds the campaign
    digest, the story summary and the recent turns, all of which change every
    turn, so a breakpoint there would be invalidated on the very next request and
    would report a 0% hit rate while looking correctly configured.
    """
    out = []
    for msg in messages:
        if msg.get("role") == "system" and isinstance(msg.get("content"), str):
            out.append({**msg, "content": [
                {"type": "text", "text": msg["content"],
                 "cache_control": {"type": "ephemeral"}}]})
        else:
            out.append(dict(msg))
    return out


class Client:
    def __init__(self, base_url=None, api_key=None, transport=None, usage_log=None,
                 timeout: float = 180):
        self.base_url = (base_url or os.environ.get("GM_LLM_URL") or DEFAULT_URL).rstrip("/")
        if api_key is None:
            api_key = os.environ.get("GM_LLM_KEY") or os.environ.get("OMNIROUTE_API_KEY", "")
        self.api_key = api_key
        self.transport = transport or _http
        self.usage_log = pathlib.Path(usage_log) if usage_log else None
        self.timeout = timeout
        self._lock = threading.Lock()

    def chat(self, model: str, messages: list, *, max_tokens: int = 600,
             temperature: float = 0.8, role: str = "dm", reasoning: str | None = None) -> Reply:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": model, "messages": apply_cache_control(messages) if caching_enabled()
                else messages, "max_tokens": max_tokens,
                "temperature": temperature, "stream": False}
        if reasoning:
            body["reasoning_effort"] = reasoning
        start = time.monotonic()
        data = self.transport(f"{self.base_url}/v1/chat/completions", body, headers, self.timeout)
        try:
            message = data["choices"][0]["message"]
            text = message["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected reply: {str(data)[:200]}") from e
        if not text.strip():
            # A blank answer with reasoning behind it is a budget problem, not a
            # model that had nothing to say. Say which, because the two need
            # opposite fixes and "the advisor was silent" is otherwise
            # indistinguishable from a working advisor that declined to comment.
            spent = int((data.get("usage") or {}).get("completion_tokens") or 0)
            thought = message.get("reasoning") or message.get("reasoning_content") or ""
            if thought or spent >= max_tokens:
                raise LLMError(
                    f"no answer: {spent}/{max_tokens} completion tokens went to "
                    f"reasoning, none to the answer. Raise max_tokens, or set "
                    f"reasoning_effort (GM_REASONING=none).")
        usage = data.get("usage") or {}
        try:
            finish = (data["choices"][0] or {}).get("finish_reason") or "stop"
        except (KeyError, IndexError, TypeError):
            finish = "stop"
        reply = Reply(text, data.get("model") or model, int(usage.get("prompt_tokens") or 0),
                      int(usage.get("completion_tokens") or 0), round(time.monotonic() - start, 2),
                      str(finish),
                      # Anthropic's native key is `input_tokens`; the OpenAI-compatible
                      # shim reports `prompt_tokens` and excludes cached tokens from
                      # both. Reading both is what makes the log honest about a
                      # provider that reports either, instead of reading zero on a
                      # cache that is in fact hitting.
                      int(usage.get("cache_read_input_tokens") or 0),
                      int(usage.get("cache_creation_input_tokens") or 0))
        self._log(role, model, reply)
        return reply

    def _log(self, role: str, model: str, reply: Reply) -> None:
        """Log the requested combo name, not the upstream model, so totals group by tier."""
        if not self.usage_log:
            return
        row = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "model": model,
               "prompt_tokens": reply.prompt_tokens,
               "completion_tokens": reply.completion_tokens, "seconds": reply.seconds,
               "cache_read": reply.cache_read, "cache_created": reply.cache_created}
        with self._lock:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.usage_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")


# Known model families -> reasoning_effort. First substring match on the
# lowercased model name wins, so put specific names before general ones.
# Combo names (dm-local) say nothing about the model behind them and fall
# through to the default; set GM_REASONING for those.
REASONING_BY_MODEL = (
    ("qwen3.5", "none"),        # ignores /no_think, burns the budget reasoning
    ("qwen3", "none"),          # /no_think works, but thinking buys little here
    ("space-bunny", "medium"),  # needs some reasoning to produce a turn
    ("gpt-oss", "medium"),      # cannot turn reasoning off, so none is invalid
)
DEFAULT_REASONING = "none"      # safe: never starves the answer of its budget


def reasoning_for_model(model: str | None) -> str:
    name = (model or "").lower()
    for needle, setting in REASONING_BY_MODEL:
        if needle in name:
            return setting
    return DEFAULT_REASONING


def reasoning_from_env(model: str | None = None) -> str | None:
    """GM_REASONING if set (even to off), else the table's pick for `model`."""
    value = os.environ.get("GM_REASONING")
    if value is None:
        value = reasoning_for_model(model)
    value = value.strip().lower()
    return None if value in ("", "off") else value


def totals(path) -> list:
    """(role, model, calls, prompt_tokens, completion_tokens), in first-seen order."""
    path = pathlib.Path(path)
    if not path.exists():
        return []
    sums: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["role"], r["model"])
        calls, p, c = sums.get(k, (0, 0, 0))
        sums[k] = (calls + 1, p + r["prompt_tokens"], c + r["completion_tokens"])
    return [(role, model, *v) for (role, model), v in sums.items()]


def cache_totals(path) -> tuple:
    """(cache_read, cache_created, uncached_prompt), summed over every logged call.

    Separate from `totals()` rather than folded into it: `totals()` returns
    5-tuples that `play.py:_usage` and `tests/test_localdm_llm.py` both unpack
    positionally, so widening its rows would break both for a number that is
    zero on every local Ollama call anyway.

    `uncached_prompt` is the number that says whether caching is doing anything.
    A cache that reports a large `cache_read` while `prompt_tokens` stays high
    means the prefix is still being re-sent uncached, which is the
    `build_messages` bug this whole change exists to fix.
    """
    path = pathlib.Path(path)
    if not path.exists():
        return (0, 0, 0)
    read = created = prompt = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        read += int(r.get("cache_read") or 0)
        created += int(r.get("cache_created") or 0)
        prompt += int(r.get("prompt_tokens") or 0)
    return (read, created, prompt)
