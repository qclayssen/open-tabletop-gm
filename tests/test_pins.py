"""`scripts/pins.py`: the allow-list, the fail-closed default, and the store.

Two guards live here and this file is the only place either is enforced, so it
carries the honest weight of CLAUDE.md's hard rule 1 -- "never read spoiler
files" -- for the pin surface.

  * `check_note_target` / `note_body`: an allow-list of three folders, so a
    spoiler file added next year is refused by default rather than allowed until
    somebody remembers to deny it.
  * `revealed()`: a pin is off the players' board unless it says otherwise.

Containment is not tested here -- it is `paths.campaign_path`, and
`tests/test_campaign_path.py` pins it. What is tested here is that *these* gates
are applied, because the failure this file exists to catch is a gate that was
written and then not called on the path that matters: the note *read*, which is
reached by a click rather than by the list endpoint that validated the pin.

`before fix:` for every case is `ModuleNotFoundError: scripts.pins`.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pins  # noqa: E402


@pytest.fixture
def camp(tmp_path):
    """A campaign with the three allowed folders and two files that must never open."""
    root = tmp_path / "campaigns" / "demo"
    for folder in ("notes", "locations", "handouts"):
        (root / folder).mkdir(parents=True)
    (root / "notes" / "harbour.md").write_text("# The Harbour\n\nFog.\n", encoding="utf-8")
    (root / "locations" / "rotunda.md").write_text("# The Rotunda\n", encoding="utf-8")
    (root / "handouts" / "wanted.md").write_text("# Wanted\n", encoding="utf-8")
    # The sealed material. None of these may ever be readable through a pin.
    (root / "state.md").write_text("**System:** D&D\n", encoding="utf-8")
    (root / "answer-key.md").write_text("Kairos is the traitor.\n", encoding="utf-8")
    (root / "DM_SEALED").mkdir()
    (root / "DM_SEALED" / "twist.md").write_text("The villain is the Duke.\n",
                                                encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "plan.md").write_text("Stage 3: burn the archive.\n",
                                             encoding="utf-8")
    return root


def pin(**over):
    base = {"x": 3, "y": 4, "label": "Harbour", "kind": "note",
            "target": "notes/harbour.md"}
    base.update(over)
    return base


# ── the allow-list ───────────────────────────────────────────────────────────
#
# before fix: ModuleNotFoundError

@pytest.mark.parametrize("target", [
    "state.md",
    "answer-key.md",
    "DM_SEALED/twist.md",
    "secrets/plan.md",
    "secrets",
    "characters/kairos.md",
    "combat/encounter.json",
    "notes",
    "",
])
def test_a_target_outside_the_allowed_folders_is_refused(camp, target):
    """before fix: ModuleNotFoundError.

    The list is the important half of this file: `secrets/plan.md` is refused
    because of its *folder*, not because its name is on a deny-list. That is the
    difference between this guard and a list of files somebody has to remember.
    """
    with pytest.raises(pins.PinError):
        pins.check_note_target(target)


@pytest.mark.parametrize("target", [
    "notes/../state.md",
    "notes/../../etc/passwd",
    "/etc/passwd",
    "notes//harbour.md",
    "notes/./harbour.md",
    "..\\state.md",
    "notes\\..\\state.md",
])
def test_a_traversal_is_refused_before_the_allow_list_is_reached(camp, target):
    """before fix: ModuleNotFoundError.

    These fail on shape, not on the folder list, so they are refused whatever the
    allow-list says -- including if someone later widens it to `"."`. A test that
    only checked the folder would pass with a wider allow-list and stop testing
    anything.
    """
    with pytest.raises(pins.PinError):
        pins.check_note_target(target)


@pytest.mark.parametrize("target", [
    "notes/harbour.txt",
    "notes/harbour",
    "notes/harbour.md.bak",
    "notes/harbour.md/",
])
def test_a_target_that_is_not_a_markdown_file_is_refused(camp, target):
    """before fix: ModuleNotFoundError.

    `.md.bak` and `harbour.md/` are the two that look like they might slip
    through a naive `endswith(".md")` on a prefix match, which is why they are
    named rather than left implicit.
    """
    with pytest.raises(pins.PinError):
        pins.check_note_target(target)


@pytest.mark.parametrize("target", [
    "notes/harbour.md",
    "locations/rotunda.md",
    "handouts/wanted.md",
])
def test_the_allowed_folders_resolve(camp, target):
    """before fix: ModuleNotFoundError.

    The positive half. An allow-list test that only proves refusal passes just as
    well for a helper that refuses everything, which is the failure mode a
    regression here would actually take.
    """
    assert pins.check_note_target(target) == target


def test_a_path_traversing_back_into_an_allowed_folder_is_still_refused(camp):
    """before fix: ModuleNotFoundError.

    `notes/../notes/harbour.md` is spelled with an allowed first segment, so a
    check that only looked at `split("/")[0]` would accept it. It names a real
    note, so it is harmless here -- but the same shape is
    `notes/../state.md` in the case above, and the helper that accepts both is
    not testing what it looks like it is testing.
    """
    with pytest.raises(pins.PinError):
        pins.check_note_target("notes/../notes/harbour.md")


def test_a_symlink_out_of_the_campaign_is_refused(camp, tmp_path):
    """before fix: ModuleNotFoundError.

    The allow-list passes on `notes/shortcut.md` -- right folder, right suffix --
    so the escape is only caught by containment. This is the case that proves the
    two gates are both load-bearing rather than one standing in for the other.
    """
    outside = tmp_path / "answer-key.md"
    outside.write_text("spoiler", encoding="utf-8")
    (camp / "notes" / "shortcut.md").symlink_to(outside)
    with pytest.raises(pins.PinError):
        pins.note_body(camp, pins.validate(pin(target="notes/shortcut.md")))


def test_a_sealed_file_reachable_through_the_allow_list_by_symlink_is_refused(camp):
    """The strongest bypass in the design, and it is one line long.

    before fix: ModuleNotFoundError -- and once pins.py existed, this test caught
    a live leak that had survived a green suite.

    Every gate passes: the request is `notes/innocent.md`, so the allow-list sees
    an allowed folder, the suffix is `.md`, and containment holds because the
    symlink points at a file **inside** the campaign. `campaign_path` is
    correct and useless here -- it answers "is this inside the campaign?", and
    the answer is yes.

    So the allow-list is applied a second time, to the path the filesystem
    actually resolved to, which is `answer-key.md`, and the pin is refused for
    what it opens rather than for what it is spelled as. That is why
    `note_body` re-derives the policy instead of trusting the record it was
    handed: the record was validated against a different string.

    This is the case a GM hits by accident -- linking a note to its canonical
    location is an ordinary thing to do -- so it cannot be left to a denylist
    that happens to notice this one filename.
    """
    (camp / "notes" / "innocent.md").symlink_to(camp / "answer-key.md")
    hostile = pins.validate(pin(target="notes/innocent.md", revealed=True))
    with pytest.raises(pins.PinError):
        pins.note_body(camp, hostile)


def test_a_symlink_to_a_sealed_folder_is_refused(camp):
    """before fix: ModuleNotFoundError.

    The same escape aimed at a whole folder, which is what a GM does when they
    link `notes/` at a shared corpus. `notes/research -> ../secrets` passes every
    check on the request and resolves to a first segment of `secrets`.
    """
    (camp / "notes" / "research").symlink_to(camp / "secrets", target_is_directory=True)
    hostile = pins.validate(pin(target="notes/research/plan.md", revealed=True))
    with pytest.raises(pins.PinError):
        pins.note_body(camp, hostile)


def test_a_symlink_to_a_real_note_still_works(camp):
    """The other direction, and the reason the fix is a re-check and not a ban.

    before fix: ModuleNotFoundError. A GM who links `notes/harbour.md` at
    `locations/harbour.md` -- both allowed folders, entirely legitimate -- must
    still get their note. Refusing every symlink would be safe and useless, and
    this suite would not notice, because every other symlink case is a refusal.
    """
    (camp / "notes" / "harbour.md").unlink()
    (camp / "notes" / "harbour.md").symlink_to(camp / "locations" / "rotunda.md")
    body = pins.note_body(camp, pins.validate(pin(revealed=True)))
    assert body == (camp / "locations" / "rotunda.md").read_text(encoding="utf-8")


def test_a_note_that_is_a_fifo_is_refused(camp):
    """before fix: ModuleNotFoundError.

    A FIFO called `notes.md` is not absolute, has no `..`, ends in `.md`, sits
    inside the campaign, and passes every check in this file -- and then blocks
    forever the moment it is opened. Refusing anything that is not a regular
    file is two words and also rejects a device node, which behaves the same way.
    Skipped where mkfifo is unavailable rather than failed.
    """
    fifo = camp / "handouts" / "pipe.md"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError, NotImplementedError):
        pytest.skip("mkfifo is unavailable here")
    with pytest.raises(pins.PinError):
        pins.note_body(camp, pins.validate(pin(target="handouts/pipe.md", revealed=True)))


# ── revealed: fail closed ───────────────────────────────────────────────────
#
# before fix: ModuleNotFoundError

def test_a_pin_is_not_on_the_players_board_unless_it_says_so(camp):
    """The default is off, and the word for that is `is True`.

    before fix: ModuleNotFoundError. A truthiness test here would treat `"false"`
    and `0` as revealed, which is the shape a hand-edited pin file has.
    """
    assert pins.revealed([{"revealed": True}]) == [{"revealed": True}]
    for absent in ({}, {"revealed": False}, {"revealed": "false"},
                   {"revealed": 0}, {"revealed": None}, {"revealed": 1},
                   {"revealed": "true"}):
        assert pins.revealed([absent]) == [], absent


def test_a_pin_that_is_not_revealed_cannot_be_read(camp):
    """The read route re-checks, because a URL can be guessed.

    before fix: ModuleNotFoundError. This is the single highest-value case in
    the file: the list endpoint filtering correctly is not sufficient if the read
    endpoint serves the body anyway, and a player who has seen the note's path
    can request it directly.
    """
    hidden = pins.validate(pin(revealed=False))
    with pytest.raises(pins.PinError):
        pins.note_body(camp, hidden)


def test_a_revealed_note_reads_back_byte_for_byte(camp):
    """The whole feature, end to end, through both gates.

    before fix: ModuleNotFoundError. Returns the file's own text -- not
    re-rendered, not escaped, not frontmatter-stripped on the server. Escaping
    and rendering are the browser's job and happen once, in
    `_renderMarkdown`, which escapes before it emits anything.
    """
    written = (camp / "notes" / "harbour.md").read_text(encoding="utf-8")
    body = pins.note_body(camp, pins.validate(pin(revealed=True)))
    assert body == written


def test_a_map_pin_is_never_a_note_read(camp):
    """before fix: ModuleNotFoundError.

    `note_body` is reached by click, not by kind-checking the URL, so this is the
    guard against a pin whose kind was edited in the file between save and read.
    """
    mapped = pins.validate({"kind": "map", "target": "some-map",
                            "x": 1, "y": 1, "revealed": True})
    with pytest.raises(pins.PinError):
        pins.note_body(camp, mapped)


def test_a_missing_note_is_an_error_not_an_empty_panel(camp):
    """before fix: ModuleNotFoundError.

    A note that has not been written yet is the normal state of a pin the GM is
    still building, so this has to be an error the GM can read -- not a 200 with
    an empty body, which would look like a note that renders as nothing.
    """
    ghost = pins.validate(pin(target="notes/not-written-yet.md", revealed=True))
    with pytest.raises(pins.PinError):
        pins.note_body(camp, ghost)


# ── the store ───────────────────────────────────────────────────────────────
#
# before fix: ModuleNotFoundError

def test_a_round_trip_preserves_every_field(camp):
    """before fix: ModuleNotFoundError.

    Asserted field by field rather than as `== pins`, because a dict equality
    would pass with the fields in a different order and would not notice one
    being silently dropped, which is the actual risk when a record passes through
    validate() twice.
    """
    original = pin(x=2.5, y=7.25, label="Fog Bank", revealed=True)
    pins.save(camp, "harbour-front", [original])
    back = pins.load(camp, "harbour-front")
    assert len(back) == 1
    for field in ("x", "y", "label", "kind", "target", "revealed"):
        assert back[0][field] == original[field], field
    assert isinstance(back[0]["x"], float)


def test_floating_cell_coordinates_survive(camp):
    """before fix: ModuleNotFoundError.

    A pin is placed by a pointer, so `x` is rarely a whole number. Integer
    coercion here would stack every pin in a map on the left-hand column, which
    is invisible in a test and obvious at the table.
    """
    pins.save(camp, "m", [{"kind": "note", "target": "notes/harbour.md",
                           "x": 2.5, "y": 7.25}])
    assert pins.load(camp, "m")[0]["x"] == 2.5
    assert pins.load(camp, "m")[0]["y"] == 7.25


def test_a_map_with_no_pins_is_empty_not_an_error(camp):
    """before fix: ModuleNotFoundError.

    The overwhelmingly common case: a map nobody has pinned. It must not raise,
    because every map load goes through this.
    """
    assert pins.load(camp, "unpinned-map") == []


def test_a_corrupt_pins_file_does_not_take_the_board_down(camp):
    """before fix: ModuleNotFoundError.

    Pins are annotations. A GM who hand-edited one badly should still get a
    working map, so a parse failure yields no pins rather than a 500 on every
    render.
    """
    path = camp / "pins" / "broken.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json at all", encoding="utf-8")
    assert pins.load(camp, "broken") == []


def test_one_bad_pin_does_not_lose_the_others(camp):
    """before fix: ModuleNotFoundError.

    A file with three good pins and one malformed record shows one pin, not zero.
    Dropping the whole list would make a single typo look like the pins were
    deleted.
    """
    path = camp / "pins" / "mixed.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([
        pin(label="One"),
        {"kind": "note"},                      # no target
        pin(label="Two", x=5),
        {"kind": "map", "target": "some-map", "x": 9, "y": 9},
    ]), encoding="utf-8")
    labels = [p["label"] for p in pins.load(camp, "mixed")]
    assert labels == ["One", "Two", "some map"]


def test_a_refused_write_leaves_the_file_untouched(camp):
    """before fix: ModuleNotFoundError.

    The error-path guarantee `test_mapseditor.py` holds every route to: a 400
    means nothing was written. Checked on the file's bytes *and* on the pins
    directory, since creating it would be a write too.
    """
    pins.save(camp, "m", [pin(label="Keeper")])
    path = camp / "pins" / "m.json"
    before = path.read_bytes()
    for bad in ({"kind": "note", "target": "state.md"},
                {"kind": "note", "target": "answer-key.md"},
                {"kind": "map", "target": "../escape"},
                {"kind": "nonsense", "target": "notes/harbour.md"}):
        with pytest.raises(pins.PinError):
            pins.save(camp, "m", [pin(label="Keeper"), bad])
    assert path.read_bytes() == before


def test_a_refused_write_does_not_create_the_pins_directory(camp):
    """before fix: ModuleNotFoundError.

    The same guarantee one level up: validation happens before `mkdir`, so a
    request that was always going to be refused does not leave a directory
    behind on the GM's campaign.
    """
    assert not (camp / "pins").exists()
    with pytest.raises(pins.PinError):
        pins.save(camp, "fresh", [{"kind": "note", "target": "state.md"}])
    assert not (camp / "pins").exists()


def test_two_pins_on_one_square_keep_the_last(camp):
    """before fix: ModuleNotFoundError.

    Two markers on one square is a board the GM cannot read -- one is always
    underneath. Last write wins, which is what clicking twice in the editor
    means, and the count stays at one so the GM sees the change happen.
    """
    pins.save(camp, "m", [pin(label="First", x=2, y=2),
                          pin(label="Second", x=8, y=8),
                          pin(label="Third", x=2, y=2)])
    back = pins.load(camp, "m")
    assert len(back) == 2
    assert {p["label"] for p in back} == {"Second", "Third"}


def test_a_map_pin_slug_is_checked_against_the_real_maps(camp):
    """before fix: ModuleNotFoundError.

    A map target is a slug, never a path -- it is looked up in display/maps/ and
    nowhere else, so a map pin cannot become a file read. Given no slug list it
    is still refused for containing a separator.
    """
    known = {"harbour-front", "the-rotunda"}
    assert pins.validate({"kind": "map", "target": "the-rotunda",
                          "x": 1, "y": 1}, known)["target"] == "the-rotunda"
    with pytest.raises(pins.PinError):
        pins.validate({"kind": "map", "target": "no-such-map",
                       "x": 1, "y": 1}, known)
    for escape in ("../state.md", "notes/harbour.md", "a/b"):
        with pytest.raises(pins.PinError):
            pins.validate({"kind": "map", "target": escape, "x": 1, "y": 1}, known)


def test_a_pin_without_a_label_gets_a_readable_one(camp):
    """before fix: ModuleNotFoundError.

    `harbour.md` -> "harbour", `sewer-crossing.md` -> "sewer crossing". A pin
    with a blank label would render as an anonymous dot, and the GM would have to
    open it to find out what it is.
    """
    assert pins.validate(pin(label="", target="notes/harbour.md"))["label"] == "harbour"
    assert pins.validate(pin(label=None,
                             target="locations/sewer-crossing.md"))["label"] \
        == "sewer crossing"


def test_a_label_cannot_carry_markup_or_newlines(camp):
    """before fix: ModuleNotFoundError.

    Labels are drawn on the board, so they are display text: control characters
    and newlines are dropped rather than escaped, and the result is escaped again
    on the way out. Two layers for one value is deliberate -- this one is
    hygiene, the browser's `esc` is the boundary.

    The `<script>` is *kept*, on purpose. Dropping angle brackets here would be
    laundering the note rather than rendering it, and this layer is not the one
    that stops it: the browser escapes the label on the way to the DOM. Asserting
    the brackets are gone would pin a behaviour that hides mistakes from the GM
    without making anything safer.
    """
    out = pins.validate(pin(label="<script>alert(1)</script>\nInn\tRoom"))["label"]
    assert "\n" not in out and "\t" not in out
    assert out == "<script>alert(1)</script> InnRoom"


def test_a_label_is_capped(camp):
    """before fix: ModuleNotFoundError.

    A label is drawn on a board that is not very wide. An uncapped one is a
    single token wide enough to cover every other pin.
    """
    assert len(pins.validate(pin(label="x" * 500))["label"]) <= pins.MAX_LABEL


def test_crafted_extra_fields_are_dropped_not_stored(camp):
    """before fix: ModuleNotFoundError.

    A pin record is posted from a browser. Anything beyond the known fields is
    discarded at validation, so a crafted POST cannot plant a key that a future
    reader would treat as trusted.
    """
    out = pins.validate(pin(elevation=99, gm_only=True, owner="admin"))
    assert set(out) == {"id", "x", "y", "label", "kind", "target", "revealed"}


def test_a_pin_map_slug_cannot_become_a_path(camp):
    """before fix: ModuleNotFoundError.

    `pins_path` is what turns a slug into a filename. It is the one place a
    caller-supplied string becomes part of a path, so it strips rather than
    trusts -- and the route checks the slug against the real map list first, so
    this is the second line rather than the only one.
    """
    for hostile in ("../../escape", "a/b", "", "..", "with space"):
        with pytest.raises(pins.PinError):
            pins.pins_path(camp, hostile)
    assert pins.pins_path(camp, "harbour-front").name == "harbour-front.json"
