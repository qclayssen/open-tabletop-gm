"""notes.py: the advisor council's notes outlive the process that asked for them."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import notes as notes_mod
from localdm.notes import Notes


def test_a_note_is_written_where_the_gm_can_find_it(tmp_path):
    n = Notes(tmp_path)
    assert n.add("Continuity: you promised Mira her brother in session 2.",
                 source="/advise", advisors=("continuity",)) is True
    assert n.path == tmp_path / "localdm" / "notes.md"
    text = n.path.read_text(encoding="utf-8")
    assert "Continuity: you promised Mira" in text
    assert "/advise" in text and "continuity" in text


def test_nothing_is_not_a_note(tmp_path):
    n = Notes(tmp_path)
    assert n.add("") is False and n.add("   \n ") is False
    assert not n.path.exists()


def test_the_note_is_kept_verbatim(tmp_path):
    """An advisor's exact wording is what the GM wants; a lossy copy is a
    different claim. This is why canon.jsonl exists and why this does too."""
    n = Notes(tmp_path)
    body = "Director: 'Mira would not say that twice.' - italics, dashes, 12% odds."
    n.add(body, source="/advise")
    assert body in n.entries()[0][3]


def test_entries_carry_stamp_source_advisors_and_body(tmp_path):
    n = Notes(tmp_path)
    n.add("first note", source="/advise council", advisors=("historian", "director"))
    n.add("second note", source="shadow", advisors=("arbiter",))
    stamp, source, advisors, body = n.entries()[1]
    assert source == "shadow" and advisors == ["arbiter"] and body == "second note"
    assert stamp                      # a real timestamp, not empty
    assert n.entries()[0][1] == "/advise council"


def test_recent_returns_the_tail_oldest_first(tmp_path):
    n = Notes(tmp_path)
    for i in range(6):
        n.add(f"note {i}")
    recent = n.recent(2)
    assert "note 4" in recent and "note 5" in recent and "note 3" not in recent
    assert recent.index("note 4") < recent.index("note 5")   # oldest first
    assert len(n.entries()) == 6                             # no limit: everything


def test_a_campaign_with_no_consults_reads_empty_not_broken(tmp_path):
    n = Notes(tmp_path)
    assert n.entries() == [] and n.recent() == ""


def test_trimming_drops_whole_leading_blocks_never_half_a_note(tmp_path, monkeypatch):
    """Half a note read as advice is worse than no note, so the cut is made at a
    block boundary or not at all."""
    monkeypatch.setattr(notes_mod, "MAX_BYTES", 400)
    n = Notes(tmp_path)
    for i in range(40):
        n.add(f"note {i} " + "x" * 60, source="/advise", advisors=("continuity",))
    text = n.path.read_text(encoding="utf-8")
    assert len(text) <= 400 + 200, len(text)
    # Every surviving block starts with a full "## stamp - source - advisors" header.
    for block in text.split("## ")[1:]:
        assert block.startswith("20") and " - /advise" in block.split("\n")[0]
    assert "note 39" in text
    # And the newest entry still parses.
    assert n.entries()[-1][1] == "/advise"


def test_a_file_too_big_to_split_is_left_alone(tmp_path, monkeypatch):
    """A single oversized note has no safe cut point, so it is kept whole rather
    than mangled - truncation would silently lose the advisor's advice."""
    monkeypatch.setattr(notes_mod, "MAX_BYTES", 10)
    n = Notes(tmp_path)
    n.add("a note that is quite long indeed, with no further blocks to follow it")
    assert "quite long indeed" in n.path.read_text(encoding="utf-8")
