#!/usr/bin/env python3
"""
check_input.py — Non-blocking check for queued player input.

Drains the display companion's player input queue and prints any pending
actions to stdout, then exits. If the queue is empty, exits silently.

Primary path — HTTP drain endpoint (display running):
  POSTs to /player-input/drain, which clears both the in-memory queue and
  the persisted .input_queue file atomically. Follows send.py's token/scheme
  pattern for auth and TLS.

Fallback path — file read (only when the display cannot be reached at all):
  Reads player_input.json (the file the app persists that same queue to)
  directly and writes [] to clear it. Useful after a display crash or when
  running without the companion.

Output format (when non-empty):
  [CharName]: action text
  [CharName2]: action text

One line per character. Called at the start of each GM turn:
  python3 display/check_input.py
"""
import json
import os
import pathlib
import ssl
import sys
import urllib.error
import urllib.request

_DIR         = pathlib.Path(__file__).parent
_SCHEME_FILE = _DIR / ".scheme"
_SCHEME      = _SCHEME_FILE.read_text(encoding="utf-8").strip() if _SCHEME_FILE.exists() else "http"
DRAIN_URL    = f"{_SCHEME}://localhost:5001/player-input/drain"
TOKEN_FILE   = _DIR / ".token"
# The queue the drain endpoint serves; gm-display-app.py persists it here.
# (.input_queue is a different, plain-text file owned by wrapper.py.)
QUEUE_FILE   = _DIR / "player_input.json"
NARRATION_TARGET = _DIR / "narration_target"   # set by the display's Narration slider
ROLL_PREFS       = _DIR / "roll_prefs.json"    # per-character roll overrides (Settings → Rolls)

_SSL_CTX = None
if _SCHEME == "https":
    _SSL_CTX = ssl.create_default_context()
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode    = ssl.CERT_NONE


def _narration_directive() -> str:
    """A bracketed length directive the GM honors this turn, or '' if unset."""
    try:
        if NARRATION_TARGET.exists():
            n = NARRATION_TARGET.read_text(encoding="utf-8").strip()
            if n.isdigit() and int(n) > 0:
                return (f"[[Narration length for this turn: aim for ~{n} words. "
                        f"The table set this — keep it concise; do not pad.]]")
    except Exception:
        pass
    return ""


def _roll_directives() -> str:
    """One [[<Char> roll mode: …]] line per per-character override, or '' if none."""
    try:
        if ROLL_PREFS.exists():
            prefs = json.loads(ROLL_PREFS.read_text(encoding="utf-8"))
            lines = [f"[[{c} roll mode: {m}]]" for c, m in prefs.items()
                     if m in ("auto", "players")]
            return "\n".join(lines)
    except Exception:
        pass
    return ""


def _print_entries(entries: list) -> None:
    if not entries:
        return
    for d in (_roll_directives(), _narration_directive()):
        if d:
            print(d)
    for entry in entries:
        char = entry.get("character", "Player")
        text = entry.get("text", "").strip()
        if text:
            print(f"[{char}]: {text}")


def main() -> None:
    # Primary: HTTP drain — clears memory and file atomically
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""
        req = urllib.request.Request(
            DRAIN_URL, method="POST",
            headers={"X-DND-Token": token, "Content-Length": "0"},
        )
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=2) as resp:
            entries = json.loads(resp.read())
        _print_entries(entries)
        return
    except urllib.error.HTTPError as e:
        # The display answered: it still owns the queue. Reading the file now
        # would deliver the same actions again on the next successful drain.
        print(f"check_input: display refused the drain ({e.code})", file=sys.stderr)
        return
    except urllib.error.URLError:
        pass                    # could not connect: the display is not running
    except Exception as e:      # read timeout, bad JSON: the app may already have drained
        print(f"check_input: drain failed ({e.__class__.__name__})", file=sys.stderr)
        return

    # Fallback: read queue file directly (display not running or unreachable)
    try:
        if QUEUE_FILE.exists():
            entries = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            QUEUE_FILE.write_text("[]", encoding="utf-8")   # clear without deleting: the app loads an empty queue on restart
            _print_entries(entries)
    except Exception:
        pass


if __name__ == "__main__":
    main()
