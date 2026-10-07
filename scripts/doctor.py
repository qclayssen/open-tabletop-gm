#!/usr/bin/env python3
"""Startup doctor: one bounded table of configuration checks.

Every row that is not ok prints a cause and a fix. A passing check prints
nothing. The whole run has one budget and retries a cold endpoint once.

    python3 scripts/doctor.py

Stdlib only. This does not change GM_REASONING's default. play.py is the
other caller, and it is wired separately.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

BUDGET_S = 20.0
PER_CALL_S = 8.0
_RETRY = ("timeout", "unreachable")

_START = None
_DISPLAY = None


class Row:
    def __init__(self, name: str, status: str, cause: str = "", fix: str = ""):
        self.name = name
        self.status = status
        self.cause = cause
        self.fix = fix


def format_report(rows: list) -> str:
    lines = []
    for row in rows:
        if row.status == "ok":
            continue
        label = "WARN" if row.status == "warn" else "FAIL"
        lines.append(f"{label} {row.name}")
        lines.append(f"  cause: {row.cause}")
        lines.append(f"  fix: {row.fix}")
    return "\n".join(lines)


def run(env=None, probe=None, budget: float = BUDGET_S, per_call: float = PER_CALL_S,
        now=None, python_version=None, flask_present=None, campaign_root=None,
        srd_message=None) -> list:
    """Return every check. `probe(role, model, timeout)` returns a label or None."""
    env = os.environ if env is None else env
    clock = now or time.monotonic
    if probe is None:
        def probe(role, model, timeout, _env=env):
            return _live_probe(_env, role, model, timeout)
    deadline = clock() + budget
    rows = [
        _python_row(python_version),
        _flask_row(flask_present),
        _campaign_row(campaign_root),
        _srd_row(srd_message),
    ]
    dm, advisor = _models(env)
    pairs = [("dm", dm)]
    if advisor != dm:
        pairs.append(("advisor", advisor))
    for role, model in pairs:
        rows.append(_endpoint_row(role, model, probe, deadline, clock, per_call))
    if not any(row.name == "reasoning" and row.status != "ok" for row in rows):
        rows.append(_reasoning_config_row(env))
    return rows


def main(argv=None, **kwargs) -> int:
    rows = run(**kwargs)
    text = format_report(rows)
    if text:
        print(text)
    return 1 if any(row.status == "fail" for row in rows) else 0


def _python_row(version) -> Row:
    err = _start().check_python(version)
    if not err:
        return Row("python", "ok")
    return Row("python", "fail", err, "Install Python 3.10 or newer and rerun python3 scripts/doctor.py.")


def _flask_row(present) -> Row:
    if present is None:
        err = _start().check_flask()
    elif present:
        err = None
    else:
        err = "Flask is not installed."
    if not err:
        return Row("flask", "ok")
    return Row("flask", "fail", err, "pip3 install flask")


def _campaign_row(root) -> Row:
    if root is None:
        from paths import campaigns_dir
        root = campaigns_dir()
    root = pathlib.Path(root)
    if not root.is_dir():
        return Row(
            "campaigns", "fail",
            f"Campaign root {root} does not exist.",
            "Create that directory, or set GM_CAMPAIGN_ROOT to the folder that contains campaigns/.",
        )
    return Row("campaigns", "ok")


def _srd_row(message) -> Row:
    if message is None:
        message = _display().srd_problem()
    if not message:
        return Row("srd", "ok")
    return Row("srd", "fail", message, "python3 systems/dnd5e/build_srd.py --no-fvtt")


def _endpoint_row(role, model, probe, deadline, clock, per_call) -> Row:
    setting = "GM_DM_MODEL" if role == "dm" else "GM_ADVISOR_MODEL"
    name = "dm-endpoint" if role == "dm" else "advisor-endpoint"
    label = _probe_with_retry(probe, role, model, deadline, clock, per_call)
    if label is None:
        return Row(name, "ok")
    if label == "no-answer-budget":
        name = "reasoning"
    cause, fix = _remedy(label, setting)
    return Row(name, "fail", cause, fix)


def _probe_with_retry(probe, role, model, deadline, clock, per_call):
    label = "timeout"
    for attempt in (1, 2):
        left = deadline - clock()
        if left <= 0.05:
            return "timeout"
        timeout = min(per_call, left)
        label = _call_probe(probe, role, model, timeout)
        if label not in _RETRY:
            return label
        if attempt == 2:
            return label
    return label


def _call_probe(probe, role, model, timeout):
    box = {}

    def target():
        try:
            box["label"] = probe(role, model, timeout)
        except BaseException as exc:
            box["exc"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return "timeout"
    if "exc" in box:
        from localdm.llm import _classify_error
        return _classify_error(box["exc"])
    return box.get("label")


def _live_probe(env, role, model, timeout):
    from localdm.llm import Client, _classify_error, reasoning_from_env
    base = (env.get("GM_LOCAL_URL") or env.get("GM_LLM_URL") or "").strip() or None
    key = env.get("GM_LLM_KEY") or env.get("OMNIROUTE_API_KEY") or ""
    client = Client(base_url=base, api_key=key, timeout=timeout)
    value = env.get("GM_REASONING")
    reasoning = reasoning_from_env(model) if value is None else _reasoning_value(value)
    try:
        client.chat(
            model, [{"role": "user", "content": "ping"}],
            max_tokens=16, temperature=0, role=role, reasoning=reasoning,
        )
    except Exception as exc:
        return _classify_error(exc)
    return None


def _reasoning_config_row(env) -> Row:
    """Warn when a model cannot run with the default and GM_REASONING was not set.

    The default stays `none`. Reporting is the remedy; existing sessions are
    not moved.
    """
    value = env.get("GM_REASONING")
    if value is not None and value.strip():
        return Row("reasoning", "ok")
    model = (env.get("GM_DM_MODEL") or "")
    from localdm.llm import REASONING_BY_MODEL
    for needle, setting in REASONING_BY_MODEL:
        if needle in model.lower() and setting not in ("", "none", "off"):
            return Row(
                "reasoning", "warn",
                f"GM_REASONING is unset and {model} requires reasoning.",
                f"Set GM_REASONING={setting}.",
            )
    return Row("reasoning", "ok")


def _reasoning_value(value: str):
    value = value.strip().lower()
    return None if value in ("", "off", "none") else value


def _models(env) -> tuple:
    dm = (env.get("GM_DM_MODEL") or "dm-local").strip()
    advisor = (env.get("GM_ADVISOR_MODEL") or "dm-advisor").strip()
    return dm, advisor


def _remedy(label: str, setting: str) -> tuple:
    if label in ("http-401", "http-403"):
        return ("The endpoint refused the key.",
                "Set GM_LLM_KEY to a key this endpoint accepts.")
    if label == "http-404":
        return ("This model name is not served here.",
                f"Set {setting} to a model this endpoint lists.")
    if label == "http-429":
        return ("The endpoint rate-limited the probe. Quota was not measured.",
                "Wait and retry. This check cannot read a balance.")
    if label == "timeout":
        return ("The probe timed out before the endpoint answered.",
                "Point GM_LOCAL_URL at the direct endpoint if this URL is a queue.")
    if label == "unreachable":
        return ("Nothing accepted a connection at the endpoint.",
                "Start the server, or set GM_LLM_URL to the address that is listening.")
    if label == "no-answer-budget":
        return ("The probe's reply budget was spent on reasoning.",
                "Set GM_REASONING to a mode this model can answer under.")
    if label == "bad-json":
        return ("The endpoint answered with something that is not a chat reply.",
                "Point GM_LLM_URL at a chat-completions endpoint.")
    if label == "empty-answer":
        return ("The endpoint returned an empty answer.",
                f"Try another {setting}.")
    return (f"The endpoint failed ({label}).",
            "Rerun python3 scripts/doctor.py once the endpoint is reachable.")


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _start():
    global _START
    if _START is None:
        _START = _load("doctor_start", ROOT / "start.py")
    return _START


def _display():
    global _DISPLAY
    if _DISPLAY is None:
        _DISPLAY = _load("doctor_display_check", ROOT / "display" / "display_check.py")
    return _DISPLAY


if __name__ == "__main__":
    sys.exit(main())
