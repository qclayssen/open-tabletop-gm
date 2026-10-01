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
                      summaries): medium (default), low or high to think harder,
                      none to switch reasoning off, or off to send no field at
                      all. Qwen3.5 ignores /no_think and spends its whole budget
                      reasoning, so a caller on it wants "none". The default is
                      the other way round on purpose: an endpoint backed by a
                      reasoning model rejects "none" AND "off" with a 400
                      ("Reasoning is mandatory for this endpoint"), which fails
                      every turn rather than one. See reasoning_from_env.
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
        body = {"model": model, "messages": messages, "max_tokens": max_tokens,
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
            finish = str((data.get("choices") or [{}])[0].get("finish_reason") or "")
            # Any blank content is an error, whatever the token count. The old
            # guard fired only when reasoning was present or the budget was
            # exhausted, so a blank reply at a PARTIAL budget (measured at
            # 513/600, finish_reason=length) fell through as a successful call
            # carrying no text: the turn rendered as nothing at all, with no
            # error anywhere. For a DM that is a player who types a question and
            # gets silence, which reads as the game ignoring them.
            raise LLMError(
                f"no answer in the reply: {spent}/{max_tokens} completion tokens, "
                f"finish_reason={finish or 'unknown'}"
                + (f", {len(thought)} of them reasoning" if thought else "")
                + ". The model returned empty content. Raise max_tokens, or LOWER "
                "reasoning_effort (GM_REASONING); if the endpoint mandates "
                "reasoning, raise GM_DM_MAX_TOKENS instead.")
        usage = data.get("usage") or {}
        try:
            finish = (data["choices"][0] or {}).get("finish_reason") or "stop"
        except (KeyError, IndexError, TypeError):
            finish = "stop"
        reply = Reply(text, data.get("model") or model, int(usage.get("prompt_tokens") or 0),
                      int(usage.get("completion_tokens") or 0), round(time.monotonic() - start, 2),
                      str(finish))
        self._log(role, model, reply)
        return reply

    def _log(self, role: str, model: str, reply: Reply) -> None:
        """Log the requested combo name, not the upstream model, so totals group by tier."""
        if not self.usage_log:
            return
        row = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "model": model,
               "prompt_tokens": reply.prompt_tokens,
               "completion_tokens": reply.completion_tokens, "seconds": reply.seconds}
        with self._lock:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.usage_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")


def reasoning_from_env() -> str | None:
    # Default "medium", not "none". "none" goes on the wire as
    # reasoning_effort: "none" (only ""/""off" are dropped), and endpoints
    # backed by a reasoning model reject that outright -- space-bunny-alpha
    # answers 400 "Reasoning is mandatory for this endpoint and cannot be
    # disabled", so every turn fails and the DM narrates nothing. Qwen3.5 is
    # the one model that wants "none", and it is a local model a caller can
    # point GM_REASONING at; a cloud reasoning model is the more common case
    # and it is the one that was broken by default.
    value = os.environ.get("GM_REASONING", "medium").strip().lower()
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
