"""Milestone 6: the prompt for one DM call, and the state.md digest."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import context        # noqa: E402

STATE = """# Campaign: demo

## Current Situation
- **Location:** Frog Pond
- **Party:** Kairos, Human Wizard 1

## Pinned Facts
*Soft facts the GM always keeps.*
- Kairos promised to find Mira's brother.

## World State
- **Season:** spring

## Live State Flags
**NPC dispositions**:
- Mira: grateful

## Session Flags
roll_mode: players
council: off

## GM Notes (hidden from players)
The frogs serve the hag.
"""


def test_the_digest_keeps_only_the_hot_sections_and_drops_helper_text():
    d = context.state_digest(STATE)
    assert "### Current Situation" in d and "Frog Pond" in d
    assert "Mira's brother" in d and "Mira: grateful" in d
    assert "**NPC dispositions**:" in d
    assert "Soft facts" not in d and "spring" not in d and "hag" not in d


def test_the_digest_respects_its_limit():
    assert len(context.state_digest(STATE, limit=40)) == 40


def test_council_setting():
    assert context.council_setting(STATE) == "off"
    assert context.council_setting("## Session Flags\n- council: auto\n") == "auto"
    assert context.council_setting("") == "auto"


def test_dm_prompt_asks_for_the_json_line_and_no_think(monkeypatch):
    monkeypatch.delenv("GM_NO_THINK", raising=False)
    p = context.dm_prompt()
    assert '{"escalate": null, "command": null}' in p and p.endswith("/no_think")
    monkeypatch.setenv("GM_NO_THINK", "0")
    assert not context.dm_prompt().endswith("/no_think")


def test_messages_are_a_stable_system_and_one_ordered_user_message():
    recent = [{"role": "player", "text": "I look around."}, {"role": "dm", "text": "Reeds."},
              {"role": "engine", "text": "Round 1."}]
    msgs = context.build_messages("SYS", "DIGEST", "Story.", recent, engine="Kairos B7 8/8",
                                  notes="Director: slow down.", player="I cast fire bolt.",
                                  task="Narrate.")
    assert msgs[0] == {"role": "system", "content": "SYS\n\n## Campaign\nDIGEST"}
    user = msgs[1]["content"]
    order = ["## Story so far", "## Recent turns", "Player: I look around.", "GM: Reeds.",
             "Engine: Round 1.", "## Engine (facts", "## Advisor notes", "## Player now",
             "## Your task"]
    positions = [user.index(s) for s in order]
    assert positions == sorted(positions)


def test_trimming_drops_the_oldest_turns_first():
    recent = [{"role": "player", "text": f"turn {i} " + "x" * 50} for i in range(20)]
    msgs = context.build_messages("SYS", "", "", recent, player="now", budget=400)
    user = msgs[1]["content"]
    assert "turn 19" in user and "turn 0 " not in user and "## Player now\nnow" in user
    assert len(msgs[0]["content"]) + len(user) <= 400
