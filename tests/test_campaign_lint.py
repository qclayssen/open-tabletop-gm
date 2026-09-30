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

## Faction Moves
- The Ninefold moved against Frog Pond.

## Recent Events
- Session 1: Kairos found the sunken bell.

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
    for section in ("World State", "Session Flags"):
        assert section in reported, section


def test_world_state_names_both_the_readers_it_has(tmp_path):
    """World State is in DIGEST_SECTIONS and is also read by the display sidebar.
    The digest rationale used to be overwritten by the sidebar one, so the reason a
    DM sees never mentioned the DM."""
    why = campaign_lint.REQUIRED_STATE_SECTIONS["World State"]
    assert "state_digest" in why and "dm_help" in why


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


# --- review fixes: ASCII output, read-only lookup, CLI guards, skipped yaml ---

def _run(*args, env_extra=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(LINT), *args], capture_output=True,
                          env=env, cwd=str(ROOT))


def test_output_is_ascii_so_cp1252_consoles_do_not_crash(tmp_path):
    camp = tmp_path / "campaigns" / "demo"
    camp.mkdir(parents=True)
    (camp / "state.md").write_text("# x\n", encoding="utf-8")
    r = _run("demo", env_extra={"GM_CAMPAIGN_ROOT": str(tmp_path)})
    assert r.returncode == 1, r.stderr
    assert b"Traceback" not in r.stderr
    r.stdout.decode("ascii")


def test_lint_does_not_migrate_a_legacy_campaign(tmp_path, monkeypatch):
    import paths
    home = tmp_path / "home"
    # A state.md, or it is not a campaign: find_campaign validates the legacy
    # folder the same way it validates the configured root, so an empty directory
    # is a miss rather than a legacy campaign (see
    # tests/test_paths_campaign_resolution.py).
    old = home / "open-tabletop-gm" / "campaigns" / "old"
    old.mkdir(parents=True)
    (old / "state.md").write_text("# Old\n", encoding="utf-8")
    new_root = tmp_path / "new"
    new_root.mkdir()
    monkeypatch.setattr(paths, "_default_root", lambda: home / "open-tabletop-gm")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(new_root))
    rep = campaign_lint.lint_campaign("old")
    assert rep.path == home / "open-tabletop-gm" / "campaigns" / "old"
    assert not (new_root / "campaigns" / "old").exists()


def test_all_with_missing_campaigns_dir_exits_2(tmp_path):
    r = _run("--all", env_extra={"GM_CAMPAIGN_ROOT": str(tmp_path / "nope")})
    assert r.returncode == 2
    assert b"Traceback" not in r.stderr


def test_campaign_and_all_together_is_a_usage_error(tmp_path):
    r = _run("demo", "--all", env_extra={"GM_CAMPAIGN_ROOT": str(tmp_path)})
    assert r.returncode == 2 and b"not both" in r.stderr


def test_campaign_name_with_separators_is_rejected(tmp_path):
    for bad in ("../x", "a/b", "a\\b", ".."):
        r = _run(bad, env_extra={"GM_CAMPAIGN_ROOT": str(tmp_path)})
        assert r.returncode == 2, bad


def test_skipped_yaml_check_is_stated_not_clean(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)   # import yaml -> ImportError
    rep = campaign_lint.Report("demo", pathlib.Path("."))
    campaign_lint.lint_arc(rep, "type: sandbox\n", 1)
    assert any("SKIPPED" in f["message"] for f in rep.findings)


# ── Fantasy Statblocks: the export is worthless if the plugin misses it ──────
# These pin the bug that shipped: export_bestiary.py wrote 334 correct notes,
# session-workflow.md said to set Bestiary Folder to Bestiary/, and the installed
# config still held the plugin's own default of "/". Nothing read it back, so
# the bestiary was silently unviewable.

STATBLOCK_NOTE = ("---\nstatblock: inline\n---\n\n# Aboleth\n\n"
                  "```statblock\nname: Aboleth\nac: 17\nhp: 135\n```\n")


def bestiary_campaign(tmp_path, paths, *, notes=("Aboleth.md",), installed=True):
    """A campaign with a Bestiary/ export and a plugin config pointing at `paths`."""
    camp = campaign(tmp_path)
    (camp / "Bestiary").mkdir(exist_ok=True)
    for n in notes:
        (camp / "Bestiary" / n).write_text(STATBLOCK_NOTE, encoding="utf-8")
    if installed:
        f = camp / ".obsidian/plugins/obsidian-5e-statblocks"
        f.mkdir(parents=True, exist_ok=True)
        (f / "data.json").write_text(json.dumps({"paths": paths}), encoding="utf-8")
    return camp


def test_the_plugin_default_path_is_an_error_not_a_working_bestiary(tmp_path):
    """`/` is the plugin's default and means the whole vault. This is the bug."""
    camp = bestiary_campaign(tmp_path, ["/"])
    rep = campaign_lint.lint_campaign("demo", camp)
    errs = findings(rep, "error")
    assert errs, "paths=['/'] must not pass as a configured bestiary"
    assert any('"/"' in f["message"] for f in errs), messages(rep)
    # and it has to name the fix, since this is read at 9am before a session
    assert any('Bestiary/' in f["hint"] for f in errs if f["hint"]), messages(rep)


def test_pointing_the_plugin_at_bestiary_is_clean(tmp_path):
    camp = bestiary_campaign(tmp_path, ["Bestiary/"])
    rep = campaign_lint.lint_campaign("demo", camp)
    assert not any("Statblock" in f["message"] or "Bestiary Folder" in f["message"]
                   for f in rep.findings), messages(rep)


def test_a_bare_slash_and_a_trailing_slash_are_the_same_folder(tmp_path):
    """The plugin normalises, so `Bestiary` and `Bestiary/` must both be clean."""
    for paths in (["Bestiary"], ["Bestiary/"]):
        camp = bestiary_campaign(tmp_path, paths)
        rep = campaign_lint.lint_campaign("demo", camp)
        assert not any("Bestiary Folder" in f["message"] for f in rep.findings), \
            (paths, messages(rep))


def test_a_typo_in_the_path_is_an_error_not_an_empty_bestiary(tmp_path):
    """Bestiary/ misspelt renders as an empty list with no error anywhere."""
    camp = bestiary_campaign(tmp_path, ["Bestiaryy/"])
    rep = campaign_lint.lint_campaign("demo", camp)
    assert any("does not exist" in f["message"] for f in rep.findings), messages(rep)


def test_an_empty_path_list_is_an_error(tmp_path):
    camp = bestiary_campaign(tmp_path, [])
    rep = campaign_lint.lint_campaign("demo", camp)
    assert any("empty" in f["message"] for f in rep.findings), messages(rep)


def test_notes_without_a_statblock_block_are_reported(tmp_path):
    """Bestiary/README.md is prose. The plugin lists it as a creature."""
    camp = bestiary_campaign(tmp_path, ["Bestiary/"],
                             notes=("Aboleth.md", "README.md"))
    (camp / "Bestiary" / "README.md").write_text("# Bestiary\n\nDo not edit.\n",
                                                 encoding="utf-8")
    rep = campaign_lint.lint_campaign("demo", camp)
    warns = findings(rep, "warn")
    assert any("README.md" in f["message"] and "1 of 2" in f["message"] for f in warns), \
        messages(rep)


def test_a_campaign_with_no_bestiary_says_nothing_about_the_plugin(tmp_path):
    """No export means nothing to point the plugin at; silence is correct."""
    rep = campaign_lint.lint_campaign("demo", campaign(tmp_path))
    assert not any("Bestiary" in f["message"] or "Statblock" in f["message"]
                   for f in rep.findings), messages(rep)


def test_an_uninstalled_plugin_is_only_warned_about_when_a_bestiary_exists(tmp_path):
    without = bestiary_campaign(tmp_path / "a", ["Bestiary/"], installed=False)
    assert any("not installed" in f["message"] for f in
               campaign_lint.lint_campaign("demo", without).findings)

    with_none = campaign(tmp_path / "b")   # separate root, no Bestiary/ folder
    assert not (with_none / "Bestiary").exists()
    assert not any("not installed" in f["message"] for f in
                   campaign_lint.lint_campaign("demo", with_none).findings)


def test_corrupt_plugin_settings_are_an_error_not_a_crash(tmp_path):
    camp = bestiary_campaign(tmp_path, ["Bestiary/"])
    (camp / ".obsidian/plugins/obsidian-5e-statblocks/data.json").write_text(
        "{ not json", encoding="utf-8")
    rep = campaign_lint.lint_campaign("demo", camp)
    assert rep.errors == 1, messages(rep)
    assert any("unreadable" in f["message"] for f in rep.findings), messages(rep)


# ── token art: Atlas wants an image per token, and most creatures have none ───

def test_art_coverage_is_reported_as_a_ratio_not_an_error(tmp_path):
    """A missing portrait is playable (the colour disc), so this must never
    raise the error count - only state the ratio, which is otherwise only
    discoverable by opening Create tokens and counting."""
    camp = bestiary_campaign(tmp_path, ["Bestiary/"], notes=("Aboleth.md", "Acolyte.md"))
    art = camp / "atlas-vtt/assets/bestiary"
    art.mkdir(parents=True)
    (art / "aboleth.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    rep = campaign_lint.lint_campaign("demo", camp)
    assert rep.errors == 0, messages(rep)
    warns = [f["message"] for f in findings(rep, "warn") if "have a portrait" in f["message"]]
    assert warns and "1 of 2 creatures" in warns[0], messages(rep)


def test_art_coverage_is_silent_when_there_is_no_art_directory(tmp_path):
    camp = bestiary_campaign(tmp_path, ["Bestiary/"])
    rep = campaign_lint.lint_campaign("demo", camp)
    assert not any("have a portrait" in f["message"] for f in rep.findings), messages(rep)


def test_the_readme_note_is_excluded_from_the_art_ratio(tmp_path):
    """README.md is prose in the bestiary folder. Counting it would report
    47/372 and quietly make the number wrong."""
    camp = bestiary_campaign(tmp_path, ["Bestiary/"], notes=("Aboleth.md",))
    (camp / "Bestiary" / "README.md").write_text("# Bestiary\n", encoding="utf-8")
    art = camp / "atlas-vtt/assets/bestiary"
    art.mkdir(parents=True)
    (art / "aboleth.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    rep = campaign_lint.lint_campaign("demo", camp)
    assert any("1 of 1 creatures" in f["message"] for f in rep.findings), messages(rep)


def test_art_matching_uses_the_exporter_slug_not_a_second_copy(tmp_path):
    """A name the exporter would slug differently must still match, which only
    holds if the lint imports map_to_atlas.slug instead of reimplementing it."""
    camp = bestiary_campaign(tmp_path, ["Bestiary/"], notes=("Adult Blue Dragon.md",))
    art = camp / "atlas-vtt/assets/bestiary"
    art.mkdir(parents=True)
    # the slug map_to_atlas.slug() actually produces for that note
    (art / "adult-blue-dragon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    rep = campaign_lint.lint_campaign("demo", camp)
    assert any("1 of 1 creatures" in f["message"] for f in rep.findings), messages(rep)
