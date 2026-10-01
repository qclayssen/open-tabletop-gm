"""npcs_to_statblocks.py: a campaign's npcs.md -> FSB notes.

The tests that matter are the loss ones. Every failure this script can produce
looks like a complete, plausible statblock that is quietly missing something --
which is why each case below asserts a specific entry is PRESENT rather than that
the output merely parses.
"""
from __future__ import annotations

import importlib.util
import pathlib
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


ns = _load("npcs_to_statblocks", "npcs_to_statblocks.py")
eb = _load("export_bestiary", "export_bestiary.py")

# A miniature npcs.md exercising every shape the real file uses: several labelled
# fields sharing one bullet, `n/a` for a value the GM never recorded, the
# Personality/Relationships subsections, and the `---` rule between NPCs.
NPCS_MD = """# NPCs — test

| Name | Role |
|------|------|
| Reeve Aldis Kett | Elected reeve |

---

### Reeve Aldis Kett
- **Role:** Elected reeve of Wickmoor, one of five Council seats | **CR/Level:** 2 | **Location:** Custom house
- **HP:** 27 | **AC:** 15 (coat over a breastplate) | **Attack:** +5 to hit, 1d8+2 bludgeoning (cudgel, kept under the counter) | **Notable abilities:** Procedural memory
- **Demeanor:** Procedural | **Motivation:** Next year's election | **Secret:** He paid Sable
- **Faction:** Reeve's Council | **Current goal:** Decline the request | **Schedule:** 8th to 6th bell

### Personality
- **Trustworthy ↔ Deceptive:** Deceptive, but only about the water
- **Ambitious ↔ Content:** Ambitious in the small way
- **Loyal ↔ Opportunistic:** Loyal to the village
- **Brave ↔ Cowardly:** Cowardly about being seen to be afraid

### Relationships
- **Knows:** Hessa Marrow — professionally, for eight years
- **Owes:** Orrin Sable (dead) — three years of silent payment
- **Fears:** Hessa Marrow — she reads the water

### Notes
Believes the causeway sinking was sabotage.

---

### Orrin Sable
- **Role:** Bell-founder | **CR/Level:** 0 (dead) | **Location:** Workshop
- **HP:** n/a | **AC:** n/a | **Attack:** n/a | **Notable abilities:** Deaf on the left
- **Faction:** independent | **Speech quirk:** n/a

### Notes
The bell he cast has no clapper.
"""


@pytest.fixture(scope="module")
def records():
    return {r["name"]: r for r in ns.parse_npcs(NPCS_MD)}


# ── parsing ────────────────────────────────────────────────────────────────

def test_both_npcs_are_found(records):
    """The `---` rule between entries must not be mistaken for a new NPC."""
    assert set(records) == {"Reeve Aldis Kett", "Orrin Sable"}


def test_several_fields_sharing_one_bullet_are_all_read(records):
    """npcs.md packs Role/CR/Location onto one line. Reading only the first field
    loses the CR, which is the single most load-bearing number on the block."""
    r = records["Reeve Aldis Kett"]
    assert r["type"] == "Elected reeve of Wickmoor, one of five Council seats"
    assert r["cr"] == 2
    assert r["hp"] == 27
    assert r["ac"] == "15 (coat over a breastplate)"


def test_an_annotated_ac_keeps_the_gms_parenthetical(records):
    """FSB takes `ac` as a number OR a string, and "keeps the breastplate secret"
    is why the number is 15 rather than 16."""
    assert records["Reeve Aldis Kett"]["ac"] == "15 (coat over a breastplate)"


def test_the_last_field_on_a_bullet_line_is_not_dropped(records):
    """Regression: `_FIELD` anchored `$` to end-of-STRING while walking the file a
    line at a time, so the final labelled field of every bullet matched nothing.
    Aldis lost his Attack, Schedule and Notable abilities without complaint."""
    r = records["Reeve Aldis Kett"]
    assert r["actions"][0]["name"] == "Cudgel"
    assert "8th to 6th bell" in r["description"]


# ── n/a is not zero ────────────────────────────────────────────────────────

def test_an_absent_value_is_omitted_rather_than_zeroed(records):
    """A dead NPC has no HP. `0` would be a claim -- a corpse with no hit points is
    a different creature from one with none recorded."""
    sable = records["Orrin Sable"]
    assert "hp" not in sable
    assert "ac" not in sable
    assert "actions" not in sable


# ── the honesty rules ──────────────────────────────────────────────────────

def test_ability_scores_are_not_invented(records):
    """npcs.md records an attack bonus but no ability scores. Six 10s would look
    like a transcribed block and be nothing of the kind."""
    for record in records.values():
        assert ns._is_statless(record)
        for ability in eb._ABILITY_ORDER:
            assert ability not in record


def test_ability_scores_are_passed_through_when_the_file_has_them():
    """A campaign that DOES record scores gets a full block, with no special case."""
    got = ns._build("Scored", [
        "- **STR:** 14 | **DEX:** 12 | **CON:** 13 | **INT:** 10 | **WIS:** 11 | **CHA:** 9"], {})
    assert [got[a] for a in eb._ABILITY_ORDER] == [14, 12, 13, 10, 11, 9]
    assert not ns._is_statless(got)
    assert eb.build_fields(got)["stats"] == [14, 12, 13, 10, 11, 9]

    # Five of six is still a refusal, not a default: the guard names what is absent.
    partial = ns._build("Partial", [
        "- **STR:** 14 | **DEX:** 12 | **CON:** 13 | **INT:** 10 | **WIS:** 11"], {})
    assert ns._stat_state(partial) == "partial"
    assert not ns._is_statless(partial), "a half-filled record must not look complete"
    assert [partial[a] for a in eb._ABILITY_ORDER[:5]] == [14, 12, 13, 10, 11]
    with pytest.raises(ValueError, match="CHA"):
        eb.build_fields(partial)


def test_a_faction_is_not_written_as_an_alignment(records):
    """"Reeve's Council" in the Alignment slot reads as a moral alignment, which is
    a different claim, and npcs.md has no alignment field for these people."""
    assert "alignment" not in records["Reeve Aldis Kett"]
    assert "Reeve's Council" in records["Reeve Aldis Kett"]["description"]


def test_the_attack_line_is_copied_verbatim(records):
    """It is NOT expanded into "Melee Weapon Attack: +5 to hit, reach 5 ft., one
    target. Hit: 6 (1d8 + 2) bludgeoning damage." The file does not say melee, does
    not give a reach, and does not give the average."""
    desc = records["Reeve Aldis Kett"]["actions"][0]["desc"]
    assert desc == "+5 to hit, 1d8+2 bludgeoning (cudgel, kept under the counter)"


def test_a_horizontal_rule_never_reaches_the_note(records):
    """The `---` between NPCs used to land inside the Notes text, quoting an
    Obsidian frontmatter delimiter into the middle of a statblock."""
    assert "---" not in records["Reeve Aldis Kett"]["description"]


# ── FSB rendering, which is where the names get lost ───────────────────────

@pytest.fixture(scope="module")
def parsed(records):
    out = {}
    for name, record in records.items():
        note = eb.note_for(record, "statblocks.json")
        body = note.split("```statblock\n", 1)[1].rsplit("\n```", 1)[0]
        out[name] = yaml.safe_load(body)
    return out


def test_every_trait_name_is_unique(parsed):
    """FSB keys traits by name in a Map: two traits sharing a name collapse to the
    last, silently. Four "Personality" and three "Relationships" traits is seven
    entries lost, and the block still looks complete."""
    for name, block in parsed.items():
        for container in ("traits", "actions", "reactions"):
            seen = [e["name"] for e in block.get(container, [])]
            assert len(seen) == len(set(seen)), f"{name}/{container}: {seen}"


def test_no_trait_name_contains_a_colon(parsed):
    """Traits round-trip through `description`, where the name is read as everything
    up to the first colon -- so "Personality: Trustworthy <-> Deceptive" came back as
    "Personality", and four of them collapsed to one."""
    for block in parsed.values():
        for entry in block.get("traits", []):
            assert ":" not in entry["name"], entry["name"]


def test_the_personality_axes_all_survive(parsed):
    names = [t["name"] for t in parsed["Reeve Aldis Kett"]["traits"]]
    for axis in ("Trustworthy", "Ambitious", "Loyal", "Brave"):
        assert any(axis in n for n in names), f"{axis} lost: {names}"


def test_every_relationship_survives(parsed):
    names = [t["name"] for t in parsed["Reeve Aldis Kett"]["traits"]]
    for rel in ("Knows", "Owes", "Fears"):
        assert any(rel in n for n in names), f"{rel} lost: {names}"


def test_a_stat_less_npc_renders_without_a_stats_table(parsed):
    """FSB binds `stats` to a fixed-width table, so a stat-LESS record must not
    emit one. The block is still useful: name, subheading, traits."""
    assert "stats" not in parsed["Orrin Sable"]
    assert parsed["Orrin Sable"]["cr"] == 0
    assert len(parsed["Orrin Sable"]["traits"]) >= 4


def test_empty_fields_are_not_emitted(parsed):
    """`size: ""` does not render in FSB, but it is still a line in the note, and
    npcs.md has no size for these people."""
    assert "size" not in parsed["Reeve Aldis Kett"]


def test_the_note_is_marked_derived_from_the_campaign_file(parsed):
    note = eb.note_for(ns.parse_npcs(NPCS_MD)[0], "statblocks.json")
    assert "Derived from `statblocks.json`" in note
    assert "Regenerated on export" in note


def test_a_subsection_before_any_npc_is_not_turned_into_one():
    """A file opening straight into `### Personality` has a subsection belonging to
    no NPC. Falling through would emit a statblock named "Personality", built from
    someone else's inner life -- a fabricated NPC wearing a real one's data."""
    records = ns.parse_npcs("### Personality\n- **Brave ↔ Cowardly:** Cowardly\n\n"
                             "### Tam Cleave\n- **CR/Level:** 1\n")
    assert [r["name"] for r in records] == ["Tam Cleave"]


def test_a_subsection_never_becomes_an_npc_name(records):
    for name in records:
        assert name not in ("personality", "relationships", "notes")


# ── the shapes npcs-full.md actually uses ───────────────────────────────────
#
# Every case below is a defect that shipped in this parser and produced a
# character nobody wrote, or no character at all. `NPCS_MD` above is the flat
# `###` shape; `NPCS_FULL_MD` is the real file's shape, where the 26 `## ` headings
# are the people and the 19 `### ` headings are their subsections. The original
# `^###` matcher took the subsections and missed every person, so this script
# passed 19 tests and emitted nothing for the only campaign that has one.

NPCS_FULL_MD = """# NPCs (full entries): strixhaven-kairos

### Voice sheet (read before any scene with these five)
- **Juno** (short, blunt): "Right, well."

---

## Juno Ashvale
- **Role:** Companion, medic | **CR/Level:** as Kairos's level (base: **Acolyte** at 1) | **Location:** first-year hall
- **Demeanor:** blunt, warm | **Secret:** Vess's assignee

### Personality
- **Brave ↔ Cowardly:** brave to a fault

---

## Magister Hesper Vael
- **Role:** Guardian, Owlin archaeomancer | **CR/Level:** **Archmage**-tier | **Location:** Owl Roost

---

## Deans Adrix and Nev (Quandrix, canon post-invasion deans)
- **Adrix:** the theorist; precise and cool. Runs the probation hearing.
- **Nev, the Practical Dean:** turns theory into fractals. Warm, and teases.

---

## Quandrix Squad and Campus Faces (minor, no secrets, no suspects)
- **Tilana Kapule** (Quandrix, outgoing Reader): strategist, gracious about being displaced.
- **Drazhomir Yarnask** (Quandrix Guard): steady and dry.

---

## Web of Wants (every student NPC: desire, fear/secret, problem with someone)

| NPC | Wants | Fear / Secret |
|-----|-------|---------------|
| Juno Ashvale | send money home | forgetting her mother's face |
"""

FULL = {r["name"]: r for r in ns.parse_npcs(NPCS_FULL_MD)}


def test_people_at_the_shallower_heading_level_are_all_found():
    """The regression this whole file exists for: `^###` matched the subsections."""
    assert {"Juno Ashvale", "Magister Hesper Vael"} <= set(FULL)


def test_the_voice_sheet_is_not_a_character():
    """A `###` section before any `##` belongs to no NPC."""
    assert not any("Voice sheet" in name for name in FULL)


def test_a_relationship_bullet_is_not_a_roster_of_two_people():
    """`Relationships:` and `Notes:` are field labels this file writes as bullets.

    Read as names, a single NPC became two characters called "Relationships" and
    "Notes" -- which is how the first roster rule turned Vess the Tallykeeper into
    a pair of people. A label that is in the field vocabulary is never a person.
    """
    assert "Relationships" not in FULL
    assert "Notes" not in FULL


def test_a_roster_heading_splits_into_the_people_it_names():
    """`Deans Adrix and Nev` is not a person; Adrix and Nev are."""
    assert "Adrix" in FULL
    assert any(name.startswith("Nev") for name in FULL)
    assert not any("Adrix and Nev" in name for name in FULL)


def test_a_roster_member_written_outside_parentheses_is_found():
    """`- **Tilana Kapule** (Quandrix, Reader): ...` puts the colon outside the bold.

    `_BULLET` requires the colon inside, so this shape matched nothing and the
    whole Squad came through as one token named after its own heading.
    """
    assert "Tilana Kapule" in FULL
    assert "Drazhomir Yarnask" in FULL


def test_a_roster_member_keeps_its_description():
    """Splitting a roster must not throw the only prose about those people away."""
    assert "strategist" in FULL["Tilana Kapule"]["description"]


def test_a_table_section_is_not_a_character():
    """`Web of Wants` is a markdown table about people, and defines no fields."""
    assert not any("Web of Wants" in name for name in FULL)


def test_a_prose_subsection_is_kept_verbatim():
    """Ysolde's `### Stat block (finale)` is the only place her numbers are written.

    A parser that keeps only Personality/Relationships/Notes deletes the most
    complete statblock in the campaign and emits a block with no HP in its place.
    """
    records = ns.parse_npcs(
        "## Ysolde Marrow\n- **Role:** Mentor\n\n"
        "### Stat block (finale)\nAC 15 (lattice ward), HP 130, spell save DC 17.\n")
    traits = records[0]["description"]
    assert "HP 130" in traits
    assert "lattice ward" in traits


def test_prose_in_the_cr_field_is_not_read_as_a_number():
    """Four characters shipped with a CR that appears nowhere in the source.

    Taking the first integer anywhere in `CR/Level` gave Mabli Quenn CR 25 (from
    "reduced HP 25 from level 5"), Theodric Vane CR 6 ("from level 6 Mage reskin"),
    Vess CR 189 ("book p.189") and Hesper CR 0 ("Scene 0B"). That is the Ruin
    Grinder failure: a plausible number transcribed from something that was not
    one, and a Challenge line the table would believe.
    """
    for value, expected in (
        ("base **Apprentice Wizard**-style (use **Mage** at reduced HP 25 from level 5)",
         "base Apprentice Wizard-style (use Mage at reduced HP 25 from level 5)"),
        ("from level 6 **Mage** reskin", "from level 6 Mage reskin"),
        ("**Daemogoth** (book p.189, CR 10)", "Daemogoth (book p.189, CR 10)"),
        ("**Archmage**-tier", "Archmage-tier"),
    ):
        assert ns._cr(value) == expected
    assert isinstance(ns._cr("base **Apprentice Wizard**-style"), str)


def test_a_real_cr_is_still_a_number():
    """The prose rule must not cost the numbers the GM actually wrote."""
    assert ns._cr("2") == 2
    assert ns._cr("**11**") == 11
    assert ns._cr("0 (dead)") == 0


def test_the_entries_file_is_preferred_over_the_index():
    """`npcs.md` says of itself: "Index only. Full entries ... in `npcs-full.md`".

    Both this script and the linter read `npcs.md` and expect headings, so for
    `strixhaven-kairos` the miss is structural rather than a parser bug. A
    campaign with no separate file still resolves.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        campaign = pathlib.Path(tmp)
        (campaign / "npcs.md").write_text(
            "| Name | Role |\n|------|------|\n| Reeve Aldis Kett | Elected reeve |\n",
            encoding="utf-8")
        assert ns.entries_file(campaign).name == "npcs.md"
        (campaign / "npcs-full.md").write_text("## Reeve Aldis Kett\n- **CR/Level:** 2\n",
                                               encoding="utf-8")
        assert ns.entries_file(campaign).name == "npcs-full.md"
