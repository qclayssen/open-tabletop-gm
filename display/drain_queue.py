#!/usr/bin/env python3
"""drain_queue.py — read sent player input out of .input_queue and hand it to the DM.

Why this exists
---------------
`/player-input/send` on the display appends the action to `.input_queue` as
soon as the player taps Send. In the normal workflow, `wrapper.py` picks that
file up and injects it into the PTY running an interactive Claude session.

When the DM is a *background subagent* there is no PTY and no wrapper process,
so `.input_queue` silently fills up and the player sees "Sent" do nothing.
This script is the consumer for that case: the orchestrating agent runs it,
reads the actions off stdout, and forwards them to the subagent in its next
message.

The file is claimed with an atomic rename before it is read, mirroring
`check_input.py`. A plain read-then-unlink is NOT safe here: the Flask app
writes `.input_queue` concurrently via write-tmp + `os.replace`, so a player
pressing Send between our `read()` and our `unlink()` would have that action
deleted without ever being read. Claiming with `os.replace` first means we
either get a complete file or none, and any action written after our claim
lands in a fresh file we will pick up on the next poll.

This makes us a *second* consumer alongside `check_input.py`. Both use the
same claim-then-read primitive, so exactly one of them wins each action.

Usage
-----
    python3 display/drain_queue.py            # print actions as a DM-ready block
    python3 display/drain_queue.py --peek     # print without consuming
    python3 display/drain_queue.py --json     # machine-readable

Exit codes
----------
    0  actions drained (or empty queue)
    1  usage error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

DISPLAY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPLAY_DIR)
import queue_claim  # (needs the sys.path line above it)

QUEUE_FILE = os.path.join(DISPLAY_DIR, ".input_queue")
TRIGGER_FILE = os.path.join(DISPLAY_DIR, ".input_trigger")


def _read_queue() -> list[dict[str, Any]]:
    """Read .input_queue defensively. Returns [] on any problem."""
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError as e:
        print(f"[drain] cannot read queue: {e}", file=sys.stderr)
        return []
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return _parse_lines(raw)


def _parse_lines(raw: str) -> list[dict[str, Any]]:
    """Parse the Flask app's plain-text queue shape: one "[<name>]: <text>" per line."""
    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        char, sep, text = line.partition(":")
        if sep and char.startswith("[") and char.endswith("]"):
            entries.append({"character": char[1:-1].strip(), "text": text.strip()})
        else:
            entries.append({"character": "Player", "text": line})
    return entries


def _render(entries: list[dict[str, Any]]) -> str:
    """Format drained actions as a block the DM can act on directly."""
    if not entries:
        return "No player input was sent."

    lines = ["## Player input from the display (party panel)", ""]
    for e in entries:
        char = e.get("character") or "Player"
        text = (e.get("text") or "").strip()
        lines.append(f"**{char}** says, in character: {text}")
    lines += [
        "",
        "Treat these as the player's in-character actions. Narrate the outcome, "
        "roll any dice with scripts/dice.py (never invent results), push the "
        "narration to the display, then hand control back.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--peek", action="store_true", help="print without consuming the queue")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a DM-ready block")
    args = ap.parse_args()

    entries = _read_queue()

    if not args.peek:
        # Claim-then-read: atomically take ownership of the current file BEFORE
        # reading it, so a concurrent write from the Flask app lands in a new
        # file rather than being deleted unread. The primitive is shared with
        # check_input.py, wrapper.py and autorun_wait.py so there is one
        # implementation to keep correct.
        #
        # NOTE: .input_trigger is deliberately NOT touched. It is not a drain
        # artefact -- it is the signal wrapper.py polls to know it may inject.
        # Deleting it here would swallow a legitimate promote-to-now signal.
        text, delivered = queue_claim.claim_and_read(QUEUE_FILE)
        if delivered:
            if text.strip():
                try:
                    data = json.loads(text)
                    entries = data if isinstance(data, list) else _parse_lines(text)
                except json.JSONDecodeError:
                    entries = _parse_lines(text)
        else:
            # The claim failed after we took ownership, so the helper put the
            # queue back. `_read_queue` above already read those same actions
            # without claiming them, so they must be dropped here: delivering them
            # and restoring them would hand the same action to the GM twice. The
            # next poll picks them up.
            entries = []
            print("[drain] claim failed after taking ownership; restored the "
                  "queue, deferring to the next poll", file=sys.stderr)

    if args.json:
        print(json.dumps({"entries": entries, "count": len(entries)}, ensure_ascii=False))
    else:
        print(_render(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
