#!/usr/bin/env python3
"""Append permanent loss records to a campaign's state.md.

    python3 scripts/tolls.py -c <campaign> add --loss "Mira was collected at the horn"

Records are generic campaign facts. Existing entries are never edited or removed.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from paths import find_campaign
from safeio import atomic_write_text

HEADING = "## Permanent Losses"
ANCHOR = "## Recent Events"
_EMPTY = "*(none recorded)*"


def append_toll(state_text: str, loss: str) -> str:
    """Return state text with one nonempty, single-line loss appended."""
    loss = loss.strip()
    if not loss or "\n" in loss or "\r" in loss:
        raise ValueError("loss must be nonempty and fit on one line")
    match = re.search(r"^## Permanent Losses[ \t]*$", state_text, re.M)
    if match:
        end = re.search(r"^## ", state_text[match.end():], re.M)
        section_end = match.end() + end.start() if end else len(state_text)
        body = state_text[match.end():section_end]
        body = re.sub(rf"(?m)^{re.escape(_EMPTY)}[ \t]*\n?", "", body, count=1)
        separator = "" if not body or body.endswith("\n") else "\n"
        return state_text[:match.end()] + body + separator + f"- {loss}\n" + state_text[section_end:]

    block = f"{HEADING}\n- {loss}\n\n"
    anchor = re.search(rf"^{re.escape(ANCHOR)}[ \t]*$", state_text, re.M)
    if anchor:
        return state_text[:anchor.start()] + block + state_text[anchor.start():]
    return state_text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"


def add_toll(campaign: str, loss: str) -> None:
    path = find_campaign(campaign) / "state.md"
    if not path.is_file():
        raise SystemExit(f"error: no state.md for campaign '{campaign}'")
    text = path.read_text(encoding="utf-8")
    try:
        updated = append_toll(text, loss)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    atomic_write_text(path, updated)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Append permanent loss to campaign state (GM-only).")
    parser.add_argument("-c", "--campaign", required=True, help="Campaign name")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="append a permanent loss record")
    add.add_argument("--loss", required=True, help="one-line loss or permanent cost")
    args = parser.parse_args(argv)
    if args.command == "add":
        add_toll(args.campaign, args.loss)
        print("permanent loss recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
