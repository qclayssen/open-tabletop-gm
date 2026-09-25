"""llm.py: one chat call to an OpenAI-compatible endpoint (OmniRoute by default).

Model names are OmniRoute combo names by default, so which real model answers
(local Ollama, a free tier, paid Claude) is decided in OmniRoute, not here.

Environment:
    GM_LLM_URL        endpoint root, default http://localhost:20128 (OmniRoute)
    GM_LLM_KEY        bearer token; falls back to OMNIROUTE_API_KEY
    GM_DM_MODEL       every turn, enemy picks, summaries   (default dm-local)
    GM_ADVISOR_MODEL  escalations and triggers             (default dm-advisor)
    GM_COUNCIL_MODEL  /advise council                      (default dm-council)
    GM_FAST_MODEL     enemy picks and summaries            (default: GM_DM_MODEL)
    GM_REASONING      reasoning_effort sent on local-tier calls (dm, picks,
                      summaries): none (default), low, medium, high, or off to
                      send nothing. Qwen3.5 ignores /no_think and spends its
                      whole budget reasoning unless this is "none".
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
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected reply: {str(data)[:200]}") from e
        usage = data.get("usage") or {}
        reply = Reply(text, data.get("model") or model, int(usage.get("prompt_tokens") or 0),
                      int(usage.get("completion_tokens") or 0), round(time.monotonic() - start, 2))
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
    value = os.environ.get("GM_REASONING", "none").strip().lower()
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
