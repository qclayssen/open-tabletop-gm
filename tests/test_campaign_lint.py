"""campaign_lint.py: what the GM is not being told, reported instead of dropped."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LINT = ROOT / "scripts" / "campaign_lint.py"
sys.path.insert(0, str(ROOT / "scripts"))

import campaign_lint

GOOD_STATE = """# Campaign: demo
**Created:** 2026-09-01  **Last session:** 2026-09-28  **Session count:** 3  \
**System Module:** dnd5e  **System Version:** 2014

## Current Situation
- **Location:** Frog Pond
- **Party:** Kairos - Human Wizard 3 | HP 14/14 | AC 13

## Pinned Facts
- Kairos promised to find Mira's brother.

## Active Quests
- The Sunken Bell.

## Open Threads & Rumours
- Who paid for the bridge?

## Live State Flags
**NPC dispositions**:
- Mira: grateful

## Session Flags
roll_mode: players
council: auto

## GM Style Notes
*Distilled calibration.*
- Short paragraphs.

## World State
- **Season:** spring

## Active Combat
*(none)*
"""

GOOD_ARC = ("```yaml\ntype: dynamic\nacts:\n  - act: 1\n    title: Setup\n"
            "    beats:\n      - id: 1a\n        status: current\n"
            "current_act: 1\ncurrent_beat: 1a\noutstanding_beats: [1a]\n```\n")

GOOD_NPCS = """# NPCs - demo

| Name | Role | Faction | Location | Attitude | Notes |
|------|------|---------|----------|----------|-------|
| Maribeth | fence | none | village | friendly | owes a debt |

---

### Maribeth
- **Role:** fence | **CR/Level:** 3 | **Location:** village
- **Demeanor:** blunt | **Motivation:** money | **Secret:** none
- **Attitude toward party:** friendly
- **Current goal:** the bridge debt
"""


def campaign(tmp_path, *, state=GOOD_STATE, npcs=GOOD_NPCS, world=None, sheets=True,
             arc=True):
    camp = tmp_path / "campaigns" / "demo"
    camp.mkdir(parents=True, exist_ok=True)
    if arc:
        state = state.replace("## World State", GOOD_ARC + "\n## World State")
    (camp / "state.md").write_text(state, encoding="utf-8")
    (camp / "npcs.md").write_text(npcs, encoding="utf-8")
    (camp / "world.md").write_text(world if world is not None else
                                   "# World: demo\n\n## Campaign Tone & Genre\n- **Tone:** grim\n\n"
                                   "## World Foundations\n- **Biome:** moor\n\n"
                                   "## Factions\n### The Bell Company\n- **Goals:** the bell\n\n"
                                   "## Quest Seed Bank\n- **Seed 1 - Drowned:** a bell under the fen\n",
                                   encoding="utf-8")
    if sheets:
        (camp / "characters").mkdir(exist_ok=True)
        (camp / "characters" / "Kairos.md").write_text(
            "# Kairos\n**Player:** quentin  **Campaign:** demo  **Last Updated:** 2026-09-28\n\n"
            "## Identity\n- **Race:** Human | **Class:** Wizard | **Level:** 3\n\n"
            "## Combat Stats\n- **HP:** 14 / 14 | **Temp HP:** 0\n- **AC:** 13 | **Initiative:** +4\n",
            encoding="utf-8")
    return camp


def findings(rep, level=None):
    return [f for f in rep.findings if level is None or f["level"] == level]


def messages(rep):
    return " | ".join(f["message"] for f in rep.findings)


# ── the clean case must actually be clean ────────────────────────────────────

def test_a_finished_campaign_produces_no_errors(tmp_path):
    campaign(tmp_path)
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert rep.errors == 0, messages(rep)
    assert "Current Situation" not in messages(rep)


# ── a renamed heading is a lost section, not a style nit ─────────────────────

def test_a_renamed_section_is_an_error_and_says_what_reads_it(tmp_path):
    campaign(tmp_path, state=GOOD_STATE.replace("## Active Quests", "## Quests"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    errs = [f for f in findings(rep, "error") if "Active Quests" in f["message"]]
    assert errs, messages(rep)
    # The GM is told who reads it, so the fix is not a guess.
    assert "DM prompt" in errs[0]["hint"]


def test_every_section_the_code_reads_is_required(tmp_path):
    """The required list must cover what context.state_digest, dm_help and the
    tactics sync actually grep for. A section that drops out of the linter's list
    is a section whose loss nothing reports."""
    campaign(tmp_path, state="# Campaign: demo\n**Created:** 2026-09-01\n")
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    reported = messages(rep)
    from localdm import context
    for section in context.DIGEST_SECTIONS:
        assert section in reported, section
    for section in ("World State", "Session Flags", "Active Combat"):
        assert section in reported, section


# ── the header line is machine-parsed ────────────────────────────────────────

def test_an_unlabelled_or_non_numeric_header_field_is_an_error(tmp_path):
    # Unlabelled: the field lost its "**...:**" wrapper, so nothing can read it.
    campaign(tmp_path, state=GOOD_STATE.replace("**Session count:** 3", "Session 3"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert any("Session count" in f["message"] and f["level"] == "error"
               for f in rep.findings), messages(rep)
    # Present but not an integer: /gm save increments it.
    campaign(tmp_path, state=GOOD_STATE.replace("**Session count:** 3", "**Session count:** three"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert any("not a number" in f["message"] and f["level"] == "error"
               for f in rep.findings), messages(rep)


def test_a_missing_campaign_exits_two(tmp_path):
    code, out = run(tmp_path, "nope")
    assert code == 2 and "not found" in out


# ── placeholders: the digest drops them, the linter names them ───────────────

def test_an_unfilled_world_line_is_reported_with_its_reason_and_line(tmp_path):
    campaign(tmp_path, world=("# World: demo\n\n## Campaign Tone & Genre\n"
                              "- **Tone:** <grimdark / heroic / horror>\n\n"
                              "## World Foundations\n- **Biome:** moor\n\n"
                              "## Factions\n### The Bell Company\n- **Goals:** the bell\n\n"
                              "## Quest Seed Bank\n- **Seed 1:** <Hook. Reward.>\n"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    warns = findings(rep, "warn")
    assert any("unfilled <placeholder>" in f["message"] for f in warns), messages(rep)
    assert [f["line"] for f in warns] == [4, 14], [(f["line"], f["message"]) for f in warns]
    assert "Tone" in warns[0]["message"] and warns[0]["hint"]


def test_the_linter_and_the_digest_agree_on_what_is_unfilled(tmp_path):
    """The whole point of sharing is_template_line: a line the linter calls
    filled must reach the DM, and one it calls template must not."""
    world = ("# World: demo\n\n## Campaign Tone & Genre\n- **Tone:** grimdark\n"
             "- **Magic level:** <none / low / high>\n\n## World Foundations\n"
             "- **Biome:** moor\n\n## Factions\n### The Bell Company\n"
             "- **Goals:** the drowned bell\n\n## Quest Seed Bank\n"
             "- **Seed 1 - Drowned:** a bell rings under the fen at low water\n")
    camp = campaign(tmp_path, world=world)
    rep = campaign_lint.lint_campaign("demo", camp)
    assert any("Magic level" in f["message"] for f in rep.findings), messages(rep)
    from localdm import context
    digest = context.notes_digest(camp, files=("world.md",))
    assert "grimdark" in digest and "moor" in digest
    assert "Magic level" not in digest          # dropped, exactly as the linter said
    assert "drowned bell" in digest             # real content survives


def test_helper_text_is_not_reported_as_a_gap(tmp_path):
    """Italic lines are instructions to the GM, meant to stay in the file."""
    world = ("# World: demo\n\n## Campaign Tone & Genre\n- **Tone:** grimdark\n"
             "*Pick one tone and stay in it.*\n\n## World Foundations\n- **Biome:** moor\n"
             "\n## Factions\n### The Bell Company\n- **Goals:** the bell\n\n"
             "## Quest Seed Bank\n- **Seed 1 - Drowned:** a bell under the fen\n")
    camp = campaign(tmp_path, world=world)
    rep = campaign_lint.lint_campaign("demo", camp)
    assert not any("helper text" in f["message"] for f in rep.findings), messages(rep)


def test_a_shipped_template_is_loud(tmp_path):
    """The real templates are almost all placeholders. A fresh /gm new campaign
    should read as unfinished, which is the whole point."""
    camp = tmp_path / "campaigns" / "fresh"
    camp.mkdir(parents=True)
    for name in ("state.md", "world.md", "npcs.md"):
        (camp / name).write_text((ROOT / "templates" / name).read_text(encoding="utf-8"),
                                 encoding="utf-8")
    rep = campaign_lint.lint_campaign("fresh", camp)
    assert rep.warnings > 20, rep.warnings


# ── the arc block ────────────────────────────────────────────────────────────

def test_the_arc_yaml_is_parsed_not_eyeballed(tmp_path):
    pytest_skip()
    campaign(tmp_path)
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert not any("arc" in f["message"].lower() for f in rep.findings), messages(rep)


def test_a_current_beat_that_is_not_outstanding_is_flagged(tmp_path):
    arc = ("```yaml\ntype: dynamic\ncurrent_beat: 2a\noutstanding_beats: [1a, 1b]\n```\n")
    campaign(tmp_path, arc=False, state=GOOD_STATE.replace("## World State",
                                                            arc + "\n## World State"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert any("outstanding_beats" in f["message"] for f in rep.findings), messages(rep)


def test_an_arc_block_that_is_not_a_mapping_is_an_error(tmp_path):
    arc = "```yaml\n- just\n- a list\n```\n"
    campaign(tmp_path, arc=False, state=GOOD_STATE.replace("## World State",
                                                            arc + "\n## World State"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert any("not a mapping" in f["message"] for f in rep.findings), messages(rep)


def test_an_arc_with_an_unknown_type_is_flagged(tmp_path):
    arc = "```yaml\ntype: improvisational\ncurrent_beat: 1a\n```\n"
    campaign(tmp_path, arc=False, state=GOOD_STATE.replace("## World State",
                                                            arc + "\n## World State"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert any("arc type" in f["message"] for f in rep.findings), messages(rep)


def test_an_arc_that_does_not_parse_is_reported_not_raised(tmp_path):
    """Nothing else reads this block, so a syntax error would otherwise sit
    unnoticed until the GM tried to use it mid-session."""
    pytest_skip()
    arc = "```yaml\ntype:dynamic\ncurrent_beat: 1a\n```\n"       # no space: invalid YAML
    campaign(tmp_path, arc=False, state=GOOD_STATE.replace("## World State",
                                                            arc + "\n## World State"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert any("does not parse" in f["message"] for f in rep.findings), messages(rep)


def test_the_arc_placeholders_inside_the_fence_are_not_reported_as_gaps(tmp_path):
    """The shipped arc template is full of \"<one sentence - ...>\" by design.
    Reporting those would bury the real gaps in noise."""
    pytest_skip()
    arc = ("```yaml\ntype: dynamic\ntheme: \"<one sentence>\"\n"
           "current_beat: 1a\noutstanding_beats: [1a]\n```\n")
    campaign(tmp_path, arc=False, state=GOOD_STATE.replace("## World State",
                                                            arc + "\n## World State"))
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert not any(f["line"] and "one sentence" in f["message"] for f in rep.findings), \
        messages(rep)


# ── npcs.md drift ────────────────────────────────────────────────────────────

def test_an_index_row_with_no_entry_is_reported(tmp_path):
    npcs = GOOD_NPCS.replace("| Maribeth | fence | none | village | friendly | owes a debt |",
                             "| Maribeth | fence | none | village | friendly | owes a debt |\n"
                             "| Old Tam | fence | none | village | neutral | owes a debt |")
    campaign(tmp_path, npcs=npcs)
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert any("Old Tam" in f["message"] for f in rep.findings), messages(rep)


def test_the_personality_and_relationships_subheadings_are_not_npcs(tmp_path):
    """They are ### headings too. Counting them as NPCs would report every
    campaign as having three extra unnamed characters."""
    campaign(tmp_path)
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert not any("has no '###" in f["message"] for f in rep.findings), messages(rep)


# ── sheets ───────────────────────────────────────────────────────────────────

def test_a_sheet_with_hp_over_max_is_an_error(tmp_path):
    camp = campaign(tmp_path)
    sheet = camp / "characters" / "Kairos.md"
    sheet.write_text(sheet.read_text(encoding="utf-8").replace("**HP:** 14 / 14", "**HP:** 20 / 14"),
                     encoding="utf-8")
    rep = campaign_lint.lint_campaign("demo", camp)
    assert any("exceeds max" in f["message"] for f in rep.findings), messages(rep)


def test_a_missing_identity_section_is_an_error(tmp_path):
    camp = campaign(tmp_path)
    sheet = camp / "characters" / "Kairos.md"
    sheet.write_text(sheet.read_text(encoding="utf-8").replace("## Identity", "## Who"),
                     encoding="utf-8")
    rep = campaign_lint.lint_campaign("demo", camp)
    assert any("Identity" in f["message"] and f["level"] == "error"
               for f in rep.findings), messages(rep)


def test_a_campaign_with_no_sheet_is_a_warning_not_an_error(tmp_path):
    campaign(tmp_path, sheets=False)
    rep = campaign_lint.lint_campaign("demo", tmp_path / "campaigns" / "demo")
    assert rep.errors == 0
    assert any("no character sheet" in f["message"] for f in rep.findings)


# ── the CLI ──────────────────────────────────────────────────────────────────

def run(root, *args):
    env = dict(os.environ, GM_CAMPAIGN_ROOT=str(root), PYTHONUTF8="1")
    p = subprocess.run([sys.executable, str(LINT), *args], capture_output=True, text=True,
                       encoding="utf-8", env=env)
    assert "Traceback" not in p.stderr, p.stderr
    return p.returncode, p.stdout


def test_the_cli_is_clean_quiet_and_zero(tmp_path):
    campaign(tmp_path)
    code, out = run(tmp_path, "demo")
    assert code == 0 and "clean" in out, out


def test_the_cli_json_is_machine_readable(tmp_path):
    campaign(tmp_path, state=GOOD_STATE.replace("## Active Quests", "## Quests"))
    code, out = run(tmp_path, "demo", "--json")
    assert code == 1
    data = json.loads(out)
    assert data[0]["campaign"] == "demo" and data[0]["ok"] is False
    assert any("Active Quests" in f["message"] for f in data[0]["findings"])


def test_strict_turns_a_warning_into_a_failure(tmp_path):
    camp = campaign(tmp_path)
    (camp / "world.md").write_text("# World: demo\n\n## Campaign Tone & Genre\n"
                                   "- **Tone:** <grimdark / heroic>\n", encoding="utf-8")
    assert run(tmp_path, "demo")[0] == 0
    assert run(tmp_path, "demo", "--strict")[0] == 1


def test_all_walks_every_campaign(tmp_path):
    campaign(tmp_path)
    other = tmp_path / "campaigns" / "other"
    other.mkdir(parents=True)
    (other / "state.md").write_text(GOOD_STATE, encoding="utf-8")
    code, out = run(tmp_path, "--all")
    assert code == 1 and "demo" in out and "other" in out


def test_naming_no_campaign_and_no_all_is_a_usage_error(tmp_path):
    code, _out = run(tmp_path)
    assert code != 0


def pytest_skip():
    """The arc checks need PyYAML; skip the whole module's arc tests without it."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("PyYAML not installed", allow_module_level=False)
