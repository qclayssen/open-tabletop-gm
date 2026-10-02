#!/usr/bin/env python3
"""
check_input.py — Non-blocking check for queued player input.

Drains the display companion's player input queue and prints any pending
actions to stdout, then exits. If the queue is empty, exits silently.

There is one queue: .input_queue, one "[Char]: text" line per action. Every
producer writes it (Party Input Send and Skip, the legacy POST /player-input,
and grid clicks via POST /combat/do), and every consumer takes it by atomic
rename through queue_claim (this script, wrapper.py, autorun_wait.py,
drain_queue.py), so each action reaches exactly one GM session.

This script claims .input_queue directly first, so it works with the display
down. It then POSTs /player-input/drain, which claims the same file
server-side and returns only what was sent in between, and clears the
display's pending indicators. Follows send.py's token/scheme pattern for auth
and TLS.

Output format (when non-empty):
  [CharName]: action text
  [CharName2]: action text

One line per character. Called at the start of each GM turn:
  python3 display/check_input.py
"""
import json
import os
import pathlib
import re
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import queue_claim  # (needs the sys.path line above it)

_DIR         = pathlib.Path(__file__).parent
_SCHEME_FILE = _DIR / ".scheme"
_SCHEME      = _SCHEME_FILE.read_text(encoding="utf-8").strip() if _SCHEME_FILE.exists() else "http"
_PORT_FILE   = _DIR / ".port"


def _display_port() -> int:
    raw = os.environ.get("GM_DISPLAY_PORT", "").strip()
    if not raw:
        try:
            raw = _PORT_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
    try:
        return int(raw or "5001")
    except ValueError:
        return 5001


_PORT = _display_port()
DRAIN_URL    = f"{_SCHEME}://localhost:{_PORT}/player-input/drain"
TOKEN_FILE   = _DIR / ".token"
# The shared queue claimed by the drain endpoint, wrapper.py, and autorun_wait.py.
READY_FILE   = _DIR / ".input_queue"
CONSUMED_URL = f"{_SCHEME}://localhost:{_PORT}/queue/consumed"
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


def _take_ready_queue(token: str) -> list:
    """Read and delete .input_queue, then clear the display's "Queued" badge."""
    # Claim the file before reading it. The app truncates and rewrites it
    # atomically, so a read-then-unlink loses any action written in the gap.
    # This used to catch only OSError, which meant a decode failure after the
    # claim stranded the actions in the .taken file; the shared helper restores
    # them instead, and a decode failure now reports as an empty drain.
    text, delivered = queue_claim.claim_and_read(READY_FILE)
    if not delivered:
        return []
    entries = []
    for line in text.splitlines():
        m = re.match(r"\[([^\]]+)\]:\s*(.*)", line.strip())
        if m and m.group(2):
            entries.append({"character": m.group(1), "text": m.group(2)})
    try:
        req = urllib.request.Request(CONSUMED_URL, method="POST",
                                     headers={"X-DND-Token": token, "Content-Length": "0"})
        urllib.request.urlopen(req, context=_SSL_CTX, timeout=2).close()
    except Exception:
        pass                    # the badge is cosmetic; the actions were delivered
    return entries



def utf8_stdout() -> None:
    """Print UTF-8 whatever the console codepage. On Windows the GM's shell reads
    stdout through a cp1252 pipe, where "→" in a roll would raise."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def main() -> None:
    utf8_stdout()
    token = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""
    # Claim the shared queue directly first, so a refused or unreachable drain
    # never strands an action. The rename makes this safe beside wrapper.py.
    ready = _take_ready_queue(token)
    # Then the HTTP drain, which claims the same file server-side and so only
    # returns what was sent in the gap. Any failure here loses nothing: whatever
    # is still queued stays in .input_queue for the next call.
    try:
        req = urllib.request.Request(
            DRAIN_URL, method="POST",
            headers={"X-DND-Token": token, "Content-Length": "0"},
        )
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=2) as resp:
            entries = json.loads(resp.read())
        _print_entries(ready + entries)
        return
    except urllib.error.HTTPError as e:
        print(f"check_input: display refused the drain ({e.code})", file=sys.stderr)
    except urllib.error.URLError:
        pass                    # could not connect: the display is not running
    except Exception as e:      # read timeout, bad JSON
        print(f"check_input: drain failed ({e.__class__.__name__})", file=sys.stderr)
    _print_entries(ready)


if __name__ == "__main__":
    main()
