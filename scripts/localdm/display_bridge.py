"""display_bridge.py: mirror the local DM's narration to the Flask display.

Optional and best effort. It speaks display/send.py's /chunk contract
({"campaign": name} to register, {"text": ...} per narration chunk, the LAN
token in X-DND-Token) but reads its port from the environment, because send.py
always posts to 5001 and another display may own that port.

Endpoint, first match wins:
    --display-url URL             (play.py)
    GM_DISPLAY_URL                e.g. http://localhost:5051
    GM_DISPLAY_PORT               http://localhost:<port>
    display/.port                 a port number, if a local launcher wrote one
    http://localhost:5001         display/.scheme picks https when present

A display that is down never stops play: one warning on stderr, then silence,
and no new attempt for RETRY_AFTER seconds so a host that drops packets does
not stall every turn. The LAN token goes to local hosts only.
Only text the caller passes to narrate() is sent; the caller keeps GM notes,
engine lines and errors out of it.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DISPLAY_DIR = pathlib.Path(__file__).resolve().parents[2] / "display"
DEFAULT_PORT = 5001
TIMEOUT = 2.0
RETRY_AFTER = 30.0          # seconds without a send after a failure
CHUNK_LIMIT = 3500            # send.py splits text bodies above this many characters
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def resolve_url(cli_url: str = "", env=None, display_dir=DISPLAY_DIR) -> str:
    """The display's base URL, with no trailing slash."""
    env = os.environ if env is None else env
    display_dir = pathlib.Path(display_dir)
    url = (cli_url or env.get("GM_DISPLAY_URL", "")).strip()
    if url:
        return url.rstrip("/")
    port = env.get("GM_DISPLAY_PORT", "").strip()
    if not port.isdigit():
        port = _read(display_dir / ".port")
    if not port.isdigit():
        port = str(DEFAULT_PORT)
    scheme = "https" if _read(display_dir / ".scheme") == "https" else "http"
    return f"{scheme}://localhost:{port}"


def split_paragraphs(text: str, limit: int = CHUNK_LIMIT) -> list:
    """send.py's split: whole paragraphs per chunk while they fit."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for p in text.split("\n\n"):
        candidate = f"{cur}\n\n{p}" if cur else p
        if len(candidate) <= limit:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        if len(p) > limit:
            chunks += [p[i:i + limit] for i in range(0, len(p), limit)]
        else:
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


class Display:
    """Registers the campaign, then posts each turn's narration as one event."""

    def __init__(self, url: str, campaign: str, *, token=None, timeout: float = TIMEOUT,
                 display_dir=DISPLAY_DIR, err=None):
        self.url = url.rstrip("/")
        self.campaign = campaign
        parts = urllib.parse.urlsplit(self.url)
        # display/.token guards this machine's display; never hand it to another host.
        if parts.hostname not in _LOCAL_HOSTS:
            token = ""
        self.token = _read(pathlib.Path(display_dir) / ".token") if token is None else token
        self.timeout = timeout
        self.err = err or sys.stderr
        self.registered = False
        self.warned = False
        self.retry_at = 0.0
        self._ctx = None
        if parts.scheme == "https":           # the display's cert is self-signed (setup_tls.py)
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def _post(self, payload: dict, path: str = "/chunk") -> bool:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-DND-Token"] = self.token
        req = urllib.request.Request(f"{self.url}{path}", data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    return True
        except urllib.error.HTTPError as e:
            return self._warn(f"HTTP {e.code}")
        except Exception:                 # offline, refused, timeout, bad URL: never fatal
            pass
        return self._warn("not reachable")

    def _warn(self, why: str) -> bool:
        self.retry_at = time.monotonic() + RETRY_AFTER
        if not self.warned:
            self.warned = True
            print(f"(display {why} at {self.url}; narration stays in the terminal)",
                  file=self.err)
        return False

    def register(self) -> bool:
        self.registered = self._post({"campaign": self.campaign})
        return self.registered

    def push_party(self, players: list) -> bool:
        """Fill the sidebar and the Party input picker; a fresh display starts empty."""
        if not players or time.monotonic() < self.retry_at:
            return False
        return self._post({"players": players, "replace_players": True}, "/stats")

    def request_roll(self, character: str, modifier: int, label: str, dc: int,
                     wait: float = 180.0):
        """Ask the player to roll in the browser (Roll button or Phone Mode); the d20 total,
        or None if the display is down or nobody rolled in time."""
        if time.monotonic() < self.retry_at:
            return None
        body = {"character": character, "spec": "1d20", "modifier": modifier,
                "label": label, "dc": dc}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-DND-Token"] = self.token

        def call(url, data=None, method="GET"):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        try:
            rid = call(f"{self.url}/dice-request", json.dumps(body).encode("utf-8"), "POST")["request_id"]
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                time.sleep(1.0)
                st = call(f"{self.url}/dice-request/{rid}")
                if st.get("complete"):
                    for text in st.get("results", []):
                        m = re.search(r"=\s*(-?\d+)\s*(?:\W|$)", str(text))
                        if m:
                            return int(m.group(1))
                    return None
            call(f"{self.url}/dice-request/{rid}", method="DELETE")
        except Exception:
            pass
        return None

    def narrate(self, text: str) -> bool:
        """Send one turn's narration; registers first if startup could not."""
        text = (text or "").strip()
        if not text:
            return False
        if time.monotonic() < self.retry_at:
            return False
        if not self.registered and not self.register():
            return False
        ok = True
        for chunk in split_paragraphs(text):
            ok = self._post({"text": chunk}) and ok
        return ok


def from_args(campaign: str, *, url: str = "", disabled: bool = False, env=None):
    """A Display, or None when mirroring is switched off."""
    env = os.environ if env is None else env
    if disabled:
        return None
    return Display(resolve_url(url, env), campaign)
