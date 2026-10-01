"""pcs_to_statblocks.py: a character sheet -> an FSB note.

The failures that matter here are the ones that render. A PC block that is missing
its saving throws, its attacks, or the whole of its Features section still looks
like a character sheet at the table -- it is simply wrong in a way nobody notices
until the rules lawyer asks why the wizard cannot cast. So each test below asserts
a specific value is PRESENT, not that the note merely builds.

`KAIROS_MD` is an abridgement of the real `characters/Kairos.md`, keeping every
shape that has caused a bug: a `# Title` above `## ` sections, an ability table
whose header must be read for order, an annotated AC, three attacks written as
table columns rather than sentences, features under `### ` inside a `## ` section,
and a `Last Updated` the note has to carry.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to re-read the emitted notes")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pcs = _load("pcs_to_statblocks", "pcs_to_statblocks.py")


KAIROS_MD = """# Kairos
**Player:** Quentin  **Campaign:** strixhaven-kairos  **Last Updated:** 2026-09-26

## Identity
- **Race:** Kenku (Multiverse) | **Class:** Wizard 1 / Fighter 1 (Chronurgy at 3) | **Level:** 1
- **Alignment:** Neutral Good (leaning) | **XP:** 0

## Ability Scores
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| 8 (-1) | 14 (+2) | 14 (+2) | 19 (+4) | 10 (+0) | 8 (-1) |

## Combat Stats
- **HP:** 8 / 8 | **Temp HP:** 0
- **AC:** 12 (13 with Mage Armor) | **Initiative:** +2 | **Speed:** 30 ft

## Saving Throws
| STR | DEX | CON | INT | WIS | CHA |
|-----|-----|-----|-----|-----|-----|
| -1 | +2 | +2 | +6* | +2* | -1 |

## Attacks
| Name | Attack Bonus | Damage | Type | Notes |
|------|-------------|--------|------|-------|
| Fire Bolt | +6 | 1d10 | fire | 120 ft cantrip |
| Dagger | +4 | 1d4+2 | piercing | finesse, thrown 20/60 |

## Known Spells / Cantrips
- **Cantrips (Wizard, 3):** Fire Bolt, Mind Slusion, Minor Illusion
- **Spellbook (1st):** Silvery Barbs, Shield, Mage Armor

## Features & Traits

### Wizard
- **Arcane Recovery** (1/day, short rest: recover slots).
- **Level 2, Chronurgy Magic:** Chronal Shift.

### Kenku Racial
- **Expert Duplication:** advantage when copying writing.

## Backstory & Notes
Kairos remembers one word and darkness.

## Tracking Sheet
*Fill in as you play.*
| Year | Exam |
|------|------|
| 1 | Placement |
"""


@pytest.fixture(scope="module")
def record():
    return pcs.parse_pc(KAIROS_MD)


@pytest.fixture(scope="module")
def fields(record):
    note = pcs.note_for_pc(record)
    body = re.search(r"```statblock\n(.*?)\n```", note, re.S).group(1)
    return yaml.safe_load(body)


# ── identity ────────────────────────────────────────────────────────────────

def test_the_name_is_the_sheet_title_not_the_first_section(record):
    """`_HEADING` starts at `## `, so its first match is `Identity`.

    That shipped a PC named "Identity" with Kairos's statistics, which is a
    character at the table called Identity.
    """
    assert record["name"] == "Kairos"


def test_race_and_class_become_the_subheading(record):
    assert record["type"] == "Kenku Wizard 1 / Fighter 1 (Chronurgy at 3)"
    assert record["alignment"] == "Neutral Good (leaning)"
    assert record["level"] == 1


def test_the_sheets_own_last_updated_is_carried(record):
    """A snapshot nobody can date is a plausible lie.

    This is the whole reason a PC block is stamped rather than just written: the
    block goes stale the moment the sheet is edited, and the date is what makes
    that visible instead of invisible.
    """
    assert record["_last_updated"] == "2026-09-26"
    assert record["_player"] == "Quentin"


def test_the_note_states_the_sheet_is_the_authority():
    note = pcs.note_for_pc(pcs.parse_pc(KAIROS_MD))
    assert "characters/Kairos.md" in note
    assert "2026-09-26" in note
    # The whole line is replaced, not a prefix of it: the tail of `note_for`'s own
    # sentence used to survive and credit export_bestiary.py for a note it never
    # wrote.
    assert "export_bestiary.py" not in note
    assert "by `export_bestiary.py`. Regenerated" not in note


# ── mechanics ───────────────────────────────────────────────────────────────

def test_ability_scores_are_transcribed_in_order(fields):
    assert fields["stats"] == [8, 14, 14, 19, 10, 8]


def test_ability_scores_are_read_from_the_sheets_own_column_order():
    """FSB's `stats` is positional, so a sheet in another order must still work.

    Assuming the printed STR/DEX/CON/INT/WIS/CHA scrambles a sheet that lists them
    differently, and a scrambled character sheet still looks completely normal.
    """
    shuffled = KAIROS_MD.replace(
        "| STR | DEX | CON | INT | WIS | CHA |\n|-----|-----|-----|-----|-----|-----|",
        "| CHA | STR | WIS | CON | INT | DEX |\n|-----|-----|-----|-----|-----|-----|",
    ).replace(
        "| 8 (-1) | 14 (+2) | 14 (+2) | 19 (+4) | 10 (+0) | 8 (-1) |",
        "| 8 (-1) | 8 (-1) | 10 (+0) | 14 (+2) | 19 (+4) | 14 (+2) |",
    )
    assert pcs.parse_pc(shuffled)["stats"] == [8, 14, 14, 19, 10, 8]


def test_saving_throws_are_emitted(record, fields):
    """They were silently absent when handed over as a finished list.

    `export_bestiary.saves()` reads a DICT keyed by ability -- the SRD's shape.
    Passing `[{Str: -1}, ...]` instead renders no saves block at all, and a
    character with no saving throws is a character the rules will mishandle.
    """
    assert isinstance(record["saves"], dict)
    assert fields["saves"] == [
        {"strength": -1}, {"dexterity": 2}, {"constitution": 2},
        {"intelligence": 6}, {"wisdom": 2}, {"charisma": -1},
    ]


def test_an_annotated_ac_keeps_the_gms_parenthetical(fields):
    """`12 (13 with Mage Armor)` is why the AC is what it is.

    Emitting a bare `12` puts Mage Armor out of reach of the sheet's own note
    that he casts it as the scene opens, and AC 12 is a number the table will use.
    """
    assert fields["ac"] == "12 (13 with Mage Armor)"


def test_hp_and_speed(fields):
    assert fields["hp"] == 8
    assert fields["speed"] == "30 ft."


def test_the_table_separator_is_not_read_as_a_row_of_zeroes():
    """`|-----|` parses as six cells with no integers.

    It is dropped by `_rows` rather than by each caller remembering to skip it,
    because a sheet that renders as all zeroes is indistinguishable from a
    character who really has 1 in every stat.
    """
    assert 0 not in pcs._rows(pcs._section(KAIROS_MD, "Ability Scores"))[1]


# ── attacks ─────────────────────────────────────────────────────────────────

def test_every_attack_becomes_an_action(fields):
    names = [a["name"] for a in fields["actions"]]
    assert names == ["Fire Bolt", "Dagger"]


def test_an_attack_keeps_its_bonus_damage_and_type(fields):
    """A printed sheet splits these across three columns; a statblock wants one line.

    Joining them is transcription: all three numbers are on the sheet. Expanding
    `1d10 fire` into a full attack sentence with a range would be interpretation.
    """
    fire_bolt = fields["actions"][0]
    assert "+6 to hit" in fire_bolt["desc"]
    assert "1d10 fire" in fire_bolt["desc"]


def test_an_attack_with_no_bonus_does_not_invent_one():
    sheet = KAIROS_MD.replace("| Fire Bolt | +6 | 1d10 | fire | 120 ft cantrip |",
                              "| Fire Bolt | n/a | 1d10 | fire | 120 ft cantrip |")
    desc = pcs.parse_pc(sheet)["actions"][0]["desc"]
    assert "to hit" not in desc


# ── features ────────────────────────────────────────────────────────────────

def test_every_feature_subsection_becomes_a_trait(fields):
    """The span bug that emptied `## Features & Traits` entirely.

    `_section` used to end at the NEXT heading of any level, so a `## ` section
    immediately followed by a `### ` had an empty body. Every one of Kairos's
    actual capabilities -- Arcane Recovery, Chronal Shift, Expert Duplication --
    was dropped, and the block still rendered, so it looked like a character.
    """
    names = [t["name"] for t in fields["traits"]]
    assert "Wizard" in names
    assert "Kenku Racial" in names


def test_a_features_span_ends_at_the_next_shallow_heading():
    body = pcs._section(KAIROS_MD, "Features & Traits")
    assert "Arcane Recovery" in body
    assert "Backstory" not in body


def test_gm_annotations_are_kept_rather_than_dropped():
    """The sheet's rulings about how to RUN it are load-bearing.

    Dropping them loses an Arbiter decision; flattening them into an unrelated
    trait is how a ruling stops being read. They are kept as written.
    """
    sheet = KAIROS_MD.replace(
        "- **Expert Duplication:** advantage when copying writing.",
        "- **Expert Duplication:** advantage when copying writing.\n"
        "- **Religion is deliberately NOT proficient** (ruled by the Arbiter).")
    desc = pcs.parse_pc(sheet)["description"]
    assert "NOT proficient" in desc


def test_spells_are_a_trait_not_a_parsed_slot_count(fields):
    """The sheet records which spells are KNOWN, which are PREPARED, and a
    per-scene budget ("ONE slot for the whole night -- Shield or Magic Missile").

    Parsing that into `spell_slots` would say two slots and be wrong for the scene
    the GM is running, which is the only moment the note is open.
    """
    spell = [t for t in fields["traits"] if t["name"] == "Spellcasting"]
    assert spell
    assert "Mage Armor" in spell[0]["desc"]
    assert "spell_slots" not in fields


def test_the_tracking_worksheet_is_not_part_of_the_character(fields):
    """"Report cards", extracurriculars and student dice are the GM's worksheet.

    A statblock that claims Kairos failed his Placement exam he has not taken yet
    is a sentence the table will read aloud.
    """
    desc = " ".join(t["desc"] for t in fields["traits"])
    assert "Report cards" not in desc
    assert "Placement" not in desc


# ── refusals ────────────────────────────────────────────────────────────────

def test_a_sheet_with_no_ability_scores_omits_rather_than_fills():
    """FSB binds `stats` to a six-wide table.

    Three scores render as a character whose CON and WIS are unreadable, which is
    not distinguishable at the table from a character who has no Constitution.
    """
    partial = KAIROS_MD.replace("| 8 (-1) | 14 (+2) | 14 (+2) | 19 (+4) | 10 (+0) | 8 (-1) |",
                                "| 8 (-1) | 14 (+2) | 14 (+2) |  |  |  |")
    record = pcs.parse_pc(partial)
    assert "stats" not in record
    assert record["hp"] == 8, "the rest of the sheet is still usable"


def test_refusing_to_write_a_pc_note_into_the_bestiary(tmp_path):
    """That directory is regenerated wholesale and a hand-written note is deleted.

    This is the one destination rule with teeth, so it is enforced at the CLI
    rather than left to the `--vault` default being right.
    """
    bestiary = tmp_path / "Bestiary"
    bestiary.mkdir()
    assert pcs.main(["--campaign", "strixhaven-kairos", "--vault", str(bestiary)]) == 1


def test_vault_is_the_vault_and_pc_notes_land_in_pcs(tmp_path):
    """`--vault` names the vault; PC notes go to `<vault>/PCs`.

    Two separate destination bugs are covered here.

    Reading `--vault` as the destination directory put one note per character into
    the vault root, beside `state.md` and `world.md`, where it reads as one of the
    campaign's own documents rather than as generated output.

    And `<vault>/Characters` was worse than merely misplaced: `Characters` and
    `characters` are ONE directory on a case-insensitive filesystem, and
    `characters/` holds the sheets. So the export overwrote Kairos's 167-line
    hand-written sheet with a generated note, silently, and the only symptom was
    `campaign_lint.py` reporting the sheet's `## Identity` section missing. The
    sheet was recovered from git; the bug is what this asserts cannot recur.
    """
    written = pcs.main(["--campaign", "strixhaven-kairos",
                        "--vault", str(tmp_path / "somevault")])
    if written == 1:      # no campaign resolvable in the test environment
        pytest.skip("campaign strixhaven-kairos is not resolvable here")
    assert (tmp_path / "somevault" / "PCs").is_dir()
    assert not (tmp_path / "somevault" / "Characters").exists()
    assert not list(tmp_path.glob("*.md")), "a note landed in the vault root"


def test_a_vault_that_is_the_sheets_directory_never_overwrites_a_sheet(
        tmp_path, monkeypatch):
    """The regression, as it actually happened.

    `--vault <campaign>` used to write to `<vault>/Characters`, and
    `Characters` is `characters` on a case-insensitive filesystem -- so the note
    landed on top of `characters/Kairos.md` and replaced a 167-line hand-written
    sheet with generated output. The only symptom anywhere was `campaign_lint.py`
    reporting `## Identity` missing.

    This asserts the property rather than the mechanism: no matter what directory
    is handed over as the vault, a sheet that this run read is still byte-identical
    afterwards. That holds because the output is always `<vault>/PCs/`, one level
    below the vault, and because the collision guard refuses rather than clobbers.
    """
    import paths
    # Each case gets its own parent, because on a case-insensitive filesystem
    # `characters` and `CHARACTERS` are the same directory and mkdir would fail on
    # the second one.
    for i, vault_name in enumerate(("characters", "CHARACTERS", "Characters")):
        campaign = tmp_path / f"case{i}" / "camp"
        (campaign / "characters").mkdir(parents=True)
        sheet = campaign / "characters" / "Kairos.md"
        sheet.write_text(KAIROS_MD, encoding="utf-8")
        monkeypatch.setattr(paths, "find_campaign",
                            lambda name, migrate=True, c=campaign: c)
        for passed in (campaign, campaign / "characters", campaign / "CHARACTERS"):
            pcs.main(["--campaign", "strixhaven-kairos", "--vault", str(passed)])
            assert sheet.read_text(encoding="utf-8") == KAIROS_MD, (
                f"the sheet was overwritten writing to {passed}")


def test_the_note_parses_as_yaml_and_carries_every_field(fields):
    """A note that does not parse renders as nothing at all, silently."""
    for key in ("name", "type", "ac", "hp", "speed", "stats", "saves",
                "traits", "actions"):
        assert key in fields, f"{key} missing from the emitted note"
