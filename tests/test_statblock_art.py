"""statblock_art.py: a statblock gets a portrait, or it is named as uncovered.

Every test here asserts a specific name, filename or reason is PRESENT in a match
or in the report, or ABSENT from one. None of them assert only that something
parsed, because the failure this script exists to prevent is a well-formed output
carrying the wrong face: `Dean Moseo (Witherbloom)` quietly taking
`beledros-witherbloom.png` renders a plausible Strixhaven dean and passes every
check there is. "It produced a report" is not the property under test. "Moseo is
in the uncovered list and beledros is nowhere near him" is.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import map_to_atlas
import register_tokens as rt
import statblock_art as sa

HAVE_PIL = rt.HAVE_PIL

# Names that cannot collide with the real 98 files in `display/tokens`, so a test
# that means to be hermetic does not silently depend on the engine checkout.
ABSENT = ("Zorbul the Unmapped", "Grullax", "Mistwarden Pell")


def png(directory: pathlib.Path, name: str, colour: bytes = b"A") -> pathlib.Path:
    """A file that is byte-distinguishable from every other art in a test."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if path.suffix == ".png" and HAVE_PIL:
        from PIL import Image
        Image.new("RGBA", (256, 256), tuple(colour * 3) + (255,)).save(path)
    else:
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode() + colour)
    return path


def note(vault: pathlib.Path, name: str, body: str = "ac: 12\nhp: 30") -> str:
    """A Fantasy Statblocks note, in the shape `export_bestiary.note_for` writes."""
    folder = vault / "Bestiary"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(
        "---\nstatblock: inline\nstatblock-link: \"#^statblock\"\n---\n\n"
        f"# {name}\n\n```statblock\nname: {name}\n{body}\n```\n^statblock\n",
        encoding="utf-8")
    return name


def vault_at(tmp_path: pathlib.Path, collections=("Strixhaven",)) -> pathlib.Path:
    """A minimal Atlas vault, close enough to the real one to be worth testing."""
    v = tmp_path / "vault"
    meta = v / "atlas-vtt" / ".atlas-data"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "assets-metadata.json").write_text(json.dumps({
        "version": 2, "vaultId": "v", "defaultCollectionId": collections[0],
        "collections": {c: {"id": c, "uid": f"uid-{c}", "version": 1, "name": c,
                            "tags": {}, "settings": {"conditions": []},
                            "createdAt": 1, "modifiedAt": 1} for c in collections},
        "assets": {},
    }, indent=2), encoding="utf-8")
    return v


def atlas_index(vault: pathlib.Path) -> dict:
    """The vault's `assets-metadata.json`, which is the document under test."""
    return json.loads(
        (vault / "atlas-vtt/.atlas-data/assets-metadata.json").read_text(encoding="utf-8"))


def index_for(tmp_path: pathlib.Path, *pools: tuple[str, dict[str, list[str]]]) -> sa.ArtIndex:
    """An index built from named pools: `(pool, {"art file": "tag"})`.

    The tag is written into the pixel data so two files that share a slug are
    genuinely different pictures rather than the same one copied twice, which is
    the only thing that makes the shadowing report mean anything.
    """
    index = sa.ArtIndex()
    for pool, files in pools:
        directory = tmp_path / f"pool-{pool}"
        for i, filename in enumerate(files):
            png(directory, filename, colour=bytes([65 + i]))
        index.add_pool(pool, directory)
    return index


# ── an exact match, and the only other door ────────────────────────────────

def test_an_exact_slug_match_takes_the_pools_own_file(tmp_path):
    index = index_for(tmp_path, ("bestiary", ["aboleth.png"]))
    match = sa.match_record("Aboleth", index, {})
    assert match["art"] == "aboleth.png"
    assert match["how"] == "exact"
    assert match["pool"] == "bestiary"


def test_case_and_spacing_do_not_hide_an_exact_match(tmp_path):
    """The slug is normalised, so `Giant  Frog!` is the same creature as the file
    `giant-frog.png`. Normalising case and punctuation is not fuzzy matching; the
    whole slug still has to be there."""
    index = index_for(tmp_path, ("bestiary", ["giant-frog.png"]))
    assert sa.match_record("Giant  Frog", index, {})["art"] == "giant-frog.png"


# ── the failure this script exists to refuse ───────────────────────────────

def test_a_name_with_an_epithet_does_not_fuzzy_match_the_bare_name(tmp_path):
    """Two Witherbloom deans exist and one of them has art. Moseo is the other.

    A prefix, a substring, an edit distance or a "closest match" would hand
    Beledros's face to Dean Moseo. Nothing here does any of those, so Moseo is
    uncovered and the art is not touched.
    """
    index = index_for(tmp_path, ("display-tokens", ["beledros-witherbloom.png"]))
    moseo = sa.match_record("Dean Moseo (Witherbloom)", index, {})
    assert "art" not in moseo, "an epithet must not reach another dean's portrait"
    assert moseo["how"] is None
    assert sa.match_record("Beledros Witherbloom", index, {})["art"] == "beledros-witherbloom.png"


def test_an_uncovered_record_carries_no_art_key_at_all(tmp_path):
    """Absent, not null. A caller that finds `art` has found a claim; a caller
    that finds None has found a gap it has to surface."""
    match = sa.match_record(ABSENT[0], index_for(tmp_path), {})
    assert "art" not in match
    assert match["name"] == ABSENT[0]


def test_a_generic_student_token_is_never_handed_to_a_named_character(tmp_path):
    """`first-year-student-3.png` renders. That is the problem.

    Six of them ship in `display/tokens` for exactly the reason a table needs a
    body to put in a chair, and handing one to Juno Ashvale would put a stranger
    in the game under her name, invisibly, mid-fight.
    """
    index = index_for(tmp_path, ("display-tokens", [
        "first-year-student-1.png", "first-year-student-2.png",
        "first-year-student-3.png", "quandrix-scholar-5.png",
        "lorehold-apprentice.png", "silverquill-student.png"]))
    for character in ("Juno Ashvale", "Mabli Quenn", "Theodric Vane"):
        assert "art" not in sa.match_record(character, index, {}), character


def test_a_two_person_crop_is_not_approved_for_one_dean(tmp_path):
    """`adrix-nev.png` is canon's joint portrait of both post-invasion deans and is
    the only Adrix art on this machine.

    It is still not on Adrix's token. A token showing two faces under one name is
    the same failure as a wrong crop: it looks finished and it is wrong. The
    absence is the decision.
    """
    index = index_for(tmp_path, ("faculty", ["adrix-nev.png"]))
    for record in ("[[Bestiary/Adrix.md|Adrix]]", "Nev, the Practical Dean"):
        assert "art" not in sa.match_record(record, index, {}), record


# ── the approval table ──────────────────────────────────────────────────────

def test_the_approval_table_is_the_only_door_past_an_exact_match(tmp_path):
    """These three are judgement calls about canon identity, not spellings.

    Each is in APPROVED with its reasoning, and each appears in the report under
    that heading so the GM reads the same sentence this script matched on.
    """
    index = index_for(tmp_path, ("display-tokens", [
        "augusta-dean-of-order.png", "oracle-of-strixhaven.png",
        "rosimyffenbip-rosie-wuzfeddlims.png"]))
    for record, expected in (
        ("Augusta, Order Returned (Lorehold, interim dean)", "augusta-dean-of-order.png"),
        ("Jadzi, Steward of Fate (Oracle of Strixhaven)", "oracle-of-strixhaven.png"),
        ("Rosie Wuzfeddlims / Rosimyffenbip", "rosimyffenbip-rosie-wuzfeddlims.png"),
    ):
        match = sa.match_record(record, index, {})
        assert match["art"] == expected, record
        assert match["how"] == "approved", record
        assert len(match["reason"]) > 40, f"{record} must carry a real reason"


def test_every_approval_is_reported_with_its_reason(tmp_path):
    index = index_for(tmp_path, ("display-tokens", ["oracle-of-strixhaven.png"]))
    sources = [("npcs", [sa.match_record(
        "Jadzi, Steward of Fate (Oracle of Strixhaven)", index, {})])]
    text = sa.build_report(sources, index, tmp_path, None)
    assert "approved past an exact match" in text
    assert "Jadzi, Steward of Fate (Oracle of Strixhaven)" in text
    assert sa.APPROVED["Jadzi, Steward of Fate (Oracle of Strixhaven)"][1] in text


def test_an_approval_naming_a_missing_file_is_refused_not_substituted(tmp_path):
    """A stale line in APPROVED must fail loudly.

    Falling back to "whatever else is in the pool" is precisely the guess the
    table exists to prevent, and it would fail silently: the record would report
    covered.
    """
    index = index_for(tmp_path, ("display-tokens", ["oracle-of-strixhaven.png"]))
    monkey = dict(sa.APPROVED)
    monkey["Jadzi, Steward of Fate (Oracle of Strixhaven)"] = (
        "oracle-of-arcavios.png", "a file that was renamed and never updated here")
    try:
        sa.APPROVED.clear()
        sa.APPROVED.update(monkey)
        with pytest.raises(sa.Refused) as excinfo:
            sa.match_record("Jadzi, Steward of Fate (Oracle of Strixhaven)", index, {})
    finally:
        sa.APPROVED.clear()
        sa.APPROVED.update(monkey)
    assert "oracle-of-arcavios.png" in str(excinfo.value)


def test_no_approval_names_a_generic_token():
    """Guard on the table itself rather than on any one run."""
    generic = ("student", "scholar", "apprentice", "pledgemage", "mascot")
    for record, (filename, _) in sa.APPROVED.items():
        assert not any(word in filename for word in generic), \
            f"{record!r} approves {filename}, which is generic filler art"


# ── recorded substitutions, honoured and quoted ────────────────────────────

AGE_SUBS = {
    "note": ("Age-variant substitution. The picture is a dragon of the correct "
             "COLOUR and does NOT depict the age. Approved by the GM on "
             "2026-09-30. Do not read the picture as evidence of age."),
    "rule": '^(Young|Adult|Ancient) <colour> Dragon$ -> art for "<colour> dragon"',
    "substitutions": {
        "young-black-dragon": {"uses": "black-dragon", "file": "black-dragon.png"},
    },
}


def write_age_subs(vault: pathlib.Path) -> None:
    folder = vault / "atlas-vtt" / "assets" / "bestiary"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / sa.SUBSTITUTION_FILE).write_text(
        json.dumps(AGE_SUBS, indent=1), encoding="utf-8")


def test_a_recorded_substitution_is_used_and_the_collection_wording_is_printed(tmp_path):
    vault = vault_at(tmp_path)
    write_age_subs(vault)
    # The age-named copy exists and the base art does not, which is the state the
    # real vault is in: the substitution was already made as copies.
    index = index_for(tmp_path, ("bestiary", ["young-black-dragon.png"]))
    index.dirs_note = vault
    subs = sa.age_substitutions(vault)
    match = sa.match_record("Young Black Dragon", index, subs)
    assert match["art"] == "young-black-dragon.png"
    assert match["how"] == "substituted"
    assert match["uses"] == "black-dragon"
    assert match["base_present"] is False

    text = sa.build_report([("bestiary", [match])], index, vault, None)
    assert AGE_SUBS["rule"] in text, "the collection's own rule must be printed"
    assert AGE_SUBS["note"] in text, "the collection's own reasoning must be printed"
    assert "Young Black Dragon" in text


def test_the_substitution_rule_is_not_reimplemented(tmp_path):
    """The recorded table names ONE substitution. The pool holds art for two more
    dragons the rule would cover.

    `Ancient Green Dragon` and `Adult Gold Dragon` both match their own art file
    exactly, so they are exact matches. But `Young Gold Dragon` has NO art of its
    own while `gold-dragon.png` sits in the pool unmentioned by the table: the
    regex in the `rule` string would cover it, and it must stay uncovered,
    because a second implementation of a GM's approval is a second opinion.
    """
    vault = vault_at(tmp_path)
    write_age_subs(vault)
    index = index_for(tmp_path, ("bestiary", [
        "young-black-dragon.png", "adult-gold-dragon.png", "gold-dragon.png"]))
    subs = sa.age_substitutions(vault)

    assert sa.match_record("Adult Gold Dragon", index, subs)["how"] == "exact"
    uncovered = sa.match_record("Young Gold Dragon", index, subs)
    assert "art" not in uncovered, "the rule string must not be executed as a rule"
    assert sa.match_record("Young Black Dragon", index, subs)["how"] == "substituted"


def test_a_collection_with_no_substitution_file_is_not_an_error(tmp_path):
    assert sa.age_substitutions(vault_at(tmp_path)) == {}


def test_an_unreadable_substitution_file_is_refused_not_ignored(tmp_path):
    """It is where the campaign records which portraits stand in for which
    creatures. Ignoring it would report a substitution without its reasoning."""
    vault = vault_at(tmp_path)
    write_age_subs(vault)
    (vault / "atlas-vtt/assets/bestiary" / sa.SUBSTITUTION_FILE).write_text(
        "{ not json", encoding="utf-8")
    with pytest.raises(sa.Refused) as excinfo:
        sa.age_substitutions(vault)
    assert sa.SUBSTITUTION_FILE in str(excinfo.value)


# ── wikilinks: `parse_npcs` hands these back whole ──────────────────────────

def test_a_wikilink_record_matches_on_its_display_text(tmp_path):
    """`npcs-full.md` writes roster bullets as links to the note each person
    belongs to, so `parse_npcs` returns the whole `[[...]]`.

    The display text is the name the GM wrote and the target's stem is that same
    name, so this is a parse rather than a guess, and it is what lets five real
    portraits reach the records they belong to.
    """
    index = index_for(tmp_path, ("display-tokens", ["saf-tarn.png", "tilana-kapule.png"]))
    match = sa.match_record("[[Bestiary/Tilana Kapule.md|Tilana Kapule]]", index, {})
    assert match["art"] == "tilana-kapule.png"
    assert match["how"] == "exact"


def test_a_bare_wikilink_falls_back_to_the_target_stem(tmp_path):
    index = index_for(tmp_path, ("display-tokens", ["aurora-luna-wynterstarr.png"]))
    match = sa.match_record("[[Bestiary/Aurora Luna Wynterstarr.md]]", index, {})
    assert match["art"] == "aurora-luna-wynterstarr.png"


def test_a_wikilink_naming_two_different_creatures_is_refused(tmp_path):
    """`[[Bestiary/Adrix.md|Nev]]` points at one note and displays another.

    There is no honest way to pick a side, and picking one puts a face on a
    coin-toss, so the record is reported with the contradiction rather than matched.
    """
    index = index_for(tmp_path, ("display-tokens", ["adrix.png", "nev.png"]))
    match = sa.match_record("[[Bestiary/Adrix.md|Nev]]", index, {})
    assert "art" not in match
    assert "different creatures" in match["problem"]


def test_a_wikilink_target_is_a_path_and_only_its_stem_is_the_name(tmp_path):
    """`[[Bestiary/Tilana Kapule.md|Tilana Kapule]]` names a FILE, not a folder.

    Comparing the whole link target against an art filename compares
    `bestiary-tilana-kapule` with `tilana-kapule`, disagrees with itself, and
    refuses a record whose portrait is sitting in the pool. Taking the stem is
    what lets the five wikilinked students in this campaign be covered at all.
    """
    index = index_for(tmp_path, ("display-tokens", ["tilana-kapule.png"]))
    assert sa.wikilink_parts("[[Bestiary/Tilana Kapule.md|Tilana Kapule]]")[1] == "Tilana Kapule"
    assert sa.match_record("[[Bestiary/Tilana Kapule.md|Tilana Kapule]]",
                           index, {})["art"] == "tilana-kapule.png"


# ── pools, shadowing and precedence ─────────────────────────────────────────

def test_two_pools_with_one_slug_report_the_shadowed_file(tmp_path):
    """`daemogoth-titan.png` is in the vault's monster collection and again in
    `display/tokens/`, and the two are different pictures.

    The earlier pool wins, and BOTH are named. A precedence nobody can see is
    indistinguishable from a bug, and this one decides whose face is on a Daemogoth.
    """
    first = tmp_path / "one"
    second = tmp_path / "two"
    png(first, "daemogoth-titan.png", colour=b"1")
    png(second, "daemogoth-titan.png", colour=b"2")
    index = sa.ArtIndex()
    index.add_pool("bestiary", first)
    index.add_pool("display-tokens", second)

    assert index.resolve("Daemogoth Titan").pool == "bestiary"
    assert len(index.shadowed) == 1
    slugged, winner, loser = index.shadowed[0]
    assert slugged == "daemogoth-titan"
    assert winner.pool == "bestiary" and loser.pool == "display-tokens"
    text = sa.build_report([], index, tmp_path, None)
    assert "daemogoth-titan" in text
    assert "shadowed" in text


def test_identical_bytes_in_two_pools_are_not_reported_as_a_shadow(tmp_path):
    """Two tools copying the same picture out of the same pack is not a
    collision. Reporting those would bury the three that matter in a list of
    fifty."""
    directory = tmp_path / "shared"
    png(directory, "aboleth.png", colour=b"Z")
    index = sa.ArtIndex()
    index.add_pool("bestiary", directory)
    index.add_pool("faculty", directory)
    assert index.shadowed == []


# ── reading the vault ───────────────────────────────────────────────────────

def test_bestiary_notes_are_counted_once_on_a_case_insensitive_filesystem(tmp_path):
    """`Bestiary` and `bestiary` are ONE directory on a default macOS volume.

    `map_to_atlas.bestiary_index` probes both spellings and survives it because
    it indexes into a dict with `setdefault`, so a doubled scan is invisible there.
    This script builds a LIST, so probing both appended every note twice: coverage
    read `371/742` and each recorded dragon printed twice. Both look like data.
    """
    vault = vault_at(tmp_path)
    note(vault, "Aboleth")
    note(vault, "Giant Frog")
    lower = vault / "bestiary"
    assert lower.is_dir(), "this only means anything where the volume folds case"
    assert lower.samefile(vault / "Bestiary")
    assert sa.bestiary_notes(vault) == ["Aboleth", "Giant Frog"]


def test_readme_in_the_bestiary_folder_is_not_a_statblock(tmp_path):
    vault = vault_at(tmp_path)
    note(vault, "Aboleth")
    (vault / "Bestiary" / "README.md").write_text(
        "# Bestiary\n\nGenerated notes, one per creature.\n", encoding="utf-8")
    assert sa.bestiary_notes(vault) == ["Aboleth"]


def test_a_note_without_the_plugin_markers_is_not_counted(tmp_path):
    vault = vault_at(tmp_path)
    (vault / "Bestiary").mkdir(parents=True, exist_ok=True)
    (vault / "Bestiary" / "Session Notes.md").write_text(
        "# Session Notes\n\nwhat happened on Tuesday\n", encoding="utf-8")
    assert sa.bestiary_notes(vault) == []


# ── the report ──────────────────────────────────────────────────────────────

def coverage_line(text: str, source: str) -> str:
    """The one report line that reports `source`'s coverage."""
    for line in text.splitlines():
        if line.startswith(f"  {source} ") and "/" in line:
            return line
    raise AssertionError(f"no coverage line for {source!r} in:\n{text}")


def test_the_report_names_every_uncovered_thing_and_no_covered_one(tmp_path):
    """The uncovered list is the deliverable: it is what art to source.

    So a covered name appearing in it is as much a bug as a missing one, and this
    asserts both directions. A record can be uncovered, matched, or refused for
    having no single name, and the last has to reach the GM too.
    """
    index = index_for(tmp_path, ("bestiary", ["aboleth.png"]))
    sources = [("bestiary", [
        sa.match_record("Aboleth", index, {}),
        sa.match_record(ABSENT[0], index, {}),
        sa.match_record("[[Bestiary/Adrix.md|Nev]]", index, {}),
    ])]
    text = sa.build_report(sources, index, tmp_path, None)
    assert "1/3" in coverage_line(text, "bestiary")
    uncovered = text.split("no portrait, by name: bestiary (2)")[1]
    assert ABSENT[0] in uncovered
    assert "different creatures" in uncovered
    assert "Aboleth" not in uncovered, "a covered note is not art to source"


def test_the_report_separates_the_three_sources(tmp_path):
    """One pooled percentage would hide which denominator is which, and the three
    are not comparable: 371 SRD notes, 33 campaign NPCs, one player.

    The totals here differ on purpose (2, 2, 1) so a single pooled line could not
    produce the same three ratios by accident.
    """
    index = index_for(tmp_path, ("bestiary", ["aboleth.png"]))
    sources = [("bestiary", [sa.match_record(n, index, {})
                             for n in ("Aboleth", "Giant Frog")]),
               ("npcs", [sa.match_record(n, index, {})
                         for n in ("Juno Ashvale", "Ninefold")]),
               ("pcs", [sa.match_record("Kairos", index, {})])]
    text = sa.build_report(sources, index, tmp_path, None)
    for source, expected in (("bestiary", "1/2"), ("npcs", "0/2"), ("pcs", "0/1")):
        assert expected in coverage_line(text, source), \
            f"{source} should read {expected}, got {coverage_line(text, source)!r}"


def test_the_report_says_the_main_cast_has_no_art(tmp_path):
    """Kairos, Ysolde, Hesper and the rest are campaign-original.

    Nothing on this machine draws them, so no amount of matching will produce
    their faces, and the report has to say so every run. The alternative is a GM
    reaching for `first-year-student-3.png` at the table.
    """
    index = index_for(tmp_path, ("display-tokens", [
        "first-year-student-1.png", "quandrix-scholar-2.png"]))
    sources = [
        ("npcs", [sa.match_record(name, index, {}) for name in (
            "Juno Ashvale", "Mabli Quenn", "Ninefold", "Tam, Observant Sequencer (Quandrix)")]),
        ("pcs", [sa.match_record("Kairos", index, {})]),
    ]
    text = sa.build_report(sources, index, tmp_path, None)
    assert "0/13 have a portrait" in text
    for label in ("PC Kairos", "Ysolde Marrow", "Hesper Vael", "Dace Orrin",
                  "Ambrin Hollis", "Selwyn Pike", "Tam"):
        assert label in text, label
    assert "No generic student, scholar, apprentice or mascot token" in text


def test_the_main_cast_names_a_character_the_campaign_no_longer_has(tmp_path):
    """`MAIN_CAST` holds record names so the claim is checkable.

    A cast member the campaign has dropped is reported as unparsed rather than
    quietly counted as covered or quietly counted as missing, because a table
    that says "13 have none" for a campaign with 11 people in it is a table
    nobody can trust on the rows it does get right.
    """
    index = index_for(tmp_path)
    sources = [("npcs", [sa.match_record("Ninefold", index, {})])]
    text = sa.build_report(sources, index, tmp_path, None)
    assert "no record named 'Juno Ashvale' was parsed at all" in text


def test_art_that_matches_nothing_is_listed_separately_from_coverage(tmp_path):
    """Art for creatures with no note yet is not coverage and not an error.

    Reporting it as either would be wrong in the direction that matters: a
    percentage that counts it says the vault is better covered than it is.
    """
    index = index_for(tmp_path, ("bestiary", ["aboleth.png", "wyvern.png"]))
    text = sa.build_report([("bestiary", [sa.match_record("Aboleth", index, {})])],
                           index, tmp_path, None)
    assert "matching no statblock (1)" in text
    assert "wyvern.png" in text


# ── registration: idempotence, dry runs, refusals ──────────────────────────

def campaign_at(tmp_path: pathlib.Path, name: str = "testcamp") -> pathlib.Path:
    """A campaign with the two files this script reads, and a resolvable root."""
    root = tmp_path / "campaigns-root"
    camp = root / "campaigns" / name
    camp.mkdir(parents=True)
    (camp / "state.md").write_text("# state\n", encoding="utf-8")
    (camp / "npcs-full.md").write_text(
        "# NPCs\n\n## Juno Ashvale\n- **Role:** Student\n- **HP:** 11\n\n"
        "## Ninefold\n- **Role:** Familiar\n- **HP:** 4\n", encoding="utf-8")
    sheets = camp / "characters"
    sheets.mkdir()
    (sheets / "Kairos.md").write_text(
        "# Kairos\n\n**Player:** Someone  **Last Updated:** 2026-09-26\n\n"
        "## Identity\n- **Race:** Kenku\n- **Class:** Wizard\n- **Level:** 1\n\n"
        "## Combat Stats\n- **HP:** 8\n- **AC:** 12\n",
        encoding="utf-8")
    return camp


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A campaign, a vault and a hermetic engine checkout, all under tmp_path.

    Art goes where `POOLS` says it goes: the vault's own `assets/bestiary/` for the
    monster, and the engine's `display/tokens/` for the people. Putting it in a
    folder of the test's own inventing produced a suite that passed against an
    index nothing was ever added to, which is the same shape as a coverage report
    that claims a match it never found.
    """
    campaign_at(tmp_path)
    vault = vault_at(tmp_path)
    note(vault, "Aboleth")
    note(vault, "Giant Frog")
    note(vault, "Rosimyffenbip Rosie Wuzfeddlims")
    monsters = vault / "atlas-vtt" / "assets" / "bestiary"
    png(monsters, "aboleth.png", colour=b"A")
    engine = tmp_path / "engine"
    (engine / "display" / "tokens").mkdir(parents=True)
    tokens = engine / "display" / "tokens"
    png(tokens, "juno-ashvale.png", colour=b"B")
    png(tokens, "first-year-student-1.png", colour=b"C")
    png(tokens, "rosimyffenbip-rosie-wuzfeddlims.png", colour=b"E")
    png(tokens, "adrix-nev.png", colour=b"F")
    # Ninefold is main cast and gets NO art, which is the real state of this
    # campaign and what makes him useful in the coverage assertions below.
    monkeypatch.setattr(sa, "ROOT", engine)
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(tmp_path / "campaigns-root"))
    return vault


def run(vault: pathlib.Path, *extra: str) -> int:
    return sa.main(["--campaign", "testcamp", "--vault", str(vault), *extra])


def test_a_real_run_registers_only_the_matched_pictures(wired):
    """Only art with a statblock behind it.

    Registering the whole pool would put a student token in Atlas beside the
    monsters, which is the wrong pile to build the next token from.
    """
    assert run(wired) == 0
    index = json.loads((wired / "atlas-vtt/.atlas-data/assets-metadata.json").read_text())
    tokens = {a["name"] for a in index["assets"].values() if a.get("type") == "token"}
    assert "aboleth" in tokens
    assert "juno-ashvale" in tokens
    assert "first-year-student-1" not in tokens, "generic filler art must stay out"
    assert "giant-frog" not in tokens, "an uncovered note must register nothing"
    assert "ninefold" not in tokens, "homebrew main cast has no art to register"


def test_running_twice_registers_nothing_new_and_churns_nothing(wired):
    """Documented as safe in a loop, so it has to be true of the index and not
    only of the file count."""
    assert run(wired) == 0
    first = (wired / "atlas-vtt/.atlas-data/assets-metadata.json").read_text()
    assert run(wired) == 0
    second = (wired / "atlas-vtt/.atlas-data/assets-metadata.json").read_text()
    assert first == second, "a second run must not rewrite the shared index"
    index = json.loads(second)
    tokens = [a for a in index["assets"].values() if a.get("type") == "token"]
    assert len(tokens) == len({a["imagePath"] for a in tokens}), "duplicate entries"


def test_dry_run_writes_nothing(wired):
    before = _tree(wired)
    assert run(wired, "--dry-run") == 0
    assert _tree(wired) == before, "--dry-run wrote into the vault"
    assert not (wired / "atlas-vtt/collections").exists()


def test_stats_writes_nothing_and_registers_nothing(wired):
    before = _tree(wired)
    assert run(wired, "--stats") == 0
    assert _tree(wired) == before
    assert not (wired / "atlas-vtt/collections").exists(), \
        "--stats staged art into the vault"


def test_stats_reports_the_three_sources_separately(wired, capsys):
    assert run(wired, "--stats") == 0
    out = capsys.readouterr().out
    assert "bestiary" in out and "npcs" in out and "pcs" in out
    assert "no portrait, by name: npcs" in out
    assert "Ninefold" in out
    assert "Report only. Nothing was written." in out


def test_a_registered_token_points_at_a_file_and_a_correctly_named_thumbnail(wired):
    """Atlas names the thumbnail by the FNV-1a/32 of the vault-relative image path.
    A token at any other name renders as a blank swatch, and nothing errors."""
    assert run(wired) == 0
    index = json.loads((wired / "atlas-vtt/.atlas-data/assets-metadata.json").read_text())
    entry = next(a for a in index["assets"].values()
                 if a.get("name") == "aboleth")
    assert (wired / entry["imagePath"]).is_file()
    if entry["thumbnailPath"]:
        stem = pathlib.Path(entry["imagePath"]).stem
        assert entry["thumbnailPath"] == (
            f"atlas-vtt/assets/thumbnails/{stem}-{rt.fnv1a_32(entry['imagePath'])}.webp")


def test_a_picture_serving_two_records_is_one_token(wired):
    """The `Rosie Wuzfeddlims / Rosimyffenbip` record and the
    `Rosimyffenbip Rosie Wuzfeddlims` note are one face.

    Atlas's entry is per picture. Staging the file once means one token rather
    than two near-identically named ones, which is what a re-run would then have
    to clean up.
    """
    assert run(wired) == 0
    index = json.loads((wired / "atlas-vtt/.atlas-data/assets-metadata.json").read_text())
    paths = [a["imagePath"] for a in index["assets"].values()
             if a.get("type") == "token" and "rosimyffenbip" in a["imagePath"]]
    assert len(paths) == 1, paths


def test_a_collection_atlas_does_not_have_is_refused(wired, capsys):
    """`register_tokens` owns this refusal and its wording is already about this
    situation, so the message reaches the GM rather than being reworded."""
    assert run(wired, "--collection", "Nope") == 1
    assert "Nope" in capsys.readouterr().err


def test_a_directory_that_is_not_an_atlas_vault_is_refused(tmp_path):
    """`register_tokens` raises its own `Refused`, and this script's subclasses it.

    Two exception classes here would mean a caller writing
    `except statblock_art.Refused` silently misses the three refusals that protect
    the shared index: a directory that is not an Atlas vault, a corrupt
    `assets-metadata.json`, and a collection Atlas does not have.
    """
    plain = tmp_path / "not-a-vault"
    plain.mkdir()
    with pytest.raises(rt.Refused):
        sa.register_matched(plain, [{"art": "aboleth.png"}],
                            index_for(tmp_path, ("bestiary", ["aboleth.png"])),
                            "Strixhaven", [], dry_run=False)


def test_this_scripts_own_refusals_are_catchable_as_register_tokens_refusals():
    """Same reason, asserted on the class rather than on one refusal."""
    assert issubclass(sa.Refused, rt.Refused)


def test_nothing_matching_is_refused_rather_than_registered_empty(tmp_path):
    """Writing an empty index change and reporting success would make `--stats`
    and a real run disagree about whether anything happened."""
    with pytest.raises(sa.Refused) as excinfo:
        sa.register_matched(vault_at(tmp_path), [], sa.ArtIndex(),
                            "Strixhaven", [], dry_run=False)
    assert "nothing matched" in str(excinfo.value)


def test_the_two_slugs_agree():
    """The matching half's `slug` and the writing half's must not drift.

    This script stages `young-black-dragon.png` and `register()` then names the
    vault file `slug(stem) + suffix`. If the two copies ever disagreed, the
    staged name and the registered name would be different files and a second run
    would register the picture again under its new name, which is exactly the
    duplication this is documented not to do.
    """
    for name in ("Young Black Dragon", "Rosie Wuzfeddlims / Rosimyffenbip",
                 "  spaced  out  ", "Will-o'-Wisp", "Giant Rat #2"):
        assert map_to_atlas.slug(name) == rt.slug(name), name


# ── the creator pack is never extracted ─────────────────────────────────────

def test_the_creator_pack_is_read_by_name_and_only_matched_members_are_copied(tmp_path):
    """267 files of third-party art must not land in the repository to use four.

    A pack is read as a directory here, and only a member that actually matched is
    copied out, later, into a temporary staging folder that is then deleted.
    """
    import zipfile
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Strixhaven D&D tokens/Miscellaneous/Gryphon Rider.png", b"\x89PNG-one")
        zf.writestr("Strixhaven D&D tokens/Miscellaneous/Second Year.png", b"\x89PNG-two")
    vault = vault_at(tmp_path)
    note(vault, "Gryphon Rider")
    note(vault, ABSENT[0])

    index = sa.build_index(vault, [], archive)
    assert index.resolve("Gryphon Rider").pool == str(archive)
    assert index.resolve(ABSENT[0]) is None, "unmatched members must go unused"

    # A dry run over the copy leaves the whole vault exactly as it was.
    before = _tree(vault)
    assert sa.main(["--vault", str(vault), "--dry-run"]) == 0
    assert _tree(vault) == before
    assert not (tmp_path / "Strixhaven D&D tokens").exists()


def test_a_missing_creator_pack_is_refused_not_ignored(tmp_path):
    with pytest.raises(sa.Refused):
        sa.build_index(vault_at(tmp_path), [], tmp_path / "not-here.zip")


# ── helpers ─────────────────────────────────────────────────────────────────

def _tree(root: pathlib.Path) -> dict:
    """Every file under `root`, by path and content.

    Used instead of mtimes because the property under test is that nothing was
    written AT ALL, and a rewrite with a fresh mtime is still a write.
    """
    import hashlib
    return {str(p.relative_to(root)): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ── the frontmatter Atlas reads ──────────────────────────────────────────────
#
# Atlas's "Create tokens" importer does not infer a picture from a folder and a
# name. It reads `image:` / `token-image:` out of the note's frontmatter and
# resolves that link against the vault. With neither key present every note scans
# as `missing-image`, which is what 405 rows of "Missing image" is: a wiring gap,
# not a missing-art problem. 73 creatures HAD a portrait and still scanned blank.

NOTE = """---
statblock: inline
statblock-link: "#^statblock"
---

# Aboleth

*Derived.*

```statblock
name: Aboleth
```
"""


def test_stamping_adds_the_image_key(tmp_path):
    note = tmp_path / "Aboleth.md"
    note.write_text(NOTE, encoding="utf-8")
    assert sa.stamp_image(note, "atlas-vtt/collections/Strixhaven/tokens/aboleth.png")
    front = sa._FRONTMATTER.match(note.read_text(encoding="utf-8")).group(1)
    assert "image: atlas-vtt/collections/Strixhaven/tokens/aboleth.png" in front


def test_stamping_leaves_the_statblock_body_alone(tmp_path):
    note = tmp_path / "Aboleth.md"
    note.write_text(NOTE, encoding="utf-8")
    sa.stamp_image(note, "x.png")
    text = note.read_text(encoding="utf-8")
    assert text.split("---\n", 2)[2] == NOTE.split("---\n", 2)[2], "body was rewritten"


def test_stamping_is_idempotent(tmp_path):
    """A second run must not rewrite the file.

    The exporter stamps on every run, so a non-idempotent stamp shows up as a
    permanently dirty working tree and makes a real change invisible in a diff.
    """
    note = tmp_path / "Aboleth.md"
    note.write_text(NOTE, encoding="utf-8")
    assert sa.stamp_image(note, "x.png")
    assert not sa.stamp_image(note, "x.png")


def test_stamping_replaces_an_existing_key_rather_than_duplicating_it(tmp_path):
    """Two `image:` keys is valid YAML that resolves to whichever comes last."""
    note = tmp_path / "Aboleth.md"
    note.write_text(NOTE.replace("statblock: inline",
                                 "image: wrong.png\nstatblock: inline"),
                    encoding="utf-8")
    sa.stamp_image(note, "right.png")
    text = note.read_text(encoding="utf-8")
    assert text.count("image:") == 1
    assert "wrong.png" not in text


def test_a_bare_filename_is_not_stamped_as_a_vault_relative_path(tmp_path):
    """Atlas tries the link RELATIVE TO THE NOTE, then the vault root.

    A bare `aboleth.png` resolves to neither, so the note scans as missing-image
    with the picture sitting in `collections/Strixhaven/tokens/`. That is the exact
    state this exists to fix, so the registered path is read off disk rather than
    assembled from the art filename.
    """
    vault = tmp_path / "vault"
    tokens = vault / "atlas-vtt/collections/Strixhaven/tokens"
    tokens.mkdir(parents=True)
    (tokens / "aboleth.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    path = sa.registered_path(vault, "aboleth.png", "Strixhaven")
    assert path == "atlas-vtt/collections/Strixhaven/tokens/aboleth.png"


def test_a_picture_that_was_never_registered_is_reported_not_guessed(tmp_path):
    vault = tmp_path / "vault"
    (vault / "atlas-vtt/collections/Strixhaven/tokens").mkdir(parents=True)
    assert sa.registered_path(vault, "aboleth.png", "Strixhaven") is None


def test_a_creature_with_art_but_no_note_is_skipped_and_named(tmp_path):
    """A portrait with nothing to hang it on is reported, never written blind."""
    vault = tmp_path / "vault"
    (vault / "Bestiary").mkdir(parents=True)
    stamped, unnoted = sa.stamp_notes(
        vault, [{"name": "Nobody", "art": "nobody.png"}], "Strixhaven", False)
    assert stamped == 0
    assert unnoted == ["Nobody"]


def test_a_curated_note_wins_over_the_derived_one_for_the_same_creature(tmp_path):
    """`Bestiary/` beats `NPCs/` for a person who is in both. GM's decision."""
    vault = tmp_path / "vault"
    (vault / "NPCs").mkdir(parents=True)
    (vault / "Bestiary").mkdir(parents=True)
    (vault / "NPCs" / "Greta Gorunn.md").write_text(NOTE, encoding="utf-8")
    (vault / "Bestiary" / "Greta Gorunn.md").write_text(NOTE, encoding="utf-8")
    found = sa._note_for(vault, "Greta Gorunn")
    assert found is not None and found.parent.name == "Bestiary"


def test_note_lookup_folds_punctuation_like_the_exporter_does(tmp_path):
    """Three of the nine approved NPC matches are written with punctuation stripped."""
    vault = tmp_path / "vault"
    (vault / "NPCs").mkdir(parents=True)
    note = vault / "NPCs" / "Augusta Order Returned Lorehold interim dean.md"
    note.write_text(NOTE, encoding="utf-8")
    found = sa._note_for(vault, "Augusta, Order Returned (Lorehold, interim dean)")
    assert found == note, "exact lookup finds nothing here; the note is not written"
