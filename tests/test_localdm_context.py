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
- **Threat arc stage:** 2 - Pressure

## Live State Flags
**NPC dispositions**:
- Mira: grateful

## Faction Moves
- The Ninefold moved against Frog Pond.

## Session Flags
roll_mode: players
council: off

## Campaign Arc
steering_notes: >
  The Ninefold has to come for her in Act 2.

## GM Notes (hidden from players)
The frogs serve the hag.
"""


def test_the_digest_keeps_only_the_hot_sections_and_drops_helper_text():
    d = context.state_digest(STATE)
    assert "### Current Situation" in d and "Frog Pond" in d
    assert "Mira's brother" in d and "Mira: grateful" in d
    assert "**NPC dispositions**:" in d
    assert "Soft facts" not in d and "hag" not in d


def test_the_dm_is_told_what_the_world_did_while_the_party_was_busy():
    """World State and Faction Moves used to be dropped, so the off-screen faction
    clocks were computed and never reached the DM."""
    d = context.state_digest(STATE)
    assert "### World State" in d and "Pressure" in d
    assert "### Faction Moves" in d and "moved against Frog Pond" in d


def test_the_digest_never_hands_the_dm_the_arc_or_the_session_flags():
    """Campaign Arc carries steering_notes and outstanding_beats: handing the DM the
    whole arc is how NPCs end up voicing the mystery early. Session Flags is the
    operator's, not the DM's."""
    d = context.state_digest(STATE)
    assert "Act 2" not in d and "Campaign Arc" not in d
    assert "roll_mode" not in d and "council: off" not in d


def test_an_unfilled_section_costs_the_prompt_nothing():
    """A fresh campaign is a blank template. Without this the DM was briefed that the
    in-world date was "<Day, Month, Year - canonical source; keep in sync above>"."""
    template = (ROOT / "templates" / "state.md").read_text(encoding="utf-8")
    d = context.state_digest(template)
    assert "<" not in d
    assert "canonical source" not in d


def test_a_filled_line_still_survives_the_template_test():
    d = context.state_digest("## World State\n- **Season:** winter\n")
    assert "winter" in d
    assert context.state_digest("## World State\n- **Season:**\n") == ""


def test_the_digest_respects_its_limit():
    assert len(context.state_digest(STATE, limit=40)) <= 40


def test_a_truncated_digest_says_it_was_truncated():
    """A bare [:limit] cut mid-line and said nothing, so a state.md that outgrew the
    budget lost its tail invisibly."""
    big = "## Open Threads & Rumours\n" + "\n".join(f"- rumour {i}" for i in range(400))
    d = context.state_digest(big, limit=600)
    assert "[truncated" in d
    assert len(d) <= 600


def test_a_digest_that_fits_is_not_marked():
    assert "[truncated" not in context.state_digest(STATE)


def test_permanent_losses_reach_the_digest_generically():
    text = "## Permanent Losses\n- Mira was collected at the horn.\n- The west gate was forfeited.\n"
    digest = context.state_digest(text)
    assert "### Permanent Losses" in digest
    assert "Mira was collected at the horn" in digest
    assert "The west gate was forfeited" in digest


def test_campaign_without_permanent_losses_has_no_digest_change():
    assert "Permanent Losses" not in context.state_digest(STATE)


def test_the_faction_log_the_engine_writes_reaches_the_dm(tmp_path):
    """world.py writes faction moves to faction_log.md; it used to be in neither
    NOTE_FILES nor the digest, so the clocks were computed and thrown away."""
    (tmp_path / "faction_log.md").write_text(
        "## 2026-09-29 12:00:00 (GM-only)\n"
        "The Ninefold moved against Frog Pond: 2/6 -> 3/6 (rain).\n",
        encoding="utf-8")
    d = context.notes_digest(tmp_path)
    assert "Ninefold moved against Frog Pond" in d


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
