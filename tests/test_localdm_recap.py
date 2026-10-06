"""Session recap on resume and the player-facing prep checklist (recap.py)."""
from __future__ import annotations

import json
import os
import time

from tests.localdm_fakes import FakeBridge, FakeClient
from localdm import llm, recap
from localdm.play import Session

STATE = """# Campaign: demo
## Recent Events
- Kairos met Maribeth at the gate.
- GM only: the moonstone is cursed.
- Kairos bought a lantern.
## Open Threads & Rumours
- The missing ledger
- A secret passage under the library
## Active Quests
- Find Hesper
## Live State Flags
- marching_order: Kairos leads, then Hesper
## GM Notes
- the villain is the dean
"""
SHEET = """# Kairos
## Equipment & Inventory
**Adventuring Gear:**
- Spellbook, 3 rations, potion of healing
## Spell Slots (if applicable)
*Prepared spells: INT mod + wizard level = 4.*
## Backstory
x
"""


def camp(tmp_path, played=True, age_h=10):
    d = tmp_path / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "localdm").mkdir()
    (d / "state.md").write_text(STATE, encoding="utf-8")
    (d / "characters" / "kairos.md").write_text(SHEET, encoding="utf-8")
    (d / "localdm" / "summary.md").write_text("The party reached the academy.\n", encoding="utf-8")
    (d / "localdm" / "canon.jsonl").write_text(
        json.dumps({"kind": "dialogue", "speaker": "Maribeth", "text": "Keep it quiet.", "turn": 1})
        + "\n" + json.dumps({"kind": "reveal", "key": "k", "text": "A secret vault exists.", "turn": 2})
        + "\n", encoding="utf-8")
    if played:
        t = d / "localdm" / "transcript.jsonl"
        t.write_text('{"role": "dm", "text": "hi"}\n', encoding="utf-8")
        old = time.time() - age_h * 3600
        os.utime(t, (old, old))
    return d


def test_recap_uses_stored_state_and_drops_private_lines(tmp_path):
    text = recap.build_recap(camp(tmp_path))
    assert "reached the academy" in text
    assert "met Maribeth" in text and "lantern" in text
    assert "Maribeth: Keep it quiet." in text
    assert "moonstone" not in text and "secret vault" not in text
    assert "villain" not in text


def test_recap_empty_when_nothing_stored(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    assert recap.build_recap(d) == ""


def test_gap_gates_the_recap_but_not_the_prep(tmp_path):
    d = camp(tmp_path, age_h=1)
    blocks = recap.session_start(d, min_gap=6)
    assert len(blocks) == 1 and blocks[0].startswith("Before you play")
    d2 = camp(tmp_path / "b", age_h=10)
    blocks = recap.session_start(d2, min_gap=6)
    assert blocks[0].startswith("Previously") and blocks[1].startswith("Before you play")


def test_flags_skip_blocks_and_new_campaign_shows_nothing(tmp_path):
    d = camp(tmp_path)
    assert recap.session_start(d, recap=False, prep=False) == []
    assert recap.session_start(camp(tmp_path / "n", played=False)) == []


def test_gap_setting_env(monkeypatch):
    assert recap.gap_setting({"GM_RECAP_GAP_HOURS": "2"}) == 2.0
    assert recap.gap_setting({"GM_RECAP_GAP_HOURS": "abc"}) == recap.DEFAULT_GAP_HOURS
    assert recap.gap_setting({}) == recap.DEFAULT_GAP_HOURS


def test_prep_is_driven_by_sheet_and_state(tmp_path):
    text = recap.build_prep(camp(tmp_path))
    assert "spells prepared" in text and "kairos" in text
    assert "3 rations" in text
    assert "Kairos leads, then Hesper" in text
    assert "The missing ledger" in text and "Find Hesper" in text
    assert "secret passage" not in text


def test_prep_without_order_prompts_for_one(tmp_path):
    d = camp(tmp_path)
    (d / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    text = recap.build_prep(d)
    assert "not set" in text and "none recorded" in text


def test_session_commands(tmp_path):
    d = camp(tmp_path)
    c = FakeClient(lambda m, msgs, role: "x")
    s = Session("demo", c, llm.Models("a", "b", "c"), camp_dir=d, bridge=FakeBridge())
    assert "Previously on" in s.handle("/recap")[0]
    assert s.handle("/prep")[0].startswith("Before you play")
    assert c.calls == []


def test_recap_filters_credential_shaped_strings(tmp_path):
    """Credential-shaped strings (API_KEY=..., Bearer ...) are filtered from recap."""
    d = tmp_path / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "localdm").mkdir()
    # Summary with credential-shaped strings
    (d / "localdm" / "summary.md").write_text(
        "The party reached the academy.\n"
        "OMNIROUTE_API_KEY=sk-12345\n"
        "Bearer abcdef12345\n"
        "ACCESS_TOKEN=xyz789\n", encoding="utf-8")
    (d / "state.md").write_text(
        "# Campaign: demo\n## Recent Events\n- Found a key.\n", encoding="utf-8")
    (d / "characters" / "kairos.md").write_text("# Kairos\n", encoding="utf-8")
    (d / "localdm" / "transcript.jsonl").write_text('{"role": "dm", "text": "hi"}\n', encoding="utf-8")
    
    text = recap.build_recap(d)
    assert "reached the academy" in text
    assert "Found a key" in text
    assert "OMNIROUTE_API_KEY" not in text
    assert "sk-12345" not in text
    assert "Bearer" not in text
    assert "abcdef12345" not in text
    assert "ACCESS_TOKEN" not in text
    assert "xyz789" not in text
