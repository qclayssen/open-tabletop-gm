"""export_bestiary.py: SRD -> Fantasy Statblocks notes.

Experiment A. The notes are derived output, so the tests that matter are the ones
that catch silent data loss: a dropped action, a stat score that fell out of the
positional array, a description that got mangled by YAML quoting. Those all render
as a plausible-looking statblock with something missing, which is the failure mode
this exporter must not have.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to re-read the emitted notes")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "export_bestiary", ROOT / "scripts" / "export_bestiary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eb = _load_module()
pytestmark = pytest.mark.skipif(
    not DATA.exists(), reason="generated SRD dataset is gitignored and needs a build")


@pytest.fixture(scope="module")
def monsters():
    with open(DATA, encoding="utf-8") as fh:
        import json
        return json.load(fh)["monsters"]


@pytest.fixture(scope="module")
def by_name(monsters):
    return {m["name"]: m for m in monsters}


# ── no silent loss ──────────────────────────────────────────────────────────

def test_every_action_survives(monsters):
    """No SRD action may be dropped.

    This is the test that matters. The SRD splits action text across two places:
    `actions[].raw` for 287 of 841 actions, and the `description` prose for the
    rest, so an exporter that picks one source silently loses the other. An early
    version did exactly that and rendered the Ancient Red Dragon with one action
    instead of six.
    """
    lost = []
    for monster in monsters:
        emitted = {a["name"] for a in eb.build_fields(monster).get("actions", [])}
        for action in monster.get("actions", []):
            if action["name"] not in emitted:
                lost.append((monster["name"], action["name"]))
    assert not lost, f"{len(lost)} actions dropped, e.g. {lost[:5]}"


def test_no_entry_has_an_empty_description(monsters):
    """FSB drops a bare-named entry's usefulness; a nameless blank is worse."""
    empties = []
    for monster in monsters:
        for container in ("traits", "actions", "legendary_actions", "mythic_actions"):
            for entry in eb.build_fields(monster).get(container, []):
                if not entry.get("desc"):
                    empties.append((monster["name"], container, entry.get("name")))
    assert not empties, f"{len(empties)} empty descriptions, e.g. {empties[:5]}"


def test_every_monster_emits_a_name(monsters):
    for monster in monsters:
        assert eb.build_fields(monster)["name"] == monster["name"]


# ── field shapes FSB actually requires ─────────────────────────────────────

def test_stats_is_a_six_element_positional_array(by_name):
    """FSB binds `stats` to a fixed-width table, in Str..Cha order.

    A block-style list renders as six rows of one number each, which is wrong but
    not obviously so, hence asserting the list form explicitly.
    """
    fields = eb.build_fields(by_name["Goblin"])
    assert fields["stats"] == [8, 14, 10, 10, 8, 8]
    note = eb.note_for(by_name["Goblin"])
    assert "stats: [8, 14, 10, 10, 8, 8]" in note, "stats must be inline, not a block list"


def test_ability_order_is_str_dex_con_int_wis_cha(by_name):
    fields = eb.build_fields(by_name["Ancient Red Dragon"])
    monster = by_name["Ancient Red Dragon"]
    for i, ability in enumerate(("str", "dex", "con", "int", "wis", "cha")):
        assert fields["stats"][i] == monster[ability]


def test_saves_drop_zero_and_use_full_names(by_name):
    """FSB drops a 0 entry itself, and expects ability names, not abbreviations."""
    fields = eb.build_fields(by_name["Ancient Red Dragon"])
    assert {"dexterity": 7} in fields["saves"]
    assert all(value != 0 for entry in fields["saves"] for value in entry.values())


def test_zero_modifier_saves_are_omitted():
    """A `saves` entry of 0 renders as a dropped line, so it should not be emitted."""
    fields = eb.build_fields({
        "name": "Dummy", "saves": {"dex": 0, "con": 3}, "stats": [10] * 6,
    })
    assert fields["saves"] == [{"constitution": 3}]


def test_speed_drops_the_redundant_walk_prefix(by_name):
    """The SRD stores "walk 30 ft."; FSB's canonical example is just "30 ft."."""
    assert eb.build_fields(by_name["Goblin"])["speed"] == "30 ft."


def test_multi_speed_is_preserved_verbatim(by_name):
    dragon = eb.build_fields(by_name["Ancient Red Dragon"])["speed"]
    assert "fly 80 ft." in dragon and "climb" in dragon


# ── text handling ───────────────────────────────────────────────────────────

def test_traits_and_actions_are_split_out_of_the_description(by_name):
    fields = eb.build_fields(by_name["Ancient Red Dragon"])
    assert "Legendary Resistance" in [t["name"] for t in fields["traits"]]
    names = [a["name"] for a in fields["actions"]]
    assert "Bite" in names and "Fire Breath" in names


def test_packed_legendary_actions_split_into_their_own_container(by_name):
    """An Ancient Red Dragon's Fire Breath carries its legendary actions in one string.

    Left packed, they render as a single unreadable action; FSB has a dedicated
    `legendary_actions` container and the printed statblock separates them.
    """
    fields = eb.build_fields(by_name["Ancient Red Dragon"])
    names = [a["name"] for a in fields.get("legendary_actions", [])]
    assert names == ["Detect", "Tail Attack", "Wing Attack (Costs 2 Actions)"]
    breath = next(a for a in fields["actions"] if a["name"] == "Fire Breath")
    assert "Legendary" not in breath["desc"]


def test_split_packed_leaves_an_ordinary_action_alone():
    assert eb.split_packed("Melee Weapon Attack: +4 to hit.") == [
        ("", "", "Melee Weapon Attack: +4 to hit.")]


def test_split_packed_labels_each_kind():
    pieces = eb.split_packed(
        "The dragon breathes fire. Legendary \u2014 Detect: It looks around.")
    assert pieces[0][0] == "" and pieces[0][2] == "The dragon breathes fire."
    assert pieces[1][0] == "legendary" and pieces[1][1] == "Detect"


# ── YAML emission, which is where prose gets mangled ───────────────────────

@pytest.mark.parametrize("text", [
    "Melee Weapon Attack: +4 to hit, reach 5 ft.",   # contains ": "
    "* Catches on fire.",                             # leading alias marker
    "[bracketed] text",                               # leading flow marker
    "{braced} text",                                  # leading flow marker
    "# not a comment",                                # leading comment marker
    "Ends with a colon:",                             # trailing colon
])
def test_dangerous_scalars_are_quoted(text):
    """FSB parses with stock Obsidian YAML and swallows failures silently.

    An unquoted `desc` containing ": " is a parse error, and the note then fails to
    index with nothing but a log line to show for it, so quoting is not cosmetic.
    """
    quoted = eb.yaml_scalar(text)
    assert quoted.startswith('"') and quoted.endswith('"')
    assert yaml.safe_load(f"k: {quoted}")["k"] == text


def test_attack_text_survives_a_yaml_round_trip(by_name):
    """The end-to-end check: an emitted note re-reads to the text we put in."""
    note = eb.note_for(by_name["Goblin"])
    body = note.split("```statblock\n", 1)[1].rsplit("\n```", 1)[0]
    parsed = yaml.safe_load(body)
    scimitar = next(a for a in parsed["actions"] if a["name"] == "Scimitar")
    assert scimitar["desc"] == ("Melee Weapon Attack: +4 to hit, reach 5 ft., one "
                                "target. Hit: 5 (1d6 + 2) slashing damage.")


def test_every_emitted_note_parses(monsters):
    """All 334 notes must be valid YAML with a usable stats array."""
    for monster in monsters:
        body = eb.note_for(monster).split("```statblock\n", 1)[1].rsplit("\n```", 1)[0]
        parsed = yaml.safe_load(body)
        assert len(parsed["stats"]) == 6, monster["name"]


# ── the derived-output contract ─────────────────────────────────────────────

def test_notes_are_marked_derived_so_they_are_never_edited_in_place(monsters):
    """`combat.py adjust TOKEN ac=16` exists because the SRD value is wrong for a
    given fight. An edited note becomes a stale snapshot with a plausible number
    that reads as true, so every note says it is regenerated."""
    for monster in monsters[:20]:
        assert "Regenerated on export" in eb.note_for(monster)


def test_note_uses_inline_mode_with_a_block_ref(by_name):
    """`inline` keeps the data in one place: the fence, with frontmatter only
    triggering the watcher. `statblock: true` would duplicate it across both."""
    note = eb.note_for(by_name["Goblin"])
    assert "statblock: inline" in note
    assert 'statblock-link: "#^statblock"' in note
    assert note.rstrip().endswith("^statblock")


def test_fence_is_bare_statblock_with_nothing_after_it(by_name):
    """The plugin's fence regex is `/^```[^\\S\\r\\n]*statblock\\s?\\n/`, a language
    tag after `statblock` does not match, and the note silently fails to register."""
    assert "\n```statblock\n" in eb.note_for(by_name["Goblin"])


def test_safe_name_strips_path_and_markup_characters():
    assert eb.safe_name("Goblin (Boss)") == "Goblin Boss"
    assert eb.safe_name("A/B\\C") == "ABC"
    assert eb.safe_name("***") == "unnamed"


# ── --data: non-SRD content stays out of dnd5e_srd.json ──────────────────────

def test_note_names_the_file_it_came_from(by_name):
    """A note derived from a licensed book must not claim to be SRD-derived. The
    provenance line is what a reader trusts to know where a statblock came from."""
    srd_note = eb.note_for(by_name["Goblin"], "dnd5e_srd.json")
    other_note = eb.note_for(by_name["Goblin"], "dnd5e_strixhaven_students.json")
    assert "Derived from `dnd5e_srd.json`" in srd_note
    assert "Derived from `dnd5e_strixhaven_students.json`" in other_note
    assert "dnd5e_srd.json" not in other_note


# ── the portrait address ─────────────────────────────────────────────────────
#
# The image is a URL to art that is NOT in this repository and is not licensed
# into it, so the only honest thing a note can carry is the address. These tests
# are the ones that stop it becoming a claim of local presence, and the one that
# stops a URL being written unquoted into YAML.

def test_a_creature_with_an_upstream_portrait_records_the_address(monsters):
    with_image = [m for m in monsters if str(m.get("image") or "").strip()]
    assert with_image, "no SRD creature carries an image address"
    for monster in with_image:
        assert monster["image"].startswith("https://"), monster["name"]


def test_the_portrait_goes_in_the_fence_and_never_the_frontmatter(by_name):
    """`statblock_art.stamp_image` owns the frontmatter `image:` and strips any
    key written there, so an address emitted there would be deleted on its next
    run. The fence is the one place both tools can write without a fight."""
    note = eb.note_for(by_name["Goblin"])
    front, _, rest = note.partition("```statblock\n")
    assert "image:" not in front
    assert "image:" in rest


def test_the_portrait_address_is_yaml_quoted(monsters):
    """A URL holds a colon, and an unquoted one is a YAML error FSB swallows
    silently: the note parses as having no portrait at all."""
    for monster in monsters[:20]:
        if not str(monster.get("image") or "").strip():
            continue
        body = eb.note_for(monster).split("```statblock\n", 1)[1].rsplit("\n```", 1)[0]
        line = next(l for l in body.splitlines() if l.startswith("image:"))
        assert line.count('"') == 2, line
        assert yaml.safe_load(body)["image"].startswith("https://")


def test_a_creature_with_no_portrait_emits_no_image_key(by_name):
    """The Strixhaven and homebrew records carry none, and an `image: ""` line
    would claim a portrait that renders as nothing."""
    record = dict(by_name["Goblin"])
    record.pop("image", None)
    assert "image:" not in eb.note_for(record)


def test_default_provenance_is_the_srd(by_name):
    assert "`dnd5e_srd.json`" in eb.note_for(by_name["Goblin"])


def test_load_monsters_reads_an_alternate_file(tmp_path, by_name):
    """The whole point of --data: a second file shaped {"monsters": [...]}, with
    dnd5e_srd.json left exactly as the SRD published it."""
    import json
    alt = tmp_path / "alt.json"
    alt.write_text(json.dumps({"monsters": [by_name["Goblin"]]}), encoding="utf-8")
    assert [m["name"] for m in eb.load_monsters(alt)] == ["Goblin"]


def test_load_monsters_reports_a_missing_file_clearly(tmp_path):
    with pytest.raises(SystemExit) as exc:
        eb.load_monsters(tmp_path / "nope.json")
    assert "not found" in str(exc.value)


# ── an ability the source never printed ───────────────────────────────────

def test_an_absent_ability_is_refused_rather_than_defaulted():
    """Ruin Grinder's INT/WIS/CHA are null because the page did not print them,
    and the data file says outright that no values were invented. Printing 10
    for all three would invent them anyway, at the table, where a DM cannot tell
    a transcribed score from an invented one."""
    with pytest.raises(ValueError) as exc:
        eb.abilities({"name": "Ruin Grinder", "str": 22, "dex": 13, "con": 15,
                      "int": None, "wis": None, "cha": None})
    msg = str(exc.value)
    assert "Ruin Grinder" in msg
    for a in ("INT", "WIS", "CHA"):
        assert a in msg, f"{a} not named in {msg!r}"


def test_a_zero_ability_is_kept_rather_than_read_as_absent():
    """The trap in the line this replaced: `or 10` treats 0 as missing. An 8 is
    not a gap, and a construct with INT 0 is a real creature, not a blank."""
    got = eb.abilities({"name": "Door", "str": 0, "dex": 0, "con": 0,
                        "int": 0, "wis": 0, "cha": 0})
    assert got == [0, 0, 0, 0, 0, 0]


def test_a_positional_stats_list_is_still_accepted():
    """Some callers already hold an explicit list. A gap cannot be read in one,
    so it is never "missing" there -- and refusing it would break them for a
    problem they do not have."""
    assert eb.abilities({"name": "Dummy", "stats": [8, 12, 13, 10, 11, 12]}) == \
        [8, 12, 13, 10, 11, 12]


# ── homebrew records: the SRD-shaped and the authored-shaped record ─────────
#
# The SRD is not the only `{"monsters": [...]}` file, and the two shapes differ in
# ways that were all silent. `--data` exists so licensed content stays out of
# dnd5e_srd.json, so these bugs only ever showed up as a Strixhaven student
# rendered with no attacks at all.

AURORA = {
    "name": "Aurora Luna Wynterstarr", "index": "aurora-luna-wynterstarr",
    "cr": 0.25, "size": "Medium", "type": "humanoid", "alignment": "Neutral",
    "hp": 33, "hp_dice": "6d8", "ac": 11, "speed": "walk 35 ft., climb 35 ft.",
    "str": 8, "dex": 12, "con": 13, "int": 10, "wis": 11, "cha": 12,
    "passive_perception": 10, "languages": "Common, Bullywug, Sylvan",
    "description": (
        "Excited to Be Here: Aurora has advantage on initiative rolls.\n\n"
        "Deathless Nature: Aurora doesn't need to breathe.\n\n"
        "Spider Climb: Aurora can climb difficult surfaces without a check.\n\n"
        "Reaction — Beginner's Luck (2/Day): When Aurora fails a saving throw, "
        "she can reroll the d20. She must use the new roll."),
    "actions": [
        {"name": "Magic Flare",
         "desc": "Melee or Ranged Spell Attack: +3 to hit, reach 5 ft. or range "
                 "60 ft., one target. Hit: 7 (1d12 + 1) force damage."},
        {"name": "Vampiric Bite",
         "desc": "Melee Weapon Attack: +3 to hit, reach 5 ft., one target. Hit: "
                 "3 (1d4 + 1) piercing damage. Aurora regains hit points equal "
                 "to the piercing damage dealt."},
    ],
}


def test_a_homebrew_action_survives():
    """The `desc` key, not `raw`.

    An exporter that reads only `actions[].raw` -- the SRD's key -- drops every
    attack in every homebrew record without a word. Aurora rendered with zero
    attacks, which is a complete-looking statblock for a creature whose whole
    threat is two attacks.
    """
    names = [a["name"] for a in eb.build_fields(AURORA)["actions"]]
    assert names == ["Magic Flare", "Vampiric Bite"]


def test_a_homebrew_action_keeps_its_full_text():
    fields = eb.build_fields(AURORA)
    flare = next(a for a in fields["actions"] if a["name"] == "Magic Flare")
    assert flare["desc"].startswith("Melee or Ranged Spell Attack: +3 to hit")


def test_consecutive_trait_paragraphs_do_not_swallow_each_other():
    """Each printed entry is its own paragraph. Splitting only before "Action
    <em-dash>" welded every trait to the one above it, and since the trait regex is
    DOTALL the first name won and ate the rest -- three traits rendered as one."""
    names = [t["name"] for t in eb.build_fields(AURORA)["traits"]]
    assert names == ["Excited to Be Here", "Deathless Nature", "Spider Climb"]


def test_a_reaction_is_not_filed_as_an_action():
    """FSB has a `reactions` container and the printed block separates the two.
    The SRD never writes a "Reaction" section, so this is homebrew-only, and
    putting it in `actions` misrepresents the source."""
    fields = eb.build_fields(AURORA)
    assert [r["name"] for r in fields["reactions"]] == ["Beginner's Luck (2/Day)"]
    assert "Beginner's Luck" not in [a["name"] for a in fields["actions"]]


def test_a_trait_named_with_a_period_is_not_left_nameless():
    """The SRD prints "Pack Tactics: text". Strixhaven prints "Gravity Shift
    (Recharge 5-6). text" -- a period, not a colon -- so the colon form misses
    entirely and the trait renders with no name at all, which is the entry's whole
    content."""
    name, desc = eb._period_trait(
        "Gravity Shift (Recharge 5-6). The archaic reverses gravity for one "
        "creature it can see within 100 feet of itself.")
    assert name == "Gravity Shift (Recharge 5-6)", "unbalanced paren, name is wrong"
    assert desc.startswith("The archaic reverses gravity")


def test_period_trait_names_stop_at_the_earliest_balanced_delimiter():
    """Otherwise "Teleport. The archaic teleports to an unoccupied space" can hand
    the name a whole second sentence."""
    name, _ = eb._period_trait("Teleport. The archaic teleports somewhere.")
    assert name == "Teleport"


def test_a_stat_less_record_emits_no_stats_table():
    """A campaign civilian, a corpse: FSB binds `stats` to a fixed-width table, so
    there is nothing to put in one. Emitting six 10s would invent six numbers."""
    fields = eb.build_fields({"name": "Wend Petch", "cr": 0, "hp": 11, "ac": 10})
    assert "stats" not in fields
    assert fields["hp"] == 11


def test_a_half_filled_record_is_refused_rather_than_treated_as_stat_less():
    """Five of six scores is a data-entry error, not a civilian. Treating it as
    stat-less would hide the gap behind a block that looks deliberate; it is
    refused and the missing ability is named."""
    with pytest.raises(ValueError, match="CHA"):
        eb.build_fields({"name": "Wend Petch", "str": 8, "dex": 12, "con": 10,
                         "int": 10, "wis": 11, "cha": None})


def test_a_partial_ability_set_is_refused_by_name_at_all():
    with pytest.raises(ValueError) as exc:
        eb.build_fields({"name": "Wend Petch", "str": 8})
    for ability in ("DEX", "CON", "INT", "WIS", "CHA"):
        assert ability in str(exc.value)


def test_empty_subheading_fields_are_not_emitted():
    """`size: ""` does not render in FSB, but it is a line in the note, and a
    stat-less record has no size to print."""
    fields = eb.build_fields({"name": "Wend Petch", "cr": 0, "hp": 11, "ac": 10})
    assert "size" not in fields and "type" not in fields
    assert "alignment" not in fields


def test_a_spell_list_survives_as_a_nameless_trait(monsters, by_name):
    """A paragraph with no "Name:" is still content.

    Acolyte's spell list has no name to split off, so it renders description-only.
    This is the paragraph that has no other home: it is not an action (no section
    marker) and not a "Name: text" trait, and an earlier version of the period-form
    fix left this branch unreachable behind a `continue`, dropping all 13 of them
    while every test still passed.
    """
    fields = eb.build_fields(by_name["Acolyte"])
    assert any(t["name"] == "" and "sacred flame" in t["desc"]
               for t in fields["traits"]), "the spell list was dropped"


def test_no_description_paragraph_is_dropped(monsters):
    """Every paragraph of every SRD description must come back as an entry.

    The exact check, at the parser: one printed paragraph in, one trait-or-section
    out. Comparing text instead does not work -- a trait legitimately splits into a
    name and a desc, so "Amphibious: The aboleth can breathe air and water." is
    never a substring of any single emitted field.

    This is the blunt check that would have caught the unreachable branch the
    Acolyte test above describes, without needing to know which creature it hit.
    """
    for monster in monsters:
        blocks = [b for b in re.split(r"\n\s*\n", monster.get("description", "").strip())
                  if b.strip()]
        traits, sections = eb.parse_description(monster.get("description", ""))
        assert len(traits) + len(sections) == len(blocks), (
            f"{monster['name']}: {len(blocks)} paragraphs in, "
            f"{len(traits) + len(sections)} entries out")


def test_every_parsed_entry_reaches_the_output(monsters):
    """The parser is not the only place to lose a paragraph: `build_fields` routes
    entries into containers and could drop one. Every name the parser found must
    appear in some container of the emitted block."""
    containers = ("actions", "bonus_actions", "reactions", "legendary_actions",
                  "mythic_actions", "lair_actions")
    for monster in monsters:
        fields = eb.build_fields(monster)
        names = {e["name"] for c in containers for e in fields.get(c, [])}
        _traits, sections = eb.parse_description(monster.get("description", ""))
        for _section, entry in sections:
            assert entry["name"] in names, f"{monster['name']}: {entry['name']} unrouted"
