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
