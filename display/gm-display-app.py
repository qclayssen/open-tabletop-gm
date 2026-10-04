"""
gm-display-app.py — GM display server

Receives text chunks from wrapper.py, detects scene context from keywords,
and pushes both to the browser via Server-Sent Events.

Endpoints:
    GET  /                   → serves index.html
    POST /chunk              → receives text chunk from wrapper.py
    POST /stats              → receives character/combat stat updates (merged, persisted)
    GET  /stream             → SSE stream to browser (text + scene + stats events)
    GET  /ping               → health check
    POST /clear              → wipe text log and broadcast clear event
    POST /player-input         → legacy: append an action to .input_queue
    POST /player-input/drain   → claim .input_queue and return it (check_input.py)
    POST /player-input/send    → send an action straight to the DM-gated queue
    POST /player-input/recall  → pull a not-yet-consumed action back out
    POST /player-input/skip    → skip a character's turn (sends a skip entry)
    GET  /srd-lookup           → look up a spell/item/feature/condition by name
"""

import hmac
import json
import pathlib
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
from collections import deque
from typing import Optional
from flask import (Flask, Response, request, render_template, jsonify,
                   send_from_directory, redirect)

_DISPLAY_DIR  = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR    = os.path.dirname(_DISPLAY_DIR)
SCRIPTS_DIR   = os.path.join(_SKILL_DIR, "scripts")
# The narration replay log. GM_TEXT_LOG_FILE moves it, which is what lets a
# test run its own display without writing to the repo: this file is
# gitignored runtime state, and a leftover one is replayed into every
# display that connects afterwards.
LOG_FILE      = os.environ.get("GM_TEXT_LOG_FILE") or os.path.join(_DISPLAY_DIR, "text_log.json")
_LOG_FALLBACK = LOG_FILE

# SRD lookup module — degrades silently if dataset not built
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
try:
    import lookup as _lookup
    _SRD_AVAILABLE = True
except Exception:
    _lookup = None          # type: ignore
    _SRD_AVAILABLE = False

from paths import (
    campaigns_dir as _campaigns_dir,
    campaign_path as _campaign_path,
    find_campaign as _find_campaign,
    campaign_system as _campaign_system,
    characters_dir as _characters_dir,
)


def _campaign_dir_for_name(name: str):
    """Resolve a display campaign name as one safe component under campaigns/."""
    if not isinstance(name, str) or not name or "\x00" in name or name in (".", ".."):
        raise ValueError("invalid campaign name")
    if "/" in name or "\\" in name:
        raise ValueError("invalid campaign name")
    return _campaign_path(_campaigns_dir(), name)


def _find_display_campaign(name: str):
    """Resolve a validated campaign name using the shared campaign lookup."""
    _campaign_dir_for_name(name)
    return _find_campaign(name)

# The map editor's terrain merge. The engine owns the rules; this only decides
# what a map file looks like, and it lives in scripts/ so the CLI and the tests
# can reach it without going through Flask.
from tactics import mapeditor as _mapeditor

# Audio module — degrades silently if numpy not installed
import sys as _sys
if _DISPLAY_DIR not in _sys.path:
    _sys.path.insert(0, _DISPLAY_DIR)
import queue_claim  # (needs the sys.path line above it)
try:
    import audio as _audio
    _audio.init()
except Exception:
    _audio = None   # type: ignore

# TTS module — degrades silently if Gemini API key not configured.
# See docs/SKILL-tts.md for setup.
try:
    import tts as _tts
except Exception:
    _tts = None   # type: ignore


def _apply_campaign_sfx_languages() -> None:
    """Read sfx_languages from the active campaign's state.md Session Flags.

    state.md line shape:  `sfx_languages: en,zh,es`
    Takes precedence over GM_SFX_LANGUAGES env var; both fall back to
    English-only if neither is set.
    """
    if _audio is None:
        return
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if not camp:
            return
        state_md = _find_display_campaign(camp) / "state.md"
        if not state_md.exists():
            return
        text = state_md.read_text(errors="replace", encoding="utf-8")
    except (OSError, ValueError):
        return
    m = re.search(r"^\s*sfx_languages:\s*([\w,\s\-]+)$", text, re.MULTILINE)
    if not m:
        return
    langs = [l.strip() for l in m.group(1).split(",") if l.strip()]
    valid = [l for l in langs if l in _audio.available_languages()]
    if valid:
        _audio.set_sfx_languages(valid)


HELP_LOCK     = os.path.join(_DISPLAY_DIR, ".help-lock")
CAMP_FILE     = os.path.join(_DISPLAY_DIR, ".campaign")
# The roster the sidebar is built from. GM_STATS_FILE moves it, for the same
# reason as GM_TEXT_LOG_FILE above: it is gitignored runtime state that every
# display loads at startup and replays into its sidebar.
STATS_FILE    = os.environ.get("GM_STATS_FILE") or os.path.join(_DISPLAY_DIR, "stats.json")
TOKEN_FILE    = os.path.join(_DISPLAY_DIR, ".token")
# Port override so a second display (tests, a demo) can run beside a live one.
# The tactics engine honours the same variable when it pushes updates.
# The GM scripts find a non-default port in display/.port, which only
# start-display.sh writes: a test display must not take over the live one.
_PORT         = int(os.environ.get("GM_DISPLAY_PORT", "5001") or 5001)
TRIGGER_FILE  = os.path.join(_DISPLAY_DIR, ".input_trigger")
QUEUE_FILE    = os.path.join(_DISPLAY_DIR, ".input_queue")
NARRATION_TARGET = os.path.join(_DISPLAY_DIR, "narration_target")  # set by display's Narration slider
ROLL_PREFS_FILE  = os.path.join(_DISPLAY_DIR, "roll_prefs.json")   # per-character roll overrides

_apply_campaign_sfx_languages()

# ─── LAN / TLS mode ───────────────────────────────────────────────────────────
# Pass --lan to bind on 0.0.0.0 and protect write endpoints with a token.
# Pass --tls (requires --lan) to enable HTTPS with a self-signed cert.
# Without --lan the server binds to localhost only; no token is required.

_LAN_MODE: bool = "--lan" in sys.argv
_TLS_MODE: bool = "--tls" in sys.argv
if _LAN_MODE:
    sys.argv.remove("--lan")   # prevent Flask from seeing an unknown flag
if _TLS_MODE:
    sys.argv.remove("--tls")


def _get_or_create_token() -> str:
    """Load or generate the LAN token. Upgrades short legacy tokens to 64-char."""
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
        if len(token) >= 48:   # 48+ chars = already long enough
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_hex(32)   # 64-char hex — brute force infeasible
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


_lan_token: Optional[str] = _get_or_create_token() if _LAN_MODE else None


# ─── Rate limiting ────────────────────────────────────────────────────────────
# Simple in-process sliding window: max 20 write requests per IP per minute.
# Prevents spam injection and brute-force token guessing on write endpoints.

import time as _time

_rate_buckets: dict[str, list] = {}
_rate_lock = threading.Lock()
_RATE_WINDOW = 60    # seconds
_RATE_MAX    = 20    # requests per window per IP


def _rate_ok(ip: str) -> bool:
    now = _time.time()
    with _rate_lock:
        bucket = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_WINDOW]
        if len(bucket) >= _RATE_MAX:
            return False
        bucket.append(now)
        _rate_buckets[ip] = bucket
    return True


# ─── Input validation helpers ─────────────────────────────────────────────────

# Allow ASCII printable plus letter ranges from every script in scope for the
# 24-locale i18n expansion: Latin Extended A/B (for é ñ ö ć ş etc.), Greek,
# Cyrillic (Russian, Ukrainian), Hebrew, Arabic, Devanagari (Hindi, Marathi),
# Bengali, Tamil, Telugu, Thai, Vietnamese diacritics, all CJK ranges,
# Hiragana, Katakana, Hangul, Halfwidth/Fullwidth.
_PRINTABLE    = re.compile(
    "[^"
    "\x20-\x7E"
    " -ɏ"             # Latin-1 + Latin Extended A/B
    "Ͱ-Ͽ"             # Greek
    "Ѐ-ӿ"             # Cyrillic
    "֐-׿"             # Hebrew
    "؀-ۿ"             # Arabic
    "ݐ-ݿ"             # Arabic Supplement
    "ऀ-ॿ"             # Devanagari
    "ঀ-৿"             # Bengali
    "஀-௿"             # Tamil
    "ఀ-౿"             # Telugu
    "฀-๿"             # Thai
    "Ḁ-ỿ"             # Latin Extended Additional (Vietnamese)
    "　-〿"             # CJK Symbols
    "぀-ゟ"             # Hiragana
    "゠-ヿ"             # Katakana
    "㐀-䶿"             # CJK Ext A
    "一-鿿"             # CJK Unified
    "가-힯"             # Hangul
    "＀-￯"             # Halfwidth / Fullwidth
    "]"
)
_SHELL_CHARS  = re.compile(r'[$`\\;|&><()\[\]{}!]')
# Unicode \w covers letters from all scripts above.
_CHAR_NAME_RE = re.compile(r"^\w[\w '\-]{0,48}\w$|^\w{1,2}$", re.UNICODE)


def _sanitize_input(text: str) -> str:
    """Strip control chars and shell metacharacters from player input text."""
    text = _SHELL_CHARS.sub("", text)
    text = _PRINTABLE.sub("", text)
    return text[:500].strip()


def _char_ok(name: str, known: set) -> bool:
    """Return True if character name is syntactically valid and in the party."""
    if not _CHAR_NAME_RE.match(name):
        return False
    if known and name not in known and name != "Everybody":
        return False
    return True


# ─── Device approval system ───────────────────────────────────────────────────
# Each browser generates a UUID device ID (localStorage). On first input attempt
# from an unseen LAN device, the request is held and the DM sees an Approve/Deny
# card on the display. Localhost is auto-approved. Denied devices are blocked for
# the session.

_approved_devices: set[str]       = set()
_denied_devices:   set[str]       = set()
_pending_devices:  dict[str, dict] = {}  # device_id -> {ip, first_seen}
_devices_lock = threading.Lock()

DEVICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".approved_devices.json")


def _load_approved_devices() -> None:
    """Load persisted approved device IDs from disk at startup."""
    global _approved_devices
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            ids = json.load(f)
        if isinstance(ids, list):
            with _devices_lock:
                _approved_devices = set(ids)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[display] warning: could not load approved devices: {e}", file=sys.stderr)


def _persist_approved_devices() -> None:
    """Write approved device IDs to disk. Must be called WITHOUT _devices_lock held."""
    with _devices_lock:
        ids = list(_approved_devices)
    try:
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(ids, f)
    except Exception as e:
        print(f"[display] warning: could not persist approved devices: {e}", file=sys.stderr)


_load_approved_devices()


# A casual home-LAN game doesn't need a per-device approval gate — it's friction
# (every phone sits on "Awaiting approval" until the GM taps a card). Default:
# trust any device that can already reach the server. Set GM_REQUIRE_APPROVAL=1
# to restore the approve/deny gate (e.g. on an untrusted/shared network).
_REQUIRE_APPROVAL = os.environ.get("GM_REQUIRE_APPROVAL", "").strip().lower() in ("1", "true", "yes", "on")


def _device_ok(device_id: str, ip: str) -> str:
    """Return 'approved', 'pending', or 'denied' for a given device."""
    if not device_id:
        return "denied"
    _need_persist = False
    with _devices_lock:
        if device_id in _approved_devices:
            return "approved"
        if device_id in _denied_devices:
            return "denied"
        # Auto-approve localhost always, and every reachable device unless the
        # approval gate is explicitly required.
        if not _REQUIRE_APPROVAL or ip in ("127.0.0.1", "::1"):
            _approved_devices.add(device_id)
            _need_persist = True
        elif device_id not in _pending_devices:
            # New LAN device with the gate on — hold and notify GM
            _pending_devices[device_id] = {
                "id":         device_id,
                "ip":         ip,
                "first_seen": _time.time(),
            }
            _broadcast({"device_request": {"id": device_id, "ip": ip}})
    if _need_persist:
        _persist_approved_devices()
        return "approved"
    return "pending"


# ─── Player input send system ────────────────────────────────────────────────
# Players type an action on the display companion UI and tap Send. It is
# appended to QUEUE_FILE (.input_queue) straight away — no staging, no Ready
# step, no "wait for N players" threshold. The DM still gates *when* the
# actions reach the model: wrapper.py injects .input_queue on the next Enter,
# check_input.py drains it at the start of a GM turn. Grid clicks (/combat/do)
# land in the same file, so there is one queue for every front-end.

_sent: dict[str, dict] = {}     # {char_name: {text, timestamp}} — sent log
_sent_lock = threading.Lock()
_queue_lock = threading.Lock()  # serialises read-modify-write of QUEUE_FILE
_expected_count = 1             # updated when stats arrive; min 1
# Accepted for compatibility with `push_stats --autorun-threshold`, but no
# longer gates anything: sends reach the queue immediately, so there is no
# "wait for N players" condition left to satisfy.
_autorun_threshold: Optional[int] = None

# Tracks which character names are currently sitting in .input_queue waiting
# for the DM to press Enter. Set when queue is written, cleared when wrapper
# POSTs /queue/consumed after injection. Persists through page reloads via SSE
# initial data and is broadcast to all connected clients on change.
_queue_status: list = []
_queue_status_lock = threading.Lock()

# Last autorun cycle broadcast — replayed on SSE reconnect so late-joining
# clients start the countdown from the correct elapsed position.
# Cleared when autorun_waiting=false (turn resolved or autorun disabled).
_autorun_cycle: Optional[dict] = None
_autorun_cycle_lock = threading.Lock()


def _normalize_slot(slot: dict) -> None:
    """Coerce a spell-slot entry to the canonical {used, max} shape in place.

    Tolerates legacy/alt payloads that use `remaining` instead of `used`.
    Without this, _slot_use/_slot_restore raise KeyError on a slot stored
    under the alt schema (e.g. after a long-rest --spell-slots full-replace).
    """
    if "used" in slot:
        return
    mx = slot.get("max", 0)
    if "remaining" in slot:
        slot["used"] = max(mx - int(slot.get("remaining", 0)), 0)
    else:
        slot["used"] = 0


def _sent_snapshot() -> dict:
    """Return a serialisable copy of the sent log."""
    return {k: {"text": v["text"]} for k, v in _sent.items()}


# A grid click's outcome, queued by /combat/do. The engine has already applied
# it, so it is a fact for the GM to narrate rather than a request: a later Send
# never replaces it and Recall never removes it.
_GRID_PREFIX = "(grid) "


def _recallable_line(line: str, character: Optional[str] = None) -> Optional[str]:
    """The character on a `[name]: text` line a player may still revise or
    recall, or None for a grid outcome or a line that is not an action."""
    m = re.match(r"^\[([^\]]+)\]:\s?(.*)$", line)
    if not m or m.group(2).startswith(_GRID_PREFIX):
        return None
    if character is not None and m.group(1) != character:
        return None
    return m.group(1)


def _queue_append(char_names: dict[str, str], replace: bool = True) -> bool:
    """Write `[char]: text` lines into .input_queue, one line per character.

    .input_queue is the only player-input queue. Every producer writes it and
    every consumer claims it through queue_claim, so an action reaches whichever
    GM front-end is running (check_input.py, wrapper.py, autorun_wait.py,
    drain_queue.py, or POST /player-input/drain) exactly once.

    Sends arrive per-character and independently, so this must not truncate the
    file — two players tapping Send in the same second both have to reach the
    DM. With `replace`, a character sending again REPLACES their own pending
    line instead of adding a second one, so a player who revises an action never
    leaves the DM holding both the old and new version. Grid outcomes are never
    replaced, and are written with `replace=False` so a move and then an attack
    both reach the GM.

    The whole read-modify-write runs under _queue_lock so concurrent sends
    can't clobber each other, and writes via tmp+os.replace because
    check_input.py may move .input_queue away at any moment; a half-written
    file it moved would lose the action.

    Returns True if the queue was written.
    """
    with _queue_lock:
        try:
            existing: list[str] = []
            if os.path.exists(QUEUE_FILE):
                with open(QUEUE_FILE, encoding="utf-8") as f:
                    existing = f.read().splitlines()
            kept = existing
            if replace:
                kept = [ln for ln in existing if _recallable_line(ln) not in char_names]
            content = "\n".join([*kept, *(f"[{c}]: {t}" for c, t in char_names.items())])
            tmp = QUEUE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            os.replace(tmp, QUEUE_FILE)
            return True
        except Exception:
            return False


def _queue_remove(character: str) -> bool:
    """Drop `[character]: ...` lines from .input_queue. Used by recall.

    Best-effort: if the DM already consumed the queue there is nothing to pull
    back, and the caller treats that as a no-op rather than an error.
    """
    with _queue_lock:
        try:
            if not os.path.exists(QUEUE_FILE):
                return False
            with open(QUEUE_FILE, encoding="utf-8") as f:
                lines = f.read().splitlines()
            kept = [ln for ln in lines if _recallable_line(ln, character) is None]
            if len(kept) == len(lines):
                return False
            if not kept:
                os.unlink(QUEUE_FILE)
                return True
            tmp = QUEUE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(kept) + "\n")
            os.replace(tmp, QUEUE_FILE)
            return True
        except Exception:
            return False


def _queued_characters() -> set:
    """Names that currently have a recallable `[name]: ...` line in .input_queue.

    Grid outcomes do not count: they are not in the sent log and cannot be
    recalled, so they must not keep a QUEUED badge or a Recall alive.
    """
    with _queue_lock:
        try:
            with open(QUEUE_FILE, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            return set()
    return {name for name in map(_recallable_line, lines) if name}


def _reconcile_queue_state() -> bool:
    """Drop `_sent` / `_queue_status` entries that are no longer in .input_queue.

    The file is the source of truth. An action that left it (consumed by a poller
    that does not POST /queue/consumed, cleared, or lost) must not keep showing
    QUEUED with a live RECALL: that tells the player their action is safe when
    nothing holds it. Broadcasts when it changed anything. Returns True if so.
    """
    queued = _queued_characters()
    with _sent_lock:
        stale = [c for c in _sent if c not in queued]
        for c in stale:
            _sent.pop(c, None)
        snap = _sent_snapshot()
    with _queue_status_lock:
        keep = [c for c in _queue_status if c in queued]
        changed = bool(stale) or len(keep) != len(_queue_status)
        _queue_status[:] = keep
        status = list(_queue_status)
    if changed:
        _broadcast({"sent_log": snap, "queue_status": status})
    return changed


def _send(character: str, text: str) -> bool:
    """Append a sent action to the DM-gated queue and record it in the sent log.

    Returns True once the action is in .input_queue. The DM still decides *when*
    it reaches Claude — wrapper.py injects the queue on the next Enter. The
    sent log and QUEUED badge are only set after the file write succeeded, so a
    failed write never shows a recallable action that does not exist.
    """
    if not _queue_append({character: text}):
        return False
    with _sent_lock:
        _sent[character] = {"text": text, "timestamp": _time.time()}
        snap = _sent_snapshot()
    with _queue_status_lock:
        if character not in _queue_status:
            _queue_status.append(character)
        status = list(_queue_status)
    _broadcast({"sent_log": snap, "queue_status": status})
    return True


def _token_ok() -> bool:
    """Return True if the request carries the correct LAN token (or we're in localhost mode)."""
    if _lan_token is None:
        return True   # localhost mode — no token required
    provided = request.headers.get("X-DND-Token", "")
    return hmac.compare_digest(provided, _lan_token)


_ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
# Battle-map artwork. A map JSON may name a file in this directory; the display
# draws it under the terrain, so it has to be fetchable. Served by one route below,
# which, like the icons route, is confined to this directory.
_MAPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maps")
# Token portraits. A token may carry a `portrait` in the combat snapshot (see
# tactics/token_portraits.py), and the display draws it inside the token's shape.
# Same one-route-one-directory arrangement as the maps, for the same reason: the
# art is third-party and gitignored, so a clone without it still draws every
# token correctly from its side colour alone.
_TOKENS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens")

app = Flask(__name__)
# Flask derives its root from the module's __name__, which is only this file's
# directory when run as __main__. Every other path here is absolute for the same
# reason, and without it a test client loaded by file location 500s on
# render_template, which is how the map editor's page and / both fail to load.
app.root_path = _DISPLAY_DIR
app.template_folder = os.path.join(_DISPLAY_DIR, "templates")
app.config['TEMPLATES_AUTO_RELOAD'] = True


# ─── Security headers and cross-origin policy ────────────────────────────────
# The display is same-origin: its page, /stream and every POST come from one
# host. No cross-origin browser access is granted (no Access-Control-Allow-*
# headers), and a state-changing request from a foreign Origin is refused unless
# it carries the LAN token. Non-browser clients (send.py, push_stats.py, curl)
# send no Origin header and are unaffected. CSP is report-only for now.
_CSP_REPORT_ONLY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
)


def _origin_is_foreign() -> bool:
    origin = request.headers.get("Origin")
    if not origin or origin == "null":
        return origin == "null"
    from urllib.parse import urlsplit
    return urlsplit(origin).netloc.lower() != request.host.lower()


@app.before_request
def _refuse_foreign_origin_writes():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if _origin_is_foreign():
        if _lan_token is not None and _token_ok():
            return None
        return jsonify({"ok": False, "error": "cross-origin request refused"}), 403
    return None


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Content-Security-Policy-Report-Only", _CSP_REPORT_ONLY)
    return resp

# Wire audio broadcast after _broadcast is defined (see bottom of file)
# — done lazily via set_broadcast() called after app is created.

# ─── Scene definitions ────────────────────────────────────────────────────────
# Each scene: keywords (weighted — more = higher priority hit),
# gradient colors [top, bottom], accent color, particle type, display label.

SCENES: dict[str, dict] = {
    "tavern": {
        "keywords": [
            "tavern", "inn", "guttered", "common room", "hearth",
            "fireplace", "ale", "mead", "barkeep", "innkeeper",
            "candle", "tallow", "flagon", "stool", "bar",
        ],
        "colors": ["#1a0800", "#2e1400"],
        "accent": "#c8601a",
        "particles": "embers",
        "label": "The Inn",
    },
    "dungeon": {
        "keywords": [
            "dungeon", "corridor", "stone floor", "torch", "iron gate",
            "portcullis", "cell", "shackle", "pit", "dank",
        ],
        "colors": ["#080818", "#12082e"],
        "accent": "#6a3aaa",
        "particles": "dust",
        "label": "The Dungeon",
    },
    "mine": {
        "keywords": [
            "mine", "seam", "shaft", "tunnel", "ore", "pickaxe",
            "foreman", "deep seam", "ashstone", "cart", "vein",
        ],
        "colors": ["#0a0a0a", "#1a1008"],
        "accent": "#806040",
        "particles": "dust",
        "label": "The Mine",
    },
    "cave": {
        "keywords": [
            "cave", "cavern", "stalactite", "stalagmite", "underground",
            "grotto", "dripping", "echo", "subterranean",
        ],
        "colors": ["#0a1520", "#0a1030"],
        "accent": "#2060a0",
        "particles": "mist",
        "label": "The Cavern",
    },
    "forest": {
        "keywords": [
            "forest", "wood", "tree", "branch", "leaves", "undergrowth",
            "hollow wood", "canopy", "root", "bark", "moss", "fern",
            "thicket", "grove",
        ],
        "colors": ["#041008", "#081a04"],
        "accent": "#40a040",
        "particles": "leaves",
        "label": "The Forest",
    },
    "castle": {
        "keywords": [
            "castle", "rampart", "battlement", "keep", "parapet",
            "drawbridge", "moat", "throne", "great hall", "manor",
        ],
        "colors": ["#0e0e1a", "#1a1a2e"],
        "accent": "#8080c0",
        "particles": "dust",
        "label": "The Castle",
    },
    "mountain": {
        "keywords": [
            "mountain", "snow", "peak", "blizzard", "frost", "glacier",
            "avalanche", "ridge", "cliff", "altitude", "wind",
        ],
        "colors": ["#0a1020", "#1a2040"],
        "accent": "#a0c0e0",
        "particles": "snow",
        "label": "The Mountains",
    },
    "ocean": {
        "keywords": [
            "ocean", "sea", "ship", "wave", "sailor", "port", "harbour",
            "dock", "tide", "storm", "mast", "hull", "water",
        ],
        "colors": ["#000d1a", "#001a33"],
        "accent": "#0060a0",
        "particles": "ripples",
        "label": "The Sea",
    },
    "desert": {
        "keywords": [
            "desert", "sand", "dune", "oasis", "scorching", "arid",
            "mirage", "camel", "sphinx",
        ],
        "colors": ["#1a0f00", "#2e1a00"],
        "accent": "#c08030",
        "particles": "sand",
        "label": "The Desert",
    },
    "ruins": {
        "keywords": [
            "ruins", "ruin", "crumble", "crumbling", "rubble", "ancient",
            "overgrown", "collapsed", "forgotten", "desolate", "remnant",
        ],
        "colors": ["#100e04", "#1e1a08"],
        "accent": "#806830",
        "particles": "dust",
        "label": "The Ruins",
    },
    "swamp": {
        "keywords": [
            "swamp", "marsh", "bog", "mud", "murky", "fetid", "reed",
            "mire", "sludge", "stagnant",
        ],
        "colors": ["#080e04", "#0e1808"],
        "accent": "#406020",
        "particles": "mist",
        "label": "The Swamp",
    },
    "crypt": {
        "keywords": [
            "crypt", "tomb", "grave", "coffin", "undead", "bones",
            "skeleton", "lich", "mausoleum", "burial", "sarcophagus",
            "dead", "death",
        ],
        "colors": ["#08000a", "#140014"],
        "accent": "#602060",
        "particles": "smoke",
        "label": "The Crypt",
    },
    "fire": {
        "keywords": [
            "fire", "flame", "burn", "blaze", "inferno", "conflagration",
            "ember", "char", "smoke", "ash cloud",
        ],
        "colors": ["#1a0500", "#2e0800"],
        "accent": "#ff4400",
        "particles": "embers",
        "label": "The Fire",
    },
    "arcane": {
        "keywords": [
            "arcane", "magic", "spell", "enchant", "rune", "glyph",
            "mystical", "ritual", "incantation", "ward", "sigil",
            "thaumaturgy", "sorcery",
        ],
        "colors": ["#080020", "#12003a"],
        "accent": "#8040ff",
        "particles": "sparks",
        "label": "The Arcane",
    },
    "city": {
        "keywords": [
            "city", "market", "street", "crowd", "village", "town",
            "square", "cobble", "district", "quarter", "merchant",
        ],
        "colors": ["#0a0f1a", "#15202e"],
        "accent": "#6080a0",
        "particles": "rain",
        "label": "The Town",
    },
    "night": {
        "keywords": [
            "night", "midnight", "moon", "star", "dark sky",
            "constellation", "celestial", "dusk", "twilight",
        ],
        "colors": ["#000008", "#04000f"],
        "accent": "#4060a0",
        "particles": "stars",
        "label": "The Night",
    },
    "temple": {
        "keywords": [
            "temple", "shrine", "altar", "holy", "sacred", "chapel",
            "prayer", "cleric", "incense", "pew", "nave",
            "pale flame",
        ],
        "colors": ["#0e0c18", "#1a1428"],
        "accent": "#c0a060",
        "particles": "smoke",
        "label": "The Temple",
    },
}

# Priority order — checked in sequence; first match wins per chunk
SCENE_PRIORITY = [
    "mine", "crypt", "arcane", "fire", "temple", "dungeon", "cave",
    "forest", "swamp", "castle", "ocean", "mountain", "desert", "ruins",
    "tavern", "city", "night",
]

# ─── ANSI / TUI chrome stripping ─────────────────────────────────────────────

class _ANSIState:
    """Character-by-character ANSI escape-sequence state machine.

    Regex approaches fail when the PTY delivers bytes one at a time, splitting
    sequences like \\x1b[4;2m across chunk boundaries.  This state machine
    carries its state across calls so cross-chunk splits are handled correctly.

    States
    ------
    normal   → emitting regular characters
    esc      → saw ESC (0x1B), waiting to see what kind of sequence follows
    csi      → inside CSI sequence (ESC [ … letter)
    osc      → inside OSC sequence (ESC ] … BEL or ST)
    osc_esc  → inside OSC, just saw ESC — might be the ST terminator (ESC \\)
    """

    __slots__ = ("_s",)

    def __init__(self) -> None:
        self._s: str = "normal"

    def feed(self, text: str) -> str:
        out: list[str] = []
        s = self._s
        for ch in text:
            c = ord(ch)
            if s == "normal":
                if c == 0x1B:
                    s = "esc"
                elif c >= 0x20 or c in (0x09, 0x0A):   # printable / tab / newline
                    out.append(ch)
                # else: other control char (bell, etc.) — discard
            elif s == "esc":
                if ch == "[":
                    s = "csi"
                elif ch == "]":
                    s = "osc"
                else:
                    s = "normal"    # 2-char ESC sequence; discard both bytes
            elif s == "csi":
                if 0x40 <= c <= 0x7E:   # final byte of CSI
                    s = "normal"
                elif c == 0x1B:         # unexpected ESC — start fresh
                    s = "esc"
                # else: parameter / intermediate byte, keep consuming
            elif s == "osc":
                if c == 0x07:           # BEL terminates OSC
                    s = "normal"
                elif c == 0x1B:
                    s = "osc_esc"
                # else: OSC payload, keep consuming
            elif s == "osc_esc":
                s = "normal" if ch == "\\" else "osc"
        self._s = s
        return "".join(out)


_ansi = _ANSIState()
_ansi_lock = threading.Lock()

_BOX_CHARS = set("╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝")
_BOX_CHAR_STRIP = "╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝"  # same set as string for str.strip()

# Characters used by Claude CLI spinner / prompt / UI
_SPINNER_CHARS = set("✽⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◓◑◒◌◎●")
_PROMPT_STARTS = ("❯", ">", "·", "▸", "ℹ", "✓", "⚠", "✗", "⟳", "↳")


def _handle_cr(text: str) -> str:
    """Handle carriage returns the way a real terminal would.

    Two distinct cases:
      \\r\\n  — a real newline (\\r\\n line ending).  Normalise to \\n first
                so the content is preserved.
      bare \\r — cursor-to-column-0 for in-place token updates.  Claude CLI
                streams each token by rewriting the current line:
                  "The" → \\r"The Gut" → \\r"The Gutte" → …
                Keep only the last segment (= the final written state).
    """
    # Step 1: treat \\r\\n as a real newline — must come before bare-\\r logic
    text = text.replace("\r\n", "\n")

    # Step 2: handle remaining bare \\r (in-place rewrites)
    lines = text.split("\n")
    result = []
    for line in lines:
        if "\r" in line:
            parts = line.split("\r")
            result.append(parts[-1])   # last segment = final state of the line
        else:
            result.append(line)
    return "\n".join(result)


def _strip_ansi(text: str) -> str:
    text = _handle_cr(text)
    with _ansi_lock:
        text = _ansi.feed(text)
    return text


def _is_chrome(line: str) -> bool:
    """Return True for lines that are TUI chrome, not DM narration.

    The Claude CLI wraps responses in a box:
        ╭──────────────────╮
        │ narration text   │
        ╰──────────────────╯
    We strip the border characters from line edges first so that content
    lines like "│ The tavern smells of ale │" are NOT filtered — only pure
    border rows (all box chars, no letters) are treated as chrome.
    """
    stripped = line.strip()

    if not stripped:
        return False   # keep blank lines — they separate paragraphs

    # Strip leading/trailing box-drawing border chars to expose the real content.
    # "│ The tavern smells of ale │" → "The tavern smells of ale"
    content = stripped.strip(_BOX_CHAR_STRIP + " ")

    # If nothing remains, the line was entirely box-drawing chrome (a border row).
    if not content:
        return True

    # All remaining checks operate on content (without box border decoration).
    c = content

    # CLI prompt / spinner lines
    if c[0] in _SPINNER_CHARS:
        return True
    if c.startswith(_PROMPT_STARTS):
        return True

    # Common spinner word patterns (e.g. "Thinking…")
    if re.match(r"^[A-Z][a-z]+ing…?$", c):
        return True

    # Claude branding / metadata
    if "claude.ai" in c.lower():
        return True

    # Session-resume instructions emitted at end of response
    if c.startswith("Resume this session with:") or re.match(r"^claude\s+--resume\s+", c):
        return True

    # Status-bar patterns: cost, token counts, rate-limit bars
    # Note: "Tokens300/0" has no space — use \s* not \s+
    if re.search(r"Tokens\s*\d|5hr:|7d:|Session:|Total:\s*\$", c):
        return True

    # Model/plan header lines ("Sonnet 4.6", "Claude Pro", "Professional", etc.)
    if re.search(r"Sonnet|Haiku|Opus|Claude\s*(Pro|Max|Team|Code)\b|Professional\b|claude-\d", c, re.I):
        return True

    # Tool-use labels emitted by Claude CLI ("Bash command", "Read command", etc.)
    if re.match(r"^(Bash|Read|Write|Edit|Glob|Grep|WebFetch|WebSearch|TaskCreate|TaskUpdate|TaskGet|TaskList|NotebookEdit|Agent|ToolSearch|ExitPlanMode|EnterPlanMode|ScheduleWakeup|Monitor|RemoteTrigger|CronCreate|CronDelete|CronList|AskUserQuestion)(\s+(command|tool|result|call))?$", c, re.I):
        return True

    # Timestamp-prefixed lines ("3ts ago …", "2m ago …") — UI timestamps concatenated with content
    if re.match(r"^\d+\s*[smhdt]+s?\s*(ago\s*)?[A-Z]", c):
        return True

    # Bare numbers (token counts, cursor column positions, etc.)
    if re.match(r"^\d+$", c):
        return True

    # Single stray characters that are ANSI/escape remnants, not real words
    if len(c) == 1 and not c.isalpha():
        return True

    # Very short non-alpha fragments (≤3 chars with no letters = not narration)
    if len(c) <= 3 and not re.search(r"[a-zA-Z]{2}", c):
        return True

    return False


def _clean(text: str) -> str:
    text = _strip_ansi(text)
    lines = text.split("\n")
    kept = []
    for line in lines:
        if _is_chrome(line):
            continue
        # Strip box-border chars from edges so content reaches the browser clean.
        s = line.strip().strip(_BOX_CHAR_STRIP + " ")
        # Blank line → preserve as paragraph separator
        kept.append(s if s else "")
    # Collapse runs of more than two consecutive blank lines
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return result


# ─── Scene detection ──────────────────────────────────────────────────────────

_DEFAULT_SCENE = "tavern"             # we start in the inn
_current_scene_name: str = _DEFAULT_SCENE
_scene_buffer: list[str] = []
_BUFFER_WINDOW = 20   # analyse last N cleaned chunks together

# A keyword counts as a whole word, with or without a common ending: "ales",
# "innkeepers" and "burning" match; "pale", "dinner" and "barely" do not.
_SCENE_PATTERNS: dict[str, re.Pattern] = {
    name: re.compile(r"\b(?:" + "|".join(re.escape(kw) for kw in scene["keywords"])
                     + r")(?:s|es|ed|ing)?\b")
    for name, scene in SCENES.items()
}


def _detect_scene(text: str) -> Optional[dict]:
    global _current_scene_name, _scene_buffer

    _scene_buffer.append(text.lower())
    if len(_scene_buffer) > _BUFFER_WINDOW:
        _scene_buffer.pop(0)

    window = " ".join(_scene_buffer)

    scores: dict[str, int] = {}
    for scene_name in SCENE_PRIORITY:
        score = len(_SCENE_PATTERNS[scene_name].findall(window))
        if score > 0:
            scores[scene_name] = score

    if not scores:
        return None

    best = max(scores, key=lambda k: scores[k])
    if best == _current_scene_name:
        return None   # no change

    _current_scene_name = best
    return SCENES[best] | {"name": best}


# ─── SSE client registry ─────────────────────────────────────────────────────

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()
# Maps a connected SSE client (queue) → the character it's bound to, if any.
# Phones connect to /stream?character=<name>; the main display has no character.
# Lets a dice-request know whether a target PC has a live phone (→ route there)
# or not (→ open the on-screen roller). Guarded by _clients_lock.
_client_chars: "dict[queue.Queue, str]" = {}


def _phone_present(char: str) -> bool:
    """True if some connected phone is bound to this character (case-insensitive)."""
    c = (char or "").strip().lower()
    if not c:
        return False
    with _clients_lock:
        return c in _client_chars.values()

# ─── Text replay log ──────────────────────────────────────────────────────────
# Stores the last N cleaned text chunks so late-connecting browsers can catch up.
# Persisted per-campaign so switching campaigns loads the correct session tail.
# Falls back to the display directory when no campaign is active.
_text_log: deque = deque(maxlen=60)
_text_log_lock = threading.Lock()


def _get_log_file() -> str:
    """Return the campaign-specific log path, or the fallback display-dir path."""
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            return str(_campaign_dir_for_name(camp) / "text_log.json")
    except Exception:
        pass
    return _LOG_FALLBACK


def _persist_log() -> None:
    """Write the current text log to disk. Called after every chunk."""
    try:
        with _text_log_lock:
            data = list(_text_log)
        with open(_get_log_file(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_log() -> None:
    """Load a previously persisted text log. Called at startup and on campaign switch.
    Handles both old string format and new dict format."""
    try:
        with open(_get_log_file(), encoding="utf-8") as f:
            data = json.load(f)
        with _text_log_lock:
            _text_log.clear()
            for item in data[-60:]:
                # Migrate old plain-string entries to dict format
                if isinstance(item, str):
                    item = {"text": item}
                _text_log.append(item)
    except Exception:
        pass


_load_log()


# ─── Session tail buffer ──────────────────────────────────────────────────────
# Rolling buffer of the last 30 text events — written to session_tail.json after
# every /chunk POST so it survives crashes. Read at /gm load for display replay
# of the previous session's last exchanges.
#
# The text_log buffer above (maxlen=60) drives in-session browser-reconnect
# replay. The tail buffer (maxlen=30) is a parallel, leaner record stamped with
# the campaign name so cross-campaign replay does not bleed.
#
# ROBUSTNESS GUARANTEES (post 2026-05-01 wipe-bug fix):
#   1. _load_tail is NON-DESTRUCTIVE: it never wipes the in-memory buffer based
#      on an empty/filtered-out load. If the file is empty, missing, or every
#      entry is filtered out by campaign mismatch, the existing buffer stays.
#   2. _persist_tail SKIPS ON EMPTY: it never overwrites an existing non-empty
#      file with an empty buffer. Breaks the "filter zeros buffer → persist
#      writes [] → file lost" failure chain at the persistence end.
#   3. _persist_tail uses ATOMIC WRITES: writes to a tempfile and atomically
#      renames into place, so a partial/crashed write can never produce a
#      truncated or zero-byte file.
#   4. The legacy fallback path is gone. Tails only ever land in the campaign-
#      specific file. If CAMP_FILE is missing/empty when persist would fire,
#      we keep the buffer in memory and skip the write — no shared file that
#      bleeds across campaigns.
_tail_buffer: deque = deque(maxlen=30)
_tail_lock = threading.Lock()


def _get_tail_file() -> "str | None":
    """Return the campaign-specific tail path, or None if no campaign is set.

    Previously this fell back to a process-local path on the display side. That
    fallback caused tail bleed across campaigns and made the wipe-on-load bug
    much harder to diagnose. New contract: campaign-specific or nothing.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            return str(_campaign_dir_for_name(camp) / "session_tail.json")
    except Exception:
        pass
    return None


def _persist_tail() -> None:
    """Write _tail_buffer to disk. Refuses to overwrite content with empty.

    Atomic-write guarantee: writes to <path>.tmp then renames, so observers
    (the next /gm load reading the file) never see a partial or zero-byte
    state.
    """
    path = _get_tail_file()
    if not path:
        # No active campaign — keep the buffer in memory, skip disk.
        return
    try:
        with _tail_lock:
            data = list(_tail_buffer)
        # Skip-on-empty guard: never blank a file that currently has content.
        if not data and os.path.exists(path):
            try:
                if os.path.getsize(path) > 2:  # 2 bytes = "[]"
                    print(f"_persist_tail: skipping empty write — {path} has content",
                          file=sys.stderr)
                    return
            except OSError:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception as e:
        print(f"_persist_tail: write failed: {e}", file=sys.stderr)


def _load_tail() -> None:
    """Load tail from disk into the buffer. NON-DESTRUCTIVE on empty/mismatch.

    Old behavior: cleared the buffer first, then re-appended filtered entries.
    Created the wipe bug: if every entry was filtered out (campaign mismatch)
    the buffer ended up empty and the next _persist_tail wrote [] to disk.

    New behavior: build the candidate buffer first, then ONLY swap it into
    place if at least one entry survived filtering. If nothing survives, the
    in-memory buffer is left alone — preserves whatever was already loaded.
    """
    path = _get_tail_file()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return  # No file yet — keep in-memory state
    except (OSError, json.JSONDecodeError) as e:
        print(f"_load_tail: read failed for {path}: {e}", file=sys.stderr)
        return
    if not isinstance(data, list):
        print(f"_load_tail: file content is not a list — leaving buffer alone",
              file=sys.stderr)
        return

    try:
        current_camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        current_camp = ""

    candidate: list = []
    for item in data[-30:]:
        if not isinstance(item, dict):
            continue
        item_camp = item.get("_camp", "")
        # If we know the campaign and the entry stamps a different campaign,
        # skip it. Entries with no stamp are kept (legacy data + tolerance).
        if current_camp and item_camp and item_camp != current_camp:
            continue
        candidate.append(item)

    if not candidate:
        # Loaded data filtered down to nothing. DO NOT replace the buffer —
        # this is the wipe-bug guard.
        return

    with _tail_lock:
        _tail_buffer.clear()
        for item in candidate:
            _tail_buffer.append(item)


_load_tail()


# ─── Character / combat stats ─────────────────────────────────────────────────
# Stored as {"players": [...], "turn_order": {...}|null}
# Players are merged by name so partial updates (just HP, just XP) work.

_current_stats: dict = {}
_stats_lock = threading.Lock()


def _persist_stats() -> None:
    try:
        with _stats_lock:
            data = dict(_current_stats)
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_stats() -> None:
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _stats_lock:
            _current_stats.update(data)
    except Exception:
        pass
    _drop_stale_turn_order()


def _encounter_active() -> "bool | None":
    """True/False when the active campaign's encounter state is known, else None.

    None means the campaign cannot be resolved (no .campaign file, unreadable),
    in which case the caller must leave the saved fight alone.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except OSError:
        return None
    try:
        _campaign_dir_for_name(camp)
        path = _find_display_campaign(camp) / "combat" / "encounter.json"
    except Exception:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            enc = json.load(f)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return None
    return isinstance(enc, dict) and enc.get("status", "active") == "active"


def _drop_stale_turn_order() -> None:
    """Clear a restored turn_order when the campaign has no active encounter.

    stats.json survives restarts on purpose (a fight in progress comes back), but
    it cannot tell a live fight from a finished one whose stats were never
    cleared. The encounter file can: absent or status "ended" means no fight.
    """
    with _stats_lock:
        if not _current_stats.get("turn_order"):
            return
    if _encounter_active() is False:
        with _stats_lock:
            _current_stats["turn_order"] = None
        _persist_stats()


_load_stats()


# ─── Player input queue ───────────────────────────────────────────────────────
# The queue itself is QUEUE_FILE (.input_queue): see _queue_append. This list
# is only what the pending_input broadcast shows. Nothing is ever delivered to
# the GM from it, so it cannot hold an action the GM's front-end does not see.
# It used to be a second queue persisted to player_input.json, read only by
# check_input.py, which is how a grid click never reached a wrapper.py GM.
_input_queue: list[dict] = []
_input_lock = threading.Lock()

# Pending GM-issued dice requests: request_id → {chars: set[str], meta: {...}, started_at: float}
# A request is "complete" when its chars set is empty (every prescribed player rolled).
# send.py --wait polls GET /dice-request/<id> to know when the GM can move on.
_dice_pending: dict = {}
_dice_pending_lock = threading.Lock()
# Finished requests keep their roll texts so --wait can print them to the GM.
_dice_done: dict = {}           # request_id → [roll text, ...], oldest first
_dice_cancelled: set = set()    # ids in _dice_done that the GM cancelled
_DICE_DONE_KEEP = 50


def _dice_finish(req_id: str, results: list, cancelled: bool = False) -> None:
    """Keep a finished request's rolls for --wait. Caller holds _dice_pending_lock."""
    _dice_done[req_id] = results
    if cancelled:
        _dice_cancelled.add(req_id)
    while len(_dice_done) > _DICE_DONE_KEEP:
        oldest = next(iter(_dice_done))
        del _dice_done[oldest]
        _dice_cancelled.discard(oldest)


def _dice_pending_snapshot() -> list:
    with _dice_pending_lock:
        return [
            {"request_id": rid, "pending": sorted(e["chars"]),
             "label": e["meta"].get("label", ""),
             # Offers ride the snapshot so a reload or a second window can see
             # what was on the table. They are the server's own copy, not
             # anything a client asserts, so this cannot widen what a phone may
             # spend — the spend is still checked against this list.
             "offers": e["meta"].get("offers") or []}
            for rid, e in _dice_pending.items() if e["chars"]
        ]



# SSE sequencing. Every broadcast payload gets a monotonically increasing `seq`.
# Payloads that carry narration (or a clear) are kept in a bounded replay buffer so
# a client that reconnects with /stream?since=<lastSeq> receives only what it
# missed. _EPOCH changes on every server start, so a client can tell that a lower
# seq means a restart rather than a duplicate. Guarded by _clients_lock.
_SEQ_BUFFER_MAX = 512
_EPOCH = secrets.token_hex(4)
_seq = 0
_seq_evicted = 0   # highest seq that has been evicted from the buffer
_seq_buffer: deque = deque(maxlen=_SEQ_BUFFER_MAX)
_CLOSE = object()   # sentinel: tells a client's generator to end its stream


def _replayable(payload: dict) -> bool:
    return bool(payload.get("text") or payload.get("clear"))


def _stamp_log_entry(log_entry: dict, seq: int) -> dict:
    """Mark a replay-log entry with the identity the client dedupes on.

    Both halves are needed. `seq` alone is not an identity across a restart:
    the counter starts at 1 again every run, so entry 3 of run 2 is not entry 3
    of run 1, and a browser that has been up across both would drop the new one
    as already drawn. `_EPOCH` is the run id, so (epoch, seq) names one entry
    of one run and nothing else.

    Entries with neither (a log written before this shipped, or a hand-edited
    one) are rendered unconditionally by the client. Guessing "probably already
    drawn" would delete narration the reader has never seen.
    """
    log_entry["seq"] = seq
    log_entry["_epoch"] = _EPOCH
    return log_entry


def _broadcast(payload: dict) -> int:
    """Fan a payload out to every SSE client. Returns the seq it was stamped with.

    The return value exists for the replay log. `_text_log` entries are what a
    reconnecting browser is replayed, and that replay carried no seq, so the
    browser's per-payload dedupe could not see it and a reconnect rendered the
    whole recent log a second time on top of what was already on screen. A
    caller that appends to `_text_log` stamps the returned seq onto its entry
    with `_stamp_log_entry`, which is what lets the client drop the part it has
    already drawn.
    """
    global _seq, _seq_evicted
    with _clients_lock:
        _seq += 1
        _assigned = _seq
        payload = {**payload, "seq": _seq}
        if _replayable(payload):
            if len(_seq_buffer) == _SEQ_BUFFER_MAX:
                _seq_evicted = _seq_buffer[0]["seq"]
            _seq_buffer.append(payload)
        dead = []
        for q in _clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            # A full queue means this client is too slow. Dropping payloads
            # silently would lose narration, so log it and close the stream: the
            # browser reconnects and replays from its last seq.
            print("[display] SSE client queue full; disconnecting so it can resume",
                  file=sys.stderr)
            _clients.remove(q)
            _client_chars.pop(q, None)
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
            q.put_nowait(_CLOSE)
    return _assigned


def _replay_since(since: int):
    """Buffered narration payloads after `since`, or None if the gap cannot be
    filled (unknown seq, restart, or the buffer has rolled past it). Caller holds
    _clients_lock."""
    if since > _seq:
        return None
    if since == _seq:
        return []
    if since < _seq_evicted:
        return None   # narration after `since` may have rolled out of the buffer
    return [p for p in _seq_buffer if p["seq"] > since]


_last_clocks = None  # last revealed-clock payload pushed, to broadcast only on change


def _clocks_payload() -> list:
    """Revealed faction clocks for the active campaign (hidden ones are absent)."""
    name = _active_campaign_name()
    if not name:
        return []
    try:
        import world as _world
        return _world.revealed_clocks(name)
    except Exception:
        return []


def _push_clocks_if_changed() -> None:
    global _last_clocks
    clocks = _clocks_payload()
    if clocks != _last_clocks:
        _last_clocks = clocks
        _broadcast({"clocks": clocks})


# ─── Routes ──────────────────────────────────────────────────────────────────

_SYSTEMS_DIR = pathlib.Path(__file__).resolve().parent.parent / "systems"


def _load_ui_manifest() -> str:
    """Return the active campaign's system UI manifest as a JSON string for the template.

    Resolves active campaign → system name → systems/<system>/ui.json. Returns
    "null" when there is no campaign, no ui.json, or the file is unreadable/invalid;
    the browser then falls back to its built-in default manifest (the D&D layout),
    so the display renders identically with or without a manifest file.

    Resolved at page render. Switching systems takes effect on the next display
    load — and the display is force-restarted each session, so this is a non-issue
    in normal use.
    """
    name = _active_campaign_name()
    if not name:
        return "null"
    try:
        _campaign_dir_for_name(name)
        system = _campaign_system(name)
        path = _SYSTEMS_DIR / system / "ui.json"
        if not path.exists():
            return "null"
        manifest = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return "null"
    # Compact, and neutralize any "</script>" that could break the inline tag.
    return json.dumps(manifest, separators=(",", ":")).replace("<", "\\u003c")


@app.route("/")
def index():
    # Pass LAN token to template so the browser can authenticate /help-request
    return render_template(
        "index.html",
        lan_token=_lan_token or "",
        narrator_voice=_read_narrator_voice(),
        tts_available=(_tts is not None),
        ui_manifest=_load_ui_manifest(),
    )


@app.route("/icons/<path:filename>")
def serve_icon(filename):
    """Serve icons, favicon and brand assets out of display/icons/.

    The templates reference /icons/<name>.png in ~20 places (class badges, dice
    and block badges, the corner logo, the app icons in <head>). Flask serves
    nothing at that prefix by default, so without this route every one of them
    404s and the UI renders with blank squares, which is what it did until
    2026-08-23. send_from_directory rejects traversal, so <path:filename>
    cannot escape the icons directory.
    """
    return send_from_directory(_ICONS_DIR, filename)


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(_ICONS_DIR, "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")


# ─── Overview map ────────────────────────────────────────────────────────────
#
# The campaign's overview map (BV4 S1): `<campaign>/maps/overview/<slug>.json`,
# a `kind: overview` spec whose pins are fractions of an image, read by
# scripts/overview_map.py. Its own page and its own static files, so the battle
# board (`renderBoard` in tactics.js) is not involved. GET only: authoring is a
# file the GM edits, the same as the note pins below.


def _atlas_spec(slug):
    """`(spec, image)` for one overview map in the active campaign, or None."""
    camp = _pin_campaign()
    if camp is None:
        return None
    import overview_map as _overview_map
    try:
        return _overview_map.load(camp, slug)
    except _overview_map.OverviewMapError:
        return None


@app.route("/atlas", methods=["GET"])
def atlas_entry():
    """The display's "Overview map" link: open the campaign's overview map.

    The first loadable spec by slug. A campaign with none gets a plain 404
    page that says so, not a broken map.
    """
    camp = _pin_campaign()
    if camp is not None:
        import overview_map as _overview_map
        for slug in _overview_map.available(camp):
            if _atlas_spec(slug) is not None:
                return redirect("/atlas/" + slug)
    return Response("No overview map in this campaign. Add one under "
                    "maps/overview/ (see scripts/overview_map.py).",
                    status=404, mimetype="text/plain; charset=utf-8")


@app.route("/atlas/<slug>", methods=["GET"])
def atlas_overview(slug):
    """The overview page. Unrevealed pins are dropped here, before rendering,
    so their labels and positions are never in the HTML."""
    found = _atlas_spec(slug)
    if found is None:
        return Response("No such overview map.", status=404,
                        mimetype="text/plain; charset=utf-8")
    import overview_map as _overview_map
    shown = _overview_map.revealed(found[0])
    # "<" is escaped because this lands inside a <script> block (as map_edit).
    return render_template(
        "atlas.html", map_name=shown["name"],
        image_url="/atlas/" + shown["slug"] + "/image",
        map_json=json.dumps(shown, ensure_ascii=False).replace("<", "\\u003c"))


@app.route("/atlas/<slug>/image", methods=["GET"])
def atlas_overview_image(slug):
    """The overview's image, only through a spec that names it.

    An SVG opened directly is a document, so this response may not run script
    whatever the file contains: a sandboxing CSP, enforced rather than
    report-only.
    """
    found = _atlas_spec(slug)
    if found is None:
        return Response("No such overview map.", status=404,
                        mimetype="text/plain; charset=utf-8")
    image = found[1]
    resp = send_from_directory(str(image.parent), image.name)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox")
    return resp


@app.route("/maps/<path:filename>")
def map_image(filename):
    """Serve a battle map's background artwork out of display/maps/.

    Maps that carry an image (see scripts/atlas_to_map.py) name a file here, and
    the display fetches it to draw under the terrain overlay. Nothing references
    this route unless a map opts in, so a map without artwork is unaffected.

    send_from_directory rejects traversal, so <path:filename> cannot escape
    display/maps/, the same guarantee the icons route relies on.
    """
    return send_from_directory(_MAPS_DIR, filename)


@app.route("/tokens/<path:filename>")
def token_portrait(filename):
    """Serve a token's portrait out of display/tokens/.

    Every creature the engine can place may carry a `portrait` in the combat
    snapshot, and the display fetches it to draw inside the token's shape. The
    route is only reached when a token has one, so a token without a portrait is
    unaffected -- which is most of them, and is a supported state rather than a
    missing file.

    Confined to display/tokens/ by send_from_directory, the same traversal
    guarantee the icons and maps routes rely on.
    """
    return send_from_directory(_TOKENS_DIR, filename)


# ─── Map editor ──────────────────────────────────────────────────────────────
#
# A GM paints terrain by clicking, and this rewrites the map's features[].
# Two things make it safe to point at a file the engine reads:
#
#   * The merge is a merge. Strokes are replayed onto the map's cells and
#     features[] is re-derived as a cover of disjoint rectangles, so painting
#     over a wall replaces the wall's rectangle instead of stacking on it.
#   * The candidate goes through tactics.maps.compile_map before it is written,
#     so anything this route saves is a map the engine has already agreed to
#     load, and grid.rows can only change where the GM actually painted.


def _map_not_found(name):
    return jsonify({"error": f"No map {name!r}. Maps: {', '.join(_mapeditor.available())}"}), 404


@app.route("/maps/<name>/edit", methods=["GET"])
def map_edit(name):
    """The painting page. Read-only: a GET must never touch the map file."""
    path = _mapeditor.find(name)
    if path is None:
        return _map_not_found(name)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
        state = _mapeditor.editor_state(spec, path.stem)
    except (OSError, ValueError) as e:
        return jsonify({"error": f"{path.name} could not be read: {e}"}), 400
    # Inline the state rather than fetching it: the page is a GM's local tool,
    # and a GET that returns 200 with everything it needs is one round trip.
    # "<" is escaped because this lands inside a <script> block.
    return render_template("mapseditor.html",
                           state=json.dumps(state).replace("<", "\\u003c"),
                           lan_token=_lan_token or "")


@app.route("/maps/<name>/features", methods=["POST"])
def map_features(name):
    """Body: {"strokes": [{type, x, y, w, h}, ...]}. Rewrites the map file.

    Tokens and the save gate are the two ways work is lost. A GM paints for ten
    minutes, so an overwrite of a map that already has features has to be
    something they asked for (hence `confirm`) and the original file is kept
    as <name>.json.bak (the first original only, never overwritten).
    """
    if not _token_ok():
        return "Forbidden", 403
    path = _mapeditor.find(name)
    if path is None:
        return _map_not_found(name)
    body = request.get_json(silent=True) or {}
    strokes = body.get("strokes")
    if not isinstance(strokes, list):
        return jsonify({"error": "strokes must be a list of rectangles"}), 400
    before = path.read_text(encoding="utf-8")
    try:
        spec = json.loads(before)
    except ValueError as e:
        return jsonify({"error": f"{path.name} could not be read: {e}"}), 400
    if not isinstance(spec, dict):
        return jsonify({"error": f"{path.name} could not be read: not a map object"}), 400
    if spec.get("features") and not body.get("confirm"):
        return jsonify({"error": f"{path.name} already has terrain on it. "
                                 "Re-save with confirm to overwrite it "
                                 f"({len(spec['features'])} rectangles; the original is kept as a .bak)."}), 409
    try:
        merged = _mapeditor.apply_strokes(spec, strokes)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        bak = _mapeditor.write(path, merged)
    except OSError as e:
        return jsonify({"error": f"{path.name} could not be written: {e}"}), 500
    compiled = json.loads(path.read_text(encoding="utf-8"))
    return jsonify({
        "ok": True,
        "slug": path.stem,
        "features": compiled.get("features", []),
        "backup": bak.name if bak != path else "",
        "state": _mapeditor.editor_state(compiled, path.stem),
    })


# ─── Note pins ───────────────────────────────────────────────────────────────
#
# A pin is a marker on a map that opens a campaign note (or another map). Pins
# are campaign state in <campaign>/pins/<map-slug>.json, authored by the GM in a
# shell -- `scripts/pin.py` -- and read from here.
#
# Two things about this block are worth stating, because they are the reason it
# is shaped this way rather than the obvious way:
#
#   * There is no POST here. The obvious design is a write route next to the
#     read route, and the display's own write gate is `_token_ok()` -- which is
#     true for every browser on the LAN, because index() hands the token to
#     every page it serves. A player at the table could plant a pin that the GM
#     then clicked in good faith, on the players' screen. So the only pins that
#     exist are ones the GM made.
#
#   * Pins are filtered in this layer, not in sync.snapshot(). Pins are not
#     encounter state, so no snapshot() call can carry them; and the note-read
#     route fires with no combat active, so there would be no snapshot to
#     consult. The precedent is _clocks_payload() just above.


@app.route("/scene", methods=["GET"])
def current_scene():
    """The campaign's persistent scene, with the marker filtered.

    A scene is campaign state in `<campaign>/scene.json` (`tactics/scenes.py`),
    authored by the GM in a shell with `combat.py scene MAP` and `combat.py here
    PLACE`. It is read from here, and like the clocks and the pins, it is
    filtered in this layer rather than in `sync.snapshot()`: a scene outlives
    the encounter, so no snapshot call can carry it, and the route fires with no
    combat running at all.

    The filter is `scenes.revealed()`, and it is `marker.revealed is True` rather
    than truthiness, because a hand-edited scene.json is a supported input and an
    absent key, a `null` and a `"yes"` are all different things to a browser.

    The payload is the map and its background either way, and the marker only
    when it is revealed. An unrevealed marker is absent rather than flagged: one
    browser audience, no `is_gm`, no viewer parameter, no second port. The GM
    reads a hidden marker from the terminal, not from here.
    """
    camp = _pin_campaign()
    if camp is None:
        return jsonify({"scene": None})
    try:
        from tactics import scenes as _scenes
    except Exception:
        return jsonify({"scene": None})
    spec = _scenes.load(camp)
    if spec is None:
        return jsonify({"scene": None})
    marker = _scenes.revealed(spec)
    payload = {"map": spec["map"], "name": spec.get("name") or spec["map"],
               "background": spec["background"],
               "extent": [{"width": spec["extent"][0], "height": spec["extent"][1]}],
               "marker": None}
    if marker is not None:
        payload["marker"] = {"name": marker["name"], "place": marker.get("place"),
                             "x": marker["x"], "y": marker["y"]}
    return jsonify({"scene": payload})


@app.route("/pins/<slug>", methods=["GET"])
def pins_for_map(slug):
    """The pins on one map, for the board to draw.

    An unrevealed pin is absent rather than flagged: the display has one
    audience and no way to tell a GM from a player, so "not on the players'
    board" is the only distinction that means anything here. See
    docs/specs/SPEC-grid-and-map.md section 10, question 4.
    """
    camp = _pin_campaign()
    if camp is None:
        return jsonify({"pins": []})
    if _mapeditor.find(slug) is None:
        return jsonify({"error": "no such map"}), 404
    try:
        import pins as _pins
        return jsonify({"pins": _pins.revealed(_pins.load(camp, slug))})
    except _pins.PinError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError:
        # A campaign that has gone away between the check and the read is not a
        # 500 the table needs to see; an empty board is.
        return jsonify({"pins": []})


@app.route("/pins/note", methods=["GET"])
def pin_note():
    """The markdown one note pin opens, as text. Query: ?map=<slug>&id=<pin id>.

    Read-only and deliberately dull: every refusal is the same 404 with the same
    body, so this route cannot be used to ask whether a sealed file exists. The
    allow-list, the containment check and the visibility rule all live in
    scripts/pins.py and are re-run here on every request -- a pin record is not
    trusted, because the GM edits that file in a text editor.
    """
    camp = _pin_campaign()
    if camp is None:
        return jsonify({"error": "no such note"}), 404
    slug = (request.args.get("map") or "").strip()
    pin_id = (request.args.get("id") or "").strip()
    if not slug or not pin_id:
        return jsonify({"error": "no such note"}), 404
    try:
        import pins as _pins
        found = next((p for p in _pins.load(camp, slug) if p["id"] == pin_id), None)
        if found is None:
            return jsonify({"error": "no such note"}), 404
        body = _pins.note_body(camp, found)
    except _pins.PinError:
        # One answer for every refusal. A message that distinguished "sealed"
        # from "absent" would turn this into an oracle over the campaign's
        # spoiler files, which is the one thing the allow-list exists to stop.
        return jsonify({"error": "no such note"}), 404
    except OSError:
        return jsonify({"error": "no such note"}), 404
    return Response(body, mimetype="text/markdown; charset=utf-8")


def _pin_campaign():
    """The active campaign's directory, or None when there is no campaign.

    Resolved the way every other per-campaign read in this app is resolved
    (CAMP_FILE, then paths.find_campaign, which honours GM_CAMPAIGN_ROOT), rather
    than through a root of our own. world.py documents at length what happens
    the second time a script keeps its own idea of where campaigns live.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except OSError:
        return None
    try:
        _campaign_dir_for_name(camp)
    except (TypeError, ValueError, OSError):
        return None
    found = _find_display_campaign(camp)
    return found if found.is_dir() else None


@app.route("/srd-lookup")
def srd_lookup():
    """Look up a spell, item, condition, feature, or monster by name.

    Query params:
        name      — the name to look up (required)
        category  — spell | item | equipment | magic_item | condition | monster | feature (optional)
        level     — character level (1–20); collapses scale progressions to the matching entry

    Returns JSON: {"found": bool, "name": str, "category": str, "text": str}
    """
    name     = request.args.get("name", "").strip()[:120]
    category = request.args.get("category", "").strip().lower() or None
    level_s  = request.args.get("level", "").strip()
    level    = int(level_s) if level_s.isdigit() and 1 <= int(level_s) <= 20 else None
    if not name:
        return jsonify({"found": False, "error": "name required"}), 400
    if not _SRD_AVAILABLE or _lookup is None:
        return jsonify({"found": False, "error": "SRD dataset not loaded"}), 503

    text = _lookup.lookup_with_level(name, category=category, level=level)
    if text:
        rec = _lookup.lookup_record(name, category=category)
        resolved_cat = (rec or {}).get("_cat", category or "")
        return jsonify({"found": True, "name": name, "category": resolved_cat, "text": text})
    # Not found — offer near-miss "did you mean?" suggestions (typo recovery)
    # plus a reference link so the frontend can still link out. `ref` is {} when
    # there is no VERIFIED destination for this category, and the frontend
    # renders no link at all in that case: a guessed URL reads as an answer and
    # dead-ends, which is worse than saying nothing.
    ref = _lookup.reference_url(name, category=category)
    suggestions = []
    try:
        for sg_name, sg_cat in _lookup.suggest(name, category=category, n=3):
            suggestions.append({"name": sg_name, "category": sg_cat})
    except Exception:
        pass  # suggestion is best-effort; never fail the lookup over it
    return jsonify({"found": False, "name": name,
                    "reference_url": ref.get("url", ""),
                    "reference_label": ref.get("label", ""),
                    # kept so an older cached frontend still gets a link
                    "wikidot_url": ref.get("url", ""),
                    "suggestions": suggestions})


@app.route("/ping")
def ping():
    return "ok", 200


@app.route("/health")
def health():
    """Server-side integrity probe used by send.py --verify and external monitors.

    Returns the live counts the send-side cares about:
      - alive: always True if the route runs
      - tail_buffer: number of entries currently in the rolling tail
      - tail_file_size: size in bytes of the on-disk session_tail.json
      - text_log: number of entries in the replay log
      - campaign: the active campaign name (empty if none set)
      - clients: connected SSE clients

    No auth required — liveness/monitoring endpoint, no PII or game content
    is exposed.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    tail_path = _get_tail_file()
    try:
        tail_size = os.path.getsize(tail_path) if tail_path and os.path.exists(tail_path) else 0
    except OSError:
        tail_size = 0
    with _tail_lock:
        tail_count = len(_tail_buffer)
    with _text_log_lock:
        log_count = len(_text_log)
    with _clients_lock:
        client_count = len(_clients)
    with _stats_lock:
        roster_count = len(_current_stats.get("players", []))
    return {
        "alive": True,
        "tail_buffer": tail_count,
        "tail_file_size": tail_size,
        "tail_path": tail_path or "",
        "text_log": log_count,
        "campaign": camp,
        "clients": client_count,
        "roster": roster_count,
        "author_violations": len(_author_violations),
    }, 200


# ─── Authorship (N11) ─────────────────────────────────────────────────────────
# Every /chunk push may carry an `author` (send.py --author / GM_DISPLAY_AUTHOR).
# It is stored on the transcript entry so a reader can tell who wrote a block.
# A harness declares the one author it expects with POST /author; a block from
# anyone else is refused (409) and recorded, and an unstamped block is accepted
# but recorded, so a second writer is loud instead of invisible. With no
# expectation declared, nothing is enforced and old callers behave as before.

_expected_author: Optional[str] = None
_author_violations: list = []
_author_lock = threading.Lock()
_AUTHOR_MAX = 40
_VIOLATIONS_KEEP = 100


def _clean_author(raw) -> str:
    return re.sub(r"[^\w .:@/-]", "", str(raw or "")).strip()[:_AUTHOR_MAX]


def _author_check(author: str, text: str) -> Optional[dict]:
    """Return a violation record if `author` is not the expected one, else None."""
    with _author_lock:
        expected = _expected_author
        if expected is None or author == expected:
            return None
        rec = {"kind": "unstamped" if not author else "wrong_author",
               "author": author, "expected": expected,
               "text": text[:120], "at": _time.time()}
        _author_violations.append(rec)
        del _author_violations[:-_VIOLATIONS_KEEP]
    print(f"[display] AUTHOR MISMATCH: expected {expected!r}, got {author or '(none)'!r}: "
          f"{text[:60]!r}", file=sys.stderr, flush=True)
    return rec


@app.route("/author", methods=["GET", "POST", "DELETE"])
def author_route():
    """GET: expected author + violations. POST {"author": "..."}: declare the
    expected author (clears old violations). DELETE: stop enforcing."""
    global _expected_author
    if not _token_ok():
        return "Forbidden", 403
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        name = _clean_author(data.get("author"))
        if not name:
            return "Bad Request", 400
        with _author_lock:
            _expected_author = name
            _author_violations.clear()
    elif request.method == "DELETE":
        with _author_lock:
            _expected_author = None
    with _author_lock:
        return jsonify({"expected": _expected_author,
                        "violations": list(_author_violations)}), 200


# ─── GM-side adjudication record (dice log DC leak) ──────────────────────────
# The GM's bookkeeping (base DC, current DC, dial word, floor, mark state) is not
# table talk. A chunk may carry `gm_log`, kept in gm-adjudication.jsonl beside
# the display and never broadcast, replayed or written to the transcript. For
# `dice` chunks, sentences that are pure bookkeeping are moved there
# automatically, and with `hide_dc` the "vs DC N" target is dropped too.

GM_LOG_FILE = os.path.join(_DISPLAY_DIR, "gm-adjudication.jsonl")
_gm_log_lock = threading.Lock()
_BOOKKEEPING = re.compile(
    r"\bDC\s+(?:falls|drops|rises|climbs|is now|now|stays|holds)\b"
    r"|^\s*(?:the\s+)?dial\s*:"
    r"|\b(?:the\s+)?floor\s+(?:is|was)\s+(?:not\s+)?reached\b"
    r"|\bno\s+mark\b|\bmark\s+(?:earned|awarded|gained|banked)\b"
    r"|\bnew\s+floor\b",
    re.IGNORECASE)
_VS_DC = re.compile(r"\s*\bvs\.?\s+DC\s*\d+", re.IGNORECASE)


def _split_gm_bookkeeping(text: str, hide_dc: bool = False) -> tuple:
    """Split a dice line into (player_text, gm_only_text)."""
    public: list = []
    private: list = []
    for line in text.splitlines():
        keep: list = []
        for sent in re.split(r"(?<=[.!?])\s+", line.strip()):
            if not sent:
                continue
            if _BOOKKEEPING.search(sent):
                private.append(sent)
            else:
                keep.append(sent)
        joined = " ".join(keep)
        if hide_dc and _VS_DC.search(joined):
            private.append(joined)
            joined = _VS_DC.sub("", joined)
        if joined:
            public.append(joined)
    return "\n".join(public).strip(), "\n".join(private).strip()


def _write_gm_log(entry: dict) -> None:
    try:
        with _gm_log_lock, open(GM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


@app.route("/chunk", methods=["POST"])
def chunk():
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}

    # Campaign registration — write .campaign file and reload log + tail for correct
    # per-campaign replay. Sent by send.py --set-campaign at /gm load. May arrive with
    # or without text.
    if "campaign" in data:
        raw_camp = data["campaign"]
        if not isinstance(raw_camp, str):
            return "Invalid campaign name", 400
        new_camp = raw_camp.strip()
        try:
            _campaign_dir_for_name(new_camp)
        except (TypeError, ValueError, OSError):
            return "Invalid campaign name", 400
        try:
            prev_camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        except Exception:
            prev_camp = ""
        try:
            # A different campaign than the one on file: wipe stale stats, turn
            # order, sent actions and queued input automatically, so switching
            # campaigns never requires a manual /clear. Re-registering the same
            # campaign (a play.py restart re-sends it) leaves state intact.
            if new_camp and new_camp != prev_camp:
                _do_clear()
            with open(CAMP_FILE, "w", encoding="utf-8") as f:
                f.write(new_camp)
            _load_log()
            _load_tail()
        except Exception:
            pass
        # Resolve and stash the system version for this campaign so the sidebar
        # badge can render. Empty string when the field is unset (legacy
        # campaigns predating the field — they should be migrated via
        # scripts/migrate_system_version.py at /gm load). Wrapped in try/except
        # so a missing paths import or malformed state.md never breaks /chunk.
        try:
            from paths import campaign_system_version as _campaign_system_version
            _sv = _campaign_system_version(str(data["campaign"]).strip())
            with _stats_lock:
                _current_stats["system_version"] = _sv
                _broadcast({"stats": dict(_current_stats)})
        except Exception:
            pass

    # Milestone award/spend — system-agnostic event for "the GM rewarded great play".
    # Renders as a gold-glow block in the feed. The system module supplies the label
    # (Inspiration / Bennie / Hero Point / Fate Point / etc.); default is "Milestone".
    is_milestone_award = bool(data.get("milestone_award"))
    is_milestone_spend = bool(data.get("milestone_spend"))
    if is_milestone_award or is_milestone_spend:
        name = str(data.get("milestone_award") or data.get("milestone_spend") or "").strip()[:80]
        label = str(data.get("label") or "Milestone").strip()[:40]
        payload: dict = {
            "milestone_award" if is_milestone_award else "milestone_spend": name,
            "label": label,
            "text": name,
        }
        log_entry: dict = dict(payload)
        if is_milestone_award and data.get("reason"):
            payload["reason"] = str(data["reason"]).strip()[:240]
            log_entry["reason"] = payload["reason"]
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        # Broadcast, then stamp, then persist. The log entry goes in before the
        # broadcast so a browser that reconnects the instant it receives the
        # payload cannot ask for a replay that does not have this entry yet, and
        # the persist comes after the stamp so the file on disk carries the
        # identity the client dedupes on.
        _stamp_log_entry(log_entry, _broadcast(payload))
        _persist_log()
        _persist_tail()
        return "", 204

    author = _clean_author(data.get("author"))
    gm_log = str(data.get("gm_log") or "").strip()[:2000]

    raw = data.get("text", "")
    if not raw:
        if gm_log:
            _write_gm_log({"at": _time.time(), "author": author, "gm_log": gm_log})
        return "", 204

    violation = _author_check(author, str(raw))
    if violation and violation["kind"] == "wrong_author":
        return jsonify({"error": "author_mismatch", "expected": violation["expected"],
                        "got": author}), 409

    is_action = bool(data.get("action"))
    is_player = bool(data.get("player"))
    is_npc    = bool(data.get("npc"))
    is_dice   = bool(data.get("dice"))
    is_tutor  = bool(data.get("tutor"))

    # Player/npc/dice/tutor/action text comes from send.py (no ANSI/chrome) — light clean only.
    # DM narration may come from wrapper.py — full clean.
    cleaned = raw.strip() if (is_action or is_player or is_npc or is_dice or is_tutor) else _clean(raw)
    if not cleaned.strip():
        return "", 204

    # GM adjudication never reaches the player-facing transcript.
    if is_dice:
        cleaned, private = _split_gm_bookkeeping(cleaned, bool(data.get("hide_dc")))
        if private:
            gm_log = (gm_log + "\n" + private).strip() if gm_log else private
    if gm_log:
        _write_gm_log({"at": _time.time(), "author": author, "gm_log": gm_log,
                       "shown": cleaned})
    if not cleaned.strip():
        return "", 204

    payload: dict = {"text": cleaned}

    if is_action:
        payload["action"] = data["action"]
    elif is_player:
        payload["player"] = data["player"]
    elif is_npc:
        payload["npc"] = data["npc"]
    elif is_dice:
        payload["dice"] = True
    elif is_tutor:
        payload["tutor"] = True
    else:
        # Scene detection only on DM narration
        scene = _detect_scene(cleaned)
        if scene:
            payload["scene"] = scene
            if _audio:
                _audio.on_scene_change(scene["name"])
        # SFX scan on all non-player text
        if _audio:
            _audio.on_text(cleaned)

    # Store full typed payload so replay preserves action/player/npc/dice/tutor context
    log_entry: dict = {"text": cleaned}
    if is_action:
        log_entry["action"] = data["action"]
    elif is_player:
        log_entry["player"] = data["player"]
    elif is_npc:
        log_entry["npc"] = data["npc"]
    elif is_dice:
        log_entry["dice"] = True
    elif is_tutor:
        log_entry["tutor"] = True

    if author:
        log_entry["author"] = author
        payload["author"] = author
    if violation:
        log_entry["author_flag"] = violation["kind"]

    # Stamp campaign onto the tail entry so cross-campaign replay can filter
    # and a stale shared file does not bleed into the active session.
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)

    # Broadcast, then stamp, then persist: see the note in chunk(). The log
    # entry is in place before the broadcast so a reconnect cannot miss it, and
    # the stamp lands before the write so the file carries the dedupe identity.
    _stamp_log_entry(log_entry, _broadcast(payload))
    _persist_log()
    _persist_tail()
    return "", 204


@app.route("/stats", methods=["POST"])
def stats():
    """Receive character/combat stat updates. Merges players by name, replaces turn_order.

    Pass replace_players=true to replace the entire player list (use on /dnd load to
    prevent stale characters from a previous campaign persisting in the sidebar).
    """
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    if not data:
        return "", 204

    _effect_expire_events: list[dict] = []
    with _stats_lock:
        if "players" in data:
            # replace_players=true wipes the list first — used on campaign load
            if data.get("replace_players"):
                _current_stats["players"] = []
            existing_players: list = _current_stats.setdefault("players", [])
            for incoming in data["players"]:
                name = incoming.get("name")
                if not name:
                    continue
                match = next((p for p in existing_players if p.get("name") == name), None)
                # Keys prefixed with _ are mutation ops, not stored fields
                _MUTATION_KEYS = {
                    "_inventory_add", "_inventory_remove",
                    "_conditions_add", "_conditions_remove",
                    "_slot_use", "_slot_restore",
                    "_hd_use", "_hd_restore",
                    "_effect_start", "_effect_end",
                    "_milestone_inc", "_milestone_dec",
                }
                if match:
                    for key, val in incoming.items():
                        if key == "_inventory_add":
                            inv = match.setdefault("sheet", {}).setdefault("inventory", [])
                            if val not in inv:
                                inv.append(val)
                        elif key == "_inventory_remove":
                            sheet = match.get("sheet", {})
                            sheet["inventory"] = [
                                i for i in sheet.get("inventory", [])
                                if i.lower() != str(val).lower()
                            ]
                        elif key == "_conditions_add":
                            conds = match.setdefault("conditions", [])
                            if val not in conds:
                                conds.append(val)
                        elif key == "_conditions_remove":
                            match["conditions"] = [
                                c for c in match.get("conditions", [])
                                if c.lower() != str(val).lower()
                            ]
                        elif key == "_slot_use":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = min(slot["used"] + 1, slot.get("max", 99))
                        elif key == "_slot_restore":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = max(slot["used"] - 1, 0)
                        elif key == "_hd_use":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = max(hd.get("remaining", 0) - 1, 0)
                        elif key == "_hd_restore":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = min(
                                hd.get("remaining", 0) + int(val),
                                hd.get("max", 99)
                            )
                        elif key == "_effect_start":
                            # val is an effect dict: {name, duration_type, ...}
                            spell_name = val.get("name", "")
                            effects = match.setdefault("effects", [])
                            # Replace any existing effect with the same name
                            match["effects"] = [
                                e for e in effects
                                if e.get("name", "").lower() != spell_name.lower()
                            ]
                            match["effects"].append(val)
                            # Sync concentration field if this is a conc effect
                            if val.get("concentration") and spell_name:
                                match["concentration"] = spell_name
                        elif key == "_effect_end":
                            # val is the spell name string
                            spell_lower = str(val).lower()
                            removed = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() == spell_lower
                            ]
                            match["effects"] = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() != spell_lower
                            ]
                            # If the ended effect was concentration, also clear it
                            if removed and any(e.get("concentration") for e in removed):
                                if match.get("concentration", "").lower() == spell_lower:
                                    match["concentration"] = None
                        elif key == "_milestone_inc":
                            # val is the label string ("Inspiration" / "Bennie" / etc.).
                            # Increments milestones[label]; max = system-defined cap or 99.
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            cap = match.get("milestone_caps", {}).get(label, 99)
                            ms[label] = min(ms.get(label, 0) + 1, cap)
                        elif key == "_milestone_dec":
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            ms[label] = max(ms.get(label, 0) - 1, 0)
                            # Drop the key entirely when it hits 0 — keeps the
                            # sidebar clean (no "Bennie: 0" lingering).
                            if ms.get(label, 0) == 0:
                                ms.pop(label, None)
                        elif isinstance(val, dict) and isinstance(match.get(key), dict):
                            match[key].update(val)
                        else:
                            match[key] = val
                else:
                    # Strip mutation ops — they're meaningless for new players
                    existing_players.append(
                        {k: v for k, v in incoming.items() if k not in _MUTATION_KEYS}
                    )

        # turn_order replaces entirely (None = clear); also ticks round-based effects
        _effect_expire_events: list[dict] = []
        if "turn_order" in data:
            new_to = data["turn_order"]
            _current_stats["turn_order"] = new_to
            # Decrement round-based effects for the actor whose turn just started
            if new_to and isinstance(new_to, dict) and new_to.get("current"):
                actor = new_to["current"].lower()
                for p in _current_stats.get("players", []):
                    if p.get("name", "").lower() != actor:
                        continue
                    kept, expired = [], []
                    for eff in p.get("effects", []):
                        if eff.get("duration_type") == "rounds":
                            eff = dict(eff)  # don't mutate in-place
                            eff["duration_remaining"] = max(0, eff.get("duration_remaining", 1) - 1)
                            if eff["duration_remaining"] <= 0:
                                expired.append(eff)
                            else:
                                kept.append(eff)
                        else:
                            kept.append(eff)
                    p["effects"] = kept
                    for eff in expired:
                        was_conc = eff.get("concentration", False)
                        if was_conc and p.get("concentration", "").lower() == eff["name"].lower():
                            p["concentration"] = None
                        _effect_expire_events.append({
                            "owner": p["name"],
                            "name": eff["name"],
                            "was_concentration": was_conc,
                        })

        # world_time replaces entirely
        if "world_time" in data:
            _current_stats["world_time"] = data["world_time"]

        # factions replaces entirely ([] clears); validate and default missing standing
        if "factions" in data:
            _VALID_STANDINGS = {"Allied", "Friendly", "Neutral", "Unfriendly", "Hostile"}
            validated_factions = []
            for _f in data["factions"]:
                if "standing" not in _f or _f["standing"] not in _VALID_STANDINGS:
                    print(
                        f"[display] faction '{_f.get('name','?')}' missing/invalid standing "
                        f"— defaulting to Neutral. Valid: {sorted(_VALID_STANDINGS)}",
                        file=sys.stderr,
                    )
                    _f = dict(_f, standing="Neutral")
                validated_factions.append(_f)
            _current_stats["factions"] = validated_factions

        # quests replaces entirely ([] clears)
        if "quests" in data:
            _current_stats["quests"] = data["quests"]

        current = dict(_current_stats)

    # autorun_waiting / autorun_cycle — display-only signals, not stored in stats
    if "autorun_waiting" in data:
        if not data["autorun_waiting"]:
            # Turn resolved — clear stored cycle so reconnecting clients don't see stale pie
            global _autorun_cycle
            with _autorun_cycle_lock:
                _autorun_cycle = None
        _broadcast({"autorun_waiting": bool(data["autorun_waiting"])})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "quests", "replace_players", "sheet", "autorun_cycle")):
            return "", 204

    if "autorun_cycle" in data:
        with _autorun_cycle_lock:
            _autorun_cycle = data["autorun_cycle"]
        _broadcast({"autorun_cycle": data["autorun_cycle"]})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet", "autorun_threshold")):
            return "", 204

    if "autorun_threshold" in data:
        global _autorun_threshold
        val = data["autorun_threshold"]
        _autorun_threshold = int(val) if val is not None else None
        _broadcast({"autorun_threshold": _autorun_threshold})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet")):
            return "", 204

    # Explicit system-version override (e.g. push_stats.py --system-version 2024).
    # The value is opaque — display just renders it as a badge. Empty/missing
    # value clears the badge. Validation happens in the system module, not core.
    if "system_version" in data:
        sv_in = str(data.get("system_version") or "").strip()
        with _stats_lock:
            if sv_in:
                _current_stats["system_version"] = sv_in
            else:
                _current_stats.pop("system_version", None)
            current = dict(_current_stats)

    _persist_stats()
    _broadcast({"stats": current})
    # Broadcast any round-based effect expiries after the stats update
    for evt in _effect_expire_events:
        _broadcast({"effect_expired": evt})

    # Party size. Sends reach the queue immediately, so this no longer gates
    # anything — kept for `push_stats --autorun-threshold` compatibility.
    global _expected_count
    with _stats_lock:
        players = _current_stats.get("players", [])
    _expected_count = max(1, len(players))

    return "", 204


@app.route("/effects/expire", methods=["POST"])
def effects_expire():
    """Called by browser when a time-based effect countdown reaches zero.
    Removes the effect from stats, clears concentration if applicable,
    and broadcasts effect_expired to all connected clients.
    """
    if not _token_ok():
        return "Forbidden", 403
    data  = request.get_json(silent=True) or {}
    owner = data.get("owner", "").strip()
    name  = data.get("name", "").strip()
    if not owner or not name:
        return "", 400

    expire_evt = None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if p.get("name", "").lower() != owner.lower():
                continue
            was_conc   = False
            new_effects = []
            for e in p.get("effects", []):
                if e.get("name", "").lower() == name.lower():
                    was_conc = e.get("concentration", False)
                    if was_conc and p.get("concentration", "").lower() == name.lower():
                        p["concentration"] = None
                else:
                    new_effects.append(e)
            p["effects"] = new_effects
            expire_evt = {"owner": p["name"], "name": name, "was_concentration": was_conc}
            break
        current = dict(_current_stats)

    if expire_evt:
        _broadcast({"effect_expired": expire_evt})
    _broadcast({"stats": current})
    _persist_stats()
    return "", 204


@app.route("/audio-toggle", methods=["POST"])
def audio_toggle():
    """Enable/disable ambient or SFX from the browser toggle switches.

    Body: {"ambient": true|false, "sfx": true|false}  (either or both keys)
    Response: {"ambient": bool, "sfx": bool, "available": bool}
    Broadcasts audio_state to all connected browsers so every device syncs.
    """
    data = request.get_json(silent=True) or {}
    if _audio:
        if "sfx" in data:
            _audio.set_sfx(bool(data["sfx"]))
        state = _audio.get_state()
    else:
        state = {"sfx": False, "available": False}
    return state, 200


@app.route("/narration-pref", methods=["POST"])
def narration_pref():
    """Set the narration-length target the GM aims for each turn.

    Body: {"target_words": int}.  0 clears the preference. Persisted to the
    display dir as a plain integer; check_input.py reads it and prepends a
    directive to queued player input so the GM honors it that turn — no
    separate file read required on the GM side.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    try:
        n = int(data.get("target_words", 0))
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5000, n))
    try:
        if n:
            with open(NARRATION_TARGET, "w", encoding="utf-8") as f:
                f.write(str(n))
        elif os.path.exists(NARRATION_TARGET):
            os.remove(NARRATION_TARGET)
    except OSError:
        pass
    return {"target_words": n}, 200


@app.route("/roll-pref", methods=["POST"])
def roll_pref():
    """Per-character roll preference. Body: {"character": str, "mode": "auto"|"players"}.

    Persisted to roll_prefs.json; check_input.py surfaces each override as a
    [[<Char> roll mode: …]] directive so the GM honors it for that character,
    overriding the campaign-wide roll_mode in state.md.

    The character name is validated against the active party via _char_ok before
    persistence — otherwise a crafted value could smuggle prompt text into the GM
    through the [[<Char> roll mode: …]] template that check_input.py emits.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    char = (data.get("character") or "").strip()
    mode = (data.get("mode") or "").strip().lower()
    if not char or mode not in ("auto", "players"):
        return {"ok": False}, 400
    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(char, known):
        return "Forbidden", 403
    try:
        prefs = {}
        if os.path.exists(ROLL_PREFS_FILE):
            with open(ROLL_PREFS_FILE, encoding="utf-8") as f:
                prefs = json.load(f)
        prefs[char] = mode
        with open(ROLL_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except (OSError, ValueError):
        pass
    return {"ok": True, "character": char, "mode": mode}, 200


# ─── Narrator voice (Gemini Flash TTS) ────────────────────────────────────────
# Voice selection persists per-campaign in state.md → ## Session Flags →
# `tts_voice: <name>`. Read at /index render, written by POST /voice.

_VOICE_PAT = re.compile(r"^\s*tts_voice:\s*([A-Za-z]+)\s*$", re.MULTILINE)


def _active_campaign_name() -> Optional[str]:
    try:
        return open(CAMP_FILE, encoding="utf-8").read().strip() or None
    except OSError:
        return None


def _read_narrator_voice() -> str:
    """Return the active campaign's tts_voice, or the module default."""
    if _tts is None:
        return ""
    name = _active_campaign_name()
    if not name:
        return _tts.DEFAULT_VOICE
    try:
        state = _find_display_campaign(name) / "state.md"
        if not state.exists():
            return _tts.DEFAULT_VOICE
        text = state.read_text(errors="replace", encoding="utf-8")
    except (OSError, ValueError):
        return _tts.DEFAULT_VOICE
    m = _VOICE_PAT.search(text)
    if not m:
        return _tts.DEFAULT_VOICE
    v = m.group(1).strip()
    return v if v in _tts.VALID_VOICES else _tts.DEFAULT_VOICE


def _write_narrator_voice(voice: str) -> bool:
    """Persist tts_voice to the active campaign's state.md → ## Session Flags."""
    if _tts is None or voice not in _tts.VALID_VOICES:
        return False
    name = _active_campaign_name()
    if not name:
        return False
    try:
        state = _find_display_campaign(name) / "state.md"
        text = state.read_text(errors="replace", encoding="utf-8") if state.exists() else ""
    except (OSError, ValueError):
        return False

    new_line = f"tts_voice: {voice}"
    if _VOICE_PAT.search(text):
        text = _VOICE_PAT.sub(new_line, text, count=1)
    else:
        if "## Session Flags" in text:
            text = re.sub(
                r"(## Session Flags\n(?:\*\(.*?\)\*\n)?)",
                r"\1" + new_line + "\n",
                text,
                count=1,
            )
        else:
            sep = "" if text.endswith("\n") else "\n"
            text = f"{text}{sep}\n## Session Flags\n{new_line}\n"

    try:
        state.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


@app.route("/tts", methods=["POST"])
def tts_synthesize():
    """Synthesize a narrator/NPC block to L16 PCM via Gemini Flash TTS.

    Body: {"text": str, "voice": str (optional)}
    Response: raw L16 PCM bytes, Content-Type: audio/L16;codec=pcm;rate=24000
    Failures: 503 (no key / module unavailable), 400 (bad input), 502 (upstream)
    """
    if _tts is None:
        return "TTS module unavailable", 503
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or _tts.DEFAULT_VOICE).strip()
    if not text:
        return "empty text", 400
    if len(text) > _tts.MAX_TEXT_CHARS:
        text = text[: _tts.MAX_TEXT_CHARS]
    if voice not in _tts.VALID_VOICES:
        voice = _tts.DEFAULT_VOICE
    if _tts.key_source() == "unset":
        return "TTS not configured (see docs/SKILL-tts.md)", 503
    try:
        pcm = _tts.synthesize_strict(text, voice)
    except _tts.TtsError as e:
        return f"TTS upstream: {e}", 502
    return Response(
        pcm,
        mimetype="audio/L16;codec=pcm;rate=24000",
        headers={
            "X-Audio-Chars": str(len(text)),
            "X-Audio-Voice": voice,
            "Cache-Control": "no-store",
        },
    )


@app.route("/voice", methods=["POST"])
def tts_voice():
    """Persist narrator voice selection for the active campaign."""
    if _tts is None:
        return jsonify({"voice": "", "persisted": False}), 503
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    voice = (data.get("voice") or "").strip()
    if voice not in _tts.VALID_VOICES:
        return jsonify({"error": "invalid voice"}), 400
    ok = _write_narrator_voice(voice)
    return jsonify({"voice": voice, "persisted": ok}), 200


@app.route("/audio/sfx/<name>")
def audio_sfx(name):
    """Serve a synthesized SFX WAV for the given effect name."""
    if not _audio:
        return "Audio not available", 503
    wav = _audio.get_sfx_wav(name)
    if wav is None:
        return "Not found", 404
    return Response(wav, mimetype="audio/wav",
                    headers={"Cache-Control": "public, max-age=3600"})


def _do_clear() -> None:
    """Wipe text log, stats, sent input and the queued-input files.

    Shared by the manual /clear route and the automatic clear that fires when /chunk
    registers a different campaign. Without the input half, a party's queued actions
    and the sidebar's turn order survive a campaign switch and leak into the next
    session — which is why /dnd new used to need a manual /clear to look right.

    Locks are taken one at a time rather than held across the whole wipe, and never
    nested, so a send racing this cannot deadlock: _send takes _sent_lock, then
    _queue_lock, then _queue_status_lock, and this takes the same three in the
    same order. The window it leaves — a send appending to .input_queue between
    the unlink and the broadcast — is the one a plain /clear always had.
    """
    global _scene_buffer, _current_stats, _input_queue, _current_scene_name
    with _text_log_lock:
        _text_log.clear()
    with _stats_lock:
        _current_stats = {}
    _scene_buffer = []
    # N3: a stale scene name ("dungeon", from the previous campaign's last
    # detection) must not carry over, or the scene title and background are wrong
    # until enough new narration re-triggers detection.
    _current_scene_name = _DEFAULT_SCENE
    with _sent_lock:
        _sent.clear()
    with _input_lock:
        _input_queue = []
    with _queue_status_lock:
        _queue_status.clear()
    for path in (LOG_FILE, STATS_FILE, QUEUE_FILE, TRIGGER_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    # The scene rides along in the broadcast so browsers already connected pick the
    # reset up now; a new connection reads _current_scene_name fresh on /stream.
    _broadcast({"clear": True, "sent_log": {}, "queue_status": [], "pending_input": [],
                "scene": SCENES[_DEFAULT_SCENE] | {"name": _DEFAULT_SCENE}})


@app.route("/clear", methods=["POST"])
def clear():
    """Wipe text log AND stats, broadcast clear to all connected browsers.

    Called on /dnd new (fresh campaign). Ensures sidebar shows no stale characters.
    """
    if not _token_ok():
        return "Forbidden", 403
    _do_clear()
    return "", 204


@app.route("/help-request", methods=["POST"])
def help_request():
    """Spawn dm_help.py to generate and send an on-demand DM hint.

    Protected by an O_EXCL lock file — concurrent requests return 409
    so multiple players clicking the button never duplicates execution.
    Lock is released by dm_help.py in its finally block.
    """
    if not _token_ok():
        return "Forbidden", 403

    # Atomic lock: O_EXCL fails if file already exists — no race condition
    try:
        fd = os.open(HELP_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, encoding="utf-8")
        os.close(fd)
    except FileExistsError:
        return "Already running", 409

    # Read active campaign name
    try:
        campaign = open(CAMP_FILE, encoding="utf-8").read().strip()
    except FileNotFoundError:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    if not campaign:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    dm_help_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dm_help.py")
    subprocess.Popen(
        [sys.executable, dm_help_py, "--campaign", campaign],
        close_fds=True,
        start_new_session=True,
    )
    return "", 202


@app.route("/player-input", methods=["POST"])
def player_input():
    """Queue a player action (legacy; the companion UI uses /player-input/send).

    Body: {"character": "Mira", "text": "I draw my rapier", "hold": false}
    Appends to the same .input_queue every consumer claims, so a GM running
    wrapper.py sees it as well as one running check_input.py. Broadcasts
    pending_input event to all connected browsers.
    """
    if not _token_ok():
        return "Forbidden", 403

    data = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", "Party"))[:50].strip()
    # One `[Char]: text` line: a bracket or newline here would forge a second
    # action, and wrapper.py drops a whole batch over one malformed line.
    text = _sanitize_input(str(data.get("text", "")))
    if not _CHAR_NAME_RE.match(character):
        return "Bad Request", 400
    if not text:
        return "empty", 400

    entry = {
        "character": character,
        "text": text,
        "hold": bool(data.get("hold", False)),
        "timestamp": _time.time(),
    }

    if not _queue_append({character: text}, replace=False):
        return "Error", 500
    with _input_lock:
        _input_queue.append(entry)
        current = list(_input_queue)
    _broadcast({"pending_input": current})
    return "", 204


# ── Spendable-feature offers (RS1) ───────────────────────────────────────────
# A "prescribed roll" arrives fully decided, so a player holding something that
# would improve it has nothing to press. An offer is the DM naming what is on
# the table for THIS roll: `--offer "Kenku Recall:advantage"`. The phone renders
# one button per offer and the player picks one or none.
#
# The engine owns the rules, so an offer carries no feature-specific logic: it is
# a shape the GM describes with (advantage / disadvantage / a flat modifier), and
# the server refuses anything it cannot honestly apply. An offer whose effect
# does nothing is worse than no offer — it spends a resource for nothing — so an
# unparseable, empty or duplicated offer is a 400 with a sentence the GM reads,
# and no request is registered.
#
# The spec claimed `_dice_pending_snapshot` already shipped the whole `meta` and
# that a `_dice_done_meta` ring kept it for late joiners. Neither is true on this
# tree: the snapshot carries request_id/pending/label only, and there is no
# `_dice_done_meta` at all. Offers are added to the snapshot here rather than
# riding a meta that was never being shipped.
_OFFER_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_OFFER_INT_RE = re.compile(r"^[+-]?\d+$")
_OFFER_DIE_RE = re.compile(r"^(\d{1,2})d(\d{1,3})$")


def _offer_slug(label: str) -> str:
    """The spend-request key for a label: 'Kenku Recall' → 'kenku_recall'.

    Derived, not declared, so a phone cannot name a feature the GM did not put
    on the table by guessing a key — the offer list is the only authority, and
    the slug is just how the phone refers to an entry in it.
    """
    return _OFFER_SLUG_STRIP.sub("_", label.lower()).strip("_")[:40]


def _parse_offers(raw) -> tuple[list, str]:
    """Normalise raw offer strings into the request's meta["offers"].

    Returns ``(offers, "")`` or ``([], "<a sentence the GM reads>")``. Every
    refusal carries a sentence: a silent 400 here would be indistinguishable
    from a network blip, which is exactly the failure the four checks in
    player_dice exist to avoid.
    """
    if raw is None:
        return [], ""
    if not isinstance(raw, list):
        return [], 'offers must be a list, e.g. ["Kenku Recall:advantage"]'
    offers: list = []
    seen: set = set()
    for entry in raw:
        if not isinstance(entry, str):
            return [], f"offer {entry!r} is not text"
        if ":" not in entry:
            return [], (f'offer {entry!r} has no effect — write it '
                        f'"Label:advantage", "Label:+2" or "Label:1d6"')
        label, _, effect_str = entry.partition(":")
        label = label.strip()
        effect_str = effect_str.strip()
        if not label:
            return [], f"offer {entry!r} has no label"
        if len(label) > 60:
            return [], f"offer label {label!r} is longer than 60 characters"
        effect: dict = {}
        terms = [t.strip() for t in effect_str.split(",")]
        if not any(terms):
            return [], (f'offer {label!r} has an empty effect — an offer that '
                        f'does nothing spends a resource for nothing')
        for term in terms:
            if not term:
                return [], (f'offer {label!r} has an empty effect term in '
                            f'{effect_str!r}')
            if term in ("advantage", "disadvantage"):
                value = term == "advantage"
                if "advantage" in effect and effect["advantage"] is not value:
                    return [], (f'offer {label!r} says both advantage and '
                                f'disadvantage')
                effect["advantage"] = value
            elif _OFFER_INT_RE.match(term):
                n = int(term)
                if abs(n) > 100:
                    return [], (f'offer {label!r} has an out-of-range modifier '
                                f'{n} — the pad works in -100..100')
                effect["modifier"] = n
            elif _OFFER_DIE_RE.match(term):
                die_m = _OFFER_DIE_RE.match(term)
                die_n, die_sides = int(die_m.group(1)), int(die_m.group(2))
                if not (1 <= die_n <= 20 and 2 <= die_sides <= 100):
                    return [], (f'offer {label!r} has an out-of-range bonus die '
                                f'({term}) — the pad works in 1..20 dice of 2..100')
                effect["bonus"] = term
            else:
                return [], (f'offer {label!r} has an unknown effect {term!r} — '
                            f'use advantage, disadvantage, a flat +/-N, or NdM')
        if not effect:
            return [], (f'offer {label!r} has an empty effect — an offer that '
                        f'does nothing spends a resource for nothing')
        key = _offer_slug(label)
        if not key:
            return [], (f'offer {label!r} has no letters or digits to name it by')
        if key in seen:
            return [], (f'two offers share the key {key!r} — the phone refers to '
                        f'an offer by that key, so they must be distinguishable')
        seen.add(key)
        offers.append({"key": key, "label": label, "effect": effect})
    return offers, ""


@app.route("/player-input/dice", methods=["POST"])
def player_dice():
    """Server-side dice roll submitted from a player's phone.

    Body: {"character": "Piper", "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional),
           "spend": "kenku_recall"  (optional — an offer's key, see /dice-request)}

    Rolls server-side (secrets.randbelow → uniform, non-spoofable), broadcasts
    a dice-typed entry on the feed, and returns the result so the phone can
    finish its slot-machine animation on the real value.

    `spend` names one of the offers the GM put on this roll. It is checked
    against the server's own copy of that list and never against anything the
    phone asserts, so a phone cannot spend a feature the GM never offered and
    cannot make the roll line name one. Checks 1 (a real pending request), 2
    (the key is one of its offers) and 4 (the effect does not double-count)
    live here; check 3 — the counter must be > 0 — arrives with the counter.
    """
    if not _token_ok():
        return "Forbidden", 403

    data = request.get_json(force=True, silent=True) or {}
    character = re.sub(r"[`\\$]", "", str(data.get("character", "Player"))[:50]).strip() or "Player"
    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    req_id    = str(data.get("request_id", "")).strip()[:24]
    spend_key = str(data.get("spend", "") or "").strip()[:40]
    bonus_in  = str(data.get("bonus", "") or "").strip().lower()

    # ── The spend checks, in order. 1, 2, 4. ────────────────────────────────
    # Read the pending entry once; the correlation block further down re-reads
    # it under the lock to drop the character from its expected-rollers set.
    offer = None
    if spend_key:
        entry = None
        if not req_id:
            return jsonify({"error": "a spend needs a prescribed roll — the GM "
                                     "did not ask for one on this character"}), 400
        with _dice_pending_lock:
            entry = _dice_pending.get(req_id)
        if entry is None:
            return jsonify({"error": "that dice request is no longer pending — "
                                     "nothing was spent"}), 400
        offer = next((o for o in (entry["meta"].get("offers") or [])
                      if o["key"] == spend_key), None)
        if offer is None:
            return jsonify({"error": f"the GM did not offer {spend_key!r} on "
                                     f"this roll — nothing was spent"}), 400

    m = re.fullmatch(r"(\d{1,2})d(\d{1,3})", spec)
    if not m:
        return jsonify({"error": "bad spec"}), 400
    n_dice, n_sides = int(m.group(1)), int(m.group(2))
    if not (1 <= n_dice <= 20 and 2 <= n_sides <= 100):
        return jsonify({"error": "out of range"}), 400
    modifier = max(-100, min(100, modifier))

    # A bonus die is its own NdM, parsed with the same regex and the same bounds
    # as the base die. It is a SEPARATE roll and it is not optional to parse
    # here: folding it into `modifier` would render the pad as 1d20+4, which
    # claims a flat 4 rather than a die that can come up 1. The table reads the
    # pad, not the JSON, so that error would be displayed, not hidden.
    # A bonus die sent by the PHONE is not trusted. `bonus` is the server's
    # answer to what the spent offer carried; if the phone names one itself it
    # is asking the display to add a die the GM never offered, which is the
    # same forgery as naming a feature. Refused rather than ignored, so the
    # player is told rather than quietly given a straight roll.
    offered_bonus = str(offer["effect"].get("bonus", "") if offer else "").strip().lower()
    if bonus_in and bonus_in != offered_bonus:
        # Either there is no bonus die on the table at all, or the phone is
        # asking for a different one than the GM offered. Both are refused
        # rather than silently corrected: a player who sees the roll they asked
        # for replaced by a different one has learned to distrust the pad.
        return jsonify({"error": (
            "this roll has no bonus die to spend" if not offered_bonus
            else f"the GM offered a {offered_bonus} bonus die, not {bonus_in}"
        ) + " — nothing was spent"}), 400

    bonus_spec = offered_bonus
    if bonus_spec:
        bm = re.fullmatch(r"(\d{1,2})d(\d{1,3})", bonus_spec)
        if not bm:
            return jsonify({"error": "bad bonus spec"}), 400
        b_dice, b_sides = int(bm.group(1)), int(bm.group(2))
        if not (1 <= b_dice <= 20 and 2 <= b_sides <= 100):
            return jsonify({"error": "bonus out of range"}), 400
    else:
        bonus_spec, b_dice, b_sides = "", 0, 0

    def _roll_once(n=None, sides=None) -> list[int]:
        return [secrets.randbelow(sides or n_sides) + 1 for _ in range(n or n_dice)]

    # 4. The effect does not double-count. Spending an advantage offer on a roll
    #    the GM already made advantageous spends a per-rest resource for nothing,
    #    and the roll line would name a feature that did not decide the roll.
    #    Refused, not clamped: "these do not stack" is a table ruling.
    if offer is not None and offer["effect"].get("advantage") is not None:
        if adv != "normal":
            return jsonify({"error": f"{offer['label']} would double-count — this "
                                     f"roll is already {adv}"}), 400
        adv = "advantage" if offer["effect"]["advantage"] else "disadvantage"
    if offer is not None and offer["effect"].get("modifier"):
        modifier = max(-100, min(100, modifier + int(offer["effect"]["modifier"])))

    if adv in ("advantage", "disadvantage") and spec == "1d20":
        r1, r2 = _roll_once(), _roll_once()
        chosen = max(r1[0], r2[0]) if adv == "advantage" else min(r1[0], r2[0])
        rolls  = [chosen]
        kept   = [chosen]
        both   = [r1[0], r2[0]]
    else:
        rolls = _roll_once()
        kept  = rolls
        both  = None

    # The bonus die is rolled AFTER advantage has resolved, and is added to the
    # kept face — never to the second d20. 2014 SRD, Bardic Inspiration: "The
    # creature can wait until after it rolls the d20 before deciding to use the
    # Bardic Inspiration die". A d4 that could change which d20 is kept would not
    # be a bonus die, it would be a third d20 with no rules behind it.
    bonus_faces = _roll_once(b_dice, b_sides) if bonus_spec else []

    subtotal = sum(kept)
    total    = subtotal + sum(bonus_faces) + modifier
    mod_str  = (f"+{modifier}" if modifier > 0 else (str(modifier) if modifier < 0 else ""))
    breakdown = f"[{', '.join(str(r) for r in (both or rolls))}]"
    if both is not None:
        breakdown += f" → keep {kept[0]} ({adv})"
    # `+ 1d4 [3]`, never `+3`. A die that can come up 1 must be shown as a die,
    # in the table's own notation, or the display is claiming a flat number it
    # did not roll.
    if bonus_faces:
        breakdown += f" + {bonus_spec} [{', '.join(str(f) for f in bonus_faces)}]"
    if modifier:
        breakdown += f" {mod_str}"
    # The header names the base die and the flat modifier only. The bonus die is
    # a separate roll and is named in the breakdown, where its faces are shown.
    head_spec = f"{spec}{bonus_spec}" if bonus_spec else spec
    # The spend is named in the transcript, which is what makes the claim
    # auditable while the counter is not yet authoritative. It is named in
    # parentheses after the check, never folded into the math: the table reads
    # the pad, and "1d20+6: [17] = 23 — Stealth (Kenku Recall)" claims one thing
    # while the bonus-die shape claims another.
    spend_label = offer["label"] if offer else ""
    if label and spend_label:
        suffix = f" — {label} ({spend_label})"
    elif label:
        suffix = f" — {label}"
    elif spend_label:
        suffix = f" ({spend_label})"
    else:
        suffix = ""
    text   = f"{character} rolls {head_spec}{mod_str}: {breakdown} = {total}{suffix}"

    payload   = {"text": text, "dice": True}
    log_entry = {"text": text, "dice": True}
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)
    # Broadcast, then stamp, then persist: see the note in chunk().
    _stamp_log_entry(log_entry, _broadcast(payload))
    _persist_log()
    _persist_tail()

    # Correlate against any pending DM request. Case-insensitive match on the
    # character name — drop them from the request's expected-rollers set.
    pending_changed = False
    if req_id:
        with _dice_pending_lock:
            entry = _dice_pending.get(req_id)
            if entry is not None:
                ci = character.lower()
                matched = next((c for c in entry["chars"] if c.lower() == ci), None)
                if matched is not None:
                    entry["chars"].discard(matched)
                    entry.setdefault("results", []).append(text)
                    pending_changed = True
                    if not entry["chars"]:
                        _dice_pending.pop(req_id, None)
                        _dice_finish(req_id, entry["results"])
    if pending_changed:
        _broadcast({"dice_pending": _dice_pending_snapshot()})

    return jsonify({
        "character": character,
        "spec": spec,
        "modifier": modifier,
        "advantage": adv,
        "rolls": rolls,
        "kept": kept,
        "both": both,
        # The pad reads `kept` and locks the reel to it. With a bonus die the
        # kept face alone does not explain the total, so the bonus spec and its
        # faces travel with it and the pad renders the same math the server did.
        "bonus": bonus_spec or None,
        "bonus_faces": bonus_faces,
        "subtotal": subtotal,
        "total": total,
        "text": text,
        "request_id": req_id or None,
        "spend": spend_key or None,
        "spend_label": spend_label or None,
    }), 200


@app.route("/dice-request", methods=["POST"])
def dice_request():
    """GM-initiated dice request — broadcast to player phones (no persistence).

    Body: {"character": "Piper" | "any",
           "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional),
           "dc": 15  (optional, informational),
           "offers": ["Kenku Recall:advantage"]  (optional, repeatable)}

    Phones bound to ?character=<name> match case-insensitively. "any" / ""
    targets every phone. No state stored — late-joining phones will not see
    requests issued before they connected.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    raw_char  = data.get("characters") if "characters" in data else data.get("character", "any")
    if isinstance(raw_char, list):
        chars = [str(c).strip() for c in raw_char if str(c).strip()]
    else:
        chars = [c.strip() for c in re.sub(r"[`\\$]", "", str(raw_char))[:200].split(",") if c.strip()]
    if not chars:
        chars = ["any"]

    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    dc        = data.get("dc")

    if not re.fullmatch(r"\d{1,2}d\d{1,3}", spec):
        return jsonify({"error": "bad spec"}), 400
    if adv not in ("normal", "advantage", "disadvantage"):
        adv = "normal"
    modifier = max(-100, min(100, modifier))
    dc_val   = int(dc) if isinstance(dc, (int, float)) else None

    # Offers are parsed and validated BEFORE the request id is minted, so a
    # malformed offer registers nothing at all — there is no half-issued request
    # for a phone to render buttons against.
    offers, offer_error = _parse_offers(data.get("offers"))
    if offer_error:
        return jsonify({"error": offer_error}), 400

    request_id = secrets.token_hex(6)

    # Only register pending entries for explicit named targets. "any" is fire-and-forget.
    trackable = [c for c in chars if c.lower() != "any"]
    if trackable:
        with _dice_pending_lock:
            _dice_pending[request_id] = {
                "chars": set(trackable),
                "meta": {"spec": spec, "modifier": modifier, "advantage": adv,
                         "label": label, "dc": dc_val, "offers": offers},
                "started_at": time.time(),
            }
        _broadcast({"dice_pending": _dice_pending_snapshot()})

    # Targets with no live phone bound → the main display should roll on-screen.
    onscreen_targets = [c for c in chars if c.lower() != "any" and not _phone_present(c)]
    payload = {
        "dice_request": {
            "request_id": request_id,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",   # legacy single-target field
            "onscreen_targets": onscreen_targets,
            "spec": spec,
            "modifier": modifier,
            "advantage": adv,
            "label": label,
            "dc": dc_val,
            "offers": offers,
        }
    }
    _broadcast(payload)
    return jsonify({
        "request_id": request_id,
        "pending": sorted(trackable),
        "complete": not trackable,
    }), 200


@app.route("/dice-request/<request_id>", methods=["GET"])
def dice_request_status(request_id):
    """Poll a dice request's completion state.

    Returns 200 with {known, complete, pending, results, label, started_at}, plus
    cancelled once the GM cancelled it. results holds each roll's text so far. A
    finished request keeps its results (the last _DICE_DONE_KEEP of them).

    `known` separates "this request finished" from "this id was never issued".
    Both used to answer complete=True, so send.py --wait printed "all rolls
    received" for a request that never existed and the GM moved on having rolled
    nothing. An unknown id is not a finished request; it is reported 404 so the
    caller fails loudly instead of proceeding on a fiction.
    """
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        entry = _dice_pending.get(request_id)
        if entry is None:
            if request_id not in _dice_done:
                # Never issued, or aged out of the _DICE_DONE_KEEP ring. Either
                # way there is no request to be complete: say so.
                return jsonify({"known": False, "complete": False, "pending": [],
                                "cancelled": False, "results": []}), 404
            return jsonify({"known": True, "complete": True, "pending": [],
                            "cancelled": request_id in _dice_cancelled,
                            "results": list(_dice_done.get(request_id, []))}), 200
        if not entry["chars"]:
            return jsonify({"known": True, "complete": True, "pending": [],
                            "cancelled": request_id in _dice_cancelled,
                            "results": list(_dice_done.get(request_id, []))}), 200
        return jsonify({
            "known": True,
            "complete": False,
            "pending": sorted(entry["chars"]),
            "results": list(entry.get("results", [])),
            "label": entry["meta"].get("label", ""),
            "started_at": entry["started_at"],
        }), 200


@app.route("/dice-request/<request_id>", methods=["DELETE"])
def dice_request_cancel(request_id):
    """Cancel a pending dice request (GM gave up waiting / moved on)."""
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        entry = _dice_pending.pop(request_id, None)
        if entry is not None:
            _dice_finish(request_id, entry.get("results", []), cancelled=True)
    _broadcast({"dice_pending": _dice_pending_snapshot(), "dice_request_cancelled": request_id})
    return "", 204


@app.route("/character/<character>", methods=["GET"])
def get_character_sheet(character):
    """Return the markdown content of a PC sheet for the active campaign.

    Used by the phone's Character tab. Resolves the active campaign from
    CAMP_FILE, then reads (via paths.py, which honors GM_CAMPAIGN_ROOT):
        <root>/campaigns/<campaign>/characters/<character>.md

    An NPC on stage resolves from <campaign>/npc-files/<name>.md (slug match,
    e.g. "The Count" -> the-count.md) after the PC locations below.

    Falls back to the global roster (<root>/characters/<character>.md) if the
    campaign-side file is missing — useful when the character was just imported
    but not yet replicated. The legacy ~/.claude/dnd/characters/ path is kept as
    a final fallback for older Claude-skill installs.

    Returns text/markdown so the phone can render in JS without server-side
    dependencies (no `markdown` lib required).
    """
    if not _token_ok():
        return "Forbidden", 403

    safe = re.sub(r"[^A-Za-z0-9 _-]", "", character).strip()[:50]
    if not safe:
        return "Bad character name", 400

    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    try:
        _campaign_dir_for_name(camp)
    except (TypeError, ValueError, OSError):
        camp = ""

    candidates = []
    if camp:
        candidates.append(str(_find_display_campaign(camp) / "characters" / f"{safe}.md"))
    candidates.append(str(_characters_dir() / f"{safe}.md"))
    # Legacy Claude-skill global roster — final fallback for older installs.
    candidates.append(os.path.expanduser(f"~/.claude/dnd/characters/{safe}.md"))

    # NPC sheets: <campaign>/npc-files/<slug>.md, so an on-stage NPC has a sheet.
    # Tried last so a PC of the same name always wins. `safe` is already reduced to
    # [A-Za-z0-9 _-], so the slug cannot contain a separator or "..".
    if camp:
        slug = re.sub(r"[^a-z0-9]+", "-", safe.lower()).strip("-")
        if slug:
            npc_dir = (_find_display_campaign(camp) / "npc-files").resolve()
            npc_path = (npc_dir / f"{slug}.md").resolve()
            if npc_path.parent == npc_dir:
                candidates.append(str(npc_path))
                # Files named with the original casing/spaces ("Count Varga.md").
                alt = (npc_dir / f"{safe}.md").resolve()
                if alt.parent == npc_dir:
                    candidates.append(str(alt))

    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    body = f.read()
            except Exception as e:
                return f"Read error: {e}", 500
            return Response(body, mimetype="text/markdown; charset=utf-8")

    return f"No sheet found for '{safe}' in campaign '{camp}'", 404




@app.route("/device/approve", methods=["POST"])
def device_approve():
    """DM approves a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _approved_devices.add(device_id)
    _broadcast({"device_approved": device_id})
    return "", 204


@app.route("/device/deny", methods=["POST"])
def device_deny():
    """DM denies a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _denied_devices.add(device_id)
    _broadcast({"device_denied": device_id})
    return "", 204


def _roster_refusal(character: str):
    """Validate a sender against the roster; return an error Response or None.

    An empty roster (stats.json wiped by hand, or a display that never got a
    push_stats seed) used to fall through _char_ok, whose `known` test is skipped
    when the roster is empty only for well-formed names, and the browser saw a
    bare 403 it could not explain. Now the reply names the cause so the panel can
    show it. The validation itself is unchanged: an unknown name is still refused.
    """
    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", []) if p.get("name")}
    if not known:
        resp = jsonify({"error": "no_roster",
                        "message": "The display has no party roster, so input is "
                                   "disabled. The GM must re-seed it with push_stats.py "
                                   "(do not edit stats.json by hand)."})
        resp.status_code = 409
        return resp
    if not _CHAR_NAME_RE.match(character):
        return jsonify({"error": "bad_name",
                        "message": "That character name is not valid."}), 403
    if not _char_ok(character, known):
        return jsonify({"error": "not_in_party",
                        "message": f"'{character}' is not in the party roster."}), 403
    return None


# Words that make a line a command rather than a question (the localdm loop's
# list): "attack the frog" is an action for the GM, "how far is the frog" is not.
_FIGHT_VERBS = ("move", "attack", "cast", "dash", "disengage", "dodge", "stand",
                "death-save", "end-turn")


def _fight_answer(character: str, text: str) -> Optional[list]:
    """Answer a fight-time question from the engine, or None to queue the line.

    "How far is the nearest kobold" and "what can I do" are facts the engine
    already holds, so they never need a model call (tactics/fightq.py, the same
    classifier the terminal loops use). Only while a fight is active and only
    for the asking character's own token. Read-only: nothing is rolled, moved
    or queued, and any failure falls back to queueing the line as before.
    """
    camp = _active_campaign_name()
    if not camp:
        return None
    try:
        from tactics import fightq, state as _state
        q = fightq.classify(text, _FIGHT_VERBS, scope="fight")
        if q is None:
            return None
        enc = _state.load(_state.encounter_path(_find_display_campaign(camp)))
        if enc.status != "active":
            return None
        want = character.strip().lower()
        pc = next((t for t in enc.tokens.values() if t.side == "pc"
                   and want in (t.name.lower(), t.id.lower(), t.name.lower().split(" ")[0])), None)
        if pc is None:
            return None
        lines = fightq.answer(enc, pc.id, q)
        return [str(x) for x in lines] or None
    except Exception:       # a bad save or an engine surprise must not eat the player's line
        return None


@app.route("/player-input/send", methods=["POST"])
def send_input():
    """Send a player action straight to the DM-gated queue (.input_queue).

    One tap, no staging and no Ready step — the action is in the queue as soon
    as this returns. The DM still controls *when* it reaches Claude.
    Broadcasts sent_log to all displays.

    Body: {"character": "Mira", "text": "draws her rapier"}
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    text      = _sanitize_input(str(data.get("text", "")))

    if not character or not text:
        return "Bad Request", 400

    refusal = _roster_refusal(character)
    if refusal is not None:
        return refusal

    lines = _fight_answer(character, text)
    if lines is not None:
        return jsonify({"answered": True, "lines": lines})

    if not _send(character, text):
        return "Error", 500
    return "", 204


@app.route("/player-input/recall", methods=["POST"])
def recall_input():
    """Pull a sent action back out, but only while it is still queued.

    Once the action has left .input_queue it can't be taken back. The reply is
    409 "No longer queued; delivery is unconfirmed": the app does not claim it
    was delivered, because a missing line may equally mean it was lost.

    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    if not character:
        return "Bad Request", 400

    in_queue = character in _queued_characters()
    recalled = _queue_remove(character)
    with _sent_lock:
        existed = _sent.pop(character, None) is not None
        snap = _sent_snapshot()
    if not recalled and not existed:
        return "Gone", 204

    with _queue_status_lock:
        if character in _queue_status:
            _queue_status.remove(character)
        status = list(_queue_status)

    _broadcast({"sent_log": snap, "queue_status": status})
    if recalled:
        return "", 204
    # Not in .input_queue. That means a consumer took it OR it was lost; the app
    # cannot tell which (only wrapper.py reports consumption). Never say "safely
    # delivered" for something we cannot confirm.
    resp = Response("No longer queued; delivery is unconfirmed", status=409,
                    mimetype="text/plain")
    resp.headers["X-Recall-State"] = "missing" if not in_queue else "unknown"
    return resp


@app.route("/player-input/skip", methods=["POST"])
def skip_input():
    """Skip a character's turn — sends a 'skips their turn' entry.

    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    if not character:
        return "Bad Request", 400

    refusal = _roster_refusal(character)
    if refusal is not None:
        return refusal

    if not _send(character, "skips their turn"):
        return "Error", 500
    return "", 204


@app.route("/queue/consumed", methods=["POST"])
def queue_consumed():
    """Called by wrapper.py after it injects .input_queue into the PTY.

    Clears the server-side queue_status and the sent log (the actions are now
    in Claude's context, so they are no longer recallable) and broadcasts to all
    clients so the 'Queued — fires on DM Enter' indicator disappears on every
    display. Token required (called from localhost by the wrapper, but checked
    for consistency).
    """
    if not _token_ok():
        return "Forbidden", 403
    with _queue_status_lock:
        _queue_status.clear()
    with _sent_lock:
        _sent.clear()
    with _input_lock:
        _input_queue.clear()
    _broadcast({"queue_status": [], "sent_log": {}, "pending_input": [],
                "dm_processing": True})
    return "", 204


@app.route("/player-input/submit-now", methods=["POST"])
def submit_now():
    """Promote .input_queue → .input_trigger for immediate injection.

    Called by the DM or Claude when they want to process queued player actions
    right now rather than waiting for the DM's next CLI Enter press.
    Token required (DM-only action).
    """
    if not _token_ok():
        return "Forbidden", 403
    try:
        content = open(QUEUE_FILE, encoding="utf-8").read()
        os.unlink(QUEUE_FILE)
    except FileNotFoundError:
        return "No queue", 204
    except Exception:
        return "Error", 500
    try:
        with open(TRIGGER_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        return "Error", 500
    # The actions are on their way to Claude — drop the sent log and the
    # "fires on DM Enter" indicator so displays don't show them as pending.
    with _sent_lock:
        _sent.clear()
    with _queue_status_lock:
        _queue_status.clear()
    _broadcast({"sent_log": {}, "queue_status": []})
    return "", 204


@app.route("/player-input/drain", methods=["POST"])
def drain_player_input():
    """Claim .input_queue and return its actions. Called by check_input.py.

    This is the same file wrapper.py and the other consumers claim, taken with
    the same queue_claim rename, so an action is returned here or injected
    there, never both and never neither. Returns the entries as JSON
    ([{"character", "text", "hold"}], the shape this route always had), then
    clears the pending and QUEUED indicators on every display. A read that
    fails after the claim restores the file and answers 503, so the caller
    delivers nothing and the next drain retries.
    """
    if not _token_ok():
        return "Forbidden", 403

    raw, delivered = queue_claim.claim_and_read(QUEUE_FILE)
    if not delivered:
        return "Queue read failed; it was restored", 503
    drained = []
    for line in raw.splitlines():
        match = re.match(r"^\[([^\]]+)\]:\s*(.*)", line.strip())
        if match and match.group(2):
            drained.append({"character": match.group(1), "text": match.group(2),
                            "hold": False})

    with _input_lock:
        _input_queue.clear()
    _broadcast({"pending_input": []})
    _reconcile_queue_state()
    return jsonify(drained), 200


# ─── Tactical grid combat ────────────────────────────────────────────────────
#
# The engine (scripts/tactics/) owns every rule. This app only relays:
#   POST /combat        the engine's snapshot after each command -> SSE {"combat": ...}
#   GET  /combat/state  the current snapshot, for a page opened mid-fight
#   POST /combat/do     a player's click, run through the same CLI the GM uses;
#                       the result is appended to .input_queue so the GM sees it
#                       and narrates it, whichever front-end the GM runs

TACTICS_CLI = os.path.join(SCRIPTS_DIR, "tactics", "combat.py")
_combat_lock = threading.Lock()
_combat_run_lock = threading.Lock()      # one engine command at a time
_current_combat: Optional[dict] = None

# Commands a browser may run. Mutating ones act only for player-controlled tokens.
_COMBAT_READ = {"reachable", "preview", "targets", "status", "spells", "preview-area", "sight"}
_COMBAT_WRITE = {"move", "attack", "dash", "disengage", "dodge", "stand",
                 "undo-move", "end-turn", "death-save",
                 "cast", "help", "hide", "escape", "ready", "reactions"}
_COMBAT_MAX_ARGS = 8          # a spell name plus Magic Missile's three darts, with room
_COMBAT_MAX_REACT = 4         # reaction answers in one re-run (--react, in the order asked)
_COMBAT_ARG_FLAGS = {"cast": ("--level",), "preview-area": ("--level",),
                     "ready": ("--level", "--target", "--trigger")}

# What a refusal says when the display has no campaign to talk to. The engine's
# "No active campaign." named a state a player cannot act on and could not be
# told apart from any other refusal; this one says what to do about it.
NO_CAMPAIGN = ("No fight is open for this display. Start one in the terminal "
               "(combat start), then reload the display.")


@app.route("/combat", methods=["POST"])
def combat_push():
    if not _token_ok():
        return "Forbidden", 403
    global _current_combat
    snap = (request.get_json(silent=True) or {}).get("combat")
    if not isinstance(snap, dict):
        return "Bad Request", 400
    with _combat_lock:
        _current_combat = snap if snap.get("status") == "active" else None
    _broadcast({"combat": snap})
    return "", 204


def _run_tactics(args: list, extra: list = ()) -> tuple:
    """Run the tactics CLI for the active campaign. (exit code, stdout).

    With no campaign set, says so in the reader's terms. "No active campaign."
    is the engine's phrasing and the display could not tell it apart from any
    other refusal, so it landed in the combat log as a line that meant nothing
    to a player and stayed there after the toast had gone.
    """
    camp = _active_campaign_name()
    if not camp:
        return 1, NO_CAMPAIGN
    try:
        _campaign_dir_for_name(camp)
    except (TypeError, ValueError, OSError):
        return 1, "Invalid campaign name."
    cmd = [sys.executable, TACTICS_CLI, "-c", camp, *args, *extra]
    with _combat_run_lock:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, f"Combat engine failed: {e.__class__.__name__}"
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def _combat_snapshot() -> dict:
    """The live fight's snapshot, asking the engine when none has been pushed.

    The engine pushes a snapshot to POST /combat after every command, so a
    display started (or restarted) after the fight began holds none. The map
    still drew, because GET /combat/state asked the engine, but /combat/do
    read the empty cache and refused every click with 409 "no player's turn
    open" while the banner said "Your turn". Every reader goes through here,
    and what the engine reports is cached, as a push would have done.
    """
    global _current_combat
    with _combat_lock:
        snap = _current_combat
    if snap is None:
        code, out = _run_tactics(["status"], ["--json"])
        if code == 0:
            try:
                fetched = json.loads(out).get("combat")
            except (ValueError, AttributeError):
                fetched = None
            if isinstance(fetched, dict) and fetched.get("status") == "active":
                with _combat_lock:
                    if _current_combat is None:
                        _current_combat = fetched
                    snap = _current_combat
    return snap or {}


@app.route("/combat/state", methods=["GET"])
def combat_state():
    snap = _combat_snapshot()
    return jsonify(snap if snap.get("status") == "active" else {})


@app.route("/combat/do", methods=["POST"])
def combat_do():
    """Body: {"cmd": "move", "args": ["kairos", "D5"], "rolls": [14], "for_me": false,
    "react": "yes"|"no"|["yes", "no", ...]|null}. Returns {"ok", "text", "result"} or {"pending": text}
    (a roll or decision is needed; nothing changed) or {"error": text}."""
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(force=True, silent=True) or {}
    cmd = str(data.get("cmd", ""))
    raw = data.get("args") or []
    if not isinstance(raw, list):
        return jsonify({"error": "args must be a list"}), 400
    args = [str(a)[:160] if str(a).startswith("--trigger=") else str(a)[:80]
            for a in raw][:_COMBAT_MAX_ARGS]
    if cmd not in _COMBAT_READ | _COMBAT_WRITE:
        return jsonify({"error": f"unknown command {cmd!r}"}), 400
    # Flags in args could smuggle in answers (--roll 20) or another campaign:
    # only the few that shape a spell or a readied action pass.
    allowed = _COMBAT_ARG_FLAGS.get(cmd, ())
    for a in args:
        if a.startswith("-") and a.split("=", 1)[0] not in allowed:
            return jsonify({"error": f"flag {a.split('=', 1)[0]!r} is not allowed here"}), 400
    actor = None
    if cmd in ("spells", "preview-area"):
        # A monster's spell list and what an area would reveal are the GM's.
        snap = _combat_snapshot()
        tokens = {t.get("id"): t for t in snap.get("tokens", [])}
        who = tokens.get(args[0]) if args else None
        if not who or who.get("controller") != "player":
            return jsonify({"error": "Only a player's own spells can be listed."}), 403
    if cmd == "sight":
        # Cover shading from a creature the players can see (the snapshot leaves
        # out hidden and unseen ones), counting only the creatures they can see.
        snap = _combat_snapshot()
        if not args or args[0] not in {t.get("id") for t in snap.get("tokens", [])}:
            return jsonify({"error": "No such creature on the map."}), 403
        args = [args[0], "--players"]
    if cmd in _COMBAT_WRITE:
        if not _rate_ok(request.remote_addr):
            return "Too Many Requests", 429
        if _device_ok(request.headers.get("X-DND-Device", ""), request.remote_addr) != "approved":
            # The panel cannot fix this by retrying, so the reply says what would
            # (a player used to see only this sentence in the log, with nothing
            # anywhere on screen explaining it).
            return jsonify({"error": "This device is not approved to act yet. "
                                     "The GM has to approve it in the terminal "
                                     "(devices approve) before the map buttons "
                                     "do anything."}), 403
        snap = _combat_snapshot()
        tokens = {t.get("id"): t for t in snap.get("tokens", [])}
        actor = tokens.get(snap.get("current"))
        if not actor or actor.get("controller") != "player":
            # Name the creature that IS acting when there is one. "It is not a
            # player's turn." next to a banner reading "Your turn, Kairos" left
            # the reader with two contradictory true sentences and no way to
            # tell which was stale.
            acting = snap.get("current")
            whose = f" It is {tokens[acting]['name']}'s turn." if acting in tokens else ""
            return jsonify({"error": f"There is no player's turn open right now.{whose} "
                                     "Nothing was sent; wait for your turn."}), 409
        if cmd not in ("undo-move", "end-turn") and (not args or args[0] != actor["id"]):
            return jsonify({"error": f"Only {actor['name']} can act now. Nothing was sent."}), 409
    extra = ["--json"]
    rolls = [int(r) for r in (data.get("rolls") or [])[:6]
             if isinstance(r, int) or (isinstance(r, str) and r.isdigit())]
    for r in rolls:
        extra += ["--roll", str(r)]
    if rolls:
        extra.append("--player-roll")
    if data.get("for_me"):
        extra.append("--for-me")
    react = data.get("react")
    for answer in ([react] if isinstance(react, str) else react if isinstance(react, list)
                   else [])[:_COMBAT_MAX_REACT]:
        if answer in ("yes", "no"):
            extra += ["--react", answer]
    code, out = _run_tactics([cmd, *args], extra)
    if code == 2 and out.startswith("usage:"):     # argparse also exits with 2 on bad input
        return jsonify({"error": out.splitlines()[-1]})
    if code == 2:
        return jsonify({"pending": out})
    if code != 0:
        return jsonify({"error": out or "The engine refused."})
    try:
        res = json.loads(out)
    except ValueError:
        return jsonify({"error": "Unreadable engine output."})
    if cmd in _COMBAT_WRITE and cmd != "reactions":   # a setting, not an action to narrate
        # Queue the outcome as the player's action so the GM narrates it.
        # One line: every consumer reads `[Char]: text` per line, and wrapper.py
        # rejects a whole batch over one unprefixed line.
        text = " ".join(re.sub(r"[`\\$]", "", res.get("text", "")).split())[:500]
        queued_text = f"{_GRID_PREFIX}{text}"
        if not _queue_append({actor["name"]: queued_text}, replace=False):
            # The engine already applied the action, so the click succeeded;
            # only the GM's copy failed. Say so where the GM will see it.
            print(f"[display] could not queue grid action for the GM: "
                  f"[{actor['name']}]: {queued_text}", file=sys.stderr, flush=True)
        with _input_lock:
            _input_queue.append({"character": actor["name"], "text": queued_text,
                                 "hold": False, "timestamp": _time.time()})
            current = list(_input_queue)
        _broadcast({"pending_input": current})
    return jsonify({"ok": True, "text": res.get("text", ""), "result": res.get("result", {})})


@app.route("/stream")
def stream():
    q: queue.Queue = queue.Queue(maxsize=256)
    _since_raw = request.args.get("since") or request.headers.get("Last-Event-ID") or ""
    try:
        since = int(_since_raw) if _since_raw.strip() else None
    except ValueError:
        since = None
    missed = None
    with _clients_lock:
        _clients.append(q)
        if since is not None and request.args.get("epoch", _EPOCH) == _EPOCH:
            missed = _replay_since(since)
        # First payload tells the client which server run and seq it is joining.
        q.put_nowait({"hello": {"epoch": _EPOCH, "seq": _seq, "resumed": missed is not None}})
        for _p in (missed or []):
            q.put_nowait(_p)
        # Register this client's bound character (phones pass ?character=/?char=);
        # the main display passes neither. Drives dice-request phone-vs-screen routing.
        _ch = (request.args.get("character") or request.args.get("char") or "").strip().lower()[:48]
        if _ch:
            _client_chars[q] = _ch

    # Send the current scene immediately on connect so the browser
    # starts with the right background even mid-session.
    initial_scene = SCENES[_current_scene_name] | {"name": _current_scene_name}
    q.put_nowait({"scene": initial_scene})

    # Replay recent entries so late-connecting / reconnecting browsers catch up.
    # Sent as a typed batch so the browser can render each item (dm/player/dice) correctly.
    with _text_log_lock:
        recent = list(_text_log) if missed is None else []
    if recent:
        q.put_nowait({"replay_batch": recent})

    # Send current stats so the sidebar is populated immediately on (re)connect.
    with _stats_lock:
        if _current_stats:
            q.put_nowait({"stats": dict(_current_stats)})

    # Replay an active grid combat so the grid appears on (re)connect.
    with _combat_lock:
        if _current_combat:
            q.put_nowait({"combat": dict(_current_combat)})

    # Revealed faction clocks only; hidden clocks never leave the server.
    _clk = _clocks_payload()
    if _clk:
        q.put_nowait({"clocks": _clk})

    # Send current input queue so the pending indicator is accurate on reconnect.
    with _input_lock:
        if _input_queue:
            q.put_nowait({"pending_input": list(_input_queue)})

    # Send the current sent log so the panel reflects live state on reconnect.
    _reconcile_queue_state()
    with _sent_lock:
        if _sent:
            q.put_nowait({"sent_log": _sent_snapshot()})

    # Send current queue status so the 'Queued' indicator survives page reload.
    with _queue_status_lock:
        if _queue_status:
            q.put_nowait({"queue_status": list(_queue_status)})

    # Send current pending dice requests so the "Waiting on…" badge survives reload.
    snap = _dice_pending_snapshot()
    if snap:
        q.put_nowait({"dice_pending": snap})

    # Replay every active dice_request so phones that connected *after* a GM
    # broadcast still pre-fill their pad and store the request_id. Without this,
    # a late-joining or reloaded phone rolls without a request_id, the roll logs
    # but the pending set never drains, and the "Waiting on…" banner gets stuck.
    with _dice_pending_lock:
        active = [(rid, dict(e["meta"]), sorted(e["chars"])) for rid, e in _dice_pending.items() if e["chars"]]
    for rid, meta, chars in active:
        q.put_nowait({"dice_request": {
            "request_id": rid,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",
            "onscreen_targets": [c for c in chars if c.lower() != "any" and not _phone_present(c)],
            "spec": meta.get("spec", "1d20"),
            "modifier": meta.get("modifier", 0),
            "advantage": meta.get("advantage", "normal"),
            "label": meta.get("label", ""),
            "dc": meta.get("dc"),
        }})

    # Replay autorun cycle so reconnecting clients resume the countdown from correct elapsed position.
    with _autorun_cycle_lock:
        if _autorun_cycle:
            q.put_nowait({"autorun_cycle": dict(_autorun_cycle)})

    # Replay threshold so the ready counter reflects the correct target on reconnect.
    if _autorun_threshold is not None:
        q.put_nowait({"autorun_threshold": _autorun_threshold})

    # Send any pending device approval requests so the DM sees them on reconnect.
    with _devices_lock:
        for dev in list(_pending_devices.values()):
            q.put_nowait({"device_request": {"id": dev["id"], "ip": dev["ip"]}})

    def generate():
        try:
            while True:
                try:
                    payload = q.get(timeout=5)
                    if payload is _CLOSE:
                        return
                    _id = payload.get("seq")
                    yield (f"id: {_id}\n" if _id is not None else "") + \
                        f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    # Self-heal: an action that left .input_queue must stop
                    # showing QUEUED. Broadcasts only when something changed.
                    try:
                        _reconcile_queue_state()
                    except Exception:
                        pass
                    try:
                        _push_clocks_if_changed()  # picks up reveal/hide/tick from world.py
                    except Exception:
                        pass
                    yield ": keepalive\n\n"   # prevent proxy timeout
        except GeneratorExit:
            with _clients_lock:
                try:
                    _clients.remove(q)
                except ValueError:
                    pass
                _client_chars.pop(q, None)

    resp = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )
    # Force a single authoritative Connection header — Werkzeug otherwise
    # emits both keep-alive (ours) and close (its default), which confuses
    # transparent proxies (e.g. eero mesh routing) into buffering the stream.
    resp.headers["Connection"] = "keep-alive"
    return resp


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Wire audio SFX broadcast now that _broadcast is defined
    if _audio:
        _audio.set_broadcast(_broadcast)

    # Numeric, never a name. Werkzeug builds an http.server.HTTPServer, and
    # HTTPServer.server_bind() calls socket.getfqdn(host) BETWEEN bind() and
    # listen(). While that lookup is outstanding the socket is bound and not
    # listening, so nothing can connect: a connection times out rather than
    # being refused. On GitHub's macos-26-arm64 image the lookup of "localhost"
    # does not come back, and start-display.sh runs this under nohup with the
    # output in a file, so a GM on a slow or hostile resolver gets the
    # "Flask server starting" banner printed just above this line, a browser
    # that never connects, and no error anywhere.
    #
    # "127.0.0.1" and "0.0.0.0" are already numeric, so they do not trigger
    # the reverse lookup either. This is the one-line form of the fix; see
    # tests/_display_child.py, which avoids the code path altogether for the
    # tests that must be able to kill this process for real.
    host = "0.0.0.0" if _LAN_MODE else "127.0.0.1"
    # TLS — only enabled when --tls is explicitly passed; HTTP is the default.
    _display_dir = os.path.dirname(os.path.abspath(__file__))
    _cert = os.path.join(_display_dir, "cert.pem")
    _key  = os.path.join(_display_dir, "key.pem")
    ssl_ctx = (_cert, _key) if (_TLS_MODE and os.path.exists(_cert) and os.path.exists(_key)) else None
    scheme  = "https" if ssl_ctx else "http"

    # Write .scheme so push_stats.py / send.py / autorun_wait.py know which to use
    try:
        with open(os.path.join(_display_dir, ".scheme"), "w", encoding="utf-8") as _sf:
            _sf.write(scheme)
    except OSError:
        pass

    if _LAN_MODE:
        print(f"GM Display — LAN mode (0.0.0.0:{_PORT}) [{scheme.upper()}]")
        print(f"  Local:  {scheme}://localhost:{_PORT}")
        print("  Token stored at:", TOKEN_FILE)
        print("  POST endpoints require X-DND-Token header (send.py/push_stats.py handle this automatically)")
        print()
    else:
        print(f"GM Display — Flask server starting on {scheme}://localhost:{_PORT}")
        print(f"Open {scheme}://localhost:{_PORT} in your browser, then Chromecast the tab.")
        print()
    app.run(host=host, port=_PORT, threaded=True, debug=False, ssl_context=ssl_ctx)
