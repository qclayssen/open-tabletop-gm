"""The reviewed tables must stay facts, and must not break a clone.

WHAT THESE PIN, AND WHY EACH ONE MATTERS
========================================
`statblock_art.py` earns its keep by refusing to put a wrong picture on a
creature. Three things here can each break that in a way the coverage number will
not show:

  * `FORM_VARIANT_ART` is 19 hand-written name pairs. A line that stops matching
    the dataset becomes a guess with a comment saying it is not one, and the
    first test below re-derives every line from `dnd5e_srd.json` and fails. The
    second fails if a NEW upstream record diverges and nobody added a line, which
    is the direction a hand-written table rots in.

  * Both art pools are gitignored, so a machine that has never run an installer
    has neither, and every reviewed line names a file that is in no pool. The
    code used to raise `Refused` on the first one, which meant this script could
    not run at all on a fresh clone -- the opposite of what `token_portraits.py`
    promises. Absent art is now reported and the record left uncovered, and these
    tests are that bug pinned from both sides: no crash, and no invented art.

  * The three main-cast approvals were each chosen by LOOKING at the candidate
    file, and the three refusals are the more useful half. "It is a dwarf" is not
    the whole of being Mabli, and a species-correct token can still be the wrong
    token; the last test records what was looked at and rejected, so nobody
    "fixes" the gap without seeing why.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import statblock_art as sa

DATA = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"


def _safe_name(name: str) -> str:
    """`export_bestiary.safe_name`, reimplemented for the test only.

    Deliberately a second copy: the point is to check the table against what the
    exporter will actually produce, and a copy that called the exporter could not
    disagree with it and so would not test anything.
    """
    cleaned = re.sub(r"[^\w\s-]", "", name)
    return re.sub(r"\s+", " ", cleaned).strip() or "unnamed"


def _note_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _safe_name(name).lower()).strip("-")


# ── FORM_VARIANT_ART: every line must be a pair the dataset asserts ──────────

@pytest.mark.skipif(not DATA.exists(), reason="generated SRD dataset is gitignored")
def test_every_line_is_a_pair_the_dataset_asserts():
    """Both halves of every pair must come from ONE record of the dataset.

    This is what stops the table rotting. A line whose `name` and `index` are not
    in the same record is a resemblance, which is the thing this file refuses.
    """
    monsters = json.loads(DATA.read_text(encoding="utf-8"))["monsters"]
    asserted = {(m["name"], m["index"]) for m in monsters}
    for note, art in sa.FORM_VARIANT_ART.items():
        expected = f"{art[:-4]}.png"
        assert expected == art, f"{note}: art is not a .png"
        pairs = [name for name, index in asserted if index == art[:-4]]
        assert pairs, f"{art} is not any record's index in the dataset"
        # The note is the record's NAME with punctuation dropped, so re-slugging
        # the record name has to produce the note the table is keyed by.
        assert note in {_safe_name(name) for name in pairs}, \
            f"{note!r} is not the safe_name of a record indexed {art[:-4]!r}"


@pytest.mark.skipif(not DATA.exists(), reason="generated SRD dataset is gitignored")
def test_the_table_is_complete_over_the_records_that_diverge():
    """Every SRD record whose note-slug differs from its index is either in the
    table or deliberately absent. A new upstream record that diverges must be
    noticed, not silently left uncovered."""
    monsters = json.loads(DATA.read_text(encoding="utf-8"))["monsters"]
    divergent = {_safe_name(m["name"]): m["index"] for m in monsters
                 if _note_slug(m["name"]) != m["index"]}
    missing = {note: index for note, index in divergent.items()
               if sa.FORM_VARIANT_ART.get(note) != f"{index}.png"}
    assert not missing, f"records whose note name and index diverge, unhandled: {missing}"


def test_a_form_variant_line_naming_a_missing_file_is_refused_not_substituted():
    """A named file that is not there is reported, never silently swapped for
    something else -- the point is that no other art can stand in."""
    index = sa.ArtIndex()
    sa.FORM_VARIANT_ART["Werewolf Wolf Form"] = "no-such-file.png"
    try:
        record = sa.match_record("Werewolf Wolf Form", index, {"substitutions": {}})
    finally:
        sa.FORM_VARIANT_ART["Werewolf Wolf Form"] = "werewolf-wolf.png"
    assert "art" not in record, "a missing file must not resolve to some other art"
    assert "no-such-file.png" in record["problem"]


def test_a_clone_with_no_art_installed_still_runs():
    """`display/tokens/` and `display/srd-art/` are gitignored, so a fresh clone
    has neither, and every reviewed line names a file that is in no pool.

    The original code raised `Refused` on the first such line, which meant
    `statblock_art.py` could not run at all on a machine that had never run an
    installer -- directly contradicting the promise in `token_portraits.py` that
    "a clone without the art still knows the set exists, still credits the
    artist, and still draws every token correctly". This is that bug, pinned.
    """
    artless = sa.ArtIndex()
    assert artless.pools == []
    for name in sa.APPROVED:
        record = sa.match_record(name, artless, {"substitutions": {}})
        assert str(record.get("how")).endswith("-unavailable"), name
        assert "art" not in record, f"{name} invented art on an artless clone"
        assert record.get("art_named"), f"{name} forgot which file it wanted"
    for name in sa.FORM_VARIANT_ART:
        record = sa.match_record(name, artless, {"substitutions": {}})
        assert "art" not in record, name


def test_an_unavailable_line_is_reported_rather_than_only_absent():
    """Silent absence would be the same bug wearing a quieter hat: a stale line
    would look exactly like a typo'd line that happens to match nothing. The
    report has to name the file it wanted."""
    artless = sa.ArtIndex()
    records = [sa.match_record(n, artless, {"substitutions": {}}) for n in sa.APPROVED]
    report = sa.build_report([("npcs", records)], artless, ROOT, None)
    assert "NOT installed" in report
    for name in sa.APPROVED:
        assert name in report, name
    # and it must not be counted as covered
    assert "0/1" in report or " 0/" in report


def test_three_cast_members_take_species_correct_pack_art():
    """Ninefold, Vess and Hesper are creatures, and a picture of the right
    creature beats a generic student token -- the same claim the age-
    substitutions already make, where the picture is of the right KIND of thing
    and the notes say not to read further into it.

    Each was chosen by LOOKING at the file, not by matching its name. That is
    why these three and not six: `wandering-archaic` is towering and faceless,
    `daemogoth-woe-eater` is antlered and lichen-green in a bog, and
    `owlin-arithmancer` is a scholar rather than a duellist. Hesper's plumage is
    NOT white, and the APPROVED line says so in as many words.
    """
    approved = set(sa.APPROVED)
    for species_correct in ("Ninefold", "Vess the Tallykeeper", "Magister Hesper Vael"):
        assert species_correct in approved, species_correct
    # The plumage is wrong and the line has to say so, because
    # `prose/PLUMAGE.md:3` is load-bearing: a GM reading only the approval table
    # would otherwise assume a white owl.
    assert "THIS ONE IS NOT WHITE" in sa.APPROVED["Magister Hesper Vael"][1]


def test_tam_is_never_matched_to_the_gorgon_statue():
    """Tam is a gorgon and `gorgon.png` exists in the bestiary pool, so an exact
    match on "gorgon" is one typo away and would be catastrophic.

    Its eyes are LIT -- the petrifying-gaze signal. `npc-files/tam.md:147` says
    her eyes "don't actually petrify" and `:183` says "Must not use gorgon gaze
    as weapon. She watches without blinking. That is all." The one thing the
    notes are most careful about is exactly what that picture shouts.

    So she takes `tam.png`, a portrait generated from her own character file with
    flat unlit eyes, and never the monster art. This is the whole reason the
    GENERATED art was worth making: it is the only Tam token that does not
    contradict her.
    """
    tam = sa.APPROVED["Tam, Observant Sequencer (Quandrix)"]
    assert tam[0] == "tam.png", tam[0]
    assert "gorgon" not in tam[0].lower()


def test_hollis_is_still_uncovered_because_the_generated_hand_was_wrong():
    """His portrait was generated and NOT installed, and the reason is recorded
    here so nobody re-adds the file: `source/2.2.md:7` states he is missing two
    fingers, four separate times, and the generated image has a full five on both
    hands. That is the most-repeated fact the vault has about him, so a token
    without it is a loud cheerful dwarf, which is a different character.

    The file is not in the pool, so the absence cannot be a manifest slip.
    """
    assert "Coach Ambrin Hollis" not in sa.APPROVED
    assert "ambrin-hollis" not in sa.APPROVED.values()
    assert "Coach Ambrin Hollis" in sa.MAIN_CAST.values()


def test_generated_cast_art_is_matched_by_exact_slug_not_by_approval():
    """Mabli, Theodric and Petra need no APPROVED line at all: their generated
    files are named for the record, so the ordinary exact match finds them.

    That is the property worth pinning. The generated art was designed to be
    named by the slug the matcher already uses, precisely so that adding a
    portrait would not need a table entry, and a future generated portrait should
    not acquire one by accident either.
    """
    index = sa.ArtIndex()
    index.add_pool("display-tokens", pathlib.Path(
        str(ROOT / "display" / "tokens")))
    for name in ("Mabli Quenn", "Theodric Vane", "Petra Lune"):
        record = sa.match_record(name, index, {"substitutions": {}})
        assert record.get("how") == "exact", f"{name}: {record.get('how')}"
        assert name not in sa.APPROVED, f"{name} needed an approval it should not"

