"""find_sheet: a character with two names still has one sheet.

A character's name is whatever its sheet's "# Name" heading says -- "Tamsin
Underbough". Its filename is usually the short form, because that is what a person
typing a file thinks to write: tamsin.md.

Matching only the filename stem against the full name found nothing, and the
caller reported "no sheet in characters/, nothing written." The sheet was there.
What was lost was the fight: HP, spent hit dice and death saves all stayed on the
encounter file and never reached the sheet, so the character read as untouched
after a fight in which they nearly died.

The filename pass has to stay first and stay exact. It is the convention every
campaign follows, and a looser rule that ran first would let a "Kairos.md" claim
to be "Kairos Kestrel" and write one character's results onto another's sheet.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tactics import sync  # noqa: E402


def _campaign(tmp_path: pathlib.Path, *sheets: tuple) -> pathlib.Path:
    folder = tmp_path / "characters"
    folder.mkdir(parents=True, exist_ok=True)
    for filename, title in sheets:
        (folder / filename).write_text(f"# {title}\n\n## Combat Stats\n- **HP:** 7 / 7\n",
                                       encoding="utf-8")
    return tmp_path


def test_the_filename_still_wins(tmp_path):
    camp = _campaign(tmp_path, ("Kairos.md", "Kairos"), ("tamsin.md", "Tamsin Underbough"))
    assert sync.find_sheet(camp, "Kairos").name == "Kairos.md"
    assert sync.find_sheet(camp, "kairos").name == "Kairos.md"      # case-insensitive


def test_a_two_name_character_is_found_by_its_heading(tmp_path):
    camp = _campaign(tmp_path, ("tamsin.md", "Tamsin Underbough"))
    assert sync.find_sheet(camp, "Tamsin Underbough").name == "tamsin.md"


def test_a_shorter_name_does_not_claim_a_longer_character(tmp_path):
    # Both passes are exact. quill.md is titled "Quill Underbough", so "Quill"
    # matches neither the stem (which is "quill.md" -> "quill"... see below) nor
    # the heading, and must not be answered with this sheet. Answering it would
    # write one character's results onto another's.
    camp = _campaign(tmp_path, ("quill-of-bloor.md", "Quill Underbough"))
    assert sync.find_sheet(camp, "Quill") is None
    assert sync.find_sheet(camp, "Quill Underbough").name == "quill-of-bloor.md"


def test_an_exact_filename_match_still_wins_over_a_different_heading(tmp_path):
    # The convention is the filename, and it has always been. A sheet called
    # Kairos.md is Kairos's sheet even if its heading was edited to something
    # else; the fight that just happened is Kairos's and has to land somewhere.
    camp = _campaign(tmp_path, ("Kairos.md", "Kairos Kestrel"))
    assert sync.find_sheet(camp, "Kairos").name == "Kairos.md"


def test_nobody_at_all_is_still_none(tmp_path):
    camp = _campaign(tmp_path, ("Kairos.md", "Kairos"))
    assert sync.find_sheet(camp, "Marra Quenn") is None


def test_a_campaign_with_no_characters_folder_is_not_an_error(tmp_path):
    assert sync.find_sheet(tmp_path, "Kairos") is None
